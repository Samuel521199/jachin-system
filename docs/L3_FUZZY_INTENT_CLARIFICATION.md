# L3 模糊意图澄清（Fuzzy Intent Clarification）

**版本**: 1.0  
**代码入口**: `l3_node/intent_clarification.py`  
**与精确遥控的关系**: 各通道应先匹配**精确指令**（如飞书 `lark_workflow_command_interceptor` 内停收网、分析、继续、进度等），**均未命中**后再调用本框架。

---

## 1. 目标

- 用户说法**不标准**但明显像某种遥控时，系统**主动反问**，请用户发**明确短指令**，减少「必须背指令词」的摩擦。
- **不替代 LLM**：单独「好的」「同意」等仍走会话与 Agent，避免误伤闲聊。
- **全 L3 可扩展**：HR·飞书仅为第一个域插件；BI、Shell、其他 IM 可各增插件。

---

## 2. 架构

| 组件 | 路径 | 职责 |
|------|------|------|
| 引擎 | `l3_node/intent_clarification.py` | `ClarificationRule`、`try_fuzzy_clarification`、默认规则集汇总、按 `channel_id` 的冷却去重 |
| HR 精确词表 | `l3_node/hr_lark_command_lexicon.py` | 分析/继续/进度/停止 inject 等**精确**谓词，供拦截器与 HR 插件共用，避免循环依赖 |
| HR 模糊插件 | `l3_node/intent_clarification_plugins/hr_recruitment_lark.py` | 招聘场景模糊正则 + 反问文案；内部调用 lexicon 排除已精确命中句 |
| 调用方 | `try_lark_workflow_command_intercept(text, channel_id=...)` 等 | 精确逻辑走完后调用 `try_default_l3_fuzzy_clarification` |

**数据流**：

1. 用户文本 → 通道内**精确**遥控（若命中则返回，结束）。  
2. 否则 → `try_default_l3_fuzzy_clarification(text, channel_id=...)`。  
3. 引擎按 `priority` 升序遍历规则，**第一条** `test(text)` 为真则返回 `reply`（并做冷却）。  
4. 无命中 → `None`，交由下游 LLM / 其他路由。

---

## 3. ClarificationRule 约定

- **`rule_id`**：`域:意图`，如 `hr_lark:analyze`，用于日志与冷却键。  
- **`priority`**：整数，**越小越先**评估；同句只命中一条规则。  
- **`test(text)`**：返回 True 表示应澄清；插件**必须**自行排除与本域精确指令冲突的情况。  
- **`reply`**：直接展示给用户的固定文案（可含 Markdown）。

---

## 4. 冷却（Cooldown）

- 键：`(channel_id, rule_id, text.casefold())`；默认 **12s** 内重复则返回通用重复提示（见 `DEFAULT_COOLDOWN_REPEAT_REPLY`）。  
- **`channel_id`**：建议传飞书 `chat_id`、WS `chat_id`、HTTP `session_id` 等，实现**按会话**隔离；不传则退化为 `global`。

---

## 5. 新增业务域（检查清单）

1. 若与某通道的**精确**指令共享谓词，将精确部分抽到独立模块（如 lexicon），避免 `intent_clarification_plugins` ↔ `interceptor` 循环 import。  
2. 在新文件 `intent_clarification_plugins/<domain>.py` 中实现 `xxx_clarification_rules() -> list[ClarificationRule]`。  
3. 在 `intent_clarification.default_l3_clarification_rules()` 中**汇总**追加（保持顺序与 priority 约定）。  
4. 在对应通道「精确未命中」分支调用 `try_fuzzy_clarification(..., channel_id=...)` 或扩展 `default_l3` 规则集。  
5. 更新本文档「插件列表」与 Cursor 规则 `085-l3-fuzzy-intent-clarification.mdc`。

---

## 6. 当前插件列表

| 插件 | rule_id 前缀 | 说明 |
|------|----------------|------|
| `hr_recruitment_lark` | `hr_lark:*` | 分析 / 继续收网 / 停抓 / 进度 / 同意+动作 等模糊说法 |

---

## 7. 修订记录

| 日期 | 说明 |
|------|------|
| 2026-03-24 | 从 Lark 拦截器抽出通用框架与 HR 插件，补充本文档 |
