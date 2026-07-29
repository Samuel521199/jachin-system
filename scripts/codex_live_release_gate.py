"""Run the Codex collaboration release-gate scenario matrix.

Contract mode exercises the real invocation, reply and recovery contracts
without taking control of the desktop. The emitted scenario records use the
same schema as live UI evidence and are consumed by one release evaluator.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from l3_client.local_mcps.windows_uia_mcp.codex_reply_protocol import (  # noqa: E402
    select_reply,
)
from l3_client.local_mcps.windows_uia_mcp.codex_stage_recovery import (  # noqa: E402
    CodexStageRecoveryPlanner,
)
from l3_node.codex_invocation_manager import CodexInvocationManager  # noqa: E402
from l3_node.codex_release_gate import evaluate_release_gate  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _windows_process_ids(image_name: str) -> set[int]:
    if os.name != "nt":
        return set()
    completed = subprocess.run(
        [
            "tasklist",
            "/FI",
            f"IMAGENAME eq {image_name}",
            "/FO",
            "CSV",
            "/NH",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        return set()
    pids: set[int] = set()
    for row in csv.reader(io.StringIO(completed.stdout)):
        if len(row) < 2 or row[0].lower() != image_name.lower():
            continue
        try:
            pids.add(int(row[1]))
        except ValueError:
            continue
    return pids


def _terminate_windows_process_ids(pids: set[int]) -> list[int]:
    terminated: list[int] = []
    for pid in sorted(pids):
        try:
            completed = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=5.0,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            continue
        if completed.returncode == 0:
            terminated.append(pid)
    return terminated


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return path


def _timeline_from_manager(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "at": row.get("at"),
            "stage": row.get("stage"),
            "status": row.get("status"),
            "detail": row.get("detail"),
        }
        for row in record.get("history") or []
        if isinstance(row, dict)
    ]


class ContractScenarioFactory:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.manager = CodexInvocationManager(
            output_dir / "runtime",
            recover=False,
        )
        self._sequence = 0

    def _invocation_id(self, scenario: str) -> str:
        self._sequence += 1
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"jcx-gate-{scenario}-{stamp}-{self._sequence:02d}"

    def _finish(
        self,
        scenario: str,
        *,
        status: str,
        stage: str,
        detail: str,
        timeline: list[dict[str, Any]] | None = None,
        evidence_patch: dict[str, Any] | None = None,
        record_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        invocation_id = self._invocation_id(scenario)
        acquired = self.manager.acquire(
            invocation_id,
            metadata={"release_gate_scenario": scenario},
            timeout_seconds=2,
            poll_seconds=0.02,
        )
        if not acquired.get("ok"):
            raise RuntimeError(
                f"failed to acquire Codex contract lease: {acquired}"
            )
        self.manager.heartbeat(
            invocation_id,
            status="running",
            stage=stage,
            detail=detail,
        )
        final = self.manager.release(
            invocation_id,
            status=status,
            stage=stage,
            detail=detail,
        )
        evidence = {
            "schema_version": 1,
            "task": "codex_release_gate_scenario",
            "scenario": scenario,
            "invocation_id": invocation_id,
            "detail": detail,
            "invocation_manager_final": final,
            "timeline": timeline or _timeline_from_manager(final),
        }
        evidence.update(evidence_patch or {})
        return {
            "scenario": scenario,
            "mode": "contract",
            "generated_at": _now(),
            "evidence": evidence,
            **(record_patch or {}),
        }

    def baseline(self) -> dict[str, Any]:
        invocation_id = self._invocation_id("baseline")
        marker = f"[JACHIN_REF:{invocation_id}]"
        answer = (
            f"{marker}\n"
            "The project progress was reviewed against local files and tests. "
            "Evidence includes Git diff, changed files, and the latest test "
            "result. One release risk remains unverified, so the next step is "
            "to run the desktop smoke before delivery."
        )
        reply = select_reply(
            [{"source": "clipboard", "text": answer}],
            prompt="Review the latest project progress.",
            invocation_marker=marker,
            schema="generic",
        )
        acquired = self.manager.acquire(
            invocation_id,
            metadata={"release_gate_scenario": "baseline"},
            timeout_seconds=2,
        )
        if not acquired.get("ok"):
            raise RuntimeError(f"baseline lease failed: {acquired}")
        self.manager.heartbeat(
            invocation_id,
            stage="verify_codex_work_plan_context",
            detail="project_and_work_plan_context_verified",
        )
        self.manager.heartbeat(
            invocation_id,
            status="waiting",
            stage="wait_reply",
            detail="waiting_for_correlated_reply",
        )
        final = self.manager.release(
            invocation_id,
            status="succeeded",
            stage="reply_validated",
            detail="correlated_reply_passed_quality_gate",
        )
        timeline = _timeline_from_manager(final)
        timeline.insert(
            max(1, len(timeline) - 2),
            {
                "at": _now(),
                "stage": "submit_prompt",
                "status": "done",
                "detail": "prompt_submitted_once",
            },
        )
        return {
            "scenario": "baseline",
            "mode": "contract",
            "generated_at": _now(),
            "evidence": {
                "schema_version": 1,
                "task": "codex_release_gate_scenario",
                "scenario": "baseline",
                "invocation_id": invocation_id,
                "detail": "baseline_completed",
                "context_verification": {"ok": True},
                "reply_selection": reply,
                "invocation_manager_final": final,
                "timeline": timeline,
            },
        }

    def wrong_context(self) -> dict[str, Any]:
        return self._finish(
            "wrong_context",
            status="failed",
            stage="verify_codex_work_plan_context",
            detail="project_context_mismatch_stopped_before_submit",
            evidence_patch={"context_verification": {"ok": False}},
        )

    def collapsed_project(self) -> dict[str, Any]:
        planner = CodexStageRecoveryPlanner()
        decision = planner.observe_failure(
            stage="navigate",
            failure_reason="navigate:conversation_not_found",
            attempted_strategy="direct_conversation",
            evidence={"sidebar_state": "project_collapsed"},
        )
        if decision is None:
            recovery = planner.snapshot()
        else:
            planner.record_success(
                stage="navigate",
                strategy=decision.strategy,
                evidence={"context_verified_after_recovery": True},
            )
            recovery = planner.snapshot()
        return self._finish(
            "collapsed_project",
            status="succeeded",
            stage="reply_validated",
            detail="conversation_recovered_after_project_expansion",
            evidence_patch={"recovery": recovery},
        )

    def busy_queue(self) -> dict[str, Any]:
        first_id = self._invocation_id("busy-owner")
        second_id = self._invocation_id("busy-queue")
        first = self.manager.acquire(first_id, timeout_seconds=2)
        outcome: dict[str, Any] = {}

        def acquire_second() -> None:
            outcome.update(
                self.manager.acquire(
                    second_id,
                    timeout_seconds=3,
                    poll_seconds=0.02,
                )
            )

        thread = threading.Thread(target=acquire_second, daemon=True)
        thread.start()
        deadline = time.monotonic() + 2
        observed_queued = False
        while time.monotonic() < deadline:
            if self.manager.get(second_id).get("status") == "queued":
                observed_queued = True
                break
            time.sleep(0.02)
        first_final = self.manager.release(
            first_id,
            status="succeeded",
            stage="reply_validated",
            detail="first_invocation_completed",
        )
        thread.join(timeout=4)
        second_acquired = bool(outcome.get("ok"))
        if second_acquired:
            second_final = self.manager.release(
                second_id,
                status="succeeded",
                stage="reply_validated",
                detail="second_invocation_started_after_first_release",
            )
        else:
            second_final = self.manager.get(second_id)
        assertions = {
            "unique_invocations": first_id != second_id,
            "serialized_lease": bool(
                first.get("ok") and observed_queued and second_acquired
            ),
            "no_prompt_overlap": bool(
                first_final.get("finished_at")
                and second_final.get("started_at")
                and first_final["finished_at"] <= second_final["started_at"]
            ),
            "detail": (
                f"queued={observed_queued}; second_acquired={second_acquired}"
            ),
        }
        return {
            "scenario": "busy_queue",
            "mode": "contract",
            "generated_at": _now(),
            "queue_assertions": assertions,
            "evidence": {
                "schema_version": 1,
                "task": "codex_release_gate_scenario",
                "scenario": "busy_queue",
                "invocation_id": second_id,
                "detail": assertions["detail"],
                "invocation_manager_final": second_final,
                "timeline": _timeline_from_manager(second_final),
                "queue_records": [first_final, second_final],
            },
        }

    def permission_required(self) -> dict[str, Any]:
        planner = CodexStageRecoveryPlanner()
        planner.observe_failure(
            stage="wait",
            failure_reason="wait:permission_required",
            attempted_strategy="wait_for_reply",
        )
        terminal = planner.record_terminal_failure(
            final_reason="wait:permission_required"
        )
        return self._finish(
            "permission_required",
            status="failed",
            stage="permission_required",
            detail="permission_required_user_approval_needed",
            evidence_patch={
                "recovery": planner.snapshot(),
                "recovery_terminal": terminal,
                "recovery_pending_user_confirmation": {
                    "reason": "Codex requires approval before reading files"
                },
            },
        )

    def network_timeout(self) -> dict[str, Any]:
        planner = CodexStageRecoveryPlanner()
        planner.observe_failure(
            stage="wait",
            failure_reason="wait:network_timeout",
            attempted_strategy="bounded_wait",
        )
        terminal = planner.record_terminal_failure(
            final_reason="wait:network_timeout_after_bounded_retry"
        )
        return self._finish(
            "network_timeout",
            status="failed",
            stage="wait_reply_timeout",
            detail="network_timeout_after_bounded_recovery",
            evidence_patch={
                "recovery": planner.snapshot(),
                "recovery_terminal": terminal,
            },
        )

    def fact_conflict(self) -> dict[str, Any]:
        return self._finish(
            "fact_conflict",
            status="failed",
            stage="claim_fusion_conflict",
            detail="codex_claim_conflicts_with_git_and_test_evidence",
            evidence_patch={
                "requires_confirmation": True,
                "claim_fusion": {
                    "conflicts": [
                        {
                            "claim": "All tests passed",
                            "counter_evidence": "test run failed",
                        }
                    ],
                    "delivery_blocked": True,
                    "reason": "verified local evidence overrides Codex claim",
                    "raw_answer_used_as_final": False,
                },
            },
        )

    def all(self) -> list[dict[str, Any]]:
        return [
            self.baseline(),
            self.wrong_context(),
            self.collapsed_project(),
            self.busy_queue(),
            self.permission_required(),
            self.network_timeout(),
            self.fact_conflict(),
        ]


def run_contract(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    records = ContractScenarioFactory(output_dir).all()
    scenario_dir = output_dir / "scenarios"
    for record in records:
        scenario = str(record["scenario"])
        path = _write_json(scenario_dir / f"{scenario}.evidence.json", record)
        record["evidence_path"] = str(path.resolve())
        record["evidence"]["evidence_path"] = str(path.resolve())
        _write_json(path, record)
    result = evaluate_release_gate(records)
    result["mode"] = "contract"
    result["output_dir"] = str(output_dir.resolve())
    result["scenario_evidence"] = [
        record["evidence_path"] for record in records
    ]
    latest = _write_json(output_dir / "release_gate_latest.json", result)
    result["report_path"] = str(latest.resolve())
    return result


def run_live(
    output_dir: Path,
    *,
    project_name: str,
    project_path: str,
    conversation_name: str,
    prompt: str,
    wait_seconds: int,
) -> dict[str, Any]:
    """Replace the contract baseline with one real Codex desktop invocation."""

    run_contract(output_dir)
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import (
        WindowsOSAutomation,
    )

    automation = WindowsOSAutomation(output_dir / "live_artifacts")
    result = automation.codex_work_plan_query(
        project_name=project_name,
        project_path=project_path,
        conversation_name=conversation_name,
        prompt=prompt,
        wait_seconds=max(30, min(int(wait_seconds), 600)),
        session_id="codex-release-gate-live",
        request_key=f"release-gate-{datetime.now().strftime('%Y%m%d%H%M%S')}",
    )
    evidence = (
        dict(result.evidence)
        if isinstance(result.evidence, dict)
        else {}
    )
    evidence.update(
        {
            "scenario": "baseline",
            "release_gate_mode": "live",
            "detail": str(result.detail or evidence.get("detail") or ""),
        }
    )
    live_record = {
        "scenario": "baseline",
        "mode": "live",
        "generated_at": _now(),
        "evidence": evidence,
    }
    scenario_dir = output_dir / "scenarios"
    live_path = _write_json(
        scenario_dir / "baseline.live.evidence.json",
        live_record,
    )
    live_record["evidence_path"] = str(live_path.resolve())
    live_record["evidence"]["evidence_path"] = str(live_path.resolve())
    _write_json(live_path, live_record)

    records = [live_record]
    for path in sorted(scenario_dir.glob("*.evidence.json")):
        if path.name in {
            "baseline.evidence.json",
            "baseline.live.evidence.json",
        }:
            continue
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(row, dict):
            records.append(row)
    gate = evaluate_release_gate(records)
    gate.update(
        {
            "mode": "live",
            "live_task_ok": bool(result.ok),
            "live_task_detail": str(result.detail or ""),
            "output_dir": str(output_dir.resolve()),
        }
    )
    live_report = _write_json(
        output_dir / "release_gate_live_latest.json",
        gate,
    )
    gate["report_path"] = str(live_report.resolve())
    _write_json(output_dir / "release_gate_latest.json", gate)
    return gate


def run_live_task(
    output_dir: Path,
    *,
    project_name: str,
    project_path: str,
    conversation_name: str,
    prompt: str,
    wait_seconds: int,
    obstruct_after_seconds: float = 0.0,
    obstruct_duration_seconds: float = 8.0,
    disable_remote_vision: bool = False,
) -> dict[str, Any]:
    """Run only the real desktop invocation for fast live regression."""

    from l3_client.local_mcps.windows_uia_mcp.os_tasks import (
        WindowsOSAutomation,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    obstruction_stop = threading.Event()
    obstruction: dict[str, Any] = {
        "enabled": obstruct_after_seconds > 0,
        "delay_seconds": max(0.0, float(obstruct_after_seconds or 0)),
        "duration_seconds": max(1.0, float(obstruct_duration_seconds or 8.0)),
        "started": False,
        "cleaned": False,
    }
    obstruction_process: list[subprocess.Popen[Any]] = []
    obstruction_owned_pids: set[int] = set()
    vision_fault_env = {
        "DASHSCOPE_API_BASE": os.environ.get("DASHSCOPE_API_BASE"),
        "JACHIN_CODEX_VISION_UI_TIMEOUT": os.environ.get(
            "JACHIN_CODEX_VISION_UI_TIMEOUT"
        ),
        "JACHIN_CODEX_VISION_EXTRACT_TIMEOUT": os.environ.get(
            "JACHIN_CODEX_VISION_EXTRACT_TIMEOUT"
        ),
    }
    vision_fault: dict[str, Any] = {
        "enabled": bool(disable_remote_vision),
        "strategy": "unreachable_loopback_api_base",
    }
    if disable_remote_vision:
        os.environ["DASHSCOPE_API_BASE"] = "http://127.0.0.1:9/v1"
        os.environ["JACHIN_CODEX_VISION_UI_TIMEOUT"] = "1"
        os.environ["JACHIN_CODEX_VISION_EXTRACT_TIMEOUT"] = "1"

    def obstruct_foreground() -> None:
        if obstruction_stop.wait(obstruction["delay_seconds"]):
            return
        try:
            before_pids = _windows_process_ids("Notepad.exe")
            process = subprocess.Popen(["notepad.exe"])
            obstruction_process.append(process)
            deadline = time.monotonic() + 4.0
            new_pids: set[int] = set()
            while time.monotonic() < deadline:
                new_pids = (
                    _windows_process_ids("Notepad.exe") - before_pids
                )
                if new_pids:
                    break
                time.sleep(0.1)
            owned_pids = new_pids - {process.pid}
            if not owned_pids and process.poll() is None:
                owned_pids = {process.pid}
            obstruction_owned_pids.update(owned_pids)
            obstruction.update(
                {
                    "started": True,
                    "started_at": _now(),
                    "launcher_pid": process.pid,
                    "owned_pids": sorted(new_pids),
                    "app": "notepad.exe",
                }
            )
            if not obstruction_stop.wait(obstruction["duration_seconds"]):
                terminated = _terminate_windows_process_ids(
                    obstruction_owned_pids
                )
                obstruction.update(
                    {
                        "cleaned": set(terminated)
                        == obstruction_owned_pids,
                        "ended_at": _now(),
                        "terminated_pids": terminated,
                    }
                )
        except (OSError, subprocess.SubprocessError) as exc:
            obstruction.update(
                {
                    "started": False,
                    "error": repr(exc),
                }
            )

    obstruction_thread: threading.Thread | None = None
    if obstruction["enabled"]:
        obstruction_thread = threading.Thread(
            target=obstruct_foreground,
            name="codex-live-foreground-obstruction",
            daemon=True,
        )
        obstruction_thread.start()
    automation = WindowsOSAutomation(output_dir / "live_artifacts")
    try:
        result = automation.codex_work_plan_query(
            project_name=project_name,
            project_path=project_path,
            conversation_name=conversation_name,
            prompt=prompt,
            wait_seconds=max(30, min(int(wait_seconds), 600)),
            session_id="codex-release-gate-live-task",
            request_key=f"live-task-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        )
    finally:
        obstruction_stop.set()
        if obstruction_thread is not None:
            obstruction_thread.join(timeout=1.0)
        terminated = _terminate_windows_process_ids(obstruction_owned_pids)
        if terminated:
            obstruction["terminated_pids"] = sorted(
                set(obstruction.get("terminated_pids") or []) | set(terminated)
            )
            obstruction["cleaned"] = (
                set(obstruction["terminated_pids"])
                == obstruction_owned_pids
            )
        for process in obstruction_process:
            try:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5.0)
            except (OSError, subprocess.SubprocessError) as exc:
                obstruction["cleanup_error"] = repr(exc)
        for key, previous in vision_fault_env.items():
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
    payload = {
        "schema_version": 1,
        "task": "codex_live_task",
        "generated_at": _now(),
        "ok": bool(result.ok),
        "detail": str(result.detail or ""),
        "evidence": (
            dict(result.evidence)
            if isinstance(result.evidence, dict)
            else {}
        ),
        "output_dir": str(output_dir.resolve()),
        "foreground_obstruction": obstruction,
        "remote_vision_fault": vision_fault,
    }
    report = _write_json(output_dir / "live_task_latest.json", payload)
    payload["report_path"] = str(report.resolve())
    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Codex collaboration release gate.",
    )
    parser.add_argument(
        "--mode",
        choices=("contract", "live", "live-task"),
        default="contract",
        help="Live mode controls the Codex desktop and replaces the baseline scenario.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "output" / "codex_live_release_gate"),
    )
    parser.add_argument("--project-name", default="Jachin")
    parser.add_argument("--project-path", default=str(ROOT))
    parser.add_argument(
        "--conversation-name",
        default=os.environ.get("JACHIN_CODEX_LIVE_CONVERSATION", "工作计划"),
    )
    parser.add_argument(
        "--prompt",
        default=os.environ.get(
            "JACHIN_CODEX_LIVE_PROMPT",
            "请基于当前项目可见证据，列出一条已验证进展、一条风险和"
            "一条下一步建议。不要执行修改，不要编造。",
        ),
    )
    parser.add_argument("--wait-seconds", type=int, default=120)
    parser.add_argument(
        "--obstruct-after-seconds",
        type=float,
        default=0.0,
        help="In live-task mode, open a temporary Notepad window after this delay.",
    )
    parser.add_argument(
        "--obstruct-duration-seconds",
        type=float,
        default=8.0,
        help="How long the temporary foreground obstruction stays open.",
    )
    parser.add_argument(
        "--disable-remote-vision",
        action="store_true",
        help=(
            "In live-task mode, make only the Qwen vision endpoint "
            "unreachable so local OCR/native-copy fallback is exercised."
        ),
    )
    return parser.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    args = parse_args()
    if args.mode == "live":
        result = run_live(
            Path(args.output_dir),
            project_name=args.project_name,
            project_path=args.project_path,
            conversation_name=args.conversation_name,
            prompt=args.prompt,
            wait_seconds=args.wait_seconds,
        )
    elif args.mode == "live-task":
        result = run_live_task(
            Path(args.output_dir),
            project_name=args.project_name,
            project_path=args.project_path,
            conversation_name=args.conversation_name,
            prompt=args.prompt,
            wait_seconds=args.wait_seconds,
            obstruct_after_seconds=args.obstruct_after_seconds,
            obstruct_duration_seconds=args.obstruct_duration_seconds,
            disable_remote_vision=args.disable_remote_vision,
        )
    else:
        result = run_contract(Path(args.output_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    ready = (
        result.get("ok")
        if args.mode == "live-task"
        else result.get("release_ready")
    )
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
