"""Release-gate evaluation for Codex desktop collaboration.

The evaluator is intentionally independent from UI automation. Live runs,
contract smokes and historical evidence all enter through the same scenario
record format, so release decisions are deterministic and auditable.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable


REQUIRED_SCENARIOS = (
    "baseline",
    "wrong_context",
    "collapsed_project",
    "busy_queue",
    "permission_required",
    "network_timeout",
    "fact_conflict",
)


@dataclass(slots=True)
class GateCheck:
    name: str
    ok: bool
    detail: str


@dataclass(slots=True)
class ScenarioResult:
    scenario: str
    ok: bool
    expected: str
    observed: str
    invocation_id: str = ""
    evidence_path: str = ""
    checks: list[GateCheck] = field(default_factory=list)


def _record(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value or [] if isinstance(item, dict)]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _contains(value: Any, *markers: str) -> bool:
    text = _text(value).casefold()
    return any(marker.casefold() in text for marker in markers)


def _timeline(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    return _rows(evidence.get("timeline"))


def _submitted(evidence: dict[str, Any]) -> bool:
    for row in _timeline(evidence):
        stage = _text(row.get("stage")).casefold()
        status = _text(row.get("status")).casefold()
        if "submit" in stage and status in {"done", "success", "succeeded"}:
            return True
    return bool(evidence.get("prompt_submitted"))


def _context_verified(evidence: dict[str, Any]) -> bool:
    for row in _timeline(evidence):
        stage = _text(row.get("stage")).casefold()
        status = _text(row.get("status")).casefold()
        if "verify_codex_work_plan_context" in stage and status in {
            "done",
            "success",
            "succeeded",
        }:
            return True
    value = _record(evidence.get("context_verification"))
    return bool(value.get("ok"))


def _recovery(evidence: dict[str, Any]) -> dict[str, Any]:
    return _record(evidence.get("recovery"))


def _strategies(evidence: dict[str, Any]) -> list[str]:
    return [
        _text(row.get("strategy"))
        for row in _rows(_recovery(evidence).get("decisions"))
        if _text(row.get("strategy"))
    ]


def _terminal_recommendations(evidence: dict[str, Any]) -> list[str]:
    terminal = _record(evidence.get("recovery_terminal"))
    return [
        _text(item)
        for item in terminal.get("recommended_next_steps") or []
        if _text(item)
    ]


def _manager_final(evidence: dict[str, Any]) -> dict[str, Any]:
    return _record(evidence.get("invocation_manager_final"))


def _has_complete_evidence(evidence: dict[str, Any]) -> bool:
    manager = _manager_final(evidence)
    return bool(
        _text(evidence.get("invocation_id"))
        and _text(manager.get("status"))
        and _text(manager.get("stage"))
        and _text(manager.get("detail"))
        and isinstance(manager.get("history"), list)
        and isinstance(evidence.get("timeline"), list)
    )


def _reply_accepted(evidence: dict[str, Any]) -> bool:
    reply = _record(evidence.get("reply_selection"))
    if reply:
        return bool(reply.get("ok"))
    return bool(evidence.get("answer"))


def _stale_reply_accepted(evidence: dict[str, Any]) -> bool:
    reply = _record(evidence.get("reply_selection"))
    validation = _record(reply.get("validation"))
    issues = " ".join(_text(item) for item in validation.get("issues") or [])
    selected = _record(reply.get("selected"))
    selected_issues = " ".join(_text(item) for item in selected.get("issues") or [])
    return bool(
        _reply_accepted(evidence)
        and (
            _contains(issues, "stale_reply", "invocation_marker_mismatch")
            or _contains(selected_issues, "stale_reply", "invocation_marker_mismatch")
        )
    )


def _raw_codex_used(evidence: dict[str, Any]) -> bool:
    if evidence.get("codex_raw_directly_used") is True:
        return True
    fusion = _record(evidence.get("claim_fusion"))
    return bool(fusion.get("raw_answer_used_as_final"))


def _scenario_expectation(scenario: str) -> str:
    return {
        "baseline": "correlated reply succeeds after verified context",
        "wrong_context": "stop before submit when project or conversation is wrong",
        "collapsed_project": "recover through project expansion or sidebar search",
        "busy_queue": "serialize invocations without prompt overlap",
        "permission_required": "pause or fail with explicit operator action",
        "network_timeout": "stop after bounded recovery with actionable next steps",
        "fact_conflict": "block unsupported Codex claims from final output",
    }.get(scenario, "scenario contract passes")


def evaluate_scenario(record: dict[str, Any]) -> ScenarioResult:
    scenario = _text(record.get("scenario") or record.get("release_gate_scenario"))
    evidence = _record(record.get("evidence")) or record
    invocation_id = _text(evidence.get("invocation_id"))
    detail = _text(evidence.get("detail"))
    manager = _manager_final(evidence)
    manager_status = _text(manager.get("status"))
    checks: list[GateCheck] = [
        GateCheck(
            "evidence_complete",
            _has_complete_evidence(evidence),
            "invocation, terminal manager state and timeline are required",
        )
    ]

    if scenario == "baseline":
        checks.extend(
            [
                GateCheck("context_verified", _context_verified(evidence), "context verified before submission"),
                GateCheck("reply_accepted", _reply_accepted(evidence), "correlated reply passed quality gate"),
                GateCheck("terminal_success", manager_status == "succeeded", f"manager_status={manager_status}"),
            ]
        )
    elif scenario == "wrong_context":
        checks.extend(
            [
                GateCheck("no_submit", not _submitted(evidence), "wrong context must stop before submit"),
                GateCheck(
                    "context_failure_visible",
                    _contains(detail or manager.get("detail"), "context", "conversation", "project"),
                    detail or _text(manager.get("detail")),
                ),
            ]
        )
    elif scenario == "collapsed_project":
        strategies = _strategies(evidence)
        checks.extend(
            [
                GateCheck(
                    "navigation_recovery_used",
                    any(
                        strategy in {
                            "expand_project_then_conversation",
                            "sidebar_search_conversation",
                        }
                        for strategy in strategies
                    ),
                    ",".join(strategies),
                ),
                GateCheck("terminal_success", manager_status == "succeeded", f"manager_status={manager_status}"),
            ]
        )
    elif scenario == "busy_queue":
        queue = _record(record.get("queue_assertions"))
        checks.extend(
            [
                GateCheck("unique_invocations", bool(queue.get("unique_invocations")), _text(queue.get("detail"))),
                GateCheck("serialized_lease", bool(queue.get("serialized_lease")), _text(queue.get("detail"))),
                GateCheck("no_prompt_overlap", bool(queue.get("no_prompt_overlap")), _text(queue.get("detail"))),
            ]
        )
    elif scenario == "permission_required":
        pending = _record(evidence.get("recovery_pending_user_confirmation"))
        checks.extend(
            [
                GateCheck(
                    "operator_action_visible",
                    bool(pending)
                    or _contains(detail, "permission", "approval", "confirmation")
                    or bool(_terminal_recommendations(evidence)),
                    detail,
                ),
                GateCheck("not_false_success", manager_status != "succeeded", f"manager_status={manager_status}"),
            ]
        )
    elif scenario == "network_timeout":
        recommendations = _terminal_recommendations(evidence)
        checks.extend(
            [
                GateCheck(
                    "bounded_attempts",
                    len(_rows(_recovery(evidence).get("attempts"))) <= int(
                        _recovery(evidence).get("max_attempts") or 5
                    ),
                    f"attempts={len(_rows(_recovery(evidence).get('attempts')))}",
                ),
                GateCheck("actionable_failure", bool(recommendations), " | ".join(recommendations)),
                GateCheck("not_false_success", manager_status != "succeeded", f"manager_status={manager_status}"),
            ]
        )
    elif scenario == "fact_conflict":
        fusion = _record(evidence.get("claim_fusion"))
        conflicts = _rows(fusion.get("conflicts"))
        checks.extend(
            [
                GateCheck("conflict_detected", bool(conflicts), f"conflicts={len(conflicts)}"),
                GateCheck(
                    "final_output_blocked",
                    fusion.get("delivery_blocked") is True
                    or evidence.get("requires_confirmation") is True,
                    _text(fusion.get("reason")),
                ),
                GateCheck("raw_codex_not_used", not _raw_codex_used(evidence), "Codex raw answer must remain advisory"),
            ]
        )
    else:
        checks.append(GateCheck("known_scenario", False, f"unknown scenario: {scenario}"))

    ok = bool(checks) and all(check.ok for check in checks)
    observed = "; ".join(
        f"{check.name}={'ok' if check.ok else 'failed'}" for check in checks
    )
    return ScenarioResult(
        scenario=scenario or "unknown",
        ok=ok,
        expected=_scenario_expectation(scenario),
        observed=observed,
        invocation_id=invocation_id,
        evidence_path=_text(
            record.get("evidence_path") or evidence.get("evidence_path")
        ),
        checks=checks,
    )


def evaluate_release_gate(
    records: Iterable[dict[str, Any]],
    *,
    required_scenarios: Iterable[str] = REQUIRED_SCENARIOS,
) -> dict[str, Any]:
    record_rows = [_record(record) for record in records]
    results = [evaluate_scenario(record) for record in record_rows]
    by_scenario = {result.scenario: result for result in results}
    required = list(dict.fromkeys(_text(item) for item in required_scenarios if _text(item)))
    missing = [scenario for scenario in required if scenario not in by_scenario]
    failed = [
        scenario
        for scenario in required
        if scenario in by_scenario and not by_scenario[scenario].ok
    ]
    evidence_rows = [
        _record(record.get("evidence")) or record for record in record_rows
    ]
    invariants = {
        "stale_reply_accepts": sum(
            1 for evidence in evidence_rows if _stale_reply_accepted(evidence)
        ),
        "wrong_context_submits": sum(
            1
            for record, evidence in zip(record_rows, evidence_rows)
            if _text(
                record.get("scenario") or record.get("release_gate_scenario")
            )
            == "wrong_context"
            and _submitted(evidence)
        ),
        "raw_codex_direct_use": sum(
            1 for evidence in evidence_rows if _raw_codex_used(evidence)
        ),
        "failures_without_complete_evidence": sum(
            1
            for evidence in evidence_rows
            if _manager_final(evidence).get("status") != "succeeded"
            and not _has_complete_evidence(evidence)
        ),
    }
    invariants_ok = all(value == 0 for value in invariants.values())
    release_ready = not missing and not failed and invariants_ok
    return {
        "schema_version": 1,
        "task": "codex_live_release_gate",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "release_ready": release_ready,
        "status": "ready" if release_ready else "blocked",
        "required_scenarios": required,
        "scenario_count": len(results),
        "passed_scenarios": sum(1 for result in results if result.ok),
        "failed_scenarios": failed,
        "missing_scenarios": missing,
        "invariants": invariants,
        "invariants_ok": invariants_ok,
        "scenarios": [
            {
                **asdict(result),
                "checks": [asdict(check) for check in result.checks],
            }
            for result in results
        ],
    }
