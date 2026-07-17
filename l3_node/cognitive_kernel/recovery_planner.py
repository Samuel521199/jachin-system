"""Bounded recovery planning for WorkOrder execution.

Recovery paths are selected from capability metadata instead of hard-coded
App/File rules.  The planner chooses exactly one next attempt after each
failure, using the latest VerificationReport plus all prior attempt records.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field
from typing import Any

from .capability_recovery_registry import CapabilityRecoveryRegistry, RecoveryCandidate
from .capability_governance_policy import governance_policy_from_work_order
from .contracts import AgentInputEnvelope, DecisionContract, InputSource, MemoryRecallRequest, RecoveryPlan, RiskLevel, VerificationReport, WorkOrder
from .memory_growth_recall import recall_memory_growth
from .runtime import build_recovery_plan

DEFAULT_MAX_RECOVERY_ATTEMPTS = 5


@dataclass(slots=True)
class RecoveryAttemptRecord:
    attempt_no: int
    work_order_id: str
    role_agent: str
    tool: str
    strategy: str
    rationale: str
    ok: bool
    verification_id: str
    failure_reason: str = ""
    observation_preview: str = ""
    elapsed_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_no": self.attempt_no,
            "work_order_id": self.work_order_id,
            "role_agent": self.role_agent,
            "tool": self.tool,
            "strategy": self.strategy,
            "rationale": self.rationale,
            "ok": self.ok,
            "verification_id": self.verification_id,
            "failure_reason": self.failure_reason,
            "observation_preview": self.observation_preview,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass(slots=True)
class RecoveryAttemptPlan:
    attempt_no: int
    strategy: str
    rationale: str
    work_order: WorkOrder
    candidate_path: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_no": self.attempt_no,
            "strategy": self.strategy,
            "rationale": self.rationale,
            "work_order": self.work_order.to_dict(),
            "candidate_path": self.candidate_path,
        }


class RecoveryPlanner:
    def __init__(
        self,
        *,
        max_attempts: int = DEFAULT_MAX_RECOVERY_ATTEMPTS,
        registry: CapabilityRecoveryRegistry | None = None,
    ) -> None:
        self.max_attempts = max(1, int(max_attempts or DEFAULT_MAX_RECOVERY_ATTEMPTS))
        self.registry = registry or CapabilityRecoveryRegistry()

    def initial_plan(
        self,
        *,
        contract: DecisionContract,
        failed_work_order: WorkOrder,
        verification: VerificationReport,
        attempt_no: int,
    ) -> RecoveryPlan | None:
        plan = build_recovery_plan(
            turn_id=contract.turn_id,
            work_order=failed_work_order,
            verification=verification,
        )
        if plan is None:
            return None
        self.max_attempts = self.registry.max_attempts_for(
            role_agent=failed_work_order.role_agent,
            tool=str(failed_work_order.inputs.get("tool") or ""),
            default=self.max_attempts,
        )
        self.max_attempts = _inline_max_attempts(failed_work_order, self.max_attempts)
        plan.max_attempts = self.max_attempts
        plan.attempt_no = attempt_no
        plan.alternative_paths = [
            {
                "selection": "dynamic_per_failure",
                "available_now": self.candidate_paths(
                    contract=contract,
                    failed_work_order=failed_work_order,
                    verification=verification,
                    attempt_records=[],
                ),
            }
        ]
        return plan

    def next_attempt(
        self,
        *,
        contract: DecisionContract,
        failed_work_order: WorkOrder,
        verification: VerificationReport,
        attempt_records: list[RecoveryAttemptRecord],
    ) -> RecoveryAttemptPlan | None:
        next_no = len(attempt_records) + 1
        if next_no > self.max_attempts:
            return None
        if not self._may_auto_recover(contract, failed_work_order, verification):
            return None

        inline_registry = _inline_recovery_registry_for(failed_work_order)
        candidate = inline_registry.select_next(
            contract=contract,
            failed_work_order=failed_work_order,
            verification=verification,
            attempt_records=attempt_records,
        ) if inline_registry else None
        if candidate is None:
            candidate = self.registry.select_next(
                contract=contract,
                failed_work_order=failed_work_order,
                verification=verification,
                attempt_records=attempt_records,
            )
        if candidate is None:
            candidate = _memory_growth_recovery_candidate(
                contract=contract,
                failed_work_order=failed_work_order,
                verification=verification,
                attempt_records=attempt_records,
            )
        if candidate is None:
            return None
        work_order = self._work_order_for_candidate(
            original=failed_work_order,
            candidate=candidate.to_dict(),
            attempt_no=next_no,
            contract=contract,
        )
        return RecoveryAttemptPlan(
            attempt_no=next_no,
            strategy=candidate.strategy,
            rationale=candidate.rationale,
            work_order=work_order,
            candidate_path=candidate.to_dict(),
        )

    def final_failure_report(
        self,
        *,
        contract: DecisionContract,
        attempt_records: list[RecoveryAttemptRecord],
        last_verification: VerificationReport,
    ) -> dict[str, Any]:
        failure_counts: dict[str, int] = {}
        for item in attempt_records:
            key = item.failure_reason or "unknown"
            failure_counts[key] = failure_counts.get(key, 0) + 1
        exhausted_tools = sorted({str(item.tool or "") for item in attempt_records if item.tool})
        exhausted_strategies = sorted({str(item.strategy or "") for item in attempt_records if item.strategy})
        failure_timeline = [
            {
                "attempt_no": item.attempt_no,
                "tool": item.tool,
                "strategy": item.strategy,
                "failure_class": _failure_class(item.failure_reason),
                "failure_reason": item.failure_reason or "unknown",
                "elapsed_ms": item.elapsed_ms,
            }
            for item in attempt_records
        ]
        stopped_reason = _stopped_reason(
            max_attempts=self.max_attempts,
            attempt_records=attempt_records,
            last_verification=last_verification,
        )
        return {
            "task_type": contract.task_type,
            "goal": contract.goal,
            "max_attempts": self.max_attempts,
            "attempt_count": len(attempt_records),
            "final_failure_reason": last_verification.failure_reason or "verification_failed",
            "stopped_reason": stopped_reason,
            "recovery_quality_score": _recovery_quality_score(attempt_records, stopped_reason),
            "failure_counts": failure_counts,
            "failure_timeline": failure_timeline,
            "exhausted_tools": exhausted_tools,
            "exhausted_strategies": exhausted_strategies,
            "attempts": [x.to_dict() for x in attempt_records],
            "recommended_next_steps": _recommend_next_steps(contract, last_verification, attempt_records),
            "memory_context_refs": list(getattr(contract, "memory_context_refs", []) or []),
        }

    def candidate_paths(
        self,
        *,
        contract: DecisionContract,
        failed_work_order: WorkOrder,
        verification: VerificationReport,
        attempt_records: list[RecoveryAttemptRecord] | None = None,
    ) -> list[dict[str, Any]]:
        paths = self.registry.candidate_snapshot(
            contract=contract,
            failed_work_order=failed_work_order,
            verification=verification,
            attempt_records=attempt_records or [],
        )
        inline_registry = _inline_recovery_registry_for(failed_work_order)
        if inline_registry:
            paths = [
                *inline_registry.candidate_snapshot(
                    contract=contract,
                    failed_work_order=failed_work_order,
                    verification=verification,
                    attempt_records=attempt_records or [],
                ),
                *paths,
            ]
        growth_candidate = _memory_growth_recovery_candidate(
            contract=contract,
            failed_work_order=failed_work_order,
            verification=verification,
            attempt_records=attempt_records or [],
        )
        if growth_candidate is not None:
            paths.append(growth_candidate.to_dict())
        return paths

    def _may_auto_recover(
        self,
        contract: DecisionContract,
        work_order: WorkOrder,
        verification: VerificationReport,
    ) -> bool:
        if verification.ok:
            return False
        governance_policy = governance_policy_from_work_order(work_order)
        if governance_policy.execution_mode == "manual_review" or governance_policy.requires_confirmation:
            return False
        if contract.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            return False
        if work_order.role_agent == "MessageExecutorAgent":
            # MessageExecutorAgent owns recipient-aware retry and dedupe.
            return False
        reason = (verification.failure_reason or "").lower()
        if any(x in reason for x in ("not allowed", "permission", "requires_confirmation", "confirm")):
            return False
        if bool(
            self.registry.candidate_snapshot(
                contract=contract,
                failed_work_order=work_order,
                verification=verification,
                attempt_records=[],
            )
        ):
            return True
        inline_registry = _inline_recovery_registry_for(work_order)
        if inline_registry and inline_registry.candidate_snapshot(
            contract=contract,
            failed_work_order=work_order,
            verification=verification,
            attempt_records=[],
        ):
            return True
        return _memory_growth_recovery_candidate(
            contract=contract,
            failed_work_order=work_order,
            verification=verification,
            attempt_records=[],
        ) is not None

    def _work_order_for_candidate(
        self,
        *,
        original: WorkOrder,
        candidate: dict[str, Any],
        attempt_no: int,
        contract: DecisionContract,
    ) -> WorkOrder:
        clone = copy.deepcopy(original)
        clone.work_order_id = f"{original.work_order_id}-recover-{attempt_no}"
        clone.status = "pending"
        clone.inputs = dict(clone.inputs)
        clone.inputs["tool"] = str(candidate.get("tool") or clone.inputs.get("tool") or "")
        clone.inputs["work_order_input"] = str(candidate.get("work_order_input") or clone.inputs.get("work_order_input") or "")
        clone.inputs["recovery"] = {
            "attempt_no": attempt_no,
            "strategy": candidate.get("strategy") or "retry",
            "rationale": candidate.get("rationale") or "",
            "source_work_order_id": original.work_order_id,
            "decision_id": contract.decision_id,
            "capability_id": candidate.get("capability_id") or "",
            "target_id": candidate.get("target_id") or "",
        }
        metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}
        if isinstance(metadata.get("governance_policy"), dict):
            clone.inputs["governance_policy"] = metadata["governance_policy"]
        return clone


def _recommend_next_steps(
    contract: DecisionContract,
    last_verification: VerificationReport,
    attempt_records: list[RecoveryAttemptRecord],
) -> list[str]:
    reason = (last_verification.failure_reason or "").lower()
    steps: list[str] = []
    if "not found" in reason or "path" in reason:
        steps.append("确认目标应用或文件路径是否存在，必要时在 capability 配置中补充明确路径。")
    if "permission" in reason or "not allowed" in reason:
        steps.append("需要用户授权或调整 capability 权限后再执行。")
    if "focus" in reason or "window" in reason:
        steps.append("检查窗口是否被最小化、是否存在同名窗口，或启用截图/OCR 校验。")
    if "timeout" in reason or "connection" in reason:
        steps.append("检查本地服务或目标应用是否已启动，再重试。")
    if not steps:
        steps.append("查看 Evidence 时间线中的每次尝试，优先处理最后一次失败原因。")
    steps.append(f"本轮已尝试 {len(attempt_records)} 次，未继续盲目执行以避免副作用。")
    return steps


def _failure_class(reason: str) -> str:
    text = str(reason or "").lower()
    if "tool_quality" in text or "summary" in text or "fetch" in text:
        return "tool_quality"
    if "window" in text or "focus" in text:
        return "window_state"
    if "path" in text or "not found" in text:
        return "target_not_found"
    if "permission" in text or "not allowed" in text or "confirm" in text:
        return "permission_or_confirmation"
    if "timeout" in text or "connection" in text or "busy" in text:
        return "transport_or_timeout"
    return "unknown"


def _stopped_reason(
    *,
    max_attempts: int,
    attempt_records: list[RecoveryAttemptRecord],
    last_verification: VerificationReport,
) -> str:
    reason = (last_verification.failure_reason or "").lower()
    if any(token in reason for token in ("permission", "not allowed", "requires_confirmation", "confirm")):
        return "needs_user_confirmation_or_permission"
    if len(attempt_records) >= max_attempts:
        return "max_attempts_reached"
    return "no_eligible_recovery_path"


def _recovery_quality_score(attempt_records: list[RecoveryAttemptRecord], stopped_reason: str) -> int:
    if not attempt_records:
        return 0
    score = 40
    unique_fingerprints = {(item.tool, item.strategy) for item in attempt_records}
    unique_failure_classes = {_failure_class(item.failure_reason) for item in attempt_records}
    if len(unique_fingerprints) >= min(3, len(attempt_records)):
        score += 25
    if len(unique_failure_classes) >= 2:
        score += 15
    if any(item.strategy not in {"initial", "retry_same_path", "retry_with_backoff_hint"} for item in attempt_records):
        score += 15
    if stopped_reason in {"max_attempts_reached", "needs_user_confirmation_or_permission"}:
        score += 5
    return max(0, min(100, score))


def _inline_recovery_registry_for(work_order: WorkOrder) -> CapabilityRecoveryRegistry | None:
    manifest = _inline_recovery_manifest(work_order)
    if not manifest:
        return None
    registry = CapabilityRecoveryRegistry(manifests=[manifest])
    return registry if registry.candidate_snapshot(
        contract=_dummy_contract_for_inline(work_order),
        failed_work_order=work_order,
        verification=_dummy_verification_for_inline(work_order),
        attempt_records=[],
    ) or manifest.get("recovery_playbook") else registry


def _inline_recovery_manifest(work_order: WorkOrder) -> dict[str, Any]:
    profile = work_order.inputs.get("capability_profile")
    if not isinstance(profile, dict):
        profile = {}
    recovery_policy = work_order.inputs.get("recovery_policy")
    if not isinstance(recovery_policy, dict):
        recovery_policy = {}
    raw_paths = profile.get("recovery_paths") or recovery_policy.get("capability_recovery_paths") or []
    if not isinstance(raw_paths, list) or not raw_paths:
        return {}
    tool = str(work_order.inputs.get("tool") or "$same")
    targets: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_paths):
        if not isinstance(raw, dict):
            continue
        if isinstance(raw.get("steps"), list):
            target = copy.deepcopy(raw)
            target.setdefault("id", f"inline_target_{index + 1}")
            target.setdefault("role_agent", work_order.role_agent)
            target.setdefault("tools", [tool])
            targets.append(target)
            continue
        strategy = str(raw.get("strategy") or "").strip()
        if not strategy:
            continue
        step = {
            "strategy": strategy,
            "tool": str(raw.get("tool") or "$same"),
            "rationale": str(raw.get("rationale") or "inline capability recovery path"),
            "priority": int(raw.get("priority") or 100),
        }
        for key in ("when", "action_patch", "action_template"):
            if isinstance(raw.get(key), dict):
                step[key] = raw[key]
        targets.append(
            {
                "id": str(raw.get("id") or f"inline_target_{index + 1}"),
                "role_agent": str(raw.get("role_agent") or work_order.role_agent),
                "tools": raw.get("tools") if isinstance(raw.get("tools"), list) else [tool],
                "max_attempts": raw.get("max_attempts", recovery_policy.get("max_attempts", 5)),
                "steps": [step],
            }
        )
    if not targets:
        return {}
    return {
        "id": str(profile.get("capability_id") or recovery_policy.get("capability_profile_id") or "inline_capability_profile"),
        "recovery_playbook": {"targets": targets},
    }


def _inline_max_attempts(work_order: WorkOrder, default: int) -> int:
    manifest = _inline_recovery_manifest(work_order)
    attempts = [default]
    for target in ((manifest.get("recovery_playbook") or {}).get("targets") or []):
        if isinstance(target, dict) and target.get("max_attempts") is not None:
            try:
                attempts.append(int(target.get("max_attempts")))
            except Exception:
                pass
    return max(1, min(8, max(attempts)))


def _dummy_contract_for_inline(work_order: WorkOrder) -> DecisionContract:
    return DecisionContract(
        decision_id=str(work_order.decision_id or "inline"),
        turn_id="inline",
        task_type=str(work_order.task or ""),
        goal=str(work_order.task or ""),
    )


def _dummy_verification_for_inline(work_order: WorkOrder) -> VerificationReport:
    return VerificationReport(
        verification_id="inline",
        work_order_id=work_order.work_order_id,
        ok=False,
        failure_reason="inline_candidate_probe",
    )


def _memory_growth_recovery_candidate(
    *,
    contract: DecisionContract,
    failed_work_order: WorkOrder,
    verification: VerificationReport,
    attempt_records: list[RecoveryAttemptRecord],
) -> RecoveryCandidate | None:
    refs = _memory_growth_recovery_refs(
        contract=contract,
        failed_work_order=failed_work_order,
        verification=verification,
    )
    if not refs:
        return None
    tool = str(failed_work_order.inputs.get("tool") or "")
    if not tool:
        return None
    reason = (verification.failure_reason or "").lower()
    preview = " ".join(str(ref.get("preview") or "") for ref in refs).lower()
    strategy_weight, strategy_mode, requires_more_evidence = _memory_growth_ref_strategy(refs)
    usage_multiplier, usage_health = _memory_growth_ref_usage_health(refs)
    if strategy_mode == "manual_review" or requires_more_evidence:
        return None
    used = {str(item.strategy or "") for item in attempt_records}
    history = " | ".join(
        f"{item.attempt_no}:{item.strategy}:{item.failure_reason or item.ok}" for item in attempt_records[-3:]
    )
    combined = f"{reason} {preview}"
    raw_input = str(failed_work_order.inputs.get("work_order_input") or "")

    learned_strategy = _learned_next_strategy(refs)

    if learned_strategy and learned_strategy not in used and _can_auto_apply_learned_strategy(learned_strategy):
        strategy = learned_strategy
        patch = {"recovery_strategy": strategy}
        if any(x in learned_strategy for x in ("timeout", "longer")) or any(x in combined for x in ("timeout", "slow", "longer timeout")):
            patch["timeout"] = 12.0
        if "evidence" in learned_strategy or "verification" in learned_strategy:
            patch["require_verification_evidence"] = True
        if "resolve_target" in learned_strategy:
            patch["resolve_target_from_memory"] = True
        if "higher_quality" in learned_strategy or "regenerate" in learned_strategy:
            patch["quality_gate"] = "strict"
        work_order_input = _patch_json_input(raw_input, patch)
    elif "memory_growth_longer_timeout" not in used and any(x in combined for x in ("timeout", "slow", "longer timeout")):
        strategy = "memory_growth_longer_timeout"
        work_order_input = _patch_json_input(raw_input, {"timeout": 12.0, "recovery_strategy": strategy})
    elif "memory_growth_retry_same_path" not in used and any(x in combined for x in ("retry", "focus", "window", "foreground")):
        strategy = "memory_growth_retry_same_path"
        work_order_input = _patch_json_input(raw_input, {"recovery_strategy": strategy})
    else:
        return None

    return RecoveryCandidate(
        capability_id="memory_growth.playbooks",
        target_id="memory_growth_recovery",
        strategy=strategy,
        tool=tool,
        work_order_input=work_order_input,
        rationale=(
            "Selected from Memory Growth playbook evidence after considering "
            f"failure_reason={verification.failure_reason or 'unknown'} history={history or 'none'}"
        ),
        priority=max(10, min(100, int(80 * strategy_weight * usage_multiplier))),
        metadata={
            "source": "memory_growth",
            "memory_context_refs": refs[:5],
            "memory_growth_lookup": {
                "live_recall_used": any(ref.get("source") == "Memory Growth Playbooks" for ref in refs),
                "learned_next_strategy": learned_strategy,
                "ref_count": len(refs),
            },
            "failure_reason": verification.failure_reason,
            "attempt_history": [item.to_dict() for item in attempt_records],
            "governance_strategy_weight": strategy_weight,
            "governance_execution_mode": strategy_mode,
            "governance_requires_more_evidence": requires_more_evidence,
            "artifact_usage_multiplier": usage_multiplier,
            "artifact_usage_health": usage_health,
        },
    )


def _memory_growth_recovery_refs(
    *,
    contract: DecisionContract,
    failed_work_order: WorkOrder,
    verification: VerificationReport,
) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for ref in list(getattr(contract, "memory_context_refs", []) or []):
        if not isinstance(ref, dict):
            continue
        if "playbook" in str(ref.get("memory_id") or "").lower() or ref.get("bucket") in {"tool_habits", "failure_hints"}:
            refs.append(ref)
    refs.extend(_live_memory_growth_recovery_refs(contract=contract, failed_work_order=failed_work_order, verification=verification))
    return _dedupe_memory_refs(refs)


def _live_memory_growth_recovery_refs(
    *,
    contract: DecisionContract,
    failed_work_order: WorkOrder,
    verification: VerificationReport,
) -> list[dict[str, Any]]:
    tool = str(failed_work_order.inputs.get("tool") or "")
    raw_text = " ".join(
        part
        for part in (
            contract.goal,
            contract.task_type,
            failed_work_order.task,
            failed_work_order.role_agent,
            tool,
            verification.failure_reason,
        )
        if part
    )
    if not raw_text.strip():
        return []
    try:
        envelope = AgentInputEnvelope(
            turn_id=contract.turn_id,
            source=InputSource.SYSTEM,
            raw_text=raw_text,
            normalized_text=raw_text,
        )
        memories, _gaps = recall_memory_growth(
            MemoryRecallRequest(
                turn_id=contract.turn_id,
                input_envelope=envelope,
                candidate_intents=[contract.task_type, failed_work_order.task],
                candidate_task_domains=[failed_work_order.role_agent, tool],
                candidate_entities=[tool, failed_work_order.role_agent],
                multi_queries={
                    "goal": contract.goal,
                    "failure": verification.failure_reason,
                    "tool": tool,
                },
                retrieval_channels=["memory_growth_playbook_memory"],
                retrieval_purpose=["recovery_planning", "learned_playbook_lookup"],
                max_results_per_channel=5,
            ),
            limit=5,
        )
    except Exception:
        return []
    refs: list[dict[str, Any]] = []
    for memory in memories:
        if "playbook" not in str(memory.memory_id or "").lower() and memory.memory_type not in {"failure_hint", "tool_habit"}:
            continue
        refs.append(
            {
                "bucket": "failure_hints" if memory.memory_type == "failure_hint" else "tool_habits",
                "memory_id": memory.memory_id,
                "source": memory.source,
                "confidence": memory.confidence,
                "preview": memory.content,
                "relevance_reason": memory.relevance_reason,
                "lookup": "live_memory_growth_recall",
            }
        )
    return refs


def _dedupe_memory_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in refs:
        key = str(ref.get("memory_id") or json.dumps(ref, sort_keys=True, ensure_ascii=False, default=str))
        if key in seen:
            continue
        seen.add(key)
        out.append(ref)
    return out


def _learned_next_strategy(refs: list[dict[str, Any]]) -> str:
    for ref in refs:
        hay = f"{ref.get('preview') or ''} {ref.get('relevance_reason') or ''}"
        value = _extract_strategy_value(hay.lower(), "next_strategy")
        if value:
            return value
    return ""


def _can_auto_apply_learned_strategy(strategy: str) -> bool:
    low = strategy.lower()
    if any(token in low for token in ("ask_user", "manual", "permission", "confirm")):
        return False
    return True


def _memory_growth_ref_strategy(refs: list[dict[str, Any]]) -> tuple[float, str, bool]:
    weights: list[float] = []
    modes: list[str] = []
    requires = False
    for ref in refs:
        hay = f"{ref.get('preview') or ''} {ref.get('relevance_reason') or ''}".lower()
        raw_weight = _extract_strategy_value(hay, "strategy_weight")
        if raw_weight:
            try:
                weights.append(float(raw_weight))
            except Exception:
                pass
        mode = _extract_strategy_value(hay, "governance_execution_mode") or _extract_strategy_value(hay, "execution_mode")
        if mode:
            modes.append(mode)
        if "requires_more_evidence=true" in hay:
            requires = True
    weight = max(weights) if weights else 1.0
    mode = "manual_review" if "manual_review" in modes else "batch_ok" if "batch_ok" in modes else "normal"
    return max(0.25, min(1.8, weight)), mode, requires


def _memory_growth_ref_usage_health(refs: list[dict[str, Any]]) -> tuple[float, dict[str, Any]]:
    rates: list[float] = []
    use_counts: list[int] = []
    failure_counts: list[int] = []
    last_failures: list[str] = []
    degraded_refs: list[str] = []
    reliable_refs: list[str] = []
    for ref in refs:
        hay = f"{ref.get('preview') or ''} {ref.get('relevance_reason') or ''}".lower()
        rate = _extract_float_value(hay, "artifact_success_rate")
        if rate is None:
            rate = _extract_float_value(hay, "memory_success_rate")
        use_count = _extract_int_value(hay, "artifact_use_count")
        failure_count = _extract_int_value(hay, "artifact_failure_count")
        last_failure = _extract_strategy_value(hay, "artifact_last_failure_reason")
        if rate is not None:
            rates.append(rate)
            if use_count >= 2 and rate < 0.5:
                degraded_refs.append(str(ref.get("memory_id") or ref.get("artifact_path") or "unknown"))
            if use_count >= 2 and rate >= 0.75:
                reliable_refs.append(str(ref.get("memory_id") or ref.get("artifact_path") or "unknown"))
        if use_count:
            use_counts.append(use_count)
        if failure_count:
            failure_counts.append(failure_count)
        if last_failure:
            last_failures.append(last_failure)

    multiplier = 1.0
    if degraded_refs:
        multiplier -= min(0.45, 0.18 * len(degraded_refs))
    if sum(failure_counts) >= 3:
        multiplier -= 0.12
    if reliable_refs and not degraded_refs:
        multiplier += min(0.2, 0.08 * len(reliable_refs))
    multiplier = max(0.35, min(1.25, multiplier))
    return multiplier, {
        "rates": rates[:5],
        "use_count": sum(use_counts),
        "failure_count": sum(failure_counts),
        "degraded_refs": degraded_refs[:5],
        "reliable_refs": reliable_refs[:5],
        "last_failure_reasons": last_failures[:5],
        "degraded": bool(degraded_refs),
        "multiplier": round(multiplier, 3),
    }


def _extract_float_value(text: str, key: str) -> float | None:
    raw = _extract_strategy_value(text, key)
    if not raw:
        return None
    try:
        return float(raw)
    except Exception:
        return None


def _extract_int_value(text: str, key: str) -> int:
    raw = _extract_strategy_value(text, key)
    if not raw:
        return 0
    try:
        return int(float(raw))
    except Exception:
        return 0


def _extract_strategy_value(text: str, key: str) -> str:
    match = re.search(rf"{re.escape(key.lower())}=([^;\s]+)", text)
    return match.group(1).strip().lower() if match else ""


def _patch_json_input(raw: str, patch: dict[str, Any]) -> str:
    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.update(patch)
    return json.dumps(data, ensure_ascii=False)
