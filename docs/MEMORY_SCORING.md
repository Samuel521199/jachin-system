# 记忆排序与 Reinforce 单一公式（memory_score）

本文档为 **L2 混合检索 + P2-9 强化** 的可解释单一事实来源；实现见 `core/db/memory_score.py`、`core/db/memory_reinforcement.py`、`core/db/l2_memory_lancedb.py`。

**产品叙事**（与 OpenClaw 话术对齐、强调 Jachin 分层能力）：见 **[MEMORY_WRITE_AND_SCORE_NARRATIVE.md](./MEMORY_WRITE_AND_SCORE_NARRATIVE.md)**。

## 0. API 可解释字段

`GET /api/v2/memory/search?q=...&explain=true`（需 `X-Sub-Account-Id`）时，每条 `results[]` 在 `id` / `content` / `created_at` 之外可含：

- `explain.memory_scoring_profile`：`A_sum_cap` / `B_l2norm_cap`
- `explain.vec_score`、`explain.bm25_norm`、`explain.vector_weight`、`explain.text_weight`
- `explain.base_hybrid`、`explain.reinforce_bonus`、`explain.total_rank_score`
- `explain.formula_ref`：指向本文

**纯向量**模式（`hybrid=false`）下 `base_hybrid` 与历史实现一致为 **向量分量 + reinforce**（`bm25_norm=0`）；混合模式下为 **加权向量+BM25 + reinforce**。

## 1. 检索总分（L2 hybrid）

对每条候选记忆：

1. **向量分量** `vec_score`：LanceDB 余弦距离 `dist` → `vec_score = max(0, 1 - dist/2)`（`dist≤2`）。
2. **BM25 分量** `bm25_norm`：对候选正文与 query 做 BM25，再按批次 `max` 归一化到 `[0,1]`。
3. **混合基分**
   `base = vector_weight * vec_score + text_weight * bm25_norm`
   权重默认 `vector_weight=0.7`, `text_weight=0.3`，可由 `nexus_config.json` → **`memory_scoring`** 覆盖。
4. **强化增量** `reinforce_bonus`：见下文 §2。
5. **排序分** `_hybrid_score = base + reinforce_bonus`。

可选 **MMR 重排**（默认开启）：在按 `_hybrid_score` 排序后，取前 `limit * mmr_pool_multiplier` 条，用正文 token Jaccard 作相似度，以 `mmr_lambda` 做最大边际相关贪心，输出 `limit` 条，减轻语义重复（对标 OpenClaw memory_search 类产品体验的一部分）。

## 2. Reinforce 合并（侧车 + 行内）

- **侧车**：`~/.jachin/memory/memory_reinforcement.json` 按 `memory_id` 累加。
- **行内**：LanceDB 行可选列 `reinforce_score`。

**合并 raw（进入饱和前）** — `profile` 来自 `memory_scoring.profile`：

| Profile | 公式 |
|--------|------|
| `A_sum_cap`（默认） | `merged_raw = min(max_boost, sidecar + row)` |
| `B_l2norm_cap` | `merged_raw = min(max_boost, sqrt(sidecar² + row²))` |

`max_boost` 由调用方传入（与 `intelligence_p2.reinforce_max_boost` 等运行时配置一致）。

**饱和 bonus**（加到 hybrid 上，避免无界放大）：

```text
reinforce_bonus = reinforce_weight * (1 - exp(-merged_raw))
```

`reinforce_weight` 默认与 `intelligence_p2.reinforce_weight` 对齐（可由 `memory_scoring` 覆盖键名时以 nexus 合并结果为准）。

## 3. nexus 配置示例

```json
{
  "memory_scoring": {
    "profile": "A_sum_cap",
    "vector_weight": 0.7,
    "text_weight": 0.3,
    "mmr_enabled": true,
    "mmr_lambda": 0.55,
    "mmr_pool_multiplier": 3
  },
  "intelligence_p2": {
    "reinforce_weight": 0.12,
    "reinforce_max_boost": 8.0
  }
}
```

**A/B 建议**：生产默认 `A_sum_cap`；若侧车与行内分常同时偏高、体感「重复加分」，可试 `B_l2norm_cap` 做对照。

## 4. UI 点赞与 API

- **精确到条目的强化**：`POST /api/v2/memory/reinforce`（`memory_id` + `delta`）。
- **UI 闭环（推荐）**：`POST /api/v2/memory/feedback`，body：`{ "memory_id": "...", "vote": "up"|"down" }`，可选 `delta` 覆盖默认；默认增量可在 `intelligence_ui.memory_feedback_up` / `memory_feedback_down` 配置。
- 请求会写入侧车并追加 `intelligence_events.jsonl` 类型 `ui_memory_thumbs_up` / `ui_memory_thumbs_down`（**不再**经 `intelligence_e` 聚合到 `_intel_from_events`，避免与单条 `memory_id` 双计）。

## 5. L3 本地检索（断网 / Memory Nexus）

工具 **`core:local_memory_search`**：`l3_node/local_memory_search.py` → **`deep_search`**（**SQLite + FastEmbed**，`~/.jachin/palace_db/memory_nexus.sqlite3`），按查询做 **向量语义检索**，返回 `matches[]`（含 `wing`/`room`/距离）。与 L2 hybrid 公式独立；产品位对标「单工具语义检索」。权威说明：**[architecture/MEMORY_NEXUS_L3.md](./architecture/MEMORY_NEXUS_L3.md)** · **[arch/04_MEMORY_ARCHITECTURE.md](./arch/04_MEMORY_ARCHITECTURE.md)**。

## 6. apply_patch 与 Python AST

`nexus_config.json`：

```json
{
  "apply_patch": {
    "python_ast_validate": true
  }
}
```

或为单次调用传入 `python_ast_validate: true`（`core:apply_patch`），在落盘前对 `.py` 结果做 `ast.parse` 预检。
