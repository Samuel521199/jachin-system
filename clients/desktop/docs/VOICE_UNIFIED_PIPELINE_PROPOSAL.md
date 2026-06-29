# 桌面语音统一链路方案（大窗 PTT × 陪伴唤醒）

> **目的**：一条 Voice Core、多种 UX Profile；桌面不再依赖 L2 voice API；声纹与意图路由作为可选增强层。  
> **状态**：**U1–U6 主路径已落地**；含 **PTT 流式 STT（Raw TCP 优先）**、**逗号/顿号级 TTS 触发**、**JVS 会话可抢占取消**（2026-06）  
> **关联**：`VOICE_MODULE_HUMAN_GUIDE.md`（人话链路）、`VOICE_WAKE_ARCHITECTURE.md`、`VOICE_INTENT_ROUTING_AND_TASK_ORCHESTRATION.md`、`VOICE_SPEAKER_VERIFICATION_PROPOSAL.md`

---

## 1. 决策摘要（当前态）


| 问题            | 结论                      | 落地情况                          |
| ------------- | ----------------------- | ----------------------------- |
| 要不要一条链路？      | **要** — Voice Core 一条   | ✅ U1–U3                       |
| 大窗要不要「我在」？    | **不要** — 仅 WAKE Profile | ✅ `voiceProfiles.ts`          |
| L2 voice API？ | 桌面 **退役**               | ✅ `api.ts` deprecated         |
| 采音统一 Rust？    | **要** — 单麦互斥            | ✅ PTT + wake + VAD            |
| 声纹识主？         | 唤醒门 + 主人轨（可选）           | ✅ S1/S2 in `wake_pipeline.rs` |
| 进 L3 前路由？     | 规则 intent router        | ✅ `voiceIntentRouter.ts`      |


**一句话**：**听→（SV）→字→路由→L3→字→声** 是桌面唯一主路径；唤醒是带门卫和「我在」的 Profile，大窗 PTT 是静音进门、当一条语音消息的 Profile。

---

## 2. 现状架构（已实现，非目标态）

```text
┌──────────────────────────────────────────────────────────────────┐
│  UX Profile（voiceProfiles.ts）                                    │
│  WAKE          CHAT_PTT           CHAT_VAD                         │
│  门卫+我在+Orb   大窗按住说          大窗 VAD 键                      │
└────────┬───────────────┬─────────────────┬───────────────────────┘
         │               │                 │
         └───────────────┴─────────────────┘
                         │ UtteranceReady（WAV / wav_base64）
                         ▼
┌──────────────────────────────────────────────────────────────────┐
│  Voice Core                                                       │
│  ① 采音截句   Rust cpal + Silero VAD + endpointing                 │
│              PTT: start_ptt_capture / stop_ptt_capture              │
│              防双提交: chat.tsx 6s WAV 指纹去重                      │
│  ② 声纹（可选） S1 verify @唤醒；S2 filter_owner_track @截句后    │
│  ③ STT        PTT: Raw TCP(18983)优先 + WS 回退；其余走 voiceCore HTTP │
│  ④ 路由+思考   voiceIntentRouter → doActualSend → L3 :18981       │
│  ⑤ 出声        voiceOrchestrator 按停顿断句（含逗号/顿号）→ JVS TTS  │
│  ⑥ 打断        bargeIn → stopPlayback + cancel JVS(可抢占) + run_abort│
└──────────────────────────────────────────────────────────────────┘
         │
         ▼
   JVS：STT + TTS + SV（HTTP 18982 + STT Raw TCP 18983）
   L3：仅文本（与键盘同 sendInput）
```

### 2.1 与旧 L2 路径对比（历史）


|      | 旧（已退役于桌面）         | 现（SSOT）                   |
| ---- | ----------------- | ------------------------- |
| 大窗录音 | 浏览器 MediaRecorder | Rust PTT                  |
| STT  | L2 内嵌             | JVS SenseVoice            |
| 思考   | L2 LLM            | L3 WebSocket              |
| TTS  | L2 synthesize     | JVS MOSS ONNX + orchestrator |


桌面 **不要求** L2 `:18888` 运行即可使用语音；文本兜底、商城等仍可走 L2。

---

## 3. 三种 Profile 对照（代码即 SSOT）

定义见 `clients/desktop/src/voice/voiceProfiles.ts`：


| 维度            | **wake**               | **chat_ptt**                                   | **chat_vad**      |
| ------------- | ---------------------- | ---------------------------------------------- | ----------------- |
| 滴声 / 「我在」     | ✅                      | ❌                                              | ❌                 |
| Orb / HUD 强绑定 | ✅                      | ❌（除非 companionMode）                            | ❌                 |
| 60s 连续窗口      | ✅                      | ❌                                              | ❌                 |
| 门卫 KWS        | ✅                      | ❌                                              | ❌                 |
| 声纹 S1/S2      | ✅（wake_pipeline）       | ❌（PTT 不经门卫链）                                   | ❌                 |
| 采音            | Rust wake_pipeline     | Rust PTT                                       | Rust VAD capture  |
| STT           | JVS（wake 链）            | JVS 流式（Raw TCP 优先，WS 回退；结束可直用 recognized_text） | JVS via voiceCore |
| 进 L3          | dispatchVoiceUtterance | 同左                                             | 同左                |
| TTS 句数上限      | 3（陪伴默认可强制）             | 随 `ttsEnabled`（0 或 3）                          | 同 PTT             |
| L2 voice API  | ❌                      | ❌                                              | ❌                 |


**陪伴态特殊逻辑**（`chat.tsx`）：`companionMode` 或 `voiceCompanionActiveRef` 为真时，强制走 `voiceOrchestrator` + HUD 镜像（`voiceCompanionBridge`），TTS 音色回落 JVS `zm_053`（避免 L2 Neural 音色误配）。

---

## 4. 关键模块与文件（落地清单）


| 层           | 文件                                                              | 职责                                               |
| ----------- | --------------------------------------------------------------- | ------------------------------------------------ |
| Profile     | `voice/voiceProfiles.ts`                                        | wake / chat_ptt / chat_vad 配置                    |
| Core STT    | `voice/voiceCore.ts`                                            | JVS 健康检查、WAV→文本                                  |
| 意图          | `voice/voiceIntentRouter.ts`                                    | 规则分流 + `implicit_signals`                        |
| 编排          | `voice/voiceOrchestrator.ts`                                    | L3 chunk → 断句 → TTS → 播放                         |
| 播放          | `voice/voicePlaybackController.ts`                              | 代次、native 优先、队列                                  |
| 桥接          | `voice/voiceCompanionBridge.ts`                                 | chat ↔ HUD 事件                                    |
| 入口          | `chat.tsx`                                                      | `dispatchVoiceUtterance`, `doActualSend`, PTT 去重 |
| Rust 唤醒     | `stt/wake_pipeline.rs`                                          | KWS、截句、SV、STT 注入                                 |
| Rust PTT    | `stt/commands.rs`, `stt/manager.rs`, `stt/stream_stt_client.rs` | start/stop_ptt_capture + 流式 STT                  |
| Rust SV 客户端 | `stt/speaker_verification.rs`                                   | 调 JVS verify / filter                            |
| JVS         | `voice_server/main.py` + `services/`*                           | STT/TTS/SV（HTTP + WS + STT Raw TCP）              |
| 认主          | `WakeModePanel.tsx` + `enroll_owner_voiceprint`                 | UI + 写 owner_voiceprint.json                     |


---

## 5. JVS 扩展：STT + TTS + SV

`/health` 示例字段：

```json
{
  "stt_ready": true,
  "tts_ready": true,
  "sv_ready": true,
  "sv_model": "cam++-modelscope-local",
  "tts_speed": 1.25,
  "stt_tcp_port": 18983,
  "model_root": ".../data/models/voice"
}
```


| 环境变量                        | 默认                                       | 说明                   |
| --------------------------- | ---------------------------------------- | -------------------- |
| `JACHIN_VOICE_MODEL_ROOT`   | `data/models/voice`                      | 模型根目录                |
| `JACHIN_VOICE_STT_TCP_PORT` | `18983`                                  | STT 裸 TCP 端口（本机 IPC） |
| `JACHIN_VOICE_SV_DIR`       | `sv/speech_campplus_sv_zh-cn_16k-common` | CAM++ 目录             |
| `JACHIN_VOICE_TTS_SPEED`    | **1.25**                                 | MOSS ONNX 合成语速（0.8～1.5） |
| `JACHIN_VOICE_TTS_VOICE`    | `zm_053`                                 | 默认音色                 |


SV 失败时 `sv_service` 回退谱统计 MVP；`/health` 的 `sv_load_error` 可诊断。

---

## 6. 声纹层（WAKE Profile 附加，非第四条管道）

不替代 Voice Core，而是挂在 **Rust 截句前后**：

```text
KWS 命中 → [S1 verify 唤醒切片] → WAKE_ACK
VAD 截句完成 → [S2 filter_owner_track] → STT(主人轨 WAV)
```

- 配置：`UserSettings.speaker_verification_*`、`speaker_owner_track_enabled`
- Profile：`~/.jachin/voice/owner_voiceprint.json`（认主 UI 生成）
- 联调：`scripts/test_speaker_verification.py`

详见 `VOICE_SPEAKER_VERIFICATION_PROPOSAL.md`；已知限制见 `VOICE_SPEAKER_VERIFICATION_FILTER_FAILURE_ANALYSIS.md`。

---

## 7. 意图路由层（进 L3 前）

`dispatchVoiceUtterance` 在 `doActualSend` 前调用 `voiceIntentRouter`：

- 输出 `VoiceDispatcherDecision`（tier、intent、interrupt_verdict、execution_lane 等）
- 写入 `implicit_signals` 供 L3 消费
- 原始 STT 保留在 `voice_raw_stt_text`；路由后文本在 `voice_routed_text`

详见 `VOICE_INTENT_ROUTING_AND_TASK_ORCHESTRATION.md`。

---

## 8. 分期实施状态

### Phase U1 — 统一「字」链路 ✅

- [x] 大窗 PTT/VAD：STT 只走 JVS，发送只走 `doActualSend`（`voiceCore.ts`）
- [x] L2 `voiceProcess` / `voiceChat` 在 Tauri 路径 deprecated
- [x] 错误提示统一 JVS + L3 端口

### Phase U2 — 统一「声」链路 ✅

- [x] 大窗 TTS：JVS + `voicePlaybackController` + `voiceOrchestrator`
- [x] Profile 控制 `maxSpeakSentences`（WAKE=3，CHAT 随 ttsEnabled）
- [x] 断句粒度下沉：逗号/顿号也触发 TTS（`sentenceBuffer.ts`）

### Phase U3 — 统一采音 ✅

- [x] 大窗 PTT：Rust `start_ptt_capture` / `stop_ptt_capture`
- [x] 单麦互斥 `SttState::capture_busy()`；PTT 前停 wake listener
- [x] PTT 双通道去重（invoke 返回 + STT_AUDIO_READY 事件）
- [x] PTT 流式 STT：Raw TCP（18983）优先，WebSocket 回退；结束携带 `recognized_text`

### Phase U4 — Profile 配置与文档 🔄 部分

- [x] `voiceProfiles.ts` 三 Profile 常量
- [x] WakeModePanel：唤醒模式 + 声纹开关 + 一键认主
- [ ] 设置页：大窗「语音朗读」与陪伴 TTS **完全分拆**（仍共用 `ttsEnabled`）
- [x] 更新 `VOICE_MODULE_HUMAN_GUIDE.md`、本文档

### Phase U5 — 声纹 S1/S2 ✅ MVP

- [x] JVS SV API（extract / verify / label_windows / filter_owner_track）
- [x] Rust `speaker_verification.rs` + `wake_pipeline` 集成
- [x] 认主 UI + `owner_voiceprint.json`
- [x] CAM++ 本地 ModelScope 加载（需 `campplus_cn_common.bin` + torchaudio 版本匹配）
- [ ] S3 Diarization（叠音场景，未做）

### Phase U6 — 意图路由 ✅ 规则版

- [x] `voiceIntentRouter.ts` + `dispatchVoiceUtterance`
- [ ] Lite 模型 / L3 gateway 路由（文档目标态）
- [x] JVS TTS 取消可抢占：`/v1/session/cancel` 实际驱动会话取消

---

## 9. 验收清单（回归）


| 场景            | 期望                                                   |
| ------------- | ---------------------------------------------------- |
| L2 关、L3+JVS 开 | 大窗 PTT 与 Orb 唤醒均可问答                                  |
| 大窗 PTT 松开     | 无 webm；STT 与唤醒同源                                     |
| PTT 一次录音      | **只产生一条** user 消息（去重生效）                              |
| PTT 流式 STT    | JVS `stt_tcp_port` 可见；PTT 优先走 Raw TCP，失败自动回退 WS      |
| 陪伴唤醒          | 有滴声/我在；HUD 同步 user/assistant                         |
| TTS           | `/health` 显示 `tts_speed`；逗号/顿号可提前开口播报                |
| 打断取消          | `cancelJvsSession` 后在进行中的合成可中止（服务端可返回 409 cancelled） |
| 声纹认主后         | `/health` 的 `sv_model` 为 cam++（非 mvp-spectral）       |
| 旁人插话（S2 开）    | 主人轨 STT 不含旁人句（依赖 CAM++ + 阈值，见分析文档）                   |


---

## 10. 不需要做的事（仍然成立）

- ❌ 不要让 L3 听麦克风  
- ❌ 不要在大窗 PTT 加「我在」  
- ❌ 不要恢复 L2 voice 作为桌面隐形兜底  
- ❌ 不要为 PTT 单独维护第二套 STT/TTS 管道

---

## 11. 已知问题与文档索引


| 问题             | 文档                                                      |
| -------------- | ------------------------------------------------------- |
| TTS 无播放        | `VOICE_COMPANION_TTS_NO_PLAYBACK_DEEP_ANALYSIS.md`      |
| 主人轨过滤不准        | `VOICE_SPEAKER_VERIFICATION_FILTER_FAILURE_ANALYSIS.md` |
| 打断后接话          | `VOICE_BARGE_IN_AND_WAKE_ACK.md`                        |
| HUD/Chat IO 重复 | `HUD_IO_DUPLICATION_QUICK_ANALYSIS.md`                  |


---

## 12. 一句话（2026-06）

**统一 Voice Core 已上线：Rust 采音、JVS 听说识、规则意图路由、L3 想、Orchestrator 念；唤醒与 PTT 只是 Profile 壳不同。当前 PTT 已流式 STT（Raw TCP 优先），TTS 已逗号级触发，打断可抢占取消。**