# 插件管理 API — 删除与隐藏

**版本**: 1.0
**关联**: L1 Nexus、L2 Core

---

## 一、L1 管理 API

**鉴权**: `X-Admin-Token` 或 `Authorization: Bearer <NEXUS_ADMIN_SECRET>` 或 cookie `nexus_admin_token`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/admin/plugins/{id}/hide` | 隐藏：visibility = PRIVATE，商城不展示，manifest 不再下发 |
| POST | `/api/v1/admin/plugins/{id}/unhide` | 取消隐藏：visibility = PUBLIC |
| POST | `/api/v1/admin/plugins/{id}/archive` | 归档下架：status = 'archived'，商城与 manifest 均不再展示 |
| POST | `/api/v1/admin/plugins/{id}/restore` | 从归档恢复：status = 'approved' |

**id 参数**: 支持 UUID 或 pluginId（如 `com.jachin.bi.daily_report`）

**示例**:
```bash
curl -X POST "https://nexus.jachin/api/v1/admin/plugins/com.jachin.bi.daily_report/hide" \
  -H "X-Admin-Token: $NEXUS_ADMIN_SECRET"
```

---

## 二、L2 管理 API

**鉴权**: `X-Sub-Account-Id` 或 `Authorization: Bearer <session_token>`

### Skill

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v2/inventory/skills/{item_id}/hide` | 隐藏：从列表排除，L3 不可见、不可下载 |
| POST | `/api/v2/inventory/skills/{item_id}/unhide` | 取消隐藏 |
| DELETE | `/api/v2/inventory/skills/{item_id}` | 卸载：移入回收站（已有） |

### L3_LOCAL MCP

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v2/inventory/l3_mcps/{item_id}/hide` | 隐藏：从列表排除，L3 不可见、不可下载 |
| POST | `/api/v2/inventory/l3_mcps/{item_id}/unhide` | 取消隐藏 |
| DELETE | `/api/v2/inventory/l3_mcps/{item_id}` | 删除：从 inventory/l3_mcps 移除 |

**隐藏列表存储**: `~/.jachin/hidden_inventory.json`

---

## 三、效果对比

| 操作 | L1 | L2 |
|------|----|----|
| **隐藏** | 商城不展示，manifest 不再下发 | 列表不返回，L3 不可下载 |
| **归档** | 商城与 manifest 均不再展示 | — |
| **删除** | — | Skill 移入回收站；MCP 直接删除 |
