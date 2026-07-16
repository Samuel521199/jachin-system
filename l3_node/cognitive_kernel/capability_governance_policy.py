"""Runtime governance policy derived from Evidence health indexes.

The policy is intentionally capability-scoped and data-driven.  It lets the
kernel consume the same health signal shown in Evidence Console without
hard-coding app/file/message-specific behavior into the Arbiter or Dispatcher.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .contracts import DecisionContract, ReviewSummary, RiskLevel, WorkOrder
from .ledger import append_event


@dataclass(slots=True)
class CapabilityGovernancePolicy:
    capability: str = ""
    days: int = 7
    score: int | None = None
    level: str = "unknown"
    execution_mode: str = "normal"
    requires_confirmation: bool = False
    reason: str = ""
    suggestions: list[dict[str, Any]] = field(default_factory=list)
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_capability_governance(
    *,
    summary: ReviewSummary | None = None,
    contract: DecisionContract | None = None,
    capability_ids: list[str] | None = None,
    days: int = 7,
    index_path: str | Path | None = None,
) -> CapabilityGovernancePolicy:
    ids = _candidate_capability_ids(summary=summary, contract=contract, explicit=capability_ids)
    index = _load_governance_index(index_path)
    if not index:
        return CapabilityGovernancePolicy(
            capability=ids[0] if ids else "",
            days=days,
            execution_mode="normal",
            reason="governance_index_missing",
        )
    health_rows = [row for row in index.get("health") or [] if isinstance(row, dict)]
    match = _best_health_match(health_rows, ids=ids, days=days)
    if not match and ids:
        match = _best_health_match(health_rows, ids=ids, days=14) or _best_health_match(health_rows, ids=ids, days=30)
    if not match:
        return CapabilityGovernancePolicy(
            capability=ids[0] if ids else "",
            days=days,
            execution_mode="normal",
            reason="capability_health_missing",
            source=str(index.get("index_path") or _default_index_path()),
        )
    return _policy_from_health(match, source=str(index.get("index_path") or _default_index_path()))


def apply_governance_to_contract(contract: DecisionContract, policy: CapabilityGovernancePolicy) -> DecisionContract:
    if policy.execution_mode == "normal":
        contract.rationale.append(f"Capability governance: {policy.reason}.")
        return contract
    contract.rationale.append(
        f"Capability governance: {policy.capability or 'unknown'} score={policy.score} level={policy.level} mode={policy.execution_mode}; {policy.reason}"
    )
    if policy.execution_mode == "manual_review" or policy.requires_confirmation:
        contract.tool_policy.requires_confirmation = True
        contract.tool_policy.confirmation_reason = policy.reason or "capability health requires confirmation"
        contract.execution_allowed = False
        contract.clarification_question = policy.reason or "这个能力近期健康分较低，请确认后再执行。"
    elif policy.execution_mode == "degraded_auto":
        if contract.risk_level == RiskLevel.LOW:
            contract.risk_level = RiskLevel.MEDIUM
            contract.tool_policy.risk_level = RiskLevel.MEDIUM
        contract.tool_policy.confirmation_reason = contract.tool_policy.confirmation_reason or policy.reason
    return contract


def governance_payload_for_work_order(
    *,
    summary: ReviewSummary,
    contract: DecisionContract,
    node_capability: str = "",
    node_tool: str = "",
) -> dict[str, Any]:
    policy = evaluate_capability_governance(
        summary=summary,
        contract=contract,
        capability_ids=[x for x in [node_capability, node_tool] if x],
    )
    return policy.to_dict()


def governance_policy_from_work_order(work_order: WorkOrder) -> CapabilityGovernancePolicy:
    payload = work_order.inputs.get("governance_policy") if isinstance(work_order.inputs, dict) else None
    if not isinstance(payload, dict):
        return CapabilityGovernancePolicy(reason="work_order_governance_missing")
    return CapabilityGovernancePolicy(
        capability=str(payload.get("capability") or ""),
        days=int(payload.get("days") or 7),
        score=int(payload["score"]) if payload.get("score") is not None else None,
        level=str(payload.get("level") or "unknown"),
        execution_mode=str(payload.get("execution_mode") or "normal"),
        requires_confirmation=bool(payload.get("requires_confirmation") or False),
        reason=str(payload.get("reason") or ""),
        suggestions=[x for x in (payload.get("suggestions") or []) if isinstance(x, dict)],
        source=str(payload.get("source") or ""),
    )


def _policy_from_health(row: dict[str, Any], *, source: str) -> CapabilityGovernancePolicy:
    score = _int(row.get("score"), default=0)
    level = str(row.get("level") or "unknown")
    suggestions = [x for x in (row.get("suggestions") or []) if isinstance(x, dict)]
    if level == "no_data":
        return CapabilityGovernancePolicy(
            capability=str(row.get("capability") or ""),
            days=_int(row.get("days"), default=7),
            score=score,
            level=level,
            execution_mode="observe",
            reason="capability_health_no_data",
            suggestions=suggestions,
            source=source,
        )
    if score < 50:
        mode = "manual_review"
        requires_confirmation = True
        reason = "capability_health_critical_requires_confirmation"
    elif score < 70:
        mode = "degraded_auto"
        requires_confirmation = False
        reason = "capability_health_degraded_prefer_recovery_or_alternate_path"
    elif score < 85:
        mode = "observe"
        requires_confirmation = False
        reason = "capability_health_watch"
    else:
        mode = "normal"
        requires_confirmation = False
        reason = "capability_health_healthy"
    return CapabilityGovernancePolicy(
        capability=str(row.get("capability") or ""),
        days=_int(row.get("days"), default=7),
        score=score,
        level=level,
        execution_mode=mode,
        requires_confirmation=requires_confirmation,
        reason=reason,
        suggestions=suggestions,
        source=source,
    )


def _candidate_capability_ids(
    *,
    summary: ReviewSummary | None,
    contract: DecisionContract | None,
    explicit: list[str] | None,
) -> list[str]:
    out: list[str] = []
    for item in explicit or []:
        _append_unique(out, item)
    if summary:
        for candidate in summary.capability_candidates or []:
            if not isinstance(candidate, dict):
                continue
            descriptor = candidate.get("descriptor") if isinstance(candidate.get("descriptor"), dict) else candidate
            if isinstance(descriptor, dict):
                _append_unique(out, descriptor.get("id") or descriptor.get("capability_id"))
        for tool in summary.candidate_tools or []:
            _append_unique(out, tool)
        _append_unique(out, summary.task_type)
        _append_unique(out, summary.top_intent)
    if contract:
        for tool in contract.tool_policy.allowed_tools or []:
            _append_unique(out, tool)
        _append_unique(out, contract.selected_workflow)
        _append_unique(out, contract.task_type)
    return out


def _append_unique(out: list[str], value: Any) -> None:
    text = str(value or "").strip()
    if text and text not in out:
        out.append(text)


def _best_health_match(rows: list[dict[str, Any]], *, ids: list[str], days: int) -> dict[str, Any] | None:
    if not ids:
        matches = [row for row in rows if _int(row.get("days"), default=0) == days and row.get("capability") == "all"]
    else:
        id_set = set(ids)
        matches = [row for row in rows if _int(row.get("days"), default=0) == days and str(row.get("capability") or "") in id_set]
    if not matches:
        return None
    matches.sort(key=lambda row: (_int(row.get("score"), default=100), -_int(row.get("evidence_count"), default=0)))
    return matches[0]


def _load_governance_index(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path else _default_index_path()
    try:
        if not target.exists():
            return {}
        data = json.loads(target.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        append_event(
            "capability_governance_index_read_failed",
            "capability_governance",
            {"path": str(target), "error": str(exc)},
        )
        return {}


def _default_index_path() -> Path:
    raw = os.getenv("JACHIN_OS_EVIDENCE_GOVERNANCE_INDEX", "").strip()
    if raw:
        return Path(raw).expanduser()
    root = Path(os.getenv("JACHIN_APP_ROOT") or Path.cwd())
    return root / "output" / "os_evidence_governance_index.json"


def _int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default
