# IM 网关规格 — 进化战役 2：无处不在的躯体

**版本**: v8.0 (The Singularity OS)
**状态**: 已实现 (Telegram/飞书)；v8.0 全渠道 Universal Message Adapter
**定位**: Universal Message Adapter — 全渠道统一适配，让用户随时随地发消息即可让内网边缘机器干活

---

## 一、架构概览

```
用户手机 (Telegram/飞书)
    │
    │ Webhook POST
    ▼
Layer 1 (Nexus)  Webhook 路由
    │
    │ 插入 agent_message_queue (inbound, pending)
    ▼
边缘 Agent 心跳拉取
    │
    │ task + pending_message_ids
    ▼
Layer 2 (daemon)  Agent Loop 执行
    │
    │ POST /api/v1/agents/result
    ▼
Layer 1 调用 Telegram API
    │
    ▼
用户手机收到执行结果
```

**NAT 穿透**：边缘 Agent 在内网，通过心跳主动拉取消息，无需公网 IP 或端口映射。

---

## 二、数据库

### 2.1 edge_agents 扩展

| 列 | 类型 | 说明 |
|----|------|------|
| im_binding_id | TEXT | IM 绑定 ID，如 Telegram chat_id |
| im_platform | TEXT | `telegram` \| `lark` |

### 2.2 agent_message_queue

| 列 | 类型 | 说明 |
|----|------|------|
| id | UUID | 主键 |
| agent_id | UUID | 关联 edge_agents |
| message_text | TEXT | 消息内容 |
| direction | TEXT | `inbound`（用户→Agent）\| `outbound`（Agent→用户） |
| status | TEXT | `pending` \| `processed` \| `failed` |
| source_meta | JSONB | 来源元数据（如 telegram_chat_id） |
| created_at | TIMESTAMPTZ | 创建时间 |
| processed_at | TIMESTAMPTZ | 处理完成时间 |

---

## 三、API

### 3.1 Webhook：Telegram

**POST** `/api/v1/webhooks/telegram`

- 接收 Telegram 机器人发来的 POST
- 解析 `message.chat.id`、`message.text`
- 根据 `im_binding_id` 查 agent，插入队列

**配置**：BotFather 设置 `setWebhook` 指向 `https://your-domain/api/v1/webhooks/telegram`

### 3.2 绑定 IM

**POST** `/api/v1/agents/bind-im`

- Headers: Cookie（需已登录）
- Body: `{ agent_id, im_binding_id, im_platform?: 'telegram'|'lark' }`
- 将 Agent 与用户 Telegram chat_id 绑定

**获取 chat_id**：与 [@userinfobot](https://t.me/userinfobot) 对话可得。

### 3.3 心跳扩展

**POST** `/api/v1/agents/heartbeat`

响应新增字段：

- `task`: 待下发的 inbound 消息文本（多条用 `\n` 拼接）
- `pending_message_ids`: 对应队列 ID 列表，供 result API 标记已处理

### 3.4 结果回传

**POST** `/api/v1/agents/result`

- Headers: `Authorization: Bearer <access_token>`
- Body: `{ result: string, message_ids?: string[] }`
- 标记消息为 processed，调用 Telegram API 将 result 发回用户手机

---

## 四、环境变量

| 变量 | 说明 |
|------|------|
| TELEGRAM_BOT_TOKEN | 从 @BotFather 获取，用于 result API 推送 |

---

## 五、飞书接入说明

1. 创建飞书应用，启用「接收消息」
2. 配置请求地址：`POST /api/v1/webhooks/lark`
3. 解析 `event.message.chat_id`、`event.message.content`（JSON）
4. 绑定：`im_platform='lark'`，`im_binding_id` 为飞书 chat_id
5. 回传：调用飞书 `im/v1/messages` 发送接口

---

## 七、相关文档

- [TELEGRAM_TUNNEL_SETUP.md](./TELEGRAM_TUNNEL_SETUP.md) - **战役 2 物理基建**（BotFather、Ngrok、setWebhook、绑定 Chat ID）
- [LAYER2_AGENT_LOOP_DESIGN.md](./LAYER2_AGENT_LOOP_DESIGN.md) - Agent Loop 与任务执行
- [JMP_SPEC.md](./JMP_SPEC.md) - JMP 协议
- [NEXUS_DAEMON.md](./NEXUS_DAEMON.md) - 守护进程总览（轻量版 daemon 心跳 + result 回传）
- [P0_TRUST_AND_HEARTBEAT_SPEC.md](./P0_TRUST_AND_HEARTBEAT_SPEC.md) - 心跳协议与 IM 扩展
- [LAYER1_ARCHITECTURE_AND_DESIGN.md](./LAYER1_ARCHITECTURE_AND_DESIGN.md) - Layer 1 数据模型
