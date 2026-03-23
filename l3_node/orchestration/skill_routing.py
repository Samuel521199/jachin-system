"""
L1 — Skill 发现 / 路由（大规模技能 → 候选技能元数据）。

封装 `SemanticRouter.match_local_skill`，供编排与调试；不替代 Agent 内 `allowed_skills`。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from l3_node.orchestration.layers import OrchestrationLayer

logger = logging.getLogger(__name__)


def _nexus_path() -> Path:
    return Path.home() / ".jachin" / "nexus_config.json"


def load_orchestration_config() -> dict[str, Any]:
    try:
        p = _nexus_path()
        if not p.exists():
            return {}
        cfg = json.loads(p.read_text(encoding="utf-8"))
        sec = cfg.get("orchestration")
        return sec if isinstance(sec, dict) else {}
    except Exception as e:
        logger.debug("[Orchestration L1] 读取 orchestration 配置失败: %s", e)
        return {}


def is_skill_routing_enabled() -> bool:
    return bool(load_orchestration_config().get("skill_routing_enabled", True))


def suggest_skills_from_intent(
    intent: str,
    *,
    threshold: float | None = None,
    top_k: int = 1,
) -> dict[str, Any]:
    """
    向量路由建议（LanceDB skills 表须已建）。当前实现返回 **最佳一条**（与 vector_router 一致）。

    top_k > 1 时仍只返回至多 1 条（后续可扩展多路检索）。
    """
    _ = top_k
    out: dict[str, Any] = {
        "layer": int(OrchestrationLayer.SKILL_ROUTING),
        "enabled": False,
        "intent": (intent or "").strip(),
        "matches": [],
        "primary": None,
    }
    if not is_skill_routing_enabled():
        return out
    th = threshold
    if th is None:
        try:
            th = float(load_orchestration_config().get("vector_router_threshold", 0.75))
        except (TypeError, ValueError):
            th = 0.75
    try:
        from core.vector_router import SemanticRouter

        router = SemanticRouter()
        hit = router.match_local_skill(intent, threshold=th)
    except Exception as e:
        logger.warning("[Orchestration L1] match_local_skill 失败: %s", e)
        out["error"] = str(e)
        return out

    out["enabled"] = True
    if hit:
        m = {
            "skill_id": hit.get("skill_id", ""),
            "path": hit.get("path", ""),
            "description": hit.get("description", ""),
            "score": hit.get("score", 0.0),
        }
        out["matches"] = [m]
        out["primary"] = m
    return out
