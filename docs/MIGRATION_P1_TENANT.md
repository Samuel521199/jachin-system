# P1 迁移指南：Organization = Tenant（SSOT）

> **文档性质**：本文档保留 **历史迁移与运维** 语境（含当时 DDL/列名表述）。**当前业务设计与实现 SSOT** 以白皮书 [05_LAYER1_NEXUS.md](./whitepaper/05_LAYER1_NEXUS.md) **§〇·一（划时代极简设计原则）** 及 `cloud/nexus` 代码为准；新功能开发请勿以本文替代该节。

**目标**：消除 `users.tenant_id` 与 `organizations.id` 双轨语义；租户隔离边界 **唯一** 为 `organizations.id`，用户归属 **唯一** 为 `organization_users`。

**配套代码**：`cloud/nexus/src/db/schema.ts`、`cloud/nexus/src/lib/tenant.ts`
**SQL 草案**：`cloud/nexus/drizzle/0012_p1_tenant_ssot.sql`

---

## 1. 语义约定

| 概念 | 说明 |
|------|------|
| **tenant_id**（HTTP / JWT / Cookie） | 必须等于 `organizations.id` 的 UUID 字符串 |
| **个人用户** | 迁移后为每个无组织成员关系的用户自动插入一行 `organizations`（`is_personal_default = true`）及 `organization_users`（`owner`） |
| **禁止** | 将 `users.id` 当作 `tenant_id` 写入 `user_licenses`、`telemetry_logs` 等新数据 |

---

## 2. 执行顺序（生产）

1. **备份数据库**。
2. **应用 SQL**（可与部署窗口一起执行）：
   - 运行 `drizzle/0012_p1_tenant_ssot.sql` 内容，或经 `drizzle-kit migrate` 等效执行。
3. **部署 L1**：确保已发布包含本 P1 改动的 `cloud/nexus` 构建。
4. **验证**：
   - `SELECT id, is_personal_default FROM organizations LIMIT 5;`
   - `SELECT COUNT(*) FROM users u LEFT JOIN organization_users ou ON ou.user_id = u.id WHERE ou.id IS NULL;` → 应为 **0**。

---

## 3. SQL 步骤说明（向前兼容）

1. **`ALTER organizations ADD is_personal_default`**
   现有行默认 `false`，不影响已存在企业组织。

2. **回填「个人默认组织」**
   对「在 `organization_users` 中没有任何行」的用户：
   - `INSERT organizations`：`name` 可为 `Personal workspace`，`is_personal_default = true`。
   - `INSERT organization_users`：`role = 'owner'`。

3. **`ALTER users DROP COLUMN tenant_id`**
   应用层已不再读取该列；若线上仍有旧脚本写入，需先停写再 DROP。

---

## 4. 数据修复（可选）

- **历史 `user_licenses.tenant_id` / `telemetry_logs.tenant_id`**：若曾误存 `users.id`，需离线脚本将 `tenant_id` 映射为对应用户的 **个人默认组织 `organizations.id`**（可通过 `organization_users` + `organizations.is_personal_default` 查找）。

---

## 5. JWT / 客户端

- 签发 JWT 时：`tenant_id`（或专用 `org_id`）应设为 **`organizations.id`**；`sub` 保持为 **`users.id`**。
- L2 仅用 `X-Tenant-Id` + 服务令牌时：中间件至少校验 **组织存在**；若携带用户 `sub`，则额外校验 **`organization_users`**（见 `tenant.ts`）。

---

## 6. 回滚

- 无自动回滚：DROP 列后需从备份恢复。若仅在测试环境，可从备份还原 `users.tenant_id` 列并删除误建的个人组织行（谨慎）。

---

## 7. 检查清单

- [ ] 备份已做
- [ ] `0012` 已执行
- [ ] 无用户缺少 `organization_users`
- [ ] JWT 与 Cookie 中 `tenant_id` 已为组织 UUID
- [ ] 监控 L1 API 4xx 是否因 `tenant_id` 格式变更上升
