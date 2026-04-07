"""
实体消解：澄清态下优先于「漂移」判断；Top1/Top2 margin 过低则保持澄清。
候选结构：{"id": str, "label": str, "score": float 可选}
"""
from __future__ import annotations

from typing import Any


def try_resolve_entity_candidates_sync(
    candidates: list[dict[str, Any]],
    user_reply: str,
    *,
    min_margin: float,
) -> dict[str, Any]:
    ur = (user_reply or "").strip()
    if not ur or not candidates:
        return {"resolved": False}

    items = [c for c in candidates if isinstance(c, dict) and (c.get("label") or c.get("name"))]
    if not items:
        return {"resolved": False}

    def _label(c: dict[str, Any]) -> str:
        return str(c.get("label") or c.get("name") or "").strip()

    def _base_score(c: dict[str, Any]) -> float:
        try:
            return float(c.get("score") if c.get("score") is not None else 0.5)
        except (TypeError, ValueError):
            return 0.5

    scored: list[tuple[float, dict[str, Any], bool]] = []
    urf = ur.casefold()
    for c in items:
        lb = _label(c)
        if not lb:
            continue
        lbf = lb.casefold()
        match = urf == lbf or lbf in urf or urf in lbf
        adj = _base_score(c) + (0.4 if match else 0.0)
        scored.append((adj, c, match))

    if not scored:
        return {"resolved": False}

    scored.sort(key=lambda x: x[0], reverse=True)
    top_s, top_c, top_m = scored[0]
    second_s = scored[1][0] if len(scored) > 1 else 0.0

    if not top_m:
        return {"resolved": False}

    if (top_s - second_s) < float(min_margin):
        return {
            "ambiguous": True,
            "reason": "top1_top2_margin",
            "top": {"id": top_c.get("id"), "label": _label(top_c)},
            "second": {"id": scored[1][1].get("id"), "label": _label(scored[1][1])} if len(scored) > 1 else None,
        }

    return {
        "resolved": True,
        "choice_id": top_c.get("id"),
        "label": _label(top_c),
    }
