# 06 — Layer 2: 控制面 (V2)

**文档类型**: 白皮书 · Layer 2 详细说明  
**版本**: V2.3  
**更新日期**: 2026-06  
**基准**: [ARCHITECTURE.md](../ARCHITECTURE.md) · [MCP_EXECUTION_MODEL.md](../MCP_EXECUTION_MODEL.md)

---

## 一、定位与职责

Layer 2（`core/`）是 **控制平面 + 数字仓库 + 可选记忆平面**。**不代理 L3 推理**。

| 职责 | 说明 |
|------|------|
| **子账号** | L2 创建；`X-Sub-Account-Id` 贯穿 inventory/MCP |
| **RBAC** | `policy_enforcer.py`；断网从 `role_permissions` 降级 |
| **API Key 保险箱** | 密文存储；RSA 加密下发 L3；L3 解密后直连 LLM |
| **数字仓库** | L1 manifest → `~/.jachin/inventory/`；侧载 `.local_meta` |
| **MCP 委托** | TaskManager + Redis Pull + HTTP 回退 + Task Token |
| **记忆（可选）** | LanceDB + Dream Weaver；`/api/v2/memory/*` |
| **L3 协同** | `coordinate` 任务分配（多节点场景） |

---

## 二、与 v8.0 / Legacy 的差异

| 维度 | v8.0 Legacy | V2 现行 |
|------|-------------|---------|
| **ReAct 执行** | `core/agent_loop.py` | **L3** `run_agent` |
| **stdio MCP Host** | L2 daemon 常见 | **L3 默认**；L2 仅 `JACHIN_L2_STDIO_MCP=1` |
| **API Key** | L2 可代理请求 | L2 只管理；**L3 直连** |
| **Wasm** | L2 `wasm_runner` 仍可存在 | **L3** 加载 `jpp:*` |
| **Omni 大脑** | `core/daemon` + event_bus | **L3** `ws_server.py:18981` |

Legacy 模块（`agent_loop.py`、`daemon.py`、`swarm/`）保留供兼容/测试，**非桌面主路径**。

---

## 三、L2 API 速查

| 接口 | 说明 |
|------|------|
| `POST /api/v2/auth/sync` | L3 注册公钥 |
| `GET /api/v2/auth/poll` | L3 轮询审批 + 密文 Key |
| `GET /api/v2/keys` | 按 node + sub_account 拉 Key |
| `GET /skills` · `GET /download` | 技能清单与下载（需 `X-Sub-Account-Id`） |
| `GET /l3_mcps` · `GET /l3_mcps/{id}/download` | L3_LOCAL MCP 制品 |
| `GET /tools` · `POST /invoke` | MCP 委托（Pull 优先） |
| `POST /api/v2/memory/sync` · `GET .../search` | 可选集中记忆 |
| `POST /api/v2/coordinate/task` 等 | 多 L3 协同 |
| `POST /api/v2/admin/*` | 子账号、Key、节点分配 |

本地管理：`v2_local_admin.py`（IAM 下放 L2）。

---

## 四、MCP 执行模型（摘要）

```text
调用方 L3 缺工具
  → L2 GET /tools（Redis 聚合在线节点能力）
  → L2 POST /invoke → TaskManager 入队
  → 目标 L3 Pull Worker 执行 stdio MCP
  → HTTP 回退（须 Task Token）
```

- **LOCAL_PINNED** 工具禁止跨节点委托（`mcp_tool_locality.py`）。
- **stdout 噪声过滤**：`core/mcp_stdio_noise_filter.py`（L3 import 时生效）。
- **路径预检**：`inventory_scanner.py` 跳过无效 filesystem/git 根。

规格：[ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md](../ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md)

---

## 五、数据存储

| 存储 | 路径/说明 |
|------|-----------|
| SQLite | `~/.jachin/l2_control.db` — sub_accounts, l3_nodes, api_keys_vault, coordinate_* |
| LanceDB | `~/.jachin/lancedb_data/` — 向量记忆、Semantic Router skills 表 |
| Redis | **可选** — L2 集群：在线 L3、Pull 队列、Leader 锁 |
| inventory | `~/.jachin/inventory/` — skills, mcps, l3_mcps |

**加密**：Master Key 加密 Key 库；下发 L3 用 RSA 公钥加密。

---

## 六、记忆与 Dream Weaver

- L2 `dream_weaver.py`：LanceDB 碎片聚类、去重、冲突标记。
- Hybrid search + MMR：`GET /api/v2/memory/search`；权重见 `memory_scoring`、[MEMORY_SCORING.md](../MEMORY_SCORING.md)。
- **与 L3 Memory Nexus 分立**：L3 宿主默认 SQLite Nexus，**不强制** sync 到 L2。

---

## 七、可插拔向量引擎（L2）

- `core/embedding/` + `core/vector_router.py` — Semantic Router 技能路由。
- 配置：`nexus_config.json` → `embedding_mode: cloud|local`。
- 详见 [PLUGGABLE_VECTOR_ENGINE.md](./PLUGGABLE_VECTOR_ENGINE.md)。

---

## 八、参考

- [FILE_STRUCTURE.md](../FILE_STRUCTURE.md)
- [CURRENT_SYSTEM_ARCHITECTURE.md](../architecture/CURRENT_SYSTEM_ARCHITECTURE.md)
- [L3_SLIM_DISTRIBUTION_AND_SUBSCRIBED_ARTIFACTS.md](../L3_SLIM_DISTRIBUTION_AND_SUBSCRIBED_ARTIFACTS.md)
