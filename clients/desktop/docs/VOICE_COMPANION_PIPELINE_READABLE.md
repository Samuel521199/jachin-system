# 语音陪伴态执行流程说明

这份文档用尽量接近人话的方式解释：用户从按下语音按钮开始，到 Jachin 说出语音回复，中间到底经过了哪些层、每层负责什么、意图路由怎么判断、哪些模型参与工作。

当前这套系统不是一个简单的“语音转文字 -> 大模型 -> 文字转语音”。它更像一条分层流水线：

```text
用户声音
  -> 录音 / 声纹可选过滤
  -> STT 语音识别
  -> STT 文本修正与热词风险判断
  -> 前端语音意图路由
  -> L3 WebSocket / HTTP 发送
  -> L3 快路由 / 直连模型 / 完整 Agent
  -> 前端流式接收文字
  -> 分句、清洗、去重
  -> JVS TTS 合成
  -> 播放队列
  -> 系统语音输出
```

## 1. 核心进程和端口

当前语音陪伴态主要有三类运行单元。

### 桌面前端

位置大致在：

```text
clients/desktop/src/chat.tsx
clients/desktop/src/voice/
```

它负责：

- 接收语音按钮、HUD、Orb、模拟脚本输入。
- 调用本地 JVS 做 STT。
- 做前端意图路由。
- 把路由结果包装成 `implicit_signals` 发给 L3。
- 接收 L3 的流式文字。
- 把文字拆成适合朗读的句子。
- 调 JVS TTS 合成语音。
- 管理播放队列和打断。

### JVS 语音服务

位置：

```text
voice_server/main.py
voice_server/services/stt_service.py
voice_server/services/tts_service.py
voice_server/services/sv_service.py
```

默认端口：

```text
http://127.0.0.1:18982
```

它负责本地音频模型：

- `/v1/stt/transcribe`：语音识别。
- `/v1/tts/synthesize`：语音合成。
- `/v1/sv/filter_owner_track`：主人声纹轨道过滤。
- `/v1/models/audio/warm`：预热 STT / TTS / SV。

### L3 智能层

位置：

```text
l3_node/ws_server.py
l3_node/agent_core.py
```

默认 WebSocket：

```text
ws://127.0.0.1:18981
```

它负责：

- 判断这一轮是否可以走语音 fast lane。
- 调用远端或本地 LLM。
- 决定是否跳过完整记忆、检索、工具、任务链路。
- 对任务类请求进入完整 agent / 工具 / 任务调度。
- 把回复流式回传给前端。

## 2. 从用户说话到 STT

用户按语音按钮后，前端会先拿到一段 WAV base64。

入口在 `chat.tsx` 的 `submitVoiceUtterance`。

大概流程是：

```text
收到 wavBase64
  -> 开启 voice_chat trace
  -> 判断是否处于陪伴态 UI
  -> 可选执行 owner-track 声纹过滤
  -> 调用 transcribeWavBase64Detailed
  -> JVS /v1/stt/transcribe
  -> 返回最终文本
```

### 声纹过滤不是每次都强制跑

陪伴态里有一个快速模式：

- 如果环境默认认为比较安静，并且声纹严格模式没开，可以跳过主人轨过滤。
- 如果设置要求严格，才会调用 `companion_filter_owner_track_wav`，再走 JVS `/v1/sv/filter_owner_track`。

声纹模型是 CAM++，它做的事情不是识别文字，而是判断这一段声音像不像主人。

它会把原始音频切成窗口，例如：

```text
step=250ms
len=900ms
high=0.38
low=0.25
```

然后输出：

```text
owner 段
other 段
跳过片段
主人轨音频
```

主人轨过滤后的音频才会送 STT。它的目标是减少旁人插话，但副作用是：如果切片边界不自然，可能剪掉词头词尾。

### STT 模型

当前 JVS STT 是：

```text
sherpa-onnx Zipformer zh-en
```

服务端文件：

```text
voice_server/services/stt_service.py
```

它做三件事：

1. 把音频解码并重采样到 16k。
2. 用 Sherpa-ONNX Zipformer 得到原始识别文本。
3. 经过 `VoiceUnderstandingCorrector` 做领域纠错和初步理解。

返回结果里不只有 `text`，还有：

```text
raw_text
corrected_text
confidence
duration_ms
backend
hotword_count
hotword_status
understanding
reply_plan
user_message
```

其中 `reply_plan` 和 `user_message` 用于一种特殊情况：STT 层已经判断“这个语音不能直接执行，需要追问”。

## 3. STT 后的安全检查

前端拿到 STT 结果后，不会马上发给 L3。

它还会做几类检查。

### 空文本检查

如果 STT 没识别出有效中文、英文或数字，会直接报“未能识别语音内容”。

### 热词污染检查

如果 STT 明显被热词带偏，例如识别结果过度贴近某些热词，前端会拦截。

这种情况不会直接执行任务，而是生成一句确认：

```text
我刚才听到的是“xxx”，但这段语音像是被热词影响了。
你可以再说一遍，或者确认这就是你要做的吗？
```

### 追问生成

如果 STT / understanding 判断缺槽，比如用户说“帮我发消息”，但没说发给谁、发什么，前端会进入追问生成路径。

这里的规则层只给出 `ReplyPlan`，真正说给用户的话会交给一个轻量 LLM composer 写成自然语言。

也就是说：

```text
规则层：判断缺什么
LLM composer：把追问说得像人话
```

## 4. 前端意图路由

前端语音路由的单一事实源是：

```text
clients/desktop/src/voice/voiceIntentRouter.ts
```

Python 里的：

```text
scripts/voice_intent_router.py
```

只是为了 benchmark / 脚本测试去调用同一个 TypeScript 路由，不是第二套路由。

### 路由输出什么

路由器输出一个 `VoiceDispatcherDecision`，核心字段有：

```text
tier                 大层级：闲聊 / 短任务 / 长任务
intent_class         更具体的意图类别
execution_lane       执行车道
interrupt_verdict    如果已有任务，判断是查状态、取消、修改、并行还是恢复
router_hints         给 L3 的提示
route_evidence       路由证据
normalized_text      给 L3 的修正文案
```

### 三个 tier

当前有三层：

```text
CHIT_CHAT   闲聊、轻问答、陪伴连接
SHORT_TASK  短任务，适合前台同步处理
LONG_TASK   长任务，适合后台提交
```

### intent_class

常见值：

```text
CHITCHAT       普通闲聊
QUERY_LIGHT    轻问答，例如“今天吃什么”
TASK_SYNC      短任务，例如“打开计算器”
TASK_ASYNC     长任务，例如“把整个目录生成报告”
CONTROL        对已有任务的控制，例如取消、查进度、修改
CLARIFY_REPLY  用户正在回答上一轮追问
AMBIGUOUS      太模糊，需要澄清
```

### execution_lane

这个字段决定后面怎么走：

```text
direct_llm          直接问模型，不进完整任务链
foreground          前台短任务
background_submit   后台长任务提交
background_control  控制已有后台任务
control_local       本地控制
```

## 5. 当前意图路由规则

路由本质是“规则主导 + 给大模型留表达空间”。

也就是说，它不是让大模型从零判断一切，而是先用规则把边界定住：

- 这是不是任务？
- 要不要执行工具？
- 要不要进后台？
- 能不能跳过记忆和检索？
- 能不能直接模板回复？
- 有没有正在运行的任务要控制？

但是具体回复内容，尤其是闲聊和轻问答，仍然由 LLM 来写。

### 5.1 presence_template：只确认“你在不在”

典型输入：

```text
你好
在吗
你在吗
听得到吗
喂
说话
讲讲话
```

路由结果：

```text
tier = CHIT_CHAT
intent_class = CHITCHAT
execution_lane = direct_llm
fast_lane = true
fast_lane_kind = presence_template
allow_template_reply = true
skip_context_retrieval = true
skip_context_sniffer = true
skip_experience_rag = true
skip_gateway_enrich = true
```

这类请求允许 L3 直接用模板回答：

```text
我在。
在呢。
听着呢。
```

它的目标是极快，让用户知道系统活着。

### 5.2 light_query：轻问答，但不能模板敷衍

典型输入：

```text
今天吃什么
你觉得我今天吃什么
我要不要喝咖啡
这个怎么选
```

路由结果：

```text
tier = CHIT_CHAT
intent_class = QUERY_LIGHT
execution_lane = direct_llm
fast_lane = true
fast_lane_kind = light_query
allow_template_reply = false
```

关键点是：

```text
light_query 也走 fast lane，但禁止模板回复。
```

所以“今天吃什么”不能再回答“我在”。它必须进入 direct LLM，让模型回答问题本身。

### 5.3 chat_direct：普通闲聊

典型输入：

```text
陪我聊聊
我今天有点累
谢谢
没事
好吧
```

路由结果通常是：

```text
CHIT_CHAT + direct_llm + fast_lane
```

它会跳过完整上下文检索，但由模型生成自然回复。

### 5.4 SHORT_TASK：短任务

典型输入：

```text
帮我打开计算器
提醒我下午开会
查一下天气
打开 Chrome
```

路由结果：

```text
tier = SHORT_TASK
intent_class = TASK_SYNC
execution_lane = foreground
fast_lane = false
play_task_ack = true
```

前端会先播一个短提示，比如：

```text
我想想。
```

然后把任务交给 L3 的正常链路。

### 5.5 LONG_TASK：长任务

典型输入：

```text
把整个目录生成报告
批量分析这些文件
把所有 md 文档逐个摘要并生成报告
```

路由结果：

```text
tier = LONG_TASK
intent_class = TASK_ASYNC
execution_lane = background_submit
force_background = true
acceptance_round = true
play_task_ack = true
hud_terminal = true
```

前端会尽快给用户一个确认：

```text
收到，我来处理。
```

然后让 L3 / task engine 去处理长任务。

### 5.6 CONTROL：有任务运行时的控制语音

如果当前已经有 active task，用户说：

```text
停一下
取消
做到哪了
进度怎么样
改成这样
继续
```

路由会优先认为这是对已有任务的控制。

可能结果：

```text
interrupt_verdict = ABORT
interrupt_verdict = STATUS
interrupt_verdict = MODIFY
interrupt_verdict = RESUME
interrupt_verdict = PARALLEL
```

这就是“任务还没执行完时用户继续讲话”的主要调配机制。

## 6. 前端如何把路由结果发给 L3

路由完成后，前端不会只发一句文本。

它会把用户原始 STT、修正后文本、路由结果、fast lane 标记、任务上下文一起塞进 `implicit_signals`。

大概包括：

```text
desktop_companion = true
voice_raw_stt_text
voice_asr_raw_text
voice_corrected_text
voice_final_text
voice_routed_text
voice_dispatcher_decision
voice_dispatch_tier
voice_intent_class
voice_dispatch_lane
voice_interrupt_verdict
voice_fast_lane
voice_fast_lane_kind
voice_allow_template_reply
voice_route_evidence
skip_context_retrieval
skip_context_sniffer
skip_experience_rag
skip_gateway_enrich
prefer_direct_llm
force_background
acceptance_round
inject_task_context
inject_light_task_context
light_task_context
target_task_id
task_context_summary
```

这包东西很重要。它告诉 L3：

- 这句话原始 STT 是什么。
- 前端修正后准备让模型看到什么。
- 这是闲聊、轻问答、短任务还是长任务。
- 要不要跳过重链路。
- 能不能用模板。
- 是否有后台任务正在跑。

## 7. L3 收到语音后的路径

L3 WebSocket 在：

```text
l3_node/ws_server.py
```

收到消息后，先做一个判断：

```text
这是不是 voice fast lane？
```

### 7.1 presence_template 的最快路径

如果是 `presence_template`，并且 `allow_template_reply = true`，L3 可以不调大模型，直接返回模板。

这条路径最快：

```text
前端路由
  -> L3 模板选择
  -> chunk
  -> answer
  -> TTS
```

适合“在吗/你好/听得到吗”。

### 7.2 light_query / chat_direct 的 fast lane

如果是 `light_query` 或普通闲聊：

```text
voice_fast_lane = true
allow_template_reply = false
```

L3 会走直连模型：

```text
_voice_fast_lane_messages
  -> engine.generate_response_stream
  -> 1 到 2 句短回复
```

这条路径跳过：

- 完整上下文检索。
- context sniffer。
- gateway enrich。
- experience RAG。
- 工具池加载。
- ReAct 工具循环。

但是它仍然会调用 LLM，所以如果远端模型首 token 卡住，用户还是会觉得慢。

L3 还有一个首 token 超时保护：

```text
JACHIN_VOICE_FAST_LANE_TIMEOUT_SEC 默认约 1.4 秒
```

如果 presence ack 超时，可以兜底“我在。”。

但如果是非 presence 的轻问答，不能兜底“我在”，否则就会答非所问，所以会抛给后续链路或报错。

### 7.3 完整 Agent 路径

如果不是 fast lane，或者 fast lane 失败，就进入：

```text
run_agent
```

完整路径可能包含：

- 会话历史。
- Memory Nexus。
- Intent Gateway。
- output format signals。
- direct_llm_bypass 判断。
- OOD veto。
- DAG / subintent 拆解。
- 工具加载。
- ReAct 循环。
- 本地工具执行。
- 长任务调度。
- 记忆写入。

这条路径能力强，但慢，也更容易出现“用户只是想聊天，系统却像在做项目调度”的感觉。

## 8. L3 里模型如何运作

### 快路由模型调用

语音 fast lane 会构造一个非常短的 system prompt。

它告诉模型：

- 你是 Jachin 的陪伴态语音助手。
- 当前是语音闲聊快路径。
- 直接、自然、温柔。
- 中文短答，1 到 2 句。
- 不要工具。
- 不要展示推理。
- 如果是轻问答，必须回答问题本身，不能只说“我在”。

模型参数大致偏保守：

```text
temperature 约 0.35
max_tokens 约 80
```

可以通过环境变量指定快路由模型：

```text
JACHIN_VOICE_FAST_LANE_MODEL
```

### direct_llm_bypass

在 `agent_core.py` 里，如果判断可以直连模型，会走：

```text
_run_direct_llm_completion
```

如果是语音 fast lane，它会：

- 禁用完整 ReAct。
- 限制输出 tokens。
- 提示不要长篇。
- 对 light_query 额外强调不要答“我在”。
- 尝试关闭模型 thinking。

### 完整 ReAct / 工具路径

任务类请求会进入更完整的 agent。

这时模型不是只“回答”，而是可能：

- 判断要调用什么工具。
- 读取文件。
- 操作窗口。
- 发消息。
- 建任务。
- 写记忆。
- 汇报执行结果。

所以任务类语音天然比闲聊慢。

## 9. 从 L3 文字到 TTS

前端不是等完整回答结束才开始朗读。

它会接收 L3 的 chunk：

```text
l3.chunk
```

然后交给：

```text
voiceOrchestrator.onL3Chunk
```

`voiceOrchestrator` 做几件事：

1. 合并流式 delta。
2. 用 `sentenceBuffer.ts` 按标点拆句。
3. 用 `speakableText.ts` 清理不适合朗读的内容。
4. 去重，避免重复说同一句。
5. 逐句调用 JVS TTS。
6. 放入播放队列。

### 分句规则

硬断句：

```text
。！？.!?
```

软断句：

```text
，,、
```

但软断句要求当前片段至少有一定长度，避免太短就切。

### TTS 清洗

朗读前会去掉：

- Markdown 代码块。
- 行内代码。
- 部分符号。
- emoji。
- 太像过程说明的句子。
- 太像列表步骤但没有结果提示的句子。

原因是语音陪伴态不适合把完整日志、表格、推理链条、代码块念出来。

## 10. TTS 模型

当前 JVS TTS 是 Kokoro ONNX。

核心文件：

```text
voice_server/services/tts_service.py
```

默认配置：

```text
voice = zm_053
speed = 1.25
sample_rate = 24000
model = Kokoro-82M-v1.1-zh-ONNX
```

前端默认值：

```text
clients/desktop/src/voice/voiceDefaults.ts
```

### Kokoro 合成流程

JVS `/v1/tts/synthesize` 收到文本后，大概流程：

```text
文本归一化
  -> 中文前端处理
  -> jieba 分词
  -> pypinyin 取拼音和声调
  -> misaki zh 转 IPA
  -> phoneme 映射到 tokenizer vocab
  -> 选择 voice bin
  -> 根据 token 长度选择 style vector
  -> ONNX 推理
  -> 修剪首尾静音
  -> 输出 WAV
```

这里要注意：TTS 不是“调用一下就完事”。Kokoro 中文链路需要自己处理：

- 数字怎么读。
- 英文怎么混读。
- 中文标点怎么影响停顿。
- 拼音声调怎么保留。
- phoneme 里模型不认识的符号怎么映射。
- voice bin 的 style index 怎么选。
- 首尾静音怎么裁。

所以之前出现“方言感、黏连、英文不读、完成说不清楚”时，本质多半不是播放问题，而是中文前端、phoneme 映射、标点停顿、style vector 选择这些层出了偏差。

### TTS 返回给前端的诊断头

JVS TTS 会在 HTTP header 里带诊断信息：

```text
X-Jachin-Duration-Ms
X-Jachin-Sample-Rate
X-Jachin-TTS-Synth-Ms
X-Jachin-TTS-Attempts
X-Jachin-TTS-Quality
X-Jachin-TTS-Kind
X-Jachin-TTS-Style-Index
X-Jachin-TTS-Style-Mode
X-Jachin-TTS-Raw-Duration-Ms
X-Jachin-TTS-Trim-Leading-Ms
X-Jachin-TTS-Trim-Trailing-Ms
```

前端会把这些写到 `voice_chat.log`，用于判断到底是：

- L3 慢。
- TTS 合成慢。
- 播放队列慢。
- 音频本身太长。
- 首尾静音太长。

## 11. 播放和打断

播放由：

```text
voicePlaybackController
```

负责。

它有一个 generation 机制。

简单说：

```text
每次新语音会话 / 打断 -> generation +1
旧 generation 的 TTS 结果即使晚到，也不该继续播放
```

打断入口：

```text
voiceOrchestrator.bargeIn()
```

它会：

- 停止当前播放。
- 清空播放队列。
- bump generation。
- 调 JVS `/v1/session/cancel` 取消对应 session 的 TTS。
- UI 回到 listening。

这就是用户“旧请求太慢，我又发了新语音”时，系统应该做的事。

## 12. 任务没执行完时用户继续说话

这个场景由两个层共同处理。

### 前端路由层

前端保存 active voice tasks。

如果有任务正在跑，新语音会先被判断是不是控制请求：

```text
取消 / 停止 -> ABORT
进度 / 做到哪了 -> STATUS
改成 / 再加 -> MODIFY
继续 -> RESUME
其他新话题 -> PARALLEL 或 direct_llm
```

这一步的目标是不要把“停一下”误当成普通聊天。

### L3 / task 层

如果路由结果是任务控制，会进入 L3 的后台控制路径。

如果是普通聊天但有 active task，前端可以注入轻量任务上下文：

```text
inject_light_task_context = true
```

这样模型可以知道“后台有个任务”，但不会编造进度。

## 13. 日志怎么看

### voice_chat.log

路径通常是：

```text
C:/Users/Samuel/.jachin/jachin_debug/voice_chat.log
```

这是最重要的端到端语音链路日志。

关键阶段：

```text
turn.begin
stt.audio_ready
sv.owner_track_ptt / sv.owner_track_ptt_fast_bypass
stt.prepare
stt.wav_ready
stt.jvs_ready
stt.jvs_transcribe_request
stt.jvs_transcribe_ok
stt.recognized
l3.send_start
l3.route_decision
l3.ws_send_ok
l3.chunk
l3.answer
tts.orchestrator.start
tts.orchestrator.chunk
tts.orchestrator.request
tts.jvs_fetch_start
tts.jvs_fetch_response
tts.jvs_blob_ok
tts.orchestrator.ok
tts.playback_enqueue
tts.playback_native_start / tts.playback_web_start
tts.playback_native_done / tts.playback_web_ended
turn.end
```

如果文字很久才出来，看：

```text
l3.send_start -> l3.chunk / l3.answer
```

如果文字出来了但很久才说话，看：

```text
tts.orchestrator.request -> tts.jvs_fetch_response -> tts.playback_start
```

如果 STT 慢，看：

```text
stt.audio_ready -> stt.recognized
```

如果声纹慢，看：

```text
sv.owner_track_ptt latencyMs
```

### voice_companion.log

路径通常是：

```text
C:/Users/Samuel/.jachin/jachin_debug/voice_companion.log
```

它更偏 UI / 陪伴态状态流，比如 Orb、HUD、会话、TTS 队列状态。

### terminal_turn 日志

路径通常是：

```text
C:/Users/Samuel/.jachin/jachin_debug/terminal_turn_*.log
```

它更偏 L3 内部 agent 过程，比如 direct_llm_bypass、ReAct、工具调用、异常。

## 14. 当前系统最容易混乱的地方

### 14.1 “快路由”有两层

第一层在前端：

```text
voiceIntentRouter.ts
```

第二层在 L3：

```text
ws_server.py
agent_core.py
```

如果两层理解不一致，就会出现：

- 前端认为是轻问答。
- L3 当成 presence ack。
- 用户问“今天吃什么”，系统答“我在”。

现在已经通过 `voice_fast_lane_kind` 和 `voice_allow_template_reply` 把这件事拉齐：

```text
presence_template 才能模板答“我在”
light_query 禁止模板，必须问模型
```

### 14.2 “规则边界”和“大模型自由度”要分清

现在的路由偏规则主导，但不是规则写死所有回复。

规则负责：

- 分层。
- 安全边界。
- 是否执行。
- 是否后台。
- 是否跳过重链路。
- 是否允许模板。

大模型负责：

- 闲聊怎么说。
- 轻问答怎么回答。
- 追问怎么自然表达。
- 任务结果怎么组织语言。

这是比较合理的方向。问题通常不在“有没有规则”，而在规则是否把某类话误分到错误车道。

### 14.3 TTS 的中文前端很敏感

Kokoro 不是完整中文产品级 TTS 封装，而是 ONNX 模型加一堆本地前端适配。

任何一层出错都可能影响听感：

- 中文标点被处理错，断句会怪。
- phoneme OOV 被丢，字会含混。
- 声调符号丢失，会有方言感。
- style index 不合适，语气会飘。
- 英文混读没归一化，会跳读或乱读。
- 首尾静音裁剪不合适，会黏连或抢拍。

### 14.4 旧请求晚到与新请求抢播放

系统用 generation 和 session cancel 解决这个问题。

但如果某个旧请求在 L3 或 TTS 内部卡很久，仍然可能出现“晚到结果”。这时要看日志确认：

- 旧请求是否被 cancel。
- 旧 TTS 是否仍然进入播放队列。
- generation 是否正确拦截旧音频。

## 15. 一句话总结

现在语音陪伴态可以理解成四层：

```text
感知层：录音、声纹、STT
路由层：判断闲聊、轻问答、短任务、长任务、任务控制
智能层：模板、快模型、direct LLM、完整 Agent / 工具 / 任务
表达层：流式文字、分句、TTS、播放、打断
```

最理想的运行方式是：

- “你好 / 在吗”秒回连接感。
- “今天吃什么”走轻问答，不进任务链，也不模板敷衍。
- “帮我打开计算器”走短任务。
- “把目录生成报告”走后台长任务。
- 任务执行中用户说“停一下 / 进度怎么样 / 改成这样”，走任务控制。
- 用户打断旧语音时，旧 TTS 和旧播放队列被取消，新请求优先。

如果之后要继续优化，建议按日志把问题归因到具体层：

```text
STT 慢或错 -> 看 voice_server STT / hotword / owner-track
路由错 -> 看 voice_dispatch_decision / route_evidence
文字慢 -> 看 L3 fast lane / direct_llm / run_agent
文字对但说话慢 -> 看 TTS synth / playback queue
说得难听 -> 看 Kokoro frontend / phoneme mapping / pause / style
旧话乱插 -> 看 generation / cancel / playback queue
```

