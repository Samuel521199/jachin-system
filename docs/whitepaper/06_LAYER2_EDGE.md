# 06 — Layer 2: 控制面 (V2)

**文档类型**: 白皮书 · Layer 2 详细说明  
**版本**: V2  
**基准**: [ARCHITECTURE_V2_LAYER3_STANDALONE.md](../ARCHITECTURE_V2_LAYER3_STANDALONE.md)

---

## 一、定位与职责 (V2)

Layer 2 是**控制平面 + 记忆平面 + 调度平面 + API Key 管理**。**不代理 L3 的推理请求**。

| 职责 | 说明 |
|------|------|
| **子账号创建** | 用户主账号在 L2 创建子账号，定义权限范围 |
| **权限管理** | 校验 L3 请求、子账号权限（L3 节点、L2 记忆与资源） |
| **API Key 管理** | L2 管理 API Key（存储、下发**密文**给 L3），**不代理推理** |
| **数据记忆** | 接收 L3 同步的记忆，经梦境优化后存储，供 L3 检索 |
| **梦境系统** | 对记忆进行聚类、去重、融合、冲突消解 |
| **L3 调度** | 当 L3 需要协同时，发布任务、选择节点、分配子任务 |
| **MCP 委托** | 调用方 L3 缺工具时，L2 **TaskManager** 入队并由目标 L3 Pull/HTTP 执行；**默认** L2 不跑 stdio MCP（`JACHIN_L2_STDIO_MCP=1` 可回滚） |

---

## 二、与 v8.0 的差异

| 维度 | v8.0 | V2 |
|------|------|-----|
| **执行引擎** | L2 运行 Agent + ReAct | **L3 运行**，L2 不执行用户任务 |
| **API Key** | L2 持有并代理请求 | L2 只管理，**密文下发** L3，L3 解密后直连 |
| **Skill** | L2 加载 | **L3 加载** |
| **记忆** | L2 存储 | L2 存储（不变），L3 定期同步 |

---

## 三、L2 API (V2)

| 接口 | 说明 |
|------|------|
| `POST /api/v2/auth/sync` | L3 注册，携带公钥 |
| `GET /api/v2/auth/poll?node_id=xxx` | L3 轮询审批状态（pending/approved + encrypted_api_keys） |
| `GET /api/v2/keys` | L3 拉取密文 Key（按 node_id + sub_account_id） |
| `POST /api/v2/memory/sync` | L3 同步本地记忆（请求体可声明 `namespace`），L2 梦境优化后回传 |
| `GET /api/v2/memory/search` | L3 检索记忆（`namespaces` 参数，按 allowed_memory_namespaces 过滤） |
| `POST /api/v2/coordinate/task` | L3 请求协同，L2 按 skill 匹配节点并分配子任务 |
| `GET /api/v2/coordinate/poll` | L3 拉取分配给自己的子任务 |
| `POST /api/v2/mcp/invoke` | L3 缺工具入口：L2 委托（Pull 优先 + HTTP 回退）；stdio 默认在 **调用方/目标 L3**（见 MCP_EXECUTION_MODEL v2.2、ARCHITECTURE_L3 v0.4） |
| `POST /api/v2/admin/sub-accounts` | 创建子账号 |
| `POST /api/v2/admin/keys` | 向保险箱添加 API Key |
| `POST /api/v2/admin/nodes/assign` | 将 L3 节点分配给子账号 |

---

## 四、数据存储

- **SQLite**: `~/.jachin/l2_control.db`
- **表**: `sub_accounts`, `sub_account_permissions`, `l3_nodes`, `api_keys_vault`, `coordinate_tasks`, `coordinate_subtasks`
- **LanceDB**: 记忆向量存储，含 `namespace` 列（细粒度隔离）
- **Redis**（L2 集群可选）: L3 在线状态、任务队列、Leader 选举锁
- **加密**: L2 用 Master Key 对称加密存储 Key；下发给 L3 时用 L3 公钥加密

---

## 五、记忆 Namespace 与 L2 无状态集群

- **Namespace 隔离**：子账号可配置 `allowed_memory_namespaces`，仅能读写指定命名空间；未配置时默认允许全部。
- **L2 无状态**：L3 状态与任务队列存 Redis，任意 L2 节点均可处理任意 L3 请求；Redis 不可用时回退 SQLite 单节点模式。

---

## 六、参考

- [ARCHITECTURE_V2_LAYER3_STANDALONE.md](../ARCHITECTURE_V2_LAYER3_STANDALONE.md)
- [V2_ARCHITECTURE_DIAGRAM.md](../V2_ARCHITECTURE_DIAGRAM.md)
