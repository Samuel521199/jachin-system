# 桌面精灵语音模式（三层架构）

## 模式对比

| 模式 | 技术难度 | 资源消耗 | 误触风险 | 适用场景 |
|------|----------|----------|----------|----------|
| **A. 录音模式 (Push-to-Talk)** | ⭐ 低 | 低（仅传输） | 极低 | 隐私要求高、环境嘈杂、长文本输入 |
| **B. 唤醒模式 (Wake-Up)** | ⭐⭐ 中 | 中（需常驻 KWS） | 低（需匹配唤醒词） | 远场交互、双手被占用时（如做饭、打字） |
| **C. 识别模式 (Continuous)** | ⭐⭐⭐ 高 | 高（需常驻 VAD+STT） | 高（容易插嘴/误执行） | 沉浸式闲聊、头脑风暴、陪伴场景 |

## 实现状态

### A. 录音模式 (Push-to-Talk) — ✅ 已实现

- **位置**：Chat 窗口话筒按钮、ChatPanel 等。
- **行为**：点击开始录音 → 说话 → 点击停止 → 整段音频送后端 STT + LLM + TTS。
- **前端**：`chat.tsx`（`startRecording` / `stopRecording`）、`ChatUI.tsx` 话筒按钮；录音中可选 Web Speech API 流式转写展示（`listeningText`）。
- **后端**：`POST /api/v2/voice/chat`（整段识别）、`/api/v2/voice/recognize`。
- **配置**：主界面 **设置 → 桌面精灵语音模式** 选「A. 录音模式」；持久化在 `settings.json` 的 `sprite_voice_mode: "push_to_talk"`。

### B. 唤醒模式 (Wake-Up) — ⚠️ 部分实现

- **设计**：常驻 KWS 检测用户配置的唤醒词/名字 → 发出 WAKE_UP 事件（payload 含 `wake_word`）→ 再进入录音或对话。
- **已实现**：
  - **Rust**：`src-tauri/src/stt/keyword_spotting.rs`、`stt/mod.rs`；Tauri 命令 `stt_start_wake_listener(wake_word?)`、`stt_stop_wake_listener`、`stt_wake_listener_running`、`stt_emit_wake_up`。支持可配置唤醒词（存于 `UserSettings.wake_word`，默认 "Jachin"）。
  - **主界面「唤醒模式」**：控制台侧栏 **唤醒模式**（`/wake`），可设置唤醒词/名字、启动/停止监听、模拟唤醒；唤醒词持久化到 `settings.json` 的 `wake_word`。
  - 当前为**占位**：后台循环仅轮询运行标志，未接入真实麦克风 + openWakeWord/ONNX；可用「模拟唤醒」测试前端对 WAKE_UP 的响应。
- **未实现**：真实 KWS 引擎（如 openWakeWord、oww-rs）、前端在「B. 唤醒模式」下对 WAKE_UP 事件的响应（如自动打开 Chat/开始录音）。
- **配置**：主界面 **设置 → 桌面精灵语音模式** 选「B. 唤醒模式」；**唤醒模式** 页设置 `wake_word` 并启动监听。持久化 `sprite_voice_mode: "wake_up"`、`wake_word: "Jachin"` 等。

### C. 识别模式 (Continuous) — ❌ 未实现

- **设计**：常驻 VAD + 流式 STT，持续把识别结果送 LLM/执行，易误触。
- **当前**：仅 Chat 内录音时使用 Web Speech API 的 `continuous: true` 做**单次录音内**的流式转写展示，并非整机常驻连续识别。
- **未实现**：常驻 VAD、连续 STT 管道、与意图/执行的衔接、防误触策略。
- **配置**：主界面 **设置 → 桌面精灵语音模式** 选「C. 识别模式」；持久化 `sprite_voice_mode: "continuous"`。选此项后目前仅保存配置，行为待实现。

## 配置在哪里

- **主界面**：
  - **设置** 页（`/preferences`，`SettingsPanel.tsx`）：Chat 流式、桌面精灵语音模式（A/B/C）。
  - **唤醒模式** 页（`/wake`，`WakeModePanel.tsx`）：唤醒词/名字、启动/停止监听、模拟唤醒；仅在此处设置并保存 `wake_word`。
- **持久化**：Tauri 应用数据目录下的 `settings.json`（与 UserSettings 一致，见 `src-tauri/src/config/user_settings.rs`）。  
  - 字段：`chat_stream_via_direct`、`sprite_voice_mode`、`wake_word`（唤醒词/名字，默认 "Jachin"）。
  - Windows：`%LOCALAPPDATA%\com.jachin.desktop\` 或便携目录 `_portable_data/settings.json`。
- **运行时**：Chat 流式在每次发起流式请求时通过 `get_user_settings` 读取；精灵模式由前端根据 `sprite_voice_mode` 决定是否启用唤醒监听；启动监听时从「唤醒模式」页或 `wake_word` 配置读取当前唤醒词。
