# Jachin Memory Governance and Confidence Architecture

生成时间：2026-07-14

本文定义 Jachin 记忆系统的治理规则。它是 `09_ai_self_growing_knowledge_system_plan.md` 的补充，重点解决“记忆如何变可信、如何变弱、如何冲突、如何被 Skill/MCP 使用、如何被复盘”的问题。

## 1. 核心原则

Jachin 的记忆不能只是一个收藏夹，也不能只是向量库命中结果。每一条可用于决策的记忆都必须具备：

- 来源：来自用户确认、工具验证、任务闭环、Skill 运行、MCP 运行、文件证据或人工配置。
- 类型：偏好、别名、纠错、项目事实、联系人、任务状态、失败经验、工具习惯等。
- 层级：turn、session、working、long_term。
- 作用域：global、skill、project、app、contact 等。
- 质量：confidence、hit_count、success_count、failure_count、last_verified_at。
- 治理状态：active、expired、review_required、conflicted、rejected。
- 证据链：能回到 raw evidence、TaskLedger、VerificationReport 或用户确认记录。

## 2. 四层记忆

| 层级 | 生命周期 | 写入条件 | 用途 |
| --- | --- | --- | --- |
| `turn` | 当前轮 | 输入、状态快照、临时解析 | 处理本轮推理 |
| `session` | 当前会话 | 对话上下文、临时引用 | 解决“刚才/那个/继续” |
| `working` | 数天到数周 | 最近动作、任务状态、失败经验 | 支撑 OS 助手连续任务 |
| `long_term` | 长期 | 用户确认、高置信工具验证、多次稳定成功 | 偏好、别名、项目事实、Skill 配置 |

短期记忆默认可以自动写入，但必须有 TTL。长期记忆不能因为一次猜测写入，必须带来源、置信度和可回滚信息。

## 3. 置信度闭环

记忆的置信度不是固定值，而是运行时反馈结果。

### 3.1 初始置信度

- 用户明确确认：高。
- 工具验证成功：中高。
- 模型推断：中低。
- 需要用户确认但尚未确认：低。
- 来自失败链路：中低，仅作为恢复提示。

### 3.2 命中后反馈

每次记忆被召回并参与执行后，必须根据结果回写：

- 成功：`success_count += 1`，置信度小幅提高，更新 `last_verified_at`。
- 失败：`failure_count += 1`，置信度下降。
- 连续失败或失败数超过成功数：设置 `review_required=true`。
- 待复核记忆召回时必须降权，必要时重新询问用户。

## 4. 冲突治理

记忆冲突不能靠最新一条简单覆盖旧记忆。以下场景必须进入治理队列：

- 同一个 alias/correction 指向不同对象。
- 当前状态与长期记忆相反。
- 用户纠正了系统曾经自动学习的内容。
- 工具验证反复失败。
- Skill/MCP 升级导致旧配置不再适用。

治理流程：

1. 暂停或降权冲突记忆。
2. 写入 `review_required` 或 `conflicts/`。
3. 控制台展示给用户或 Weekly Review。
4. 用户确认或系统复盘后合并、废弃、降级或保留。
5. 治理动作本身写回 raw evidence。

## 5. Skill/MCP 作用域

业务 Skill 可以拥有自己的记忆域，但不能脱离统一 Memory Nexus。

推荐字段：

```json
{
  "domain": "skill",
  "skill_id": "com.jachin.skill.pmo-copilot",
  "owner": "user",
  "memory_type": "project_fact"
}
```

例子：

- PMO：项目多维表、推送群、战报格式、风险判断经验。
- 英语助手：词书进度、熟悉度、错词本、例句偏好。
- 桌面执行 Agent：App 别名、常用路径、打开/关闭失败经验。

主系统只负责统一写入、召回、评分、复核和过期。Skill/MCP 只声明自己的记忆类型、作用域和使用场景。

## 6. 记忆写入门控

`MemoryWriteAgent` 写入前必须判断：

- 是否是稳定事实。
- 是否只是当前任务状态。
- 是否是用户偏好或纠错。
- 是否和现有记忆冲突。
- 是否需要用户确认。
- 应该进入哪一层记忆。
- 是否需要绑定 skill_id/domain。

禁止把一次性猜测、临时模型判断、未验证工具结果直接写成长事实。

## 7. 召回排序

记忆召回不能只看关键词。排序必须综合：

- 文本相关性。
- confidence。
- success_count / failure_count。
- 是否 review_required。
- 层级：turn/session/working/long_term。
- 最近使用时间。
- 当前任务域、App、Skill、文件、联系人是否匹配。

例如用户说“关闭”，应优先召回最近动作和当前窗口；用户说“打开 lock”，如果曾确认 `lock -> Lark` 且近期成功，应直接命中；如果该纠错近期失败多次，应重新询问。

## 8. 可视化和复盘

Memory Center / Memory Growth 页面应展示：

- 最近学习了什么。
- 哪些记忆命中过。
- 哪些记忆失败过。
- 哪些记忆需要确认。
- 哪些记忆即将过期。
- 哪些 Skill/MCP 正在使用哪些记忆。
- 每条记忆的证据链和治理记录。

Weekly Review 应复盘：

- 高频成功记忆是否升级。
- 高频失败记忆是否降权。
- 临时记忆是否应该沉淀为长期事实。
- 长期记忆是否陈旧。
- Skill/MCP 的专属记忆是否污染了全局域。

## 9. 当前工程落点

当前实现以 `memory_lifecycle.py` 作为本地 lifecycle index，以 `memory_confidence.py` 作为统一质量规则层。

已覆盖：

- 初始置信度计算。
- 记忆层级分类。
- Skill/domain/owner 作用域提取。
- 成功/失败反馈。
- 待复核标记。
- 召回排序降权。
- 实体纠错记忆接入统一 lifecycle。
- Memory Growth pending queue 展示 `review_required` 记忆。

后续优先级：

1. Memory Center 增加可编辑、删除、合并、确认按钮。
2. Skill manifest 增加 memory domain schema。
3. Weekly Review 对 lifecycle 质量进行趋势统计。
4. MemoryWriteAgent 写入前做冲突检测。
5. RecallAgent 按 domain/skill_id 精准过滤，防止业务记忆污染全局。
