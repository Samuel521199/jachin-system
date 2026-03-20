# IM 通道层 — Lark/Telegram 等同维度

L3 独立使用 Lark、Telegram 等 IM 通道，无需 Layer 1。Lark 优先使用长连接，无需公网 IP/ngrok。

## 招聘测试（必须使用长连接）

**HR 招聘功能测试必须通过 Lark 长连接**，在飞书应用内直接与机器人对话。

### 配置步骤

1. **配置** `~/.jachin/config/im_channels.yaml`：`enabled: true`，填写 `app_id`、`app_secret`
   - 飞书中国版：`domain: "https://open.feishu.cn"`
   - Lark 国际版：`domain: "https://open.larksuite.com"`（默认）

2. **飞书/Lark 开发者后台**（关键，否则收不到消息）：
   - 进入应用 → **事件与回调** → **回调配置**
   - 添加事件：**接收消息**（`im.message.receive_v1`）
   - 订阅方式：选择 **「使用长连接接收回调」**，点击保存
   - ⚠️ **必须在 L3 长连接已连接成功时才能保存**（控制台出现 `connected to wss://...` 后再去后台保存）

3. 启动 L3，在 Lark 中发「我要招聘」开始多轮问答

4. 会话按 chat_id 持久化到 `~/.jachin/l3_lark_sessions.json`

WebSocket/终端仅用于镜像展示，**不可替代长连接**进行招聘流程测试。

### 常见问题

| 现象 | 原因 | 处理 |
|------|------|------|
| 连接超时 `timed out during opening handshake` | 网络/防火墙/代理 | 重试或检查网络；飞书中国版用 `domain: "https://open.feishu.cn"` |
| 连接成功但发消息无回复 | 后台未切换「使用长连接接收回调」 | L3 在线时，去后台选择该方式并保存 |
| 消息无回复 | 未订阅「接收消息」事件 | 在回调配置中添加 `im.message.receive_v1` |
| chat_ids 配置了但收不到 | 发消息的会话 chat_id 不在列表 | 检查 chat_id 是否匹配，或清空 chat_ids 处理全部 |
| `ping failed, no close frame` 长连接断开 | 旧版：Agent 阻塞 WebSocket 线程 | 已修复：Agent 在独立线程池执行，不再阻塞 |

## 配置

路径：`~/.jachin/config/im_channels.yaml`（支持 `JACHIN_HOME` 环境变量覆盖）

首次启动 L3 时，若文件不存在会自动创建示例。打包后可随意修改，无需重新打包。

## Lark 长连接

- **mode**: `long_connection`（默认）
- **app_id / app_secret**: 飞书应用凭证，可从环境变量 `LARK_APP_ID`、`LARK_APP_SECRET` 覆盖
- **chat_ids**: 多机共享时，本节点只处理这些 chat_id；空则处理全部
- **domain**: `https://open.feishu.cn`（飞书中国版）或 `https://open.larksuite.com`（国际版）

飞书后台需选择「使用长连接接收回调」。

## 多机共享

多台 L3 共享同一飞书应用时：

1. 各节点配置 **chat_ids**，指定本节点负责的会话
2. chat_ids 应互斥（各节点不重叠），否则消息可能被忽略
3. 飞书将消息随机推送到某一连接；若推送到「错误」节点，该节点会因 chat_id 不在列表而忽略

## 扩展 Telegram

在 `im_channels/` 下新增 `telegram_channel.py`，实现 `InboundIMChannel`，并在 `__init__.py` 的 `_REGISTRY` 中注册即可。
