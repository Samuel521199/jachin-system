# Fleet ACL（P2）API 草案

**状态**：草案 — 与 `cloud/nexus/src/db/schema.ts` 中 `device_groups`、`device_group_members`、`edge_agents.device_group_id` 及扩展后的 `org_role` 对齐。实现时可选用 **REST（Next Route Handlers）** 或 **tRPC**，下列路径为示意。

**全局前提**

- 所有请求必须已通过 Auth.js 会话 / JWT 解析出 **当前用户**。
- **租户边界**：`X-Tenant-Id` / JWT `tenant_id` **必须**等于已校验的 `organizations.id`；与 P1 SSOT 一致。
- **RULE**：凡涉及 `edge_agents` 的查询与变更，**必须**在数据访问层附加 `organization_id = <verified org>`（及下文组作用域），防止 **Cross-Tenant IDOR**。

---

## 一、组织级角色 `org_role`（`organization_users.role`）

| 值 | 说明 |
|----|------|
| `owner` | 组织所有者 |
| `admin` | 组织管理员 |
| `member` | 普通成员 |
| `fleet_admin` | **车队管理员**：管理本 org 下设备组与边缘设备（含分组、组成员） |
| `viewer` | **只读成员**：仅查看舰队/设备/组信息，不可改 |

**中间件建议**：解析 `(userId, orgId)` → 查 `organization_users` → 挂载 `orgRole`。后续接口按最小权限校验。

---

## 二、组级角色 `device_group_member_role`（`device_group_members.role`）

| 值 | 说明 |
|----|------|
| `admin` | 该组内管理边缘设备与组成员（在 **org 级权限允许** 的前提下） |
| `viewer` | 该组只读 |

**有效权限** = `f(orgRole, groupMembership)`（应用层统一计算，不得只信其一）。
注释：**组表是在 org 级权限之下的细粒度覆写** — 例如仅将某用户限制在特定 `device_groups` 上可见。

---

## 三、REST 风格接口清单（草案）

基础前缀示例：`/api/v1/orgs/:orgId/...`（`:orgId` 必须与 session 中已验证租户一致）。

### 设备组 `device_groups`

| 方法 | 路径 | 说明 | 中间件 / 角色 |
|------|------|------|----------------|
| `GET` | `/device-groups` | 列出当前 org 下设备组 | `orgRole` ∈ `owner`,`admin`,`fleet_admin`,`viewer`；仅 `viewer` 时只读 |
| `POST` | `/device-groups` | 创建组 | `owner`,`admin`,`fleet_admin` |
| `GET` | `/device-groups/:groupId` | 组详情 | 同上；若启用组级 ACL，需额外校验用户对该 `groupId` 有 `device_group_members` 或 org 级足够角色 |
| `PATCH` | `/device-groups/:groupId` | 更新名称/描述 | `owner`,`admin`,`fleet_admin` 或 组 `admin`（策略由产品定） |
| `DELETE` | `/device-groups/:groupId` | 删除组（`edge_agents.device_group_id` 置 null 或拒绝非空组，由实现定） | `owner`,`admin`,`fleet_admin` |

### 组成员 `device_group_members`

| 方法 | 路径 | 说明 | 中间件 / 角色 |
|------|------|------|----------------|
| `GET` | `/device-groups/:groupId/members` | 列出组成员 | 可读组的用户 |
| `POST` | `/device-groups/:groupId/members` | 添加用户及 `role` | `owner`,`admin`,`fleet_admin` 或 组 `admin` |
| `PATCH` | `/device-groups/:groupId/members/:userId` | 修改组内角色 | 同上 |
| `DELETE` | `/device-groups/:groupId/members/:userId` | 移除组成员 | 同上 |

### 边缘设备 `edge_agents`

| 方法 | 路径 | 说明 | 中间件 / 角色 |
|------|------|------|----------------|
| `GET` | `/edge-agents` | 列出设备（**必须** `WHERE organization_id = :verifiedOrgId`，可选 `device_group_id` 过滤） | 按 org + 组可见性过滤 |
| `GET` | `/edge-agents/:agentId` | 单设备详情 | 校验 agent 属于该 org；组级用户仅能访问其组成员身份覆盖的组内设备 |
| `PUT` | `/edge-agents/:agentId/group` | 设置/清空 `device_group_id` | `fleet_admin` / `admin` / `owner` 或 组 `admin`（且目标组属于该 org） |
| `PATCH` | `/edge-agents/:agentId` | 其他字段更新 | 与现有一致 + org 过滤 |

**tRPC 映射**：可将上述资源拆为 `fleet.groups.*`、`fleet.agents.*` 等 router procedures，**在同一 `protectedProcedure` 内**注入 `verifiedOrganizationId` 与 `orgRole`，再执行组级检查。

---

## 四、实现检查清单

1. 任意 `edge_agents` 的 `SELECT`/`UPDATE`/`DELETE`：**WHERE** 子句含 `organization_id = ctx.verifiedOrgId`（或由 `device_groups.org_id` 连接保证同 org）。
2. 使用 `device_group_id` 时：校验对应 `device_groups.org_id = verifiedOrgId`。
3. `viewer`（org 或组）仅允许 GET 类操作。
4. 审计日志（可选）：记录跨组迁移设备、组成员变更。

---

## 五、参考

- `cloud/nexus/src/db/schema.ts` — `deviceGroups`、`deviceGroupMembers`、`edgeAgents.deviceGroupId`、`orgRoleEnum`
- `cloud/nexus/drizzle/0013_fleet_acl_device_groups.sql` — SQL 迁移
