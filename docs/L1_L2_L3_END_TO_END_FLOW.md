# L1-L2-L3 端到端流程指南

**版本**: V2 (2026-04)  
**适用场景**: L1 云端已启动，L2 与 L3 在同一台机器上运行，跑通「发布 → 同步 → 分配」完整链路。

**L1↔L2 信任建立**（架构、白名单、API）以 **[L1_L2_PAIRING_AND_WEB_BRIDGE.md](./L1_L2_PAIRING_AND_WEB_BRIDGE.md)** 为准：优先 **L2 `/gateway` → L1 注册邮箱+密码**（无跳转）或 **Nexus 账号登录（Web Bridge）**；无 Web/无头时用 **CLI 6 位码** 辅助。

---

## 一、前置条件

| 组件 | 状态 | 说明 |
|------|------|------|
| **L1 云端** | ✅ 已启动 | `cd cloud/nexus && npm run dev`，默认 http://localhost:3000 |
| **L2 网关** | 待启动 | FastAPI，端口 18888 |
| **L3 桌面端** | 待启动 | Tauri 桌面应用，连接 L2 |

**环境要求**:
- Python 3.10+
- Node.js 18+
- L1 需配置 `DATABASE_URL`（PostgreSQL）以持久化 Store、配对、License 等数据

---

## 二、流程总览

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  1. L2 与 L1 建立信任（nexus_config.json）                                    │
│     主：/gateway 填 L1 邮箱+密码，或「Nexus 账号登录」→ L1 l2-bridge 回跳兑换   │
│     辅：python -m core.cli pair → 6 位码 → L1 确认（无头/恢复）                │
│     配对成功后 L2 热启心跳与 CloudSync（一般无需仅为配对而重启 L2）              │
├─────────────────────────────────────────────────────────────────────────────┤
│  2. L1 发布技能                                                              │
│     L1 Store / The Forge 上传 .zip → 审核通过 → plugins_registry             │
├─────────────────────────────────────────────────────────────────────────────┤
│  3. L1 订阅技能（租户授权）                                                   │
│     L1 Store 订阅 → user_licenses (tenant_id = instance_id)                  │
├─────────────────────────────────────────────────────────────────────────────┤
│  4. L2 从 L1 拉取 manifest 并下载                                            │
│     CloudSyncDaemon 轮询 /api/v1/sync/manifest → 下载到 ~/.jachin/inventory  │
├─────────────────────────────────────────────────────────────────────────────┤
│  5. L2 分配技能给 L3                                                          │
│     L2 本地管理台 /admin → 角色 → 勾选物资 → 保存 → role_permissions         │
├─────────────────────────────────────────────────────────────────────────────┤
│  6. L3 连接 L2 并获取权限                                                     │
│     桌面端输入 L2 地址 → 发起神经接驳 → L2 审批 → auth/poll 获取 allowed_skills │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 三、详细步骤

### 步骤 1：L2 与 L1 建立信任（写入 nexus_config）

L2 需有效 `~/.jachin/nexus_config.json` 才能拉取 manifest 与上报遥测。

#### 方式 A（推荐，无跳转）：L1 注册邮箱 + 密码

1. 配置 L2 `NEXUS_BASE_URL` 指向可达的 L1（容器内需能访问该地址）；`BRAIN_BASE_URL` 用于 Web 回跳（本方式可不依赖）。详见 [L1_L2_PAIRING_AND_WEB_BRIDGE.md](./L1_L2_PAIRING_AND_WEB_BRIDGE.md)。
2. 启动 L2 后打开 `http://<L2>:18888/gateway/`（或你的 `BRAIN_BASE_URL/gateway/`）。
3. **用户名**填 L1 注册邮箱，**密码**填 L1 密码，点击登录。L2 向 L1 校验后写入 `nexus_config.json` 并签发 Admin JWT，并 **热启** manifest 同步与 L1 心跳。

#### 方式 B：Nexus 账号登录 / Web Bridge（OAuth-only 或偏好浏览器在 L1 域登录）

1. 配置 L2 `NEXUS_BASE_URL`、`BRAIN_BASE_URL`；L1 配置 `L2_BRIDGE_ALLOWED_RETURN_PREFIXES`（须覆盖 `BRAIN_BASE_URL` 前缀）。
2. 启动 L2 后打开 `/gateway/`。
3. 点击 **「使用 Nexus 账号登录」**，在 L1 登录并确认授权，回跳后写入 `nexus_config.json` 并签发 Admin JWT（同样会热启后台任务）。

#### 方式 C（辅助）：CLI 6 位配对码（无头 / SSH / 恢复）

**C.1 确保 L1 已启动**

```powershell
cd cloud\nexus
npm run dev
# 访问 http://localhost:3000 确认可打开
```

**C.2 执行配对**

```powershell
# 在项目根目录
python -m core.cli pair --base-url http://localhost:3000
```

终端会显示 6 位配对码（如 `X7A-9K2`）。

**C.3 在 L1 确认**

1. 打开 http://localhost:3000/console/pair（或 `/pair`）
2. 输入 6 位码并确认
3. CLI 轮询成功后写入 `~/.jachin/nexus_config.json`

**验证配置**（方式 A/B/C 通用）

```json
// ~/.jachin/nexus_config.json
{
  "instance_id": "jachin-xxxxxxxx",
  "access_token": "jch-xxxxxxxx",
  "nexus_base_url": "http://localhost:3000",
  "l1_user_id": "00000000-0000-0000-0000-000000000001"
}
```

**重要**：`instance_id` 将作为 L2 的 `tenant_id`，用于 manifest 鉴权和 License 归属。

---

### 步骤 2：L1 发布技能

技能需先发布到 L1 Store，并审核通过，才能被 L2 订阅和同步。

**2.1 准备技能包**

- 根目录包含 `plugin.json`（含 `id`、`name`、`version` 等）
- 含 `.wasm` 文件（SKILL）或 MCP 配置
- 打包为 `.zip`

**2.2 通过 Store 发布**

1. 打开 http://localhost:3000/store 或 The Forge
2. 上传 `.zip` 包
3. 填写 `visibility`（PUBLIC 需审核，PRIVATE 可影子上传）
4. 提交发布

**2.3 审核（若为 PUBLIC）**

- 打开 http://localhost:3000/dashboard/admin/review
- 审核通过后，`plugins_registry.status` 变为 `approved`

**2.4 记录 `item_id`**

- 发布成功后，Store 会返回或展示 `plugin_id` 和 `item_id`（UUID）
- `item_id` 为 `plugins_registry.id`，订阅时使用

---

### 步骤 3：L1 订阅技能（租户授权）

L2 拉取 manifest 时，L1 只返回该 `tenant_id` 下 `user_licenses` 中 ACTIVE 的物资。因此需先完成订阅。

**3.1 确定 tenant_id**

- 使用配对后的 `instance_id` 作为 `tenant_id`

**3.2 订阅方式 A：通过 L1 Web（Cookie）**

1. 打开 http://localhost:3000/store
2. 在浏览器控制台执行，设置 tenant cookie：
   ```javascript
   document.cookie = "nexus_tenant_id=" + encodeURIComponent("你的instance_id") + "; path=/; max-age=31536000";
   ```
3. 刷新页面，找到目标技能，点击「订阅」或调用订阅接口

**3.3 订阅方式 B：直接调用 API**

```powershell
# 将 INSTANCE_ID 替换为 nexus_config.json 中的 instance_id
# 将 ITEM_UUID 替换为 plugins_registry 中该技能的 id (UUID)

curl -X POST "http://localhost:3000/api/v1/store/subscribe" `
  -H "Content-Type: application/json" `
  -H "X-Tenant-Id: 你的instance_id" `
  -d '{"item_id": "ITEM_UUID"}'
```

**3.4 验证订阅**

- 查询 `user_licenses` 表，应有 `tenant_id = instance_id`、`item_id = 技能UUID`、`status = ACTIVE` 的记录

---

### 步骤 4：启动 L2 并从 L1 拉取技能

**4.1 启动 L2 网关**

```powershell
.\scripts\start-layer2.ps1
# 选择 [3] Gateway（L2 审批网关）
# 或直接：.\scripts\run-gateway.ps1
```

L2 将监听 http://localhost:18888。

**4.2 确保 nexus_config 含 tenant_id**

编辑 `~/.jachin/nexus_config.json`，添加或确认：

```json
{
  "tenant_id": "你的instance_id",
  "instance_id": "你的instance_id",
  "access_token": "jch-xxx",
  "nexus_base_url": "http://localhost:3000"
}
```

若未显式设置 `tenant_id`，sync_daemon 会使用 `instance_id`。

**4.3 云边同步**

- L2 启动后，`CloudSyncDaemon` 会定期（默认 60 秒）请求 `GET /api/v1/sync/manifest`
- 携带 `Authorization: Bearer <access_token>` 和 `X-Tenant-Id: <tenant_id>`
- L1 返回该租户已订阅的物资清单
- L2 对比 `~/.jachin/inventory/`，下载新增/更新的包到 `~/.jachin/inventory/skills/<item_id>/` 或 `mcps/`

**4.4 验证下载**

```powershell
dir $env:USERPROFILE\.jachin\inventory\skills
# 应能看到已同步的技能目录
```

---

### 步骤 5：L2 分配技能给 L3（本地 IAM）

L2 数据主权：权限由 L2 本地管理，不依赖 L1 下发。

**5.1 登录 L2 管理台**

1. 打开 http://localhost:18888/admin
2. 使用默认账号登录：`admin` / `admin123`

**5.2 创建角色并分配物资**

1. 左侧「角色」：输入 `role_id`（如 `r_dev`），点击「添加」
2. 选中该角色
3. 右侧「物资大盘」会列出本地所有技能（L1 同步的 PUBLIC + 本地侧载的 PRIVATE）
4. 勾选要分配给该角色的技能/MCP
5. 点击「保存」

**5.3 创建子账号并绑定角色**

1. 打开 L2 Admin API 或 `admin_ui`：http://localhost:18888/admin（若集成子账号管理）
2. 创建子账号，设置 `role_id` 为刚创建的角色（如 `r_dev`）
3. 或通过 API：`POST /api/v2/admin/sub-accounts` 创建，并在 DB 中设置 `sub_accounts.role_id`

**5.4 审批 L3 节点并绑定子账号**

- L3 注册后，在 L2 管理台将节点分配给该子账号
- API：`POST /api/v2/admin/nodes/assign`，body: `{ "node_id": "xxx", "sub_account_id": "xxx" }`

---

### 步骤 6：L3 连接 L2 并获取技能权限

**6.1 启动 L3 桌面端**

```powershell
.\scripts\start-layer3.ps1
# 或 cd clients\desktop && npm run tauri:dev
```

**6.2 发起神经接驳**

1. 在 GatewayConnectScreen 输入 L2 地址：`http://localhost:18888`
2. 点击「发起神经接驳」
3. L3 会调用 `POST /api/v2/auth/sync` 注册，获得 `node_id`

**6.3 L2 审批**

1. 打开 http://localhost:18888/admin
2. 在节点列表中，将待审批节点分配给已配置好角色权限的子账号
3. 或调用 `POST /api/v2/admin/nodes/assign`

**6.4 L3 获取 Key 与权限**

- L3 轮询 `GET /api/v2/auth/poll?node_id=xxx`
- 审批通过后，返回 `encrypted_api_keys` 和 `allowed_skills`（来自子账号的 role_permissions）
- L3 用本地私钥解密 API Key，并根据 `allowed_skills` 过滤可调用的技能

**6.5 L3 同步 MCP（L3_LOCAL）**

- L3 获批后，`l3_node/bootstrap.py` 调用 `sync_mcps_from_l2()`（`l3_node/mcp_sync.py`）
- 请求 `GET /api/v2/inventory/l3_mcps` 获取清单，下载缺失/变更的包到 `~/.jachin/l3_mcp_cache/`
- `mcp_registry` 从 l3_mcp_cache 动态加载工具；本机无工具时走 `invoke_via_l2`（**兼容** 链路至 peer，目标 Pull 见 ARCHITECTURE_L3）

---

## 四、数据流与关键表

| 层级 | 表/存储 | 关键字段 |
|------|---------|----------|
| **L1** | `plugins_registry` | id, plugin_id, status, package_url, visibility |
| **L1** | `user_licenses` | tenant_id, item_id, status |
| **L1** | `edge_agents` | id(=instance_id), pairing_code, auth_token, status |
| **L2** | `~/.jachin/nexus_config.json` | instance_id, access_token, tenant_id, nexus_base_url |
| **L2** | `~/.jachin/inventory/skills/` | 同步的 Wasm 技能目录 |
| **L2** | `~/.jachin/inventory/l3_mcps/` | 同步的 L3_LOCAL MCP 目录 |
| **L2** | `roles`, `role_permissions` | role_id, item_id |
| **L2** | `sub_accounts` | id, role_id |
| **L2** | `l3_nodes` | id, sub_account_id |
| **L3** | `~/.jachin/l3_skill_cache/` | 从 L2 拉取的 Wasm 技能 |
| **L3** | `~/.jachin/l3_mcp_cache/` | 从 L2 拉取的 L3_LOCAL MCP（mcp_sync 同步） |

---

## 五、常见问题

### Q1: L2 拉取 manifest 返回空列表

- 检查 `user_licenses` 是否有 `tenant_id = instance_id` 且 `status = ACTIVE`
- 检查 `nexus_config.json` 中 `tenant_id` 或 `instance_id` 是否与 `user_licenses.tenant_id` 一致
- 检查 `plugins_registry` 中对应插件 `status = approved`

### Q2: L3 看不到技能

- 确认 L2 `/api/v2/admin/inventory` 有该技能
- 确认角色已分配该技能的 `item_id`（格式如 `skill:xxx` 或 `mcp:xxx`）
- 确认子账号的 `role_id` 正确，且 L3 节点已分配给该子账号

### Q3: 配对后 nexus_config 无 tenant_id

- 手动添加 `"tenant_id": "你的instance_id"` 到 `nexus_config.json`

### Q4: L1 无 Store 订阅入口

- 可先用 API 直接调用 `POST /api/v1/store/subscribe`
- 或通过 SQL 手动插入 `user_licenses`（需知道 `plugins_registry.id` 的 UUID）

### Q5: L2 日志报 503 Service Unavailable（heartbeat / manifest / telemetry）

**现象**：`[L1Heartbeat] 心跳失败: Server error '503 Service Unavailable' for url 'http://localhost:3000/api/v1/...'`

**根因**：L1 (Nexus) 在 localhost:3000 返回 503，常见于：
1. **L1 未启动** — 若 L1 未运行，通常为 Connection Refused；503 表示有服务在 3000 端口响应
2. **Next.js 冷启动/编译中** — `npm run dev` 首次请求或热重载时，Next.js 可能返回 "Server is temporarily busy"
3. **PostgreSQL 未启动** — 若 `DATABASE_URL` 已配置但数据库不可用，部分 L1 接口可能异常

**处理**：
1. 确保 L1 已启动：`.\scripts\start-cloud.ps1` 或 `cd cloud/nexus && npm run dev`
2. 等待 L1 完全就绪（浏览器访问 http://localhost:3000 可打开）
3. 若使用 PostgreSQL，确保服务已启动（默认 localhost:5432）
4. L2 已内置 503 重试（最多 3 次），短暂 503 会自动恢复

---

## 六、快速自检清单

- [ ] L1 已启动，http://localhost:3000 可访问
- [ ] L2 已配对，`~/.jachin/nexus_config.json` 含 access_token、instance_id
- [ ] L1 已发布并审核通过至少一个技能
- [ ] L1 已订阅该技能（user_licenses 有记录，tenant_id = instance_id）
- [ ] L2 已启动，http://localhost:18888 可访问
- [ ] `~/.jachin/inventory/skills/` 下有同步的技能目录
- [ ] L2 管理台已创建角色并分配物资
- [ ] L2 已创建子账号并绑定角色
- [ ] L3 已连接 L2 并完成审批
- [ ] L3 可正常调用已分配技能
- [ ] L3 获批后已同步 MCP（`~/.jachin/l3_mcp_cache/` 有内容）；跨机 MCP 依赖兼容路径或未来 TaskManager

---

**相关文档**:
- [PAIRING_PROTOCOL_SPEC.md](./PAIRING_PROTOCOL_SPEC.md) — **L2↔L3** 配对（非 L1↔L3）
- [ARCHITECTURE_V2_LAYER3_STANDALONE.md](./ARCHITECTURE_V2_LAYER3_STANDALONE.md) — V2 架构
- [QUICKSTART.md](./QUICKSTART.md) — 快速开始
