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

将 `config/mcp_servers.json.example` 复制为 `~/.jachin/mcp_servers.json`，按需修改路径：

```json
{
  "mcp_servers": [
    {
      "id": "filesystem",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "~/.jachin/workspace"],
      "env": null
    },
    {
      "id": "shell",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-shell"],
      "env": null
    }
  ]
}
```

**注意**：`server-filesystem` 的路径参数需为绝对路径，Windows 下请使用 `C:\\Users\\YourUser\\.jachin\\workspace` 等形式。

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
