# 语音唤起（Voice Wake）架构方案

> **状态**：已编码（ambient 特性）— KWS 当前为 **STT 辅助回退**（JVS 短窗 STT + 短语匹配）；Porcupine 低功耗门卫待 Windows 原生库就绪后接入  
> **前置完成**：陪伴态语音输入（JVS STT）+ 语音输出（JVS TTS + 系统扬声器）已打通  
> **关联文档**：`VOICE_COMPANION_MODULE_PLAN.md`、`AMBIENT_AUDIO_ARCHITECTURE.md`、`docs/VOICE_AND_TTS_GUIDE.md`、**`VOICE_BARGE_IN_AND_WAKE_ACK.md`**（打断与唤醒口头确认）  
> **代码锚点**：`src-tauri/src/stt/wake_pipeline.rs`、`wake_listener.rs`、`wake_kws.rs`、`voice_wake_bridge.rs`、`keyword_spotting.rs`、`stt_start_wake_listener`、`WakeModePanel.tsx`、`UserSettings.wake_word`、`voiceSessionStore.ts`、`chat.tsx`（WAKE_UP）

---

## 1. 核心结论：L3 不需要、也不应该常驻听麦

| 问题 | 答案 |
|------|------|
| 唤醒模块放在哪？ | **Tauri 桌面端 Rust 主进程** — 音频的唯一入口 |
| L3 / JVS / Python 需要常驻听麦吗？ | **否** — JVS 仅做唤醒**之后**的 STT/TTS；L3 只处理文本 |
| 「一直在监听」是指什么？ | 仅 **KWS 门卫**（Porcupine，~数 MB）在听唤醒词，不是 SenseVoice 全量 STT |
| L3 何时介入？ | 唤醒 → VAD 截断 → JVS STT 出文本后，走 Sensory WS（18981）进入文本轮次 |

**为什么不放在 L3/Python？**

- 常驻麦克风权限归属模糊，隐私/休眠策略难以统一管控
- 与「JVS 与 L3 零耦合」原则冲突
- L3 宕机时麦克风仍应被释放，若在 L3 内则无法独立控制

---

## 2. 三层架构：门卫 → 触发中枢 → 主脑

```
┌──────────────────────────────────────────────────────────────────┐
│  门卫 The Watcher（Rust，常驻 KWS）                               │
│  · 唤醒模式 ON 时后台运行，应用退出 / 隐私模式时立即 stop          │
│  · 内存目标 < ~10MB（Porcupine 级）；CPU 仅 KWS 前向               │
│  · 只认**用户配置的唤醒句**（非硬编码）；其余 PCM 帧用完即弃          │
│  · KWS 阶段音频绝不发往 JVS / L3 / 网络                           │
└────────────────────────────┬─────────────────────────────────────┘
                             │ Tauri 事件 WAKE_UP
┌────────────────────────────▼─────────────────────────────────────┐
│  触发中枢 Trigger Hub（Rust + React voice 模块）                  │
│  · 并行：Earcon「滴」+ Orb → LISTENING（绿）+ sessionStore        │
│  · 暂停 KWS（防唤醒词被录入 utterance）                            │
│  · voiceCompanionActiveRef = true（预备陪伴轮次）                  │
│  · Rust VAD 截断（同一 cpal 流，React 不开第二条麦）               │
│  · 超时无人声 → 回 KWS_IDLE（见 §4 状态机）                        │
└────────────────────────────┬─────────────────────────────────────┘
                             │ utterance WAV（完整一句话）
┌────────────────────────────▼─────────────────────────────────────┐
│  主脑 Pipeline                                                    │
│  · JVS :18982  POST /v1/stt/transcribe → 文本（SenseVoice，1次）  │
│  · onCompanionInject(text) → doActualSend → L3 :18981            │
│  · voiceOrchestrator 断句 + TTS（≤3 句 speakable 策略）           │
└──────────────────────────────────────────────────────────────────┘
```

**组件职责一览**

| 组件 | KWS 期间 | 唤醒后采集 | L3 回复后 |
|------|----------|------------|-----------|
| `keyword_spotting.rs` | ✅ KWS 推理 | 协同 VAD / 暂停自身 | 冷却后恢复 |
| `voiceSessionStore` | `idle` 呼吸 | → `listening` | `thinking` → `speaking` → `idle` |
| `voiceOrchestrator` | ❌ 未启动 | ❌ 无文本，不参与 | ✅ `startSession` + TTS 队列 |
| `JachinOrb` | 安静呼吸 | **绿色律动** | thinking / speaking 动效 |

> `voiceOrchestrator` 是 L3 文本到达后的 TTS 编排器，不是 KWS 引擎。唤醒瞬间只需更新 sessionStore + Orb + Earcon；STT 出字后才调 `orchestrator.startSession()`。

---

## 3. 端到端 Workflow

### 3.1 静默期（KWS_IDLE）

- `JachinOrb` 右下角安静呼吸；HUD 隐藏或 idle
- Rust 门卫 Porcupine 占麦，微循环推理；帧用完即弃
- **禁止**在此阶段加载 SenseVoice 或向 JVS 发任何请求

### 3.2 唤醒瞬间（WAKE_ACK，≤500ms 内完成）

用户说出**当前已保存的唤醒句**（见 §7.4，可自定义，非固定「Hey Jachin」）：

1. KWS 回调 → `WakeWordDetector::emit_wake_up` → Tauri 事件 `WAKE_UP`
2. **并行执行**（不阻塞彼此）：
   - **Earcon**：Rust `voice_companion_play_wav` 播短 WAV「滴」(~150ms)
   - **UI**：`voiceSessionStore.setState('listening')` + Orb 绿色律动；可选 `companion_reveal` 浮出 Orb
   - **Rust**：KWS 暂停/降权，切入 VAD 采集，启动 `LISTENING_TIMEOUT` 计时器（默认 6s）
3. **此时不做**：不调 `doActualSend`、不调 `orchestrator.startSession()`

### 3.3 录音截断（LISTENING → ENDPOINTING）

- VAD 检测到人声 → 进入 `SPEAKING`，写入 utterance buffer
- 尾音静音 **> 800ms** 或达最长 **15s** → `ENDPOINTING`
- 若 `LISTENING_TIMEOUT`（6s）到期仍无人声 → 播短提示「嘀——」→ 回 `KWS_IDLE`（不送 STT）
- 最短有效语音 < **300ms** → 视为噪声，丢弃，回 `KWS_IDLE`

### 3.4 STT 与陪伴轮次（COMPANION_TURN）

- utterance WAV（16kHz mono）→ `POST JVS :18982 /v1/stt/transcribe`
- **JVS 懒启动**：若 JVS 尚未运行，先 `jvs_start` 等待就绪（≤3s），超时则降级提示「语音服务启动中，请稍候」
- STT 文本 → `onCompanionInject(text)` → 走与 `simulate_voice_companion_chat.ps1` **完全相同**的注入路径 → HUD user 气泡 → `doActualSend` → L3
- L3 chunk/answer → HUD 流式 + `voiceOrchestrator` TTS

### 3.5 回合结束

- TTS 播完 → `voiceSessionStore('idle')` → Orb 回呼吸
- **冷却 1–2s** 后恢复 KWS 监听（避免 TTS 回声误触发）

---

## 4. 状态机（端侧 SSOT）

```
┌─────────────────────────────────────────────────────────────────────┐
│                            KWS_IDLE                                  │
│  门卫监听；Orb 呼吸；内存 ~数 MB；KWS CPU 低占用                     │
└────────────────────────┬────────────────────────────────────────────┘
                         │ KWS 超阈值 / onKeywordDetected
                         ▼
                    ┌─────────┐
                    │WAKE_ACK │  Earcon + Orb 绿色 + KWS 暂停（≤500ms）
                    └────┬────┘
                         │
                         ▼
                    ┌──────────┐
                    │LISTENING │  VAD 等人声；启动超时计时器 6s
                    └────┬─────┘
          有人声 │              │ 超时 6s / 噪声 <300ms
                 ▼              ▼
          ┌──────────┐     ┌──────────────────────┐
          │SPEAKING  │     │ 播短提示「嘀」→ 回     │
          │写 buffer │     │ KWS_IDLE（冷却 500ms）│
          └────┬─────┘     └──────────────────────┘
  尾音 800ms │   │ 满 15s
               ▼
          ┌────────────┐
          │ENDPOINTING │  编码 WAV → JVS STT（JVS 懒启动）
          └─────┬──────┘
                │ STT 成功
                ▼
         ┌──────────────────┐
         │ COMPANION_TURN   │  inject → doActualSend → L3 → TTS
         └──────────┬───────┘
                    │ TTS 播完 + 冷却 1–2s
                    ▼
                KWS_IDLE（门卫恢复）
```

**Barge-in**（TTS 播放中用户开口，或 Verbal ACK 播放中）：
→ VAD 检测用户语音 → `orchestrator.bargeIn()` + L3 `run_abort` → 回 `LISTENING`（**不要求**重复唤醒句）。详见 **`VOICE_BARGE_IN_AND_WAKE_ACK.md`**。

**STT 失败 / JVS 超时**：
→ 播提示「没有听清，请再试一次」→ 回 `KWS_IDLE`，不卡死 `LISTENING`

---

## 5. 与现有陪伴链路的衔接

唤醒**不新建** L3 协议，只新增「自动注入」入口：

| 步骤 | 动作 | 对应文件 |
|------|------|----------|
| 1 | 监听 `WAKE_UP` 事件 | `chat.tsx` 或 `OrbWindow.tsx` |
| 2 | `voiceSessionStore.setState('listening')` | `voiceSessionStore.ts` |
| 3 | Orb / HUD 视觉反馈 | `JachinOrb.tsx`、`HUDMessagePanel.tsx` |
| 4 | Earcon 播放 | Rust `voice_companion_play_wav`（短 WAV）|
| 5 | `voiceCompanionActiveRef = true` | `chat.tsx` |
| 6 | VAD 截断 → JVS STT | `keyword_spotting.rs` + `endpointing.rs` + `voiceBridge.ts` |
| 7 | `onCompanionInject(text)` | 同 `voice-sim-user-input` 路径 |
| 8 | `doActualSend` → L3 | 已实现 |
| 9 | `orchestrator.startSession` + TTS | `doActualSend` 内已有 |

---

## 6. 麦克风：单麦权原则

**唯一 `cpal` 流在 Rust `AudioCaptureManager`**；React/WebView 不独立开麦。

原因：WebView 独占麦克风会导致与 Rust VAD 双轨冲突、Windows WASAPI 排他模式锁定、后台窗口无权限等问题（陪伴 TTS 已有前车之鉴）。

```
cpal 流（唯一）
    ↓ 重采样 16kHz（rubato）
    ↓
  fan-out
    ├── KWS worker：仅 KWS_IDLE 时消费（Porcupine）
    └── VAD worker：仅 LISTENING…ENDPOINTING 时消费（Silero-VAD）
```

- `ambient` feature 的 `audio_capture.rs` / `endpointing.rs` 作为 VAD 段复用基础
- JVS **不持有麦克风**，仅 HTTP 接收 WAV

---

## 7. KWS 引擎选型

### 7.1 选型对比

| 方案 | 优点 | 缺点 | 建议 |
|------|------|------|------|
| **Picovoice Porcupine（Rust）** | 企业级离线；`.ppn` 仅数 KB；Rust crate；零网络 | 自定义句需对应 `.ppn`；AccessKey 有月度上限 | **Phase W1 首推**；用户改句须换/训模型 |
| **openWakeWord（ONNX）** | 与端侧 `ort` 完全统一；无第三方 Key；纯离线开源 | 中文/自定义唤醒词需训练或选合适预训练模型；阈值需调 | 无 Picovoice 许可时的 **Plan B**，或长期替代选项 |
| **Snowboy** | — | 已停止维护 | 不推荐 |
| **SenseVoice 流式找词** | — | 数百 MB、高 CPU、延迟大 | **禁止作为 KWS** |

> **推荐路径**：W1 内置词联调；W3 用户自由填写 `wake_word` 并热重载；W4 录制训练绑定专属 `.ppn`。无 AccessKey 时可切 openWakeWord。

### 7.3 唤醒句 SSOT（存储与读取）

| 项 | 说明 |
|----|------|
| **用户配置字段** | `UserSettings.wake_word`（`config/user_settings.rs`） |
| **设置 UI** | `WakeModePanel.tsx`（唤醒模式页：输入框 + 保存 + 启动监听） |
| **IPC 传参** | `stt_start_wake_listener({ wake_word })` — 优先用调用参数，否则读 `UserSettings`，再空则用出厂默认 |
| **出厂默认（可改）** | `Jachin` — **仅**在未配置时的占位，**不是**产品强制唤醒句 |
| **环境变量覆盖（可选）** | `JACHIN_WAKE_WORD` — 仅用于开发/企业部署预置，**不覆盖用户已保存的设置** |
| **KWS 模型目录** | `data/models/voice/kws/` — 按唤醒句或模型 id 存放 `.ppn` / ONNX |
| **事件 payload** | `WAKE_UP` 携带 `{ wake_word: "<当前生效句>" }`，便于 UI 展示「由哪句触发」 |

**原则：唤醒句由用户选定并持久化；代码与文档中不得写死唯一合法唤醒词。**

### 7.4 用户自定义唤醒句（产品要求）

用户可在设置中**自由填写**用于唤起的短语或名字，例如：

- 「嘿 Jachin」「贾钦」「小贾」「Hey Jarvis」（开发联调）
- 用户自定义昵称、短句（在引擎与校验规则允许范围内）

#### 7.4.1 设置流程（与现有 UI 对齐）

```
用户在 WakeModePanel 输入唤醒句
    → 校验（见 7.4.2）
    → update_user_settings({ wake_word })
    → 若门卫正在运行：stt_stop_wake_listener → 加载/绑定新 KWS 模型 → stt_start_wake_listener({ wake_word })
    → 设置页展示「当前监听：{wake_word}」
```

- **保存即生效**：修改唤醒句后，若监听已开启，须 **热重载 KWS**（stop → 换模型 → start），无需重启整个应用。
- **陪伴 Orb 路径**：唤醒模式 ON 时，门卫使用的 `wake_word` 与 `WakeModePanel` / 设置页 **同一 SSOT**，禁止 Orb 链路与控制台各记一套。

#### 7.4.2 输入校验（实现约束，非固定词表）

| 规则 | 建议值 | 说明 |
|------|--------|------|
| 最小长度 | 2 个字符（中文）/ 3 个字母（英文） | 过短易误唤醒 |
| 最大长度 | 32 字符 | Porcupine / UI 友好 |
| 允许字符 | 中文、英文、数字、空格、常见标点 | 禁止纯符号 |
| 禁止内容 | 空串、仅空格 | 保存时回退出厂默认或拒绝并提示 |
| 与指令区分 | — | 唤醒句只用于 KWS；用户指令在唤醒**之后**的 VAD 段采集，二者不混 |

校验失败时：**不写入** `wake_word`，提示用户缩短/改写，**不静默回退到隐藏默认值**（避免用户以为已改成某句而实际仍在听旧句）。

#### 7.4.3 KWS 引擎与用户自定义句的绑定

用户改唤醒句时，门卫加载的 **不是**「改字符串就能听」——须为**该句**提供可推理的 KWS 资产：

| 引擎 | 用户改句后的行为 |
|------|------------------|
| **Porcupine** | 须为该句准备 `.ppn`（Picovoice Console 训练或内置词表匹配）。本地缓存路径建议：`data/models/voice/kws/{slug}.ppn`，`UserSettings` 可增加 `wake_kws_model_path` 或按 hash 映射 |
| **openWakeWord（Plan B）** | 按句训练/选用对应 ONNX，或通用模型 + 阈值；同样须 **模型与 wake_word 绑定** 后 reload |
| **内置词（仅开发）** | Porcupine 内置 `jarvis` 等 — **仅 W1 联调**，不对用户暴露为「唯一合法句」 |

**用户自选句的产品路径（推荐）**

1. **Phase W3**：设置页自由输入 + 保存 + 热重载；出厂默认 `Jachin` 可一键恢复。
2. **Phase W4**：设置内「录制 3 次唤醒句」→ 后台调 Picovoice Console API 或本地训练流水线生成 `.ppn` → 自动写入 `kws/` 并绑定（真正「任意句」）。
3. **过渡方案**：设置页提供 **预设列表 + 自定义输入**；自定义句若暂无 `.ppn`，提示「该唤醒句需生成语音模型，请先用预设或完成录制训练」，**禁止**静默改用别的句。

#### 7.4.4 多唤醒句（可选，Phase W4）

- 用户可配置 **最多 N 句**（建议 N=3）同时生效，如「嘿 Jachin」+「小贾」。
- KWS 加载多个 `.ppn` / 多 keyword；`WAKE_UP` payload 标明 `matched_wake_word`。
- 与单句模式共用同一套 `UserSettings` 扩展字段（如 `wake_words: string[]`），**向后兼容**单字段 `wake_word`。

#### 7.4.5 禁止事项

- ❌ 在 Rust/TS 中 `const WAKE_PHRASE = "Hey Jachin"` 作为唯一监听目标（仅允许 `DEFAULT_WAKE_WORD` 作未配置占位）
- ❌ 用户改句后仍监听旧 `.ppn` 且不提示
- ❌ L3 prompt 里写死「用户必须说 Hey Jachin」（L3 不参与唤醒）

### 7.5 Porcupine 落地要点（与自定义句配合）

- **绑定**：`picovoice/porcupine` Rust crate，运行在 `keyword_spotting.rs` 的独立 worker 线程
- **开发联调**：可用内置 `jarvis` 验证链路，**不代表**用户最终只能使用该词
- **配置**：`PICOVOICE_ACCESS_KEY`；`wake_sensitivity` 进 `UserSettings`（0.0–1.0，默认 0.5）
- **降级**：AccessKey 未配置 → 自动切 openWakeWord；openWakeWord 模型缺失 → 唤醒功能禁用，退化为 PTT + 点击 Orb

> **推荐路径**：W1 用内置词联调；W3 开放 `UserSettings.wake_word` + 热重载；W4 录制训练生成用户专属 `.ppn`，实现真正自由选定唤醒句。

---

## 8. Earcon 与 Orb 反馈规范

| 时间点 | 听觉 | 视觉 | 时序要求 |
|--------|------|------|----------|
| 检测到唤醒词 | 单音「滴」约 150ms | Orb → **LISTENING** 绿色律动 | 与 VAD 启动**并行**，总延迟 ≤500ms |
| LISTENING 超时（无人声）| 短「嘀——」降调 | Orb 回呼吸 | 超时后 ≤300ms |
| VAD 录到人声 | 可选轻声第二音（本期可省略）| Orb 律动略增强 | — |
| STT 完成 | 无 | HUD user 气泡出现 | — |
| L3 thinking | 无 | Orb **thinking** 动效 | 已有 |
| TTS 播报 | 回复内容 | Orb **speaking** 动效 | `voiceOrchestrator` 已有 |

> Earcon 路径：优先 Rust `voice_companion_play_wav`（系统扬声器），避免 WebView `<audio>` 在 Orb 未聚焦时无声。Earcon WAV 文件放 `data/audio/earcon_wake.wav`（单声道 16kHz，约 150ms）。

---

## 9. 何时开启 / 关闭门卫

| 条件 | KWS 动作 |
|------|----------|
| 用户「唤醒模式」ON（设置页） | `stt_start_wake_listener` |
| 隐私模式 ON | **立即 stop**，释放麦克风 |
| 休眠模式 ON | **立即 stop**，释放麦克风 |
| 应用退出 | stop |
| 无麦克风权限 / 设备不存在 | 唤醒功能禁用，设置页灰化，降级 PTT |
| JVS 服务宕机（KWS 阶段不受影响）| KWS 继续；唤醒后 STT 失败时提示 |

联动现有 IPC：`get_privacy_mode`、`get_hibernate_mode`（在 `main.rs` 启动逻辑统一门禁）。

---

## 10. 安全与误唤醒防护

1. **KWS 阶段音频不上传、不落盘**（仅 debug 开关下可打帧能量日志，不含 PCM 原文）
2. **TTS 播放期间**暂停 KWS 或将阈值提升至 0.9（防 Jachin 说自己名字时触发）
3. **冷却期**：TTS 结束后 1–2s 内忽略 KWS 结果
4. **最短 utterance 300ms**：低于此长度不送 STT，回 KWS_IDLE
5. **灵敏度可调**：`wake_sensitivity`（0.5 默认）；唤醒句变更后建议提示用户重新试唤醒一次
6. **日志阶段**：`voice_companion.log` 增加 `wake.detected`、`wake.earcon`、`wake.timeout`、`wake.stt_ok`、`wake.stt_fail`（均不含 PCM）

---

## 11. L3 需要做什么

**几乎不需要改动**。

| 项 | 必要性 | 说明 |
|----|--------|------|
| `channel=voice_wake` 元数据 | 可选 | Sensory 消息带 `source: wake`，供统计/限流 |
| 唤醒专属 system prompt | 不推荐 | 语音只是 I/O 形态，`run_agent` 与文本聊天同一路 |
| L3 监听麦克风 | **禁止** | 违反分层原则 |
| `run_abort` 打断支持 | 已有（Phase C） | Barge-in 时使用 |

L3 的 Sensory WS 是**等文本、推文本**的长连接，不是音频通道。

---

## 12. 设计边界与禁忌

| 操作 | 结论 | 原因 |
|------|------|------|
| L3 / JVS 常驻听麦 | ❌ 禁止 | 违反分层；隐私管控困难 |
| React/WebView 独立开 `MediaRecorder` 采集 | ❌ 禁止 | 与 Rust VAD 双轨冲突，易产生权限/无声问题 |
| `@picovoice/porcupine-react` 作量产方案 | ❌ 禁止 | WebView 占麦；Rust 方案已有基础 |
| KWS 阶段调用 SenseVoice | ❌ 禁止 | 数百 MB 常驻，违背轻量门卫目标 |
| 唤醒单独新建 L3 发送链路 | ❌ 禁止 | 与现有 `onCompanionInject` 路径重复 |
| `voiceOrchestrator` 在 KWS 阶段启动 | ❌ 禁止 | 尚无文本，无意义 |
| 硬编码唯一唤醒句 | ❌ 禁止 | 须 `UserSettings.wake_word`，用户可自由改 |
| 用户改句后仍监听旧 KWS 模型 | ❌ 禁止 | 须热重载或明确提示缺模型 |
| Porcupine React SDK 用于联调原型 | ⚠️ 可临时 | 联调后迁 Rust；不进 main |

---

## 13. 前置依赖与准备（Phase W1 开工前）

| 依赖项 | 说明 | 获取方式 |
|--------|------|----------|
| `PICOVOICE_ACCESS_KEY` | Porcupine 推理所需 | [picovoice.ai](https://picovoice.ai) 免费注册，月度限额 |
| 内置关键词 `jarvis` | **仅 W1 链路联调** | Porcupine 内置，非用户最终唤醒句 |
| 用户唤醒句对应 `.ppn` | 每句或每用户一份 KWS 模型 | Console 训练 / W4 录制流水线；目录 `data/models/voice/kws/` |
| `earcon_wake.wav` | 唤醒提示音 | 项目内自制或使用 CC0 音效，约 150ms，16kHz mono |
| `ambient` feature 或独立 VAD | Silero-VAD + endpointing | `Cargo.toml` 启用 `ambient` feature |

---

## 14. 分阶段交付

### Phase W1 — 门卫骨架（1–2 天）

- [ ] `keyword_spotting.rs` 接入 Porcupine Rust crate（用内置 `jarvis` 联调）
- [ ] `WAKE_UP` → `voiceSessionStore.listening` + Orb 绿色 + Earcon
- [ ] LISTENING_TIMEOUT 6s → 回 KWS_IDLE
- [ ] 隐私/休眠/应用退出 → `stt_stop_wake_listener`
- [ ] `voice_companion.log` 增加 `wake.*` 阶段

### Phase W2 — 采集 + STT 串联（2–3 天）

- [ ] 单麦权 `AudioCaptureManager`；KWS / VAD fan-out
- [ ] JVS 懒启动检测（超时提示）
- [ ] utterance WAV → JVS STT → `onCompanionInject`（与 PTT 同路径）
- [ ] STT 失败降级提示，回 KWS_IDLE

### Phase W3 — 体验打磨（2–3 天）

- [ ] TTS 期间 KWS 阈值提升 + 冷却计时
- [ ] Barge-in 与唤醒共存（`orchestrator.bargeIn` + `run_abort`）
- [ ] **唤醒句**：`WakeModePanel` 自由输入 + 保存到 `UserSettings.wake_word` + 校验
- [ ] 改句后 **热重载** KWS（stop → 换模型 → start）；设置页展示「当前监听：{wake_word}」
- [ ] 唤醒开关、灵敏度滑块

### Phase W4 — 产品化与可选（按需）

- [ ] **录制 3 次**生成用户专属 `.ppn`，实现任意自定义唤醒句（见 §7.4.3）
- [ ] 预设唤醒句列表 + 自定义输入；无模型时明确提示，不静默换句
- [ ] 无 AccessKey 时自动切 openWakeWord（Plan B）
- [ ] 全局快捷键与唤醒共存策略
- [ ] 多唤醒句（`wake_words[]`，最多 3 句，见 §7.4.4）

---

## 15. 验收标准

1. 唤醒模式 ON，JVS **无** SenseVoice 级常驻 CPU；门卫内存 **≤ ~10MB**。
2. 用户将唤醒句改为 **非默认值**（如「小贾」）并保存后，说该句 **≤500ms** 内：Earcon + Orb 绿色 LISTENING（须加载对应 KWS 模型）。
3. KWS 阶段日志中 **零条** JVS/L3 音频请求。
4. 6s 内无人声：Orb 自动回呼吸，门卫恢复，无需用户操作。
5. 说完指令 → 自动 STT → HUD user 气泡，无需键盘。
6. L3 回复与 PTT 路径输出一致；TTS ≤ 3 句 speakable。
7. 隐私/休眠切换后 **500ms** 内 `stt_wake_listener_running = false`。
8. JVS 宕机：Orb 提示「语音服务不可用」后回 idle，不卡死 LISTENING。
9. Porcupine AccessKey 缺失：降级提示清晰，应用其余功能不受影响。
10. 修改 `wake_word` 后若监听已开，**无需重启应用** 即按新句监听；`WAKE_UP` payload 中的 `wake_word` 与设置一致。

---

## 16. 一句话总结

**门卫（Porcupine @ Rust）只认用户配置的唤醒句（`UserSettings.wake_word`，可自由选定）；检测到后 Earcon + Orb LISTENING + Rust VAD 截断；utterance WAV 送 JVS STT；文本走陪伴 inject → L3；`voiceOrchestrator` 只管回复 TTS。L3 从不监听麦克风。**
