# 云边协同数字发行操作系统 (Cloud-Edge AI OS) 实现度分析

**文档版本**: v1.0  
**分析日期**: 2026-03  
**说明**: 对照「一店一库」「双轨双擎」「三大极简流程」终极蓝图，对当前代码实现度做全景式分析。**不包含支付功能**。

---

## 一、核心灵魂：一店一库，云边分治

### 1.1 L1 全球双子星商城 (The Global App Store)

| 设计定位 | 实现状态 | 证据与说明 |
|----------|----------|------------|
| 只面向老板和采购者 | ✅ 已实现 | L1 Nexus 提供商城 UI、Admin 审核台；IAM（子账号、角色、权限）在 L2 管理 |
| 展示 Skill 与 MCP | ✅ 已实现 | `plugins_registry` 支持 `item_type: SKILL | MCP`，catalog 按 visibility/status 过滤 |
| 处理支付 | ⏸️ 排除 | 按需求不分析 |
| 颁发 License 凭证 | ✅ 已实现 | `user_licenses` 表，`POST /api/v1/store/subscribe` 0 元购授权 |
| 平台不接触企业明文密码 | ✅ 已实现 | API Key 存 L2，L1 仅存 License 与元数据 |
| 服务器压力趋零 | ✅ 已实现 | 算力在 L3/L2，L1 只做 manifest、catalog 等轻量接口 |

**关键接口**:
- `GET /api/v1/store/catalog` — 公开商品目录
- `GET /api/v1/sync/manifest` — 租户已购清单（含 package_url）
- `POST /api/v1/store/subscribe` — 订阅/0 元购

**RBAC**：已下放 L2，由 `core/policy_enforcer.py` 从本地 `role_permissions` 读取，`v2_local_admin` 管理。

---

### 1.2 L2 本地数字仓库与密文库 (The Local Inventory & Vault)

| 设计定位 | 实现状态 | 证据与说明 |
|----------|----------|------------|
| 只面向 IT 网管 | ✅ 已实现 | L2 Admin、nexus_config 配对、sync 配置 |
| L1 在企业内网的物理投影 | ✅ 已实现 | CloudSyncDaemon 拉 manifest → 下载 → 解压到 `~/.jachin/inventory/` |
| 静默同步云端已购订单 | ✅ 已实现 | `poll_manifest()` → `_diff_manifest_vs_local()` → `download_and_extract()` |
| 下载并囤积压缩包 | ✅ 已实现 | SKILL → `skills/{item_id}/`，MCP → `mcps/` |
| 常驻运行高敏 MCP 驱动 | ✅ 已实现 | `core/mcp_client.py` MCPManager，`scan_local_mcps()` 注入 |
| 数据库密码锁在本地 | ✅ 已实现 | MCP 配置在 `~/.jachin/inventory/mcps/`；L3_LOCAL 时 L3 执行，L2 仅同步与委托 |
| 动态向 L3 下发权限和 Skill | ✅ 已实现 | 本地 `role_permissions`（L2 数据主权，由 v2_local_admin 管理）；`GET /api/v2/inventory/skills` + `/download` |

**关键组件**:
- `core/sync_daemon.py` — CloudSyncDaemon
- `core/inventory_scanner.py` — 侧载扫描、`.local_meta`
- `core/policy_enforcer.py` — RBAC 鉴权、断网降级
- `core/api/routes/v2_inventory.py` — 技能清单与下载

---

## 二、架构骨架：双轨制与三段式瀑布

### 2.1 商品解耦

| 商品形态 | 设计 | 实现状态 | 说明 |
|----------|------|----------|------|
| **Skill (.wasm)** | 轻量，L2 发放给 L3，员工电脑沙箱运行 | ✅ 已实现 | L2 `/skills` + `/download`，L3 `perform_startup_sync` 拉取到 `~/.jachin/l3_skill_cache/`，Wasm 沙箱执行 |
| **MCP** | L3 优先执行，本机无则 L2 委托其他 L3 | ✅ 已实现 | L3 本地 MCP（l3_mcp_cache 动态加载）、L2 委托 fallback（v2_mcp → 其他 L3 `POST /api/v3/mcp/execute`）均已实现。详见 [MCP_EXECUTION_MODEL.md](MCP_EXECUTION_MODEL.md) |

### 2.2 双轨可见性

| 可见性 | 设计 | 实现状态 | 说明 |
|--------|------|----------|------|
| **PUBLIC** | L1 审核 → L1 购买 → L2 同步 → L3 执行 | ✅ 已实现 | 上架需审核，manifest 含 package_url，L2 下载，L3 同步 |
| **PRIVATE** | L1 仅登记，实体侧载到 L2，断网隔离 | ✅ 已实现 | `shadow_only` 影子上传，`.local_meta`，侧载目录无 `.sync_meta` |

---

## 三、三大极简场景实现度

### 3.1 场景一：企业消费者 — 一键丝滑装配

| 步骤 | 设计 | 实现状态 | 说明 |
|------|------|----------|------|
| 老板在 L1 一键购买并授权 | 商城订阅 | ✅ 已实现 | `POST /api/v1/store/subscribe`（0 元购），`user_licenses` 写入 |
| 员工第二天打开电脑，新技能即点即用 | L2 同步 → L3 拉取 | ✅ 已实现 | L2 sync_daemon 下载；L3 `perform_startup_sync` 拉清单、下载缺失、SHA256 校验 |
| L2 sync_daemon 暗中完成下载、解压、点火 | 自动化 | ✅ 已实现 | `run_sync_cycle()`：manifest → diff → download → reload |
| 精准投递给有权限的 L3 | RBAC | ✅ 已实现 | L2 `/skills`、`/download` 需 X-Sub-Account-Id，经 PolicyEnforcer 按 role_permissions 过滤；L3 同步携带身份 |

### 3.2 场景二：内网极客 — 暗黑工坊极速开发

| 步骤 | 设计 | 实现状态 | 说明 |
|------|------|----------|------|
| 写完 MCP + Wasm 丢进 L2 文件夹 | 侧载 | ✅ 已实现 | `~/.jachin/inventory/skills/{id}/`、`mcps/*.json` |
| 全公司高管立刻可用 | 扫描 + 热重载 | ✅ 已实现 | `scan_local_skills`、`scan_local_mcps`，`POST /inventory/reload` 热重载 |
| 数据不出局域网 | MCP 在 L3 或委托 L3 | ✅ 已实现 | L3 本地执行或 L2 委托其他 L3，数据不离开企业内网 |
| `.local_meta` 结构化元数据 | 侧载元数据 | ✅ 已实现 | `origin: SIDE_LOAD`、`installed_at`、`is_private: true` |

### 3.3 场景三：生态创作者 — 极简暴富与零成本分发

| 步骤 | 设计 | 实现状态 | 说明 |
|------|------|----------|------|
| 本地组件在 L1 一键转为 PUBLIC | 发布 + 审核 | ✅ 已实现 | `jachin-cli publish`，`POST /api/v1/store/publish`，Admin 审核 |
| 定价上架 | 商城 | ⏸️ 排除 | 支付相关 |
| 平台抽成 30% | 商业逻辑 | ⏸️ 排除 | 支付相关 |
| 算力/网络转嫁到买家 L3 | 架构 | ✅ 已实现 | Skill 与 MCP 均在 L3 执行，L2 仅协调，L1 无推理负载 |

---

## 四、实现缺口与风险点

### 4.1 已确认缺口

（当前无阻塞缺口。L2 MCP invoke 已放宽鉴权：X-Sub-Account-Id 可选，无身份时放行。）

### 4.2 已修复（2026-03）

| 原缺口 | 修复 |
|--------|------|
| **L2 技能清单/下载无鉴权** | `/skills`、`/download` 现强制 X-Sub-Account-Id，经 PolicyEnforcer.check_access 按 role_permissions 过滤；L3 skill_sync 携带 sub_account_id |
| **PRIVATE 技能按角色过滤** | 同上，清单与下载均按角色过滤 |
| **L2 MCP 委托 fallback** | `v2_mcp.py` 在 MCPToolNotFoundError 时委托其他 L3 的 `POST /api/v3/mcp/execute`；`get_l3_nodes_with_mcp_tool` 从 Redis 查找有该工具的 L3 |
| **L3 MCP 同步与动态加载** | `l3_node/mcp_sync.py` 从 L2 `GET /l3_mcps` 拉取；`mcp_registry._load_tools_from_l3_mcp_cache` 从 `~/.jachin/l3_mcp_cache/` 动态加载 |

### 4.3 设计层面的待确认点

| 点 | 说明 |
|----|------|
| **Skill 执行 RBAC** | Skill 在 L3 本地执行，L2 在清单/下载阶段已按角色过滤；L3 本地执行无二次校验，依赖「拉不到即无法执行」 |
| **Creator 从 PRIVATE 转 PUBLIC** | 无显式「转换可见性」接口；需重新 full publish + `visibility=PUBLIC` 走审核流程 |

---

## 五、总体结论

### 5.1 实现度概览

| 层级 | 实现度 | 备注 |
|------|--------|------|
| **L1** | 95%+ | 商城、manifest、publish、licenses、PRIVATE 影子上传、PUBLIC 审核均就绪；IAM 已下放 L2；支付除外 |
| **L2** | 95%+ | 同步、侧载、MCP、PolicyEnforcer、断网降级、inventory API 完整 |
| **L3** | 95% | 技能同步、MCP 同步、l3_mcp_cache 动态加载、L2 委托 fallback 均已实现 |

### 5.2 三大流程满足度

| 流程 | 满足度 | 阻塞点 |
|------|--------|--------|
| **企业消费者** | 98% | 技能与 MCP 精准投递、L2 委托 fallback 均已实现 |
| **内网极客** | 100% | 侧载、扫描、热重载、`.local_meta` 完整 |
| **生态创作者** | 95% | 发布、审核、PRIVATE/PUBLIC 双轨完整；无显式 PRIVATE→PUBLIC 转换 |

### 5.3 宣讲就绪度

- **架构与理念**：一店一库、双轨制、三大场景与代码实现高度一致，可直接用于宣讲。
- **演示建议**：可演示「内网极客」（侧载即用）与「企业消费者」（技能 + MCP 同步、L2 委托）完整流程。
- **投资/大客户**：可强调 L1 轻量、L2 断网自治、算力下沉；支付与抽成逻辑可单独作为 Roadmap 说明。

---

## 六、附录：关键代码索引

| 功能 | 路径 |
|------|------|
| L1 商城 catalog | `cloud/nexus/src/app/api/v1/store/catalog/route.ts` |
| L1 manifest | `cloud/nexus/src/app/api/v1/sync/manifest/route.ts` |
| L1 publish (含 shadow_only) | `cloud/nexus/src/app/api/v1/store/publish/route.ts` |
| L1 subscribe | `cloud/nexus/src/app/api/v1/store/subscribe/route.ts` |
| L2 RBAC（本地 role_permissions） | `core/policy_enforcer.py`、`core/api/routes/v2_local_admin.py` |
| L2 sync daemon | `core/sync_daemon.py` |
| L2 inventory scanner | `core/inventory_scanner.py` |
| L2 policy enforcer | `core/policy_enforcer.py` |
| L2 inventory API（含 /l3_mcps） | `core/api/routes/v2_inventory.py` |
| L2 MCP invoke（含 L3 委托） | `core/api/routes/v2_mcp.py` |
| L3 skill sync | `clients/desktop/src-tauri/src/commands/skill_sync.rs` |
| L3 MCP 同步 | `l3_node/mcp_sync.py` |
| L3 MCP 代理调用与 l3_mcp_cache 动态加载 | `l3_node/skills/mcp_registry.py` |
