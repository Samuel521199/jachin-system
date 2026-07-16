"""Capability intelligence profiles for Skill/MCP routing.

This layer lets capability metadata explain how a tool should be used:
preconditions, verification, recovery, dependencies, and quality gaps.  The
kernel can route by these profiles instead of hard-coding every Skill/MCP.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from .ledger import append_event


@dataclass(slots=True)
class CapabilityIntelligenceProfile:
    capability_id: str
    task_type: str
    domain: str
    risk: str
    preconditions: list[dict[str, Any]] = field(default_factory=list)
    verification_methods: list[dict[str, Any]] = field(default_factory=list)
    recovery_paths: list[dict[str, Any]] = field(default_factory=list)
    required_mcps: list[str] = field(default_factory=list)
    required_models: list[str] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)
    missing_metadata: list[str] = field(default_factory=list)
    routing_terms: list[str] = field(default_factory=list)
    source: str = ""
    quality_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _descriptor_dict(descriptor: Any) -> dict[str, Any]:
    to_dict = getattr(descriptor, "to_dict", None)
    if callable(to_dict):
        return dict(to_dict())
    return dict(descriptor)


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _infer_preconditions(data: dict[str, Any], metadata: dict[str, Any]) -> list[dict[str, Any]]:
    preconditions: list[dict[str, Any]] = []
    for item in _list(metadata.get("preconditions") or metadata.get("required_state")):
        if isinstance(item, dict):
            preconditions.append(dict(item))
        elif item:
            preconditions.append({"kind": "metadata", "value": str(item)})
    for mcp in _list(metadata.get("required_mcps")):
        preconditions.append({"kind": "required_mcp", "id": str(mcp)})
    for model in _list(metadata.get("required_models")):
        preconditions.append({"kind": "required_model", "id": str(model)})
    inputs = [str(v) for v in _list(data.get("inputs")) if str(v)]
    for input_name in inputs:
        if input_name in {"recipients", "recipient", "chat_id", "to"}:
            preconditions.append({"kind": "slot", "name": "recipient", "required": True})
        elif input_name in {"message", "content", "text"}:
            preconditions.append({"kind": "slot", "name": "message", "required": True})
        elif input_name in {"app", "app_name"}:
            preconditions.append({"kind": "slot", "name": "app", "required": True})
        elif input_name in {"path", "file_path", "directory"}:
            preconditions.append({"kind": "slot", "name": "path", "required": True})
        elif input_name:
            preconditions.append({"kind": "slot", "name": input_name, "required": False})
    return preconditions


def _verification_methods(data: dict[str, Any], metadata: dict[str, Any]) -> list[dict[str, Any]]:
    methods: list[dict[str, Any]] = []
    for item in _list(metadata.get("verification") or metadata.get("verification_methods")):
        if isinstance(item, dict):
            methods.append(dict(item))
        elif item:
            methods.append({"method": str(item), "source": "metadata"})
    for evidence in _list(data.get("evidence")):
        if evidence:
            methods.append({"method": str(evidence), "source": "descriptor_evidence"})
    return methods


def _recovery_paths(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    playbook = metadata.get("recovery_playbook") or {}
    if isinstance(playbook, dict):
        targets = playbook.get("targets") or playbook.get("paths") or playbook.get("strategies")
        result: list[dict[str, Any]] = []
        for item in _list(targets):
            if isinstance(item, dict):
                result.append(dict(item))
            elif item:
                result.append({"strategy": str(item)})
        return result
    return []


def _side_effects(data: dict[str, Any]) -> list[str]:
    risk = str(data.get("risk") or "")
    actions = {str(v) for v in _list(data.get("actions"))}
    effects: list[str] = []
    if risk in {"external_effect", "high", "critical"} or actions.intersection({"send_message", "notify"}):
        effects.append("external_communication")
    if actions.intersection({"delete", "remove", "move", "write", "rename"}):
        effects.append("filesystem_mutation")
    if actions.intersection({"open_app", "close_app", "switch_window", "focus"}):
        effects.append("desktop_state_change")
    return effects


def build_capability_intelligence(descriptor: Any) -> CapabilityIntelligenceProfile:
    data = _descriptor_dict(descriptor)
    metadata = dict(data.get("metadata") or {})
    verification = _verification_methods(data, metadata)
    recovery = _recovery_paths(metadata)
    required_mcps = [str(v) for v in _list(metadata.get("required_mcps")) if str(v)]
    required_models = [str(v) for v in _list(metadata.get("required_models")) if str(v)]
    missing: list[str] = []
    if not data.get("inputs"):
        missing.append("inputs")
    if not verification:
        missing.append("verification")
    risk = str(data.get("risk") or "unknown")
    if risk in {"external_effect", "high", "critical"} and not recovery:
        missing.append("recovery_playbook")
    if not data.get("examples"):
        missing.append("examples")
    if risk == "unknown":
        missing.append("risk")
    quality = 1.0 - min(0.85, 0.16 * len(set(missing)))
    profile = CapabilityIntelligenceProfile(
        capability_id=str(data.get("id") or ""),
        task_type=str(data.get("task_type") or ""),
        domain=str(data.get("domain") or ""),
        risk=risk,
        preconditions=_infer_preconditions(data, metadata),
        verification_methods=verification,
        recovery_paths=recovery,
        required_mcps=required_mcps,
        required_models=required_models,
        side_effects=_side_effects(data),
        missing_metadata=sorted(set(missing)),
        routing_terms=[
            str(v)
            for v in [
                data.get("id"),
                data.get("domain"),
                data.get("task_type"),
                *(_list(data.get("actions"))),
                *(_list(data.get("objects"))),
                *(_list(data.get("examples"))),
            ]
            if str(v)
        ],
        source=str(data.get("source") or ""),
        quality_score=round(max(0.0, min(1.0, quality)), 3),
    )
    return profile


def build_capability_intelligence_index(
    capabilities: Iterable[Any],
    *,
    turn_id: str = "capability_intelligence",
) -> dict[str, CapabilityIntelligenceProfile]:
    index: dict[str, CapabilityIntelligenceProfile] = {}
    for capability in capabilities:
        profile = build_capability_intelligence(capability)
        if profile.capability_id:
            index[profile.capability_id] = profile
    append_event(
        "capability_intelligence_indexed",
        turn_id,
        {
            "count": len(index),
            "low_quality": [
                {"id": p.capability_id, "missing": p.missing_metadata, "quality": p.quality_score}
                for p in index.values()
                if p.quality_score < 0.75
            ][:50],
        },
    )
    return index
