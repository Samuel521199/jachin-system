# Voice STT Hotword Domination Safety Proposal

## 这次问题的重新判断

用户实际说的是：

> 帮我打开计算器算一下 40*90

系统最终却进入了：

> 找到 Chrome

这个现象不应该简单归因于“STT 模型太差”。普通 ASR 即使识别错误，也很难把一整句包含“计算器、算一下、40*90”的语音稳定改写成一个刚好存在于热词/实体库里的任务短句“找到 Chrome”。

更合理的判断是：**热词模块、流式 STT 结果、Jachin/L3 语音调度之间存在耦合问题**。也就是说，某个中间态结果被当成最终 STT 事实传给了 L3，而 L3 又在缺少证据约束的情况下把它当成真实用户意图执行了。

因此本文档的重点不是继续提高 ASR 模型本身，而是解决：

1. 流式 partial/final 的边界不清。
2. 热词偏置在所有场景里全局生效。
3. 前端把流式识别文本当作最终文本。
4. L3 收到的只有被污染后的文本，缺少 STT 证据链。
5. 可执行任务没有根据语音来源做安全降级。

## 目前链路中最可疑的问题

### 1. 前端可能把流式文本当成最终 STT 结果

当前语音链路里存在一个危险捷径：

```ts
const streamText = stripDefaultSadEmojiSuffix((preRecognizedText || "").trim());
const canUseStreamText = Boolean(streamText) && wavForStt === wavBase64;
```

只要 `preRecognizedText` 非空，并且音频没有被切换，前端就会把流式识别文本包装成最终结果：

```ts
{
  text: streamText,
  rawText: streamText,
  correctedText: streamText,
  source: "jvs_stream_ws"
}
```

这会跳过最终 HTTP STT：

```ts
transcribeWavBase64Detailed(...)
```

结果是：

- 没有真正的 final STT 二次确认。
- 没有完整 `confidence`、`durationMs`、`backend`、`hotword_status` 等证据。
- 没有 final-vs-partial 对照。
- 没有 hotword domination 风险判断。
- L3 只能看到一个已经被流式/热词污染过的文本。

这正好解释了为什么 L3 看到的是“找到 Chrome”，而不是“帮我打开计算器算一下 40*90”。

### 2. 流式 STT 不应该等同于最终 STT

流式识别的 partial 结果本来就不稳定。它适合做实时字幕、UI 提示、语音输入中的即时反馈，但不适合直接驱动：

- 打开应用。
- 发送消息。
- 删除文件。
- 控制系统。
- 触发外部副作用。

尤其是在启用热词偏置后，partial 结果更容易提前贴近热词库里的高权重词，例如 `Chrome`、`Lark`、`Vivian`。

如果前端把这种 partial 文本直接当 final，就会形成非常危险的路径：

```text
用户真实语音
  ↓
流式 partial 被热词吸附
  ↓
前端把 partial 当 final
  ↓
L3 收到污染文本
  ↓
Router 正常但基于错误输入执行
```

这个问题不是 Router 自己能解决的，因为 Router 根本没有看到原始语音事实。

### 3. 热词提供器把不同用途的词混在一起了

当前热词来源包括：

- `l3_node.voice_entity_correction`
- `data/voice/sherpa_hotwords.txt`
- `data/voice/domain_lexicon.json`
- `data/voice/stt_hotwords.json`

这些词被合并成一个全局热词集合。问题是它们的用途不同：

- `Chrome` 是应用实体。
- `Lark` 是应用实体。
- `Vivian`、`Neil`、`Ethan` 是联系人实体。
- 公司人员名称是联系人候选。
- 一些中文别名是纠错候选。

但 ASR 热词层并不知道这些区别。它只看到“这些词都应该被偏置”。这会导致应用名、联系人名在任何语音上下文里都可能被过度吸附。

例如用户说：

```text
打开计算器算一下 40*90
```

如果流式片段里有一段模糊音频被热词拉向 `Chrome`，而前端又直接采用这个 partial，就会变成“找到 Chrome”。

### 4. L3 没有收到足够的语音证据

L3 当前主要拿到的是最终文本，例如：

```text
找到 Chrome
```

它没有足够可靠的信息判断：

- 这个文本来自流式 partial 还是 final STT。
- 是否经过热词强偏置。
- 是否存在 hotword-on/off 差异。
- 音频时长是否明显长于文本长度。
- 是否缺失用户真实说出的数字、计算词、上下文词。
- 是否只是一个热词吸附结果。

所以 L3 的行为从自身视角看并不奇怪：

```text
输入：找到 Chrome
结论：用户想打开/找到 Chrome
```

真正的问题在 L3 之前：错误的文本被当成可信事实送进来了。

## 新的核心原则

### 原则 1：流式结果只能做 UI 提示，不能直接驱动可执行任务

`jvs_stream_ws` 的文本必须被标记为 provisional，也就是临时文本。

它可以显示给用户看，但不能直接触发：

- 应用打开。
- 文件操作。
- 消息发送。
- 系统控制。
- 外部工具调用。

只有 final STT 结果可以进入可执行任务链路。

### 原则 2：L3 必须知道 STT 证据来源

L3 不应该只接收一个字符串。它至少需要接收：

```json
{
  "text": "找到Chrome",
  "raw_text": "...",
  "corrected_text": "...",
  "source": "jvs_stream_ws | jvs_http_transcribe | jvs_ws_final",
  "finalized": true,
  "confidence": 0.91,
  "duration_ms": 6800,
  "hotword_status": "applied",
  "hotword_count": 144,
  "hotword_dominated": false,
  "alternatives": []
}
```

如果这些信息缺失，L3 应该默认降级为追问，而不是执行。

### 原则 3：热词分层，不再全局同权重生效

热词不能再只是一张扁平表。需要按用途区分：

```text
acoustic_bias_light       轻量声学提示
entity_candidate         实体候选，不直接改写
app_open_bias            只有应用打开上下文才提高权重
contact_bias             只有联系人/消息上下文才提高权重
math_domain_bias         计算器、计算、数字、乘除等数学上下文
confirmation_phrase      确认/取消类短语
```

其中 `Chrome` 这类应用名不能在所有场景里都拥有强势热词权重。它应该在已经有应用打开上下文时增强，而不是把其他领域的句子吸过去。

### 原则 4：可执行动作必须通过 final evidence gate

只要任务会产生外部副作用，就必须满足：

1. STT 来源是 final。
2. final 文本不是 hotword dominated。
3. 文本和音频时长大致匹配。
4. 任务类型与上下文证据一致。
5. 如果存在高热词吸附风险，必须追问。

## 解决方案

### Phase 0：先堵住最危险的执行路径

#### 0.1 PTT 结束后必须跑 final STT

PTT 录音结束后，即使已经有流式 `preRecognizedText`，也必须调用最终 STT：

```text
recording finished
  ↓
always call final transcribe
  ↓
compare streamText vs finalText
  ↓
only finalText can enter executable L3 path
```

`preRecognizedText` 只能作为 UI 预览，不再作为最终输入。

#### 0.2 `jvs_stream_ws` 来源禁止执行

如果某次语音输入进入 L3 时仍然是：

```text
source = jvs_stream_ws
```

则 L3 必须禁止打开应用、发送消息、执行系统工具，只能：

- 等待 final STT。
- 或追问用户确认。

建议策略：

```text
source == jvs_stream_ws
  and task is executable
  -> block execution
  -> ask clarification
```

#### 0.3 前端不要伪造完整 STT trace

前端不能把流式文本包装成：

```json
{
  "text": streamText,
  "rawText": streamText,
  "correctedText": streamText
}
```

这种结构会让后续模块误以为它是可信 final 结果。

应该明确标记：

```json
{
  "text": streamText,
  "source": "jvs_stream_ws",
  "finalized": false,
  "provisional": true
}
```

并且这种结果不能直接执行。

### Phase 1：增加 final-vs-stream 仲裁

PTT 完成后应同时保留：

```text
stream_text: 流式识别文本
final_text: 最终识别文本
hotword_on_text: 热词开启识别结果
hotword_off_text: 可选，无热词识别结果
```

仲裁规则：

1. `final_text` 优先级最高。
2. `stream_text` 只做辅助证据。
3. 如果 `stream_text` 是热词实体短句，而 `final_text` 明显不同，不能执行热词任务。
4. 如果 `hotword_on_text` 和 `hotword_off_text` 差异巨大，且热词版本更像任务短句，要标记为 `hotword_dominated`。

示例：

```text
stream_text: 找到Chrome
final_text: 打开计算器算一下四十乘九十
decision: use final_text, reject stream_text
```

或：

```text
stream_text: 找到Chrome
final_text: 不确定
duration: 6800ms
decision: ask clarification, do not open Chrome
```

### Phase 2：热词分层和降权

#### 2.1 把热词拆成不同层

建议把热词配置拆成：

```json
{
  "apps": [
    {"name": "Chrome", "aliases": ["谷歌浏览器"], "scope": "app_open"},
    {"name": "Lark", "aliases": ["飞书", "拉克"], "scope": "app_open"}
  ],
  "contacts": [
    {"name": "Vivian", "scope": "contact"},
    {"name": "Neil", "scope": "contact"},
    {"name": "Ethan", "scope": "contact"}
  ],
  "math": [
    {"name": "计算器", "scope": "math"},
    {"name": "算一下", "scope": "math"},
    {"name": "乘以", "scope": "math"}
  ]
}
```

不同层的词不能全部用同一强度注入 ASR。

#### 2.2 默认只启用轻量热词

第一遍 STT 只使用轻量热词：

```text
Lark / Chrome / Vivian / Neil / Ethan 等实体可以轻微提示
但不能强行改写整句
```

然后由理解层进行实体召回和任务判断。

#### 2.3 强热词只在上下文明确后启用

例如：

- 已经检测到“打开/启动/切到”时，才增强应用名。
- 已经检测到“给/发消息/联系/找人”时，才增强联系人。
- 已经检测到“算/计算/多少/乘/加/减/除/数字”时，增强数学域。

这可以避免 `Chrome` 在计算类语音里抢占结果。

### Phase 3：Hotword Domination 检测

新增一个风险检测器，判断结果是不是“被热词支配”。

触发条件包括：

1. 音频时长较长，但文本极短。
2. 文本几乎只包含热词实体。
3. 文本缺少用户原句中常见的动作、数字、对象。
4. 热词开启结果和无热词结果差异很大。
5. 识别结果刚好等于某个高频任务模板，例如“找到 Chrome”。
6. 流式文本与 final 文本冲突。

示例：

```json
{
  "text": "找到Chrome",
  "duration_ms": 6800,
  "hotword_count": 144,
  "source": "jvs_stream_ws",
  "hotword_dominated": true,
  "reason": [
    "stream_source_used_as_final",
    "short_hotword_task_from_long_audio",
    "missing_final_stt_evidence"
  ]
}
```

一旦 `hotword_dominated = true`，可执行任务必须禁止直接执行。

### Phase 4：L3 增加语音安全门

L3 进入 Router 前增加一个 voice evidence gate。

#### 4.1 允许执行的条件

只有满足以下条件，才允许进入可执行工具链：

```text
finalized == true
source in ["jvs_http_transcribe", "jvs_ws_final"]
hotword_dominated == false
confidence >= threshold
task_likelihood >= threshold
```

#### 4.2 必须追问的条件

以下情况必须追问：

```text
source == jvs_stream_ws
hotword_dominated == true
final_text missing
duration/text ratio abnormal
selected task is executable but evidence incomplete
```

追问话术应该是自然语言，而不是结构化调试信息：

```text
我听到的是“找到 Chrome”，但这段语音有点不确定。你是要打开 Chrome，还是要打开计算器算 40 乘 90？
```

### Phase 5：为计算器/数学域补齐上下文

这次事故里，用户真实意图是：

```text
打开计算器算一下 40*90
```

所以系统需要把以下内容作为强上下文特征：

- 计算器
- 算一下
- 多少
- 加减乘除
- 数字串
- `40*90`
- `40 乘 90`

如果文本里出现数字表达式或数学动作词，应用热词要自动降权，数学域要升权。

这不是为单句写规则，而是给“数学/计算”这个任务域补完整的域特征。

## 修改后的目标链路

```text
用户语音
  ↓
流式 STT partial
  ↓
只显示为 UI 字幕，不执行
  ↓
PTT 结束
  ↓
final STT
  ↓
stream/final/hotword evidence arbitration
  ↓
hotword domination detector
  ↓
voice evidence gate
  ↓
候选任务解析
  ↓
高置信执行 / 低置信追问
```

## 针对本次事故的期望行为

即使流式识别中出现：

```text
找到 Chrome
```

系统也不能直接打开 Chrome。

正确行为应该是以下之一：

### 情况 A：final STT 正确

```text
final_text: 打开计算器算一下40乘90
action: 打开计算器或进入计算任务
```

### 情况 B：final STT 不确定

```text
stream_text: 找到Chrome
final_text: 不确定
duration_ms: 6800
action: 追问用户
```

追问：

```text
我刚才听得不太稳。你是想打开 Chrome，还是想打开计算器算 40 乘 90？
```

### 情况 C：只有 stream result

```text
source: jvs_stream_ws
action: 禁止执行
```

必须等待 final STT 或追问。

## 验收标准

### 必须通过的回归用例

1. 用户说“帮我打开计算器算一下 40*90”，系统不能输出或执行“找到 Chrome”。
2. 如果流式结果是“找到 Chrome”，但 final 结果不同，必须采用 final。
3. 如果没有 final 结果，`jvs_stream_ws` 不能触发应用打开。
4. 长音频识别成极短热词任务时，必须标记 `hotword_dominated`。
5. `Chrome`、`Lark`、联系人姓名不能在所有上下文里以同等强度全局吸附。
6. L3 收到缺少 STT 证据的语音任务时，必须追问或降级，不能执行。

### 日志验收

每次语音任务日志必须能看到：

```text
stream_text
final_text
selected_text
source
finalized
confidence
duration_ms
hotword_count
hotword_status
hotword_dominated
hotword_domination_reason
voice_gate_decision
```

没有这些字段，就无法判断问题发生在 STT、热词、前端还是 L3。

## 实施优先级

### P0：必须马上做

1. PTT 结束后始终跑 final STT。
2. 禁止 `jvs_stream_ws` 结果直接进入可执行 L3 路由。
3. 前端不要把流式文本伪装成 final STT trace。
4. L3 增加 `source/finalized` 安全判断。

### P1：短期完成

1. 增加 stream-vs-final 仲裁。
2. 增加 hotword domination detector。
3. 热词拆分 scope/type/weight。
4. 应用名、联系人名默认降为轻量热词。
5. 数学/计算域加入上下文特征。

### P2：中期完成

1. 支持 hotword-on/off 双通道对比。
2. 记录用户确认反馈，持续校准热词权重。
3. 为 L3 提供完整语音证据对象，而不是单个字符串。
4. 建立语音回归集，包括“计算器 40*90 不能变 Chrome”。

## 最终结论

这次问题的根因不应优先理解为“STT 离谱到把整句话听成 Chrome”。更大的可能是：

```text
流式/热词中间结果
  被前端当成 final
  又被 L3 当成可信用户意图
  最后被 Router 正常执行
```

所以真正要修的是语音链路的证据边界和安全门：

- 流式不是 final。
- 热词不是事实。
- L3 不能只信一个字符串。
- 可执行任务必须有 final STT 证据。
- 热词支配结果必须追问。

把这几个边界建立起来后，即使底层 ASR 偶尔被热词吸附，也不会再直接变成错误执行。
