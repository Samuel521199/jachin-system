# 语音模式与安全指令协议 (Voice Modes & Safety Command Protocol)

> CTO 需求分析落地文档。基于 Jachin 三层架构，定义三种语音交互模式及「安全指令协议」，防止误触导致系统级误操作。

---

## 1. 可行性概览

| 模式 | 技术难度 | 资源消耗 | 误触风险 | 适用场景 |
|------|----------|----------|----------|----------|
| **A. 录音模式 (Push-to-Talk)** | ⭐ 低 | 低 | 极低 | 隐私高、环境嘈杂、长文本 |
| **B. 唤醒模式 (Wake-Up)** | ⭐⭐ 中 | 中 (常驻 KWS) | 低 | 远场、双手占用 (做饭、打字) |
| **C. 识别模式 (Continuous)** | ⭐⭐⭐ 高 | 高 (VAD+STT 常驻) | **高** | 沉浸式闲聊、头脑风暴、陪伴 |

**结论**：三种模式在 Jachin 架构下均可实现；核心难点是 **模式 C 的意图分流与安全风控**。

---

## 2. 架构分工

- **Layer 3 (Client)**：轻量过滤（KWS、VAD、流式上传）。
- **Layer 2 (Brain)**：深度解析（实时 STT、语义路由、意图分类、执行/闲聊分流）。

### 2.1 模式 B：唤醒模式 (Wake-Up)

- **Layer 3**
  - 轻量级 KWS（如 openWakeWord / Porcupine）。
  - 唤醒词：`"Jachin"` 或 `"贾维斯"`。
  - 逻辑：检测到唤醒词 → 播放「叮」声 → 开启约 5 秒录音窗口 → 音频送 Layer 2。
- **Layer 2**
  - STT 转录 → LLM 意图识别 → 执行或回复。

### 2.2 模式 C：识别/闲聊模式 (Continuous)

- **Layer 3**
  - VAD（如 Silero）：有人说话即推流到 Layer 2。
- **Layer 2**
  - **实时转录**：持续 STT，类字幕输出。
  - **语义路由器 (Semantic Router)**：
    - 判断「这句话是否在对 Jachin 说」。
    - 依据：上下文连续性、称谓、是否疑问等。
  - 输出：`CHAT`（闲聊）或 `COMMAND`（需走安全协议）。

---

## 3. 安全指令协议 (Safety Command Protocol)

原则：**闲聊随意，执行命令必须「带刺」**。

### 规则 1：命令前缀 (The "Sudo" Word)

- 识别模式下：
  - **普通对话**（如「帮我写首诗」）→ 直接 LLM 回复，**禁止**调用 FileTool / ShellTool 等系统能力。
  - **系统级操作**（文件、网络、系统控制）必须带 **触发短语** 才视为指令。
- 触发短语示例：`"系统指令"`、`"Jachin Execute"`。
- 示例：
  - 错误：`"把这个文件删了。"` → 忽略或仅文本回复「我不能直接删除」。
  - 正确：`"系统指令，删除当前文件。"` 或 `"Jachin Execute, delete this file."`

### 规则 2：二次确认 (Confirmation Handshake)

- 对 **高风险操作 (Risk Level: High)**，即使带前缀也不立即执行。
- 流程：
  1. 用户：`"系统指令，格式化 D 盘。"`
  2. Jachin：UI 变红/告警 + TTS：`"⚠️ 检测到高风险操作：格式化 D 盘。请口述确认码 'Alpha-9' 或点击确认以继续。"`
  3. 用户：口述 `"Alpha-9"` 或点击确认。
  4. Jachin：执行并反馈。

### 规则 3：视觉/UI 反馈 (The "Red Mode")

- **闲聊模式**：UI 为蓝/青 (Blue/Cyan)。
- **检测到「系统指令」前缀**：UI 切换为 **红/橙 (Alert Mode)** HUD，明确表示「已进入操作模式，请谨慎说话」。
- 高风险待确认时：可叠加「危险」态（如红色边框 + 确认弹窗）。

---

## 4. 实现步骤 (Cursor 执行计划)

### Step 1：升级 STT 引擎 (Layer 3) ✅ 已实现

- **位置**：`clients/desktop/src-tauri/src/stt/`。
- **已做**：
  - `stt/mod.rs`、`stt/keyword_spotting.rs`：唤醒词检测骨架，后台 Loop 占位（可后续接入 openWakeWord ONNX + cpal）。
  - 事件名 `WAKE_UP`，Tauri 命令：`stt_start_wake_listener`、`stt_stop_wake_listener`、`stt_wake_listener_running`、`stt_emit_wake_up`（测试用）。
- **待扩展**：在 Loop 内集成麦克风采集 + ONNX 推理，检测到唤醒词时 `app.emit(WAKE_UP_EVENT, ...)`。

### Step 2：语义路由器 (Layer 2) ✅ 已实现

- **位置**：`core/voice/intent_router.py`，API：`POST /api/v2/voice/intent`。
- **已做**：
  - `IntentRouter.route(text)` → `RoutedIntent(intent_type, risk_level, stripped_text)`。
  - 命令前缀：`系统指令`、`Jachin Execute`、`Execute` 等；高危词：删除、格式化、关机等 → `risk_level=high`。
  - 无前缀为 `CHAT`；有前缀为 `COMMAND`，并根据内容计算 `low`/`medium`/`high`。
- **后续**：在 Chat/Orchestrator 调用链中，对 `CHAT` 禁止 FileTool/ShellTool（由执行器或 Prompt 约束）。

### Step 3：UI 安全锁 (Risk UI) ✅ 已实现

- **位置**：`chat.tsx`、`components/Chat/HolographicChat.tsx`。
- **已做**：
  - 发送前调用 `routeIntent(text)`；若 `COMMAND` + `risk_level=high` 则弹出二次确认弹窗（口述确认码 Alpha-9 或点击确认）。
  - `riskLevel` 状态：`safe` | `warning` | `danger`；传入 `HolographicChat`，展开/收起态均根据 risk 显示橙/红边框（Alert Mode）。
  - 确认后执行发送，取消则清空待发送并恢复 safe。

---

## 5. 总结

| 维度 | 结论 |
|------|------|
| **可行性** | 三种模式在 Jachin 三层架构下均可实现。 |
| **安全性** | 录音模式最安全；唤醒模式误唤醒可控；识别模式依赖 **前缀强制 + 高危二次确认 + UI 视觉反馈** 三重锁。 |

后续实现时按 **Step 1 → Step 2 → Step 3** 顺序推进，每步可单独验收后再进入下一步。
