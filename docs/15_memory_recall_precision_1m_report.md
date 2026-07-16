# Million-scale Local Memory Recall Stress Report

Date: 2026-07-15

## Goal

This test only targets the memory system. It does not test Lark, Calculator, Browser, file opening, or other desktop workflows. The goal is to verify whether Jachin can recall target memories accurately, stably, and explainably when the local lifecycle memory store contains 1,000,000 noisy records.

## Three-layer Recall Architecture

### Layer 1: inverted-index keyword recall

The memory lifecycle store indexes content, tags, memory_type, domain, owner, skill_id, and evidence fields such as governance_key, entity_key, app_key, project_key, target_id, and type.

A query first uses query terms to retrieve candidates from the inverted index. Technical identifiers such as `switch_existing_window`, `post-send`, and `app_key` are split into searchable natural-language terms while keeping compact forms available.

### Layer 2: rule-score coarse ranking

Candidate memories are ranked with deterministic score signals:

- keyword hit count;
- confidence;
- hit_count;
- success_count;
- failure_count;
- review_required;
- memory layer;
- recency.

This layer keeps recall fast and predictable, while lowering the rank of failed, stale, conflicting, expired, low-confidence, or review-required memories.

### Layer 3: normalized dot-product rerank

The top 64 coarse candidates are reranked with local normalized hash vectors. Query and candidate vectors are L2-normalized, so dot product is equivalent to cosine similarity.

This gives the system a lightweight semantic tie-breaker without scanning all million records as vectors.

## Test Commands

```powershell
python -m pytest -o addopts= -q tests\unit\test_memory_stress_mvp.py tests\unit\test_memory_quality_governance.py tests\unit\test_memory_recall_precision.py
python scripts\memory_recall_precision_stress.py --noise-count 100000 --skip-governance
python scripts\memory_recall_precision_stress.py --noise-count 1000000 --skip-governance
```

## Unit Test Result

```text
10 passed
```

Covered areas:

- duplicate storm handling;
- expired-memory filtering;
- Chinese compact query recall;
- evidence key recall;
- noisy recall precision;
- underscore and hyphen tool-name recall;
- normalized dot-product math;
- three-layer Evidence marker.

## 100k Hot-index Result

```text
Noise memories: 100000
Top1 rate: 1.0
Top3 rate: 1.0
MRR: 1.0
Avg recall: 26.47 ms
Max recall: 103 ms
Seed elapsed: 24782 ms
Index warmup elapsed: 5989 ms
Index terms: 1203
Index postings: 1013551
Result: PASS
```

Evidence:

```text
output\memory_recall_precision\20260715_114129\memory_recall_precision_stress.evidence.json
```

## 1M Hot-index Result

```text
Noise memories: 1000000
Top1 rate: 1.0
Top3 rate: 1.0
MRR: 1.0
Avg recall: 157.27 ms
Max recall: 649 ms
Seed elapsed: 260026 ms
Index warmup elapsed: 57025 ms
Index terms: 1203
Index postings: 10133882
Result: PASS
```

Evidence:

```text
output\memory_recall_precision\20260715_105408\memory_recall_precision_stress.evidence.json
```

## Conclusion

Jachin Memory Lifecycle now has a clear three-layer recall pipeline:

1. inverted-index keyword candidate recall;
2. rule-score coarse ranking;
3. normalized dot-product rerank.

At 1,000,000 noisy memories, the hot-index path still keeps target recall at Top1 and controls average recall below 200 ms. The current implementation is suitable for 100k to 1M hot-index local recall. For 10M-scale memory, the next architecture step should be persistent SQLite FTS / BM25 with background warmup and incremental index updates.
