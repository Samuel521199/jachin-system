# 03 — 业务流程 (The Workflow)

**文档类型**: 白皮书 · 业务流程  
**版本**: V2.3  
**更新日期**: 2026-06  
**基准**: [L1_L2_L3_END_TO_END_FLOW.md](../L1_L2_L3_END_TO_END_FLOW.md)

---

## 一、新设备觉醒流

### 1.1 L1 账号与工作区（V2.2+）

| 步骤 | 主体 | 行为 |
|------|------|------|
| 1 | 用户 | 在 L1 注册（**仅**创建 `users`）或 OAuth 登录 |
| 2 | 用户 | 进入 `/console/workspace`，**创建或加入**组织 → `organization_users` |
| 3 | JWT | Auth.js 注入 `orgId` / `orgRole`；业务 API 经 `withOrgRole` 校验 |

### 1.2 L1↔L2 控制面信任

| 步骤 | 主体 | 行为 |
|------|------|------|
| 1 | 管理员 | L2 `/gateway`：L1 邮箱+密码 或 Nexus OAuth |
| 2 | 或 | L1 `/console/l2-bridge` → `bridge_code` → 写入 `~/.jachin/nexus_config.json` |
| 3 | 辅助 | `python -m cli.jachin_cli pair` 六位码（无头/恢复） |

详见 [L1_L2_PAIRING_AND_WEB_BRIDGE.md](../L1_L2_PAIRING_AND_WEB_BRIDGE.md)。

### 1.3 L2↔L3 零信任配对（主流程）

| 步骤 | 主体 | 行为 |
|------|------|------|
| 1 | Tauri 桌面 | GatewayConnectScreen 输入 L2 地址，「发起神经接驳」 |
| 2 | `l3_node` | 生成 RSA 密钥对，`POST /api/v2/auth/sync` 注册 |
| 3 | L2 管理员 | `POST /api/v2/admin/nodes/assign` 分配子账号 |
| 4 | `l3_node` | 轮询 `GET /api/v2/auth/poll`，收到密文 API Key，解密后 bootstrap |
| 5 | `l3_node` | 启动 `ws://127.0.0.1:18981/sensory`；桌面 Omni 连接 |

详见 [PAIRING_PROTOCOL_SPEC.md](../PAIRING_PROTOCOL_SPEC.md)。

---

## 二、商城订阅 → L3 执行（一键装配）

| 步骤 | 主体 | 行为 |
|------|------|------|
| 1 | L1 | 用户订阅 → `user_licenses` |
| 2 | L2 | `sync_daemon` poll manifest → 下载到 `~/.jachin/inventory/` |
| 3 | L2 | `policy_enforcer` 按 `role_permissions` RBAC |
| 4 | L3 | `skill_sync` → `GET /skills` + `/download` → `l3_skill_cache/` |
| 5 | L3 | `mcp_sync` → `GET /l3_mcps` → `l3_mcp_cache/`；`mcp_stdio_bootstrap` 注册 stdio |
| 6 | L3 | `run_agent` 加载工具池（Native + MCP + Skill 白名单）执行用户任务 |

---

## 三、跨网 IM 通讯流（Telegram / Lark 等）

**原则**：L1 做 Webhook 入队；**执行在 L3**（非 L2 Agent Loop）。

| 步骤 | 主体 | 行为 |
|------|------|------|
| 1 | 用户 IM | 发送自然语言指令 |
| 2 | L1 | `/api/v1/webhooks/{platform}` → 清洗 → `agent_message_queue` |
| 3 | L2/L3 | **过渡期**：L2/L3 拉取 pending；**目标态**：L1 WS 长连推送 |
| 4 | **L3** | Lark/Telegram channel 注入 → `run_agent` ReAct → 工具调用 |
| 5 | L1/L3 | 结果经 Callback 或 channel 回传 IM |

L3 原生 IM 实现：`l3_node/channels/lark/` 等；配置见 [L3_LARK_CONFIG_SINGLE_SOURCE.md](../L3_LARK_CONFIG_SINGLE_SOURCE.md)。

---

## 四、前台对话 vs 后台重负荷

| 类型 | 路径 | 说明 |
|------|------|------|
| 前台 | `run_agent` 同步 | `foreground_tool_policy` 超时；长任务应转后台 |
| 后台 | `core:submit_background_task` | Worker 队列；断电对账 → `zombie_tasks.json` |
| 恢复 | `core:check_interrupted_tasks` | 新会话晨会；WS 事件 `zombie_tasks_pending` |

SSOT：[前台闲聊与后台重负荷任务的物理隔离与背压熔断.md](../前台闲聊与后台重负荷任务的物理隔离与背压熔断.md)

---

## 五、梦境与记忆压缩（L2 可选）

| 步骤 | 主体 | 行为 |
|------|------|------|
| 1 | L2 | 短期对话/日志写入 LanceDB 或 SQLite 碎片 |
| 2 | L2 | `dream_weaver.py` / `dreamer.py` 空闲或阈值触发 |
| 3 | L2 | 聚类、去重、冲突标记 → 更新 core_memory |
| 4 | L3 | 宿主侧默认走 **Memory Nexus** 回合末 `commit_drawer`，与 L2 梦境 **并行可选** |

---

## 六、舰队蓝图下发（B 端）

| 步骤 | 主体 | 行为 |
|------|------|------|
| 1 | L1 控制台 | Forge 编排 → `blueprints` AST JSON |
| 2 | L1 | 按组织/设备组选择 `edge_agents`，更新 `current_blueprint_id` 或 `deploy_commands` |
| 3 | L2 边缘 | 心跳/轮询侦测版本 → 拉取 manifest → inventory 热重载 |
| 4 | L3 | 下次 sync 拉取新 Skill/MCP |

**鉴权红线**：所有 `edge_agents` 查询必须带已验证 `organization_id`（防 IDOR）。

---

## 七、语音唤醒流（Voice Wake）

| 步骤 | 主体 | 行为 |
|------|------|------|
| 1 | Tauri / `clients/desktop` | Porcupine「Hey Jachin」唤醒 |
| 2 | STT | Whisper / 云端 STT → 文本 |
| 3 | **L3** | 文本经 Sensory WS → `run_agent` |
| 4 | TTS | MOSS ONNX/XTTS/Edge-TTS 播报 |

规范见 `.cursor/rules/055-tts-service.mdc`、`docs/VOICE_AND_TTS_GUIDE.md`。

---

## 八、规划中：cron_thinker 生物钟

每 30 分钟本地环顾（日志、邮件、指标）→ 异常则 IM 推送。**当前代码未完整落地**；与后台任务调度、`autonomy/` 模块演进方向一致。
