# Lark 机器人 AI 对话配置

## 功能说明

- **普通问题**：用户发问 → 百炼生成回复（Key/主模型与 L3 统一，见 `core.plugin_llm_identity`）→ 机器人发送
- **任务请求**：用户说「同步多维表」「抓取简历」等 → 只记录到 `data/lark_tasks.json`，不执行

核心逻辑在 `com.jachin.hr.recruitment/tools/atom_lark_chat.py`，可被 MCP、Webhook、交互脚本共同调用。

## 使用方式

### 方式一：本地交互模式（快速测试）

无需配置 Webhook，在终端模拟对话，回复会同步到 Lark：

```bash
cd skills_repo/plugin
python com.jachin.hr.recruitment/lark_bot.py --interactive
```

- 输入普通问题 → AI 回复并发送到 Lark
- 输入任务关键词（同步、抓取、发布等）→ 只记录，回复「已记录」

### 方式二：Webhook 模式（真实 Lark 对话）

1. **启动 Webhook 服务**（先启动，再配置）：
   ```bash
   cd skills_repo/plugin
   python com.jachin.hr.recruitment/lark_bot.py --webhook --port 5000
   ```

2. **暴露公网**（新开终端）：
   ```bash
   ngrok http 5000
   ```
   **重要**：把终端里显示的 `Forwarding` 地址（如 `https://abcd1234.ngrok-free.app`）复制下来，不要用占位符 `xxx`。

3. **在 Lark 开发者后台配置**（顺序不能反）：
   - 进入应用 →「开发配置」→「事件与回调」
   - **订阅方式**：选择「将回调发送至开发者服务器」
   - **请求地址**：填 `https://你的真实ngrok地址/lark-webhook`（例如 `https://abcd1234.ngrok-free.app/lark-webhook`）
   - 切到「**事件配置**」tab → 点击「添加事件」→ 勾选 `im.message.receive_v1`（接收消息）
   - 切到「**加密策略**」→ 建议先关闭「数据加密」（否则需实现解密逻辑）
   - **权限**：若要支持私聊回复，需在「权限管理」中开通 **「获取用户发给机器人的单聊消息」**
   - 点击「保存」

4. **发版**：若提示需发布，则创建版本并发布

5. **测试**：在 Lark 里 @机器人 或 发消息给机器人，若配置正确，脚本终端会打印「收到: chat_id=... text=...」

## 任务关键词

以下关键词命中时，只记录不执行：

同步、多维表、抓取、收网、简历、发布、发职位、打招呼、推荐牛人、求简历、执行、运行

## 记录的任务

任务保存在 `data/lark_tasks.json`，格式：

```json
[
  {
    "user_id": "xxx",
    "chat_id": "oc_xxx",
    "text": "帮我同步多维表",
    "recorded_at": "2025-03-10T12:00:00"
  }
]
```

可由定时任务或人工处理后执行对应操作。

---

## 常见问题

### 1. 保存请求地址时报「返回数据不是合法的JSON格式」

- **原因**：Lark 验证 URL 时，服务端返回了非 JSON（如 HTML 或纯文本）
- **处理**：
  1. 确认请求地址是**真实 ngrok 地址**，不是 `https://xxx.ngrok-free.app` 占位符
  2. 启动脚本后再在 Lark 保存：`python com.jachin.hr.recruitment/lark_bot.py --webhook --port 5000`
  3. ngrok 和 Flask 都正常时再点击「保存」
  4. 若仍失败，可尝试 [localtunnel](https://localtunnel.github.io/www/) 或 [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps) 代替 ngrok

### 2. 在 Lark 里 @机器人 没反应，脚本收不到

- **原因**：未正确订阅「接收消息」事件
- **处理**：
  1. 进入「事件与回调」→「事件配置」→ 添加 `im.message.receive_v1`
  2. 确认应用已发版，权限包含 `im:message:receive_v1`
  3. 机器人需在目标群/单聊中

### 3. 群聊 @ 会回复，但私聊（机器人自己的对话框）不回复

- **原因**：群聊和私聊需要**不同的权限**，仅开通群聊权限无法接收单聊消息
- **处理**：
  1. 进入飞书开放平台 → 应用 → **权限管理**
  2. 在「消息与群组」权限组中，找到并开通 **「获取用户发给机器人的单聊消息」**（或「读取用户发给机器人的单聊消息」）
  3. 保存后创建新版本并**发布**，待管理员审批生效
  4. 发版生效后，私聊消息事件会正常推送，机器人即可在私聊中回复
- **排查**：若仍不回复，可设置环境变量 `LARK_DEBUG_EVENT=1` 后重启 Webhook，查看日志是否有「未从事件中解析到 chat_id」的提示
