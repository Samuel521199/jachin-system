# MCP 接入规范 (Model Context Protocol)

**版本**: V2（2026-03），与 [MCP_EXECUTION_MODEL.md](./MCP_EXECUTION_MODEL.md) v2.2 一致  
**定位**: **四大原语**中的 **MCP**（外部协议进程、`mcp:*`；高信任本机托管）

**四大原语**：本文所述 MCP 在 Jachin 中归类为 **MCP 原语**（外部协议进程、`mcp:*`），与 **Tools**（`core:*`/`jpp:*`）、**Skills**（声明式 SOP）、**Agent Tasks**（delegate/后台/coordinate）并列定义见 **[Jachin 视角的「四大原语」终极架构规范.md](./Jachin%20视角的「四大原语」终极架构规范.md)**。

---

## 一、用途

复用 Model Context Protocol 生态中的 stdio/SSE 服务端。V2 默认在 **L3** 进程内托管 `MCPManager`；L2 负责清单同步与跨节点委托（Task Token、Pull），见 [ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md](./ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md)。

**适用场景**：高信任本机/边缘节点（个人设备、企业内网）。

---

## 二、 架构位置

**V2 默认**：stdio MCP **宿主进程为 L3**（`l3_node/mcp_stdio_bootstrap.py` 拉起 `core/mcp_client.py` 的 `MCPManager`）。L2 负责清单同步与跨节点委托，**默认不在 L2 起 stdio 子进程**（回滚：`JACHIN_L2_STDIO_MCP=1`）。详见 [MCP_EXECUTION_MODEL.md](./MCP_EXECUTION_MODEL.md)、[ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md](./ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md)。

| 原语 | 形态 | 信任级别 | 用途 |
|------|------|----------|------|
| **MCP** | MCP 宿主 | 高信任 | 本地系统控制、开箱工具 |
| **Skills** | SKILL.md | 用户可控 | 声明式轻量技能 |
| **Tools · jpp** | Wasm 沙箱 | 零信任 | 商城第三方付费插件（jpp 原子） |

---

## 三、 实现要点

### 3.1 核心组件

- **实现**: `core/mcp_client.py`（`MCPManager`）
- **L3 启动**: `l3_node/mcp_stdio_bootstrap.py`（与 inventory 扫描配合）
- **职责**: 连接 MCP 服务器、发现工具、执行工具调用、将结果返回 ReAct 循环
- **协议**: [Model Context Protocol](https://modelcontextprotocol.io/) 标准

### 3.2 工具发现与注册

1. 启动时读取配置 `~/.jachin/mcp_servers.json`，列出要连接的 MCP 服务器
2. 通过 stdio/SSE 连接各 MCP 服务器
3. 调用 `tools/list` 获取可用工具列表
4. 将工具名、描述、参数 Schema 注册到 Agent 的可用 Action 集合

### 3.3 ReAct 集成

- Agent 在 `[Action]` 阶段可选择调用 MCP 工具或 Wasm 插件
- 路由规则：若 `action_name` 在 MCP 工具列表中，则走 `core/mcp_client.py`；否则走 `core/wasm_runner.py`

### 3.4 配置示例

将 `config/mcp_servers.json.example`（或 `tools/mcp-official/mcp_servers.jachin.example.json`）中的条目合并进 `~/.jachin/mcp_servers.json`，按需修改路径：

```json
{
  "mcp_servers": [
    {
      "id": "filesystem",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem@0.6.2", "~/.jachin/workspace"],
      "env": null
    },
    {
      "id": "shell",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-shell"],
      "env": null
    },
    {
      "id": "tavily-search",
      "name": "Tavily Search",
      "command": "npx",
      "args": ["-y", "tavily-mcp@latest"],
      "env": {
        "TAVILY_API_KEY": "${TAVILY_API_KEY}"
      }
    }
  ]
}
```

**注意**：

- `server-filesystem` 的路径参数需为绝对路径，Windows 下请使用 `C:\\Users\\YourUser\\.jachin\\workspace` 等形式。
- **Tavily**：npm 包名为 **`tavily-mcp`**（勿使用已下架/不存在的 `@tavily/mcp-server`）。在 `.env` 或系统环境中设置 `TAVILY_API_KEY`；JSON 里使用 `"${TAVILY_API_KEY}"`，由 `core/mcp_embedded_runtime.resolve_mcp_cfg_placeholders` 在拉起子进程前从 **当前 L3 进程环境** 展开，避免把密钥写进配置文件落盘（若需明文仅放本机 `mcp_servers.json` 亦可，但不建议进版本库）。
- **stdio 子进程与 API Key（深度说明）**：为何会出现「L3 已加载 Key 仍报 -32600」、SDK 白名单、`dotenv` 与 **cwd**、Windows **npx** 链等，以及新增依赖密钥的 MCP 时的通用清单，见 **[MCP_STDIO_API_KEY_AND_ENV.md](./MCP_STDIO_API_KEY_AND_ENV.md)**。
- Windows 下 `command: npx` 由 `core/mcp_client._resolve_stdio_command` 尽量解析为 `npx.cmd`。

### 3.5 npm / npx 包名校验（强制，防 404）

`mcp_servers.json` 里 `"command": "npx"` 时，`args` 中的 **包名必须与 npm 上真实存在的包一致**。禁止凭记忆或「看起来像官方」自行拼 `@scope/pkg`。

| 要求 | 说明 |
|------|------|
| **合并进仓库前** | 对包名执行一次核验：`npm view <包名> version`（exit 0）或打开 `https://www.npmjs.com/package/<包名>` 确认存在。 |
| **禁止** | 未核验就写入 `config/mcp_servers.json.example`、`tools/mcp-official/**`、`.env.example` 注释、或对外文档中的 `npx -y …`。 |
| **易混点** | JSON 里的 **`id`**（如 `tavily-search`）是 Jachin 侧标识，**不等于** npm 包名；**包名**只看 `args` 里传给 `npx -y` 的字符串。 |
| **已踩坑案例（反例）** | `@tavily/mcp-server` → **npm 404，包不存在**。Tavily 官方本地 MCP 包名为 **`tavily-mcp`**（示例：`npx -y tavily-mcp@latest`）。 |
| **替代形态** | 部分厂商提供 **远程 MCP URL**（HTTPS + `mcp-remote` 等），与本地 `npx` 二选一；若采用须在文档中写清命令与鉴权，同样不得臆造 URL。 |

**自检命令示例**（开发机执行）：

```bash
npm view tavily-mcp version
npm view @modelcontextprotocol/server-filesystem version
```

**Cursor / PR 检查清单**：改 MCP 示例配置或新增「官方 MCP」条目时，勾选：□ 已 `npm view` 或网页核对包名 □ 已在本文或 PR 描述中写明核验方式。

---

## 四、 安全约束

- **仅高信任环境启用**：企业部署时可配置 `mcp_enabled: true`；C 端默认可关闭
- **工具白名单**：可配置 `mcp_tool_allowlist` 限制 Agent 可调用的工具
- **无网络隔离**：MCP 工具运行在宿主机，具备完整系统权限，需用户明确授权

---

## 五、 参考

- [MCP 官方文档](https://modelcontextprotocol.io/)
- [MCP 服务器列表](https://github.com/modelcontextprotocol/servers)
- `docs/whitepaper/06_LAYER2_EDGE.md` — L2 边缘与执行面总览（术语以四大原语为准）
