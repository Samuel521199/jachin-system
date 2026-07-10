"""Smoke-test the Cognitive Kernel Stage E evidence chain.

This script validates the path:

DecisionContract -> WorkOrder -> RoleExecutor -> Verification -> Recovery -> Ledger

It uses real FileExecutor native file I/O, simulated app/message tools, and a
safe fake MemoryWrite channel by default. Pass ``--real-memory`` only when you
want to append to the configured local Memory Nexus.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _configure_env(out_root: Path) -> None:
    repo = str(_repo_root())
    if repo not in sys.path:
        sys.path.insert(0, repo)
    os.environ["JACHIN_COGNITIVE_KERNEL_HOME"] = str(out_root)
    os.environ.setdefault("JACHIN_HOME", str(out_root / "jachin_home"))


async def _dispatches(real_memory: bool) -> list[dict[str, Any]]:
    from l3_node.cognitive_kernel.dispatcher import dispatch_tool_work_order
    from l3_node.cognitive_kernel.runtime import close_turn

    results = []

    async def legacy_should_not_run(_work_order):
        raise AssertionError("direct role executor should handle this tool")

    file_write = await dispatch_tool_work_order(
        turn_id="stage-e-file-write",
        goal="Stage E smoke writes a file through FileExecutorAgent.",
        tool="core:fs_write",
        work_order_input='{"path":"stage_e/smoke.txt","content":"stage-e-file-ok"}',
        executor=legacy_should_not_run,
    )
    close_turn(
        turn_id=file_write.contract.turn_id,
        final_text="file write smoke done",
        executed_work_orders=[file_write.work_order.work_order_id],
        verification_reports=[file_write.verification],
    )
    results.append(_result_summary("file_write", file_write))

    file_read = await dispatch_tool_work_order(
        turn_id="stage-e-file-read",
        goal="Stage E smoke reads a file through FileExecutorAgent.",
        tool="core:fs_read",
        work_order_input='{"path":"stage_e/smoke.txt"}',
        executor=legacy_should_not_run,
    )
    close_turn(
        turn_id=file_read.contract.turn_id,
        final_text="file read smoke done",
        executed_work_orders=[file_read.work_order.work_order_id],
        verification_reports=[file_read.verification],
    )
    results.append(_result_summary("file_read", file_read))

    app_calls = {"n": 0}

    async def flaky_app(_work_order):
        app_calls["n"] += 1
        if app_calls["n"] == 1:
            return '{"ok":false,"error":"timeout"}'
        return '{"ok":true,"active_window":"Calculator","screenshot_path":"C:/tmp/stage_e_calc.png"}'

    app_switch = await dispatch_tool_work_order(
        turn_id="stage-e-app-switch",
        goal="Stage E smoke switches an app and auto-recovers once.",
        tool="mcp:windows_window_switch",
        work_order_input='{"window_title":"Calculator"}',
        executor=flaky_app,
    )
    close_turn(
        turn_id=app_switch.contract.turn_id,
        final_text="app switch smoke done",
        executed_work_orders=[app_switch.work_order.work_order_id],
        verification_reports=[app_switch.verification],
    )
    app_summary = _result_summary("app_switch_recovery", app_switch)
    app_summary["calls"] = app_calls["n"]
    results.append(app_summary)

    async def dry_lark_sender(_work_order):
        return json.dumps(
            {
                "ok": True,
                "send_ok": True,
                "dry_run": True,
                "recipient": "Neil",
                "message_id": "stage-e-dry",
                "ocr": "Neil stage-e smoke message visible",
            },
            ensure_ascii=False,
        )

    lark_send = await dispatch_tool_work_order(
        turn_id="stage-e-message-send",
        goal="Stage E smoke validates MessageExecutorAgent evidence without sending real Lark.",
        tool="mcp:lark_send_text",
        work_order_input='{"recipients":["Neil"],"text":"Stage E smoke: dry Lark message."}',
        executor=dry_lark_sender,
    )
    close_turn(
        turn_id=lark_send.contract.turn_id,
        final_text="message smoke done",
        executed_work_orders=[lark_send.work_order.work_order_id],
        verification_reports=[lark_send.verification],
    )
    results.append(_result_summary("message_send_dry", lark_send))

    if not real_memory:
        import l3_node.tools.core_local_memory_append as mem_append

        async def fake_append(*, content: str, tags: list[str] | None = None):
            return {"ok": True, "content": content, "tags": tags or [], "fake": True}

        mem_append.async_run_local_memory_append = fake_append

    memory_write = await dispatch_tool_work_order(
        turn_id="stage-e-memory-write",
        goal="Stage E smoke writes memory through MemoryWriteAgent.",
        tool="core:local_memory_append",
        work_order_input='{"content":"Stage E smoke validated role evidence chain.","tags":["stage-e","workflow"]}',
        executor=legacy_should_not_run,
    )
    close_turn(
        turn_id=memory_write.contract.turn_id,
        final_text="memory write smoke done",
        executed_work_orders=[memory_write.work_order.work_order_id],
        verification_reports=[memory_write.verification],
    )
    results.append(_result_summary("memory_write", memory_write))
    return results


def _result_summary(name: str, result: Any) -> dict[str, Any]:
    return {
        "name": name,
        "ok": bool(result.verification.ok),
        "role": result.work_order.role_agent,
        "work_order_id": result.work_order.work_order_id,
        "verification_id": result.verification.verification_id,
        "recovery": result.recovery_plan.to_dict() if result.recovery_plan else None,
        "observation_preview": str(result.observation or "")[:300],
    }


def _read_ledger_events(out_root: Path) -> list[dict[str, Any]]:
    ledger_dir = out_root / "ledger"
    events: list[dict[str, Any]] = []
    for path in sorted(ledger_dir.glob("cognitive_kernel_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


def _write_bridge_evidence(events: list[dict[str, Any]], results: list[dict[str, Any]]) -> Path:
    repo = _repo_root()
    out_dir = repo / "output" / "os_vision"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"cognitive_kernel_stage_e_smoke_{stamp}.evidence.json"
    ok = all(row["ok"] for row in results)
    role_events = [
        event
        for event in events
        if event.get("event_type") in {"role_execution_started", "role_execution_finished"}
    ]
    timeline = [
        {
            "ts": str(event.get("ts_ms", "")),
            "stage": event.get("event_type", "event"),
            "status": "done",
            "detail": json.dumps(event.get("payload", {}), ensure_ascii=False)[:500],
            "evidence": event.get("payload", {}),
        }
        for event in events
    ]
    path.write_text(
        json.dumps(
            {
                "task": "Cognitive Kernel Stage E smoke",
                "ok": ok,
                "detail": "stage_e_smoke_complete" if ok else "stage_e_smoke_failed",
                "timeline": timeline,
                "role_executions": role_events,
                "metrics": {
                    "workflow_id": "cognitive_kernel_stage_e_smoke",
                    "attempt_count": len(role_events),
                },
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


async def _main_async(args: argparse.Namespace) -> int:
    out_root = Path(args.out_root).resolve()
    _configure_env(out_root)
    results = await _dispatches(real_memory=args.real_memory)
    events = _read_ledger_events(out_root)
    evidence_path = _write_bridge_evidence(events, results)
    ok = all(row["ok"] for row in results)
    print(
        json.dumps(
            {
                "ok": ok,
                "out_root": str(out_root),
                "ledger_dir": str(out_root / "ledger"),
                "bridge_evidence": str(evidence_path),
                "results": results,
                "event_count": len(events),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out-root",
        default=str(_repo_root() / "output" / "cognitive_kernel_stage_e_smoke"),
        help="Cognitive Kernel home used for the smoke ledger.",
    )
    parser.add_argument(
        "--real-memory",
        action="store_true",
        help="Append to the configured Memory Nexus instead of using a safe fake append.",
    )
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
