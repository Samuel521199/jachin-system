# 语音陪伴态执行流程说明（按当前实现）

这份文档说明用户说一句话之后，Jachin 语音陪伴态实际经过哪些模块、哪些模型参与，以及系统边界在哪里。本文按 2026-07-09 当前仓库代码和本机配置解析结果修订。

当前架构口径：语音层是输入能力层，不是任务大脑。它只负责把声音尽量可靠地变成文字，并附带声纹、热词、实体纠错等低层元数据。至于用户到底想做什么、是否缺信息、要不要追问、要不要执行工具，都由 L3 决定。

当前本机配置解析结果：

```text
voice_backend = cloud
stt_backend   = cloud
stt_model     = fun-asr-realtime
tts_backend   = cloud
tts_model     = cosyvoice-v3-plus
tts_fast_model= cosyvoice-v3-flash
tts_voice     = longanhuan
local_stt_dir = data/models/voice/stt/sherpa-onnx-zipformer-zh-en-2023-11-22
local_tts_dir = data/models/voice/tts
```

最重要的结论：

- 当前语音识别主路径不是 SenseVoice，也不是 Kokoro。
- 当前 STT 主路径是 JVS 的 cloud backend，模型为 DashScope `fun-asr-realtime`。
- Sherpa-ONNX Zipformer 仍在代码里，但它是 `JACHIN_STT_BACKEND=local` 时的本地 STT fallback。
- Kokoro-ONNX 是本地 TTS fallback，不是 STT；当前默认 TTS 主路径是 DashScope CosyVoice。
- 语音层不做缺槽判断、不发起追问、不判断任务类型、不决定 fast lane。它只把识别文本和底层语音元数据交给 L3。
- L3 是任务理解、追问、确认、工具调用和执行决策的唯一上层大脑。

## 1. 总体链路

```text
用户语音
  -> 桌面端录音 / VAD / 可选声纹过滤
  -> JVS STT：cloud fun-asr-realtime
  -> 热词增强 / 实体名纠错 / STT 元数据整理
  -> 桌面端把识别文本直接作为用户输入发送给 L3
  -> L3 判断意图、缺槽、是否追问、是否执行任务
  -> L3 返回文字流
  -> 桌面端分句、清洗、去重
  -> JVS TTS：cloud CosyVoice，或本地 Kokoro fallback
  -> 播放队列输出语音
```

这条链路里，语音层不再拥有“脑子控制权”。它不能因为自己觉得缺少消息内容就直接追问，也不能因为自己觉得是闲聊就决定走 fast lane。它只上报事实：我听到了什么、原始文本是什么、纠错文本是什么、热词状态如何、是否有声纹过滤。

## 2. 主要进程和端口

### 桌面端

主要位置：

```text
clients/desktop/src/chat.tsx
clients/desktop/src/voice/voiceCore.ts
clients/desktop/src/voice/voiceBridge.ts
```

它负责：

- 采集用户语音，整理成 WAV / base64。
- 可选调用 JVS 声纹过滤。
- 调用 JVS `/v1/stt/transcribe`。
- 接收 STT 返回的 `text`、`raw_text`、`hotword_*`、`understanding` 等低层信息。
- 把最终识别文本直接发给 L3。
- 把 STT 元数据作为 `implicit_signals` 附带给 L3，供 L3 参考。
- 接收 L3 流式回复，拆句后调用 TTS。
- 管理播放队列、打断和取消。

它不负责：

- 判断用户意图。
- 判断缺不缺槽位。
- 生成追问。
- 选择任务执行路径。
- 决定是否跳过上下文检索。
- 决定是否走 voice fast lane。

### JVS 语音服务

主要位置：

```text
voice_server/main.py
voice_server/config.py
voice_server/services/cloud_stt_service.py
voice_server/services/stt_service.py
voice_server/services/cloud_tts_service.py
voice_server/services/tts_service.py
voice_server/services/sv_service.py
voice_server/services/voice_understanding.py
```

默认 HTTP 端口：

```text
http://127.0.0.1:18982
```

主要接口：

```text
GET  /health
POST /v1/stt/transcribe
POST /v1/tts/synthesize
POST /v1/sv/filter_owner_track
POST /v1/models/audio/warm
```

JVS 的实际 STT/TTS backend 由环境变量决定：

```text
JACHIN_VOICE_BACKEND=cloud      # 默认 cloud
JACHIN_STT_BACKEND=cloud|local  # 未设置时继承 voice_backend
JACHIN_TTS_BACKEND=cloud|local  # 未设置时继承 voice_backend
```

### L3 智能层

主要位置：

```text
l3_node/ws_server.py
l3_node/http_server.py
l3_node/agent_core.py
l3_node/os_mission_router.py
l3_node/intent_gateway/
```

常见端口：

```text
ws://127.0.0.1:18981
http://127.0.0.1:18991
```

L3 负责：

- 判断用户意图。
- 判断是否缺信息。
- 生成追问或确认。
- 决定是否调用工具。
- 决定走轻量回复、直连模型、完整 Agent，还是任务路由。
- 处理飞书、浏览器、文件、项目等外部动作。

## 3. 当前 STT 主路径

当前本机解析出来的 STT 是：

```text
stt_backend = cloud
stt_model   = fun-asr-realtime
backend     = dashscope:fun-asr-realtime
```

调用链路：

```text
voiceCore.ts / voiceBridge.ts
  -> JVS POST /v1/stt/transcribe
  -> voice_server/main.py
  -> CloudSttService.transcribe()
  -> CloudSttService._transcribe_fun_asr()
  -> 热词 / vocabulary / 实体名纠错
  -> 返回 STT 文本和元数据
```

`fun-asr-realtime` 属于 cloud backend。它和 SenseVoice 没有关系，也和 Kokoro 没有关系。

### JVS 输出边界

JVS 可以输出：

```text
text
raw_text
confidence
duration_ms
language
backend
hotword_count
hotword_status
hotword_sources
understanding.entity_candidates
```

JVS 不应该输出或驱动：

```text
reply_plan
user_message
clarification_required
selected task
task_candidates
```

如果返回体里为了兼容旧字段仍有 `reply_plan` 或 `user_message`，它们应为空，不参与控制流。当前语音层的 `understanding` 也只保留实体候选和 `voice_layer_scope=stt_only` 这类低层信息。

### Cloud STT 的热词情况

当前 `fun-asr-realtime` 路径会读取 `SttHotwordProvider` 的热词快照，来源包括：

```text
l3_node.voice_entity_correction.export_hotwords()
data/voice/domain_lexicon.json
data/voice/stt_hotwords.json
data/voice/sherpa_hotwords.txt
config/voice_domain_lexicon.json
JACHIN_STT_HOTWORDS
```

在 Fun-ASR 路径里，代码会尝试通过 DashScope vocabulary / raw_input 传入热词。是否真正被云端模型采用，要看返回的 `hotword_status`、`hotword_count`、`hotword_sources` 和 DashScope SDK/API 行为。

### 本地 STT fallback

本地 STT 仍然存在，但当前不是主路径。启用方式：

```text
JACHIN_STT_BACKEND=local
```

本地路径使用：

```text
voice_server/services/stt_service.py
sherpa-onnx-zipformer-zh-en-2023-11-22
```

本地 Sherpa 路径在无热词时使用 `greedy_search`，有热词文件时使用 `modified_beam_search`，并读取：

```text
JACHIN_STT_MAX_ACTIVE_PATHS，默认 4
JACHIN_STT_HOTWORDS_SCORE，默认 4.0
```

准确说法是：Sherpa 是本地 STT fallback，不是当前默认 STT 主路径。

## 4. 实体纠错和领域词

STT 识别完成后，系统会做低层实体名纠错。这是语音输入质量增强，不是意图判断。

主要位置：

```text
l3_node/voice_entity_correction.py
voice_server/services/voice_understanding.py
data/voice/domain_lexicon.json
```

它处理的是实体名，不是全文任意改写。典型实体包括：

```text
app:     Lark / 飞书 / Chrome / VS Code / Codex
contact: Vivian / Neil / Ethan
project: Jachin
```

典型别名：

```text
luck / lock / 拉克 -> Lark
viian / vivan / vivien / 微微安 -> Vivian
背书 -> Lark / 飞书相关候选
一分 / 一份 / 一芬 -> Ethan
charge -> Jachin
```

纠错层原则：

- 可以把明显的专有名词错听纠正回来。
- 不能根据纠错结果决定用户要不要追问。
- 不能把消息正文当成槽位去擅自改写。
- 是否执行外部动作必须由 L3 判断。

## 5. 追问体系

当前正确边界是：追问属于 L3，不属于语音层。

语音层只做：

```text
声音 -> 文本
声音 -> 声纹过滤结果
声音/文本 -> 热词与实体纠错元数据
```

L3 才做：

```text
文本 -> 意图理解
文本 -> 槽位判断
文本 -> 是否需要追问
文本 -> 追问话术生成
文本 -> 是否执行工具
```

例如用户说：

```text
请你帮我打开 Lark，然后发一条测试消息给 Vivian，内容是我今天要睡觉
```

语音层只应该把它转成尽可能准确的文本，并附带：

```text
raw_text
corrected_text
hotword_status
entity_candidates
```

如果 STT 听成：

```text
帮我打开 luck 给 viian 发一条消息内容是我今天要睡觉
```

语音层可以通过热词/实体纠错把 `luck`、`viian` 修成 `Lark`、`Vivian`。但它不应该判断“缺不缺消息内容”，更不应该直接追问。最终任务解析和追问都交给 L3。

## 6. 前端语音发送

当前前端语音发送链路应保持无脑转发：

```text
STT final text
  -> dispatchVoiceUtterance()
  -> doActualSend(text, extraImplicitSignals)
  -> L3
```

`extraImplicitSignals` 只携带事实型元数据，例如：

```text
voice_input_mode = stt_only
voice_asr_raw_text
voice_corrected_text
voice_final_text
voice_stt_confidence
voice_stt_backend
voice_stt_duration_ms
voice_stt_hotword_count
voice_stt_hotword_status
voice_stt_hotword_sources
voice_stt_hotword_dominated
voice_stt_understanding
```

前端语音层不再发送这些控制型信号：

```text
voice_intent_class
voice_dispatch_lane
voice_fast_lane
voice_fast_lane_kind
voice_reply_plan
clarification_pending
skip_context_retrieval
skip_context_sniffer
skip_experience_rag
skip_gateway_enrich
prefer_direct_llm
force_background
acceptance_round
target_task_id
```

这些都应该由 L3 自己决定。

## 7. L3 后续决策

L3 收到语音文本后，应该把它当作普通用户输入，只是多了一些 STT 元数据可参考。

L3 可以根据自己的任务路由、意图网关、OS mission router、pending resolver 等模块决定：

- 是否是闲聊。
- 是否是任务。
- 是否要打开应用。
- 是否要发送飞书消息。
- 是否缺少消息内容、联系人、应用名等信息。
- 是否需要追问。
- 是否需要确认外部动作。
- 是否可以直接执行。

如果 L3 要追问，它可以用自己的 clarification / slot filling / reply composer 机制生成话术。这个追问不能由 JVS 或桌面语音层直接决定。

## 8. TTS 当前路径

当前本机配置解析出来的 TTS 是：

```text
tts_backend    = cloud
tts_model      = cosyvoice-v3-plus
tts_fast_model = cosyvoice-v3-flash
tts_voice      = longanhuan
```

调用链路：

```text
L3 返回文字
  -> 桌面端分句
  -> JVS POST /v1/tts/synthesize
  -> CloudTtsService
  -> DashScope CosyVoice
  -> 返回 WAV
  -> 桌面播放队列
```

Kokoro-ONNX 仍然存在，但它是本地 TTS fallback。启用方式：

```text
JACHIN_TTS_BACKEND=local
```

本地 Kokoro 路径位于：

```text
voice_server/services/tts_service.py
data/models/voice/tts/Kokoro-82M-v1.1-zh-ONNX
```

准确说法是：当前默认 TTS 是 cloud CosyVoice；Kokoro 是 local fallback。

## 9. 声纹过滤

声纹服务位于：

```text
voice_server/services/sv_service.py
```

JVS 暴露接口：

```text
POST /v1/sv/filter_owner_track
```

它用于主人声纹过滤，降低非主人声音、环境音或串音进入 STT 的概率。它不是 ASR 模型，也不负责文字纠错，更不负责意图判断。

## 10. 当前最常见的问题归因

### 专有名词错听

例如：

```text
Lark -> luck / 拉克
Vivian -> viian / 外面
Jachin -> charge / 加勤
Ethan -> 一分
```

这类问题优先看 STT 和实体纠错：

```text
raw_text
corrected_text
understanding.entity_candidates
hotword_status
hotword_sources
```

### 简单问题被错误追问

如果一个完整请求被追问，责任应先归到 L3 决策，而不是语音层。语音层不应该再因为缺槽、热词风险或前端路由判断直接追问。

排查时看：

```text
L3 intent gateway / mission router / pending resolver
L3 收到的 voice_final_text
L3 收到的 voice_asr_raw_text / voice_corrected_text
L3 自己生成的 clarification reason
```

### 热词风险

热词风险可以作为元数据上报，例如 `voice_stt_hotword_dominated=true`。但它不应该在语音层直接阻断用户输入。是否要确认，应由 L3 根据上下文决定。

### 语音慢

主要看这几段耗时：

```text
STT
L3 first token / first useful reply
TTS
Total
```

追问耗时也应该算在 L3，而不是语音层的 ReplyPlan composer。

## 11. 排查入口

常用日志：

```text
C:/Users/Samuel/.jachin/jachin_debug/voice_chat.log
C:/Users/Samuel/.jachin/jachin_debug/voice_companion.log
C:/Users/Samuel/.jachin/jachin_debug/terminal_turn_*.log
```

常用健康检查：

```text
curl http://127.0.0.1:18982/health
```

重点确认字段：

```text
stt_backend
stt_model
tts_backend
tts_model
tts_fast_model
stt_hotword_model
stt_vocabulary_id_configured
sv_model
```

## 12. 不能再混淆的模型名

```text
Fun-ASR / qwen ASR：STT，负责语音识别
Sherpa-ONNX Zipformer：本地 STT fallback
CosyVoice：当前 cloud TTS 主路径
Kokoro-ONNX：本地 TTS fallback
qwen flash：L3 可用的轻量 LLM，不属于语音层必经追问链路
qwen3.5-plus / qwen-max 等：更重的 L3 推理或复杂任务模型
```

Kokoro 不能解释 STT 识别错；STT 识别错要看 Fun-ASR、热词、实体纠错、音频采集和 VAD。

## 13. 当前文档口径

这份文档的口径是：

- 主路径按当前本机配置写：cloud STT `fun-asr-realtime`，cloud TTS `cosyvoice-v3-plus`。
- 本地 Sherpa 和 Kokoro 只作为 fallback 说明。
- 语音层只做 STT、声纹、热词、实体纠错和 TTS，不做意图路由或缺槽追问。
- L3 负责所有任务理解、追问、确认和执行。
- 任何“语音层判断缺槽并追问用户”“前端 voiceIntentRouter 决定语音任务路径”“当前已经使用 SenseVoice”“当前 STT 是 Kokoro”的说法都是错误的。