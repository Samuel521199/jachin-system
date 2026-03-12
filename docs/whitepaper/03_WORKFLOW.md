# 03 — 业务流程 (The Workflow)

**文档类型**: 白皮书 · 业务流程  
**版本**: v8.0 (The Singularity OS)

---

## 一、 新设备觉醒流 (V2 L3-L2 零信任配对)

这是 C 端用户和 B 端员工接入 Jachin Nexus 星图的方式。**极客**亦可使用 `jachin-cli pair` 完成 L1 配对（Layer 2 daemon）。

### V2 L3 桌面端（主流程）

| 步骤 | 动作主体 | 行为描述 |
|------|----------|----------|
| 1 | **Layer 3 (Tauri)** | 用户双击运行桌面端，显示 GatewayConnectScreen，输入 L2 网关地址，点击「发起神经接驳」。 |
| 2 | **L3 (l3_node)** | 生成 RSA 密钥对，`POST /api/v2/auth/sync` 向 L2 注册，轮询 `GET /api/v2/auth/poll` 等待审批。 |
| 3 | **L2 管理员** | 在 L2 后台 `POST /api/v2/admin/nodes/assign` 将节点分配给子账号。 |
| 4 | **L3 (l3_node)** | 收到加密 Key，私钥解密，引擎点火，启动 ws://127.0.0.1:18981。 |
| 5 | **Layer 3 (React)** | 检测 L3 就绪，UI 丝滑过渡为主大盘。 |

### Legacy：Layer 2 daemon（L1 6 位码）

Layer 2 daemon、jachin-cli、run-pair 仍使用 L1 配对：`pairing/request` → 6 位码 → `pairing/confirm` → `pairing/status` → 写入 `nexus_config.json`。

---

## 二、 跨网通讯与执行流 (IM Gateway + ReAct)

打破内外网物理隔离，实现随时随地掏出手机即接触底层算力。

| 步骤 | 动作主体 | 行为描述 |
|------|----------|----------|
| 1 | **用户 (Telegram)** | 在街上向专属机器人发送：“查一下北京天气并写入本地日志”。 |
| 2 | **Layer 1 (Next.js)**| Webhook 捕获消息，查库匹配 `agent_id`，存入 `agent_messages` 队列。 |
| 3 | **Layer 2 (Daemon)** | 拉取 `pending_task`（过渡期：10 秒/次 HTTP 心跳；P0：WS 长连推送）。 |
| 4 | **Layer 2 (Agent)** | **进入 Nexus Hook Pipeline**：<br>1. `[Thought]` 需要调用天气 API。<br>2. `[Action]` 选择 MCP 或 Wasm 插件（双轨制）；Swarm Hook 可拦截 heavy_tools 外包至虫群。<br>3. `[Observation]` 获取结果；若报错则自我修复重试。<br>4. `[Action]` 再次调用 MCP 文件工具或 Wasm 写入。 |
| 5 | **Layer 2 (Daemon)** | 得到 `[Final Answer]`，向 Layer 1 发起 `/api/v1/agents/callback`。 |
| 6 | **Layer 1 (Next.js)**| 收到结果，调用 Telegram API 推送至用户手机。手机震动，闭环完成。 |

---

## 三、 每日进化流 (生物学梦境压缩)

确立边缘智能体数字生命体征的核心流程。

| 步骤 | 动作主体 | 行为描述 |
|------|----------|----------|
| 1 | **Layer 2 (SQLite)** | 白天：高频、无损地将对话、报错日志写入 `short_term_logs`。 |
| 2 | **Layer 2 (Dreamer)**| 凌晨 (如 3:00 AM)：触发梦境机制。提取海马体中的昨日数据喂给本地 LLM。 |
| 3 | **Layer 2 (LLM)** | **反思与提纯**：剥离无效闲聊，提取出“主人偏好”、“异常环境规律”等关键信息。 |
| 4 | **Layer 2 (SQLite)** | 遗忘（清空）短期日志，将提纯的高价值 Tag 存入 `core_memory`。 |
| 5 | **Layer 2 (Agent)** | 次日清晨：系统唤醒，最新的核心记忆已无缝挂载于 System Prompt 中。 |

---

## 四、 舰队指令下发流 (企业级批量热更新)

面向 B 端客户的极速降维打击。

| 步骤 | 动作主体 | 行为描述 |
|------|----------|----------|
| 1 | **Layer 1 (UI)** | 管理员在 The Forge 图形化连线，将新策略编译为 AST JSON。 |
| 2 | **Layer 1 (UI)** | 在舰队指挥大屏中，勾选全球 500 个门店的边缘节点，点击“批量下发蓝图”。 |
| 3 | **Layer 1 (DB)** | 数据库中这 500 个节点的 `current_blueprint_id` 瞬间更新。 |
| 4 | **Layer 2 (Daemon)** | 全球节点的下一次心跳 (10秒内) 侦测到版本变更。 |
| 5 | **Layer 2** | 边缘守护进程拉取新 AST，下载所需 MCP 配置/SKILL.md/Wasm 插件，进行热重载，算力阵型瞬间切换。 |

---

## 五、 生物钟主动环顾流 (cron_thinker)

脱离云端，每 30 分钟主动环顾。

| 步骤 | 动作主体 | 行为描述 |
|------|----------|----------|
| 1 | **Layer 2 (cron_thinker)** | 定时触发，读取 HEARTBEAT.md 或配置的检查清单。 |
| 2 | **Layer 2 (cron_thinker)** | 扫描系统日志、未读邮件、异常指标。 |
| 3 | **Layer 2 (Agent)** | 若发现需关注事项，通过 IM 主动推送报警给用户。 |
| 4 | **Layer 2 (cron_thinker)** | 无异常则静默，等待下一轮。 |

---

## 六、 语音唤醒流 (Voice Wake — Hey Jachin)

复刻钢铁侠 Jarvis 体验。

| 步骤 | 动作主体 | 行为描述 |
|------|----------|----------|
| 1 | **Layer 3 (Porcupine/Snowboy)** | 监听“Hey Jachin”唤醒词。 |
| 2 | **Layer 3** | 唤醒后开始录音，VAD 检测结束。 |
| 3 | **Layer 3** | Whisper STT 转文本，发送至 Layer 2 Agent。 |
| 4 | **Layer 2 (Agent)** | ReAct 循环执行，得到 Final Answer。 |
| 5 | **Layer 3** | 调用 TTS (Kokoro/XTTS) 播报结果。 |