# 语音识别容错与实体纠错方案（STT Robustness & Entity Correction）

> **状态**：设计稿（未编码）  
> **触发案例**：用户说「帮我打开 luck 给 viian 发一条消息内容是我今天要睡觉」  
> → STT 输出含噪：`luck`（本意 Lark）、`viian`（本意 Vivian）  
> → 系统直接用噪声文本执行，`mcp:windows_open_app(luck)` 抛 `FileNotFoundError`  
> **本质问题**：不只是「语音识别错一点」——**后面整条执行链太相信 STT 第一版文本**，把每个字都当圣旨；输入端微小抖动 × 执行端绝对硬匹配 × 中间无缓冲带 → 任务全面崩溃  
> **本案关键矛盾**：IO 的 reasoning 已看懂「最终目标是发消息」，结构化结果却仍是 `app_control` + `app_name=luck`，`recipients` / `message` 为空仍放行执行  
> **架构缺口**：STT 与执行器之间缺少 **上下文缓冲带**；识别、归一化、路由、槽位校验、失败恢复 **全链路都不承认不确定性**  
> **关联文档**：`VOICE_OS_TOOL_INTENT_ROUTING_PROPOSAL.md`、`VOICE_INTENT_ROUTING_AND_TASK_ORCHESTRATION.md`

---

## 1. 问题背景：语音识别本质上不可靠

### 1.1 语音到文字是有损转换

无论用哪家 ASR（自动语音识别）引擎，语音转文字都不是完美的复刻，而是 **按声学概率猜出来的文字序列**。常见的失真类型有：

| 失真类型 | 举例 | 原因 |
|----------|------|------|
| **同音/近音替换** | `lark → luck`、`Lark → lock` | 音节相近，模型从语言概率选了更常见的词 |
| **人名/专有名词出错** | `Vivian → viian`、`Jachin → Jason` | 专有名词在训练语料中频次低，易被忽略字母 |
| **语言混合出错** | 中文句子里夹英文 app 名 | 中英文切换点 ASR 最脆弱 |
| **停顿造成截断** | 「给…viian 发…」→ 分成两段 | 静音检测截断句子 |
| **口音/环境噪声** | 发音不标准或有背景音 | 识别率直接下降 |

**关键前提**：对 Jachin 来说，通过语音输入的每一句话 **都应该被当成「可能有噪声的信号」处理**，而不是「精确的文字输入」。现在系统没有这个前提假设。

### 1.2 触发案例的完整失败链

```
用户说：「帮我打开 Lark 给 Vivian 发一条消息内容是我今天要睡觉」
          ↓ STT 识别
文字输出：「帮我打开 luck 给 viian 发一条消息内容是我今天要睡觉」
          ↓ IntentOrchestrator 归一化（字面直传）
normalized：「帮我打开luck给viian发一条消息内容是我今天要睡觉」
          ↓ IO 槽位提取（只信第一版文本）
app_name = "luck"（字面值，无候选、无 suspect 标记）
recipients = []（"viian" 未进收件人槽）
message = ""（「内容是…」未抽出）
missing_slots = []  ← 校验虚设，空槽位仍放行
          ↓ IO 内部矛盾
reasoning: "correct task type is lark_message_send"
结构化结果: task_type = app_control  ← 说一套做一套
          ↓ IO 路由决策（被「打开」绑架）
route = mcp:windows_open_app, params={app_name:"luck"}
route_policy = execute（置信 0.9，快速路径直接执行）
          ↓ 工具执行
FileNotFoundError: 系统找不到指定的文件
          ↓ 无二次路由、无 recovery
最终回复直接透传 FileNotFoundError，「发消息」真实目标被打开失败覆盖
```

**0 轮 ReAct，0 次澄清，0 次重试，0 次失败后改道**——错了就直接告诉用户「找不到文件」，任务彻底失败。

系统非常「耿直」：拿到 `app_name="luck"` 就去注册表/文件系统里找 `luck.exe`，差一个字母就是天壤之别——**在硬件和系统调用层，硬匹配没有容错空间**。

---

## 2. 根因分析

用三句话概括这次灾难是怎么发生的：

| 环节 | 发生了什么 | 人话解释 |
|------|-----------|----------|
| **STT：声学猜字，不懂语义** | `Lark → luck`，`Vivian → viian` | 听声引擎只负责「听起来像什么字」，不知道你电脑装了 Lark、同事叫 Vivian |
| **执行链：绝对硬匹配** | `app_name="luck"` 原样进 MCP | 路由和工具层太「诚实」，不做模糊搜索、不做纠错、不做反问 |
| **中间缺缓冲带** | STT 裸文本 → 直接进 IO | 没有一层像人脑一样的「结合上下文猜用户到底指什么」 |
| **全链路当圣旨** | 无 suspect_tokens、无候选词 | 下游只看到一个「确定的 luck」，不知道它可能是 Lark |

下面展开各层细节。

### 2.1 根因一：语音文本被当成「事实」，没有不确定性

STT 输出进入系统后，**只保留一个最终字符串**，没有附带：

- **候选词 / 别名**（luck 可能是 Lark / 飞书 / lock）；
- **实体级置信度**（这个词有多可疑）；
- **suspect_tokens**（标记「音近、待核实」的 token 列表）。

下游路由器因此 **只看到一个确定的 `luck`**，无法做「大概率是 Lark，但需确认」这类人类式判断。  
**核心问题**：后端把 STT 的 **每个字都当圣旨**——这是比「听错一个词」更深的问题。

### 2.2 根因二：STT 是「声学驱动」，不是「语义驱动」

当前 STT 引擎以通用语言模型工作，**不知道 Jachin 系统里有哪些应用名、联系人名、专有名词**。

- 「Lark」在普通话语境下发音近似「lock」或「luck」，通用语言模型会优先选更常见的英文词；
- 「Vivian」是外来音译名，ASR 在识别中文句子中夹带的英文人名时错误率极高；
- 系统没有把「已知应用列表」「联系人名单」「Jachin 专有词表」注入到 ASR 的热词/偏置表里。

**后果**：应用名和联系人名这两类「最关键的槽位」恰好是 ASR 最容易出错的类型。

**不能指望 STT 永远 100% 准确**——架构必须假设：**听错的概率永远大于零**，并在 **识别 → 归一化 → 路由 → 槽位校验 → 失败恢复** 每一环都传递「不确定」。

### 2.3 根因三：缺少领域别名词典（Domain Lexicon）

语音任务高度依赖 **音近词 → 真实实体** 的映射，但系统没有维护：

| 类型 | 应有映射 | 现状 |
|------|---------|------|
| 应用别名 | luck / lark / lock → Lark（飞书） | 无，luck 直接当 app 名 |
| 联系人 | viian → Vivian | 无，收件人槽为空 |
| 常用 App 英文名 | chrome / clone、vscode / ws code | 无兜底 |

这类 **Domain Lexicon（领域词典）** 应作为缓冲带和工具软匹配的 SSOT，而不是散落在各 MCP 里各猜各的。

### 2.4 根因四：路由被「打开」绑架，复合任务拆错

用户说的是 **复合任务**：「打开应用 + 给某人发消息 + 内容是…」。  
系统却把 **第一个动词「打开」** 当主任务，忽略了后面的 **「给 viian 发一条消息内容是…」**。

| 用户结构 | 系统错误理解 | 正确理解 |
|----------|-------------|---------|
| 打开 X（手段） | 主任务 = app_control | 手段动作 |
| 给 Y 发消息（目的） | 被忽略 | **主任务 = message_send** |
| 内容是 Z | message 槽为空 | message = Z |

**任务优先级规则（Codex）**：**发送 / 创建 / 删除 / 提交** 等有明确结果的动作，优先级 **高于** **打开 / 切换 / 进入** 等手段动作。  
看到「给 X 发消息 内容是 Y」模式 → 主任务 **必须** 是 `message_send`，打开 App 只是可选前置步骤。

### 2.5 根因五：槽位强校验缺失——空槽仍放行

若主任务是发消息，**必填槽位** 为：

- `recipient` = viian（纠错后 Vivian）
- `message` = 我今天要睡觉

日志里两者皆空，却 `missing_slots: []` —— **校验规则形同虚设**：不能因为 `app_name` 有值就放行；**消息任务没有 recipient / message 就不能执行**。

这是 P0 级 bug：槽位校验应与 task_type **绑定**，不能全局写死「无缺失」。

### 2.6 根因六：下游执行链是「绝对硬匹配（Hard Match）」

L3 意图路由器和底层 MCP 工具拿到槽位后，往往做 **精确字符串匹配**：

- `windows_open_app(app_name="luck")` → 在已安装应用/注册表里找字面叫 `luck` 的程序；
- 找不到 → 立刻 `FileNotFoundError`，没有「你是不是指 Lark？」这一问。

在操作系统和 RPA 层，**差一个字母就是两个完全不同的目标**。执行端不能假设上游输入永远干净。

### 2.7 根因七：缺少「上下文缓冲带（Contextual Buffer）」

人类听到「打开 luck 给 viian 发消息」会结合上下文瞬间纠错；当前路径却是 STT 裸文本 **直接** 进 IO → MCP（见 §2.1）。缓冲带负责 **归一化 + 实体映射 + suspect_tokens**，目前不存在。

### 2.8 根因八：IO 内部自我矛盾——reasoning 说对了，routing 走错了

日志里 reasoning 已写明 `lark_message_send`，结构化结果却仍是 `app_control` + `windows_open_app`。**LLM reasoning 与结构化 task_type 冲突时，必须触发一致性门禁并拦下执行**——本案正是应被拦住的信号，却直接放行了。

### 2.9 根因九：HIDCA 域判断错误

日志 `semantic_router_domain: OS_CONTROL`，真实目标应是 `WORKSPACE_LARK`——因主任务被误判为「打开 luck」，域、工具剪枝、Prompt 全偏了。

### 2.10 根因十：失败后没有二次路由（Execution Recovery）

`windows_open_app(luck)` 失败后，系统 **没有回看原句**：

- 句子里还有「给 viian 发消息内容是…」→ 应 **改走** Lark/IM 发消息路径；
- 或至少问：「你是指 Lark/飞书吗？」

但它把 **打开失败当作整任务失败**，`FileNotFoundError` 直接透传——**「发消息」的真实目标被手段步骤的失败覆盖**。对有副作用的任务，**打开窗口失败不应抹掉发消息意图**。

---

### 2.11 根因十一：多轮对话上下文被忽略

本案中，如果用户在同一会话的上一轮刚说过「用 Lark 发消息」或「联系 Vivian」，那么这一轮说出 `luck` / `viian` 时，系统本可以用 **会话历史** 直接推断：

> 「这个人 30 秒前说过 Lark，现在说 luck，大概率是同一个东西。」

但系统把每一轮语音都当成独立事件处理，**不携带上一轮已确认的实体上下文**。这是缓冲带容易利用却被遗漏的信号来源。

---

## 3. 问题影响面

这不是一个偶发的边角案例。以下场景都会触发同类失败：

| 场景 | STT 噪声类型 | 后果 |
|------|-------------|------|
| 「打开 chrome」被识别成「打开 clone」 | 同音替换 | 找不到 clone 应用 |
| 「发给 Alex」被识别成「发给 electra」 | 人名扭曲 | 找不到联系人 |
| 「打开 VS Code」→「打开 WS code」 | 首字母混淆 | 找不到 WS code |
| 「帮我搜 notion」→「帮我搜 notion note」| 增词 | 工具参数异常 |
| 「发给 Jachin 组的 Frank」→「发给 Jachin 组的 frank」| 大小写 | 联系人查找失败 |

**规律**：应用名 + 联系人名 + 专有名词 = ASR 最不可靠的三类实体，也恰好是 Jachin 任务执行最依赖的三类槽位。这是系统性的脆弱点，不是运气问题。

---

## 4. 解决方案：给语音和执行之间加「减震器」

**核心思路**：问题不是 STT 必须 100% 准，而是 **后端把 STT 每个字都当圣旨**。要让语音可靠，必须让 **识别、归一化、路由、槽位校验、失败恢复** 全链路都能 **承认不确定性**。

### 4.0 整体框架：三层能力 × 四道防线

两种视角描述同一套方案——缺任何一层，仍可能在某一环节崩盘。

**视角 A：按执行位置（四道防线）**

| 防线 | 位置 | 作用 | 优先级 |
|------|------|------|--------|
| **① 前端** | STT 引擎 | 热词注入，从源头减少听错 | P1 |
| **② 大脑** | STT → IO 之间 | **上下文缓冲带**：语义纠错、suspect_tokens、Domain Lexicon | **P0** |
| **③ 底层** | MCP / 工具执行 | 模糊匹配，硬匹配改软匹配 | P0 |
| **④ UX** | 失败与 external 操作 | 优雅降级、轻确认、recovery | P0 |
| **路由补强** | IO / HIDCA | 复合任务、槽位强校验、reasoning≡route | P0 |

**视角 B：按职责分层（三层能力）**

| 层 | 做什么 | 对应防线 |
|----|--------|---------|
| **第一层：语音容错** | 热词、Domain Lexicon、suspect_tokens、多轮上下文 | ①② |
| **第二层：意图路由** | 复合句目标抽取、任务优先级、槽位强校验、reasoning≡route | 路由补强 |
| **第三层：执行闭环** | 轻确认、失败后 recovery、副作用预览、用户反馈学习 | ③④ |

---

### 4.1 防线一：STT 热词注入（前端防线）

**最轻量的一级**——在 ASR 引擎初始化时，把「系统里真实存在的东西」告诉听声模型。

大多数现代 ASR（Whisper、Azure Speech、阿里/千问语音等）都支持 **Hotwords / Prompt / 偏置词表**：听到类似「拉克」的发音时，若词表里有 **Lark** 的权重加成，会优先输出 Lark 而不是大众词 luck。

需要注入的词表：

| 词表类型 | 内容 | 更新频率 |
|----------|------|----------|
| 已安装 / 常用应用 | Lark、Codex、Calculator、Chrome… | 启动时扫描 |
| 飞书联系人 | Vivian、Alex、Frank… | 登录后同步 |
| Jachin 专有词 | Jachin、MCP、Codex… | 固定 |
| 高频指令动词 | 发送、打开、截图… | 固定 |

**预期**：同音误识别概率可降约 40～70%（经验值，视引擎而定）。

**局限**：热词 **只能减少错误，不能消灭错误**——仍必须依赖缓冲带、suspect_tokens 和后续各层兜底。

**STT 输出结构升级（Codex）**：不只传一句 `final_text`，应传：

```json
{
  "raw_text": "帮我打开 luck 给 viian发一条消息…",
  "normalized_text": "帮我打开luck给viian发一条消息…",
  "suspect_tokens": [
    { "token": "luck", "candidates": ["Lark", "飞书"], "reason": "phonetic_near_app" },
    { "token": "viian", "candidates": ["Vivian"], "reason": "contact_fuzzy" }
  ],
  "channel": "voice_stt"
}
```

下游 **不得** 只读 `normalized_text` 就当事实——必须携带 `suspect_tokens` 进入缓冲带与 IO。

---

### 4.2 防线二：上下文缓冲带 — LLM 前置语义纠错（大脑防线，**优先落地**）

这是 Gemini 方案里最值得采纳的一点，也是本方案 **最推荐优先实现** 的一层：**不要让 STT 原始输出直接进入意图解析器**。

在 STT 与 Intent Orchestrator 之间插入 **Text Normalization / Semantic Correction** 层——即 §2.3 所说的「上下文缓冲带」：

```text
STT 原始文本（含 luck、viian）
        │
        ▼
┌───────────────────────────────────────┐
│  上下文缓冲带（Semantic Correction）     │
│  输入：裸文本 + 已知应用列表 + 联系人名单  │
│  输出：纠错后标准文本 + corrected_entities │
└───────────────────────────────────────┘
        │
        ▼
Intent Orchestrator（只吃「干净文本」）
```

**推荐实现：轻量 LLM 纠错**

- 使用 **极快的小模型**（或单次短 prompt 调用），延迟目标 **< 500ms**；
- System Prompt 示例（人话版）：

> 你是语音纠错助手。用户说的是语音识别结果，可能有同音错字。  
> 当前常用软件：[Lark, Codex, Calculator, Chrome, VS Code, …]  
> 常见联系人：[Vivian, …]  
> 请修正文本中的软件名、人名，保留原意；若无法确定，标注 `needs_clarify`。

- 输入：`帮我打开 luck 给 viian 发一条消息内容是我今天要睡觉`
- 输出：`帮我打开 Lark 给 Vivian 发一条消息内容是我今天要睡觉`  
  + `corrected_entities: [{raw:"luck", corrected:"Lark"}, {raw:"viian", corrected:"Vivian"}]`

**为什么 LLM 适合做这一层（而 STT 不适合）**：

| | STT | LLM 纠错层 |
|---|-----|-----------|
| 驱动方式 | 声学概率 | **语义 + 领域词表** |
| 是否知道 Lark 在系统里 | 否 | **是（注入词表）** |
| 能否结合「发消息」猜 IM 软件 | 否 | **是** |

**与规则纠错的关系（双轨，不互斥）**：

- **LLM 主路径**：泛化好，能处理「打开那个发消息的软件」这类间接说法；
- **规则辅路径**（§4.3）：LLM 关闭、超时或低置信时，用编辑距离 + 拼音兜底；
- **交叉校验**：LLM 纠错结果与规则候选不一致时 → 降置信度或触发澄清，禁止静默执行。

**日志要求**：Router Evidence 必须同时保留 `stt_raw`、`buffer_corrected`、`corrected_entities[]`、`suspect_tokens[]`。

**多轮上下文辅助（新）**：缓冲带在纠错时应 **额外参考** 本次会话的近 5 条历史消息，提取已出现过的实体：

> 如果历史里已有 `Lark`、`Vivian`，本轮说出 `luck`/`viian` 时，置信度直接提升，可静默纠错而不需澄清。

**Domain Lexicon（领域词典）**：与缓冲带共用 SSOT，维护：

- 应用别名：luck / lark / lock → Lark（飞书）；按 **用户常用应用** 排序；
- 联系人：viian → Vivian（显示名、英文名、拼音）；
- 项目名 / 常用 IM 名。

音近归一化规则：`luck/lark/lock` 在「发消息」语境下 **优先映射到飞书/Lark**，而非盲目打开本地 exe。

**Domain Lexicon 维护策略**：

| 来源 | 内容 | 更新时机 |
|------|------|----------|
| 系统自动扫描 | 已安装应用列表 | 启动 / 每日 |
| 飞书通讯录同步 | 联系人显示名、英文名 | 登录后 / 小时级 |
| 用户手动配置 | 自定义别名（如「那个文档」→ Notion） | 用户设置 |
| **纠错反馈学习（重要）** | 用户纠正「我说的是 X 不是 Y」→ 记录 luck→Lark 等映射 | 实时 |

Lexicon 不应是只读静态文件——**每次用户确认纠错就是一条学习样本**（见 §4.10）。

---

### 4.3 防线二补充：规则实体纠错（缓冲带兜底）

当 LLM 不可用、超时或需交叉校验时，用 **确定性规则** 完成同类工作（与 §4.2 并行，不替代）：

```text
STT 原始文本
    ↓ 1. 句型修复（截断、重复词）
    ↓ 2. 应用名：编辑距离 + 拼音 vs 已知应用列表
    ↓ 3. 联系人：编辑距离 + 拼音 vs 联系人名单
    ↓ 4. 常见误识别映射表（luck→lark 等）
    ↓ 输出：纠错文本 + corrected_entities[] + 置信度
```

**纠错置信度分级**：

| 编辑距离 / 相似度 | 处理方式 |
|-----------------|----------|
| 完全匹配 | 直接使用 |
| 编辑距离 ≤ 1 / 相似度 ≥ 0.90 | 静默纠错，回复里说明（§4.6） |
| 编辑距离 2～3 / 相似度 0.70～0.90 | 纠错 + 询问确认 |
| 编辑距离 > 3 / 相似度 < 0.70 | 澄清，不猜测 |

**本案**：`luck → Lark`（编辑距离 1）、`viian → Vivian`（编辑距离 2）均应在进入 IO **之前** 被缓冲带捕获。

---

### 4.4 防线三：工具层模糊匹配（底层防线）

**执行工具不能再做 Exact Match。** 在 `windows_open_app`、`lark_message_send` 等工具的参数解析处引入 **软匹配**：

```text
接到 app_name="luck"
    ↓
遍历已知应用列表（已安装 + 常用别名）
    ↓
编辑距离 / 拼音 / 音标相似度
    ↓
最近候选：Lark（距离 1）→ 自动映射并重试
    ↓（若无合理候选）
上升到 UX 防线（§4.5），不抛 FileNotFoundError
```

联系人同理：`recipients=["viian"]` → 模糊查通讯录 → 候选 Vivian。

**原则**：工具层是 **最后一道实体纠错机会**——即使缓冲带漏了，工具也不应因差一个字母直接崩溃。

---

### 4.5 防线四：优雅降级与用户确认（UX 防线）

当模糊匹配仍无法确定目标时，**Agent 必须拦截底层错误，翻译成人类语言**：

**禁止**：
> 我尝试打开或切换到 luck，但没有完成：failed:FileNotFoundError(2, '系统找不到指定的文件。', None, 2, None)。

**应当**：
> 我没有在电脑上找到叫做 luck 的软件。您是想打开 **Lark（飞书）** 吗？

联系人：
> 通讯录里没有找到 viian，您是要发给 **Vivian** 吗？

这不仅掩盖识别错误，还给了用户 **纠正机会**——是语音 Agent 体验的分水岭。

**external 操作（发消息）额外规则**：

- **轻确认**：低置信或存在 suspect_tokens 时，执行前先问一句：  
  > 「我理解为：在飞书给 Vivian 发『我今天要睡觉』，对吗？」
- 即使自动纠错成功，也应 **先说出理解再执行**（§4.7），避免静默发错人。

**「何时问、何时静默执行」决策表**（见 §4.6）：

| 情形 | 处理方式 |
|------|---------|
| 实体完全匹配 + 无 suspect | 静默执行，主动告知 |
| suspect_tokens 存在 + 编辑距离 ≤ 1 + local 操作 | 静默纠错 + 回复里说出 |
| suspect_tokens 存在 + external 操作（发消息/邮件） | **必须先确认** |
| 会话历史已出现相同实体 | 置信度提升 → 可降级为静默纠错 |
| 纠错候选 ≥ 2 个相近 | 列出候选让用户选，不猜 |
| 编辑距离 > 3 且无历史支撑 | 直接澄清「我没听清 X，是指？」 |

---

### 4.6 第二层补强：意图路由（复合任务 + 槽位强校验）

缓冲带解决「字写对了」；**意图层** 还要解决「任务判对了、槽位齐了、reasoning 与 route 一致」。

**（1）复合句目标抽取**

对「打开 X + 给 Y 发消息 + 内容是 Z」类句子，**先做目标抽取，再选工具**：

```text
模式：给 {recipient} 发(一条)?消息(内容(是|为))? {message}
→ 主 task_type = message_send（或 lark_message_send）
→ 打开 X 降为 optional_precursor（可选前置，失败不终止主目标）
```

**（2）任务优先级（手段 < 目的）**

| 优先级 | 动作类型 | 示例 |
|--------|---------|------|
| 高 | 发送 / 创建 / 删除 / 提交 / 购买 | 发消息、建群、删文件 |
| 低 | 打开 / 切换 / 进入 | 打开 Lark、切到某窗口 |

**不能因为句首是「打开」就把整句判成 app_control。**

**（3）槽位强校验（按 task_type 绑定）**

| task_type | 必填槽 | 本案应有 | 日志实际 |
|-----------|--------|---------|---------|
| `message_send` | recipient, message | viian→Vivian, 我今天要睡觉 | **皆空** |
| `app_control` | app_name | luck | 有（但错） |

规则：**消息任务没有 recipient / message → missing_slots 非空 → 禁止 execute**，不能因 `app_name` 有值就放行。

**（4）reasoning ↔ task_type 一致性门禁（Codex，P0）**

当 LLM reasoning 给出的 `task_type`（如 `lark_message_send`）与结构化 `route`（如 `windows_open_app`）**不一致**：

```text
→ 一致性 = FAIL
→ 禁止执行当前 route
→ 以 reasoning 中的 task_type 重新路由，或 clarify
```

本案日志 **必须被此门禁拦住**。

**HIDCA**：域标签以 **主任务目的** 为准；发消息 → `WORKSPACE_LARK`，非 `OS_CONTROL`。

---

### 4.7 主动透明：执行时说出纠错与理解

缓冲带或工具层发生纠错时，**回复里必须说明**（尤其 external 操作）：

> 「我把 luck 理解为 Lark（飞书），把 viian 理解为 Vivian。我理解为：在飞书给 Vivian 发『我今天要睡觉』。如果不对请告诉我。」

**副作用预览**：发送前展示 **最终对象 + 内容**；用户应能一眼核对「发给谁、发什么」。

静默纠错 = 用户无法验证系统是否听对，信任无法建立。

**纠错结果的处理**：用户如果回复「对」→ 执行；回复「不对」或「我说的是 X」→ 记录到 Domain Lexicon（见 §4.10），然后以修正后的理解重新执行。

---

### 4.8 第三层：执行闭环 — 失败后二次路由（Execution Recovery）

工具失败 **不是终点**，而是 **改道的触发器**（Codex 第三层核心）：

```text
windows_open_app(luck) → FileNotFoundError
    ↓
Step 1：查 Domain Lexicon 别名 → Lark
    ↓
Step 2：回看原句 — 是否含「给 X 发消息」？
    → 是：改走 lark_message_send（主目标未变）
    → 否：尝试 open_app(lark) 或 clarify
    ↓
Step 3：仍失败 → UX 澄清，禁止透传 FileNotFoundError
```

**关键原则**：

- **手段失败不覆盖目的**：打开窗口失败 ≠ 发消息任务失败；
- **Recovery 最多 1～2 次换策略**（见 `080-jachin-execution-resilience`），然后 Brief 或 clarify；
- Observation 写入 `[StrategyShift]`，Router Evidence 记 `recovery_attempts[]`。

---

### 4.9 延迟预算与性能约束

加了多层处理，语音响应会不会变慢？这是必须正视的工程问题。

**各层延迟估算**：

| 层 | 预期延迟 | 说明 |
|----|---------|------|
| STT 识别 | 已有（不变） | 基线 |
| suspect_tokens 生成 | < 5ms | 规则扫描，几乎无感 |
| 规则纠错（编辑距离） | < 20ms | 确定性算法 |
| **LLM 缓冲带纠错** | **150～500ms** | 主要新增延迟 |
| 多轮历史查询 | < 10ms | 内存查 |
| IO 槽位校验 + 一致性门禁 | < 30ms | 规则检查 |
| 合计新增 | **约 200～600ms** | 视 LLM 响应速度 |

**可接受原则**：语音交互「感知到的响应延迟」< 1 秒。LLM 缓冲带 < 500ms 是硬约束。

**超时降级策略**（LLM 缓冲带超时时）：

```text
LLM 缓冲带超时 / 不可用
    ↓
自动切换到规则纠错（编辑距离 + Lexicon，<20ms）
    ↓
置信度标记降级（因 LLM 未确认）
    ↓
external 操作：强制走轻确认路径，不直接执行
```

**优化方向（远期）**：对高频用户的 Lexicon 做预热缓存，可将 LLM 层命中率降低甚至绕过。

---

### 4.10 用户反馈学习闭环（个性化 Domain Lexicon）

这是让系统 **越用越聪明** 的关键——目前缺失。

**触发时机**：

| 用户说了什么 | 系统提取的样本 |
|-------------|--------------|
| 「不是，我说的是 Lark」 | `luck → Lark`，写入个人 Lexicon |
| 「我说的是 Vivian，不是 viian」 | `viian → Vivian`，写入联系人别名 |
| 轻确认中点击「是 Lark」 | 隐式确认：`luck → Lark`，频次 +1 |
| 每次成功纠错后用户没有否认 | 弱正向信号：置信度微调 |

**个性化优先级**：个人 Lexicon > 系统默认 Lexicon。比如甲的常用 app 是 Lark，乙的可能是 Slack——`luck` 对甲映射 Lark，对乙应映射 Slack。

**存储与隐私**：个人 Lexicon 存在本地（或用户账号绑定），不跨用户共享；支持用户查看和删除。

**防止污染**：单次纠错不立即生效（权重低），需要 **3 次以上同向确认** 才写入稳定映射；防止误操作污染词典。

---

### 4.11 整体架构改造图

```text
用户语音
    │
    ▼
[防线① STT + 热词表]
    │  raw_text + normalized_text + suspect_tokens
    ▼
[防线② 上下文缓冲带]                    ← LLM 纠错（<500ms）/ 超时降规则
    │  + 多轮历史实体 + Domain Lexicon    ← 个人 Lexicon 优先
    │  buffer_corrected / corrected_entities / 置信度
    ▼
[「何时问、何时静默」决策]               ← 见 §4.5 决策表
    │  ← external 操作 → 轻确认（继续下面流程）
    ▼
[第二层：意图路由]
    │  复合句目标抽取 → 主 task_type
    │  槽位强校验（message 必有 recipient+message）
    │  reasoning ↔ task_type 一致性门禁
    ▼
[HIDCA 域判断]                          ← 以主任务目的为准
    │
    ▼
[防线③ 工具层模糊匹配]
    │
    ▼（失败）
[第三层：Execution Recovery]            ← 别名 → 改道发消息 → clarify
    │
    ▼
[防线④ 副作用预览 + 主动透明回复]
    │
    ▼（用户确认纠错 / 否认）
[§4.10 反馈学习]                        ← 写回个人 Domain Lexicon
```

---

## 5. 快速路径（Fast Path）在 STT 场景下需要收紧

`VOICE_OS_TOOL_INTENT_ROUTING_PROPOSAL.md §5.10` 定义了「显式信号快速路径」：置信度 ≥ 0.80 时跳过多候选排序，直接执行。

> **命名说明**：本文中 `Codex` 一词（如「Codex→Lark 办公流」）专指 Jachin 系统内的 **Codex 工作流工具**（项目简报 / 多维表模板），与本文引用的"AI 建议方 Codex"无关，两者不要混淆。AI 建议来源已统一改为「核心设计原则」。

**语音 STT 输入时，快速路径必须增加前置条件**——否则会出现「缓冲带未纠错 + 高置信度 = 自信地做错事」（本案即如此）：

| 条件 | 键盘输入 | 语音 STT 输入 |
|------|---------|--------------|
| 快速路径触发置信阈值 | ≥ 0.80 | ≥ 0.85（更严） |
| **必须经过上下文缓冲带** | 可选 | **必须**（§4.2） |
| 核心实体已纠错或 `suspect_tokens` 已处理 | 不需要 | **必须** |
| **槽位强校验通过**（按 task_type） | 必须 | **必须**（message 任务缺 recipient/message 禁止 execute） |
| reasoning 与 route / task_type 一致 | 必须 | **必须** |
| external 操作直接执行 | 视置信度 | **轻确认或先说出理解** |

**本案教训**：IO 置信 0.9 触发 execute，但 `luck` / `viian` 未经缓冲带 → 快速路径成了 **加速失败** 的通道。

---

## 6. 评测集扩充

基于本次案例，在路由评测集里增加以下类别：

### 6.1 新增分类：STT 噪声容错

| 原始 STT 文本 | 预期理解 | 预期工具 |
|--------------|---------|---------|
| 「打开 luck 发消息」 | Lark 发消息 | `mcp:lark_message_send` |
| 「发给 viian」 | 发给 Vivian | recipients=[Vivian] |
| 「打开 clone 浏览器」 | 打开 Chrome | `mcp:windows_open_app(chrome)` |
| 「搜 notion note」 | 搜 Notion | target=notion |
| 「打开 WS Code」 | 打开 VS Code | `mcp:windows_open_app(vscode)` |
| **「打开 luck 给 viian 发消息内容是…」** | **主任务=发消息**，非 app_control | `lark_message_send`；打开仅为可选前置 |
| **reasoning 与 route 冲突** | 应拦截，不 execute | consistency=FAIL |

样本 schema 应同时记录 `stt_raw`、`buffer_corrected`、`suspect_tokens`；断言 **用户回复中不得出现** `FileNotFoundError`；复合句用例断言 **手段失败后可 recovery 到发消息**。

### 6.2 新增验收指标

| 指标 | 目标 |
|------|------|
| 单字符编辑距离内应用名纠错成功率 | ≥ 90% |
| 双字符编辑距离内联系人名识别率 | ≥ 80% |
| STT 噪声场景下原始错误信息透传用户 | **0 次**（应替换为友好降级） |
| STT 场景 external 操作无告知静默执行 | **0 次** |
| 复合句误判为 app_control（主目标为 send） | **0 次** |
| message 任务缺 recipient/message 仍 execute | **0 次** |
| reasoning↔route 冲突仍 execute | **0 次** |
| 工具 not_found 后成功 recovery 或 clarify | ≥ 80%（Eval 子集） |

---

## 7. 实施阶段

| 阶段 | 内容 | 对应防线 | 优先级 |
|------|------|----------|--------|
| **Phase A — UX 止血** | 工具 not_found / 联系人找不到 → 友好澄清；**禁止** FileNotFoundError 透传 | ④ UX | P0 |
| **Phase B — 工具模糊匹配** | `windows_open_app`、联系人解析引入编辑距离/拼音软匹配 | ③ 底层 | P0 |
| **Phase C — 上下文缓冲带（LLM）** | STT 与 IO 之间加语义纠错层；Router Evidence 记 stt_raw / buffer_corrected | ② 大脑 | **P0** |
| **Phase D — 规则纠错兜底** | LLM 不可用时的编辑距离 + 映射表；与 LLM 交叉校验 | ② 补充 | P0 |
| **Phase E — 意图路由补强** | 复合句目标抽取；任务优先级；槽位强校验；reasoning↔task_type 门禁 | 第二层 | P0 |
| **Phase F — Execution Recovery** | not_found → 别名 → 改道发消息 → clarify；`[StrategyShift]` 日志 | 第三层 | P0 |
| **Phase G — suspect_tokens + Lexicon** | STT 输出结构升级；Domain Lexicon SSOT（含个人词典初版） | 第一层 | P0 |
| **Phase H — STT 热词注入** | 应用列表、联系人同步到 ASR 热词表 | 第一层 | P1 |
| **Phase I — 多轮上下文实体继承** | 缓冲带读取近 5 轮历史实体；置信度提升逻辑 | 第一层 | P1 |
| **Phase J — Fast Path 收紧** | STT 必须过缓冲带 + 槽位校验 + 一致性门禁 | 全链路 | P1 |
| **Phase K — 轻确认与副作用预览** | 低置信语音轻确认；发送前展示对象+内容；「何时问」决策表 | 第三层 | P1 |
| **Phase L — 延迟监控** | 缓冲带超时降级策略；各层延迟埋点 | 性能 | P1 |
| **Phase M — 反馈学习闭环** | 用户纠错 → 写回个人 Lexicon；3 次确认写入稳定映射 | 第三层 | P2 |
| **Phase N — 评测集** | STT 噪声 + 复合句 + recovery + 多轮上下文；PR 门禁 | — | P2 |

**落地建议**：  
**P0 先做 A + B + C + E + F + G**——止血、缓冲带、路由判对主任务、槽位校验、失败后改道；本案 luck/viian 类故障的 **主路径** 在此。热词（Phase H）和多轮上下文（Phase I）作为增量优化，不阻塞核心链路。

---

## 8. 代码与文档锚点

| 主题 | 路径 |
|------|------|
| STT 管道 | `clients/desktop/src/voice/` |
| **上下文缓冲带（待建）** | STT 与 `agent_preflight` / IO 之间；可复用 `semantic_intent_engine` 轻量调用模式；超时降级到规则纠错 |
| **Domain Lexicon（待建）** | 应用别名、联系人音近映射 SSOT；含个人 Lexicon 存储（本地 / 账号绑定） |
| **多轮实体缓存（待建）** | 会话级实体上下文，供缓冲带置信度提升使用 |
| Intent Orchestrator | `l3_node/agent_preflight.py` |
| 语义槽位解析 | `l3_node/semantic_slot_parser.py` |
| 工具池与工具执行 | `l3_node/primitives/tools/tool_pool.py` |
| OS 应用打开工具 | MCP `windows_open_app` 实现 |
| 联系人查找 | Lark 联系人 MCP 工具 |
| Prompt 组装 | `l3_node/agent_core.py` |
| 意图路由完整方案 | `clients/desktop/docs/VOICE_OS_TOOL_INTENT_ROUTING_PROPOSAL.md` |
| 语音管道说明 | `clients/desktop/docs/VOICE_MODULE_HUMAN_GUIDE.md` |

---

## 9. 小结

**表面现象**：算发 Lark 消息，却去打开不存在的 luck.exe。  
**本质**：**STT 微小抖动 × 全链路把第一版文本当圣旨 × 复合任务判错 × 无 recovery** → 任务全面崩溃。

Agent 变「笨」，不是因为用户没说清楚，而是因为 **让机械的系统 API 直接承接了模糊的自然语音，且中间没有任何一环承认「我可能听错了」**。

| 缺失 | 应对 |
|------|------|
| STT 当事实，无 suspect_tokens | 第一层：热词 + Lexicon + 缓冲带 |
| 会话历史实体未利用 | 第一层：多轮上下文辅助纠错 |
| 「打开」绑架复合句 | 第二层：目标抽取 + 任务优先级 + 槽位强校验 |
| reasoning≠route 仍执行 | 第二层：一致性门禁 |
| 工具失败即终止 | 第三层：Execution Recovery + 轻确认 |
| FileNotFoundError 透传 | 第三层：副作用预览 + 人话澄清 |
| 纠错结果无法学习 | 第三层：用户反馈 → 个人 Lexicon 写回 |

**一句话**：哪怕听成 luck，系统也应能合理猜到——**你大概率是要用 Lark/飞书给 Vivian 发消息**；这需要 **识别、归一化、路由、槽位校验、失败恢复** 全链路都承认不确定性，还要 **越用越懂你**。

**四道防线 vs 三层能力**：四道防线管「在哪加减震」；三层能力管「每段链路该有什么职责」——两者互补，合并落地，不是二选一。

---

## 修订记录

| 日期 | 说明 |
|------|------|
| 2026-07-02 | 初稿：基于 luck/viian 案例的 STT 容错与实体纠错分析 |
| 2026-07-02 | v2：整合 Gemini 分析——「输入抖动→执行崩溃」、上下文缓冲带、四道防线（热词/LLM 大脑/工具软匹配/UX 降级）；LLM 前置语义纠错标为 P0 优先落地；实施阶段重排 |
| 2026-07-02 | v3：整合 Codex 分析——「全链路太信第一版文本」、suspect_tokens、Domain Lexicon、复合任务/任务优先级、槽位强校验、reasoning↔route 一致性门禁、Execution Recovery、轻确认与副作用预览；Codex 三层与 Gemini 四道防线对照表 |
| 2026-07-02 | v4：补充遗漏项——多轮上下文实体继承（§2.11）、延迟预算与缓冲带超时降级策略（§4.9）、「何时问何时静默」完整决策表（§4.5 扩展）、用户反馈学习闭环（§4.10）、Domain Lexicon 维护策略与个人词典；整体架构图加入反馈回路；实施阶段补 Phase I/L/M/N；消除「Codex」命名歧义 |
