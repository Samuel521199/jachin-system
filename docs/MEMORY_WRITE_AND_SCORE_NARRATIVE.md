# 记忆写入节奏 × 统一排序 — 产品叙事（对标 OpenClaw）

OpenClaw 在社区叙事上强在 **memoryFlush**、**MEMORY.md**、**planning-files** 等 **简单好讲** 的故事。Jachin 工程能力已更 **分层**（L2 向量 + L3 本地 + 梦境 + 锚点），本文用 **同一套话术** 对外说明，减少「双轨混乱」体感。

## 1. 一句话

**Jachin 用「Compaction 前刷新 → 核心记忆 + 工作区锚点 → 可审计 checkpoint」保证长会话不断档；用「混合检索 + 单一公式化的排序分 + 可选可解释字段」保证检索越用越准。**

## 2. 记忆写入节奏（对齐 OpenClaw memoryFlush / 锚点）

| 环节 | Jachin 行为 | 配置入口 |
|------|-------------|----------|
| **即将压上下文** | `memory_flush`：静默 LLM 回合 → `core_memory` | `llm.memory_flush.enabled`, `soft_threshold` |
| **重置/新会话前** | `run_pre_reset_memory_flush` | context reset API |
| **工作区必须更新** | `workspace_must_update` + mtime 校验 | `llm.memory_flush.workspace_must_update` |
| **锚点仍 stale** | `second_llm` / `touch_workspace_anchors` | `anchor_remediate` |
| **专用静默写文件（可选）** | `silent_anchor_file_round`：白名单路径 JSON 直写 | `llm.memory_flush.silent_anchor_file_round` |
| **压缩后审计** | `post_compaction_audit`、`findings` MACHINE_CHECKPOINT、`l3_local` | `post_compaction_audit` |

**相对 OpenClaw**：Jachin 多 **L2 梦境提纯**、**子账号/命名空间**、**多节点**；叙事上强调 **「可配置 + 可验收（审计 JSONL）」** 而非仅「模型自觉写文件」。

## 3. 统一 memory_score「体感」

- **公式单一事实源**：[MEMORY_SCORING.md](./MEMORY_SCORING.md)  
- **侧车 + 行内 reinforce**：在公式里先 **合并为 `merged_raw`**（profile **A_sum_cap** / **B_l2norm_cap**），再进入 **饱和 bonus**，避免「两个分谁说了算」说不清的观感。
- **API 可观测**：`GET /api/v2/memory/search?explain=true` 返回每条 `explain`：`vec_score`、`bm25_norm`、`reinforce_bonus`、`total_rank_score`、`memory_scoring_profile` — 便于控制台/Lark **展示「为什么排第一」**，产品层可压过「只有社区口头 memory_score」的 OpenClaw 叙事。

## 4. 隐式反馈闭环

- **显式**：`POST /api/v2/memory/reinforce`、`POST /api/v2/memory/feedback`（vote）  
- **隐式**：[IMPLICIT_SIGNALS.md](./IMPLICIT_SIGNALS.md)（跳过/停留/追问）→ `intelligence_e` 可选加权  

对外可概括为：**「显式点赞 + 隐式行为 → 同一套排序公式里的 reinforce 分量」**。

## 5. 建议对外话术（简版）

> Jachin 在压上下文和重置前会 **主动刷新记忆**，并要求 **工作区锚点文件** 与 **findings/progress** 等 checkpoint **可机器验收**；检索使用 **向量 + 关键词 + 用户反馈合并成的统一排序分**，并可在 API 中 **展开每一项分量**。这比单一 Markdown 记忆更 **适合企业多账号与多节点**；若喜欢 OpenClaw 的极简文件模型，可把 **`workspace_must_update`** 配成 `memory/MEMORY.md` + `task_plan.md` 获得相近 **使用习惯**。
