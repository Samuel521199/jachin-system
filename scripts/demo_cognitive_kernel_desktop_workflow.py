"""Live desktop demo for the Memory-first Cognitive Kernel mainline.

Flow:
ReviewBoard/Arbiter-equivalent contract -> WorkOrder Dispatcher -> RoleExecutor
-> Verification -> TurnClosure -> Evidence bridge.

By default this script:
- Opens/switches/closes Windows Calculator through AppControlExecutorAgent.
- Reads a demo file through FileExecutorAgent.
- Runs a dry Lark send through MessageExecutorAgent.

Optional flags enable real file open/reveal and real Lark send.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
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


def _make_contract_and_work_order(
    *,
    turn_id: str,
    tool: str,
    action_input: str,
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
        task_type="desktop_live_demo",
        goal=goal,
        selected_workflow="desktop_app_file_message_evidence_demo",
        selected_roles=[role_agent, "VerificationAgent", "RecoveryAgent", "TurnClosureAgent"],
        risk_level=RiskLevel.LOW,
        tool_policy=policy,
        execution_allowed=True,
        verification_criteria=["role_execution_finished", "verification_report"],
        rationale=["stage_h_live_demo"],
    )
    work_order = WorkOrder(
        work_order_id=f"wo-{turn_id}",
        decision_id=decision_id,
        role_agent=role_agent,
        task=goal,
        inputs={"tool": tool, "action_input": action_input},
        tool_policy=policy,
        expected_outputs=["observation", "evidence"],
        verification_criteria=["ok"],
    )
    return contract, work_order


async def _dispatch_existing(
    *,
    turn_id: str,
    tool: str,
    action_input: str,
    role_agent: str,
    goal: str,
    legacy_executor,
):
    from l3_node.cognitive_kernel.dispatcher import dispatch_existing_work_order
    from l3_node.cognitive_kernel.runtime import close_turn

    contract, work_order = _make_contract_and_work_order(
        turn_id=turn_id,
        tool=tool,
        action_input=action_input,
        role_agent=role_agent,
        goal=goal,
    )
    result = await dispatch_existing_work_order(
        contract=contract,
        work_order=work_order,
        executor=legacy_executor,
    )
    close_turn(
        turn_id=contract.turn_id,
        final_text=f"{turn_id} done",
        executed_work_orders=[work_order.work_order_id],
        verification_reports=[result.verification],
        aborted=not bool(result.verification.ok),
    )
    return result


async def _run_demo(args: argparse.Namespace) -> list[dict[str, Any]]:
    demo_dir = _repo_root() / "output" / "cognitive_kernel_live_demo"
    demo_dir.mkdir(parents=True, exist_ok=True)
    demo_file = demo_dir / "desktop_workflow_demo.txt"
    demo_file.write_text("Jachin desktop workflow live demo file.\n", encoding="utf-8")

    results: list[dict[str, Any]] = []

    async def legacy_should_not_run(work_order):
        raise AssertionError(f"legacy executor should not run for {work_order.role_agent}")

    async def dry_message_sender(work_order):
        payload = json.loads(str(work_order.inputs.get("action_input") or "{}"))
        return json.dumps(
            {
                "ok": True,
                "send_ok": True,
                "dry_run": True,
                "recipients": payload.get("recipients") or payload.get("recipient") or [],
                "message_preview": str(payload.get("message") or payload.get("text") or "")[:200],
                "message_id": f"dry-{work_order.work_order_id}",
                "ocr": "dry run message preview verified",
            },
            ensure_ascii=False,
        )

    if platform.system().lower() == "windows" and not args.no_desktop:
        for name, tool, action in [
            (
                "app_open_calculator",
                "mcp:windows_open_app",
                {"app_name": "calculator", "args_json": "[]", "out_dir": str(demo_dir)},
            ),
            (
                "app_switch_calculator",
                "mcp:windows_window_switch",
                {"keywords": "calculator,calc,\u8ba1\u7b97\u5668", "timeout": 5.0, "out_dir": str(demo_dir)},
            ),
            (
                "app_close_calculator",
                "mcp:windows_window_close",
                {"keywords": "calculator,calc,\u8ba1\u7b97\u5668", "timeout": 5.0, "out_dir": str(demo_dir)},
            ),
        ]:
            result = await _dispatch_existing(
                turn_id=f"stage-h-{name}",
                tool=tool,
                action_input=json.dumps(action, ensure_ascii=False),
                role_agent="AppControlExecutorAgent",
                goal=f"Live demo {name}",
                legacy_executor=legacy_should_not_run,
            )
            results.append(_summary(name, result))
    else:
        results.append({"name": "app_control_skipped", "ok": True, "reason": "non_windows_or_no_desktop"})

    file_read = await _dispatch_existing(
        turn_id="stage-h-file-read",
        tool="core:fs_read",
        action_input=json.dumps({"path": str(demo_file)}, ensure_ascii=False),
        role_agent="FileExecutorAgent",
        goal="Read demo file through FileExecutorAgent.",
        legacy_executor=legacy_should_not_run,
    )
    results.append(_summary("file_read", file_read))

    if args.real_file_reveal and platform.system().lower() == "windows":
        for name, tool in [
            ("file_reveal", "mcp:windows_file_reveal_in_explorer"),
            ("file_open", "mcp:windows_file_open"),
        ]:
            result = await _dispatch_existing(
                turn_id=f"stage-h-{name}",
                tool=tool,
                action_input=json.dumps({"path": str(demo_file)}, ensure_ascii=False),
                role_agent="FileExecutorAgent",
                goal=f"Live demo {name}",
                legacy_executor=legacy_should_not_run,
            )
            results.append(_summary(name, result))

    message_payload = {
        "recipients": [args.recipient],
        "message": "Jachin Stage H live demo: AppControl + File + Message + Evidence chain is visible.",
    }
    message_result = await _dispatch_existing(
        turn_id="stage-h-message-dry" if not args.send_lark else "stage-h-message-real",
        tool="mcp:windows_lark_send_message",
        action_input=json.dumps(message_payload, ensure_ascii=False),
        role_agent="MessageExecutorAgent",
        goal="MessageExecutorAgent send preview and verification.",
        legacy_executor=_real_lark_sender if args.send_lark else dry_message_sender,
    )
    results.append(_summary("message_send_real" if args.send_lark else "message_send_dry", message_result))
    return results


async def _real_lark_sender(work_order):
    raw = str(work_order.inputs.get("action_input") or "{}")
    try:
        from core.mcp_client import mcp_registry

        result = await mcp_registry.invoke("mcp:windows_lark_send_message", raw)
        return json.dumps(
            {
                "ok": True,
                "send_ok": True,
                "channel": "mcp_registry",
                "raw_result": result,
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps(
            {
                "ok": False,
                "send_ok": False,
                "channel": "mcp_registry",
                "error": str(exc),
                "tool": "mcp:windows_lark_send_message",
            },
            ensure_ascii=False,
        )


def _summary(name: str, result: Any) -> dict[str, Any]:
    return {
        "name": name,
        "ok": bool(result.verification.ok),
        "role": result.work_order.role_agent,
        "work_order_id": result.work_order.work_order_id,
        "verification_id": result.verification.verification_id,
        "recovery": result.recovery_plan.to_dict() if result.recovery_plan else None,
        "observation_preview": str(result.observation or "")[:500],
    }


def _read_ledger_events(out_root: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in sorted((out_root / "ledger").glob("cognitive_kernel_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


def _write_bridge_evidence(
    *,
    out_root: Path,
    results: list[dict[str, Any]],
) -> Path:
    events = _read_ledger_events(out_root)
    out_dir = _repo_root() / "output" / "os_vision"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"cognitive_kernel_desktop_live_demo_{stamp}.evidence.json"
    timeline = [
        {
            "ts": str(event.get("ts_ms", "")),
            "stage": event.get("event_type", "event"),
            "status": "done",
            "detail": json.dumps(event.get("payload", {}), ensure_ascii=False)[:800],
            "evidence": event.get("payload", {}),
        }
        for event in events
    ]
    role_events = [
        event
        for event in events
        if event.get("event_type") in {"role_execution_started", "role_execution_finished"}
    ]
    path.write_text(
        json.dumps(
            {
                "task": "Cognitive Kernel desktop live demo",
                "ok": all(row.get("ok") for row in results),
                "detail": "desktop_live_demo_complete",
                "timeline": timeline,
                "role_executions": role_events,
                "metrics": {
                    "workflow_id": "desktop_app_file_message_evidence_demo",
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
    results = await _run_demo(args)
    evidence = _write_bridge_evidence(out_root=out_root, results=results)
    payload = {
        "ok": all(row.get("ok") for row in results),
        "out_root": str(out_root),
        "ledger_dir": str(out_root / "ledger"),
        "bridge_evidence": str(evidence),
        "results": results,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out-root",
        default=str(_repo_root() / "output" / "cognitive_kernel_desktop_live_demo"),
    )
    parser.add_argument("--no-desktop", action="store_true", help="Skip real Windows app open/switch/close.")
    parser.add_argument("--real-file-reveal", action="store_true", help="Open/reveal the demo file on Windows.")
    parser.add_argument("--send-lark", action="store_true", help="Actually send the demo message through Lark.")
    parser.add_argument("--recipient", default="Neil")
    return asyncio.run(_main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
