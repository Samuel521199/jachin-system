"""
统一记忆排序与 reinforce 可解释性（文档见 docs/MEMORY_SCORING.md）。

- merged_reinforce_raw：侧车 + 行内分合并为单一 raw（支持 profile A/B）
- saturated_bonus：检索加权增量（与 LanceDB hybrid 一致）
- load_memory_scoring_config：nexus memory_scoring / intelligence_p2 兼容
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

_NEXUS = Path.home() / ".jachin" / "nexus_config.json"


def load_memory_scoring_config() -> dict[str, Any]:
    """合并 nexus 中 memory_scoring 与 intelligence_p2（P2-9）字段。"""
    out: dict[str, Any] = {
        "profile": "A_sum_cap",
        "vector_weight": 0.7,
        "text_weight": 0.3,
        "mmr_enabled": True,
        "mmr_lambda": 0.55,
        "mmr_pool_multiplier": 3,
        "reinforce_weight": 0.12,
        "reinforce_max_boost": 8.0,
    }
    if not _NEXUS.exists():
        return out
    try:
        cfg = json.loads(_NEXUS.read_text(encoding="utf-8"))
    except Exception:
        return out
    ms = cfg.get("memory_scoring")
    if isinstance(ms, dict):
        for k, v in ms.items():
            if v is not None:
                out[k] = v
    p2 = cfg.get("intelligence_p2")
    if isinstance(p2, dict):
        # 与 _p2_reinforce_params 一致：检索加权用 reinforce_weight（可选别名 reinforce_retrieval_weight）
        rw = p2.get("reinforce_retrieval_weight")
        if rw is None:
            rw = p2.get("reinforce_weight")
        if rw is not None:
            out["reinforce_weight"] = float(rw)
        if p2.get("reinforce_max_boost") is not None:
            out["reinforce_max_boost"] = float(p2["reinforce_max_boost"])
    return out


def merged_reinforce_raw(
    sidecar: float,
    row: float,
    *,
    max_boost: float,
    profile: str | None = None,
) -> float:
    """
    单一 reinforce 合并 raw（进入饱和函数前）。

    Profile:
    - A_sum_cap（默认）: raw = min(max_boost, side + row) — 与历史 P2-9 一致
    - B_l2norm_cap: raw = min(max_boost, sqrt(side^2 + row^2)) — 减轻双轨重复计数感
    """
    prof = (profile or "A_sum_cap").strip()
    side = max(0.0, float(sidecar))
    row = max(0.0, float(row))
    cap = max(0.01, float(max_boost))
    if prof == "B_l2norm_cap":
        return min(cap, math.sqrt(side * side + row * row))
    return min(cap, side + row)


def saturated_bonus(raw: float, *, weight: float) -> float:
    """bonus = weight * (1 - exp(-raw))，与 hybrid_reinforce_bonus 一致。"""
    w = max(0.0, float(weight))
    r = max(0.0, float(raw))
    if w <= 0 or r <= 0:
        return 0.0
    return w * (1.0 - math.exp(-r))


def explain_hybrid_score(
    vec_score: float,
    bm25_norm: float,
    *,
    vector_weight: float,
    text_weight: float,
    reinforce_bonus: float,
) -> dict[str, float]:
    """供日志/调试：可解释的排序分量。"""
    base = float(vector_weight) * float(vec_score) + float(text_weight) * float(bm25_norm)
    return {
        "vec_score": float(vec_score),
        "bm25_norm": float(bm25_norm),
        "vector_weight": float(vector_weight),
        "text_weight": float(text_weight),
        "base_hybrid": round(base, 6),
        "reinforce_bonus": float(reinforce_bonus),
        "total": round(base + float(reinforce_bonus), 6),
    }
