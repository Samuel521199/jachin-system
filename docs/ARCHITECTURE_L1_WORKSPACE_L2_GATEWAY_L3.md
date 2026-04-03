# L1 工作区 · L2 网关 · L2↔L3 配对 — 架构权威说明

**版本**: 2026-04  
**状态**: **仓库现行架构**（P0～P2；**P3** 多工作区 manifest；**slug**；**L3 向导**；**ORG_REQUIRED** 已落地，见 §2）。  
**本文定位**：术语边界、产品语义、**当前实现摘要**、代码入口；与实现冲突时以代码为准，并应回写修订本文。

---

## 0. 术语边界（必读，避免误读）

| 说法 | 正确定义 |
|------|-----------|
| **谁和谁「配对」？** | **只有 L2 与 L3 之间存在配对关系**（零信任：`auth/sync`、`auth/poll`、密钥下发等）。这是 **控制面（L2）↔ 设备/边缘（L3）** 的协议。 |
| **L1 与 L3** | **L1 不与 L3 配对。** L1 不提供 L3 的配对接口，L3 也不向 L1 建立配对信道。L1 负责 **自然人账号、工作区（组织）、订阅与商店**；必要时仅通过 **只读 API**（如 `GET /api/v1/me/workspaces`）给 **L3 设备端** 填表用，属于 **元数据**，不是「L1↔L3 配对」。 |
| **L1 与 L2** | **L1↔L2** 是 **控制面与云端的信任建立**（`nexus_config`、manifest、心跳），与 L3 无关。 |
| **人怎么操作？** | **工作区 owner/admin** 使用 **L1 账号** 登录 **L2** `http://<L2>:18888/gateway/`（或本地 `admin` 运维账号），在 **L2 界面** 审批 **L2↔L3**、管理子账号；**不在 L1 控制台审批 L3**。 |
| **L3 上填工作区** | L3 **只向 L2** 发 `auth/sync`，body 含 **`organization_id`** 或 **`organization_slug`**（及可选 `workspace_name`）；已配对 L2 时解析后的 UUID 须落在 **`nexus_config.sync_tenant_ids`**。工作区列表可从 L1 **`GET /api/v1/edge/me/workspaces`**（edge Bearer）拉取，**仅为填表**。 |

---

## 1. 目标产品语义（与实现对齐）

### 1.1 身份与工作区（L1）

- **L1 账号 = 自然人**；注册 **仅** 创建 `users`，**不**自动创建组织。
- **工作区** = `organizations` + `organization_users`；创建时 **必填展示名**（`/console/workspace`、`POST /api/v1/organizations/create`）。
- **L2 `/gateway/`**：仅 **组织角色 `owner` / `admin`**（及本地 **`admin`**）可完成 L1 相关登录与 Web Bridge；普通成员会收到 **403**（`verify-credentials`、`mint`/`redeem`、`GET /api/v1/l2-gateway/gateway-access`、**快速登录** 等路径）。
- **L1↔L2**：邮箱+密码、Web Bridge、CLI 辅助；见 `docs/L1_L2_PAIRING_AND_WEB_BRIDGE.md`。

### 1.2 网关界面（L2 `/gateway/`）

- **工作区成员**：`GET /api/v2/admin/workspace-members`（L2→L1 服务端密钥拉取）。
- **子账号 `l1_pairing_code`**：界面 **不展示**（数据层可保留）。

### 1.3 L2↔L3 配对

- **L3 → L2**：`POST /api/v2/auth/sync` 含 **`organization_id` 或 `organization_slug`**（已配对 L2 时须落在 **`sync_tenant_ids`**）、可选 `workspace_name`。
- **审批**：L2 `/gateway/` 待审批列表含 **工作区标识**；管理员分配 `sub_account` 后 `auth/poll` 下发密钥。  
- **协议细节**：`docs/PAIRING_PROTOCOL_SPEC.md`（**禁止**将本流程写成 L1↔L3 配对）。

### 1.4 部署形态

- **常见**：单 L2 ↔ 一个或多个 L1 工作区：`nexus_config.tenant_id` 为**活动租户**；`sync_tenant_ids` 为需合并同步 manifest 的 UUID 列表。
- **L1 manifest**：L2 用同一 edge Bearer 对每租户带 **`X-Tenant-Id`** 拉取并合并去重（见 `sync/manifest`）。

---

## 2. 当前实现摘要（仓库事实）

| 层级 | 要点 |
|------|------|
| **L1 注册** | `registerUserOnly`（`cloud/nexus/src/lib/auth/genesis.ts`）；`POST /api/auth/register`。 |
| **L1 会话** | `auth.ts` JWT：`listOrganizationsForUser` + 默认工作区；无组织时 `orgId` 为空，`/console` 重定向至 `/console/workspace`。 |
| **L1 API** | `GET /api/v1/me/workspaces`、`GET /api/v1/edge/me/workspaces`、`GET /api/v1/me/workspace-role`、`GET /api/v1/l2-gateway/gateway-access`、`GET /api/v1/l2-gateway/workspace-members`（服务端密钥）等。 |
| **L1↔L2** | `verify-credentials`；Web Bridge `mint`/`redeem`（owner/admin）；`nexus_config` 落盘见 `v2_admin._persist_l1_pairing_to_l2`。 |
| **L2 网关鉴权** | Admin JWT 含 `workspace_gateway_access`；`get_current_admin` 对显式 `false` 拒绝；**快速登录** 前调 L1 `gateway-access`（需 `NEXUS_BASE_URL`）。 |
| **P3 多工作区** | `nexus_config.sync_tenant_ids`；`core/sync_daemon.py` 多租户 manifest 合并；`/gateway`：`POST /api/v2/admin/nexus-profile`、`GET /api/v2/admin/l1-workspaces-edge`。 |
| **slug** | `organizations.slug`（可选唯一）；`POST .../organizations/create`；L3 `organization_slug`；`GET /api/v1/edge/resolve-org?slug=`。 |
| **L3 向导** | `GET .../l3/setup`；`POST /api/v3/setup/workspaces`、`save-gateway-org`。 |
| **onboarding** | `fleet` / `instances` / `fleet/deploy` 与 `withOrgRole`：已登录无 `orgId` → **`403` + `ORG_REQUIRED`**。 |
| **L2↔L3** | `v2_auth.auth_sync` 校验 **允许组织集合**（`sync_tenant_ids`）；`l3_nodes`；`bootstrap.py`：`JACHIN_ORGANIZATION_ID` / `JACHIN_ORGANIZATION_SLUG`、`l2_gateway_config.json`。 |

**历史数据**：迁移或老用户仍可能存在 `is_personal_default` 个人组织；**新注册路径**不再自动创建。

---

## 3. 余量（可按产品继续收紧）

| 项 | 说明 |
|----|------|
| **其它控制台 API** | 未全部逐路由加 `ORG_REQUIRED`；可按业务列表补全。 |
| **slug 强制** | 创建组织时 slug 仍为可选；契约主键仍为 UUID。 |

---

## 4. 相关文档

- `docs/L1_L2_PAIRING_AND_WEB_BRIDGE.md` — L1↔L2 网关与桥接。  
- `docs/PAIRING_PROTOCOL_SPEC.md` — **L2↔L3** 零信任 API 与配置路径。  
- `docs/README.md` — 文档索引。

---

## 5. 代码入口

| 区域 | 路径 |
|------|------|
| L1 注册 | `cloud/nexus/src/lib/auth/genesis.ts`、`app/api/auth/register/route.ts` |
| L1 会话 | `cloud/nexus/src/auth.ts` |
| L1 网关 / 桥接 / 权限 | `app/api/v1/l2-gateway/*`、`app/api/v1/l2-bridge/*` |
| L2 管理 / 登录 | `core/api/routes/v2_admin.py`、`core/admin_auth.py` |
| L2 网关 UI | `core/admin_ui/index.html`、`l1-bridge-callback.html` |
| **L2↔L3** | `l3_node/bootstrap.py`、`core/api/routes/v2_auth.py`、`l3_node/http_server.py`（`/l3/setup`） |
| **L1 edge 工作区** | `app/api/v1/edge/me/workspaces`、`edge/resolve-org` |
| **L1 DB** | `drizzle/0014_organizations_slug.sql`（`organizations.slug`） |

---

## 6. 修订记录

| 日期 | 说明 |
|------|------|
| 2026-04 | 初版、术语边界、改造清单 |
| 2026-04 | 对齐 L2↔L3 / 非 L1↔L3 表述 |
| 2026-04 | **改版**：删除过时「差距表」；改为现行实现摘要 + P3/余量；与代码一致 |
| 2026-04 | **P3 + slug + L3 向导 + ORG_REQUIRED** 落地；更新 §2/§3 |
