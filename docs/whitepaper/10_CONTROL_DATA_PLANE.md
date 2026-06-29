# 10 — 控制面与数据面分离：能直连绝不绕路

**文档类型**: 白皮书 · 架构升维路线图  
**版本**: V2.3  
**更新日期**: 2026-06  
**状态**: **战略蓝图**（部分 IM 拉取仍为过渡态）

---

## 一、问题

若所有流式 Token、图像、语音经 L1 中转，L1 将成为高成本路由器。

**解法**：控制面（鉴权、计费、信令）与数据面（感官/流式 payload）分离；**能直连绝不绕路**。

---

## 二、现行 vs 目标

| 场景 | **现行（2026-06）** | **目标** |
|------|---------------------|----------|
| 桌面 ↔ L3 | ✅ `localhost:18981` WS | 同左 |
| L3 ↔ LLM | ✅ 直连 API | 同左 |
| L2 ↔ L3 | ✅ HTTP + MCP 委托 | 同左 |
| L1 ↔ L2 manifest | ✅ HTTP sync | + WS 长连推送 |
| 第三方 IM | ⚠️ Webhook → PG 队列 → 拉取 | WS 长连推送 |
| 手机 ↔ 家中 L2 | ⚠️ 经 L1 | mDNS / WebRTC P2P |
| 流式 chunk 到手机 | ⚠️ 多跳 | P2P 或局域网直连 |

---

## 三、局域网（LAN）：mDNS ⏳

| 组件 | 目标行为 |
|------|----------|
| L2 | 广播 `_jachin-nexus._tcp.local` |
| L3 移动客户端 | 优先内网 IP 直连 WS |
| 效果 | 断 WAN 时本地调度仍可运行 |

**注意**：桌面 Tauri 已固定连 `127.0.0.1:18981`（本机 L3），与 mDNS 场景（远程手机控家中节点）不同。

---

## 四、广域网（WAN）：WebRTC P2P ⏳

1. L1 仅交换 SDP/ICE（信令）  
2. L3 ↔ L2 打洞建立 Data Channel  
3. 流式数据不走 L1  
4. 失败时 TURN 中继 fallback  

推荐 P0 选型：WebRTC Data Channel + 复用 L1 WS 信令。

---

## 五、第三方 IM：Edge Pull → WS Push

| 现状 | 目标 |
|------|------|
| L2/L3 轮询 `agent_message_queue` | L1 Webhook 后立即 WS 推送到在线 L2/L3 |
| 延迟 ≈ 轮询间隔 | 延迟 ≈ 单次 RTT |

L3 原生 Lark 等通道可走 **直连 L3** 路径，减少 L1 中转（见 `l3_node/channels/lark/`）。

---

## 六、智能路由矩阵（目标态）

| 场景 | 路径 | L1 角色 |
|------|------|---------|
| 同机 | Tauri → 127.0.0.1:18981 | 鉴权一次 |
| 同 LAN | mDNS → 内网 WS | 鉴权一次 |
| WAN 原生 App | WebRTC P2P | 信令 |
| WAN 打洞失败 | TURN | 中继 |
| Telegram 等 | Webhook → WS → L3 | 网关 |

---

## 七、实施路线图

| 阶段 | 任务 | 状态 |
|------|------|------|
| P0 | IM：轮询 → WS 推送 | ⏳ |
| P1 | L2 mDNS 广播 | ⏳ |
| P2 | WebRTC 信令 + P2P | ⏳ |
| P3 | TURN Fallback | ⏳ |

---

## 八、相关文档

- [LAYER3_L2_WAN_ARCHITECTURE.md](../LAYER3_L2_WAN_ARCHITECTURE.md)
- [OMNI_SENSORY_BUS.md](./OMNI_SENSORY_BUS.md)
- [07_LAYER3_TERMINAL.md](./07_LAYER3_TERMINAL.md)
