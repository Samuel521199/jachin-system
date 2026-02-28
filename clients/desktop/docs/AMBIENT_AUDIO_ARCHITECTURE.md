# 全天候无感陪伴 — 架构评估与技术栈

## 一、可行性深度分析 (CTO Feasibility Assessment)

基于 **VAD（静音检测）+ Trailing Silence（尾音超时）** 的截断方案，是业界唯一可行且成熟的**离线端侧**方案。

| 维度 | 结论 | 说明 |
|------|------|------|
| **技术可行性** | 极高 | cpal 获取底层音频流，损耗极低；silero-vad.onnx 极小（&lt;2MB），单次推理 1–2ms，端侧单核 CPU 即可实时运行。 |
| **体验可行性** | 优秀 | 解决「什么时候该停止录音」的痛点，与唤醒词/录音按钮形成「听 → 说 → 想 → 答」闭环。 |

---

## 二、核心技术栈与模型 (Core Tech Stack & Models)

代码位置：**Rust**，位于 `clients/desktop/src-tauri/src/`。

| 层级 | 选型 | 说明 |
|------|------|------|
| **语言层** | Rust | 端侧逻辑全部在 `src-tauri/src/`，与 Tauri 主进程同进程。 |
| **音频采集** | **cpal** | 跨平台音频 I/O 原生库，后台非阻塞读取麦克风，低延迟。 |
| **重采样** | **rubato** 或 **daspmix** | 将麦克风 48kHz（或 44.1kHz）实时转为 16kHz，满足 VAD/STT 输入。 |
| **AI 推理** | **ort** | ONNX Runtime Rust 绑定，加载并运行 ONNX 模型。 |
| **VAD 模型** | **silero_vad.onnx** | 业界公认的轻量级端侧 VAD，&lt;2MB，512 样本/块，输出语音概率。 |
| **线程通信** | **crossbeam-channel** 或 **tokio::sync::mpsc** | 音频线程 → Worker 线程传递样本，保证回调不卡顿、无阻塞。 |
| **输出格式** | 16kHz, Mono, f32 | 重采样后与 Silero-VAD、后端 STT 一致。 |
| **截断逻辑** | 尾音静音超时 | 连续静音 &gt; 800ms 且 Buffer 已有数据 → 一次完整发言 → 编码并 `emit_all`。 |

---

## 三、状态机流转架构图 (State Machine Flow)

音频引擎在运行时，在以下状态间流转；实现 `ambient_audio.rs` 时须按此状态机驱动 Buffer 与截断逻辑。

```
[IDLE]  (闲置 / 等待唤醒)
   │
   │  触发：WAKE_UP 事件 或 按下录音键
   ▼
[LISTENING]  (等待第一声)
   │
   │  条件：VAD > 0.5 且持续几帧
   ▼
[SPEAKING]  (正在说话 → 写入 Buffer)
   │
   │  · VAD < 0.5  → 启动 Silence Timer
   │  · VAD > 0.5  → 重置 Silence Timer
   ▼
[ENDPOINTING]  (Silence Timer > 800ms 或 达到 15 秒上限)
   │
   ├── 1. 截断音频，结束本段循环，发送给 Layer 2（STT 引擎）
   └── 2. 状态重置回 [IDLE]
```

| 状态 | 含义 |
|------|------|
| **IDLE** | 未在录；等待唤醒词或用户按录音键后进入 LISTENING。 |
| **LISTENING** | 已开麦，等待 VAD 检测到人声（连续几帧 > 0.5）再进入 SPEAKING。 |
| **SPEAKING** | 正在录入发言，样本写入 Buffer；静音则启动尾音计时，有人声则重置计时。 |
| **ENDPOINTING** | 尾音超时（800ms）或达到 15 秒 → 截断、送 STT、回到 IDLE。 |

---

## 四、核心难点与排雷 (Risk Mitigation)

### 1. 采样率不一致（采样率地狱）

- **问题**：麦克风默认采样率多样（44.1kHz、48kHz 等），而 VAD/STT 模型通常只接受 **16kHz**。
- **要求**：**必须在 Rust 端侧做实时重采样（Resampling）**，不能依赖设备恰好为 16kHz。
- **实现要点**：
  - 在**音频回调或紧接其后的无锁路径**中，将设备采样率 → 16kHz（如 48k→16k 线性插值或小型 resampler 库）。
  - 仅将 **16kHz 的 f32 流** 送入下游 VAD 与 Ring Buffer。

### 2. 音频回调不得阻塞（线程安全）

- **问题**：音频回调运行在**实时、高优先级**线程，若在其中做网络请求、大块内存分配或 ONNX 推理，会导致卡顿、爆音、丢帧。
- **要求**：
  - **禁止**在回调内：网络 I/O、大内存分配、ONNX 推理、文件 I/O。
  - **仅**在回调内：拷贝/重采样后的样本写入 **无锁队列（Lock-free RingBuffer）**。
  - 由**独立 Worker 线程**从 RingBuffer 取数据，执行：凑齐 512 样本 → VAD 推理 → Buffer 逻辑 → 超时截断 → 编码/写文件 → `emit_all("ambient_speech_detected", payload)`。

---

## 五、推荐数据流（与 Cursor 规范一致）

```
[麦克风] → cpal 回调
    → 实时重采样到 16kHz (Mono, f32)
    → 写入 无锁 RingBuffer
        → Worker 线程读取
            → 每 512 样本跑 Silero-VAD
            → VAD < 0.5：静音计数；VAD >= 0.5：写入发言 Buffer
            → 连续静音 > 800ms 且 Buffer 非空 → 一次完整发言
                → WAV 编码 / Base64
                → app_handle.emit_all("ambient_speech_detected", payload)
```

实现 `ambient_audio.rs` 时须遵循：**状态机（§三）**、上述数据流与两条排雷（重采样 + 无锁队列 + Worker）。
