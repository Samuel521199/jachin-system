# 意图编排与工具仲裁方案（Intent Orchestrator）

> **状态**：设计稿（未编码）  
> **触发案例**：用户说「打开 Windows 原生计算器算 20+70，用 Windows MCP，**不要打开 Lark**」→ 系统仍打开 Lark  
> **本质问题**：两层叠加——(1) 缺少「**先理解任务、再匹配工具、不确定就问**」的编排链；(2) L3 进入 ReAct 前把 **100+ 工具** 和 **Lark Chat ID 等混合上下文** 一股脑丢给模型，在工具过多时选型能力 **断崖下跌**（业界经验：>20～30 个工具即明显恶化）  
> **目标升级**：从「把这次路由对」→「**以后任何新需求都能稳定理解、选对工具、知道不确定时该澄清**」  
> **架构双轴**：**Intent Orchestrator**（任务理解与仲裁）+ **HIDCA**（Hierarchical Intent & Dynamic Context Assembly，L3 执行面的语义网关 / 工具剪枝 / 上下文沙盒）  
> **关联文档**：`VOICE_INTENT_ROUTING_AND_TASK_ORCHESTRATION.md`、`docs/L3_AMBIGUOUS_INTENT_ARCHITECTURE.md`、`docs/L3_FUZZY_INTENT_CLARIFICATION.md`、`docs/capability_domains/os_assistant.md`、`docs/architecture/L3_TOOL_POOL_AND_MCP_ASSEMBLY.md`

---

## 1. 问题陈述

### 1.1 用户真正要什么

用户希望 Jachin 像 **JARVIS** 一样：**听懂你要什么结果**，再决定用哪条能力路径——而不是系统内部先想「我手头有哪些 workflow 可以套」。

面对任意新说法，系统应先回答四件事：

| # | 问题 | 答错时的典型后果 |
|---|------|------------------|
| 1 | 用户到底要达成 **什么结果**？ | 算了加法却开了 IM |
| 2 | 这件事属于 **哪个任务域**？ | 本地操控被当成飞书办公流 |
| 3 | 需要 **哪些能力/工具**？ | 工具池里 Lark 工具过多，模型随手选 |
| 4 | 当前理解 **够不够执行**？不够就该 **问**，而不是乱动 | 模糊句硬套 Codex→Lark 模板 |

### 1.2 触发案例（症状，不是病根）

- 用户明确：Windows 原生计算器、Windows MCP、**不要 Lark**
- 实际：打开 Lark/飞书
- 直接技术原因：句末「不需要打开 lark」里的 `lark` 被当成「要打开的 App」（见 §3.1）
- **更深原因 A（编排）**：没有「多候选比较 + 反证 + 执行前一致性检查」，**第一个命中的规则或最显眼的工具名就赢了**
- **更深原因 B（L3 执行面）**：ReAct Agent **直接消化原始请求**；`assemble_tool_pool` 合并后工具池常含 **百级** MCP（Lark 系工具优先级高、描述长、示例多），再叠加 `lark_chat_id` 等 implicit 变量 → 模型 **潜意识以为自己在飞书会话里干活**（见 §3.5）

### 1.3 影响面

| 场景 | 后果 |
|------|------|
| 语音 Companion | STT 长句 + 纠偏句；误开 Lark 直接摧毁信任 |
| 桌面 Chat | Preflight 未命中时 ReAct 自由选工具，Lark 先验过高 |
| 未来任意新 MCP | 每加一个 workflow 就加一条「谁先匹配」规则 → 不可维护 |
| 负向约束 | 「不要 X」被当正向提及，越纠偏越错 |

---

## 2. 回归基准用例

以下句子应 **100%** 路由到 `calculator_calculate` → `mcp:windows_calculator_calculate`，且 **禁止** 任何 Lark 打开/发送类工具：

```
给我打开 windows 上原生的计算器给我算 20+70 等于几，
注意是用 windows 那个 mcp 来打开电脑原本的计算器计算哦，不需要打开 lark
```

**期望观测**：识别意图 `calculator_calculate`；前台为 Windows 计算器；Router Evidence 记录「因 user_negation 排除 lark」。

**变体**（均应命中计算器）：「用电脑算 20+70，别打开飞书」「windows mcp 计算器 20+70」「打开计算器，不是 Lark」。

此用例进入 **路由评测集**（§8），每次改路由逻辑必须回归。

---

## 3. 根因分析

### 3.1 系统性根因：缺少 Intent Orchestrator

当前路径本质是 **分散的规则短路 + ReAct 兜底**，没有统一的「任务理解 → 工具仲裁」层：

```text
用户输入
  → Voice 分流（闲聊/短/长）
  → Preflight 若干拦截器（HR、OS Mission…）
  → 语义槽位（一条 MissionIntent，先到先得）
  → ReAct（模型从 tools[] 里猜 Action）
```

缺什么：

- **不先建「任务框架」就直接选工具**
- **同一句话只产出一个意图**，不做多候选排序
- **不算反证**（例如：没有收件人却走发消息）
- **调用工具前不做「意图 vs 工具」一致性检查**
- **外部副作用工具**（发消息、删文件）与 **本地操控** 门槛相同
- **ReAct 前未做域级工具剪枝**：模型「看得见」的 Lark 工具太多，**看不见就不会误调** 这一简单事实未被利用
- **上下文未按域隔离**：OS 任务仍携带 IM 通道变量，造成 **Context Pollution（上下文污染）**

### 3.2 分层症状（仍须修复）

```text
用户语音/文字
    │
    ▼
 L0  Voice Dispatcher          ← 无 OS 域 / 无约束下发
    ▼
 L1  Agent Preflight           ← OS Router 未覆盖全通道；命名偏 Codex/Lark
    ▼
 L2  semantic_slot_parser      ← 否定句、多实体、先到先得（§3.3）
    ▼
 L3  ReAct + os_assistant 域   ← Lark 工具 & Prompt  overweight
```

#### 3.3 L2 已确认 bug：否定句里的「lark」抢优先级

`semantic_slot_parser._detect_app_name` 按字典顺序匹配，`lark` 排在 `calculator` 前。句末「不需要打开 lark」使 compact 文本含 `lark` → `APP_CONTROL` 打开 Lark，而 `20+70` 表达式被丢弃。

**结论**：这是 Phase 0 必修项，但修完这一条 **不等于** 有了通用意图能力——还需要 §5 的编排层。

**Phase 0 否定提取的算法思路（不写代码，描述逻辑）**：

否定提取应在 `_detect_app_name` 之前完成，作为独立的 pre-pass：

1. **扫描否定模式**：识别「不要/不需要/别/禁止/不是/而不是 + [可选动词] + [实体]」，将命中实体写入 `negations[]`；
2. **逻辑剥离**：不是字符删除，而是给实体打 `negated=True` 标记——后续 `_detect_app_name` 遍历别名时跳过 `negations` 里的实体；
3. **Calculator 表达式优先级规则**：只要句中存在算术表达式 AND 计算器别名 → **强制返回 `CALCULATOR_CALCULATE`**，不再继续 app_name 匹配（这是"任务类型 > app 名称"的明确优先级）；
4. **主动词绑定**：多个 app 名同时出现时，取与 **主动作动词距离最近** 的实体作为 target，而非最后出现的；
5. **「windows mcp」作为域 require 信号**：识别到「windows mcp / 原生 / 本机」等词时，直接写入 `require_domains: [os_assistant]`，此后候选的 domain 不为 `desktop_control` 的要被重惩。

#### 3.4 L3 Prompt / 工具池结构性偏置

`os_assistant` 域中 Codex→Lark 的 CRITICAL OVERRIDE 合理，但 Lark 工具数量、示例频次远高于纯本地工具，模型在略模糊时倾向 Lark。用户说的「windows mcp」没有结构化槽位，仅靠自然语言易被忽略。

#### 3.5 L3 执行面：工具过载 + 上下文污染（「指鹿为马」的直接推手）

即便用户句子本身解析正确，只要请求 **漏进 ReAct 大循环**，仍极易走错。根因是 **执行环境未按域收缩**：

**（1）工具过载（Tool Overload）**

- `load_tools` + MCP Registry 合并后，单轮 `tools[]` 可达 **100～180** 量级（视 `allowed_skills`、本地 MCP 合并开关而定）；
- 研究与应用经验均表明：Tool Calling 在工具数 **超过约 20～30** 后，误选率显著上升；
- Lark 系工具（发送、读消息、多维表、Codex 模板等）在列表中 **数量多、描述长、排在显著位置**，对 Qwen/GPT 类模型构成 **高先验引力**；
- 计算器案例：模型并非「不懂算术」，而是 **在 100 个工具里先看到了更「像聊天软件」的路径**。

**（2）上下文污染（Context Pollution）**

- 飞书长连接、Companion WS、deferred 调度等路径会向 L3 注入 `lark_chat_id`、`originating_lark_chat_id`、`implicit_signals.lark_*`；
- 执行 **本地 OS 操控** 时，这些变量对任务 **无信息量**，却会 bias System Prompt 与模型自我定位——**「我是 IM 里的助手」** 而非 **「我是本机 OS 编排器」**；
- 即使用户明确说「不要 Lark」，**上下文中仍残留 Lark 会话 ID**，与用户文本形成 **信号冲突**，模型常 **信上下文不信用户**。

**结论**：仅修 `semantic_slot_parser` 不够；必须在 **ReAct 启动前** 完成 **域判定 → 工具剪枝 → 上下文沙盒 → 域专用 Prompt**（§5.11～§5.13）。

---

## 4. 设计目标

### 4.1 产品目标

1. **任务优先于工具**：先理解「要什么结果」，再匹配 capability，而不是先看到 `windows_lark_*` 就套。
2. **约束可执行**：「不要 Lark」「用 Windows MCP」是 **硬门禁**，不是 prompt 建议。
3. **不确定就澄清**：精准 ≠ 永远猜中；**知道什么时候不能猜** 才是精准。
4. **可解释、可进化**：每次决策有完整证据链 + 评测集回归（§7、§8）。
5. **语音与文字同构**：同一套 Intent Frame，Voice 只多 STT 归一化。

### 4.2 非目标（V1）

- 不追求开放域 100% 自动执行；
- 不在 Rust/JVS 跑 LLM；
- 不用「再加 500 条 if-else」替代理解层——规则负责 **门禁**，不负责 **全部语义**。

---

## 5. 目标架构：Intent Orchestrator

在 **所有 MCP / workflow / ReAct 工具调用之前**，增加统一编排层 **Intent Orchestrator**（IO）。  
它产出的是 **可执行的决策包**，不是直接代替 ReAct，而是 **约束、排序、门禁** ReAct。

**与 HIDCA 的关系**：IO 负责「**判域与仲裁**」；判完域之后，由 **HIDCA 三板斧**（§5.11～§5.13）负责「**ReAct 启动前的执行环境收缩**」。二者串联，缺一不可。

### 5.1 总流程

```text
用户输入（Voice STT / Chat / IM）
    │
    ▼
┌──────────────────────────────────────────────────────────┐
│ Intent Orchestrator（IO）                                  │
│  normalize → Intent Frame → 多候选+反证 → Capability 匹配   │
│  → Risk Gate → Consistency Check（预判）                   │
└────────────────────────────┬─────────────────────────────┘
                             ▼
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
    确定性执行          澄清反问         route_policy=react_fallback
  （高置信+低副作用）  （中置信/缺槽）              │
          │                  │                  ▼
          │                  │     ┌────────────────────────────┐
          │                  │     │ HIDCA（仅 ReAct 路径）        │
          │                  │     │ ① Semantic Router 域标签    │
          │                  │     │ ② Dynamic Tool Pruning 剪枝 │
          │                  │     │ ③ Context Sandbox 上下文沙盒│
          │                  │     └─────────────┬──────────────┘
          │                  │                   ▼
          │                  │            ReAct Agent 启动
          │                  │            n_tools ≪ 全量池
          └──────────────────┴───────────────────┘
                             ▼
                    Router Evidence 落盘 → UI/HUD
                             ▼
              每次 tool call 仍过 Consistency Check（§5.7）
```

### 5.2 Intent Frame：第一步只回答「用户要什么」

**不在这一步选 tool id。** 与现有 `MissionIntent` / `VoiceDispatcherDecision` 对齐，扩展为 SSOT：

```json
{
  "goal": "用户想完成的最终结果，用自然语言概括",
  "domain": "desktop_control | communication | code | file | web | data | schedule | hr | chitchat | unknown",
  "action": "open | calculate | send | search | edit | analyze | create | compare | control | unknown",
  "target": "目标对象，如 calculator / lark / project_name",
  "inputs": {
    "expression": "20+70",
    "recipients": [],
    "project_path": ""
  },
  "constraints": {
    "require_domains": ["os_assistant"],
    "require_tools": [],
    "exclude_domains": ["lark_im"],
    "exclude_tools": ["mcp:windows_open_app:lark", "mcp:windows_lark_*"]
  },
  "forbidden": [
    { "entity": "lark", "scope": "open_app", "source": "user_negation" }
  ],
  "explicit_signals": ["windows_mcp", "原生计算器", "不需要打开lark"],
  "side_effect_level": "none | local | external | destructive",
  "confidence": 0.0,
  "route_policy": "execute | clarify | react_fallback"
}
```

**域（domain）与 Jachin 现有概念映射**：

| Intent Frame domain | HIDCA 路由域标签 | 对应能力 / 工具组 |
|---------------------|------------------|-------------------|
| `desktop_control` | `OS_CONTROL` | `os_assistant`、Windows UIA MCP |
| `communication` | `WORKSPACE_LARK` | Lark 发送/读消息、IM 通道 |
| `code` | `CODE_EXEC` | Codex、shell、apply_patch |
| `file` | `OS_CONTROL`（子集） | 文件 find/copy/delete… |
| `data` / 检索 | `KNOWLEDGE_RAG` | 记忆、RAG、SQLite 只读 |
| `hr` | `HR_RECRUITMENT` | 招聘调度、HR Lark 拦截 |
| `chitchat` | `CHITCHAT` | direct_llm / 语音闲聊快轨 |

> **说明**：HIDCA 的域标签是 ReAct 启动前的 **粗粒度开关**（用于剪枝与沙盒）；IO 的 Intent Frame 是 **细粒度任务描述**（含 action、slots、forbidden）。前者可由后者 **确定性映射**，不必另搞一套语义。

**副作用分级（side_effect_level）** 决定门槛（§5.6）：

| 级别 | 含义 | 示例 |
|------|------|------|
| `none` | 只读、心算、闲聊 | 问答、读文件 |
| `local` | 本机 UI/文件，不影响他人 | 打开计算器、记事本 |
| `external` | 影响外部系统/他人 | 发 Lark、发邮件、Git push |
| `destructive` | 不可逆或需强确认 | 删文件、覆盖写库 |

### 5.3 多路候选 + 排序：禁止「一条规则命中就结束」

同一句话 **至少生成 2～5 个候选解释**，再比较，而不是先到先得：

```text
candidate A: domain=desktop_control, action=calculate, score=0.86
  + 支持: 「计算器」「20+70」「Windows MCP」「打开…算」
  - 反证: 无

candidate B: domain=desktop_control, action=open, target=lark, score=0.31
  + 支持: 文本含「lark」「打开」
  - 反证: user_negation(lark); 主动词「打开」修饰对象是计算器; 有算术表达式

candidate C: domain=communication, action=send, score=0.08
  + 支持: （无强支持）
  - 反证: 无收件人; 无发送动词; 用户目标非消息交付

candidate D: unknown, score=0.05
```

**打分原则**：不是「关键词出现次数」，而是 **支持证据加权 − 反证惩罚**。  
用户显式 `forbidden` / `exclude` 的反证权重 **高于** 裸关键词命中。

实现上分两路产出候选（§5.9）：

- **规则路**：`semantic_slot_parser`、`task_understanding_engine`、Voice 信号
- **LLM 路（可选）**：复杂句拆解、新域泛化；输出 **同一 Intent Frame schema**

最终由 Orchestrator **合并、去重、排序**；规则路可在 P0 约束上与 LLM 路 **否决**（例如 LLM 不可覆盖 user_negation）。

### 5.4 工具选择 = 能力匹配（Capability Matching）

工具不再靠「句子里有没有 lark」来选，而是 **Intent Frame × Tool Capability Schema** 做匹配。

每个工具（含 MCP、Native、workflow 模板）应声明 capability（现有 `capability_semantic_registry.py` 可扩展）：

```yaml
tool_id: mcp:windows_calculator_calculate
capabilities:
  actions: [calculate, open_app]
  objects: [windows_calculator, arithmetic_expression]
  required_inputs: [expression]
  side_effect_level: local
  success_evidence: [active_window, ocr_result, screenshot]
  cannot_handle: [send_message, lark_delivery, code_analysis]
```

```yaml
tool_id: mcp:windows_lark_send_message
capabilities:
  actions: [send]
  objects: [lark_chat, lark_group]
  required_inputs: [recipients_json, message]
  side_effect_level: external
  success_evidence: [screenshot, ocr_sent_confirmation]
  cannot_handle: [local_calculate, open_calculator]
```

匹配步骤：

1. 按 `domain + action + target` 筛候选工具；
2. 剔除 `constraints.exclude_*` 命中项；
3. 检查 `required_inputs` 是否齐全 → 缺则 **clarify**，不硬跑；
4. 对 `cannot_handle` 与 Intent Frame 冲突的 **降权或剔除**；
5. 产出 `ExecutionPlan`：`{ tool_id, params, confidence, match_reasons[] }`。

### 5.5 反证机制（Counter-Evidence）

每个候选 intent 必须同时记录 **支持** 与 **反对**：

```text
【候选 B 反证详情】
- forbidden: user_negation(lark) ← 权重最高
- 主目标: 「打开计算器」距动词最近，优于句末提及 lark
- 任务类型: 存在 expression=20+70 → calculate 优于 open_app
- communication 反证: 无 recipients、无「发给/发送」
```

**计算器案例的正确结论**：A 高分、B 因反证暴跌、C 几乎排除 → 选 A，而非 B。

### 5.6 外部副作用工具：默认保守（Risk Gate）

凡 `side_effect_level >= external` 的工具，执行门槛 **显著高于** 本地操控：

| 条件 | 要求 |
|------|------|
| 动作 | 明确（send / delete / push…） |
| 对象 | 明确（收件人、路径、表名…） |
| 内容 | 明确（message、fields…） |
| 反证 | 无强反证（含 user forbidden） |
| 置信度 | ≥ 阈值（建议 0.75+，可配置） |

**不满足 → 澄清，不执行。** 典型：

- 「发一下」「同步一下」「打开那个」→ **禁止** 硬套 Codex→Lark 或 Lark 发送
- 「总结项目发给 Vivian」且槽位齐全 → 可走 external workflow

本地 `desktop_control` + `side_effect_level=local`（如计算器）在置信度足够时 **可直接执行**，无需多余确认。

### 5.7 Intent-Tool Consistency Check（执行前兜底）

**真正调用工具前**，必须生成一条机器可读的一致性判断：

```text
用户目标: 在本机 Windows 计算器完成算术 20+70
计划工具: mcp:windows_open_app(app_name=lark)
一致性: INCONSISTENT
原因: domain 不匹配; user forbidden lark; 存在专用工具 windows_calculator_calculate
动作: REJECT → 重新路由或澄清
```

```text
用户目标: 在本机 Windows 计算器完成算术 20+70
计划工具: mcp:windows_calculator_calculate(expression=20+70)
一致性: CONSISTENT
动作: ALLOW
```

**任何**工具调用（含 ReAct 选的 Action、Preflight 短路、Mission Router）**都必须过此检查**。  
不一致时：宿主 **拒跑**，Observation 写入 `routing_violation`，禁止同参无限重试（见 `080-jachin-execution-resilience`）。

### 5.8 澄清策略：知道什么时候不能猜

| 情况 | 行为 |
|------|------|
| 最高候选置信度 < 0.45 | 澄清 |
| 前两名候选分差 < 0.15 | 澄清（二选一） |
| external 工具缺必填槽 | 澄清 |
| OS vs Lark 信号冲突且居中置信 | 澄清，**禁止默认 Lark** |

澄清文案示例：

> 你是要我 **在本机用 Windows 计算器算 20+70**，还是要 **打开飞书做别的**？回复「计算器」或「飞书」。

复用 `L3_FUZZY_INTENT_CLARIFICATION` 插件机制，新增 `os_local_vs_lark` 等域插件；**禁止**对「好的」「嗯」等无动作短句在澄清层抢答（现有产品约束不变）。

### 5.9 LLM 与规则分工

| 职责 | 规则 | LLM |
|------|------|-----|
| 高风险门禁、forbidden、exclude | ✅ 主 | ❌ 不可覆盖 |
| 必填槽位、格式校验 | ✅ 主 | 辅助补槽 |
| 安全 / side_effect 门槛 | ✅ 主 | ❌ |
| 泛化理解、新域、复杂句拆解 | 辅助 | ✅ 主 |
| 多候选意图生成 | 结构化候选 | 可增补候选 |
| 最终排序 | ✅ 含反证公式 | 仅提供特征 |

```text
LLM 生成/丰富候选理解
  → 规则做安全约束、反证、排序
  → Capability Schema 做工具匹配
  → Consistency Check 做执行前兜底
  → Eval Set 做回归保障
```

默认：**规则 + capability 匹配可独立工作**；`JACHIN_ENABLE_LLM_INTENT_PARSER=1` 时 LLM 仅在中低置信度介入（沿用 `semantic_intent_engine` 钩子）。

### 5.10 显式信号快速路径（用户说清楚了，直接做）

这是澄清策略（§5.8）的 **对立面**：当用户已经把意图说得足够清楚，系统应该 **直接执行**，而不是再多此一举地问「你是要计算器还是飞书」。

**快速路径触发条件（全部满足时跳过多候选排序，直接进确定性执行）**：

| 条件 | 含义 |
|------|------|
| `confidence ≥ 0.80` | 候选得分足够高 |
| `explicit_signals` 含工具域强信号 | 如「windows mcp」「原生计算器」「用电脑」「本机」 |
| `forbidden` / `negations` 已明确 | 用户已排除干扰项（如「不要 Lark」） |
| `side_effect_level = local` | 本地操控，无外部副作用风险 |
| 无缺失必填槽位 | 表达式、app 名等已抽出 |

**计算器案例**：满足全部条件 → 快速路径，**不触发澄清，不走 ReAct，直接调 `mcp:windows_calculator_calculate`**。

**核心原则：用户说了就信用户。** 用户提供了明确的任务、工具域、否定约束，系统的责任是执行，而不是质疑。让用户重复解释自己已经说清楚的事情，是体验的最大杀手。

快速路径的执行结果要在回复中 **主动告知**（见 §6.5 主动透明）。

### 5.11 HIDCA ①：L3 前置语义网关（Semantic Router）

**原则**：**不要让执行具体任务的 ReAct Agent 直接处理「未经域判定的原始请求 + 全量工具池」。**

Semantic Router 是 IO 的 **ReAct 侧入口**（或 IO 的第 0 步）：在 Tool Calling 循环开始前，**唯一必须先完成的输出是「本轮属于哪个域」**——而不是让 ReAct 模型同时做「理解 + 选工具 + 执行」。

**工作机制**：

| 步骤 | 做什么 | 不做什么 |
|------|--------|----------|
| 输入 | 归一化后的用户句 + IO 产出的 Intent Frame 摘要 | 不把 100+ tools 描述塞进 Router |
| 输出 | 1～2 个 **域标签** + 置信度 + 与 IO 候选是否一致 | 不输出具体 tool id（留给 Capability 匹配） |
| 优先级 | **用户文本 > implicit 通道变量** | 不因存在 `lark_chat_id` 就默认 `WORKSPACE_LARK` |

**计算器案例**：即使用户从飞书会话发起、上下文带 Lark ID，Router 仍应输出 `OS_CONTROL`——依据「打开计算器」「Windows MCP」「原生」「不需要打开 lark」等强特征 + IO 的 `forbidden(lark)` 反证。

**实现选型（由易到难，可并存）**：

1. **规则 + IO 映射（默认、低延迟）**：Intent Frame 的 `domain` 直接映射 HIDCA 标签；否定/forbidden 由规则 **一票否决** IM 域。
2. **Few-shot 小调用（可选）**：对低置信度句，用 **极短** 分类 prompt（只输出 domain JSON），仍 **不** 带全量 tools。
3. **微调小模型（远期）**：仅当规则 + Few-shot 仍不足时考虑；**不是 V1 前置条件**。

Semantic Router 与 IO **共用 Intent Frame SSOT**，避免两套分类器打架；不一致时 **规则/forbidden 路优先**。

### 5.12 HIDCA ②：动态工具剪枝（Dynamic Tool Pruning）

**原则**：**模型看不见的工具，就不可能误调。** 这是降低 L3 泛化错误 **性价比最高** 的一步。

**问题量化**：全量池 ~100～180 tools → 剪枝后单域 **≤15～25**（理想 **≤10**）→ ReAct 才处于模型可靠区。

**工作机制**：

```text
Semantic Router 输出 domain = OS_CONTROL
    │
    ▼
assemble_tool_pool 之后、build_tools_description 之前
    │
    ▼
按 Domain ↔ Tool 绑定表 物理剔除 非本域工具
    │
    ▼
ReAct 可见 tools[] 仅含例如：
  mcp:windows_calculator_calculate
  mcp:windows_open_app
  mcp:windows_notepad_save_text
  mcp:uia_snapshot / uia_click / uia_set_text
  core:fs_read（若任务涉文件）
  … 合计 ≈ 5～15 个
    │
    ▼
WORKSPACE_LARK 下全部 windows_lark_* / atom_lark_* 不在列表中
```

**工具 ↔ 域绑定**（维护 SSOT，建议扩展现有 `capability_catalog.py` / `capability_semantic_registry.py`）：

- 每个 tool id 声明 **primary_domain**（一个主域）+ 可选 **secondary_domains**；
- 剪枝是 **硬剔除**，不是 prompt 里写「尽量不要用 Lark」；
- IO 的 `constraints.exclude_tools` / `forbidden` **叠加** 到剪枝结果（用户说不要 Lark → Lark 工具 id 全删）；
- **CHITCHAT** 域：`tools[]` 为空或仅只读，配合 `direct_llm`。

**与 Capability Matching 的关系**：§5.4 在 **全量池** 上算最佳工具；剪枝在 **域确定后** 缩小搜索空间。顺序：**IO 判域 → 域剪枝 →（可选）域内 Capability 排序 → ReAct**。

**Lazy Loading**：未判定域前 **不** 向 ReAct 暴露任何 MCP 长描述；Deterministic 路径（OS Mission Router）可直接调用工具 **绕过 ReAct**，此时剪枝不影响。

**效果预期（计算器案例）**：`n_tools` 从 ~177 降到 ~8 后，即便 ReAct 兜底，模型 **物理上无法** 选择 `windows_lark_send_message` 或 `windows_open_app(lark)`。

### 5.13 HIDCA ③：上下文沙盒（Context Sandboxing）

**原则**：**域不同，上下文不同。** 执行 OS 任务时，IM 通道变量是 **噪声甚至毒素**，必须从本轮 ReAct 视野中剥离或降权。

**污染来源示例**：

| 变量 / 信号 | 典型来源 | OS 任务中 |
|-------------|----------|-----------|
| `lark_chat_id` / `originating_lark_chat_id` | 飞书长连接、deferred 调度 | **剥离** |
| `implicit_signals.lark_*` | WS Companion 透传 | **剥离** |
| System Prompt 中 Lark 办公 SOP | `os_assistant` 全量注入 | **不注入**（改域专用块） |
| 历史中上一轮 Lark 工具 Observation | 多轮 ReAct | **保留摘要即可**，避免冗长 OCR 干扰 |

**沙盒规则（按 HIDCA 域）**：

```yaml
OS_CONTROL:
  strip_implicit_keys: [lark_chat_id, lark_reply_chat_id, originating_lark_chat_id, feishu_*]
  system_prompt_profile: os_local_admin   # 见下
  inject_block: PROMPT_INJECT_OS_ASSISTANT_LOCAL_ONLY
  forbid_blocks: [PROMPT_INJECT_LARK_DELIVERY, CODEX_LARK_CRITICAL_OVERRIDE]

WORKSPACE_LARK:
  keep: lark_chat_id  # 本域需要
  system_prompt_profile: lark_workspace_orchestrator
  inject_block: PROMPT_INJECT_OS_ASSISTANT_LARK_SUBSET

CHITCHAT:
  strip_all_tool_context: true
  system_prompt_profile: companion_chat
```

**重塑 System Prompt（域专用 persona）**——OS 域示例文案：

> 你当前角色是 **Windows 本机 OS 编排器**，通过 MCP 操控本地桌面应用与文件。  
> 工作空间是 **用户电脑**，不是飞书/Lark/任何聊天窗口。  
> 除非用户明确要求发消息，否则 **不得** 打开或操作 IM 类 App。

这与 §6.4 的 Prompt **分区** 同义；沙盒层保证 **该分区以外的 Lark 指令块不会被注入**。

**注意**：沙盒 **不删除** 持久化会话历史存储——仅在 **本轮** `run_agent` 的 prompt 组装与 `implicit_signals` 中不可见；用户若在本轮明确说「发给 Vivian」，Router 应 **切域** 到 `WORKSPACE_LARK` 并 **重建** 上下文包，而非 OS 沙盒内硬做发送。

### 5.14 与现有组件的衔接（不推倒重来）

| 现有组件 | 在 IO / HIDCA 中的角色 |
|----------|------------------------|
| `voiceIntentRouter.ts` | L0：产出 Voice 级 Intent Frame 片段 + `side_effect_level` hint |
| `agent_preflight.py` | 调用 IO；HR/BI 等域插件在 IO **之前或之内** 注册 |
| `os_mission_router.py` | 高置信 `desktop_control` 的 **确定性执行器**（可绕过 ReAct） |
| `semantic_slot_parser.py` | 规则路候选生成器之一；Semantic Router 输入源 |
| `capability_router.py` | 工具匹配后的 route 选择；Consistency Check |
| `capability_semantic_registry.py` | Tool Capability Schema + **Domain 绑定 SSOT** |
| `capability_catalog.py` | 域 Prompt 块锚点；沙盒 `inject_block` / `forbid_blocks` |
| `assemble_tool_pool` | **Dynamic Tool Pruning 挂载点**（合并后、描述生成前） |
| `agent_core._build_system_prompt` | **Context Sandbox + 域专用 Prompt** 挂载点 |
| `build_tools_description` | 仅接收剪枝后的 `tools[]` |
| `agent_core` ReAct | 仅在 `react_fallback` 且 HIDCA 收缩后启动 |

### 5.15 多步序列意图编排

有一类请求 IO 不能当成单一意图处理，需要拆成有序的多步执行链：

> 「把 20+70 算出来，然后把结果发给 Vivian」

这是 **两个 Intent Frame 的序列**，而不是一个混合路由问题：

```text
Step 1: domain=desktop_control, action=calculate, expression=20+70
        → 先执行，得到结果 90

Step 2: domain=communication, action=send, recipients=[Vivian], message="结果是 90"
        → 依赖 Step 1 的输出
```

**关键规则**：

1. 发现连词「然后/接着/再/之后/并且发给」时，IO 应 **拆步** 而非合并为一个混合意图；
2. 步骤之间有 **数据依赖**（Step 2 的 `message` 来自 Step 1 的结果）时，编排层需处理传参；
3. 每一步分别过各自的 Risk Gate 和 Consistency Check——Step 1 是 `local`，Step 2 是 `external`（需要收件人明确）；
4. 不要因为句子里有「Vivian」就把 Step 1 也路由到 `communication` 域——「发给 Vivian」是 Step 2 的槽，不是 Step 1 的域标签。

**V1 实现建议**：OS Mission Router 已支持「先 MCP 操作、后可选 Lark 发送」的 workflow 模板，这是现有的最近路径；完整的多步序列编排可作为 Phase 后期功能，V1 先处理单步 + 显式序列（「…然后发给…」）两种模式。

---

## 6. 分层改造要点（实施映射）

### 6.1 L0 桌面 Voice Dispatcher

- 新增 **`OS_LOCAL_CONTROL`** 意图类；禁止 OS 操控句走 `direct_llm` / 闲聊快轨
- STT 归一化：**否定子句剥离**后再做关键词匹配
- 经 WS 下发 `intent_frame` 片段（扩展 `implicit_signals`）

### 6.2 L1 Preflight 统一入口

- 所有通道 **先过 IO**，再进 HR/OS 等域插件
- `maybe_run_codex_lark_mission` 心智改为 **OS 子执行器**；Codex→Lark 仅是 `communication + code` 高置信路径
- 每次决策 **强制** 写 Router Evidence（§7）

### 6.3 L2 语义槽位

- 否定检测、多 app 消歧、打分制别名（废除 dict 顺序先到先得）
- 输出 **候选列表** 而非单个 `MissionIntent`

### 6.4 L3 ReAct：HIDCA + Prompt 分区 + 最小动作原则

**Semantic Router**：`run_agent` 入口读取 IO 的 `domain` → 输出 HIDCA 域标签；与 `lark_chat_id` **解耦**。

**Dynamic Tool Pruning**：在 `assemble_tool_pool` 之后按域 **硬删** 非本域 tool id；记录剪枝前后 `n_tools` 写入 Router Evidence。

**Context Sandbox**：`_build_system_prompt` 与 implicit 组装处按 `system_prompt_profile` 选块；OS 域 **strip** Lark 相关键。

**Prompt 分区**（与 §5.13 一致）：

- **Local OS（`OS_CONTROL`）**：计算器、窗口、文件、系统状态；
- **Lark Delivery（`WORKSPACE_LARK`）**：仅在本域注入 Codex→Lark CRITICAL 等块；
- **CHITCHAT**：无工具或仅只读。

**Consistency Check + direct_llm veto**：剪枝是防误选 **第一层**；Consistency Check 是 **最后一层**；OS/算术信号禁止 `direct_llm_bypass` 心算。

**最小动作原则**：对于 OS 本地任务，优先选 **最专用** 的工具，而不是最通用的。`mcp:windows_calculator_calculate` 比 `mcp:windows_open_app + uia_click + ...` 更好；`mcp:windows_lark_send_message` 比 `mcp:windows_open_app(lark) + uia_set_text + ...` 更好。越专用的工具越难误用、越容易验证成功证据、延迟也更低。Capability Schema 里的 `preferred_over` 字段可以声明这种偏好。

### 6.5 主动透明：执行时告诉用户你做了什么

当 IO 应用了用户的否定/约束信号执行任务时，系统 **主动** 在回复里说出来，不要静默执行：

**语音 Companion 示例**：
> 「好的，用 Windows 原生计算器来算，不打开飞书。20 加 70 等于 90。」

**桌面 Chat 示例**：
> 「已用 Windows 计算器完成 20+70 = **90**，未打开 Lark（你说了不需要）。[证据截图]」

**为什么重要**：
- 用户说了「不要 Lark」，系统正确执行后 **不说**，用户不知道到底有没有生效，还是会担心；
- 主动透明是 **信任建立** 的核心，也是语音 JARVIS 体验的标志——「我听到你了，我按你说的做了」；
- 有了回复里的说明，用户发现系统理解错了（比如用户其实要飞书）时也更容易纠正。

HUD/Evidence Panel 展示的摘要应同步包含：`识别为：计算 20+70` / `已排除：Lark（用户明确指定）`。

### 6.6 会话域粘性与否定持久性

**当前轮次否定**（V1）：否定信号仅作用于当前 Intent Frame，不跨轮次。每轮重新解析。

**会话粘性（可选，V2）**：用户在某轮明确说「这次不要 Lark」后，可选择将 `negations` 写入会话状态，后续几轮沿用——实现为 `session_negations[]`，随 `VoiceDispatcherContext` 传递。但需要 **明确的重置触发**（用户主动说「现在打开 Lark」时清除）。

**域粘性**：本轮确定为 `OS_CONTROL` 后，下一轮若用户只说「再算一下 30+50」（无域信号），可优先沿用上轮域，而不是重新从零解析。粘性 TTL 建议 1～3 轮，可配置。不跨语音 Session 边界。

**注意**：域粘性和否定粘性不同——前者是「猜测用户意图延续」，后者是「用户明确的约束延续」。粘性过强会导致下一个完全不同的请求被错误地继承上轮语境，实现时需要「新话题打断」检测。

---

## 7. 决策链日志（Router Evidence）

**不能**只记「最后调了哪个工具」。每次请求落盘：

```json
{
  "utterance": "…",
  "normalized": "…",
  "intent_frame": { },
  "candidates": [
    {
      "domain": "desktop_control",
      "action": "calculate",
      "score": 0.86,
      "support": ["…"],
      "counter": []
    },
    {
      "domain": "desktop_control",
      "action": "open",
      "target": "lark",
      "score": 0.31,
      "support": ["…"],
      "counter": ["user_negation(lark)", "…"]
    }
  ],
  "tool_candidates": [
    { "tool_id": "mcp:windows_calculator_calculate", "score": 0.91, "match_reasons": ["…"] }
  ],
  "chosen": {
    "tool_id": "mcp:windows_calculator_calculate",
    "route_policy": "deterministic_execute",
    "why": "top candidate, no counter-evidence, consistency=PASS"
  },
  "rejected": [
    { "tool_id": "mcp:windows_open_app", "params": {"app_name": "lark"}, "why": "consistency=FAIL, user_negation" }
  ],
  "clarification": null,
  "hidca": {
    "semantic_router_domain": "OS_CONTROL",
    "tools_before_prune": 177,
    "tools_after_prune": 9,
    "stripped_context_keys": ["lark_chat_id", "originating_lark_chat_id"],
    "system_prompt_profile": "os_local_admin"
  },
  "latency_ms": { "parse": 12, "match": 8, "prune": 3, "total": 35 }
}
```

日志检索键：`[IntentOrchestrator]`、`[RoutingViolation]`、`[ConsistencyCheck]`、`[StrategyShift]`  
HUD / Evidence Panel 对用户展示摘要：**识别意图、排除了什么、为什么**。

---

## 8. 路由评测集（Routing Eval Set）

每次误路由 **沉淀为样本**；每次改 IO/规则/Prompt **跑全量回归**。这才是「新需求不出错」的基础。

### 8.1 样本 schema

```json
{
  "id": "os_calc_negation_lark_001",
  "utterance": "给我打开windows计算器算20+70，不要打开lark",
  "expected_domain": "desktop_control",
  "expected_action": "calculate",
  "expected_tool": "mcp:windows_calculator_calculate",
  "forbidden_tools": ["mcp:windows_open_app", "mcp:windows_lark_send_message"],
  "expected_hidca_domain": "OS_CONTROL",
  "expected_tools_after_prune_max": 15,
  "expected_stripped_context": ["lark_chat_id"],
  "tags": ["negation", "voice", "regression"],
  "wrong_tool": "mcp:windows_open_app(lark)",
  "why_wrong": "negation not applied; first-match lark alias",
  "risk": "external_side_effect_misroute"
}
```

### 8.2 集内分类（V1 规模建议）

> **样本策略**：每个类别先补 **黄金正向样本**（期望路由正确）和 **黄金负向样本**（期望不调用某工具）；测试时同时断言「应该做什么」和「不应该做什么」，防止只通过正向不通过负向。

| 分类 | 条数 | 说明 |
|------|------|------|
| OS 本地操控 | ≥ 30 | 计算器、记事本、窗口、文件 |
| 否定 / 纠偏 | ≥ 15 | 「不要 Lark」「不是飞书」 |
| **带 Lark 上下文的 OS 请求** | ≥ 10 | 有 `lark_chat_id` 但仍应 `OS_CONTROL` + 剪枝 |
| **显式信号快速路径** | ≥ 10 | 「windows mcp 计算器 20+70」→ 快速执行，**不应触发澄清** |
| **多步序列意图** | ≥ 10 | 「算结果然后发给 Vivian」→ 两步独立路由 |
| Lark 正向 | ≥ 20 | 发送、读消息、开群（不可误伤） |
| Codex→Lark 办公流 | ≥ 15 | 项目简报类 |
| 模糊应澄清 | ≥ 20 | 「发一下」「打开那个」→ 必须 clarify |
| 闲聊 / 不应调工具 | ≥ 15 | 防止 over-tooling |

### 8.3 验收指标

| 指标 | 目标 |
|------|------|
| Eval Set 通过率 | 100%（PR 门禁） |
| OS 操控首次 tool 正确率 | ≥ 90% |
| user forbidden 时误开窗口 | **0** |
| 显式信号句触发不必要澄清 | **0**（用户说清楚了不该再问） |
| 模糊句 silent wrong execute | **0**（应 clarify 却执行） |
| ReAct 路径 `OS_CONTROL` 剪枝后 n_tools | ≤ 15（建议 ≤ 10） |
| OS 任务误带 Lark 工具入 prompt | **0** |
| 多步序列首步路由正确率 | ≥ 85% |

---

## 9. 实施阶段

| 阶段 | 内容 | 优先级 |
|------|------|--------|
| **Phase 0 — 止血** | 否定句不参与 app 检测；calculator+expression 优先；Eval 集建立 + 本案入集 | P0 |
| **Phase 1 — IO 骨架** | Intent Frame；多候选+反证；Router Evidence 落盘 | P0 |
| **Phase 2 — 匹配与门禁** | Capability Schema；Risk Gate；Consistency Check | P0 |
| **Phase 3 — HIDCA 剪枝** | Domain↔Tool 绑定表；`assemble_tool_pool` 后硬剪枝；Evidence 记 n_tools | P0 |
| **Phase 4 — HIDCA 沙盒** | OS 域 strip Lark implicit；域专用 System Prompt profile | P0 |
| **Phase 5 — 主动透明** | 执行时回复说明 negation 已应用；HUD Evidence 摘要 | P1 |
| **Phase 6 — 全通道接入** | Voice/Chat/IM 统一过 IO + Semantic Router | P1 |
| **Phase 7 — Prompt 块拆分** | `os_assistant` Local / Lark 块物理分离 | P1 |
| **Phase 8 — 澄清插件** | os_local_vs_lark；模糊句 clarify | P2 |
| **Phase 9 — 会话域粘性** | session_negations[]；域粘性 TTL；跨轮继承 | P2 |
| **Phase 10 — 多步序列** | 序列意图拆步；步间数据依赖传参 | P2 |
| **Phase 11 — LLM Router（可选）** | 低置信度 Few-shot 域分类 | P3 |

---

## 10. 代码与文档锚点

| 主题 | 路径 |
|------|------|
| 语音分流 | `clients/desktop/docs/VOICE_INTENT_ROUTING_AND_TASK_ORCHESTRATION.md` |
| Voice 规则 | `clients/desktop/src/voice/voiceIntentRouter.ts` |
| L3 Preflight | `l3_node/agent_preflight.py` |
| OS Mission Router | `l3_node/os_mission_router.py` |
| 语义槽位 | `l3_node/semantic_slot_parser.py` |
| 语义引擎 | `l3_node/semantic_intent_engine.py` |
| Capability 路由 | `l3_node/capability_router.py` |
| Capability 语义 | `l3_node/capability_semantic_registry.py` |
| 工具池 | `l3_node/primitives/tools/tool_pool.py` |
| Prompt 组装 | `l3_node/agent_core.py`（`_build_system_prompt`） |
| 工具池规范 | `docs/architecture/L3_TOOL_POOL_AND_MCP_ASSEMBLY.md` |
| OS 域 Prompt | `docs/capability_domains/os_assistant.md` |
| 模糊澄清 | `docs/L3_FUZZY_INTENT_CLARIFICATION.md` |
| 计算器单测 | `tests/unit/test_os_mission_router.py` |

---

## 11. 小结

**表面现象**：算加法却打开 Lark。  
**本质**：两条链都缺——

1. **IO 链**：任务理解 → 多候选仲裁 → 能力匹配 → 风险门禁 → 一致性兜底 → 不确定则澄清  
2. **HIDCA 链**：ReAct 前 **判域 → 剪枝 → 沙盒**，避免 **百级工具 + Lark 上下文** 把模型推成「IM 助手」

升级后系统应能回答：

> 我理解用户目标是什么 → 我知道属于哪个域 → **ReAct 只能看到该域的少量工具** → **上下文里没有不该有的 Lark 信号** → 我能解释为什么选这个工具

**Intent Orchestrator** 负责「**判对与判能不能做**」；**HIDCA** 负责「**让 ReAct 少犯错**」——模型看不见 Lark 工具、也感受不到 Lark 会话时，**指鹿为马** 的概率才会真正下来。计算器/Lark 案例是第一条 Eval 样本。

升级后系统还要做到三件此前没做到的事：

| 原来的问题 | 升级后的行为 |
|------------|--------------|
| 用户说了不要 Lark，系统还是开了 | `forbidden` + 剪枝双重保障，**物理**无法误调 |
| 用户说了工具和域，系统还是多此一举问 | **显式信号快速路径**（§5.10），直接执行 |
| 执行完用户不知道有没有生效 | **主动透明**（§6.5），回复里说出「已排除 Lark，用 Windows 计算器」 |

**一句话**：不是「让系统猜得更准」，而是「让系统真正理解用户说的每一句话，包括说了什么、不要什么、以及用户已经说清楚的时候就不要再问了」。

---

## 修订记录

| 日期 | 说明 |
|------|------|
| 2026-06-30 | 初稿：计算器/Lark 误路由案例与分层根因 |
| 2026-06-30 | v2：Intent Orchestrator、多候选反证、Capability 匹配、Consistency Check、Eval Set |
| 2026-06-30 | v3：吸收 HIDCA（Semantic Router、Dynamic Tool Pruning、Context Sandboxing）；补充工具过载与上下文污染根因 |
| 2026-06-30 | v4：补充 §5.10 显式信号快速路径；Phase 0 否定提取算法思路；§5.15 多步序列意图；§6.5 主动透明；§6.6 会话域粘性与否定持久性；最小动作原则；评测集新增快速路径与多步序列类别；小结强化三项原则对照表 |
