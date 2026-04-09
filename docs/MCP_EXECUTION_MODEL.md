# MCP 执行模型 — L3 本机优先 + L2 TaskManager（目标）/ 兼容委托（现状）

**版本**: 2.2  
**状态**: 与 [ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md](./ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md) **v0.4 对齐**  
**定位**: 统一 MCP/Skill 执行策略；区分 **目标规格** 与 **仓库当前兼容实现**

**四大原语**：本文聚焦 **MCP** 的宿主与委托；与 **Tools**、**Skills**、**Agent Tasks** 并列定义见 **[Jachin 视角的「四大原语」终极架构规范.md](./Jachin%20视角的「四大原语」终极架构规范.md)**。

---

## 一、权威规格（目标态）

**单一事实来源**：`docs/ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md`

摘要：

| 原则 | 说明 |
|------|------|
| **L3 默认执行面** | stdio/本机 MCP 在 L3；L2 为 **TaskManager**（队列、路由、TTL、DLQ），不默认代跑 LOCAL_PINNED 工具链 |
| **拉取模型** | 边缘 L3 多在 NAT 后；跨节点任务须由 **L3→L2 持久反向信道** 拉取/订阅，**禁止**将「L2 单播 HTTP POST 到任意 L3」作为**唯一**投递手段（`inbound-capable` 节点可作补充） |
| **背压** | L3 可回报 **ResourceExhausted**；Manifest 含负载与 `max_concurrent_tasks` |
| **多维 Manifest + Affinity** | 路由不得仅按 `tool_name`；须 `os/arch/runtimes/tags` 与任务 **affinity** 匹配 |
| **凭证** | 跨节点 **禁止**转发用户完整 JWT；须 **L1 签发的 Task Token**（绑定 `task_id` + 能力范围） |
| **高敏载荷（P3）** | 可选 E2EE 信封，L2 **盲路由** |

---

## 二、执行策略（概念流程）

```
L3 需要调用某 MCP
    │
    ├─ 本机已声明且可执行？
    │   └─ 是 → 本机 MCPManager / mcp_registry 本地执行（默认路径）
    │
    ├─ 工具为 ROUTABLE 且需跨节点？
    │   └─ 目标态 → L3-A 创建任务 + Task Token → L2 入 **下行队列** → L3-B **Pull/Sub** 执行 → 事件回传
    │
    └─ 本机无工具、需兄弟节点代跑？
        └─ 现状兼容 → L3 POST L2 /api/v2/mcp/invoke → L2 可能 HTTP POST 至他机 L3 /api/v3/mcp/execute（见 §三）
```

**复杂多子任务**：由 L2 **任务编排**（coordinate / TaskManager）拆分与聚合；不以「同步阻塞 HTTP 长等」为默认。

---

## 三、当前仓库实现（L3 stdio 默认 + Pull 优先 + HTTP 回退）

| 环节 | 实现 |
|------|------|
| **L3 本机 stdio MCP** | `mcp_stdio_bootstrap`：`MCPManager.start()` + `scan_local_mcps`（`mcp_servers.json`、`inventory/mcps`）+ **`register_l3_packaged_stdio_mcps`**（`l3_mcp_cache` 中 L3_LOCAL 包的 `plugin.json` → `stdio_server`）。`mcp_registry` 合并 `l3_mcp_cache` 的 Python `tools[]` 与 Manager 工具。 |
| **L2 默认不跑 stdio** | `core/inventory_scanner.py` 在 L2 进程扫描 inventory 时若 `for_l2_host=True`（默认）且 **`JACHIN_L2_STDIO_MCP` 未开启**，不向 L2 的 `MCPManager` 注入侧载 MCP。`core/inventory_reloader.py` 仅在 `l2_stdio_mcp_enabled()` 时 `manager.start()` / `stop()`。 |
| **L2 工具列表** | `GET /api/v2/mcp/tools`：基础来自 Redis 聚合各 L3 心跳中的 `mcp_tools`（`core/l3_redis_state.aggregate_mcp_tools_catalog_from_redis`）；若 **`JACHIN_L2_STDIO_MCP=1`**，再与 L2 本机 `MCPManager` 合并（按工具名去重）。 |
| **L2 invoke** | `POST /api/v2/mcp/invoke`：若开启 L2 stdio 且本机可执行则本地调用；否则 **仅** 走委托（Pull 队列 → 目标 L3 HTTP，或 HTTP 回退），不在默认配置下于 L2 起 stdio 子进程。 |
| L3 缺工具 | `mcp_registry` → `invoke_via_l2` → `POST {L2}/api/v2/mcp/invoke`（`l2_gateway_config.json` 中 `sub_account_id` 存在时会设置 **`X-Sub-Account-Id`**） |
| L2 无本机工具、需兄弟 L3 代跑 | **优先**：Redis `l3_mcp_delegate_queue:{node_id}` + L3 `l3_node/mcp_delegate_pull_worker.py` 轮询 `GET /api/v2/mcp/delegate/poll`，结果 `POST /api/v2/mcp/delegate/result`（**无需 peer 入站 HTTP**）。**回退**：`get_l3_nodes_with_mcp_tool(..., require_l3_http_url=True)` → `POST {peer}/api/v3/mcp/execute`。 |
| L3 被调入口（HTTP 回退） | `l3_node/http_server.py` → `POST /api/v3/mcp/execute` |

**npm / npx**：`~/.jachin/mcp_servers.json` 中通过 `npx -y <包>` 拉起的 **包名必须与 npm registry 一致**，合并示例与文档前须核验，见 **`docs/MCP_SPEC.md` §3.5**。

环境变量：**`JACHIN_L2_STDIO_MCP=1`** — 在 L2 宿主机上恢复旧行为（本机 stdio MCPManager + `GET /tools` 合并本机列表）。**`JACHIN_MCP_DELEGATE_PULL=0`** 关闭 Pull 优先；**`JACHIN_MCP_PULL_WORKER=0`** 关闭 L3 侧拉取协程。无 Redis 时自动仅走 HTTP 回退。

**Task Token（跨节点委托）**：L2 在 Pull 与 HTTP 入站委托时签发 `task_token`（`core/mcp_task_token.py`），绑定 `task_id` + `tool_name` + 执行端 `node_id` + `sub_account_id`。L3 执行前校验；**`JACHIN_MCP_TASK_TOKEN_SECRET`** 建议在 L2 与所有 L3 设为同一密钥（未设时弱回退见模块说明）。**`JACHIN_MCP_DELEGATE_ALLOW_LEGACY_NO_TOKEN=1`** 允许 Pull 消费无令牌任务（仅迁移/排障）。**`JACHIN_L3_MCP_EXECUTE_ALLOW_LEGACY=1`** 允许 `/api/v3/mcp/execute` 不带令牌（不安全）。

**租户边界**：候选执行节点 = Redis 在线 ∩ SQLite `l3_nodes` 已分配给该子账号。

**LOCAL_PINNED**：`core/mcp_tool_locality.py` 列出的工具（与 L3 本地 MCP 列表一致，可 `~/.jachin/mcp_tool_locality.json` 覆盖）**禁止**跨节点委托。

**尚未落地**（仍见 ARCHITECTURE_L3）：由 **L1 中心化签发** Task Token（当前为 L2 对称密钥 `JACHIN_MCP_TASK_TOKEN_SECRET`）、多维 Manifest 严格路由、E2EE 信封、与 coordinate 共用的单条 WebSocket 多路复用。

---

## 四、与「路径 2 / 3」叙述的对应

| 路径 | 含义 | 与 v2.2 关系 |
|------|------|----------------|
| **路径 2** | MCP 长期在 L2 代跑、L3 仅代理 | ❌ 非长期目标；**回滚** `JACHIN_L2_STDIO_MCP=1` 可在 L2 恢复侧载 stdio（排查/迁移用） |
| **路径 3（L3_LOCAL）** | 制品在 L3 执行，订阅经 L2 同步到 `l3_mcp_cache` | ✅ 与「L3 默认执行面」一致；跨节点投递以 **§一** 为准，**§三** 为过渡 |

---

## 五、相关代码索引

| 用途 | 路径 |
|------|------|
| L2 是否启用本机 stdio | `core/l2_stdio_mcp_flag.py` |
| L2 invoke + 兼容委托 | `core/api/routes/v2_mcp.py` |
| L2 inventory 扫描（L2 默认跳过 MCP 注入） | `core/inventory_scanner.py`（`scan_local_mcps(..., for_l2_host=...)`） |
| L2 热重载与 MCP 生命周期 | `core/inventory_reloader.py` |
| Redis 工具目录聚合（`GET /tools`） | `core/l3_redis_state.py`（`aggregate_mcp_tools_catalog_from_redis` 等） |
| L3 在线与工具列表（委托用） | `core/l3_redis_state.py`（`write_l3_node_status`、`get_l3_nodes_with_mcp_tool`） |
| L3 stdio Host 启动 | `l3_node/mcp_stdio_bootstrap.py` |
| L3 注册表与 L2 转发 | `l3_node/primitives/mcp/registry.py` |
| L3 HTTP 执行入口 | `l3_node/http_server.py`（`/api/v3/mcp/execute`） |
| MCPManager / stdio 客户端 | `core/mcp_client.py`（L3 默认使用；L2 仅 `JACHIN_L2_STDIO_MCP=1` 时） |
| Task Token 签发/校验 | `core/mcp_task_token.py` |
| 工具 locality（禁止委托） | `core/mcp_tool_locality.py` |
| 委托目标 ∩ SQLite 分配 | `core/l3_node_db_filter.py` |

---

## 六、相关文档

- [ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md](./ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md) — TaskManager、NAT、Token、Manifest、Mermaid 图
- [ARCHITECTURE.md](./ARCHITECTURE.md) — 一店一库总览
- [SKILL_MCP_FLOW_AND_RECENT_CHANGES.md](./SKILL_MCP_FLOW_AND_RECENT_CHANGES.md) — 流转与 API 清单
