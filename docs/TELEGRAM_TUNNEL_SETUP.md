# 战役 2 物理基建：架设跨网隧道

**目标**：打通 Telegram 到你本地电脑的内网隧道，让手机发来的每一句话都能唤醒本地 Agent Loop。

Layer 1 跑在 `localhost:3000` 时，Telegram 官方服务器无法直接访问。需要完成以下 4 步，这座跨网大桥才能真正合拢。

---

## 1. 召唤信使 (The BotFather)

1. 打开 Telegram，搜索 **@BotFather**
2. 发送 `/newbot`，按提示给机器人起名（如 `JachinNexus_Bot`）
3. 拿到 **HTTP API Token**（形如 `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`）

**配置环境变量**：在 `cloud/nexus/` 下创建或编辑 `.env.local`：

```bash
# cloud/nexus/.env.local
TELEGRAM_BOT_TOKEN="你的_BOT_TOKEN"
```

> 若使用 `.env.example`，复制为 `.env.local` 后填入 `TELEGRAM_BOT_TOKEN`。

---

## 2. 撕开内网裂缝 (Ngrok 隧道)

Webhook 需要公网 HTTPS 地址。**新开一个终端**，运行：

```bash
npx ngrok http 3000
```

复制 ngrok 生成的公网地址，例如：

```
https://abc-123-xyz.ngrok-free.app
```

> 确保 Layer 1 已在 `localhost:3000` 运行（`start.bat` → 选项 1）。

---

## 3. 注册神经节点 (Set Webhook)

将 Token 和 Ngrok 地址拼成以下 URL，在浏览器中访问：

```
https://api.telegram.org/bot<你的_BOT_TOKEN>/setWebhook?url=<你的_NGROK_地址>/api/v1/webhooks/telegram
```

**示例**：

```
https://api.telegram.org/bot123456789:ABCdefGHIjklMNOpqrsTUVwxyz/setWebhook?url=https://abc-123-xyz.ngrok-free.app/api/v1/webhooks/telegram
```

**成功响应**：

```json
{"ok":true,"result":true,"description":"Webhook was set"}
```

此时 Telegram 已成功挂载到你的本地控制台。

---

## 4. 绑定你的专属指挥官 ID

### 4.1 获取 Chat ID

1. 在 Telegram 搜索 **@userinfobot**（或任意能显示 ID 的机器人）
2. 发送任意消息，获取你的 **Chat ID**（一串数字，如 `987654321`）

### 4.2 写入 edge_agents

在数据库 **edge_agents** 表中（Nexus Console 或 Drizzle Studio）：

1. 找到一台 `status = active` 的设备（即已配对的 Layer 2）
2. 编辑该行，填入：
   - **im_binding_id**：你的 Chat ID（如 `987654321`）
   - **im_platform**：`telegram`

### 4.3 或使用 API 绑定（需登录）

若已登录 Nexus Console，可调用：

```bash
curl -X POST "http://localhost:3000/api/v1/agents/bind-im" \
  -H "Content-Type: application/json" \
  -H "Cookie: <你的登录 Cookie>" \
  -d '{"agent_id":"<edge_agents.id>","im_binding_id":"987654321","im_platform":"telegram"}'
```

---

## 验证流程

1. **Layer 1**：`localhost:3000` 运行中
2. **Ngrok**：`npx ngrok http 3000` 运行中，公网地址已 setWebhook
3. **Layer 2**：`python -m core.cli daemon` 或 `.\scripts\start-layer2.ps1` 选 Light
4. **Telegram**：给你的机器人发一条消息，如「查一下天气」

预期：Layer 2 终端出现 ReAct 循环，执行完成后结果会通过 Telegram 推回你的手机。

---

## 常见问题

| 问题 | 处理 |
|------|------|
| Webhook 返回 404 | 确认 Ngrok 地址末尾为 `/api/v1/webhooks/telegram`，且 Layer 1 在 3000 端口 |
| 发消息无反应 | 检查 edge_agents 中 `im_binding_id`、`im_platform` 是否正确，设备 `status=active` |
| 结果未推回手机 | 检查 `TELEGRAM_BOT_TOKEN` 是否已配置，重启 Layer 1 |
| Ngrok 免费版重启后地址变化 | 需重新执行 setWebhook，或使用 ngrok 固定域名（付费） |

---

**相关文档**：[IM_GATEWAY_SPEC.md](./IM_GATEWAY_SPEC.md)
