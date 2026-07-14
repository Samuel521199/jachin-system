#!/usr/bin/env python3
"""OS Live Stress Matrix for the Memory-first Cognitive Kernel.

Default mode is safe: it validates planning, learning, blocking, and recovery
semantics without touching the desktop. Pass --live-safe to also run the
existing safe desktop matrix rows. Lark delivery remains dry-run unless the
called lower-level script is explicitly configured otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "output" / "os_live_stress_matrix"
LIVE_CONFIRMED_LARK_ALLOWLIST = {"Neil", "测试备注冒烟草稿"}
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value)[:90].strip("_") or "row"


def _write(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json(data), encoding="utf-8")
    return path


def _parse_recipients(value: str) -> list[str]:
    recipients = [item.strip() for item in str(value or "").split(",") if item.strip()]
    return recipients or ["Neil", "测试备注冒烟草稿"]


def _validate_live_lark_recipients(recipients: list[str]) -> list[str]:
    blocked = [name for name in recipients if name not in LIVE_CONFIRMED_LARK_ALLOWLIST]
    if blocked:
        raise ValueError(
            "live-confirmed Lark recipients are restricted to "
            f"{sorted(LIVE_CONFIRMED_LARK_ALLOWLIST)}; blocked={blocked}"
        )
    return recipients


@dataclass(slots=True)
class MatrixRow:
    name: str
    category: str
    ok: bool
    detail: str
    elapsed_ms: int
    evidence_path: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "ok": self.ok,
            "detail": self.detail,
            "elapsed_ms": self.elapsed_ms,
            "evidence_path": self.evidence_path,
            "payload": self.payload,
        }


class Matrix:
    def __init__(self, run_id: str, out_dir: Path) -> None:
        self.run_id = run_id
        self.out_dir = out_dir
        self.rows: list[MatrixRow] = []

    def run(self, category: str, name: str, func: Callable[[], dict[str, Any]]) -> MatrixRow:
        started = time.perf_counter()
        payload: dict[str, Any]
        try:
            payload = func()
            ok = bool(payload.get("ok"))
            detail = str(payload.get("detail") or ("ok" if ok else "failed"))
        except Exception as exc:
            ok = False
            detail = f"{type(exc).__name__}: {exc}"
            payload = {
                "ok": False,
                "detail": detail,
                "exception": repr(exc),
                "traceback": traceback.format_exc(),
            }
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        evidence_path = self.out_dir / f"os_live_stress_{self.run_id}_{_safe_name(category + '_' + name)}.evidence.json"
        evidence = {
            "task": "os_live_stress_matrix",
            "run_id": self.run_id,
            "category": category,
            "name": name,
            "ok": ok,
            "detail": detail,
            "elapsed_ms": elapsed_ms,
            "payload": payload,
        }
        _write(evidence_path, evidence)
        row = MatrixRow(
            name=name,
            category=category,
            ok=ok,
            detail=detail,
            elapsed_ms=elapsed_ms,
            evidence_path=str(evidence_path),
            payload=payload,
        )
        self.rows.append(row)
        print(f"[{'PASS' if ok else 'FAIL'}] {category} / {name}: {detail} ({elapsed_ms} ms)")
        return row

    def write_summary(self) -> Path:
        passed = sum(1 for row in self.rows if row.ok)
        summary = {
            "task": "OS Live Stress Matrix",
            "run_id": self.run_id,
            "ok": passed == len(self.rows),
            "detail": f"{passed}/{len(self.rows)} passed",
            "repo_root": str(REPO_ROOT),
            "platform": platform.platform(),
            "rows": [row.to_dict() for row in self.rows],
            "metrics": {
                "total": len(self.rows),
                "passed": passed,
                "failed": len(self.rows) - passed,
                "avg_elapsed_ms": int(sum(row.elapsed_ms for row in self.rows) / max(1, len(self.rows))),
            },
        }
        path = self.out_dir / f"os_live_stress_matrix_{self.run_id}.evidence.json"
        _write(path, summary)
        report = self.out_dir / f"os_live_stress_matrix_{self.run_id}.md"
        report.write_text(_markdown_report(summary), encoding="utf-8")
        return path


def _markdown_report(summary: dict[str, Any]) -> str:
    lines = [
        "# OS Live Stress Matrix Report",
        "",
        f"- run_id: `{summary['run_id']}`",
        f"- result: `{summary['detail']}`",
        f"- platform: `{summary['platform']}`",
        "",
        "| Category | Scenario | Status | Detail | Evidence |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in summary["rows"]:
        lines.append(
            "| {category} | {name} | {status} | {detail} | `{evidence}` |".format(
                category=row["category"],
                name=row["name"],
                status="PASS" if row["ok"] else "FAIL",
                detail=str(row["detail"]).replace("|", "/"),
                evidence=row["evidence_path"],
            )
        )
    lines.append("")
    return "\n".join(lines)


def _ctx(text: str, *, turn_id: str, kernel_home: Path, active_window: dict[str, Any] | None = None, recent_actions: list[str] | None = None):
    os.environ["JACHIN_COGNITIVE_KERNEL_HOME"] = str(kernel_home)
    from l3_node.cognitive_kernel.contracts import (
        AgentInputEnvelope,
        InputSource,
        MemoryEvidence,
        RelevantMemoryBundle,
        StateSnapshot,
        TaskLedgerEntry,
    )
    from l3_node.cognitive_kernel.pipeline import CognitiveTurnContext

    envelope = AgentInputEnvelope(
        turn_id=turn_id,
        source=InputSource.TEXT,
        raw_text=text,
        normalized_text=text,
        confidence=0.92,
    )
    state = StateSnapshot(
        snapshot_id=f"state-{turn_id}",
        generated_at_ms=int(time.time() * 1000),
        freshness_ms=100,
        active_window=active_window or {},
        risk_state={"unsaved_documents": "unknown"},
    )
    memory = RelevantMemoryBundle(
        turn_id=turn_id,
        recent_actions=[
            MemoryEvidence(
                memory_id=f"recent-{idx}",
                memory_type="short_term_action",
                content=content,
                source="os_live_stress_matrix",
                confidence=0.9,
                relevance_reason="stress matrix recent action",
            )
            for idx, content in enumerate(recent_actions or [])
        ],
        confidence=0.86,
    )
    return CognitiveTurnContext(
        envelope=envelope,
        state_snapshot=state,
        memory_bundle=memory,
        ledger_entry=TaskLedgerEntry(
            turn_id=turn_id,
            input_envelope=envelope,
            state_snapshot=state,
            memory_bundle=memory,
        ),
    )


def _plan(text: str, *, turn_id: str, kernel_home: Path, **kwargs: Any) -> dict[str, Any]:
    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn

    result = plan_cognitive_turn(_ctx(text, turn_id=turn_id, kernel_home=kernel_home, **kwargs))
    return result.to_dict()


def planning_common_apps(kernel_home: Path) -> dict[str, Any]:
    cases = [
        ("open WeChat", "WeChat"),
        ("open Chrome", "Chrome"),
        ("open Edge", "Edge"),
        ("open Excel", "Excel"),
        ("open WPS", "WPS"),
        ("open Cursor", "Cursor"),
        ("open VS Code", "VSCode"),
    ]
    plans = []
    failures = []
    for idx, (text, expected) in enumerate(cases):
        plan = _plan(text, turn_id=f"os-live-common-app-{idx}", kernel_home=kernel_home)
        target = (((plan.get("review_summary") or {}).get("target") or {}).get("name") or "")
        ok = target == expected and bool((plan.get("work_orders") or []))
        plans.append({"input": text, "expected": expected, "target": target, "ok": ok})
        if not ok:
            failures.append(plans[-1])
    return {
        "ok": not failures,
        "detail": "common_app_planning_ok" if not failures else "common_app_planning_failed",
        "plans": plans,
        "failures": failures,
    }


def learning_generalizes_after_guidance(kernel_home: Path) -> dict[str, Any]:
    from l3_node.cognitive_kernel.contracts import RiskLevel, ToolPolicy, WorkOrder
    from l3_node.cognitive_kernel.entity_corrections import (
        get_learned_app_correction,
        record_confirmed_entity_correction_from_work_order,
    )

    confirmed = WorkOrder(
        work_order_id="os-live-guided-lock-lark",
        decision_id="os-live-guided-lock-lark",
        role_agent="AppControlExecutorAgent",
        task="open Lark after user guidance",
        inputs={
            "tool": "mcp:windows_open_app",
            "target": {
                "type": "app",
                "name": "Lark",
                "heard_as": "lock",
                "candidate_alias": "lark",
                "entity_score": 0.91,
                "requires_entity_confirmation": True,
            },
        },
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_open_app"], risk_level=RiskLevel.LOW),
    )
    learned_ok = record_confirmed_entity_correction_from_work_order(work_order=confirmed, turn_id="os-live-learn-lock")
    direct = get_learned_app_correction("lock")
    fuzzy = get_learned_app_correction("lok")
    plan = _plan("open lok", turn_id="os-live-open-lok-after-learning", kernel_home=kernel_home)
    target = (plan.get("review_summary") or {}).get("target") or {}
    ok = (
        learned_ok
        and direct.get("name") == "Lark"
        and fuzzy.get("name") == "Lark"
        and target.get("name") == "Lark"
        and target.get("source") == "learned_entity_correction"
        and (plan.get("decision_contract") or {}).get("execution_allowed") is True
    )
    return {
        "ok": ok,
        "detail": "learned_app_correction_generalized" if ok else "learned_app_correction_failed",
        "direct": direct,
        "fuzzy": fuzzy,
        "plan": plan,
    }


def correction_negative_feedback_reopens_review(kernel_home: Path) -> dict[str, Any]:
    from l3_node.cognitive_kernel.contracts import RiskLevel, ToolPolicy, WorkOrder
    from l3_node.cognitive_kernel.entity_corrections import (
        get_learned_app_correction,
        record_confirmed_entity_correction_from_work_order,
        record_entity_correction_usage_from_work_order,
    )

    confirmed = WorkOrder(
        work_order_id="os-live-negative-guided-lock-lark",
        decision_id="os-live-negative-guided-lock-lark",
        role_agent="AppControlExecutorAgent",
        task="open Lark after user guidance",
        inputs={
            "tool": "mcp:windows_open_app",
            "target": {
                "type": "app",
                "name": "Lark",
                "heard_as": "lock",
                "candidate_alias": "lark",
                "entity_score": 0.91,
                "requires_entity_confirmation": True,
            },
        },
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_open_app"], risk_level=RiskLevel.LOW),
    )
    assert record_confirmed_entity_correction_from_work_order(
        work_order=confirmed,
        turn_id="os-live-negative-learn-lock",
    )
    learned_work = WorkOrder(
        work_order_id="os-live-negative-learned-lock-lark",
        decision_id="os-live-negative-learned-lock-lark",
        role_agent="AppControlExecutorAgent",
        task="open Lark from learned correction",
        inputs={
            "tool": "mcp:windows_open_app",
            "target": {
                "type": "app",
                "name": "Lark",
                "source": "learned_entity_correction",
                "heard_as": "lock",
                "surface_norm": "lock",
            },
        },
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_open_app"], risk_level=RiskLevel.LOW),
    )
    record_entity_correction_usage_from_work_order(
        work_order=learned_work,
        turn_id="os-live-negative-feedback-1",
        ok=False,
        failure_reason="app_executable_not_found",
    )
    record_entity_correction_usage_from_work_order(
        work_order=learned_work,
        turn_id="os-live-negative-feedback-2",
        ok=False,
        failure_reason="app_executable_not_found",
    )
    direct = get_learned_app_correction("lock")
    fuzzy = get_learned_app_correction("lok")
    plan = _plan("open lock", turn_id="os-live-open-lock-after-negative-feedback", kernel_home=kernel_home)
    target = (plan.get("review_summary") or {}).get("target") or {}
    ok = (
        direct.get("name") == "Lark"
        and direct.get("requires_confirmation") is True
        and direct.get("review_required") is True
        and fuzzy == {}
        and target.get("name") == "Lark"
        and target.get("requires_entity_confirmation") is True
        and (plan.get("decision_contract") or {}).get("execution_allowed") is False
    )
    return {
        "ok": ok,
        "detail": "negative_feedback_reopened_confirmation" if ok else "negative_feedback_failed",
        "direct": direct,
        "fuzzy": fuzzy,
        "plan": plan,
    }


def close_uses_latest_recent_app(kernel_home: Path) -> dict[str, Any]:
    plan = _plan(
        "close",
        turn_id="os-live-close-latest",
        kernel_home=kernel_home,
        active_window={"app_name": "Jachin", "title": "Jachin Console"},
        recent_actions=[
            '{"task":"open_app","target_app":"Browser"}',
            '{"task":"open_app","target_app":"WeChat"}',
        ],
    )
    target = (plan.get("review_summary") or {}).get("target") or {}
    ok = target.get("name") == "WeChat" and target.get("source") == "recent_action_memory"
    return {
        "ok": ok,
        "detail": "close_latest_recent_app_ok" if ok else "close_latest_recent_app_failed",
        "target": target,
        "plan": plan,
    }


def close_uses_latest_under_long_recent_history(kernel_home: Path) -> dict[str, Any]:
    actions = [
        json.dumps({"task": "open_app", "target_app": "Browser", "index": idx}, ensure_ascii=False)
        for idx in range(120)
    ]
    actions.append(json.dumps({"task": "open_app", "target_app": "WeChat", "index": 121}, ensure_ascii=False))
    plan = _plan(
        "close",
        turn_id="os-live-close-long-history-latest",
        kernel_home=kernel_home,
        active_window={"app_name": "Jachin", "title": "Jachin Console"},
        recent_actions=actions,
    )
    target = (plan.get("review_summary") or {}).get("target") or {}
    ok = target.get("name") == "WeChat" and target.get("source") == "recent_action_memory"
    return {
        "ok": ok,
        "detail": "close_long_recent_history_latest_ok" if ok else "close_long_recent_history_latest_failed",
        "recent_action_count": len(actions),
        "target": target,
        "plan": plan,
    }


def calculator_task_splits_to_open_and_calculate(kernel_home: Path) -> dict[str, Any]:
    plan = _plan("open calculator and calculate 99+100", turn_id="os-live-calculator-dag", kernel_home=kernel_home)
    tools = [wo.get("inputs", {}).get("tool") for wo in plan.get("work_orders") or []]
    ok = tools == ["mcp:windows_open_app", "mcp:windows_calculator_calculate"]
    return {
        "ok": ok,
        "detail": "calculator_dag_ok" if ok else "calculator_dag_failed",
        "tools": tools,
        "task_dag": plan.get("task_dag"),
        "plan": plan,
    }


def lark_message_has_slots_and_two_steps(kernel_home: Path) -> dict[str, Any]:
    plan = _plan("send to Neil: hello", turn_id="os-live-lark-message", kernel_home=kernel_home)
    work_orders = plan.get("work_orders") or []
    tools = [wo.get("inputs", {}).get("tool") for wo in work_orders]
    payload = {}
    if len(work_orders) >= 2:
        try:
            payload = json.loads(work_orders[1].get("inputs", {}).get("work_order_input") or "{}")
        except Exception:
            payload = {}
    recipients = []
    try:
        recipients = json.loads(payload.get("recipients_json") or "[]")
    except Exception:
        recipients = []
    ok = tools == ["mcp:windows_open_app", "mcp:windows_lark_send_message"] and recipients == ["Neil"] and payload.get("message") == "hello"
    return {
        "ok": ok,
        "detail": "lark_message_plan_ok" if ok else "lark_message_plan_failed",
        "tools": tools,
        "payload": payload,
        "plan": plan,
    }


def missing_message_slots_blocks_execution(kernel_home: Path) -> dict[str, Any]:
    plan = _plan("open L A R K send message", turn_id="os-live-lark-missing-slots", kernel_home=kernel_home)
    review = plan.get("review_summary") or {}
    decision = plan.get("decision_contract") or {}
    ok = review.get("needs_clarification") is True and decision.get("execution_allowed") is False and not plan.get("work_orders")
    return {
        "ok": ok,
        "detail": "missing_slots_blocked_ok" if ok else "missing_slots_blocked_failed",
        "review": review,
        "decision": decision,
    }


def file_read_open_reveal_planning(kernel_home: Path, out_dir: Path) -> dict[str, Any]:
    demo = out_dir / "fixtures" / "os_live_demo_file.txt"
    demo.parent.mkdir(parents=True, exist_ok=True)
    demo.write_text("Jachin OS live stress fixture.\n", encoding="utf-8")
    cases = [
        (f"read file {demo}", "core:fs_read"),
        (f"open file {demo}", "mcp:windows_file_open"),
        (f"reveal file {demo}", "mcp:windows_file_reveal_in_explorer"),
    ]
    rows = []
    failures = []
    for idx, (text, expected_tool) in enumerate(cases):
        plan = _plan(text, turn_id=f"os-live-file-{idx}", kernel_home=kernel_home)
        tools = [wo.get("inputs", {}).get("tool") for wo in plan.get("work_orders") or []]
        ok = tools[:1] == [expected_tool]
        rows.append({"input": text, "expected_tool": expected_tool, "tools": tools, "ok": ok})
        if not ok:
            failures.append(rows[-1])
    return {
        "ok": not failures,
        "detail": "file_read_open_reveal_planning_ok" if not failures else "file_read_open_reveal_planning_failed",
        "fixture": str(demo),
        "rows": rows,
        "failures": failures,
    }


def recovery_attempt_limit_summary(kernel_home: Path) -> dict[str, Any]:
    os.environ["JACHIN_COGNITIVE_KERNEL_HOME"] = str(kernel_home)
    from l3_node.cognitive_kernel.contracts import DecisionContract, RiskLevel, ToolPolicy, VerificationReport, WorkOrder
    from l3_node.cognitive_kernel.recovery_planner import RecoveryAttemptRecord, RecoveryPlanner

    contract = DecisionContract(
        decision_id="os-live-recovery-contract",
        turn_id="os-live-recovery",
        task_type="app_control",
        goal="open Browser under repeated focus failure",
        selected_roles=["AppControlExecutorAgent"],
        risk_level=RiskLevel.LOW,
        tool_policy=ToolPolicy(allowed_tools=["mcp:windows_window_switch"], risk_level=RiskLevel.LOW),
        execution_allowed=True,
        memory_context_refs=[
            {
                "bucket": "failure_hints",
                "memory_id": "memory_growth:playbook:browser-focus-timeout",
                "source": "Memory Growth Playbooks",
                "preview": "timeout focus retry longer timeout foreground strategy_weight=1.2",
            }
        ],
    )
    work = WorkOrder(
        work_order_id="os-live-recovery-work",
        decision_id=contract.decision_id,
        role_agent="AppControlExecutorAgent",
        task="switch Browser",
        inputs={"tool": "mcp:windows_window_switch", "work_order_input": '{"window_title":"Chrome"}'},
        tool_policy=contract.tool_policy,
    )
    verification = VerificationReport(
        verification_id="os-live-recovery-verify",
        work_order_id=work.work_order_id,
        ok=False,
        failure_reason="timeout waiting for foreground window",
    )
    planner = RecoveryPlanner(max_attempts=2)
    records = [
        RecoveryAttemptRecord(1, "wo-1", "AppControlExecutorAgent", "mcp:windows_window_switch", "initial", "initial path", False, "v1", "timeout waiting for foreground window"),
        RecoveryAttemptRecord(2, "wo-2", "AppControlExecutorAgent", "mcp:windows_window_switch", "memory_growth_longer_timeout", "longer timeout", False, "v2", "window still not foreground"),
    ]
    next_attempt = planner.next_attempt(contract=contract, failed_work_order=work, verification=verification, attempt_records=records)
    report = planner.final_failure_report(contract=contract, attempt_records=records, last_verification=verification)
    ok = next_attempt is None and report.get("attempt_count") == 2 and "timeout waiting for foreground window" in report.get("failure_counts", {})
    return {
        "ok": ok,
        "detail": "recovery_attempt_limit_ok" if ok else "recovery_attempt_limit_failed",
        "next_attempt": next_attempt.to_dict() if next_attempt else None,
        "final_failure_report": report,
    }


def lifecycle_store_corruption_is_ignored(kernel_home: Path) -> dict[str, Any]:
    os.environ["JACHIN_COGNITIVE_KERNEL_HOME"] = str(kernel_home)
    from l3_node.cognitive_kernel.contracts import MemoryWriteRequest
    from l3_node.cognitive_kernel.memory_lifecycle import recall_lifecycle_memories, write_lifecycle_memory

    write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="os-live-lifecycle-corrupt",
            source_event="os_live_stress",
            memory_type="tool_habit",
            content="When Browser focus fails, use foreground verification before reporting success.",
            confidence=0.82,
            ttl="permanent",
            evidence=[{"ok": True, "source": "os_live_stress"}],
        )
    )
    store = kernel_home / "memory" / "memory_lifecycle.jsonl"
    store.write_text(store.read_text(encoding="utf-8") + "{bad json line\n\n", encoding="utf-8")
    hits = recall_lifecycle_memories("Browser foreground verification", memory_types=["tool_habit"], limit=5)
    ok = len(hits) == 1 and "Browser focus fails" in hits[0].content
    return {
        "ok": ok,
        "detail": "lifecycle_corrupt_line_ignored" if ok else "lifecycle_corrupt_line_failed",
        "hit_count": len(hits),
        "store": str(store),
    }


def live_safe_bridge(run_dir: Path) -> dict[str, Any]:
    cmd = [sys.executable, str(REPO_ROOT / "scripts" / "capability_live_matrix.py"), "--skip-live-desktop"]
    proc = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    return {
        "ok": proc.returncode == 0,
        "detail": "capability_live_matrix_safe_ok" if proc.returncode == 0 else "capability_live_matrix_safe_failed",
        "command": cmd,
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-3000:],
        "stderr_tail": proc.stderr[-3000:],
        "run_dir": str(run_dir),
    }


def _live_contract_and_work_order(
    *,
    turn_id: str,
    tool: str,
    action_input: dict[str, Any],
    role_agent: str,
    goal: str,
):
    from l3_node.cognitive_kernel.contracts import DecisionContract, RiskLevel, ToolPolicy, WorkOrder

    decision_id = f"decision-{turn_id}"
    policy = ToolPolicy(
        allowed_tools=[tool],
        denied_tools=[],
        risk_level=RiskLevel.LOW,
        requires_confirmation=False,
        verification_required=True,
    )
    contract = DecisionContract(
        decision_id=decision_id,
        turn_id=turn_id,
        task_type="live_confirmed_stress",
        goal=goal,
        selected_workflow="live_confirmed_role_dispatcher",
        selected_roles=[role_agent, "VerificationAgent", "RecoveryAgent", "TurnClosureAgent", "MemoryWriteAgent"],
        risk_level=RiskLevel.LOW,
        tool_policy=policy,
        execution_allowed=True,
        verification_criteria=["role_execution_finished", "verification_report", "turn_closure"],
        rationale=["user_explicitly_authorized_live_confirmed_stress"],
    )
    work_order = WorkOrder(
        work_order_id=f"wo-{turn_id}",
        decision_id=decision_id,
        role_agent=role_agent,
        task=goal,
        inputs={
            "tool": tool,
            "work_order_input": json.dumps(action_input, ensure_ascii=False),
        },
        tool_policy=policy,
        expected_outputs=["observation", "visual_or_structured_evidence"],
        verification_criteria=["strict_side_effect_verification"],
    )
    return contract, work_order


async def _dispatch_live_work_order(
    *,
    turn_id: str,
    tool: str,
    action_input: dict[str, Any],
    role_agent: str,
    goal: str,
    executor: Callable[[Any], Any],
) -> dict[str, Any]:
    from l3_node.cognitive_kernel.contracts import MemoryWriteRequest
    from l3_node.cognitive_kernel.dispatcher import dispatch_existing_work_order
    from l3_node.cognitive_kernel.runtime import close_turn

    contract, work_order = _live_contract_and_work_order(
        turn_id=turn_id,
        tool=tool,
        action_input=action_input,
        role_agent=role_agent,
        goal=goal,
    )
    result = await dispatch_existing_work_order(
        contract=contract,
        work_order=work_order,
        executor=executor,
    )
    extra_memory: list[MemoryWriteRequest] = []
    if not result.verification.ok:
        extra_memory.append(
            MemoryWriteRequest(
                turn_id=turn_id,
                source_event="live_confirmed_stress_failure",
                memory_type="failure_hint",
                content=json.dumps(
                    {
                        "tool": tool,
                        "role_agent": role_agent,
                        "goal": goal,
                        "failure_reason": result.verification.failure_reason,
                        "observation_preview": str(result.observation or "")[:800],
                    },
                    ensure_ascii=False,
                ),
                evidence=[
                    {
                        "type": "live_confirmed_failure",
                        "work_order_id": work_order.work_order_id,
                        "verification_id": result.verification.verification_id,
                    }
                ],
                confidence=0.74,
                ttl="30d",
                merge_policy="append_action_chain",
            )
        )
    closure = close_turn(
        turn_id=turn_id,
        final_text=f"{goal}: {'passed' if result.verification.ok else 'failed'}",
        executed_work_orders=[work_order.work_order_id],
        verification_reports=[result.verification],
        aborted=not result.verification.ok,
        extra_memory_write_requests=extra_memory,
    )
    return {
        "ok": bool(result.verification.ok),
        "detail": "live_work_order_verified" if result.verification.ok else result.verification.failure_reason,
        "turn_id": turn_id,
        "contract": result.contract.to_dict(),
        "work_order": result.work_order.to_dict(),
        "verification": result.verification.to_dict(),
        "recovery_plan": result.recovery_plan.to_dict() if result.recovery_plan else None,
        "attempts": result.attempts or [],
        "final_failure_report": result.final_failure_report,
        "closure": closure.to_dict(),
        "observation_preview": str(result.observation or "")[:2000],
    }


async def _live_lark_executor(work_order: Any) -> str:
    raw = str(work_order.inputs.get("work_order_input") or "{}")
    payload = json.loads(raw)
    recipients = payload.get("recipients")
    if not isinstance(recipients, list):
        recipients = json.loads(str(payload.get("recipients_json") or "[]"))
    recipients = _validate_live_lark_recipients([str(item) for item in recipients])
    message = str(payload.get("message") or "")
    if not message.strip():
        raise ValueError("live-confirmed lark send requires non-empty message")
    out_dir = str(payload.get("out_dir") or "")
    max_attempts = int(payload.get("max_attempts") or 2)
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server

    return windows_uia_server.windows_lark_send_message(
        json.dumps(recipients, ensure_ascii=False),
        message,
        out_dir,
        max_attempts,
    )


async def _live_calculator_executor(work_order: Any) -> str:
    payload = json.loads(str(work_order.inputs.get("work_order_input") or "{}"))
    expression = str(payload.get("expression") or "")
    expected = str(payload.get("expected") or "")
    out_dir = str(payload.get("out_dir") or "")
    from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server

    return windows_uia_server.windows_calculator_calculate(expression, expected, out_dir)


async def _transport_should_not_run(work_order: Any) -> str:
    raise AssertionError(f"unexpected legacy transport for {work_order.role_agent}: {work_order.inputs}")


def live_confirmed_lark_send(kernel_home: Path, run_dir: Path, recipients: list[str], message: str) -> dict[str, Any]:
    os.environ["JACHIN_COGNITIVE_KERNEL_HOME"] = str(kernel_home)
    recipients = _validate_live_lark_recipients(recipients)
    rows = []
    for index, recipient in enumerate(recipients):
        turn_id = f"os-live-confirmed-lark-{index}-{_safe_name(recipient)}-{int(time.time())}"
        payload = {
            "recipients": [recipient],
            "recipients_json": json.dumps([recipient], ensure_ascii=False),
            "message": message,
            "out_dir": str(run_dir / "live_confirmed" / "lark" / _safe_name(recipient)),
            "max_attempts": 2,
            "allowlist": sorted(LIVE_CONFIRMED_LARK_ALLOWLIST),
        }
        rows.append(
            asyncio.run(
                _dispatch_live_work_order(
                    turn_id=turn_id,
                    tool="mcp:windows_lark_send_message",
                    action_input=payload,
                    role_agent="MessageExecutorAgent",
                    goal=f"Send live-confirmed Lark message to {recipient}",
                    executor=_live_lark_executor,
                )
            )
        )
    failures = [row for row in rows if not row.get("ok")]
    return {
        "ok": not failures,
        "detail": "live_lark_send_verified" if not failures else "live_lark_send_failed",
        "allowed_recipients": sorted(LIVE_CONFIRMED_LARK_ALLOWLIST),
        "requested_recipients": recipients,
        "message": message,
        "rows": rows,
        "failures": failures,
    }


def live_confirmed_file_open_reveal(kernel_home: Path, run_dir: Path) -> dict[str, Any]:
    os.environ["JACHIN_COGNITIVE_KERNEL_HOME"] = str(kernel_home)
    demo = run_dir / "fixtures" / "live_confirmed_reveal_open.txt"
    demo.parent.mkdir(parents=True, exist_ok=True)
    demo.write_text("Jachin live-confirmed file reveal/open fixture.\n", encoding="utf-8")
    rows = []
    for name, tool in [
        ("file_reveal", "mcp:windows_file_reveal_in_explorer"),
        ("file_open", "mcp:windows_file_open"),
    ]:
        rows.append(
            asyncio.run(
                _dispatch_live_work_order(
                    turn_id=f"os-live-confirmed-{name}-{int(time.time())}",
                    tool=tool,
                    action_input={"path": str(demo), "out_dir": str(run_dir / "live_confirmed" / name)},
                    role_agent="FileExecutorAgent",
                    goal=f"Live-confirmed {name} for fixture file",
                    executor=_transport_should_not_run,
                )
            )
        )
    failures = [row for row in rows if not row.get("ok")]
    return {
        "ok": not failures,
        "detail": "live_file_reveal_open_verified" if not failures else "live_file_reveal_open_failed",
        "fixture": str(demo),
        "rows": rows,
        "failures": failures,
    }


def live_confirmed_calculator_visual(kernel_home: Path, run_dir: Path) -> dict[str, Any]:
    os.environ["JACHIN_COGNITIVE_KERNEL_HOME"] = str(kernel_home)
    result = asyncio.run(
        _dispatch_live_work_order(
            turn_id=f"os-live-confirmed-calculator-{int(time.time())}",
            tool="mcp:windows_calculator_calculate",
            action_input={
                "expression": "91+9",
                "expected": "100",
                "out_dir": str(run_dir / "live_confirmed" / "calculator"),
            },
            role_agent="AppControlExecutorAgent",
            goal="Live-confirmed Windows Calculator visual calculation 91+9",
            executor=_live_calculator_executor,
        )
    )
    return {
        "ok": bool(result.get("ok")),
        "detail": "live_calculator_visual_verified" if result.get("ok") else "live_calculator_visual_failed",
        "expression": "91+9",
        "expected": "100",
        "result": result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run OS live stress matrix for planning, memory learning, and safe live probes.")
    parser.add_argument("--live-safe", action="store_true", help="Also run safe live probes through capability_live_matrix.py.")
    parser.add_argument(
        "--live-confirmed",
        action="store_true",
        help="Run user-authorized real desktop actions: Lark send, file reveal/open, and calculator visual verification.",
    )
    parser.add_argument(
        "--confirmed-lark-recipients",
        default="Neil,测试备注冒烟草稿",
        help="Comma-separated Lark recipients for --live-confirmed. Hard allowlist: Neil, 测试备注冒烟草稿.",
    )
    parser.add_argument(
        "--confirmed-message",
        default="",
        help="Message body for live-confirmed Lark sends. Defaults to a timestamped stress-test note.",
    )
    args = parser.parse_args()

    run_id = _stamp()
    run_dir = OUT_DIR / run_id
    kernel_home = run_dir / "kernel_home"
    matrix = Matrix(run_id=run_id, out_dir=run_dir)

    matrix.run("planning", "common_apps_generalize", lambda: planning_common_apps(kernel_home))
    matrix.run("learning", "guided_app_correction_generalizes", lambda: learning_generalizes_after_guidance(kernel_home))
    matrix.run("learning", "negative_feedback_reopens_review", lambda: correction_negative_feedback_reopens_review(kernel_home))
    matrix.run("memory", "close_uses_latest_recent_app", lambda: close_uses_latest_recent_app(kernel_home))
    matrix.run("memory", "close_uses_latest_under_long_recent_history", lambda: close_uses_latest_under_long_recent_history(kernel_home))
    matrix.run("workflow", "calculator_open_then_calculate_dag", lambda: calculator_task_splits_to_open_and_calculate(kernel_home))
    matrix.run("workflow", "lark_message_open_then_send_slots", lambda: lark_message_has_slots_and_two_steps(kernel_home))
    matrix.run("safety", "missing_message_slots_block_execution", lambda: missing_message_slots_blocks_execution(kernel_home))
    matrix.run("workflow", "file_read_open_reveal_planning", lambda: file_read_open_reveal_planning(kernel_home, run_dir))
    matrix.run("recovery", "attempt_limit_and_failure_summary", lambda: recovery_attempt_limit_summary(kernel_home))
    matrix.run("resilience", "lifecycle_store_corrupt_line", lambda: lifecycle_store_corruption_is_ignored(kernel_home))
    if args.live_safe:
        matrix.run("live_safe", "capability_live_matrix_bridge", lambda: live_safe_bridge(run_dir))
    if args.live_confirmed:
        recipients = _validate_live_lark_recipients(_parse_recipients(args.confirmed_lark_recipients))
        message = args.confirmed_message.strip() or f"Jachin live-confirmed 压测消息：验证真实发送和证据链。run={run_id}"
        matrix.run(
            "live_confirmed",
            "lark_send_allowlisted_recipients",
            lambda: live_confirmed_lark_send(kernel_home, run_dir, recipients, message),
        )
        matrix.run(
            "live_confirmed",
            "file_reveal_and_open",
            lambda: live_confirmed_file_open_reveal(kernel_home, run_dir),
        )
        matrix.run(
            "live_confirmed",
            "calculator_visual_91_plus_9",
            lambda: live_confirmed_calculator_visual(kernel_home, run_dir),
        )

    summary = matrix.write_summary()
    print("\nSummary evidence:")
    print(summary)
    return 0 if all(row.ok for row in matrix.rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
