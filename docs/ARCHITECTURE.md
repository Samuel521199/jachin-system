# Jachin 云边协同数字发行操作系统 — 架构规范

**版本**: V2 (2026-03)  
**状态**: 当前实现基准  
**定位**: 一店一库、双轨双擎、三大极简流程

---

## 一、核心范式

### 1.1 一店一库，云边分治

| 层级 | 定位 | 职责 |
|------|------|------|
| **L1 全球商城** | 商业收银台 | 展示 Skill/MCP、处理订阅、颁发 License；不接触企业明文密码，不提供推理算力 |
| **L2 本地数字仓库** | 企业数字金库 | L1 在企业内网的物理投影；静默同步已购订单、下载囤积包、运行 MCP、向 L3 下发权限与 Skill |

### 1.2 双轨制

| 商品形态 | 流转 |
|----------|------|
| **Skill (.wasm)** | 轻量，L2 发放给 L3，员工电脑沙箱运行 |
| **MCP** | 重，死锁 L2 网关，绝不下发 L3；L3 通过 HTTP 代理调用 |

| 可见性 | 流转 |
|--------|------|
| **PUBLIC** | L1 审核 → 购买 → L2 同步 → L3 执行 |
| **PRIVATE** | L1 仅登记；实体侧载到 L2，断网隔离 |

---

## 二、三层架构

```
┌─────────────────────────────────────────────────────────────────┐
│  L1 (cloud/nexus) — 平台                                         │
│  用户主账号、商城、manifest、licenses；IAM 已下放 L2              │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  L2 (core/) — 控制面 + 数字仓库                                   │
│  子账号、权限、API Key 保险箱、记忆、L3 调度、MCP、inventory       │
│  不代理推理                                                       │
└─────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  L3 (l3_node/ + clients/desktop) — 执行面                         │
│  单体 Agent、多 Skill、本地记忆；持密文 Key 直连 LLM API           │
└─────────────────────────────────────────────────────────────────┘
```

---

## 三、关键组件

| 组件 | 路径 | 说明 |
|------|------|------|
| L1 商城 | `cloud/nexus/src/app/api/v1/store/` | catalog、publish、subscribe、licenses |
| L1 同步 | `cloud/nexus/src/app/api/v1/sync/` | manifest |
| L2 同步 | `core/sync_daemon.py` | CloudSyncDaemon：manifest → 下载；RBAC 本地管理 |
| L2 仓库 | `core/inventory_scanner.py` | 侧载扫描、`.local_meta` |
| L2 权限 | `core/policy_enforcer.py` | RBAC、断网降级、role_permissions |
| L2 清单 | `core/api/routes/v2_inventory.py` | `/skills`、`/download`（需 X-Sub-Account-Id） |
| L2 MCP | `core/api/routes/v2_mcp.py` | `/invoke`（需 X-Sub-Account-Id） |
| L3 同步 | `clients/desktop/src-tauri/src/commands/skill_sync.rs` | 从 L2 拉取技能 |
| L3 Agent | `l3_node/agent_core.py` | ReAct、工具调用 |

---

## 四、数据流

### 4.1 企业消费者（一键装配）

1. L1 订阅 → `user_licenses`
2. L2 `poll_manifest` → `download_and_extract` → `~/.jachin/inventory/`
3. L2 本地 `role_permissions`（RBAC 由 L2 管理，不依赖 L1；见 `v2_local_admin`）
4. L3 `perform_startup_sync` → `GET /skills`（带 X-Sub-Account-Id）→ `GET /download` → `~/.jachin/l3_skill_cache/`

### 4.2 内网极客（侧载）

1. 将 MCP/Wasm 放入 `~/.jachin/inventory/`
2. L2 `scan_local_*` → 生成 `.local_meta`
3. `POST /inventory/reload` 热重载

### 4.3 生态创作者（发布）

1. `jachin-cli publish` → L1 `POST /store/publish`
2. PRIVATE：`shadow_only=true`，仅 metadata
3. PUBLIC：完整包 → `status=pending` → Admin 审核

---

## 五、存储

| 存储 | 用途 |
|------|------|
|  PostgreSQL | L1：plugins_registry、user_licenses（IAM 已下放 L2） |
| SQLite | L2：sub_accounts、role_permissions、api_keys_vault、l3_nodes |
| LanceDB | L2：向量记忆 |
| 文件系统 | `~/.jachin/inventory/`、`~/.jachin/l3_skill_cache/`、`~/.jachin/l2_control.db` |

---

## 六、禁止项（已废弃）

- `core/dapr/`、`core/ray_cluster/`、`core/memory/schema/`（已移除）
- Dapr、Ray、Qdrant、PostgreSQL 作为 L2 主存储

---

## 七、附录：关键 API

| 接口 | 说明 |
|------|------|
| `GET /api/v1/store/catalog` | 公开商品 |
| `GET /api/v1/sync/manifest` | 租户已购清单 |
| `POST /api/v1/store/subscribe` | 订阅 |
| `POST /api/v2/local-admin/roles/assign` | L2 本地 RBAC 角色权限（L2 数据主权） |
| `GET /api/v2/inventory/skills` | 技能清单（需 X-Sub-Account-Id） |
| `GET /api/v2/inventory/skills/{id}/download` | 下载（需 X-Sub-Account-Id） |
| `POST /api/v2/mcp/invoke` | MCP 调用（需 X-Sub-Account-Id） |
