# BI 机器人连接 L3 终端

## 概述

BI 机器人在飞书会话 `oc_b4078d98862b1defeae9937895ed4fad` 中与用户对话，需连接到 L3 终端方能由 Agent 执行分析与回复。

## 配置

### 1. atom_lark_notifier 配置

已写入 `config/mcps/atom_lark_notifier/config.yaml`：

- `robot_name`: "BI助手"
- `app_id` / `app_secret`: BI 助手机器人应用凭证（cli_a930e0054ecf5ed1）
- `default_chat_id`: 机器人所在会话（oc_b4078d98862b1defeae9937895ed4fad）
- `lark_use_feishu`: true（飞书中国版）

### 2. bi_daily_report 配置

`config/skills/com.jachin.bi.daily_report/bi_daily_report.yaml` 中 `distribution.lark_chat_id` 已设为该 chat_id。

### 3. 环境变量（可选）

若需在 `.env` 中覆盖：

```
LARK_APP_ID=cli_a930e0054ecf5ed1   # BI助手 app_id
LARK_APP_SECRET=xxx                 # 从飞书开放平台获取，勿提交到仓库
BI_LARK_CHAT_ID=oc_b4078d98862b1defeae9937895ed4fad
LARK_USE_FEISHU=1
L3_WS_URL=ws://127.0.0.1:18981/sensory
```

## 运行顺序

1. **启动 L3 节点**（WebSocket 端口 18981，可选，不配置 L3_WS_URL 则走本地百炼）：
   ```bash
   python -m l3_node --ws-only
   ```

2. **启动 BI Lark 长连接**（接收飞书消息并转发到 L3）：
   ```bash
   # Lark 国际版应用（若报 Incorrect domain name 用此）
   python scripts/run_bi_lark_long_connection.py --domain lark

   # 飞书中国版应用
   python scripts/run_bi_lark_long_connection.py --domain feishu
   ```

3. **Lark 开放平台配置**（脚本连接成功后**必须**完成）：
   - 应用 → 事件与回调 → 订阅方式 → 选择「**使用长连接接收回调**」→ **保存**（长连接在线时才能保存成功）
   - 事件配置 → 添加「**接收消息** `im.message.receive_v1`」→ 保存并发布

## 数据流

- **Lark → L3**：用户在飞书发消息 → 长连接接收 → `process_lark_message` → L3 WebSocket → Agent 执行 → 回复
- **L3 → Lark**：`_send_reply` 通过 IM API 将回复发回飞书

## 终端-Lark 镜像（可选）

若希望桌面终端与飞书群聊双向同步：

1. 桌面端 `.env` 设置 `VITE_LARK_CHAT_ID=oc_b4078d98862b1defeae9937895ed4fad`
2. L3 将 `LARK_MIRROR_PUSH_URL` 指向 Webhook 的 `/api/mirror-push`（与 lark_bot 同端口）

详见 `skills_repo/plugin/docs/LARK_TO_JACHIN_L3_INTEGRATION.md` 第七节。

## 故障排查

| 现象 | 排查 |
|------|------|
| 长连接成功但收不到消息 / 不回复 | 1) Lark 后台是否已切换为「使用长连接接收回调」并保存；2) 是否已添加事件「接收消息 im.message.receive_v1」；3) 机器人是否已加入该群/会话 |
| L3_WS_URL 未配置 | 在 config/mcps/atom_lark_notifier/config.yaml 中设置 l3_ws_url；或 .env 中设置 L3_WS_URL |
| 远程计算机拒绝网络连接 / L3 连接失败 | 1) L3 需在本机运行；2) 用 `python scripts/detect_l3_ws_url.py` 检测本机 L3 实际端口；3) 将正确的 l3_ws_url 写入 config |
| 发消息失败 / 回复发不出去 | 使用 --domain lark 时发消息 API 也走国际版，脚本已自动设置 LARK_USE_FEISHU=0 |

## 如何确认本机 L3 的 WebSocket 地址

1. **先启动 L3**（任选其一）：
   - `python -m l3_node --ws-only`
   - 或打开 Jachin 桌面端（会拉起 L3）

2. **运行检测脚本**：
   ```bash
   python scripts/detect_l3_ws_url.py
   ```
   脚本会尝试 18981～18985 端口，并输出可用地址。

3. **将地址写入配置**：编辑 `config/mcps/atom_lark_notifier/config.yaml`，将 `l3_ws_url` 设为脚本输出的地址。
