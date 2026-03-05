# 全息感官总线 (Omni-Sensory Bus)

**文档类型**: 白皮书 · 架构设计  
**版本**: v8.0 (The Singularity OS)  
**状态**: 已实现，v8.0 扩展 Session Multiplexing、全链路 runId、流式神经

---

## 一、 设计理念

> **"小孩子才做选择，划时代的数字生命全都要。"**

Jachin Nexus 的三大交互形态（Voice、GUI/Sprite、IM）不是互相割裂的模块，而是同一颗大脑的**外接感官器官**。全息感官总线通过**端口-适配器架构 (Hexagonal Architecture)**，将所有输入归一化、输出多路分发，让核心大脑（Layer 2 Agent Loop）对交互来源无感。

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        全息感官总线 (Omni-Sensory Bus)                     │
├─────────────────────────────────────────────────────────────────────────┤
│  输入归一化 (Input Normalization)                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                               │
│  │ Voice    │  │ Sprite   │  │ IM       │   →  Event(source, intent, payload)  │
│  │ 麦克风   │  │ 桌面精灵  │  │ Telegram │                               │
│  └──────────┘  └──────────┘  └──────────┘                               │
│       │              │              │                                    │
│       └──────────────┼──────────────┘                                    │
│                      ▼                                                   │
│              OmniInputEvent 队列                                          │
│                      │                                                   │
│                      ▼                                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │              大脑 (Agent Loop) — 只管 Event，不问来源                   │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
│                      │                                                   │
│                      ▼                                                   │
│              OmniOutputEvent                                             │
│                      │                                                   │
│  输出多路分发 (Output Multiplexing)                                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                               │
│  │ source=  │  │ source=  │  │ source=* │                               │
│  │ voice    │  │ telegram │  │ (全局)   │                               │
│  │ → TTS    │  │ → HTTP   │  │ → WS     │                               │
│  │ 朗读     │  │ Callback │  │ 精灵动画  │                               │
│  └──────────┘  └──────────┘  └──────────┘                               │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 二、 三大感官器官

### 2.1 Jachin Voice (听觉与喉咙)

| 项目 | 说明 |
|------|------|
| **定位** | 绝对无感的空间级交互 (Ambient Computing) |
| **目标用户** | 硬核极客、智能家居控制者 |
| **Animus Protocol (灵魂联动)** | 接入 **pvporcupine** 离线唤醒词引擎，实现 "Hey Jachin" **极低功耗监听** |
| **STT** | Whisper 转文本 → `emit_omni_input("voice", text, {})` |
| **TTS** | Edge-TTS / CosVoice 合成播报 |
| **边界** | 无屏幕亦可运行（树莓派 + 麦克风 + 音箱） |

### 2.2 Jachin Sprite (桌面精灵与外壳)

| 项目 | 说明 |
|------|------|
| **定位** | 具象化视觉陪伴 + 零摩擦企业级入口 |
| **目标用户** | B 端企业员工、二次元/桌搭爱好者 |
| **控制台模式** | Tauri 系统托盘，扫码即连、静默拉起 Layer 2 |
| **精灵模式** | 透明无边框窗口 + Live2D / 赛博朋克 3D 悬浮球 |
| **Layer 3 视觉投射** | Daemon 启动**本地 WebSocket Server**，将 OmniOutputEvent（思考状态、HITL 授权请求等）**实时广播**给 Tauri 桌面精灵，驱动动画 |
| **感官联动** | `[Thought]` → 托腮思考；`core:shell_exec` → 挥动手臂 |

### 2.3 Jachin Link (全息通讯网关)

| 项目 | 说明 |
|------|------|
| **定位** | 跨越物理网关的异步远程统治 |
| **目标用户** | 数字游牧民、DevOps、连锁店长 |
| **UMA** | Universal Message Adapter，Telegram/飞书/Slack 统一清洗 |
| **异步投递** | Layer 1 入队 → Layer 2 拉取（过渡期：心跳；P0：WS 长连推送）→ Agent Loop |
| **形态** | 算力永远跟随手机 |

---

## 三、 标准感官协议 (Standard Sensory Protocol)

### 3.1 SensoryInputEvent (输入归一化)

```python
@dataclass
class SensoryInputEvent:
    source: str   # "cli" | "voice" | "gui" | "webhook" | "telegram" | "layer3" | "lark"
    intent: str   # 用户输入的文本
    metadata: dict # 扩展数据 (ast_json, pending_message_ids, session_id, run_id, ...)
    # v8.0 Session Multiplexing: metadata 中可携带 session_id（Telegram Chat ID、设备 UUID、CLI 会话 ID）
    # v8.0 全链路追踪: metadata 中 run_id 贯穿全链路；无则 emit_omni_input / _persist_omni_input_sync 自动生成
```

### 3.2 SensoryOutputEvent (输出多路分发)

```python
@dataclass
class SensoryOutputEvent:
    source_ref: str   # 回显输入来源，用于路由
    content: str       # 大脑结论（或 chunk 时为单 token 碎片）
    action_type: str  # "text" | "tts_play" | "ui_animate" | "chunk" | "thought" | "action" | "observation" | "answer"
    metadata: dict    # step_type, session_id, run_id, chunk 等；用于 IM 回传、能力协商过滤
```

---

## 四、 OmniSensoryBus 类与 API

### 4.1 单例模式

```python
from core.event_bus import get_bus, SensoryInputEvent, SensoryOutputEvent

bus = get_bus()
```

### 4.2 核心方法

| 方法 | 说明 |
|------|------|
| `subscribe(event_type, handler)` | 订阅事件，如 `"output.cli"`、`"output.voice"`、`"output"` 全局 |
| `publish_input(event: SensoryInputEvent)` | 异步发射输入 |
| `publish_output(event: SensoryOutputEvent)` | 异步发射输出 |
| `start_brain_worker()` | 启动 brain_worker 后台任务 |
| `set_step_callback(cb)` | 设置 ReAct 步骤打印回调 |

### 4.3 兼容 API（daemon 等使用）

| 函数 | 说明 |
|------|------|
| `emit_omni_input(source, intent, payload)` | 同步发射输入（put_nowait） |
| `subscribe_omni_output(source, handler)` | 订阅输出，handler 接收 OmniOutputEvent |
| `start_omni_consumer()` | 启动 brain_worker |

---

## 五、 实现位置与感官插件

| 组件 | 路径 | 说明 |
|------|------|------|
| 全息感官总线 | `core/event_bus.py` | OmniSensoryBus、SensoryInputEvent、SensoryOutputEvent |
| brain_worker | `_brain_worker()` | 消费 SensoryInputEvent → agent_loop.run() → publish_output |
| Daemon 挂载 | `core/daemon.py` | start_omni_consumer() + subscribe_omni_output("telegram", ...) |
| IM 输入注入 | 拉取 task（过渡期：心跳；P0：WS 推送） | `emit_omni_input("telegram", task, {...})` |
| **CLI 感官插件** | `core/cli.py` shell | 构造 SensoryInputEvent(source='cli') → publish_input；订阅 output.cli → Rich 打印 |
| **Voice 感官插件** | `core/senses/voice_organ.py` | STT (SpeechRecognition+Whisper) → emit → TTS (edge-tts+pygame)；入口 `python -m core.voice_cli` |

---

## 六、 持久化与 Layer 3 视觉投射

- **持久化感官总线**：OmniSensoryBus 底层挂载 **SQLite 队列**，确保进程重启不丢事件。
- **Layer 3 视觉投射**：Daemon 启动本地 WebSocket Server，将 OmniOutputEvent（如 `step_type="thought"`、`action_type="hitl_request"`）实时广播给 Tauri 桌面精灵，驱动动画（托腮思考、挥动手臂等）。

## 七、 v8.0 Session Multiplexing（会话多路复用）

在 OmniSensoryBus 与 Agent Loop 之间增加 **Session Manager（会话管理器）**：

- 按 `session_id` 动态拉起**独立的 Agent Actor 协程**
- 每个 session 拥有独立的记忆上下文、工具调用栈、HITL 挂起状态
- 支持同一用户开多个并行任务线程，互不干扰
- 实现千万级并发下的记忆隔离

**实现约束**：`SensoryInputEvent` 应携带 `session_id`（缺省时使用 `source` 作为 fallback）。详见 `docs/whitepaper/V8_SINGULARITY_OS.md`。

---

## 八、 v8.0 全链路 runId 追踪 (Distributed Tracing)

- 每次用户请求在 `emit_omni_input` / `_persist_omni_input_sync` 中自动注入 `run_id`（无则 `uuid.uuid4().hex` 生成）。
- `SensoryInputEvent.metadata["run_id"]` 贯穿 `_process_single_task` → `agent_run` → `PipelineContext` → `SensoryOutputEvent.metadata["run_id"]`。
- 所有 `on_step`、`on_hitl_request`、chunk 广播的 payload/metadata 携带 `run_id`。
- 日志输出带 `[RunID: {run_id[:8]}]` 短前缀，企业级可观测性。

---

## 九、 v8.0 流式神经 (Streaming Chunk)

- LLM 推理由「一次性输出」改为**逐 token 流式输出**。
- `core/llm_provider.py`：`generate_response_stream(messages, chunk_callback)`，每 chunk 调用 `await chunk_callback(chunk_text)`。
- `_on_chunk`：`bus.publish_output(SensoryOutputEvent(action_type="chunk", metadata={run_id, ...}))`。
- **能力协商**：`_should_send_to_client` 仅当客户端 caps 含 `stream_chunk` 时转发 `step_type="chunk"` 事件。
- Manifest 示例：`{"device": "pc", "caps": ["ui_render", "hitl_popup", "stream_chunk"]}`。

---

## 十、 与原有 Workflow 总线的关系

`core/event_bus.py` 同时承载：

1. **全息感官总线**：`OmniInputEvent` / `OmniOutputEvent`，供 Voice/Sprite/IM 使用。
2. **Workflow 总线**：`BusEvent`，供 `nexus_daemon`、`workflow_runner`、`ingress` 等插件使用。

两者互不干扰，端口-适配器架构确保未来感官扩展无需改动核心大脑。
