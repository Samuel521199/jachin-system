# 语音与 TTS 指南 (Jarvis 协议)

**版本**: v8.0 (The Singularity OS)

---

## 系统概述

- **STT**：语音 → 文本（Whisper / 阿里云）
- **TTS**：文本 → 语音（Kokoro 本地 / Edge TTS / 云端）
- **三种模式**：录音 (Push-to-Talk)、唤醒 (Wake-Up)、连续识别 (Continuous)

## 全息听觉流 (v8.0)

**Hey Jachin** — 复刻钢铁侠 Jarvis 体验：

1. **唤醒词**：Layer 3 集成 Porcupine 或 Snowboy，监听“Hey Jachin”
2. **录音**：唤醒后开始录音，VAD 检测结束（静音 800ms 或满 15 秒）
3. **STT**：Whisper 转文本，发送至 Layer 2 Agent
4. **执行**：ReAct 循环，得到 Final Answer
5. **TTS**：Kokoro/XTTS 播报结果

**实现位置**：`clients/desktop/src-tauri/src/stt/`，参考 `.cursor/rules/ambient-audio.mdc`、`057-voice-endpointing.mdc`

---

## API 端点

| 端点 | 说明 |
|------|------|
| `POST /api/v2/voice/recognize` | 语音识别 |
| `POST /api/v2/voice/synthesize` | 语音合成 |

---

## 安全指令协议

- **命令前缀**：系统操作须带 `"系统指令"` 等触发短语
- **二次确认**：高风险操作须弹窗或口述确认码
- **Alert Mode**：检测到系统指令时 UI 边框变色

---

## TTS Fallback 链

1. Local (Kokoro ONNX)
2. Edge (XTTS)
3. Cloud (Aliyun / CosyVoice)
