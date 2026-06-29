# 05 — Layer 1: Jachin Nexus (平台)

**文档类型**: 白皮书 · Layer 1 详细说明  
**版本**: V2.3  
**更新日期**: 2026-06  
**基准**: [ARCHITECTURE_L1_WORKSPACE_L2_GATEWAY_L3.md](../ARCHITECTURE_L1_WORKSPACE_L2_GATEWAY_L3.md) · Schema: [`cloud/nexus/src/db/schema.ts`](../../cloud/nexus/src/db/schema.ts)

---

## 〇、与现行实现对齐（必读）

### V2.2 工作区显式 Onboarding

- `POST /api/auth/register` **仅**写入 `users`。
- 用户登录后在 **`/console/workspace`** **创建或加入**组织，`organization_users` 写入角色后 JWT 才有 `orgId`/`orgRole`。
- **不再**对新注册用户自动调用 `ensurePersonalWorkspace`。
- 历史数据可能仍含 `is_personal_default=true` 的个人组织；**新用户路径**以显式 onboarding 为准。

### 组织即租户（SSOT）

- 租户 = `organizations.id`；API/JWT 中为 `orgId`。
- **禁止**从 `users.tenant_id` 推断租户；**必须**查 `organization_users`。
- 业务写操作：`withOrgRole`（`lib/with-org-role.ts`）；机器桥：`extractTenantIdAllowingMachineFallback`。

---

## 一、定位与哲学

Layer 1 是 **平台 SaaS**：商城、订阅、License、组织与舰队元数据、IM Webhook 入队。

| 戒律 | 说明 |
|------|------|
| **不存 L3 隐私记忆** | 对话、梦境、本地文件均在 L2/L3 |
| **不跑用户推理** | 无 LLM 代理层 |
| **商业** | B 端舰队 + C 端商城；`plugins_registry`、`user_licenses`、`transactions` |

**与 L2/L3 边界**：L1 不直接驱动 L3 stdio；经 manifest → L2 inventory → L3 cache。

---

## 二、核心模块

### 2.1 身份与工作区

- **Auth.js**：Credentials + OAuth；Drizzle Adapter；JWT 含 `orgId`/`orgRole`。
- **组织 API**：`organizations/create`、`members/invite`、`members/join`、`active-org`、`list`。
- **Magic Join**：邀请 JWT 验签后直接 INSERT `organization_users`（无邮件状态机表）。

### 2.2 商城 Store

| API | 说明 |
|-----|------|
| `GET /store/catalog` | 商品列表 |
| `POST /store/publish` | 开发者发布（`tools/jachin-cli publish`） |
| `POST /store/subscribe` | 订阅 → `user_licenses` |
| `GET /store/licenses` | 许可证查询 |
| `POST /store/unpublish` | 下架 |

### 2.3 同步与制品

- `GET /sync/manifest` — L2 `CloudSyncDaemon` 轮询入口。
- `POST /forge/publish` — 蓝图/插件元数据。
- `plugins_registry` — Skill/MCP/Wasm 元数据；PUBLIC 需 Admin 审核（`admin/review/*`）。

### 2.4 舰队 Fleet

- `edge_agents` — 设备心跳、`current_blueprint_id`、IM 绑定。
- `device_groups` + `device_group_members` — 组级 ACL（P2）。
- `POST /fleet/deploy` — 批量蓝图/部署指令。
- **红线**：所有 fleet 查询 **WHERE organization_id = 已验证 org**。

### 2.5 L2 网关桥接

| API | 说明 |
|-----|------|
| `l2-gateway/verify-credentials` | L2 网关邮箱登录验 L1 账号 |
| `l2-bridge/mint` · `redeem` | Web Bridge 六位码 |
| `pairing/request` · `confirm` · `status` | CLI 辅助配对 |

### 2.6 IM 网关

- `webhooks/telegram` 等 → `agent_message_queue`。
- `agents/bind-im` — 设备与 IM 账号绑定。
- 执行面在 **L3**；L1 只做入队与回传路由。

### 2.7 桌面分发

- `cloud/jachin-downloads/` — 安装包 CDN/更新通道（独立应用）。
- `desktop/releases` — 版本与 presign。

---

## 三、多租户与权限

### 3.1 组织角色 `org_role`

| 角色 | 摘要 |
|------|------|
| `owner` / `admin` / `member` | 常规组织角色 |
| `fleet_admin` | 舰队管理 |
| `viewer` | 只读 |

### 3.2 权限链路

```text
User → organization_users (org + role)
     → [可选] device_group_members
     → edge_agents (organization_id 硬边界)
```

详见 [API_FLEET_ACL_DRAFT.md](../API_FLEET_ACL_DRAFT.md)。

### 3.3 关于「个人默认工作区」

**V2.2 前**：曾自动为个人用户创建 `is_personal_default` 组织以实现单轨多租户。  
**V2.2 后**：产品改为 **显式创建/加入工作区**；`is_personal_default` 字段保留供历史数据与迁移，**新注册路径不依赖自动生根**。

---

## 四、数据底座（Drizzle 摘要）

| 表 | 用途 |
|----|------|
| `users`, `accounts`, `sessions` | Auth.js |
| `organizations`, `organization_users` | 租户与成员 |
| `device_groups`, `device_group_members` | 舰队分组 ACL |
| `edge_agents` | 边缘设备 |
| `blueprints` | AST/策略 JSON |
| `plugins_registry`, `user_licenses`, `transactions` | 商城 |
| `agent_message_queue`, `deploy_commands` | IM/部署队列 |
| `desktop_app_releases`, `telemetry_logs` | 桌面与遥测 |

完整定义：`cloud/nexus/src/db/schema.ts`。

---

## 五、去 BaaS 化状态

| 项 | 状态 |
|----|------|
| Drizzle + PostgreSQL | ✅ 已落地 |
| Auth.js 闭环 | ✅ 已落地 |
| Redis 队列替代 DB 轮询 | ⏳ 规划（见 [09_DE_BAASIFICATION.md](./09_DE_BAASIFICATION.md)） |
| MinIO / Helm 私有化 | ⏳ 规划 |

---

## 六、废弃声明

1. ~~Dapr Pub/Sub 中继~~ → HTTP/WS 混合；目标 Jachin Mesh。
2. ~~注册自动建 org~~ → V2.2 显式 workspace。
3. ~~L1 企业子账号 IAM~~ → 下放 L2 `v2_local_admin`。

---

## 七、参考

- [USER_GUIDE_NEXUS_QUICK.md](../USER_GUIDE_NEXUS_QUICK.md)
- [L1_L2_PAIRING_AND_WEB_BRIDGE.md](../L1_L2_PAIRING_AND_WEB_BRIDGE.md)
- [09_DE_BAASIFICATION.md](./09_DE_BAASIFICATION.md)
