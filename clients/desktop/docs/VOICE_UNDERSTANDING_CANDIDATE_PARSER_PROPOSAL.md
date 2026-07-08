# 语音理解候选任务解析器方案

> **状态**：设计稿（未编码）  
> **目标**：解决 STT 文本不稳定时，语音任务仍能被正确理解、纠错、确认或安全拒绝的问题。  
> **核心原则**：不要继续为每一种听错结果补一条规则，而是构建“实体优先、候选生成、任务评分、重排确认、反馈学习”的通用语音理解层。  
> **适用范围**：中英混杂命令、专有名词、联系人、应用名、项目名、发消息/打开应用/查找对象等高频桌面语音任务。  
> **关联文档**：`VOICE_STT_ROBUSTNESS_PROPOSAL.md`、`VOICE_INTENT_ROUTING_AND_TASK_ORCHESTRATION.md`、`VOICE_OS_TOOL_INTENT_ROUTING_PROPOSAL.md`

---

## 1. 背景

当前语音链路的主要问题不是单个 ASR 后端“不够聪明”，而是系统把 ASR 输出当成了可靠文本。

真实样本已经证明：

| 用户真实意图 | ASR 可能输出 | 问题 |
|---|---|---|
| 找到 Vivian | `找到威廉` / `赵刀威廉` / `找到里面` | 实体错，甚至意图触发词也错 |
| 打开 Lark | `打开LUCK` / `帮我打开LUCK` | 英文应用名被替成常见英文词 |
| 找到飞书 | `遭到飞蕊` / `他找到匪书` | 中文近音实体错误 |
| 在 Lark 给 Vivian 发消息 | `在LUCK 的威廉发消息` | 应用和联系人同时错 |
| 普通闲聊 | `你说的都是对的` | 被弱拼音误召回成 `find_app Lark` |
| 无意义短音 | `啊` | 被弱拼音误召回成 `open_app Lark` |

如果继续按局部规则修：

```text
赵刀 -> 找到
威廉 -> Vivian
LUCK -> Lark
匪书 -> 飞书
```

短期 demo 会变好，但长期不可扩展。下一次用户换一种说法，仍然会出现新 bug。

因此需要把语音理解从：

```text
先识别关键词 -> 再按关键词解析槽位
```

升级为：

```text
先召回实体候选 -> 生成多个任务候选 -> 综合打分 -> 确认或执行
```

更准确地说，这是一次 **倒置管道（Global Entity First）** 改造：

```text
不要先问“这句话像哪个意图”
而要先问“这句话里最像用户世界里的哪个实体”
```

例如：

```text
ASR: 给我打开碧帘的绘画
```

旧流程会因为 `打开` 先判定为 `open_app`，然后只去应用列表里找 `碧帘`，最终找不到。

新流程应先做全局实体扫描，发现 `碧帘` 在声学/拼音上更像联系人 `Vivian`，于是生成：

```json
{
  "entity_candidates": [
    {"type": "contact", "canonical": "Vivian", "matched_text": "碧帘", "score": 0.8}
  ]
}
```

再由 `contact` 这个实体锚点反推：这更可能是联系人交互任务，而不是打开应用任务。

但后续实验也暴露了一个同样重要的问题：**Global Entity First 只能负责召回，不能直接负责承认任务**。

错误现象：

```text
ASR: 你说的都是对的
弱召回: 你说 ~= 飞书, 说的 ~= VS Code
错误输出: find_app {app: Lark}
```

```text
ASR: 啊
弱召回: 啊 ~= 拉
错误输出: open_app {app: Lark}
```

这不是缺少某条规则，而是架构层少了：

```text
no_task 竞争
任务承认证据门槛
负样本评估
置信度校准
```

因此本方案必须明确区分：

```text
实体召回：可以宽松，宁可多召回候选
任务承认：必须保守，必须允许 no_task 胜出
```

---

## 2. 目标

### 2.1 产品目标

系统不要求 STT 字面结果永远正确，但要求最终任务理解尽可能正确。

例如：

```text
ASR: 赵刀威廉
```

系统应该能生成：

```json
{
  "intent": "find_contact",
  "slots": {
    "contact": "Vivian"
  },
  "confidence": 0.76,
  "needs_confirmation": true
}
```

而不是停在：

```text
intent=unknown
```

### 2.2 工程目标

1. 减少为单个误识别样本写死规则。
2. 让联系人、应用、项目等实体来自动态 Lexicon，而不是硬编码。
3. 支持多 ASR 结果或 n-best 结果合并。
4. 用任务模板评分替代单一关键词命中。
5. 对低置信度和高风险任务进入确认。
6. 通过用户确认反馈持续学习。

---

## 3. 总体架构

```text
ASR 原文 / 多路 ASR 候选
  ↓
文本标准化
  ↓
Utterance Type / Task Likelihood 判断
  ↓
全局实体锚定（跨联系人 / 应用 / 项目 / 专有名词）
  ↓
动态 Lexicon 槽位召回
  ↓
全量任务候选生成
  ↓
任务模板评分 + no_task 竞争
  ↓
证据门槛 / 置信度校准
  ↓
LLM / 小模型有限候选重排（可选）
  ↓
确认 / 执行 / 拒绝 / 普通对话
  ↓
用户反馈学习
```

核心变化：

```text
不再要求 ASR 文本必须准确命中“找到 / 打开 / 发消息”
而是允许系统从实体和上下文反推可能任务
```

这也意味着解析器不能把用户说法绑定到有限触发词上。用户可能说：

```text
打开 / 找一下 / 帮我切到 / 跟我连一下 / 呼叫 / 看下 / 进入 / 给我弄到
```

这些表达方式写不完。系统应该把它们视为弱信号，而不是唯一入口。

同时，系统也不能把每一句语音都当作任务。语音输入可能是：

```text
任务指令
普通闲聊
用户确认
用户否认
上一轮补充
无意义短音
自言自语
```

所以候选集合里必须有：

```text
no_task / chat / statement
```

所有任务候选都要和 `no_task` 竞争。只有任务证据足够时，系统才可以输出可执行意图。

---

## 4. 关键设计

### 4.1 倒置管道：Global Entity First

工业级方向是：

```text
全局实体锚定 -> 反推可能意图 -> 候选任务评分
```

不要先按初判 intent 去某一个“抽屉”里找实体。

错误做法：

```python
if intent == "open_app":
    candidates = match_in_app_dict(text)
```

这个逻辑的死穴是：一旦 `intent` 初判错，后续全盘皆输。

正确做法：

```text
1. 从全句提取所有可能名词片段
2. 把这些片段同时放进 contacts / apps / projects / custom_terms 里召回
3. 得到跨槽位 entity_candidates
4. 再用 entity type、动作弱信号、上下文和历史反馈生成任务候选
```

示例：

```text
ASR: 给我打开碧帘的绘画
```

全局实体锚定：

```json
[
  {
    "type": "contact",
    "canonical": "Vivian",
    "matched_text": "碧帘",
    "score": 0.8,
    "evidence": ["phonetic_similarity", "contact_lexicon"]
  }
]
```

候选任务：

```json
[
  {
    "intent": "contact_interaction",
    "slots": {"contact": "Vivian"},
    "score": 0.74,
    "reasons": ["contact_entity_anchor", "open_like_action"]
  },
  {
    "intent": "open_app",
    "slots": {},
    "score": 0.16,
    "reasons": ["open_like_action", "no_app_entity"]
  }
]
```

注意：即使 `打开` 是应用动作词，也不能让它一票否决联系人候选。`打开 Vivian 的绘画` 很可能是 `打开 Vivian 的会话` 或 `查看 Vivian 相关内容` 的错听，需要交给候选任务评分和有限重排处理。

### 4.2 召回不等于任务承认

Global Entity First 是 **召回策略**，不是 **任务承认策略**。

召回阶段应该回答：

```text
这句话里有哪些片段可能像用户词库里的实体？
```

任务承认阶段应该回答：

```text
这些实体候选是否足以证明用户正在请求一个任务？
```

这两个阶段不能混在一起。

反例：

```text
ASR: 你说的都是对的
```

全局扫描可能弱召回：

```json
[
  {"type": "app", "canonical": "Lark", "matched_text": "你说", "score": 0.66},
  {"type": "app", "canonical": "VS Code", "matched_text": "说的", "score": 0.66}
]
```

这类候选只能说明“有一个音近片段”，不能说明用户要打开或查找应用。正确结果应该是：

```json
{
  "selected": {
    "intent": "no_task",
    "confidence": 0.92
  },
  "entity_candidates": [],
  "task_candidates": []
}
```

核心原则：

```text
弱实体召回不能单独触发任务
```

### 4.3 Utterance Type / Task Likelihood

在任务候选生成之前，应先判断这一句是否像任务请求。

输出示例：

```json
{
  "utterance_type": "chat_or_statement",
  "task_likelihood": 0.08,
  "reasons": ["no_action_evidence", "no_strong_entity", "statement_like_text"]
}
```

或：

```json
{
  "utterance_type": "task_request",
  "task_likelihood": 0.86,
  "reasons": ["explicit_action", "strong_entity_anchor"]
}
```

Task likelihood 的信号来源：

| 信号 | 说明 |
|---|---|
| 明确动作证据 | `打开`、`找一下`、`发消息`、`内容是` |
| 强实体证据 | 精确命中 `Lark`、`Vivian`、`飞书` |
| 结构证据 | `给 X 发消息`、`打开 X`、`找到 X` |
| 上下文证据 | 当前正在语音命令模式、上一轮在确认任务 |
| 负向闲聊证据 | `你说的对`、`谢谢`、`不用了`、`不是这个` |
| 无意义短音 | `啊`、`嗯`、空音频、截断音 |

如果 `task_likelihood` 很低，则直接输出：

```text
no_task
```

不进入执行候选。

### 4.4 no_task 竞争

候选集合里必须永远包含 `no_task`。

示例：

```json
{
  "candidates": [
    {
      "intent": "no_task",
      "score": 0.92,
      "reasons": ["statement_like", "weak_entity_only"]
    },
    {
      "intent": "find_app",
      "slots": {"app": "Lark"},
      "score": 0.41,
      "reasons": ["weak_entity_phonetic_only"]
    }
  ],
  "selected": "no_task"
}
```

`no_task` 应该在以下情况获得高分：

| 场景 | 示例 |
|---|---|
| 普通陈述 | `你说的都是对的` |
| 反馈/确认 | `是的`、`对`、`不对` |
| 礼貌用语 | `谢谢`、`不用了` |
| 无意义短音 | `啊`、`嗯` |
| 只有弱实体音近 | `你说` 弱像 `飞书` |
| 没有动作、没有结构、没有强实体 | 任意普通句 |

### 4.5 任务承认证据门槛

任务候选必须满足最小证据组合，不能只靠一个弱实体相似度。

决策矩阵：

| 实体证据 | 动作证据 | 上下文证据 | 决策 |
|---|---|---|---|
| 弱 | 无 | 无 | `no_task` |
| 弱 | 弱 | 无 | `no_task` 或低置信确认 |
| 弱 | 强 | 无 | 任务候选，必须确认 |
| 强 | 无 | 无 | 短命令可候选，否则 `no_task` |
| 强 | 强 | 无 | 任务候选 |
| 强 | 弱 | 强 | 任务候选，确认 |
| 多实体强 | 强 | 有 | 高置信任务 |

证据分级：

| 证据级别 | 示例 | 是否可单独触发任务 |
|---|---|---|
| 强实体 | 精确命中 `Lark` / `Vivian` / `飞书` | 短命令可触发 |
| 中实体 | 用户历史确认过的混淆 | 可触发，但多半确认 |
| 弱实体 | 纯拼音/音素相似，如 `你说 ~= 飞书` | 不可单独触发 |
| 强动作 | `打开` / `发消息` / `内容是` | 可与实体共同触发 |
| 弱动作 | 音近动作、模糊动作 | 不能单独触发 |
| 强结构 | `给 X 发消息` | 可强力加分 |

因此：

```text
弱实体 + 无动作 = no_task
弱实体 + 强动作 = 低置信任务，必须确认
强实体 + 无动作 + 短句 = 候选任务，通常确认
强实体 + 强动作 = 高置信任务
```

### 4.6 实体优先

当前做法容易失败：

```text
先判断 intent
再按 intent 找 entity
```

因为一旦 `找到` 被听成 `赵刀`，intent 就变成 `unknown`。

新方案改为：

```text
先从 ASR 文本召回所有可能实体
再根据实体类型反推任务
```

示例：

```text
ASR: 赵刀威廉
```

实体候选：

```json
[
  {
    "type": "contact",
    "canonical": "Vivian",
    "matched_text": "威廉",
    "score": 0.68
  }
]
```

任务候选：

```json
[
  {
    "intent": "find_contact",
    "slots": {"contact": "Vivian"},
    "score": 0.72
  },
  {
    "intent": "send_message",
    "slots": {"contact": "Vivian"},
    "score": 0.31
  }
]
```

### 4.7 动态 Lexicon

Lexicon 不应只是一组手写规则，而应来自用户真实上下文。

来源包括：

| 类型 | 来源 |
|---|---|
| 应用 | 系统已安装应用、常用应用、历史打开记录 |
| 联系人 | Lark/飞书联系人、通讯录、历史消息对象 |
| 项目 | 工作区名称、最近文件夹、任务系统项目 |
| 专有名词 | 用户手动添加、历史确认学习、组织词库 |

Lexicon 示例：

```json
{
  "apps": {
    "Lark": {
      "aliases": ["lark", "luck", "lock", "飞书", "拉克"],
      "active": true
    }
  },
  "contacts": {
    "Vivian": {
      "aliases": ["vivian", "vivi", "薇薇安", "微微安"],
      "active": true
    }
  }
}
```

注意：这里的 aliases 不是最终形态。长期应该从拼音、音素、历史 ASR 混淆、用户反馈中自动生成。

### 4.8 全局拼音 / 声学模糊扫描

Gemini 方案里最值得吸收的一点是：**跨槽位全局召回**。

拿到 STT 文本后，不应该先按意图选择某一个词库，而应对全句做候选片段扫描。

候选片段来源：

| 片段来源 | 示例 |
|---|---|
| 连续中文短片段 | `碧帘`、`威廉`、`飞蕊` |
| 英文 / 字母片段 | `LUCK`、`VIVI` |
| 中英混杂片段 | `v 薇 m` |
| 动词后片段 | `打开 ____`、`找到 ____` |
| 介词/结构后片段 | `给 ____ 发消息`、`的 ____` |

每个片段同时进入所有实体池：

```text
contacts
apps
projects
custom_terms
```

匹配方式：

| 方法 | 作用 |
|---|---|
| 字符编辑距离 | 处理 `LUCK` / `Lark` |
| 拼音编辑距离 | 处理 `碧帘` / `Vivian`、`匪书` / `飞书` |
| 音素 / 发音近似 | 处理中英混杂和口音 |
| 历史混淆权重 | 处理用户反复出现的个人发音 |
| 热词命中权重 | 结合 ASR hotwords 结果 |

输出不是“强制替换后的文本”，而是候选：

```json
{
  "span": "碧帘",
  "candidates": [
    {
      "type": "contact",
      "canonical": "Vivian",
      "score": 0.8,
      "evidence": ["pinyin_similarity"]
    },
    {
      "type": "project",
      "canonical": "BilianProject",
      "score": 0.42,
      "evidence": ["character_similarity"]
    }
  ]
}
```

不建议采用：

```text
发现 best_match 后立刻强制 text.replace(...)
```

原因是这会造成误纠。正确做法是把 `best_match` 送入后续任务候选评分，只有当最终候选胜出并且风险允许时，才在结构化输出里使用 canonical entity。

### 4.9 槽位召回

槽位召回不直接改文本，只产出候选。

输入：

```text
赵刀威廉
```

输出：

```json
{
  "entity_candidates": [
    {
      "type": "contact",
      "canonical": "Vivian",
      "evidence": ["single_contact_context", "phonetic_similarity"],
      "score": 0.68
    }
  ]
}
```

召回信号包括：

| 信号 | 说明 |
|---|---|
| 精确命中 | 文本中直接出现 canonical 或 alias |
| 模糊匹配 | 编辑距离、字符相似度 |
| 拼音相似 | 中文近音，如 `匪书` 与 `飞书` |
| 英文近音 | `luck/lock` 与 `Lark` |
| 类型上下文 | 短命令 + 唯一联系人 |
| 历史确认 | 用户曾确认过类似文本 |

### 4.10 靠实体反推意图

不要只生成一个任务。应根据实体候选和文本线索生成多个候选。

实体类型是非常强的路由信号：

| 命中的实体类型 | 倾向任务 |
|---|---|
| contact | `find_contact` / `contact_interaction` / `send_message` |
| app | `open_app` / `app_control` |
| project | `open_project` / `summarize_project` / `search_project` |
| file/document | `open_file` / `summarize_file` |

示例：

```text
ASR: 给我打开碧帘的绘画
Entity: Vivian(contact)
```

此时 `打开` 不应直接锁死为 `open_app`。更合理的候选是：

```json
[
  {
    "intent": "contact_interaction",
    "slots": {"contact": "Vivian"},
    "score": 0.74,
    "needs_confirmation": true
  },
  {
    "intent": "open_app",
    "slots": {},
    "score": 0.16
  }
]
```

如果最终任务语义仍不清楚，可以进入 LLM / 小模型重排或询问：

```text
你是要打开和 Vivian 的聊天会话吗？
```

### 4.11 全量任务候选生成

不要只生成一个任务。应根据实体候选和文本线索生成多个候选。

示例：

```text
ASR: 赵刀威廉
```

候选：

```json
[
  {
    "intent": "find_contact",
    "slots": {"contact": "Vivian"},
    "score": 0.72,
    "reasons": ["contact_entity", "short_command"]
  },
  {
    "intent": "send_message",
    "slots": {"contact": "Vivian"},
    "score": 0.31,
    "reasons": ["contact_entity", "missing_message_action"]
  },
  {
    "intent": "open_app",
    "slots": {},
    "score": 0.05,
    "reasons": ["no_app_entity"]
  }
]
```

### 4.12 任务模板评分

任务模板不再依赖单个关键词，而是综合多个特征打分。

#### open_app

成立条件：

```text
需要 app 候选
```

加分信号：

| 信号 | 示例 |
|---|---|
| app 实体命中 | Lark / Chrome / VS Code |
| 打开类动作近似 | 打开 / 启动 / 进入 / 用一下 |
| 短命令结构 | `打开LUCK` |
| 历史偏好 | 用户常打开该应用 |

#### find_contact

成立条件：

```text
需要 contact 候选
```

加分信号：

| 信号 | 示例 |
|---|---|
| contact 实体命中 | Vivian |
| 查找类动作近似 | 找 / 查 / 看 / 联系 |
| 短命令结构 | `赵刀威廉` |
| 当前域 | 用户在联系人/消息上下文中 |

#### send_message

成立条件：

```text
需要 contact 候选
可选 app 候选
需要发消息语义或 message_content
```

加分信号：

| 信号 | 示例 |
|---|---|
| contact 实体 | Vivian |
| app 实体 | Lark |
| 发送类动作 | 发消息 / 发信息 / 内容是 |
| 正文槽位 | `内容是今晚吃什么` |

安全策略：

```text
send_message 永远需要确认，除非未来有明确的可信自动执行策略。
```

#### contact_interaction

这是 Gemini 方案里“命中联系人后进入社交/通讯意图”的安全版本。

成立条件：

```text
需要 contact 候选
```

加分信号：

| 信号 | 示例 |
|---|---|
| contact 实体锚点 | Vivian |
| 会话类动作 | 打开 / 看下 / 联系 / 呼叫 / 跟我连一下 |
| 消息上下文 | 当前使用 Lark/飞书 |
| 历史行为 | 用户常打开该联系人会话 |

降分信号：

| 信号 | 示例 |
|---|---|
| 明确消息正文 | 更可能是 `send_message` |
| 明确拨打语义 | 更可能是 `call_contact` |
| 明确文件/项目对象 | 可能不是联系人任务 |

安全策略：

```text
contact_interaction 可以打开会话，但发消息、拨打电话、删除记录等动作必须确认。
```

### 4.13 多 ASR 候选合并

不要把单个 STT 文本当真相。

候选来源：

```text
SenseVoice result
Zipformer result
Zipformer hotword result
未来 ASR n-best
未来云端 ASR fallback
```

合并示例：

```json
{
  "asr_texts": [
    {"engine": "zipformer", "text": "赵刀威廉", "weight": 1.0},
    {"engine": "zipformer_hotword", "text": "赵刀威廉", "weight": 1.0},
    {"engine": "sensevoice", "text": "找到威廉", "weight": 0.9}
  ]
}
```

解析器在多个文本上同时召回实体和任务候选，然后合并评分。

### 4.14 LLM / 小模型有限重排

LLM 不应自由改写用户文本。

LLM 只接收结构化候选，并且只能在候选中选择或拒绝。

输入：

```json
{
  "asr_texts": ["赵刀威廉", "找到威廉"],
  "entity_candidates": [
    {"type": "contact", "name": "Vivian", "score": 0.68}
  ],
  "task_candidates": [
    {"intent": "find_contact", "slots": {"contact": "Vivian"}, "score": 0.72},
    {"intent": "send_message", "slots": {"contact": "Vivian"}, "score": 0.31}
  ]
}
```

输出：

```json
{
  "selected_intent": "find_contact",
  "selected_slots": {"contact": "Vivian"},
  "confidence": 0.78,
  "needs_confirmation": true,
  "reason": "The contact candidate is strong, but the action word is noisy."
}
```

禁止：

```text
让 LLM 在没有候选约束时自由猜联系人、应用或正文。
```

Gemini 方案把 LLM 称作“语义洗衣机”，这个方向可以吸收，但必须加边界：

```text
LLM 只能重排候选，不能凭空创造候选。
LLM 不能直接决定高风险动作静默执行。
LLM 输出必须是结构化 JSON，并且带 confidence / needs_confirmation。
```

例如：

```text
用户指令：给我打开 Vivian 的绘画
已知实体：Vivian 是联系人
候选任务：
1. contact_interaction: 打开 Vivian 会话
2. find_contact: 查找 Vivian
3. open_app: 打开应用
```

LLM 可以选择：

```json
{
  "selected_intent": "contact_interaction",
  "selected_slots": {"contact": "Vivian"},
  "confidence": 0.71,
  "needs_confirmation": true,
  "reason": "The word after Vivian is noisy, but contact_interaction is more plausible than open_app."
}
```

### 4.15 确认策略

确认不是失败，而是安全机制。

需要确认的情况：

| 场景 | 策略 |
|---|---|
| 低置信度纠错 | 问用户确认 |
| 多候选接近 | 给出 2 到 3 个选项 |
| 高风险动作 | 必须确认 |
| 消息发送 | 默认确认 |
| 删除/提交/付款 | 强确认或拒绝自动执行 |

示例：

```text
你是要找 Vivian 吗？
```

或：

```text
你是要在 Lark 给 Vivian 发消息：“今晚吃什么” 吗？
```

### 4.16 反馈学习

用户确认后，不应丢弃这次信息。

记录：

```json
{
  "asr_text": "赵刀威廉",
  "resolved_intent": "find_contact",
  "resolved_slots": {"contact": "Vivian"},
  "context": {
    "task_family": "contact_lookup",
    "source": "voice",
    "engine": "zipformer"
  },
  "confirmed_at": "2026-07-06T00:00:00Z"
}
```

下一次类似输入可以提高：

```text
find_contact + Vivian
```

的候选分，而不是让工程师再写规则。

### 4.17 对 Gemini 方案的取舍

采纳：

| 建议 | 处理方式 |
|---|---|
| 倒置管道 / Global Entity First | 作为主架构采纳 |
| 全局拼音/声学扫描 | 作为跨槽位实体召回层采纳 |
| 靠实体反推意图 | 作为候选任务生成的重要信号采纳 |
| LLM 兜底理解 | 改为“有限候选重排”，只在候选集合内选择 |
| 全局实体召回 | 仅作为召回层采纳，不作为任务承认依据 |

不直接采纳：

| 建议 | 原因 | 替代方案 |
|---|---|---|
| 找到 best_match 后强制替换文本 | 容易误纠普通文本或正文 | 输出 entity_candidates，由任务评分决定是否使用 |
| 命中联系人后直接覆盖 intent | 可能把“打开 Vivian 的文件”误判为联系人会话 | 生成多个任务候选并打分 |
| 让 LLM 自由解释“真实意图” | 可能幻觉联系人、正文或动作 | LLM 只能在结构化候选中选择或拒绝 |
| 在测试脚本里继续补局部 if/else | 仍然会陷入规则无底洞 | 新增独立候选解析器实验层 |
| 弱实体召回后直接生成执行任务 | 会把闲聊误触发为任务 | 必须经过 no_task 竞争和证据门槛 |

---

## 5. 输出协议

候选任务解析器的输出应是结构化结果，而不是只返回 corrected_text。

建议结构：

```json
{
  "asr_texts": [
    {"engine": "zipformer", "text": "赵刀威廉"}
  ],
  "selected": {
    "intent": "find_contact",
    "slots": {
      "contact": "Vivian"
    },
    "confidence": 0.76,
    "needs_confirmation": true
  },
  "alternatives": [
    {
      "intent": "send_message",
      "slots": {"contact": "Vivian"},
      "confidence": 0.31
    }
  ],
  "entity_candidates": [
    {
      "type": "contact",
      "canonical": "Vivian",
      "matched_text": "威廉",
      "span": [2, 4],
      "score": 0.68,
      "evidence": ["global_fuzzy_scan", "contact_lexicon"]
    }
  ],
  "debug": {
    "reason": "contact candidate found; action word noisy; selected find_contact by short-command template"
  }
}
```

普通闲聊 / 无任务输入的输出示例：

```json
{
  "asr_texts": [
    {"engine": "zipformer", "text": "你说的都是对的"}
  ],
  "utterance": {
    "type": "chat_or_statement",
    "task_likelihood": 0.08
  },
  "selected": {
    "intent": "no_task",
    "confidence": 0.92,
    "needs_confirmation": false
  },
  "alternatives": [],
  "entity_candidates": [],
  "debug": {
    "reason": "weak phonetic entity candidates were rejected; no task evidence"
  }
}
```

无意义短音 / 截断音输出示例：

```json
{
  "asr_texts": [
    {"engine": "zipformer", "text": "啊"}
  ],
  "utterance": {
    "type": "non_task_audio",
    "task_likelihood": 0.02
  },
  "selected": {
    "intent": "no_task",
    "confidence": 0.98
  },
  "entity_candidates": [],
  "debug": {
    "reason": "too short; no strong entity; no explicit action"
  }
}
```

---

## 6. 评估方式

不要只评估 ASR 字面准确率。

应评估：

```text
音频 -> ASR 候选 -> 候选任务解析 -> 最终 intent/slots
```

### 6.1 指标

| 指标 | 含义 |
|---|---|
| raw_entity_hit | ASR 原文是否直接命中实体 |
| resolved_entity_hit | 解析器最终是否命中实体 |
| intent_hit | 最终 intent 是否正确 |
| slot_hit | 关键槽位是否正确 |
| confirmation_hit | 需要确认的任务是否进入确认 |
| false_correction_rate | 不该纠的文本是否被误纠 |
| false_task_trigger_rate | 闲聊/确认/无意义短音被误触发为任务的比例 |
| no_task_hit | 负样本是否正确输出 `no_task` |
| weak_entity_rejection_rate | 只有弱拼音实体证据时是否被拒绝 |
| unsafe_execution_rate | 高风险任务是否被错误静默执行 |

### 6.2 测试集

继续使用固定录音测试集：

```text
data/eval_wav/stt_entity/
```

每条样本需要 manifest：

```json
{
  "id": "find_vivian_001",
  "wav": "data/eval_wav/stt_entity/find_vivian_001.wav",
  "spoken": "找到 Vivian",
  "expected": {
    "intent": "find_contact",
    "entity": "Vivian"
  }
}
```

必须新增负样本集：

```text
data/eval_wav/stt_negative/
```

负样本示例：

```json
{
  "id": "chat_agree_001",
  "wav": "data/eval_wav/stt_negative/chat_agree_001.wav",
  "spoken": "你说的都是对的",
  "expected": {
    "intent": "no_task"
  }
}
```

负样本类型至少覆盖：

| 类型 | 示例 |
|---|---|
| 普通闲聊 | `你说的都是对的`、`今天挺好的` |
| 用户确认 | `是的`、`对`、`没错` |
| 用户否认 | `不是这个`、`不用了` |
| 礼貌用语 | `谢谢`、`辛苦了` |
| 无意义短音 | `啊`、`嗯`、空录音 |
| 思考/补充 | `我想一下`、`刚才那个方案挺好` |

### 6.3 目标门槛

第一阶段建议门槛：

```text
resolved_entity_hit >= 90%
intent_hit >= 90%
no_task_hit >= 98%
false_task_trigger_rate <= 1%
send_message confirmation_hit = 100%
unsafe_execution_rate = 0%
false_correction_rate <= 2%
```

---

## 7. 落地计划

### Phase 1：实验解析器

新增实验层：

```text
voice_understanding_experiment.py
```

输入：

```text
ASR 文本列表
Lexicon
用户反馈历史
```

输出：

```text
utterance type
task likelihood
global entity candidates
no_task candidate
task candidates
selected intent
slots
confidence
needs_confirmation
alternatives
debug evidence
```

目标：

```text
用现有 8 条正样本录音跑通 Global Entity First、候选生成与任务评分
同时新增负样本录音，验证 no_task 不误触发
```

### Phase 1-B：任务承认与 no_task 校准

新增任务承认层：

```text
Utterance Type / Task Likelihood
No-Task Candidate
Evidence Threshold
Confidence Calibration
```

目标：

```text
把“召回到了实体”和“用户真的要执行任务”分开
弱实体召回不能单独生成可执行任务
普通闲聊和无意义短音必须输出 no_task
```

### Phase 2：Lexicon 数据化

把联系人、应用、项目从静态 JSON 升级成动态来源。

优先级：

1. 应用列表
2. Lark/飞书联系人
3. 用户手动别名
4. 历史确认反馈
5. 项目和工作区名称

### Phase 3：任务模板评分

实现模板：

```text
open_app
find_contact
find_app
contact_interaction
send_message
```

每个模板输出：

```text
score
required_slots
missing_slots
risk_level
needs_confirmation
evidence_breakdown
no_task_competition_score
```

### Phase 4：多 ASR 候选合并

接入：

```text
SenseVoice raw
Zipformer raw
Zipformer hotword
未来 n-best / fallback
```

候选解析器只关心：

```text
asr_texts[]
```

而不绑定某个 ASR 后端。

### Phase 5：LLM 有限重排

仅在以下情况调用：

```text
候选分接近
低置信度但用户意图可能明确
复合任务结构复杂
```

LLM 只允许在候选中选择。

### Phase 6：确认反馈闭环

用户确认后写入反馈库。

后续同类输入提高对应候选权重。

---

## 8. 非目标

本方案不试图解决：

1. 让 ASR 永远逐字正确。
2. 用一张无限大的错词表覆盖所有情况。
3. 让 LLM 无约束地猜测用户意图。
4. 对高风险动作静默执行。
5. 把实验脚本直接接入主链路。
6. 在发现最相似实体后立刻强制改写原文。
7. 因为命中某一类实体就无条件覆盖最终 intent。

---

## 9. 结论

继续修单条规则会进入无底洞。

正确方向是把语音理解改成：

```text
实体优先
候选生成
任务模板评分
有限重排
确认学习
```

这样即使 ASR 输出：

```text
赵刀威廉
遭到飞蕊
打开LUCK
在LUCK 的威廉发消息
```

系统也不是逐字相信，而是生成多个可能任务，再根据用户词库、上下文、风险和历史反馈选择最合理的动作。

一句话：

```text
不要再问“这句话哪个字听错了”
而要问“在当前用户世界里，这句话最可能对应哪个任务”
```

---

## 10. 追问 / 澄清机制：把“不确定”变成一等结果

### 10.1 为什么必须单独设计追问

确认和追问不是同一个能力。

确认适用于：

```text
系统已经理解出完整任务
但任务有风险
需要用户确认是否执行
```

例如：

```text
用户：在 Lark 给 Vivian 发消息，内容是今晚吃什么
系统：你要在 Lark 给 Vivian 发送“今晚吃什么”，确认发送吗？
```

追问适用于：

```text
系统判断用户大概率在发起任务
但必要槽位缺失或证据太弱
不能安全补全
```

例如：

```text
ASR：打开 LUCK 帮我给你发一条消息
系统不应该猜 contact=Vivian
系统应该问：你要发给谁？
```

如果没有追问状态，候选解析器只能在两种坏选择里选一个：

1. 强行猜一个实体，导致误发、误打开、误执行。
2. 直接失败，用户体验差。

所以语音理解层需要第三种输出：

```text
clarification_required
```

它表示：用户意图可能存在，但当前证据不足，必须补问。

### 10.2 新增输出类型

候选解析器最终不应该只输出：

```text
task
no_task
confirmation
```

而应该输出：

```text
task_ready
task_requires_confirmation
clarification_required
no_task
reject_or_safe_fail
```

结构化结果示例：

```json
{
  "type": "clarification_required",
  "intent": "send_message",
  "known_slots": {
    "app": "Lark"
  },
  "missing_slots": ["contact", "message_content"],
  "uncertain_slots": [
    {
      "slot": "contact",
      "candidates": [
        {
          "value": "Vivian",
          "score": 0.75,
          "evidence": ["pinyin_similarity", "phonetic_similarity"]
        }
      ],
      "reason": "weak_entity_for_high_risk_slot"
    }
  ],
  "question": "你要在 Lark 发给谁？",
  "resume_token": "voice-clarify-20260706-001",
  "risk_level": "high",
  "can_execute": false
}
```

### 10.3 什么时候必须追问

以下情况必须进入追问，而不是自动补槽。

| 场景 | 例子 | 正确动作 |
|---|---|---|
| 高风险任务缺必要槽位 | 发消息但 contact 不确定 | 追问收件人 |
| 高风险任务槽位只有弱实体 | `Vivian@0.75 phonetic_similarity` | 不自动填 Vivian |
| 消息内容缺失 | “给 Neil 发消息”但没说内容 | 追问消息内容 |
| app 可疑但 contact 明确 | “在路车给 Vivian 发消息” | 追问是否是 Lark |
| ASR 质量低 | “发一掉休息” | 追问或要求重说 |
| 多个候选接近 | Neil / Vivian / Ethan 分差小 | 让用户选择 |
| no_task 与 task 分数接近 | 闲聊被误召回实体 | 追问或保持 no_task |

尤其是 `send_message`：

```text
contact 不能靠弱模糊自动补齐
message_content 不能靠猜
app 可以有默认值，但需要在高风险动作里展示给用户确认
```

### 10.4 实体强度分级

实体候选必须分级，不能只看一个总分。

强实体：

```text
exact
substring
ASR 原文直接出现 canonical
ASR 原文直接出现明确 alias
历史确认过的稳定错读
```

中实体：

```text
高分拼音匹配
上下文位置合理
候选数量少
没有强竞争者
```

弱实体：

```text
只有 pinyin_similarity / phonetic_similarity
分数低于 0.85
命中片段过短
命中片段是“你、我、他、那个、这个”等代词或虚词附近
多个联系人候选分数接近
```

高风险槽位要求：

| 槽位 | 低风险任务 | 高风险任务 |
|---|---|---|
| app | 中实体可用 | 中实体可用，但执行前展示 |
| contact | 中实体可打开会话 | 必须强实体，否则追问 |
| message_content | 可为空 | 必须明确，否则追问 |
| file/path | 必须强实体 | 必须强实体 + 确认 |

### 10.5 追问类型

追问不是一句通用的“我没听清”，而是按缺失信息分类。

#### 10.5.1 追问收件人

触发条件：

```text
intent=send_message
contact missing 或 weak
```

示例：

```text
我听到你想在 Lark 发消息。你要发给谁？
```

如果有候选但不确定：

```text
你是要发给 Vivian、Neil，还是其他人？
```

#### 10.5.2 追问消息内容

触发条件：

```text
intent=send_message
contact strong
message_content missing 或 ASR 质量低
```

示例：

```text
要发给 Vivian 的内容是什么？
```

#### 10.5.3 追问应用

触发条件：

```text
contact strong
send/open action strong
app missing 或 app weak
```

示例：

```text
你要通过 Lark 发，还是用其他应用？
```

#### 10.5.4 要求重说

触发条件：

```text
ASR 文本质量很低
动作、实体、内容都不稳定
```

示例：

```text
这句我没听清，请再说一遍要做什么。
```

### 10.6 多轮追问状态机

追问必须保存上下文，否则用户回答“Neil”时系统不知道 Neil 是收件人。

状态对象：

```json
{
  "resume_token": "voice-clarify-20260706-001",
  "created_at": "2026-07-06T16:00:00+08:00",
  "expires_at": "2026-07-06T16:02:00+08:00",
  "intent": "send_message",
  "known_slots": {
    "app": "Lark"
  },
  "missing_slots": ["contact", "message_content"],
  "last_question": "你要在 Lark 发给谁？",
  "source_asr_texts": [
    "打开 LUCK 帮我给你发一条消息"
  ],
  "risk_level": "high"
}
```

用户回答：

```text
Neil
```

系统解析时应优先进入 clarification resume 模式：

```json
{
  "resume_token": "voice-clarify-20260706-001",
  "filled_slots": {
    "contact": "Neil"
  },
  "remaining_missing_slots": ["message_content"],
  "next_question": "要发给 Neil 的内容是什么？"
}
```

用户再回答：

```text
今晚吃什么
```

系统得到完整任务：

```json
{
  "type": "task_requires_confirmation",
  "intent": "send_message",
  "slots": {
    "app": "Lark",
    "contact": "Neil",
    "message_content": "今晚吃什么"
  },
  "question": "确认在 Lark 给 Neil 发送“今晚吃什么”吗？"
}
```

### 10.7 追问与确认的边界

追问是为了补齐信息。

确认是为了执行前安全检查。

流程必须是：

```text
缺槽 / 弱槽
  -> 追问
  -> 补齐槽位
  -> 生成完整任务
  -> 高风险确认
  -> 执行
```

不能跳过追问直接确认错误任务。

错误示例：

```text
ASR：打开 LUCK 帮我给你发一条消息
系统：你要给 Vivian 发消息，确认吗？
```

正确示例：

```text
ASR：打开 LUCK 帮我给你发一条消息
系统：我听到你想在 Lark 发消息。你要发给谁？
```

### 10.8 对 corrected_text 的约束

追问场景下，不应该强行改写最终文本。

当实体是弱证据时：

```text
raw_text: 请你打开那个帮我给路车发一掉休息
```

禁止输出：

```text
请你打开那个帮v薇mLark发一掉休息
```

应该输出：

```json
{
  "raw_text": "请你打开那个帮我给路车发一掉休息",
  "display_text": "请你打开那个帮我给路车发一掉休息",
  "normalized_text": "请你打开那个帮我给路车发一掉休息",
  "type": "clarification_required"
}
```

只有强实体才能进入用户可见 `corrected_text`。

弱实体只能进入：

```text
uncertain_slots
debug evidence
alternatives
```

### 10.9 实施步骤

#### Phase C-1：扩展候选解析输出协议

新增字段：

```text
type
known_slots
missing_slots
uncertain_slots
question
resume_token
can_execute
clarification_reason
```

验收：

```text
弱 contact 不再生成可执行 send_message
而是输出 clarification_required
```

#### Phase C-2：实现实体强度分级

在 EntityCandidate 上新增：

```text
strength: strong | medium | weak
match_quality
matched_span_quality
is_pronoun_like_span
is_short_fragment
has_strong_surface_match
```

验收：

```text
Vivian@0.75 phonetic_similarity -> weak
Lark exact -> strong
Neil exact -> strong
```

#### Phase C-3：改造 send_message 模板

规则：

```text
send_message 必须有 contact
contact weak -> clarification_required
message_content missing -> clarification_required
message_content low_quality -> clarification_required
完整后仍 needs_confirmation=true
```

验收样例：

```text
打开 LUCK 帮我给你发一条消息
=> clarification_required: ask_contact

打开 LARK 帮我给 EASY 发一条消息
=> 如果 EASY 不是强联系人，ask_contact

在 LARK 给 Neil 发消息
=> ask_message_content

在 LARK 给 Neil 发消息内容是今晚吃什么
=> task_requires_confirmation
```

#### Phase C-4：实现 Clarification Session Store

保存最近一次追问状态。

建议：

```text
内存 store 先行
TTL 2 分钟
按 voice session / user session 绑定
同一用户新任务可覆盖旧追问
```

验收：

```text
系统问“你要发给谁？”
用户回答“Neil”
系统能填入 contact=Neil
并继续追问内容或进入确认
```

#### Phase C-5：前端语音交互接入

前端收到：

```json
{
  "type": "clarification_required",
  "question": "你要在 Lark 发给谁？"
}
```

应该：

```text
朗读 / 展示 question
进入等待用户回答状态
把下一句语音带上 resume_token
```

验收：

```text
用户不用重新说完整命令
只说“Neil”即可继续
```

#### Phase C-6：反馈学习

当用户在追问中补齐槽位后，记录：

```json
{
  "original_asr": "给 EASY 发消息",
  "clarified_slot": "contact",
  "resolved_value": "Ethan",
  "context": "send_message",
  "confirmation": true
}
```

下次类似输入可以提高 Ethan 候选，但仍不应绕过高风险确认。

### 10.10 测试矩阵

必须增加以下测试：

| 输入 | 期望 |
|---|---|
| 打开 LUCK 帮我给你发一条消息 | ask_contact |
| 打开 LARK 帮我给 EASY 发一条消息 | ask_contact 或候选选择，不允许自动 Vivian |
| 请你打开那个帮我给路车发一掉休息 | ask_repeat 或 ask_contact，不允许自动 Vivian |
| 在 LARK 给 Neil 发消息 | ask_message_content |
| 在 LARK 给 Neil 发消息内容是今晚吃什么 | task_requires_confirmation |
| Neil | 如果有追问上下文，填 contact；否则 no_task 或 find_contact 候选 |
| 今晚吃什么 | 如果上一轮问 message_content，填内容；否则 no_task/chat |

关键指标：

```text
false_contact_fill_rate
clarification_trigger_rate
clarification_success_rate
wrong_confirmation_rate
send_message_safe_block_rate
```

验收目标：

```text
弱证据联系人自动填槽率 = 0
发送消息缺 contact 时追问率 = 100%
发送消息缺 message_content 时追问率 = 100%
高风险完整任务确认率 = 100%
闲聊/无意义短音误触发任务率持续下降
```

### 10.11 产品原则

语音助手不能只追求“猜中率”。

真正可靠的系统必须做到：

```text
能猜时猜
该问时问
不该做时不做
```

尤其在发消息、删除、提交、付款、发邮件、改文件这类高风险任务中：

```text
宁可多问一句
不要替用户猜一个关键槽位
```
