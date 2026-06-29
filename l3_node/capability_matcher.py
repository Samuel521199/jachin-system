"""Capability matcher for semantic task routing."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from l3_node.capability_embedding_index import CapabilityMatch, match_capabilities
from l3_node.capability_router import choose_capability_route
from l3_node.capability_semantic_registry import CapabilityDescriptor, build_capability_registry
from l3_node.mission_intent_schema import CapabilityRoute, MissionTaskType
from l3_node.task_understanding_engine import TaskUnderstanding, infer_task_understanding


@dataclass
class CapabilityMatchResult:
    selected: CapabilityDescriptor | None
    route: CapabilityRoute
    candidates: list[CapabilityMatch] = field(default_factory=list)
    reason: str = ""
    confidence: float = 0.0
    understanding: TaskUnderstanding | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected": self.selected.to_dict() if self.selected else None,
            "route": self.route.to_dict(),
            "candidates": [item.to_dict() for item in self.candidates],
            "reason": self.reason,
            "confidence": self.confidence,
            "understanding": self.understanding.to_dict() if self.understanding else None,
        }


def _tool_ids(tools: list[dict[str, Any]] | None) -> set[str]:
    out: set[str] = set()
    for item in tools or []:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("id") or item.get("name") or "").strip()
        if not raw:
            continue
        out.add(raw)
        if raw.startswith("mcp:"):
            out.add(raw.removeprefix("mcp:"))
        else:
            out.add(f"mcp:{raw}")
    return out


def _allowed(tool_id: str, allowed: list[str] | None) -> bool:
    if allowed is None:
        return True
    allowed_set = {str(x).strip() for x in allowed if str(x).strip()}
    return tool_id in allowed_set or tool_id.removeprefix("mcp:") in allowed_set


def _available(capability: CapabilityDescriptor, tools: list[dict[str, Any]] | None, allowed: list[str] | None) -> bool:
    ids = _tool_ids(tools)
    if not ids:
        return True
    return (capability.id in ids or capability.id.removeprefix("mcp:") in ids) and _allowed(capability.id, allowed)


def match_task_to_capability(
    user_input: str,
    tools: list[dict[str, Any]] | None,
    allowed: list[str] | None = None,
    *,
    limit: int = 6,
) -> CapabilityMatchResult:
    understanding = infer_task_understanding(user_input)
    registry = build_capability_registry(tools)
    candidates = match_capabilities(user_input, registry, limit=limit)
    available_candidates = [item for item in candidates if _available(item.capability, tools, allowed)]

    route = choose_capability_route(understanding.intent, tools or [], allowed)
    selected: CapabilityDescriptor | None = None
    reason = ""

    if route.ok:
        for item in registry:
            if item.id == route.tool_id or item.id.removeprefix("mcp:") == route.tool_id.removeprefix("mcp:"):
                selected = item
                break
        reason = "mission_schema_route_won"
    elif available_candidates:
        selected = available_candidates[0].capability
        route = CapabilityRoute(
            ok=True,
            tool_id=selected.id,
            workflow_id=selected.workflow_id,
            reason="semantic_capability_match",
            required_slots=list(selected.inputs),
            missing_slots=list(understanding.missing_slots),
        )
        reason = "semantic_match_won"
    else:
        route = CapabilityRoute(
            ok=False,
            reason="no_available_capability_match",
            missing_slots=list(understanding.missing_slots),
        )
        reason = "no_available_candidate"

    confidence = max(
        understanding.confidence,
        available_candidates[0].score if available_candidates else 0.0,
        0.0,
    )
    if understanding.intent.task_type == MissionTaskType.UNKNOWN and selected is not None:
        confidence = min(confidence, available_candidates[0].score if available_candidates else confidence)

    return CapabilityMatchResult(
        selected=selected,
        route=route,
        candidates=candidates,
        reason=reason,
        confidence=round(float(confidence), 3),
        understanding=understanding,
    )
