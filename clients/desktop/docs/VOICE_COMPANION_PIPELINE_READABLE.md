# 语音陪伴态执行流程说明

这份文档用尽量接近人话的方式解释：用户从按下语音按钮开始，到 Jachin 说出语音回复，中间到底经过了哪些层、每层负责什么、意图路由怎么判断、哪些模型参与工作。

当前这套系统不是一个简单的“语音转文字 -> 大模型 -> 文字转语音”。它更像一条分层流水线：

```text
用户声音
  -> 录音 / 声纹可选过滤
  -> STT 语音识别（热词只在云端 DashScope 原生侧辅助识别）
  -> 前端整理最终文本和语音诊断证据
  -> L3 WebSocket / HTTP 发送
  -> L3 认知内核主循环
  -> 主循环理解意图、编排任务、选择工具或直接回答
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
- 展示和记录 STT、声纹、云端诊断、TTS 状态。
- 把最终识别文本、原始识别文本、语音诊断、设备和会话上下文发给 L3。
- 不在前端做任务意图裁决；任务意图、工具选择、任务编排由 L3 主循环负责。
- 接收 L3 的流式文字。
- 把文字拆成适合朗读的句子。
- 调 JVS TTS 合成语音。
- 管理播放队列和打断。

### JVS 语音服务

位置：

```text
voice_server/main.py
voice_server/services/cloud_stt_service.py
voice_server/services/stt_service.py
voice_server/services/cloud_tts_service.py
voice_server/services/tts_service.py
voice_server/services/sv_service.py
```

默认端口：

```text
http://127.0.0.1:18982
```

它负责语音能力的本地入口。当前可以按配置走云端模型，也可以走本地模型或本地兜底：

- `/v1/stt/transcribe`：语音识别。
- `/v1/stt/stream`：流式语音识别 WebSocket。
- raw TCP STT：默认 `tcp://127.0.0.1:18983`，桌面 PTT 优先使用，失败再回退 WebSocket。
- `/v1/tts/synthesize`：语音合成。
- `/v1/tts/stream`：流式语音合成 WebSocket。
- `/v1/sv/filter_owner_track`：主人声纹轨道过滤。
- `/v1/models/audio/warm`：预热 STT / TTS / SV。

当前这台机器上的健康检查会暴露这些关键状态：

```text
stt_backend
stt_model
stt_http_timeout_sec
stt_cloud_soft_timeout_sec
stt_local_fallback_enabled
stt_local_fallback_ready
stt_stream_mode
tts_backend
tts_model
sv_ready
```

这些字段比文档里的静态描述更权威。实际排查时先看 `/health`。

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

- 接收语音最终文本和语音诊断上下文。
- 进入认知内核主循环。
- 在主循环里完成理解、意图路由、任务编排、工具选择、验证和回复组织。
- 根据任务本身决定是否调用模型、工具、MCP、后台任务等能力。
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

当前这套语音服务的 STT 不是单一模型，而是“云端优先 + 本地兜底”：

```text
主路径：DashScope Fun-ASR / fun-asr-realtime
本地兜底：sherpa-onnx Zipformer zh-en
```

服务端文件：

```text
voice_server/services/cloud_stt_service.py
voice_server/services/stt_service.py
voice_server/main.py
```

默认流程是：

1. `/v1/stt/transcribe` 收到完整 WAV。
2. JVS 先启动云端 DashScope STT。
3. 如果云端在 `JACHIN_STT_CLOUD_SOFT_TIMEOUT_SEC` 内没有返回，默认 7 秒，会启动本地 sherpa 兜底。
4. 如果云端先返回有效结果，就使用云端结果。
5. 如果本地兜底先返回，就先把本地结果交给前端，同时后台记录云端是否晚到。

所以日志里看到：

```text
backend = dashscope:fun-asr-realtime
```

表示云端 STT 直接成功。

看到：

```text
backend = sherpa-onnx-zipformer+fallback_from_cloud
```

表示云端没有在软超时内返回，本轮最终使用了本地 sherpa 兜底。

本地 sherpa 会把音频解码、重采样到 16k，再用 Zipformer 得到原始识别文本。云端 DashScope 路径会通过 SDK 调用 Fun-ASR，并返回云端识别文本。两条路径的结果都会被包装成统一的 STT 结果格式。

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

其中 `understanding` 里可能包含：

```text
cloud_diagnostics      云端 STT 诊断事件
stt_orchestration      云端/本地兜底编排事件
stt_fallback           本地兜底是否被使用
```

当前 STT 层本身只做识别、云端原生热词偏置、轻量文本清洗和诊断。本地 sherpa 兜底不使用项目热词。真正的任务意图、缺槽追问、工具选择，交给 L3 认知内核主循环负责；前端只传递最终文本和证据。

### 云端 STT 诊断和本地兜底

现在 JVS 会把云端 STT 的关键阶段写入 `understanding.cloud_diagnostics`，前端再把这些事件展开写进 `voice_chat.log`。

常见阶段包括：

```text
stt.cloud_start
stt.cloud_dns
stt.cloud_connect
stt.cloud_hotwords_snapshot_start
stt.cloud_hotwords_snapshot_done
stt.cloud_vocabulary_sync_start
stt.cloud_vocabulary_sync_done / stt.cloud_vocabulary_sync_skipped / stt.cloud_vocabulary_sync_exception
stt.cloud_upload_start
stt.cloud_sdk_call_done
stt.cloud_result
stt.cloud_exception
```

这些阶段用于回答一个问题：云端 STT 慢，到底慢在哪里。

例如：

```text
cloud_dns 慢       -> DNS 或网络解析慢
cloud_connect 慢   -> 到 DashScope endpoint 的 TCP 连接慢
cloud_vocabulary_sync 慢或异常 -> 热词词表同步慢或失败
cloud_upload_start 后长期没有 cloud_result -> SDK 调用、上传、服务端排队或识别慢
cloud_exception    -> 云端 SDK 或服务端返回异常
```

云端和本地兜底的编排事件会写在 `understanding.stt_orchestration`，前端也会展开成日志阶段：

```text
stt.cloud_wait_start
stt.cloud_soft_timeout
stt.fallback_start
stt.fallback_result
stt.cloud_late_observer_started
stt.cloud_late_result
```

其中：

```text
cloud_soft_timeout
```

表示云端没有在软超时时间内回来，于是 JVS 启动本地 sherpa 兜底。

```text
cloud_late_result
```

表示本地结果已经先返回，但云端后来又回来了。这个事件主要用于排查云端是“慢”还是“彻底失败”。

注意：当前系统为了前台响应速度，fallback 先返回时不会等待云端晚到结果再改写已经发给 L3 的文本。云端晚到结果主要作为诊断证据。

### 真流式 STT 和最终 STT

当前 JVS 有两类 STT 接口：

```text
/v1/stt/transcribe  完整 WAV 识别，用于最终文本
/v1/stt/stream      WebSocket 流式识别
raw TCP STT         本机低开销流式识别，桌面 PTT 优先使用
```

当前云端配置支持真正的 DashScope 实时流式 STT：

```text
stt_stream_mode = cloud_realtime
```

它的含义是：桌面录音过程中，音频块可以通过 raw TCP 或 WebSocket 进入 JVS，JVS 再用 DashScope `Recognition.start()`、`send_audio_frame()`、`stop()` 把音频流发给云端。

如果 health 里看到：

```text
stt_stream_mode = batch_incremental
```

那说明当前没有使用真云端流式，只是在 JVS 内部对累计音频做增量识别。这个模式可以提供预览，但不应该被当成真正降低云端延迟的流式 STT。

无论有没有流式预览，任务执行仍应以最终 STT 为准。前端现在会把 PTT 期间拿到的 `recognized_text` 当作预览证据，最终仍调用 `/v1/stt/transcribe` 得到 finalized 结果。

### STT 热词是什么

热词可以理解成给 STT 的“助听名单”。

普通语音识别模型不一定认识你的工作场景。比如你说：

```text
打开 Lark
给 Vivian 发消息
切到 VS Code
打开 Codex
看一下 Jachin 项目
```

如果没有热词，模型可能把这些词听成：

```text
Lark -> luck / lock / 拉克
Vivian -> vivi / 微微安 / 薇薇安
VS Code -> ws code / w s code
Codex -> code x / 扣得克斯
Jachin -> jacking / 加勤 / 嘉钦
```

热词的作用不是“强行改字”，而是在 STT 解码时告诉模型：

```text
这些词在当前系统里很常见。
如果声音有点像它们，请优先考虑它们。
```

所以热词更像“听写时旁边放了一张常用人名、应用名、项目名清单”，不是后期把所有相似词都粗暴替换。

### 当前有哪些热词

当前热词不是写死在一个地方，而是由 `SttHotwordProvider` 汇总。

服务端位置：

```text
voice_server/services/stt_hotwords.py
```

当前会从这些来源取词：

```text
l3_node.voice_entity_correction.export_hotwords()
data/voice/sherpa_hotwords.txt
data/voice/domain_lexicon.json
data/voice/stt_hotwords.json
config/voice_domain_lexicon.json
环境变量 JACHIN_STT_HOTWORDS
```

热词数量不是固定值，会随 `l3_node.voice_entity_correction.export_hotwords()`、配置文件和环境变量变化。实际值以日志里的 `hotword_count` 或 `/health` 对应状态为准。

```text
hotword_count = 当前这一轮实际加载的热词数量
```

主要分成几类。

#### 应用 / 工具名

```text
Lark
飞书
Chrome
浏览器
VS Code
vscode
Codex
```

它们还带有常见误听别名，例如：

```text
Lark: lark, feishu, flybook, luck, lock, 拉克, 拉
Chrome: chrome, google chrome, clone, 浏览器
VS Code: vs code, vscode, visual studio code, ws code, w s code
Codex: codex, code x, 扣得克斯
```

#### 联系人名字

当前联系人热词里包含：

```text
Vivian
Neil
Ethan
John
Berlith
Gordon
Nathan
Gavin
Daniel
Jade
Hex
Root
Seth
Buck
Cole
Jack Looi
Patrick
Jin
Lin
Samuel
Haku
Victor
Vigo
Mark
Jay
Max
Figo
Fincher
Anna
AnnaAnna
Lucy
Makoto
Musk
Elara
Summer
Jovi
Donnie
David
Rence
KK
Mariz
Tom
Reina
Mada
Stefan
Leslie
Hope
Germaine
```

其中 Vivian、Neil、Ethan 这类常被语音任务用到的人名，会有更多别名。

例如 Vivian：

```text
Vivian
vivian
vivi
viian
vivan
vivien
薇薇安
微微安
V薇
```

#### 项目 / 系统名

```text
Jachin
jachin
jacking
加勤
嘉钦
```

这类热词主要帮助识别项目名、系统名，避免把 Jachin 听成别的普通英文词。

#### 权重

热词都有权重。权重可以理解成“提醒力度”。

例如当前比较重要的词：

```text
Lark    25
Vivian  25
Jachin  20
飞书    20
薇薇安  18
```

普通联系人和常见别名一般是 10 或 20；一些兼容大小写的实验词可能是 4、5、6、8。

权重不是越高越好。太高会让模型过度相信热词，把不相关的声音也听成热词。

### 热词如何辅佐 STT

当前热词只服务云端 DashScope STT。Jachin 不再给本地 sherpa 兜底路径生成热词文件，也不再让本地 sherpa 因热词切换到 `modified_beam_search`。

云端 DashScope 路径大概是：

```text
录音音频
  -> JVS /v1/stt/transcribe
  -> SttHotwordProvider 汇总热词
  -> DashScope vocabulary / raw_input.context
  -> Fun-ASR 云端识别
  -> 得到 raw_text
  -> 返回 text / raw_text / hotword metadata / cloud_diagnostics
```

本地 sherpa 兜底路径大概是：

```text
录音音频
  -> STT 服务收到音频
  -> Sherpa-ONNX Zipformer 固定用 greedy_search 解码
  -> 得到 raw_text
  -> 轻清洗
  -> 返回 text / raw_text，并标记 local_hotwords = disabled
```

更人话一点：

1. 每次识别前，JVS 会拿一份最新热词清单。
2. 如果走云端，JVS 会把热词传给 DashScope vocabulary 或 `raw_input.context`。
3. 如果走本地 sherpa，JVS 不读取 `SttHotwordProvider`，不生成临时 hotwords 文件。
4. 本地 sherpa 固定 `greedy_search`，避免热词把兜底识别吸偏。
5. 最后输出识别文本，并把最终文本交给 L3 主循环。

#### “多个可能听法”是怎么来的

STT 模型听声音时，不是像人一样一次性听出一句确定的话。更接近下面这个过程：

```text
声音特征进模型
  -> 每一小段音频都可能对应多个字 / 拼音 / token
  -> 解码器边听边保留几条候选路径
  -> 每条路径都有一个分数
  -> 分数最高的路径成为最终识别文本
```

比如用户说“打开 Lark”，声音比较糊的时候，模型内部可能同时觉得这些都说得通：

```text
打开 Lark
打开 luck
打开 lock
打开拉克
打开那个
```

本地 sherpa 兜底现在固定走最朴素的 `greedy_search`：当前哪一个 token 分最高，就一路选下去。

```text
本地 sherpa / greedy_search：一路往前猜，不做项目热词偏置
```

云端 DashScope 的热词权重仍然可能影响选择。它的效果更像：

```text
原本：打开 luck  51 分，打开 Lark  49 分 -> 输出 luck
云端热词后：打开 luck 51 分，打开 Lark  49 分 + 热词加成 -> 可能输出 Lark
```

所以热词风险主要来自云端原生热词或历史日志里的旧本地热词行为。当前本地 sherpa 兜底不再参与热词偏置。

#### 最后输出识别文本是怎么做的

云端和本地最终都会被 JVS 规整成同一种返回结构。

如果走本地 sherpa，JVS 从 sherpa stream 里拿到：

```text
stream.result.text
```

如果走云端 DashScope，JVS 从 DashScope `RecognitionResult` 里提取 sentence text。

然后 JVS 会做轻清洗：

```text
去掉多余空白
去掉没有意义的空结果
得到 raw_text
```

当前云端 STT 路径会做轻量 domain terms 替换，例如把一些常见误听词规整成 `Jachin / Codex / Lark`。

当前本地 sherpa 兜底路径不走项目热词，也不走 `VoiceUnderstandingCorrector` 的任务理解链路。它只负责把音频转成尽量朴素的 `raw_text / text`，避免兜底识别阶段再引入额外偏置。

最终 STT 结果会输出：

```text
text
raw_text
confidence
backend
understanding（可能为空，或只包含本地热词禁用等诊断）
```

最终前端通常看到的是：

```text
raw_text       原始 STT 文本
corrected_text 如果某一路径有轻量规整，会显示规整后文本
text           最终交给 L3 主循环的文本
```

#### 实体纠错层具体按什么规则改名

下面这套 `VoiceUnderstandingCorrector` 是历史上的本地后处理/辅助理解模块说明，不是当前本地 sherpa 兜底主链路。当前架构里，更完整的应用、联系人、任务槽位理解主要在 L3 认知内核主循环里完成。

```text
voice_server/services/voice_understanding.py
VoiceUnderstandingCorrector
```

`VoiceUnderstandingCorrector` 做的不是简单的全局替换，而是“实体识别 + 任务理解”。大概分四步。

第一步，加载实体库。

实体库包含应用、联系人、项目：

```text
apps: Lark, Chrome, VS Code, Codex
contacts: Vivian, Neil, Ethan, ...
projects: Jachin
```

每个标准名都有别名，例如：

```text
Lark: lark, feishu, flybook, luck, lock, 拉克
Vivian: vivian, vivi, 薇薇安, 微微安
VS Code: vs code, vscode, visual studio code, ws code
Jachin: jachin, jacking, 加勤, 嘉钦
```

第二步，在整句里扫描可能的实体。

它会用几种相似方式找候选：

```text
完全相同：vivian == Vivian
子串包含：google chrome 里包含 chrome
字符相似：vivien 和 Vivian 很像
拼音相似：薇薇安 和 Vivian 对应同一个联系人
发音折叠：一些 v/w、ph/f、ck/k 之类的近似会放宽
```

每个候选会有分数和强度：

```text
strong  很确定
medium  有点像，可以在有上下文时使用
weak    太弱，不能直接执行
```

第三步，看这句话有没有动作意图。

系统会检查动作词，例如：

```text
打开 / 启动 / 切到 / open
找到 / 搜索 / 查找 / find
发送 / 发消息 / message / send
```

然后把动作和实体组合起来。

比如：

```text
打开 + Lark     -> open_app
给 + Vivian + 发消息 -> send_message
找到 + Neil     -> find_contact
Jachin + 项目   -> open_project 或相关项目意图
```

第四步，决定能不能直接规整成标准名字。

规则大概是：

```text
如果实体很强，而且动作也明确 -> 可以把别名换成标准名
如果实体有点像，但不够确定 -> 需要确认或追问
如果是发消息，但缺联系人或消息正文 -> 不直接执行，生成追问
如果整句话不像任务 -> 不强行改，尽量保留原文本
```

举例：

```text
原始 STT: 打开拉克
实体候选: 拉克 -> Lark，强匹配
动作: 打开
结果: corrected_text = 打开 Lark
```

```text
原始 STT: 给薇薇安发消息
实体候选: 薇薇安 -> Vivian，强匹配
动作: 发消息
缺失: 消息正文
结果: 不直接执行，进入追问：要发的内容是什么？
```

```text
原始 STT: 找一下 vivien
实体候选: vivien -> Vivian，中高相似
动作: 找一下
结果: 可能规整成 Vivian；如果分数不够，会要求确认
```

```text
原始 STT: 我觉得 lark 这个词挺怪
虽然出现 Lark，但不像任务动作
结果: 不应该直接变成“打开 Lark”或执行任务
```

如果某个历史/辅助纠错层被启用，它不应该无脑替换，而是先看：

```text
像不像实体
像不像任务
动作是否明确
槽位是否完整
风险是否需要确认
```

这里有两层不要混在一起：

```text
云端热词层：通过 DashScope 原生能力，让 STT 更容易听出 Lark / Vivian / Jachin
理解层：由 L3 主循环在“打开/发送/切到”等上下文里判断标准实体、动作和槽位
```

举例：

```text
用户说：帮我打开拉克
云端 STT 有热词后更容易听出：拉克
L3 主循环看到“打开 + 拉克”
最终可能规整成：帮我打开 Lark
```

再比如：

```text
用户说：给薇薇安发消息
云端 STT 有热词后更容易听出：薇薇安 / Vivian
L3 主循环看到“给 + 人名 + 发消息”
最终规整成：给 Vivian 发消息
```

### 热词不会做什么

热词不是万能的。

它不会保证：

```text
只要说了就一定识别正确
任何相似声音都安全替换
长句里所有英文都读准
任务槽位一定完整
```

它只是提高某些词在云端 STT 里被选中的概率。

所以热词既能救识别，也可能带来副作用。

典型副作用是：用户说了一大段话，但模型因为热词太强，把最后结果压成一个很短的任务句，比如：

```text
打开 Lark
给 Vivian 发消息
切到 Chrome
```

这时系统就要判断：这到底是真实指令，还是被热词“吸过去”了。

## 3. STT 后的安全检查

前端拿到 STT 结果后，不会马上发给 L3。

它还会做几类检查。

### 空文本检查

如果 STT 没识别出有效中文、英文或数字，会直接报“未能识别语音内容”。

### 热词污染检查

如果 STT 明显被热词带偏，例如识别结果过度贴近某些热词，前端会拦截。

这一步在前端：

```text
clients/desktop/src/chat.tsx
detectVoiceHotwordDomination(...)
```

它主要看几个信号。

#### 1. 结果是不是短热词任务

例如识别结果里出现：

```text
chrome
lark
vivian
neil
ethan
vscode
cursor
飞书
拉克
谷歌
浏览器
```

同时又有任务动作词：

```text
打开
启动
找到
切到
进入
给
发
发送
消息
open
find
send
```

并且整句话很短，就会被标记成“可能是热词任务”。

比如：

```text
打开 Lark
给 Vivian 发消息
切到 Chrome
```

这些都属于高风险形态。它们不是一定错，但如果音频很长、结果却这么短，就要警惕。

#### 2. 录音很长，但识别结果很短

如果用户录了 4.5 秒以上，最后却只识别成一个很短的热词任务，系统会认为可疑。

原因很简单：

```text
用户讲了很久，结果只剩“打开 Lark”
这可能不是用户真实完整意思，而是热词把识别结果吸偏了。
```

对应原因名：

```text
short_hotword_task_from_long_audio
```

#### 3. 热词数量很大，且结果正好是短任务

当前热词集合数量是动态的。如果日志里 `hotword_count` 较大，说明这一轮带了较多上下文偏置。

如果热词数量超过阈值，并且识别结果又是很短的热词任务，系统会更谨慎。

对应原因名：

```text
large_hotword_set_short_task
```

但有一个例外：如果文本明显是数学或计算请求，比如：

```text
打开计算器
一加一等于多少
算一下
```

系统会降低热词污染判断，避免把正常计算类请求误拦。

#### 4. 流式预览和最终 STT 冲突

如果前面流式预览听到的是一段较长文本，但最终 STT 突然变成很短的热词任务，也会可疑。

对应原因名：

```text
stream_final_conflict_hotword_task
```

#### 5. STT 不是最终结果

如果来源是临时流式识别，或者 `finalized=false`，任务类请求也会更谨慎。

对应原因名：

```text
non_final_stt_source
```

这种情况不会直接执行任务，而是生成一句确认：

```text
我刚才听到的是“xxx”，但这段语音像是被热词影响了。
你可以再说一遍，或者确认这就是你要做的吗？
```

最终用户看到的效果就是：

```text
不会马上打开软件 / 发消息 / 执行任务
而是先问你确认
```

这是一种安全刹车。

### 热词最终会带来什么效果

理想效果：

```text
“打开拉克”      -> 更容易识别并规整成 “打开 Lark”
“给薇薇安发消息” -> 更容易识别并规整成 “给 Vivian 发消息”
“切到 vs code”  -> 更容易识别并规整成 “切到 VS Code”
“jachin 项目”   -> 更容易保留 Jachin
```

遇到不确定情况时：

```text
系统不会直接执行
系统会把听到的候选句说出来让你确认
```

日志里能看到：

```text
hotword_count
hotword_status
hotword_sources
hotwordDominated
hotwordDominationReasons
```

如果一轮语音被拦截，`voice_chat.log` 里会出现：

```text
stt.hotword_dominated_blocked
```

这说明系统不是“没听懂就乱执行”，而是发现识别结果可能被热词带偏，所以停下来问你。

### 追问生成

如果 STT / understanding 判断缺槽，比如用户说“帮我发消息”，但没说发给谁、发什么，前端会进入追问生成路径。

这里的规则层只给出 `ReplyPlan`，真正说给用户的话会交给一个轻量 LLM composer 写成自然语言。

也就是说：

```text
规则层：判断缺什么
LLM composer：把追问说得像人话
```

## 4. 语音文本如何进入 L3 主循环

前端现在不做任务意图裁决。它不会再把语音先分成 `CHIT_CHAT / SHORT_TASK / LONG_TASK`，也不会用前端路由决定 `direct_llm / foreground / background_submit`。

前端负责的是把“可被 L3 使用的事实”交出去：

```text
最终识别文本
原始 STT 文本
修正后文本
STT backend / source / duration / confidence
cloud_diagnostics
stt_orchestration
声纹结果
是否陪伴态
当前会话 / 任务上下文摘要
附件 / UI 状态
```

也就是说，前端不回答这些问题：

```text
这是不是任务？
要不要调用工具？
该用哪个 MCP？
要不要后台执行？
要不要追问？
```

这些问题交给 L3 认知内核主循环。

## 5. L3 主循环负责意图路由

语音文本到 L3 后，和普通文本一样进入认知内核主循环。主循环会自己判断：

```text
用户是在闲聊、提问、下指令、控制已有任务，还是在补充上一轮信息？
是否需要工具 / MCP / OS workflow？
目标对象是什么？
缺什么槽位？
风险等级如何？
执行前要不要确认？
执行后如何验证？
失败时如何恢复或追问？
```

这里的“意图路由”不是前端规则表，也不是旧的 voice fast lane，而是 L3 主循环的一部分。

主循环可以为了性能选择轻量路径，比如非常简单的问答可以少检索、少开工具、少做任务编排；但这是 L3 内部优化，不是前端提前裁决。

## 6. 前端传给 L3 的内容

前端仍然可以发送 `implicit_signals`，但它的含义变了：它不是“前端判好的路由结果”，而是 L3 主循环使用的证据包。

大概包括：

```text
desktop_companion = true
voice_raw_stt_text
voice_asr_raw_text
voice_corrected_text
voice_final_text
voice_stt_backend
voice_stt_source
voice_stt_confidence
voice_stt_duration_ms
voice_stt_hotword_count
voice_stt_hotword_status
voice_stt_hotword_sources
voice_cloud_diagnostics
voice_stt_orchestration
voice_sv_status
voice_session_id
voice_companion_ui
active_task_context
```

这些字段只描述“发生了什么”和“识别证据是什么”。它们不应该作为最终任务裁决。

如果日志或兼容代码里还能看到下面这些旧字段，它们只能当历史兼容或调试信息看，不能当当前架构的事实来源：

```text
voice_dispatch_tier
voice_intent_class
voice_dispatch_lane
voice_fast_lane
prefer_direct_llm
execution_lane
```

当前真正的事实来源是 L3 主循环日志里的理解、任务编排、工具选择、验证结果和最终回复。

## 7. L3 主循环内部怎么处理

主循环不是简单地把一句话扔给模型。它大致按这个顺序工作：

1. 接收用户文本和语音证据。
2. 归一化输入，比如清理 STT 噪声、合并上下文、识别本轮是新问题还是上一轮补充。
3. 读取必要的环境状态、短期记忆、长期记忆、当前任务状态。
4. 理解用户真正想要的结果。
5. 判断是直接回答、继续对话、追问补槽，还是进入任务执行。
6. 如果需要执行任务，生成工单：目标、对象、边界、风险、可用工具、验证方式。
7. 选择模型、工具、MCP、OS workflow、后台任务或子 agent。
8. 执行或委派执行。
9. 验证结果。
10. 组织用户能听懂的最终回复。

不是每一轮都必须完整跑完所有昂贵步骤。比如一句普通闲聊不需要真的启动工具链；但架构责任仍然在 L3 主循环，而不是前端路由。

## 8. 模型和工具如何参与

旧文档里曾经把语音链路写成类似这样的分层：

```text
快路由 / 直连模型 / 完整 Agent
```

这个说法现在容易误导。更准确的说法是：

```text
L3 认知内核主循环
  -> 根据任务需要选择轻量回答、完整推理、工具、MCP、OS workflow、后台任务或子 agent
```

也就是说，`direct_llm`、fast lane、短任务、长任务这些概念如果还存在，只能是 L3 内部的执行优化或兼容字段，不再是前端对语音请求做出的顶层路由判断。

对用户来说，真实链路应该理解成：

```text
我说一句话
  -> STT 把声音变成文本和证据
  -> 前端把文本和证据交给 L3
  -> L3 主循环判断我要什么
  -> L3 决定直接回答还是调工具做事
  -> L3 验证后给出人话回复
  -> 前端把回复播出来
```

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

当前 JVS TTS 默认走云端 DashScope CosyVoice：

核心文件：

```text
voice_server/services/cloud_tts_service.py
voice_server/services/tts_service.py
```

当前健康检查里常见的云端配置是：

```text
tts_backend = cloud
tts_model = cosyvoice-v3-plus
tts_fast_model = cosyvoice-v3-flash
tts_voice = longanhuan
speed = 1.0
sample_rate = 24000
```

前端默认值：

```text
clients/desktop/src/voice/voiceDefaults.ts
```

注意：前端日志里可能还会出现 `zm_053` 这类本地 voice alias 或 UI 配置字段；最终云端实际使用的声音要看 JVS 返回的 stream meta 或 `/health`。

### 云端 CosyVoice 合成流程

JVS `/v1/tts/synthesize` 收到文本后，大概流程：

```text
文本清洗 / 归一化
  -> 调 DashScope CosyVoice HTTP 接口
  -> 返回 WAV / PCM 音频
  -> 前端播放
```

流式 TTS 会走：

```text
/v1/tts/stream
  -> DashScope CosyVoice streaming
  -> tts.jvs_stream_start
  -> tts.jvs_stream_meta
  -> tts.playback_pcm_chunk
  -> tts.jvs_stream_done
```

这就是现在日志里常见的云端 TTS 形态。

### 本地 Kokoro 路径

如果 `JACHIN_TTS_BACKEND` 配成 local，JVS 才会使用 Kokoro ONNX：

```text
voice_server/services/tts_service.py
model = Kokoro-82M-v1.1-zh-ONNX
```

本地 Kokoro 路径大概会做：

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

所以，如果系统当前是云端 TTS，就不要把口音、延迟、英文读法问题直接归因到 Kokoro。只有 `/health` 显示 `tts_backend = local` 时，Kokoro 中文前端、phoneme 映射、style vector 这些才是主排查对象。

云端 TTS 的问题更多要看 DashScope 返回延迟、stream 首包时间、voice 配置和前端播放队列。

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

这个场景由前端状态传递和 L3 主循环共同处理。

### 前端状态层

前端可以保存 active voice tasks，用来展示状态、打断播放、取消当前语音会话，并把当前任务摘要作为上下文发给 L3。

前端不直接判断这一句到底是取消、查进度、修改任务还是新话题。它只把这些事实交给 L3：

```text
active_task_id
active_task_status
active_task_summary
last_user_turn
voice_final_text
voice_stt_evidence
```

这一步的目标是让 L3 主循环有足够上下文判断用户是在控制已有任务，还是开启一个新问题。

### L3 主循环 / task 层

L3 主循环根据用户原话和 active task context 判断本轮意图：

```text
取消 / 停止 -> ABORT
进度 / 做到哪了 -> STATUS
改成 / 再加 -> MODIFY
继续 -> RESUME
其他新话题 -> PARALLEL 或普通回答
```

如果判断是任务控制，会进入 L3 的后台控制路径。

如果是普通聊天但有 active task，前端注入的任务上下文只作为背景证据：

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
stt.cloud_start
stt.cloud_dns
stt.cloud_connect
stt.cloud_upload_start
stt.cloud_result / stt.cloud_exception
stt.cloud_soft_timeout
stt.fallback_start
stt.fallback_result
stt.cloud_late_result
stt.jvs_transcribe_ok
stt.local_fallback_used
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
tts.jvs_stream_start
tts.jvs_stream_meta
tts.playback_pcm_chunk
tts.jvs_stream_done
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

如果要进一步判断云端 STT 慢在哪里，看：

```text
stt.cloud_dns
stt.cloud_connect
stt.cloud_vocabulary_sync_*
stt.cloud_upload_start
stt.cloud_result / stt.cloud_exception
stt.cloud_soft_timeout
stt.fallback_result
stt.cloud_late_result
```

如果出现：

```text
stt.local_fallback_used
```

说明这一轮最终用了本地 sherpa 兜底，而不是云端直接成功。

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

它更偏 L3 内部过程，比如认知内核主循环、归一化、记忆/环境状态读取、任务编排、工具调用、验证、异常。

## 14. 当前系统最容易混乱的地方

### 14.1 不要再把旧前端路由当事实源

旧版本里前端和 L3 都有路由判断，容易出现两层理解不一致：

- 前端认为是轻问答。
- L3 当成任务或 presence ack。
- 用户问正常问题，系统给出不相干回复。

当前文档按新架构描述：前端不再做任务意图裁决。前端只传语音文本、STT 证据、声纹状态、UI 状态和 active task context；真正的理解、路由、任务编排和工具选择由 L3 认知内核主循环负责。

如果日志里还能看到 `voice_fast_lane_kind`、`voice_allow_template_reply`、`voice_dispatch_*` 这类字段，要优先判断它们是兼容字段、调试字段，还是仍在影响执行。架构上不应该再依赖它们作为顶层决策。

### 14.2 “结构边界”和“大模型自由度”要分清

新架构不是完全不要结构，也不是把所有事情交给一段自由文本模型输出。

结构负责：

- 主循环步骤。
- 工单字段。
- 工具和 MCP 调用边界。
- 风险控制。
- 验证要求。
- 失败恢复策略。

大模型负责：

- 理解用户真实意图。
- 填写对象、动作、约束、缺槽。
- 判断是否需要追问。
- 选择合适的执行路径。
- 把结果组织成自然、人话的回复。

问题通常不在“有没有结构”，而在结构是否抢走了 L3 主循环本该做的语义判断。

### 14.3 TTS 要先区分云端还是本地

当前默认是云端 DashScope CosyVoice。排查口音、停顿、英文混读、延迟时，先看 `/health`：

```text
tts_backend = cloud  -> 看 CosyVoice、voice、首包时间、stream chunk、播放队列
tts_backend = local  -> 看 Kokoro 中文前端、phoneme mapping、style、静音裁剪
```

如果是云端 TTS，重点看：

- `tts.jvs_stream_meta` 里的 backend、model、voice、synthesisText。
- `firstAudioMs` 是否过高。
- `tts.playback_pcm_chunk` 是否连续。
- 播放队列是否被旧 generation 占住。

如果是本地 Kokoro，才重点看：

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
传递层：前端整理最终文本、语音诊断、UI 状态和任务上下文
认知层：L3 主循环理解意图、补槽、编排任务、选择工具、验证恢复
表达层：流式文字、分句、TTS、播放、打断
```

最理想的运行方式是：

- “你好 / 在吗”秒回连接感。
- “今天吃什么”由 L3 主循环判断为普通问答，不进任务链，也不模板敷衍。
- “帮我打开计算器”由 L3 主循环判断为本地 OS 任务，再选择对应工具。
- “把目录生成报告”由 L3 主循环判断为更长任务，再决定后台执行或分派。
- 任务执行中用户说“停一下 / 进度怎么样 / 改成这样”，由 L3 主循环结合 active task context 判断任务控制意图。
- 用户打断旧语音时，旧 TTS 和旧播放队列被取消，新请求优先。

如果之后要继续优化，建议按日志把问题归因到具体层：

```text
STT 慢或错 -> 看 voice_server STT / hotword / owner-track
意图或任务判断错 -> 看 terminal_turn 里的 L3 主循环、归一化、任务编排、工具选择、验证证据
文字慢 -> 看 L3 主循环耗时、模型首 token、工具执行耗时
文字对但说话慢 -> 看 TTS synth / playback queue
说得难听 -> 先看 tts_backend；cloud 看 CosyVoice/voice/首包，local 看 Kokoro frontend / phoneme mapping / pause / style
旧话乱插 -> 看 generation / cancel / playback queue
```
