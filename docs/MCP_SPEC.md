# MCP 接入规范 (Model Context Protocol)

**版本**: v8.0 (The Singularity OS)  
**定位**: Layer 2 双轨制引擎 — 轨道 A

---

## 一、 划时代意义

**瞬间继承全球最大的 AI 工具生态**。Layer 2 实现 MCP Client，GitHub 上现成的文件读写、本地 Shell 控制、PostgreSQL 查询、Git 操作等数百个标准 MCP 服务器，Jachin 可**免代码、开箱即用**。直接拥有最高级别的系统控制力。

**适用场景**：极其信任的本地环境（个人设备、企业内网边缘节点）。

---

## 二、 架构位置

| 轨道 | 形态 | 信任级别 | 用途 |
|------|------|----------|------|
| **A** | MCP 宿主 | 高信任 | 本地系统控制、开箱工具 |
| B | SKILL.md | 用户可控 | 声明式轻量技能 |
| C | Wasm 沙箱 | 零信任 | 商城第三方付费插件 |

---

## 三、 实现要点

### 3.1 核心组件

- **位置**: `core/mcp_client.py`
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

```json
{
  "mcp_servers": [
    {
      "id": "filesystem",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user"]
    },
    {
      "id": "shell",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-shell"]
    }
  ]
}
```

---

## 四、 安全约束

- **仅高信任环境启用**：企业部署时可配置 `mcp_enabled: true`；C 端默认可关闭
- **工具白名单**：可配置 `mcp_tool_allowlist` 限制 Agent 可调用的工具
- **无网络隔离**：MCP 工具运行在宿主机，具备完整系统权限，需用户明确授权

---

## 五、 参考

- [MCP 官方文档](https://modelcontextprotocol.io/)
- [MCP 服务器列表](https://github.com/modelcontextprotocol/servers)
- `docs/whitepaper/06_LAYER2_EDGE.md` — 双轨制引擎总览
