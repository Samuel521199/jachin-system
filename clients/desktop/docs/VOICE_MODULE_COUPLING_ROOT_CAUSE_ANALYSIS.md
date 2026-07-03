# 语音模块「改一处坏一处」— 根因分析与治理方向

> **文档性质**：架构与工程问题分析（非修复 PR）。  
> **背景**：开发者在改陪伴态 UI、TTS 语速/音色、唤醒、PTT、L3 流式播报等功能时，频繁出现「改 UI 布局坏了」「改音色口音变了」「改断句策略播报被拦」等连带回归。  
> **结论先行**：**不是**「所有语音代码都写在一个文件里」的单文件灾难，而是 **「表面模块化 + 多套默认值 + 隐式 fallback + 超大胶水入口」** 叠加造成的 **契约缺失型耦合**。  
> **关联文档**：`VOICE_UNIFIED_PIPELINE_PROPOSAL.md`（目标架构）、`VOICE_MODULE_HUMAN_GUIDE.md`（人话链路）、`COMPANION_UI_REGRESSION_ROOT_CAUSE_ANALYSIS.md`（陪伴 UI 专项）

---

## 一、用户感受到的问题（症状表）

| 你改了什么 | 常常坏什么 | 典型表现 |
|------------|------------|----------|
| 陪伴窗尺寸 / CSS / Orb 布局 | 语音按钮点不了、PTT 无响应 | `OrbWindow` 裁切、拖拽区挡住按钮 |
| `chat.tsx` 里 L3 流式 / 路由逻辑 | 陪伴态不再播报、只出字不出声 | `voiceCompanionActiveRef` 被误清、TTS session 未 arm |
| 设置页 `ttsVoice` / `ttsEnabled` | 口音没变或突然变成 Edge 女声 | 实际走的路径没用你改的配置 |
| `voiceOrchestrator` 断句 / 上限 | 「播一半就静音」 | `maxSpeakSentences` 与逗号级分段交互 |
| JVS 语速 / 模型 | 前端听感不变 | 只改了 config，JVS 进程未重启 |
| 唤醒 / PTT Rust 链路 | HUD 重复消息、STT 双提交 | invoke 返回 + 事件双通道 |
| 旧测试页 / L2 API | 联调结论与桌面主链路不一致 | `voice-test.html`、`VoiceTest.tsx` 仍走 L2 |

这些现象**看起来像**「改 A 文件牵连 B 文件」，但根因往往是：**A 和 B 共享同一套未文档化的隐式契约**，而契约散落在多个文件与 ref 里。

---

## 二、先澄清：不是单文件，但入口是「上帝文件」

### 2.1 语音目录其实已经拆了模块

桌面 `src/voice/` 下已有 **14 个专职文件**（2026-07 统计）：

| 文件 | 行数（约） | 职责 |
|------|------------|------|
| `voiceIntentRouter.ts` | 383 | 进 L3 前规则路由 |
| `voiceOrchestrator.ts` | 319 | L3 chunk → 断句 → TTS → 播放队列 |
| `voicePlaybackController.ts` | 264 | 播放代次、native/WebView、打断 |
| `voiceCore.ts` | 173 | STT 统一入口（JVS HTTP 回退） |
| `voiceBridge.ts` | 139 | JVS HTTP 封装 |
| `sentenceBuffer.ts` | 24 | 断句缓冲 |
| `voiceProfiles.ts` | 39 | wake / chat_ptt / chat_vad Profile |
| 其余 | trace / bridge / session | 日志、HUD 桥、状态 |

Rust 侧也有独立 crate 模块：`stt/`、`jvs/`、`tts/`、`voice_playback.rs`、`wake_ack.rs` 等。

**所以：问题不是「没拆文件」，而是「拆了执行层，没拆决策层与配置层」。**

### 2.2 真正的上帝入口：`chat.tsx`

`clients/desktop/src/chat.tsx` 当前 **约 2479 行**，在同一组件内同时承担：

| 职责类别 | 示例 |
|----------|------|
| 聊天 UI / 会话 / 附件 | messages、sessions、Omni 大窗 |
| L3 / L2 发送与流式 | `doActualSend`、Sensory WS、HTTP fallback |
| 陪伴态模式切换 | `companionMode`、`useCompanionMode`、Rust `invoke` |
| 语音 PTT / VAD | `startRecording`、`submitVoiceUtterance` |
| TTS 会话编排 | `voiceOrchestrator.startSession`、L2 `createAudioQueue` |
| 语音路由 | `dispatchVoiceUtterance`、`voiceIntentRouter` |
| Orb / HUD 同步 | `voiceCompanionBridge`、`emitCompanionL3ToHud` |
| 全局语音 ref | `chatJvsVoiceActiveRef`、`voiceCompanionActiveRef` |

**改语音功能几乎必然 touch `chat.tsx`**；而陪伴 UI 只是其中 `companionMode ? … : …` 的一个分支。  
这与 `COMPANION_UI_REGRESSION_ROOT_CAUSE_ANALYSIS.md` 的结论一致：**功能与 UI 在同一棵 React 树里，没有物理隔离。**

---

## 三、根因 1：多套 TTS 主路径并存（引擎分裂）

文档 SSOT（`VOICE_UNIFIED_PIPELINE_PROPOSAL.md`）声明桌面主路径应统一 **JVS Kokoro**，但代码里至少存在 **四条可发声路径**：

```text
路径 A（新主路径 · 陪伴 / 语音按钮）
  chat.tsx → voiceOrchestrator → synthesizeByJvs → JVS :18982 /v1/tts/synthesize

路径 B（旧主路径 · 键盘聊天 + ttsEnabled）
  chat.tsx doActualSend → createAudioQueue → synthesizeSpeech()
    → 优先 Tauri tts_speak（Rust 本地）
    → 失败则 L2 /api/v2/voice/synthesize（Edge Neural）

路径 C（显式 L2）
  synthesizeSpeechL2Only() → L2 Edge TTS
  （orchestrator 内曾作 fallback，当前为保持音色一致已禁用自动回退）

路径 D（遗留 / 测试 / 其它 UI）
  voiceChat()、VoiceTest.tsx、ChatPanel、AeroPrismSprite、web/voice-test.html
```

### 3.1 同一次 `doActualSend` 内的分叉

`doActualSend`（约 983 行起）根据 ref 决定走哪条路：

```text
forceCompanionVoice = voiceCompanionActiveRef || companionModeRef
  → voiceOrchestrator + JVS（路径 A）

chatJvsVoiceActiveRef && maxSpeakSentences > 0
  → voiceOrchestrator + JVS（路径 A，companionUi=false）

否则 ttsEnabled && audioEl
  → createAudioQueue + synthesizeSpeech（路径 B）
```

**你在设置里改 `ttsVoice`，只影响路径 B 传入的 `ttsVoice`；陪伴态走路径 A 时会被 `FIXED_L3_JVS_VOICE` 覆盖。**

### 3.2 为什么「改音色」像抽奖

| 入口 | 实际引擎 | 默认音色 |
|------|----------|----------|
| 陪伴态 L3 回复 | JVS Kokoro | 硬编码 `zm_053` |
| 大窗点「语音输入」后 L3 | JVS Kokoro | 硬编码 `zm_053` |
| 普通键盘聊天 + 朗读开关 | Tauri / L2 | `spriteStore.ttsVoice` 或 L2 默认 |
| 旧 voiceChat 组件 | L2 | `zh-CN-XiaoxiaoNeural` |

**口音不一致不是模型随机，是路径随机。**

---

## 四、根因 2：配置「看起来可配，实际被下游覆盖」（假配置）

### 4.1 音色默认值至少五处

| 位置 | 默认值 | 是否参与陪伴主路径 |
|------|--------|------------------|
| `voice_server/config.py` | `JACHIN_VOICE_TTS_VOICE` → `zm_053` | ✅ JVS 服务端默认 |
| `spriteStore.ts` | `ttsVoice: "zm_053"` | ⚠️ 仅部分路径 |
| `chat.tsx` | `FIXED_L3_JVS_VOICE = "zm_053"` | ✅ 强制覆盖 |
| `voiceOrchestrator.ts` | `FIXED_L3_JVS_VOICE = "zm_053"` | ✅ startSession / speakSentence 再次固定 |
| `api.ts` synthesizeSpeech | `zh-CN-XiaoxiaoNeural` | ❌ L2 路径 |

`Persona.tsx` 设置页改的是 `spriteStore.ttsVoice`，但 orchestrator 注释写「可传 `ttsVoice`」，实现里却：

- `startSession` 忽略 opts，写死 `FIXED_L3_JVS_VOICE`
- `speakSentence` 再次写死同一常量

**维护者会误以为「传参生效」，实际上 UI 与 orchestrator 各说各话。**

### 4.2 语速同理

- JVS 侧：`JACHIN_VOICE_TTS_SPEED`（当前默认 **1.25**）在 `voice_server/config.py`
- 前端 **无** 对应单一配置源；L2 `synthesizeSpeech` 已改为引用统一 `DEFAULT_KOKORO_TTS_SPEED`（1.25）
- 改 config 后若 JVS 进程未重启，听感不变 → 被误判为「改代码没用」

### 4.3 朗读开关语义分裂

| 开关 | 影响范围 |
|------|----------|
| `spriteStore.ttsEnabled` | 路径 B（键盘聊天 L2 队列）；`resolveChatSpeakSentences` |
| `chatJvsVoiceActiveRef` | 路径 A，`maxSpeakSentences` 强制为 PTT profile 的 3 |
| 注释「语音按钮输入始终朗读（不受全局 ttsEnabled 影响）」 | PTT 提交路径 |

**同一个「要不要读出来」，在不同入口有不同规则。**

---

## 五、根因 3：UI 状态与语音会话状态纠缠

### 5.1 关键 ref 三角

```text
companionModeRef          ← UI：是否处于陪伴缩窗模式
voiceCompanionActiveRef   ← 语音：HUD/Orb 会话是否 active
chatJvsVoiceActiveRef     ← 语音：本次是否由「语音输入按钮」触发
```

三者组合决定：

- 是否 `voiceOrchestrator.startSession`
- `companionUi` 是否为 true（影响 Orb phase、HUD 镜像）
- 是否走 JVS 还是 L2 TTS 队列
- 声纹 S2 `companion_filter_owner_track_wav` 是否启用
- `maxSpeakSentences` 取值

**改陪伴 UI 事件时序（如 HUD 会话抖动）可能误清 `voiceCompanionActiveRef`，直接导致 TTS session 不 arm——表现为「UI 好好的，就是不出声」。**

### 5.2 Orb 状态与语音状态双写

- TS：`voiceSessionStore.setState("listening"|"thinking"|"speaking")`
- Rust：`notifyCompanionVoicePhase`（native bridge）
- HUD：`voiceCompanionBridge` 事件

三处需手动对齐，无单一状态机 SSOT。

---

## 六、根因 4：新旧链路 + 隐式 fallback

### 6.1 桌面 vs L2 voice API

`api.ts` 中 `voiceProcess` / `voiceChat` 已标 `@deprecated`，但：

- `ChatPanel.tsx`、`AeroPrismSprite.tsx` 仍调用 `voiceChat`
- `VoiceTest.tsx` 仍用 L2 默认 Neural 音色
- `synthesizeSpeech` 仍 **优先** `tts_speak`，再 fallback L2

**「退役」在类型/注释层完成，在运行时未隔离。**

### 6.2 STT 同样多路径

| Profile | STT 路径 |
|---------|----------|
| PTT（新） | Rust 流式 Raw TCP / WS → `recognized_text` 优先 |
| PTT / VAD（回退） | `voiceCore.transcribeWavBase64` → JVS HTTP multipart |
| Wake | Rust wake_pipeline → JVS HTTP |

改流式 STT 时若只测 PTT，唤醒路径仍走旧 HTTP，容易出现「PTT 快了、唤醒还是慢」的半拉子体验。

### 6.3 测试页不可信

`web/voice-test.html` 存在编码损坏与旧 L2 假设（Codex 已指出）。  
**不能作为桌面主链路验收标准。**

---

## 七、根因 5：跨语言、跨进程的尺寸与生命周期契约（UI 专项）

语音与陪伴 UI 的耦合不限于 TS：

```text
tauri.conf.json / main.rs     ← 窗口物理尺寸、companion 模式 flag
companionLayout.ts            ← 逻辑高度、MIN_WINDOW、glow 溢出
chat.html / globals.css       ← overflow（为大窗设计）
chat.tsx                      ← companionMode 分支
OrbWindow.tsx / JachinOrb.tsx ← 132px 球 + 外圈 glow
```

Rust 改 `CHAT_COMPANION_H` 而不改 `companionLayout.ts`，或 CSS 改 `overflow:hidden` 而不改 glow 安全边距，都会出现 **「语音按钮还在 DOM 里，但点不到」**。  
详见 `COMPANION_UI_REGRESSION_ROOT_CAUSE_ANALYSIS.md`。

---

## 八、根因 6：策略改动缺少「影响面地图」

近期性能优化（流式 STT、逗号级 TTS、TTS 取消抢占、Raw TCP IPC）本身合理，但暴露同一类问题：

| 改动 | 预期收益 | 非 obvious 影响面 |
|------|----------|-------------------|
| 逗号/顿号断句 | 更早开口 | `maxSpeakSentences` 按「段」计数 → 播一半被 cap |
| 硬句末才计 cap | 放长回复 | 与逗号断句策略需联合设计 |
| 固定 `zm_053` | 音色不漂移 | 设置页音色失效 |
| PTT 流式 STT | 降延迟 | 声纹 S2 过滤后不能复用 stream text |
| `/session/cancel` 抢占 | 打断止损 | 409 与 orchestrator fallback 行为 |

**缺少「策略 × Profile × 引擎」矩阵时，优化会变成回归。**

---

## 八点五、耦合模式归类（为什么会反复犯）

> 理解「症状→根因」还不够；只有命名反复出现的**耦合模式**，才能在 Code Review 时拦截它，而不是每次等回归出现再 patch。

### P1：配置引力（Configuration Gravity）

**定义**：一个配置值被多处消费，每处消费者都觉得「不放心，我再加一个默认」，导致默认值层层叠压。后来者改最上层，底层悄悄覆盖，结果什么都没变。

**在本系统的体现**：`ttsVoice` 在 Persona 设置页 → `spriteStore` → `chat.tsx` → `voiceOrchestrator.startSession` → `voiceOrchestrator.speakSentence` 逐层被覆盖，用户改了最上层却无效。

**通用治理**：配置只能有**一个权威写入点**；其他消费者只读不写，如有本地 override 必须明确 `// override: reason`。

---

### P2：状态渗透（State Leakage）

**定义**：本属于某一层的状态，因「顺手传进来」或「全局 ref 更方便」，逐渐在多层之间流淌，每层都能读写，没有人是「owner」。

**在本系统的体现**：`voiceCompanionActiveRef` 本应是「语音会话是否在陪伴态」的语音层状态，却在 UI 层（`chat.tsx`）被读写，在 Rust 事件回调里被修改，在 intent router 里被判断——任何一层的时序错误都会悄悄把它设错，表现为「UI 好好的，就是不出声」。

**通用治理**：每个 state field 必须有唯一 owner；其他层通过**事件/selector 只读**，禁止跨层 mutation。

---

### P3：协议隐身（Silent Protocol）

**定义**：模块之间存在隐式约定（「我调你时你应该在 X 状态」），但没有任何类型/文档强制，一旦约定被打破，出错点与根因相距很远。

**在本系统的体现**：`doActualSend` 调 `voiceOrchestrator.startSession` 之前，隐含要求 `voiceCompanionActiveRef` 必须已经被 HUD 事件正确设置；但 HUD 事件和 `doActualSend` 的触发没有依赖顺序保证——时序抖动就导致 session 未 arm。

**通用治理**：用 TypeScript 类型、Guard 函数或显式 `armReason` 参数把隐式协议**编码进接口**，而不是靠注释和口传。

---

### P4：幽灵降级（Ghost Fallback）

**定义**：错误处理路径不可见——失败时静默切到另一引擎/模式，既没有日志，UI 也没有变化，使开发者误以为主路径在正常工作。

**在本系统的体现**：JVS 挂掉时，`synthesizeSpeech` 静默切到 L2，用户听到声音，开发者以为 JVS 正常，实际上 JVS 配置改动从未生效。联调永远通过，bug 永远在生产出现。

**通用治理**：降级必须产生可检索日志；关键路径降级应在 UI 可选显示（调试面板或 Orb 颜色）；严禁「无声」切换引擎。

---

### P5：上帝组件引力（God Component Gravity）

**定义**：因为「改 A 功能最快的路是在 chat.tsx 加几行」，每次需求都向同一个文件聚集，导致它越来越大，而它越大、改动它的成本越高，又反过来让人更倾向于「就在这里加几行」。

**在本系统的体现**：`chat.tsx` 已 2479 行，且仍在增长。每次语音新功能（流式 STT、PTT 去重、声纹过滤、陪伴模式切换）都在这里加 ref 和 if-else，使得 UI 改动必须 touch 语音逻辑，语音逻辑改动必须 touch UI。

**通用治理**：新功能**必须**先问「这属于哪一层」；能放进 Hook 或 Store 的，禁止直接放进组件。建立代码评审规则：`chat.tsx` 新增超过 20 行需 owner review。

---

### P6：跨运行时契约漂移（Cross-Runtime Contract Drift）

**定义**：多个运行时（Rust/React/CSS/Python）需要共享同一个常量（如窗口尺寸、端口号、音色 ID），各自硬编码，随着版本演进逐渐产生不一致。

**在本系统的体现**：陪伴窗高度在 `main.rs`（Rust 物理像素）、`companionLayout.ts`（逻辑尺寸）、`globals.css`（overflow 规则）三处各有一份；JVS 端口在 `config.py`、`api.ts`、`voiceBridge.ts`、`.env.example` 四处出现。任一处改动其余未同步即产生 UI 裁切或连接失败。

**通用治理**：跨运行时共享常量必须有**唯一 source of truth**（如 `companionLayout.ts` 已是雏形），其他运行时通过 build 脚本生成或运行时读取，禁止手动多处维护。

---

```text
                    ┌─────────────────────────────────────┐
                    │           chat.tsx（胶水层）          │
                    │  UI + L3 + refs + TTS 分叉 + PTT    │
                    └───────────┬─────────────────────────┘
                                │
          ┌─────────────────────┼─────────────────────┐
          ▼                     ▼                     ▼
   voiceOrchestrator      voiceCore / bridge      spriteStore
   (JVS, 硬编码音色)      (JVS HTTP STT)         (ttsEnabled/Voice)
          │                     │                     │
          ▼                     ▼                     ▼
        JVS :18982          JVS :18983 TCP       api.synthesizeSpeech
                                                    → tts_speak / L2
```

| 维度 | 现状 | 问题类型 |
|------|------|----------|
| 文件结构 | `voice/` 已拆分 | ✅ 执行层模块化 |
| 配置 | 5+ 处默认音色/语速 | ❌ 无 SSOT |
| TTS 引擎 | JVS + Tauri + L2 并存 | ❌ 多主路径 |
| 入口 | `chat.tsx` 2479 行 | ❌ 决策层未模块化 |
| 状态 | 3+ ref + Zustand + Rust flag | ❌ 隐式状态机 |
| 文档 vs 代码 | Unified Pipeline 说统一 JVS | ⚠️ 路径 B/D 仍活跃 |
| 测试 | 旧 HTML/组件 | ❌ 非主链路 |

**一句话：系统「看起来模块化」，但「谁决定用什么音色、什么引擎、播几句」仍散落在 ref 和 if-else 里。**

### 9.1 目标态架构（治理完成后应长什么样）

治理后，系统的层边界应该清晰到「改哪一层只需改哪一层」：

```text
┌──────────────────────────────────────────────────────────────────┐
│  UI 层（chat.tsx / OrbWindow / HUD）                              │
│  ✅ 只表达意图：startCapture() / sendText() / bargeIn()           │
│  ✅ 只订阅状态：voiceSessionStore.mode → Orb 颜色 / 按钮文案      │
│  ❌ 不做引擎判断  ❌ 不持有 ttsVoice  ❌ 不读写 voiceCompanionRef  │
└──────────────────────┬───────────────────────────────────────────┘
                       │ 意图 / 事件
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  语音会话层（useVoiceCapture + useVoiceL3Playback）               │
│  ✅ 决定「何时开始/结束一次语音回合」                              │
│  ✅ 管理 session 生命周期（arm / disarm / barge-in）               │
│  ✅ 调用 VoiceGateway（不关心底层引擎）                            │
│  ❌ 不管 Orb 是什么颜色  ❌ 不管窗口大小                           │
└──────────────────────┬───────────────────────────────────────────┘
                       │ speak / chunk / finish
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│  VoiceGateway（升格后的 voiceOrchestrator）                       │
│  ✅ 唯一读取 VoiceConfigStore（音色/语速/Profile）                 │
│  ✅ 唯一调用 synthesizeByJvs                                       │
│  ✅ 管理断句 / 播放队列 / 取消令牌                                 │
│  ❌ 不知道 UI 处于什么模式  ❌ 不持有任何 React ref                │
└──────────────────────┬───────────────────────────────────────────┘
                       │ HTTP / TCP
                       ▼
┌────────────────────────────────┐   ┌─────────────────────────────┐
│  JVS :18982 / :18983           │   │  VoiceConfigStore           │
│  唯一 TTS 引擎（Kokoro）        │   │  唯一配置 SSOT              │
│  唯一 STT 引擎（SenseVoice）   │   │  与 JVS /health 同步        │
└────────────────────────────────┘   └─────────────────────────────┘
```

**层边界三条铁律**：

1. **UI 层不做引擎判断**：`chat.tsx` 里不能出现 `synthesizeByJvs` / `synthesizeSpeech` 的直接调用。
2. **配置层不散落**：`ttsVoice` / `ttsSpeed` 的默认值在整个 TS 侧只能出现一次（`VoiceConfigStore` 初始值）。
3. **状态机唯一 owner**：`voiceSessionStore.mode` 是 Orb/HUD/Rust 订阅的唯一来源；任何层不能绕过它直接改 Orb 颜色。

---

## 十、分阶段治理路线图（整合版）

> 本节整合内部根因分析与外部重构建议（Gemini 四阶段等），**去粗取精、按依赖排序**。  
> **原则不变**：先契约与止血，再断幽灵链路，再拆 `chat.tsx`，最后收束为单一网关。**不要求 Big Bang 重构。**

### 10.0 总览：四阶段与优先级

| 阶段 | 主题 | 成本 | 收益 | 主要风险 | 前置依赖 |
|------|------|------|------|----------|----------|
| **一** | 止血与统一配置 | 低 | 极高 | 迁移期 `spriteStore` 双写导致暂时性不一致 | 无 |
| **二** | 斩断幽灵链路（Fail Fast） | 低～中 | 高 | Strict Mode 误伤非语音开发流程 | 阶段一（至少音色 SSOT 就位） |
| **三** | 拆解 `chat.tsx` | 中～高 | 极高 | Hook 粒度过粗；ref 三角迁移期状态短暂不一致 | 阶段一、二 |
| **四** | 语音网关层收束 | 中 | 高 | 升格 orchestrator 时 barge-in / 409 处理回归 | 阶段三 |
| **五**（延伸） | 陪伴 UI 物理隔离 | 中 | 高（UI 专项） | 窗口尺寸 SSOT 迁移期 Rust/TS 双版本并行 | 可与阶段三并行，见 §10.6 |

> **风险缓解总原则**：每个阶段应**先建新接口 → 并行运行 → 切流 → 删旧代码**，避免大爆炸式重构导致全链路回归。

```text
阶段一：配置 SSOT + 消灭假参数     ← 改音色/语速立刻可预期
   ↓
阶段二：Legacy 显形 + Fallback 可观测  ← 联调不再被 silent fallback 骗
   ↓
阶段三：chat.tsx 抽 Hook + 状态机    ← 改 UI 少碰语音决策
   ↓
阶段四：VoiceOrchestrator = 唯一出站网关  ← UI 只表达意图，不选引擎
   ↓
阶段五：陪伴 UI 尺寸/overflow SSOT   ← 与 COMPANION_UI 文档对齐
```

---

### 10.1 第一阶段：止血与统一配置（成本低，收益极高）

**目标**：消灭「多重默认值」和「契约欺骗」，让配置可预期。对应原 Phase V0。

#### 10.1.1 建立独立的 `useVoiceConfigStore`（采纳 · 修正）

**采纳**：用 Zustand 建立**语音运行时专用**配置 Store，与 `spriteStore`（Avatar/主题/动画）**分离**。

**修正（不采纳原建议的粗糙形态）**：

| Gemini 原意 | 本方案取舍 |
|-------------|------------|
| Store 里放 `voiceEngine: JVS \| L2` 供业务切换 | ❌ **不设为用户可随意切换的引擎开关**。桌面 SSOT 是 **JVS 唯一主路径**；`effectiveEngine` 只能是只读派生字段（或 debug 专用），避免再现「设置选 JVS、运行时走 L2」 |
| 与 `spriteStore.ttsVoice` 并存 | ⚠️ **迁移**：Persona 设置页改写入 `useVoiceConfigStore`；`spriteStore` 的 `ttsVoice`/`ttsEnabled` 逐步 deprecated 或变为对 Voice Store 的 thin proxy |

**建议 Store 字段（示意）**：

```typescript
// 目标形态（示意，非现有代码）
type VoiceConfigState = {
  // —— 用户可配 ——
  ttsVoice: string;           // Kokoro voice id，如 zm_053
  ttsEnabled: boolean;        // 键盘聊天是否朗读（Profile 可 override）
  ttsSpeed: number;           // 与 JVS /health 对齐，启动时 sync

  // —— 策略（可来自 voiceProfiles，非用户乱改）——
  breakPolicy: "sentence" | "pause";
  maxSpeakSentences: number;

  // —— 只读 / 观测 ——
  jvsHealthy: boolean;
  effectiveEngine: "jvs" | "legacy_l2" | "legacy_tauri";  // 实际最近一次 TTS 用的引擎（审计用）

  setTtsVoice / setTtsEnabled / syncFromJvsHealth / ...
};
```

**启动时**：调用 JVS `/health`，把 `tts_voice`、`tts_speed` 写入 Store 或校验与本地默认一致，避免「改了 config 文件、前端还以为 1.25」。

#### 10.1.2 清理硬编码（采纳）

全局检索并**消除业务路径中的字面量默认值**（允许仅在 Store **唯一** default 常量处保留一次）：

| 字面量 | 当前散落位置 | 动作 |
|--------|--------------|------|
| `zm_053` | `chat.tsx`、`voiceOrchestrator.ts`、`spriteStore.ts`、`voice_server/config.py` | TS 侧只读 Store；Python 侧仍为服务端 fallback default |
| `zh-CN-XiaoxiaoNeural` | `api.ts`、`VoiceTest.tsx`、`ChatPanel` 等 | 移入 `legacy/` 或改为显式 `LEGACY_L2_VOICE` 常量，**禁止**出现在桌面主路径 |

**禁止**：在 `chat.tsx` 第 140 行、`voiceOrchestrator.ts` 内再出现第二份 `FIXED_L3_JVS_VOICE`。

#### 10.1.3 修复 `voiceOrchestrator` 接口契约（采纳 · 强化）

**采纳**：消灭「假配置」——要么严格使用入参，要么从类型上删除。

| 现状 | 目标 |
|------|------|
| `startSession(..., { ttsVoice })` 传入后被内部覆盖 | **二选一**：(A) 删除 opts 中的 `ttsVoice`，统一 `getVoiceConfig().ttsVoice`；(B) 入参仅作 session 级 override，禁止内部再写死 |
| 注释写「可传 voice」但无效 | 更新 JSDoc + TypeScript，**编译期**暴露谎言 |

**经验法则**：若某字段在 100% 调用点传相同值，它不应是参数，应是 Store 字段。

#### 10.1.4 阶段一验收标准

- [ ] 改 Persona 音色后，陪伴态 + 语音按钮 + orchestrator **同一音色**
- [ ] 代码库 TS 主路径 **零** `FIXED_L3_JVS_VOICE` / 散落 `zm_053`
- [ ] `voiceOrchestrator` 公共 API 无「文档说有、实现没有」的字段
- [ ] `/health` 的 `tts_speed` 可在 UI 或 debug 面板看到并与听感一致

---

### 10.2 第二阶段：斩断幽灵链路（Fail Fast，暴露真实问题）

**目标**：让旧路径与 silent fallback **显形**，不再掩盖 JVS 问题。对应原 Phase V1 前半。

#### 10.2.1 切断「静默降级」（采纳 · 修正）

**采纳**：`api.ts` 的 `synthesizeSpeech` 当前 `tts_speak → L2` 静默 fallback，是「改音色像抽奖」「JVS 挂了却还能响」的根源，必须 **可观测**。

**修正（不采纳「dev 一律 throw 阻断渲染」作为唯一手段）**：

| 手段 | 场景 | 说明 |
|------|------|------|
| **强制 loud log** | 所有环境 | 回退时必须打结构化日志/trace，例如 `[VoiceFallback] engine=l2 reason=jvs_unavailable`；写入 `voiceChatTrace` |
| **开发 Strict Mode** | `import.meta.env.DEV` + 显式 env 开关 | 可选 `VITE_VOICE_STRICT_NO_FALLBACK=1` 时，fallback 直接 **reject**，便于联调 JVS；**默认仍允许 fallback**，避免阻塞非语音专项开发 |
| **生产** | 用户可感知降级 | 可保留有限 fallback，但 UI 应可选提示「本地语音服务不可用」；**禁止**无日志切换引擎 |

**不采纳**：不加区分地在所有 `development` 构建里 throw Error 阻断整页——会把 L3/聊天开发与语音专项耦死，误伤面过大。

#### 10.2.2 桌面主路径收口到 JVS（采纳）

| 动作 | 说明 |
|------|------|
| `doActualSend` 路径 B | 从 `createAudioQueue + synthesizeSpeech` **迁到** `voiceOrchestrator` + JVS（与路径 A 合并） |
| 路径 B 删除后 | `synthesizeSpeech` 仅保留给 legacy 目录或明确 `@deprecated` 导出 |

#### 10.2.3 隔离废弃代码（采纳）

| 资产 | 动作 |
|------|------|
| `web/voice-test.html` | 移入 `clients/desktop/legacy/voice/` 或 repo 级 `legacy/voice/`，文件头 `@deprecated` + 指向 `VOICE_MODULE_HUMAN_GUIDE.md` |
| `VoiceTest.tsx`、`ChatPanel` 内 `voiceChat` | 同上，或标记仅 dev 菜单可见 |
| `api.ts` 的 `voiceChat` / `voiceProcess` | 保留导出但 **JSDoc @deprecated**，grep CI 禁止新引用 |

**不采纳**：物理删除旧代码（除非已确认零引用）——先 **隔离 + 禁止新增依赖**，再分 PR 删除。

#### 10.2.4 阶段二验收标准

- [ ] 主路径 TTS **仅 JVS**；故意停 JVS 时，日志/trace 明确可见 fallback 或失败（无 silent switch）
- [ ] 新代码 grep 无 `voiceChat(`、`synthesizeSpeech(`（除 legacy 目录）
- [ ] `voice-test.html` 不在主文档验收清单内

---

### 10.3 第三阶段：拆解 `chat.tsx`（核心手术）

**目标**：UI 状态与语音会话状态机分离。对应原 Phase V2 + V3。

#### 10.3.1 抽离逻辑 Hook（采纳 · 分步）

**采纳**：从 `chat.tsx` 剥离语音硬件与会话逻辑，建议拆为两个 Hook（比单一 `useVoiceSession` 更清晰）：

| Hook | 职责 | 从 chat.tsx 迁出 |
|------|------|------------------|
| `useVoiceCapture` | PTT/VAD、`start_ptt_capture`、 `STT_AUDIO_READY`、`submitVoiceUtterance` | 录音按钮、Rust invoke |
| `useVoiceL3Playback` | `doActualSend` 内 TTS  arm/disarm、orchestrator chunk/finish、cleanup | ~983–1400 行 TTS 相关块 |

**不采纳**：一步把 `chat.tsx` 变成「纯展示组件」——2479 行文件需要 **2～3 个 PR** 渐进抽离，否则 review 与回归不可控。

#### 10.3.2 状态隔离：ref 三角 → Store（采纳 · 修正）

**采纳**：`voiceCompanionActiveRef`、`chatJvsVoiceActiveRef`、`companionModeRef` 不应继续作为「语音决策依据」散落在 UI 文件。

**目标形态**：

```text
useVoiceSessionStore（或扩展现有 voiceSessionStore）
  ├── mode: "idle" | "listening" | "thinking" | "speaking"
  ├── profile: VoiceUxProfile
  ├── companionUi: boolean          // 是否绑定 Orb/HUD
  ├── sessionId: string
  └── armReason: "companion" | "chat_ptt" | "keyboard_tts" | null
```

`chat.tsx` 只：

- 调用 `voiceSession.startCapture()` / `voiceSession.sendText()`（意图）
- 订阅 `voiceSession.mode` 决定 Orb 高亮、按钮文案

**修正**：`companionMode`（窗口缩略 UI 模式）与 `voiceSession.companionUi`（是否走陪伴播报链）**可以相关但不应是同一 ref**。前者来自 Rust `omni-companion-mode`，后者是语音层 derived state。

#### 10.3.3 显式状态机（采纳）

```text
idle → listening → thinking → speaking → idle
         ↑ barge-in ────────────────┘
```

- 单一 Store 驱动：`voiceSessionStore` + `notifyCompanionVoicePhase` + HUD bridge **订阅同一 source**
- 禁止在 `chat.tsx`、`voiceOrchestrator`、`voicePlaybackController` 三处各自 `setState("speaking")`

#### 10.3.4 阶段三验收标准

- [ ] `chat.tsx` 内无 `voiceCompanionActiveRef` / `chatJvsVoiceActiveRef`（或仅剩只读订阅）
- [ ] 改 `OrbWindow` 布局 PR **零** 改动 `doActualSend` TTS 分支
- [ ] 状态迁移图有单元测试或 trace 断言（至少 dev 文档化）

---

### 10.4 第四阶段：语音网关层（Voice Gateway）

**目标**：UI 只表达「要说什么」，不选择引擎。对应原 Phase V4 的「单入口」思想。

#### 10.4.1 不新建重复类，升格现有 `voiceOrchestrator`（采纳 · 修正）

**采纳**：「任何发声走统一网关」。

**修正（不采纳「再包一层 VoiceGateway 类」作为默认方案）**：

- 现有 `voiceOrchestrator` **已经是** L3 流式 TTS 网关雏形；再包一层易成 **双网关**（决策权再次分裂）。
- 推荐：**重构并 rename 职责** 为 `VoiceGateway`（或保留类名、在文档中定义其为唯一出站 API），而不是 parallel 新类。

#### 10.4.2 网关 API 边界（采纳 · 细化）

Gemini 建议「只调 `speak(text)`」——对 **单次播报** 足够；对 **L3 流式** 需保留 session API：

| API | 用途 | 调用方 |
|-----|------|--------|
| `startReplySession(opts)` | 绑定 turnSessionId、profile、maxSpeak | `doActualSend` 开头 |
| `onTextChunk(delta)` | 流式断句 | L3 Sensory hook |
| `finishReplySession()` | flush 尾句、等待播完 | L3 answer 结束 |
| `speak(text)` | 单句直通（唤醒 ack、快捷播报） | Rust bridge / 测试 |
| `bargeIn()` | 打断 | 用户开口 / 快捷键 |

**规则**：网关内部 **唯一** 读取 `useVoiceConfigStore` + `voiceProfiles`；**唯一** 调用 `synthesizeByJvs`（legacy 仅 debug flag）。

UI / `chat.tsx` **禁止** import `synthesizeSpeech`、`synthesizeByJvs`。

#### 10.4.3 Profile × 策略矩阵（保留原 V4 清单）

文档化并纳入 CI / 手动回归：

| Profile | STT | TTS 出站 | 音色来源 | maxSpeak | UI 绑定 |
|---------|-----|----------|----------|----------|---------|
| wake | HTTP/唤醒链 | Gateway → JVS | VoiceConfigStore | 3 | Orb+HUD |
| chat_ptt | 流式优先 | Gateway → JVS | VoiceConfigStore | 3 | 可选 HUD |
| chat_vad | HTTP | Gateway → JVS | VoiceConfigStore | ttsEnabled | 无 |
| keyboard | — | Gateway → JVS | VoiceConfigStore | ttsEnabled | 无 |

#### 10.4.4 阶段四验收标准

- [ ] 全仓库（除 legacy/）仅 **一个模块** export TTS 合成函数给 UI 层
- [ ] 新增 UI 组件发声 ≤ 3 行：`voiceGateway.speak(...)` 或 session API
- [ ] Profile 矩阵 4 行回归用例全部通过

---

### 10.5 对外部建议的取舍摘要（Gemini → 本方案）

| 建议 |  verdict | 说明 |
|------|----------|------|
| 统一 Voice Store | ✅ 采纳 | 独立 `useVoiceConfigStore`，与 spriteStore 分离 |
| Store 内可切换 JVS/L2 引擎 | ❌ 不采纳 | 主路径固定 JVS；legacy 仅观测字段 + 显式 fallback |
| 删除 zm_053 / XiaoxiaoNeural 硬编码 | ✅ 采纳 | 默认值只留 Store / server config 一处 |
| 修 orchestrator 假参数 | ✅ 采纳 | 编译期删除或严格使用 |
| fallback 静默降级 | ❌ 不采纳 | 改为 loud log + 可选 strict mode |
| dev 一律 throw 阻断 | ⚠️ 部分 | 仅 `VITE_VOICE_STRICT_NO_FALLBACK` 时启用 |
| legacy 目录隔离 | ✅ 采纳 | 先隔离再删 |
| 抽 Hook 拆 chat.tsx | ✅ 采纳 | 分 `useVoiceCapture` + `useVoiceL3Playback`，渐进式 |
| chat 变「傻瓜组件」 | ⚠️ 部分 | 终态目标；中期仍保留 L3 消息 UI 同文件 |
| 新建 VoiceGateway 类 | ⚠️ 修正 | 升格 `voiceOrchestrator`，避免双网关 |
| 只暴露 `speak(text)` | ⚠️ 部分 | 流式场景仍需 session API |

---

### 10.6 第五阶段（延伸）：陪伴 UI 物理隔离

与 `COMPANION_UI_REGRESSION_ROOT_CAUSE_ANALYSIS.md` 对齐，可与阶段三 **并行**：

- 陪伴态最小 UI Subtree 独立 bundle / 懒加载
- Rust `main.rs` 与 `companionLayout.ts` 窗口数字 **单一 SSOT**
- `chat.html` overflow 规则按 companion / expanded 分 profile

**语音与 UI 解耦后**，阶段五 PR 应不再修改 TTS/Store 逻辑。

---

## 十一、开发时的「防踩坑」检查清单

### 11.1 功能改动检查（改任何语音 PR 前必问）

- [ ] 我改的是哪条 **TTS 路径**（A/B/C/D）？四条是否都测了？
- [ ] 音色从 **哪一处默认** 读？设置页会不会假生效？
- [ ] 是否 touch 了 `chat.tsx` 的 ref 三角（companion / voiceCompanion / chatJvs）？
- [ ] 断句策略变更是否影响 **maxSpeakSentences 计数语义**？
- [ ] JVS 改动是否需要 **重启 voice_server**？
- [ ] 陪伴 UI 是否涉及 **窗口高度 / overflow**（见 COMPANION UI 文档）？
- [ ] 是否误用 **voice-test.html / VoiceTest** 作为验收？

### 11.2 层边界检查（每条对应一个耦合模式，见 §8.5）

> 对照「**我新增的代码在哪个文件**」，判断它是否越界：

| 我在… | 不应该出现 | 对应模式 |
|-------|------------|----------|
| `chat.tsx` / UI 组件 | `synthesizeByJvs`、`synthesizeSpeech`、`ttsVoice` 字面量、直接改 `voiceOrchestrator` 内部状态 | P1 配置引力 / P5 上帝组件引力 |
| `voiceOrchestrator.ts` | React ref、`companionModeRef`、`document.querySelector`、直接 import `chat.tsx` | P2 状态渗透 |
| 任意新模块 | `zm_053`、`zh-CN-XiaoxiaoNeural` 字面量 | P1 配置引力 |
| `voice/` Hook | `tts_speak` / `createAudioQueue` 直接调用（L2 路径）| P4 幽灵降级 |
| 任意 `.ts` 文件 | `CHAT_COMPANION_H`、`COMPANION_W` 窗口尺寸字面量（应从 `companionLayout.ts` 读）| P6 跨运行时契约漂移 |

### 11.3 配置修改后的生效验证

改语音配置后，**按以下顺序**确认生效，避免「改了没用」：

1. `voice_server/config.py` 改动 → 必须重启 JVS（`uvicorn` 进程），验证 `/health` 响应已更新
2. `spriteStore.ts` / `VoiceConfigStore` 改动 → 验证 Persona 设置页改动后陪伴态**确实**用新音色（不是 fallback 到 `zm_053`）
3. `voiceProfiles.ts` 改动 → 验证 wake / ptt / vad 三种入口各自行为符合 Profile 定义
4. `companionLayout.ts` 窗口尺寸改动 → 同步检查 `main.rs` 对应常量 + CSS `overflow` 规则

---

## 十二、与「单文件论」的对照

| 说法 | 是否准确 |
|------|----------|
| 「语音全写在一个文件里」 | ❌ 不准确；`voice/` + Rust `stt/` 已拆分 |
| 「改 UI 会坏语音」 | ✅ 准确；`chat.tsx` + ref + 窗口契约耦合 |
| 「改音色会坏口音」 | ✅ 准确；多引擎 + 硬编码覆盖 + 假配置 |
| 「应该先把 chat.tsx 拆成 20 个文件」 | ⚠️ 不够；要先有 Contract 和单主路径，否则只是碎文件耦合 |
| 「文档说统一 JVS 就应该统一了」 | ❌ 运行时仍有多条 fallback |

---

## 十三、三条简单铁律（「改一处不坏另一处」的最小条件）

在所有架构治理完成之前，这三条铁律可以**立刻执行**，覆盖 80% 的回归风险：

### 铁律一：配置只有一个写入点

任何与语音相关的默认值（音色、语速、最大句数、JVS 端口），在整个前端 TS 代码里**只能出现一次**作为真正的默认——在 `VoiceConfigStore` 的初始值或 `voiceProfiles.ts` 中。其他地方一律读取，不写入，不重复定义。

**触发条件**：一旦你在非 Store/Profile 文件里写了 `"zm_053"` 或 `18982`，就是在制造下一个回归炸弹。

### 铁律二：UI 文件不调合成函数

`chat.tsx`、`OrbWindow.tsx`、`ChatPanel.tsx` 等 UI 文件里，**不能直接调用** `synthesizeByJvs`、`synthesizeSpeech`、`tts_speak`。它们只能调用 VoiceGateway/orchestrator 的 session API 或 `speak()`。

**触发条件**：如果在一个 React 组件里看到了 TTS 相关 `fetch`/`invoke`，说明这段逻辑下移到错误的层了。

### 铁律三：状态只有一个 setter

`voiceSessionStore.mode` 的状态变更，只能发生在 VoiceGateway 或 `useVoiceCapture`/`useVoiceL3Playback` Hook 内部。Orb、HUD、Rust bridge 只**订阅**，不**写入**。

**触发条件**：如果在 `OrbWindow` 或 `JachinOrb` 里直接设置 `"speaking"` 状态，就是在制造下一个「UI 好好的，就是不出声」的 bug。

---

## 十四、一句话收束

**语音模块的恶心之处，不在于文件少，而在于「决策权」分散：谁发声、用什么嗓子、播到哪停、UI 算不算陪伴态——这些答案散落在 `chat.tsx` 的 ref、`voiceOrchestrator` 的硬编码、`api.ts` 的 fallback、Rust 的窗口 flag 和设置页的 Zustand 里。**

治理的先后顺序应是（详见 **§10 分阶段治理路线图**）：

1. **阶段一**：`useVoiceConfigStore` + 消灭硬编码与假参数（止血）
2. **阶段二**：Legacy 隔离 + Fallback 可观测 + 主路径收口 JVS（Fail Fast）
3. **阶段三**：从 `chat.tsx` 抽 Hook / 状态机，ref 三角迁入 Store（核心手术）
4. **阶段四**：`voiceOrchestrator` 升格为唯一 Voice Gateway（单出站 API）
5. **阶段五**（延伸）：陪伴 UI 物理隔离（与 COMPANION UI 文档并行）

在此之前，任何局部优化（流式 STT、逗号断句、取消抢占）都值得做 **影响面矩阵** 评估，否则很容易变成「改一处、坏另一处」的 Whack-a-Mole。

---

## 附录 A：关键代码锚点（便于 Code Review）

| 主题 | 文件 | 锚点 |
|------|------|------|
| 上帝入口 | `chat.tsx` | ~140 `FIXED_L3_JVS_VOICE`；~983 `doActualSend` TTS 分叉；~1893 `submitVoiceUtterance` |
| 假配置 | `voiceOrchestrator.ts` | ~74 `startSession`；~219 `speakSentence` 固定音色 |
| L2 fallback | `api.ts` | ~1648 `synthesizeSpeech` |
| 设置页 | `spriteStore.ts` / `Persona.tsx` | `ttsEnabled` / `ttsVoice` |
| Profile SSOT | `voiceProfiles.ts` | `VOICE_PROFILES` |
| JVS 默认 | `voice_server/config.py` | `tts_voice` / `tts_speed` |
| 陪伴 UI | `COMPANION_UI_REGRESSION_ROOT_CAUSE_ANALYSIS.md` | 全文 |
| 目标架构 | `VOICE_UNIFIED_PIPELINE_PROPOSAL.md` | Phase U1–U6 |
| 治理路线图 | 本文 §10 | 阶段一～五 |

---

## 附录 B：旧 Phase 编号对照

| 原 §10 编号 | 整合后阶段 |
|-------------|------------|
| Phase V0 Voice Contract | **阶段一** 止血与统一配置 |
| Phase V1 单一 TTS 主路径 | **阶段二** 斩断幽灵链路 |
| Phase V2 Session Controller | **阶段三** 拆解 chat.tsx（部分） |
| Phase V3 显式状态机 | **阶段三** 拆解 chat.tsx（部分） |
| Phase V4 Profile 矩阵 | **阶段四** 语音网关层 |
| Phase V5 UI 物理隔离 | **阶段五** 延伸 |

---

*文档版本：2026-07-01 v3 · 整合 Codex 根因分析 + Gemini 四阶段治理建议（取舍见 §10.5）+ 六大耦合模式归类（§8.5）+ 目标态架构图（§9.1）+ 层边界检查表（§11.2）+ 三条铁律（§13）*
