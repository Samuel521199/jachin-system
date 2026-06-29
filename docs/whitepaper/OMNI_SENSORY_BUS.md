# 全息感官总线 (Omni-Sensory Bus)

**文档类型**: 白皮书 · 架构设计  
**版本**: V2.3  
**更新日期**: 2026-06  
**状态**: **L3 WebSocket 为主路径**；`core/event_bus.py` 为 Legacy

---

## 一、设计理念

Voice、桌面 GUI、IM 是同一 **L3 Agent** 的外接感官。输入归一化 → `run_agent` → 输出按来源多路分发。

```
┌──────────────────────────────────────────────────────────────┐
│  L3 Omni-Sensory (ws://127.0.0.1:18981/sensory)               │
├──────────────────────────────────────────────────────────────┤
│  输入: chat · voice · lark · manifest · subscribe_bg_tasks   │
│         ↓                                                     │
│  agent_core.run_agent (ReAct)                                 │
│         ↓                                                     │
│  输出: thought/action/observation/answer/chunk · zombie_*    │
│         ↓                                                     │
│  分发: Tauri Omni · TTS · IM callback                         │
└──────────────────────────────────────────────────────────────┘
```

---

## 二、三大感官（现行）

### 2.1 Voice

| 项 | 说明 |
|----|------|
| 唤醒 | Porcupine「Hey Jachin」（`clients/desktop` STT 模块） |
| STT | Whisper / 云端 |
| 路径 | 文本 → Sensory WS → L3 |
| TTS | MOSS ONNX/XTTS/Edge-TTS |

### 2.2 桌面 Omni（Sprite / Console）

| 项 | 说明 |
|----|------|
| UI | `clients/desktop/src/chat.tsx` |
| WS | `useSensoryWebSocket.ts` — onopen 发送 `manifest`、`subscribe_background_tasks` |
| 事件 | ReAct 步骤、流式 chunk、HITL、`zombie_tasks_pending` 横幅 |

### 2.3 IM（Lark / Telegram）

| 项 | 说明 |
|----|------|
| L1 | Webhook → 队列（Telegram 等） |
| L3 | `channels/lark/` 原生入站/出站（飞书主路径） |
| 执行 | **L3 run_agent**，非 L2 agent_loop |

---

## 三、WebSocket 协议（L3）

**服务**：`l3_node/ws_server.py`，默认 `18981`。

| 客户端消息 | 说明 |
|------------|------|
| `manifest` | 能力协商（caps: `stream_chunk`, `hitl_popup` 等） |
| `subscribe_background_tasks` | 订阅后台/zombie 事件 |
| 用户文本 / 多模态附件 | 触发 `run_agent` |

| 服务端事件 | 说明 |
|------------|------|
| ReAct 步骤 | thought / action / observation |
| `chunk` | 流式 token（客户端 caps 含 `stream_chunk` 时） |
| `zombie_tasks_pending` | 断电遗留任务摘要 |
| 后台任务进度 | `l3_event_bus` 广播 |

桌面 SSOT：[CURRENT_SYSTEM_ARCHITECTURE.md](../architecture/CURRENT_SYSTEM_ARCHITECTURE.md) §5–§6。

---

## 四、标准事件形状（概念）

```python
# 输入（概念模型）
@dataclass
class SensoryInputEvent:
    source: str      # "gui" | "voice" | "lark" | "telegram" | ...
    intent: str      # 用户文本
    metadata: dict   # session_id, run_id, attachments, ...

# 输出
@dataclass
class SensoryOutputEvent:
    source_ref: str
    content: str
    action_type: str  # "text" | "chunk" | "thought" | "answer" | ...
    metadata: dict
```

Legacy `core/event_bus.py` 使用同名概念；**桌面连 L3 WS，不依赖 core daemon**。

---

## 五、Legacy：core/event_bus + daemon

| 组件 | 路径 | 状态 |
|------|------|------|
| OmniSensoryBus | `core/event_bus.py` | Legacy v8.0 |
| brain_worker | `_brain_worker()` → `agent_loop` | Legacy |
| Daemon | `core/daemon.py` | Legacy IM 拉取 |

保留供 headless/测试；**产品主路径 = L3 ws_server**。

---

## 六、Session 与 runId

- **session_id**：多会话隔离（IM chat id、桌面 session）
- **run_id**：单次请求追踪；日志 `[RunID: xxxx]`
- L3 `foreground_run_registry` / IM session 队列实现并行会话

---

## 七、流式神经 (Streaming)

- LiteLLM stream → WS `action_type=chunk`
- 客户端 manifest 声明 `stream_chunk` 才转发（能力协商）

---

## 八、持久化

- **Legacy**：`core/event_bus` SQLite 队列（`event_queue.db`）
- **L3 后台任务**：`~/.jachin/workspace/.background_tasks/` SQLite + `zombie_tasks.json`

---

## 九、参考

- [07_LAYER3_TERMINAL.md](./07_LAYER3_TERMINAL.md)
- [10_CONTROL_DATA_PLANE.md](./10_CONTROL_DATA_PLANE.md)
- [DESKTOP_OMNI_MULTIMODAL_ATTACHMENT_PERFORMANCE.md](../architecture/DESKTOP_OMNI_MULTIMODAL_ATTACHMENT_PERFORMANCE.md)
