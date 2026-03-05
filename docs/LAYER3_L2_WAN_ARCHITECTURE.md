# Layer 3 与 Layer 2 广域网通信架构

**版本**: v8.0 (The Singularity OS)  
**定位**: 广域网中 Layer 3（用户端）与 Layer 2（边缘大脑）的匹配方式、通信架构与数据流

---

## 一、核心结论：Layer 3 不直连 Layer 2

在广域网 (WAN) 中，**Layer 3 与 Layer 2 不建立直接连接**。二者均通过 **Layer 1 云端枢纽** 中转，实现 NAT 穿透与身份匹配。

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           广域网 (WAN) 通信拓扑                                    │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│   用户手机 (Telegram)              公网                   用户家中内网              │
│   ┌──────────────┐              ┌──────────────┐       ┌──────────────────┐ │
│   │  Layer 3     │   Webhook    │   Layer 1    │ 心跳  │   Layer 2        │ │
│   │  (Telegram   │ ──────────►  │   (Nexus     │ ◄──── │   (daemon.py)    │ │
│   │   App)       │   POST       │   Cloud)     │       │   边缘大脑        │ │
│   │              │              │              │ ────► │   (内网/NAT后)   │ │
│   │  发消息 →    │              │  匹配+队列    │ 回调  │   执行+回传      │ │
│   └──────────────┘              └──────────────┘       └──────────────────┘ │
│         │                                │                      │           │
│         │                                │                      │           │
│         └────────────────────────────────┼──────────────────────┘           │
│                                          │                                   │
│                                    Layer 1 作为唯一                          │
│                                    匹配中枢与中转                             │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 二、匹配方式：三层绑定关系

| 绑定链 | 用途 | 数据来源 |
|--------|------|----------|
| **1. access_token ↔ edge_agents** | Layer 2 心跳鉴权 | 配对后 `pairing/status` 返回，写入 `~/.jachin/nexus_config.json` |
| **2. im_binding_id ↔ edge_agents** | Layer 3 (IM) 消息路由 | 用户通过 `bind-im` API 将 Telegram chat_id 绑定到 agent |
| **3. agent_id ↔ agent_message_queue** | 任务队列归属 | 消息插入时 `agent_id`，心跳拉取时按 `agent_id` 过滤 |

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           匹配关系 (Layer 1 数据库)                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│   edge_agents 表                                                                 │
│   ┌─────────────────────────────────────────────────────────────────────────┐  │
│   │ id (agent_id)  │ auth_token  │ im_binding_id │ im_platform │ status     │  │
│   │─────────────────────────────────────────────────────────────────────────│  │
│   │ uuid-xxx      │ jch-abc123  │ 123456789     │ telegram    │ active     │  │
│   └─────────────────────────────────────────────────────────────────────────┘  │
│          │                    │                    │                            │
│          │                    │                    │                            │
│          ▼                    ▼                    ▼                            │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐                      │
│   │ 心跳鉴权      │    │ 配对下发     │    │ Webhook 路由  │                      │
│   │ Bearer token │    │ 写入 nexus_  │    │ chat_id →    │                      │
│   │ → agent_id   │    │ config.json │    │ agent_id     │                      │
│   └──────────────┘    └──────────────┘    └──────────────┘                      │
│          │                    │                    │                            │
│          └────────────────────┼────────────────────┘                            │
│                               ▼                                                 │
│   agent_message_queue 表                                                         │
│   ┌─────────────────────────────────────────────────────────────────────────┐  │
│   │ agent_id │ message_text │ direction │ status                              │  │
│   │ uuid-xxx │ "查天气"     │ inbound   │ pending → processed                 │  │
│   └─────────────────────────────────────────────────────────────────────────┘  │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 三、完整流程：从用户发消息到结果回传

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    跨网通讯流程 (IM Gateway + 心跳拉取)                             │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ① 用户 (Telegram) 发消息 "查北京天气"                                            │
│         │                                                                       │
│         ▼                                                                       │
│  ② Telegram 服务器 → POST /api/v1/webhooks/telegram                              │
│         │         Body: { message: { chat: { id: 123456789 }, text: "..." } }   │
│         ▼                                                                       │
│  ③ Layer 1: chat_id 123456789 → 查 edge_agents (im_binding_id=123456789)        │
│         │         → agent_id = uuid-xxx                                          │
│         │         → INSERT agent_message_queue (agent_id, "查北京天气", pending) │
│         ▼                                                                       │
│  ④ Layer 2 (daemon) 每 10 秒: POST /api/v1/agents/heartbeat                      │
│         │         Headers: Authorization: Bearer <access_token>                 │
│         │         Body: { instance_id: "dev-001" }                               │
│         ▼                                                                       │
│  ⑤ Layer 1: access_token → 查 edge_agents (auth_token 或 id) → agent_id        │
│         │         → SELECT agent_message_queue WHERE agent_id AND status=pending│
│         │         → 返回 { task: "查北京天气", pending_message_ids: [...] }      │
│         ▼                                                                       │
│  ⑥ Layer 2: 注入 event_bus → Agent Loop 执行 → 得到 Final Answer                │
│         │                                                                       │
│         ▼                                                                       │
│  ⑦ Layer 2: POST /api/v1/agents/result                                          │
│         │         Body: { result: "北京晴 25°C", message_ids: [...] }           │
│         ▼                                                                       │
│  ⑧ Layer 1: UPDATE agent_message_queue SET status=processed                      │
│         │         → 调用 Telegram Bot API 将 result 推送到 chat_id 123456789     │
│         ▼                                                                       │
│  ⑨ 用户手机收到 "北京晴 25°C"                                                    │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 四、Layer 3 的两种形态

| 形态 | 网络位置 | 与 Layer 2 的通信方式 |
|------|----------|------------------------|
| **IM 客户端 (Telegram/飞书)** | 广域网任意位置 | 经 Layer 1 中转：Webhook → 队列 → 心跳拉取 → 回调 |
| **Tauri 桌面端 (同机)** | 与 Layer 2 同机 | **直连** `ws://localhost:8080/sensory`：流式 chunk、HITL、Swarm 雷达 |

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    Layer 3 双形态通信路径                                          │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  【形态 A】Telegram 手机 (广域网)                                                  │
│                                                                                 │
│     用户手机 ──► Telegram ──► Layer 1 Webhook ──► agent_message_queue            │
│                                                          │                      │
│     用户手机 ◄── Telegram ◄── Layer 1 回调 ◄──────────────┘                      │
│                          ▲                                                      │
│                          │ 心跳拉取 + result 回传                                │
│                          │                                                      │
│                    Layer 2 (内网)                                                │
│                                                                                 │
│  ─────────────────────────────────────────────────────────────────────────────  │
│                                                                                 │
│  【形态 B】Tauri 桌面 (与 Layer 2 同机)                                           │
│                                                                                 │
│     Tauri Chat 窗口 ──► ws://localhost:8080/sensory ◄── Layer 2 daemon            │
│                              │                                                    │
│                              │ layer3_broadcast: thought/action/chunk/HITL       │
│                              │ 能力协商 (manifest: ui_render, stream_chunk, ...)  │
│                              ▼                                                    │
│                        全息感官投射 (流式打字机、Handoff、Swarm 雷达)               │
│                                                                                 │
│     ※ 配对流程：Tauri 扫码 → Layer 1 pairing/confirm → access_token             │
│        → 写入 ~/.jachin/nexus_config.json → Tauri 静默拉起 Layer 2 daemon         │
│        → Layer 2 读取同一 config，用 access_token 连 Layer 1                     │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 五、Jachin Mesh：Layer 2 连 Layer 1 的双通道

Layer 2 连接 Layer 1 有两种方式（优先 WebSocket，失败则 HTTP 心跳）：

| 通道 | 端点 | 说明 |
|------|------|------|
| **WebSocket (量子隧道)** | `wss://<layer1>/api/v1/agents/stream` | 长连，毫秒级下发 task/blueprint；断线指数退避重连 |
| **HTTP 心跳兜底** | `POST /api/v1/agents/heartbeat` | 每 5 秒轮询，无 WebSocket 时启用 |

两种通道均携带 `Authorization: Bearer <access_token>`，Layer 1 据此解析 `agent_id` 并返回该 agent 的待办任务。

---

## 六、配对流程：Layer 3 与 Layer 2 的「结婚证」

Tauri 桌面端与 Layer 2 的匹配，通过**配对**建立同一 `edge_agent` 记录：

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                    配对流程 (扫码即连)                                              │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ① Tauri 启动，未检测到 ~/.jachin/nexus_config.json                              │
│         │                                                                       │
│         ▼                                                                       │
│  ② Tauri (Rust) → POST /api/v1/pairing/request                                   │
│         │         Layer 1 返回 { session_id, short_code, pair_url }             │
│         │         生成二维码 (含 pair_url + code)                                  │
│         ▼                                                                       │
│  ③ 用户手机扫码 → 打开 Layer 1 /pair 页面 → 输入 6 位码 → 点击「授权」             │
│         │                                                                       │
│         ▼                                                                       │
│  ④ Layer 1: POST /api/v1/pairing/confirm { code }                               │
│         │         创建/更新 edge_agents，status=active，生成 auth_token          │
│         ▼                                                                       │
│  ⑤ Tauri 轮询 GET /api/v1/pairing/status?session_id=...                          │
│         │         收到 { status: "success", access_token, instance_id }         │
│         ▼                                                                       │
│  ⑥ Tauri 写入 ~/.jachin/nexus_config.json                                        │
│         │         { access_token, instance_id, nexus_base_url }                 │
│         ▼                                                                       │
│  ⑦ Tauri 静默拉起 core/daemon.py (OS 级无黑框启动)                               │
│         │                                                                       │
│         ▼                                                                       │
│  ⑧ Layer 2 daemon 读取 nexus_config.json → 用 access_token 连 Layer 1           │
│         │         → 心跳/WebSocket 建立，与 edge_agents 记录绑定                  │
│         ▼                                                                       │
│  ⑨ Tauri 与 Layer 2 共享同一 edge_agent，匹配完成                                │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 七、 未来升维：控制面与数据面分离

当前架构为**中心化中转**。规划中的升维方案将实现**控制面/数据面分离**：

- **局域网**：mDNS 零配置发现，内网 IP 直连，Layer 1 零压力
- **广域网原生**：WebRTC P2P 打洞，Layer 1 仅作信令交换
- **第三方 IM**：HTTP 轮询 → WebSocket 长连推送，事件驱动

详见 [10_CONTROL_DATA_PLANE.md](./whitepaper/10_CONTROL_DATA_PLANE.md)。

---

## 八、 相关文档

- [IM_GATEWAY_SPEC.md](./IM_GATEWAY_SPEC.md) — IM 网关与消息队列
- [PAIRING_PROTOCOL_SPEC.md](./PAIRING_PROTOCOL_SPEC.md) — 配对协议
- [P0_TRUST_AND_HEARTBEAT_SPEC.md](./P0_TRUST_AND_HEARTBEAT_SPEC.md) — 心跳与鉴权
- [03_WORKFLOW.md](./whitepaper/03_WORKFLOW.md) — 业务流程白皮书
- [10_CONTROL_DATA_PLANE.md](./whitepaper/10_CONTROL_DATA_PLANE.md) — 控制面/数据面分离（未来升维）
