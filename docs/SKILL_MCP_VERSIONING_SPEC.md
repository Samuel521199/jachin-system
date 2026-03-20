# Skill / MCP 版本化与更新规范

**版本**: 1.0  
**日期**: 2026-03  

---

## 一、设计目标

- Skill 和 MCP 支持语义化版本号（major.minor.patch）
- 通过版本迭代，更新后上传云端
- 本地可选择是否拉取新版本
- 拉取新版本时：**删旧拉新**，原子性更新

---

## 二、数据流

```
L1 manifest: { id, version, package_url, package_sha256, ... }
     ↓
L2 sync_daemon: 比较 local .sync_meta.version vs manifest.version
     ↓
若 manifest.version > local：下载到临时目录 → 校验 → 删除旧目录 → 解压新包 → 写出配置 → 热重载
     ↓
L2 inventory API: 返回 skills/mcps（含 version 字段）
     ↓
L3 skill_sync / mcp_sync: 比较 L2 返回的 version vs 本地 plugin.json.version
     ↓
若远程更新：删除 l3_*_cache/{id}/ → 解压新包 → 写出配置
```

---

## 三、版本比较规则

- 使用 `_parse_version("1.2.3")` → `(1, 2, 3)` 元组比较
- `manifest.version > local.version` 时触发更新
- `package_url` 变更也触发更新（兼容旧逻辑）

---

## 四、本地元数据

| 层级 | 存储位置 | 字段 |
|------|----------|------|
| L2 Skills | `~/.jachin/inventory/skills/{id}/.sync_meta` | package_url, item_id, version, wasm_sha256 |
| L2 MCP (L3_LOCAL) | `~/.jachin/inventory/l3_mcps/{id}/.sync_meta` | package_url, item_id, version |
| L2 MCP (L2_GATEWAY) | `~/.jachin/inventory/mcps/.{id}.sync_meta` | package_url, item_id, version |
| L3 Skills | `~/.jachin/l3_skill_cache/{id}/plugin.json` | version |
| L3 MCP | `~/.jachin/l3_mcp_cache/{id}/plugin.json` | version |

---

## 五、API 变更

### L1 GET /api/v1/sync/manifest

响应项新增：
- `version`: 语义化版本，如 "1.0.0"
- `changelog`: 可选，更新说明

### L2 GET /api/v2/inventory/skills

技能项已含 `version`（来自 plugin.json 或 registered_local_skills）。

### L2 GET /api/v2/inventory/l3_mcps

MCP 项新增 `version`（来自 plugin.json）。

---

## 六、更新触发方式

| 方式 | 说明 |
|------|------|
| **自动** | L2 sync_daemon 定时轮询 manifest，发现新版本即下载 |
| **手动** | 用户点击 L2 管理面板「同步」或 L3「检查更新」→ 触发 trigger-sync |
| **配置** | 可扩展：仅提示不自动、或完全禁用自动更新 |

---

## 七、配置写出（config_writeout）

更新时保持 076 规范的 merge 策略：
- `overwrite_if_missing`：目标不存在才写入
- `copy_missing`：仅复制目标不存在的文件
- 不覆盖用户已修改的配置
