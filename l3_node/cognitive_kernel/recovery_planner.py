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
from .contracts import DecisionContract, RecoveryPlan, RiskLevel, VerificationReport, WorkOrder
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
        return {
            "task_type": contract.task_type,
            "goal": contract.goal,
            "max_attempts": self.max_attempts,
            "attempt_count": len(attempt_records),
            "final_failure_reason": last_verification.failure_reason or "verification_failed",
            "failure_counts": failure_counts,
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


def _memory_growth_recovery_candidate(
    *,
    contract: DecisionContract,
    failed_work_order: WorkOrder,
    verification: VerificationReport,
    attempt_records: list[RecoveryAttemptRecord],
) -> RecoveryCandidate | None:
    refs = [
        ref
        for ref in list(getattr(contract, "memory_context_refs", []) or [])
        if isinstance(ref, dict)
        and (
            "playbook" in str(ref.get("memory_id") or "").lower()
            or ref.get("bucket") in {"tool_habits", "failure_hints"}
        )
    ]
    if not refs:
        return None
    tool = str(failed_work_order.inputs.get("tool") or "")
    if not tool:
        return None
    reason = (verification.failure_reason or "").lower()
    preview = " ".join(str(ref.get("preview") or "") for ref in refs).lower()
    strategy_weight, strategy_mode, requires_more_evidence = _memory_growth_ref_strategy(refs)
    if strategy_mode == "manual_review" or requires_more_evidence:
        return None
    used = {str(item.strategy or "") for item in attempt_records}
    history = " | ".join(
        f"{item.attempt_no}:{item.strategy}:{item.failure_reason or item.ok}" for item in attempt_records[-3:]
    )
    combined = f"{reason} {preview}"
    raw_input = str(failed_work_order.inputs.get("work_order_input") or "")

    if "memory_growth_longer_timeout" not in used and any(x in combined for x in ("timeout", "slow", "longer timeout")):
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
        priority=max(20, min(100, int(80 * strategy_weight))),
        metadata={
            "source": "memory_growth",
            "memory_context_refs": refs[:5],
            "failure_reason": verification.failure_reason,
            "attempt_history": [item.to_dict() for item in attempt_records],
            "governance_strategy_weight": strategy_weight,
            "governance_execution_mode": strategy_mode,
            "governance_requires_more_evidence": requires_more_evidence,
        },
    )


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
