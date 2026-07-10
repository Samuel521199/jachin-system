# 陪伴态回复无语音播报 — 深度问题分析

> **状态**：分析稿（仅分析，不改代码）
> **问题定义**：陪伴态（Orb/HUD）有文本回复，但用户听不到语音播报。
> **目标**：给出可执行的根因树、排查路径、日志判读标准和修复优先级。
> **关联文档**：`VOICE_INTENT_ROUTING_AND_TASK_ORCHESTRATION.md`、`VOICE_BARGE_IN_AND_WAKE_ACK.md`、`HUD_IO_DUPLICATION_QUICK_ANALYSIS.md`

---

## 1. 现象边界（先把问题说清）

本问题**不是**"完全没有回复"，而是：

1. 用户在陪伴态说话或输入文字后，AI（L3）正常返回了内容；
2. HUD 或聊天气泡里**能看到文字**；
3. 但**喇叭没有声音**（或偶发有声、偶发无声）。

这说明：**上游链路（语音识别、意图路由、AI 回复）基本是通的**，问题几乎可以确定是在"把文字转成语音再播出来"这段链路上。

---

## 2. 语音播报链路全貌（发生了什么）

先用白话描述一次完整播报经过了哪些步骤：

```
用户说话/输入
    ↓
【chat.tsx】收到 AI 回复的文字 chunk
    ↓
【voiceOrchestrator】把文字流按句子切分
    ↓（每句话独立处理）
【speakSentence】把句子交给 TTS 引擎合成语音
    ↓ 优先 JVS（本地引擎，端口 18982）
    ↓ JVS 失败 → 回退 L2 云端 TTS（zh-CN-XiaoxiaoNeural 等）
    ↓ 两个都失败 → 静默，无语音
    ↓ 合成成功 → 得到 WAV 音频数据
【voicePlaybackController】把音频加入播放队列
    ↓ 优先 native 播放（Rust 调用系统扬声器）
    ↓ native 失败 → 回退 WebView <audio> 播放
    ↓ 播放结束 → 取下一句
```

这是一个**双层容错管道**：合成有 JVS/L2 两档，播放有 native/WebView 两档。但任一层如果"出错但没有被正确感知"，就会导致"文本有、声音无"的现象，而且表面上看起来一切正常。

---

## 3. 什么是"代次"（Generation）— 理解后续分析的关键概念

代次（generation）是一个**整数版本号**，用来标记"当前这轮播报属于哪一次会话"。

每当：
- 一轮新的语音会话开始（`startSession`）
- 用户打断正在播报的内容（`bargeIn`）

代次就会 +1。

所有已经在合成队列里、或已经在播放队列里的音频，都会携带它生成时的代次号。播放器在每次实际播放前都会检查："这段音频的代次是否等于当前代次？"如果不等，就**静默丢弃**。

**白话理解**：代次就像"批次号"。如果你说"帮我查天气"，系统开始合成"好的，今天……"，但还没播完你又说"算了，帮我写邮件"，系统立刻开一个新批次（代次+1），上一批次的音频即使已经合成好了，也全部作废。

正常情况下这个机制是对的，但**时序稍微一乱**，就会导致合成好的音频被误判为"旧批次"而丢弃。

### 代次检查点（共 4 层）

代码里实际有 4 个地方会做代次比对，任何一个不通过都会静默跳过：

| 检查层 | 位置 | 含义 |
|--------|------|------|
| 合成前检查 | `speakSentence` 第一行 | 队列里等待合成的句子，合成前先验代次 |
| 合成后检查 | TTS 返回后，`enqueue` 前 | 合成耗时可能 500ms~1s，期间如果被打断就丢弃 |
| 入队检查 | `enqueue()` 内 | 音频数据入播放队列时再验一次 |
| 出队检查 | `playLoop()` 内 | 真正播放前最后验一次 |

这意味着：**即使合成成功，如果在这 500~1000ms 的合成过程中用户触发了打断，音频会被合成完成后立即丢弃**，用户感知就是"没声音"。

---

## 4. 根因树（按可能性从高到低排列）

---

### A. 高概率：`maxSpeakSentences` 上限截断（容易被忽视）

**白话**：系统默认只播前 3 句，第 4 句起静默丢弃。

#### 现象
- 短回复（1~2 句）正常播，长回复后半段无声；
- 日志中出现 `orchestrator.tts_skip_cap`。

#### 代码证据
`voiceOrchestrator.ts` 默认值：
```typescript
private maxSpeakSentences = 3;  // 默认只播 3 句
```

`speakSentence` 里每次播报前会检查：
```typescript
if (this.spokenSentenceCount >= this.maxSpeakSentences) {
  voiceCompanionDebug("orchestrator.tts_skip_cap", { ... });
  return;  // 静默截断，不抛出任何错误
}
```

**关键问题**：这个上限**不会抛出错误、不会在界面上提示**，用户只会感觉"后面没声了"。

#### 验证方式
输入一个肯定会超过 3 句的内容（如"用 5 句话介绍你自己"），看看第 4、5 句是否静默。

---

### B. 高概率：播放层异常（native 失败 + WebView 回退也失败）

**白话**：系统调用 Windows 系统扬声器失败了，备用的网页播放也没能顶上。

#### 现象
- 日志有 `orchestrator.tts_ok`（合成成功了）
- 但随后出现 `playback.native_fail`
- 再之后出现 `playback.play_fail`（WebView 也失败了）

#### 代码证据
`voicePlaybackController.ts` 播放流程：

```typescript
// 优先 native
if (this.preferNativePlayback) {
  try {
    await invoke("voice_companion_play_wav", { wavBase64 });
    return; // 成功，不继续
  } catch (e) {
    // native 失败 → 不抛错，继续往下走 WebView
  }
}
// 落到这里：用 <audio> 播放
await a.play();
```

**设计缺陷**：`primeAutoplay`（用来解锁浏览器自动播放限制的函数）里有这样一行：

```typescript
if (this.preferNativePlayback || this.autoplayPrimed || this.playing) return;
```

因为 `preferNativePlayback = true`，**`primeAutoplay` 永远不会在 native 模式下运行**。
一旦 native 播放失败回退到 WebView，WebView 的自动播放限制从未被解锁，`audio.play()` 就会被浏览器拦截 → 静音。

#### 典型后果
- 日志看起来一切正常，但实际无声
- 切换系统音频设备后可能复现或消失

---

### C. 高概率：代次竞争导致合成好的音频被自己杀掉

**白话**：AI 刚开始说话，但前端某个状态变化同时触发了"打断"，把刚合成好的音频扔掉了。

#### 现象
- 日志有 `orchestrator.tts_request`，很快出现 `orchestrator.barge_in` / `voice_play_stop`
- 或频繁出现 `playback.enqueue_skip_stale` / `orchestrator.tts_skip_stale`

#### 机制
`bargeIn()` 调用后会执行 `bumpGeneration()`（代次 +1），此时正在进行的合成任务代次就"过期"了。
如果"进入新会话"和"上一轮语音合成返回"几乎同时发生（差几十毫秒），代次就会失配，音频被丢弃。

#### 关联
`VOICE_BARGE_IN_AND_WAKE_ACK.md` 已明确提到：打断与播放在时序上容易互相踩踏，尤其"思考中无声态"最易误判。

---

### D. 中高概率：JVS 可用但不稳定（健康检查通过 ≠ 每次 TTS 都成功）

**白话**：本地语音引擎（JVS）报告自己在线，但实际每次请求合成时可能超时或失败。

#### 现象
- `jvs.tts_fetch_fail` 间歇出现
- 启动后前几轮最明显（模型冷启动，GPU 还没加载完）

#### 关键点
JVS 是跑在本地端口 18982 的语音合成服务。"健康检查通过"只代表它能响应 ping，不代表每次合成请求都一定稳定，尤其：
- 刚启动后的前几次请求（模型正在热加载）
- 大量请求并发时
- 模型推理本身偶发超时

现在已有 JVS 失败 → L2 云端回退机制，但如果 L2 网络也不通（如离线环境），就彻底静音。

---

### E. 中概率：音色路由走了不同的引擎

**白话**：根据配置的音色名字，系统会选择不同的 TTS 引擎，可能绕过了 JVS。

#### 机制
`voiceOrchestrator.ts` 里有这段逻辑：

```typescript
const useMandarinNeuralVoice = /^zh-CN-.*Neural$/i.test((ttsVoice || "").trim());
const blob = useMandarinNeuralVoice
  ? await synthesizeSpeechL2Only(speakable, ttsVoice)  // 直接走 L2 云端
  : await synthesizeByJvs(speakable, ttsVoice, sessionId);  // 走本地 JVS
```

如果 `ttsVoice` 配置为 `zh-CN-XiaoxiaoNeural` 这类格式，**直接绕过 JVS 走 L2 云端**。
这本身不是 bug，但如果：
- 你以为在用 JVS，日志里却没有 `jvs.*` 相关条目；
- 或者 L2 网络不可达；

就会产生意料之外的静音。

---

### F. 中概率：文本被过滤为空（内容全是符号/代码）

**白话**：AI 回复的内容以代码块、表格、特殊符号为主，系统认为"没有可以朗读的句子"。

#### 现象
- 日志中 `orchestrator.tts_skip_unspeakable` 频率高

#### 机制
`prepareSentenceForTts` 会过滤 Markdown 语法、emoji、代码块、纯符号段等。
如果某轮 AI 输出主要是代码或表格，可能没有任何一句话能被合成，整轮静音。

---

### G. 低概率：音频设备/系统级静音

**白话**：业务层一切正常，但系统声音没出来（蓝牙切换、系统静音、默认设备变更）。

#### 现象
- 全链路日志几乎都是"成功"，但无声
- 切换音频输出设备后恢复

---

## 5. 为什么"看起来一切正常却没声音"

因为整条链路是**异步多段流水线**，每一段都有自己的成功/失败标准，但彼此不强依赖：

| 阶段 | 成功 | 但不代表 |
|------|------|---------|
| L3 返回文字 | ✓ | TTS 合成成功 |
| TTS 合成成功 | ✓ | 音频没被代次机制丢弃 |
| 音频入队 | ✓ | 播放器实际播出了声音 |
| 播放器播放开始 | ✓ | 没被后续打断立即停止 |
| 系统说"播放成功" | ✓ | 用户真的听到了声音 |

每个阶段的失败**都是静默的**（不会弹出错误，不会在界面上提示），所以从用户角度只能感知到最终的"没声音"，而看不出卡在了哪一段。

---

## 6. 建议排查顺序（不改代码即可执行）

> 按此顺序排查，10 分钟内可定位 80% 的场景。

### 步骤一：找日志文件

**主日志**：`%USERPROFILE%\.jachin\jachin_debug\voice_companion.log`
**辅助日志**：`%USERPROFILE%\.jachin\jachin_debug\voice_chat.log`

### 步骤二：看一次完整播报是否有以下全部关键词

一次正常播报，应该能在日志里按顺序找到：

```
chat.companion_send_start       ← 用户发送了内容，开始走陪伴态路径
orchestrator.start_session      ← 新会话开始
orchestrator.tts_request        ← 某句话开始请求合成
jvs.tts_fetch_ok                ← JVS 合成成功（或 orchestrator.tts_fallback_l2_ok）
playback.enqueue                ← 音频加入播放队列
playback.native_start           ← 开始调用系统扬声器播放
playback.native_ok              ← 系统扬声器播放完成（或 playback.play_ended）
orchestrator.stream_idle        ← 本轮播报结束，回到空闲
```

### 步骤三：根据"断在哪"定位根因

| 缺失了哪个日志 | 说明卡在哪里 | 对应根因 |
|----------------|-------------|---------|
| 没有 `companion_send_start` | 没走陪伴态路径 | chat.tsx 路由问题 |
| 有 `tts_request`，没有 `jvs.tts_fetch_ok` | JVS 合成失败 | 根因 D |
| 没有 `tts_request`，有 `tts_skip_cap` | 超过句子上限 | 根因 A（最容易忽视！） |
| 没有 `tts_request`，有 `tts_skip_unspeakable` | 文本被过滤 | 根因 F |
| 有 `tts_ok`，没有 `playback.enqueue` | 代次过期，音频入队前被丢弃 | 根因 C |
| 有 `enqueue`，没有 `native_ok`/`play_ended` | 播放层失败 | 根因 B |
| 有 `native_ok`，但紧接 `barge_in` | 被打断 | 根因 C（时序竞争） |

### 步骤四：辅助验证

用 `voice_chat.log` 核实是否存在同轮**超时、run 切换、barge-in 触发**，避免串日志误判。

---

## 7. 复现矩阵（建议测试这 4 组）

目的是通过不同场景缩小根因范围。

| 测试组 | 输入内容 | 重点观察 | 验证根因 |
|--------|---------|---------|---------|
| ① 静态短句 | "你好" | 是否每次都稳定播 | 基线验证 |
| ② 强制多句 | "用 5 句话介绍你自己" | 第 4、5 句是否静默 | 根因 A（句数上限） |
| ③ 并发干扰 | 刚开始说话就立刻再点语音 | 是否频繁 `voice_play_stop` | 根因 C（代次竞争） |
| ④ 设备切换 | 切换系统默认音频设备后重试 | 是否与设备绑定 | 根因 B / G |

每轮测试记录 `trace_id` + `run_id`，避免多轮日志混在一起误判。

---

## 8. 与现有文档的一致性结论

与 `VOICE_BARGE_IN_AND_WAKE_ACK.md`、`HUD_IO_DUPLICATION_QUICK_ANALYSIS.md` 一致的关键结论：

1. 陪伴态问题本质是**编排时序 + 播放容错**问题，不是单一按钮 bug；
2. 打断链路和播放链路高度耦合，必须一起看；
3. 需要"单轮次可观测性"才能稳定复盘。

---

## 9. 修复优先级建议（仅方案，不改代码）

### P0（必须先做）

1. **建立"每轮播报闭环指标"**：从 `request → synth_ok → enqueue → play_ok → ended`，任意环节断链就打一条明确的失败日志（而非只记录各自的 warning）。
2. **明确 native/web 回退是否命中**，在日志中输出唯一结论字段，例如 `play_path=native|web|none`；目前 native 失败后静默继续，很难从日志里看出发生了回退。
3. **修复 `primeAutoplay` 的 native 模式跳过问题**：native 失败回退 WebView 时，WebView 的自动播放可能从未被解锁，导致双重失败（native 失败 + WebView 被浏览器拦截）。
4. **对 `maxSpeakSentences` 截断增加可观测性提示**：至少在日志里明确写"第 N 句因上限被跳过"，方便排查。

### P1（高收益）

1. **JVS 连续失败短窗熔断**：连续 N 次失败后直接切 L2，不再每次都等 JVS 超时。
2. **对 barge-in 增加最小冷却时间**：避免"刚入队就被停"的代次竞争，可设 150~200ms 保护窗口。
3. **对 `tts_skip_unspeakable` 提供统计告警**：避免被误认为播放故障，影响排查方向。

### P2（体验增强）

1. **陪伴态增加"播报降级提示"**（例如 HUD 小字：`语音已切换到备用引擎`）。
2. **Debug 面板显示当前播放路径和最近错误摘要**（`play_path`、最近 3 次失败原因）。
3. **`maxSpeakSentences` 的值**可以做成配置项，方便不同场景调节（对话式场景用 3，详细介绍场景可以调 6~8）。

---

## 10. 一句话结论

> **"陪伴态有字没声"不是单点故障，而是合成层、播放层与打断时序的复合问题。最可能的故障点是：①句子数量上限静默截断、②native 播放失败后 WebView 回退也被浏览器拦截、③代次竞争导致合成好的音频在毫秒级误判为过期而丢弃。用 `voice_companion.log` 按阶段闭环排查是最快的定位路径。**
