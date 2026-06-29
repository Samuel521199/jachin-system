#!/usr/bin/env python3
"""Run OS Assistant evidence demo tasks from the desktop console.

The desktop shell only starts this runner. The real OS workflow stays in the
Windows UIA MCP skill layer, so the console remains a launch/evidence surface.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from l3_client.local_mcps.windows_uia_mcp.os_tasks import WindowsOSAutomation  # noqa: E402


def _loads_list(raw: str) -> list[str]:
    text = str(raw or "[]").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip()
    try:
        data = json.loads(text or "[]")
    except Exception:
        try:
            data = json.loads(text.replace('\\"', '"') or "[]")
        except Exception:
            if text.startswith("[") and text.endswith("]"):
                data = [item.strip().strip("'\"") for item in text[1:-1].split(",") if item.strip()]
            else:
                data = [text] if text else []
    if isinstance(data, str):
        data = [data]
    if not isinstance(data, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in data:
        text = str(item or "").strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def _json_result(result) -> dict:
    return {
        "task": result.task,
        "ok": result.ok,
        "detail": result.detail,
        "evidence_path": result.evidence.get("evidence_path"),
        "evidence_panel_path": result.evidence.get("evidence_panel_path"),
        "report_path": result.evidence.get("report_path"),
        "recipients": result.evidence.get("recipients"),
    }


def _ensure_evidence_file(out_dir: Path, result, label: str) -> Path:
    evidence_path = result.evidence.get("evidence_path") if isinstance(result.evidence, dict) else None
    if evidence_path and Path(str(evidence_path)).exists():
        return Path(str(evidence_path))
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{label}.evidence.json"
    payload = {
        "task": result.task,
        "ok": result.ok,
        "detail": result.detail,
        "generated_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "evidence_path": str(path),
        "timeline": [
            {
                "ts": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
                "stage": "run_template",
                "status": "done" if result.ok else "failed",
                "detail": result.detail,
                "evidence": result.evidence,
            }
        ],
        **(result.evidence if isinstance(result.evidence, dict) else {"evidence": result.evidence}),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _write_runner_summary(out_dir: Path, payload: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "os_evidence_task_runner_result.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _write_run_state(out_dir: Path, *, mode: str, status: str, payload: dict | None = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "mode": mode,
        "pid": os.getpid(),
        "status": status,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "out_dir": str(out_dir),
    }
    if payload:
        state.update(payload)
    (out_dir / "run_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def run_standard_demo(args: argparse.Namespace) -> dict:
    out_dir = Path(args.out_dir).expanduser().resolve()
    _write_run_state(out_dir, mode="standard_demo", status="running", payload={"project_name": args.project_name, "recipients": _loads_list(args.recipients_json)})
    auto = WindowsOSAutomation(out_dir=out_dir)
    result = auto.codex_lark_standard_demo(
        project_name=args.project_name,
        project_path=args.project_path,
        recipients=_loads_list(args.recipients_json),
        since_days=args.since_days,
        wait_seconds=args.wait_seconds,
        send_summary=not args.dry_run,
        remember=True,
    )
    payload = {"mode": "standard_demo", "result": _json_result(result)}
    payload["summary_path"] = str(_write_runner_summary(out_dir, payload))
    _write_run_state(out_dir, mode="standard_demo", status="completed" if result.ok else "check", payload=payload)
    return payload


def run_smoke_matrix(args: argparse.Namespace) -> dict:
    out_dir = Path(args.out_dir).expanduser().resolve()
    _write_run_state(out_dir, mode="smoke_matrix", status="running", payload={"project_name": args.project_name})
    scenarios = [
        {"name": "Vivian", "recipients": ["Vivian"]},
        {"name": "Vivian + Samuel", "recipients": ["Vivian", "Samuel"]},
        {"name": "Vivian + 测试备注冒烟草稿", "recipients": ["Vivian", "测试备注冒烟草稿"]},
    ]
    rows = []
    for index, scenario in enumerate(scenarios, start=1):
        auto = WindowsOSAutomation(out_dir=out_dir / f"scenario_{index}_{scenario['name'].replace(' ', '_').replace('+', 'and')}")
        result = auto.codex_lark_standard_demo(
            project_name=args.project_name,
            project_path=args.project_path,
            recipients=scenario["recipients"],
            since_days=args.since_days,
            wait_seconds=args.wait_seconds,
            send_summary=not args.dry_run,
            remember=True,
        )
        row = {"scenario": scenario["name"], **_json_result(result)}
        rows.append(row)
    passed = sum(1 for row in rows if row.get("ok"))
    payload = {"mode": "smoke_matrix", "passed": passed, "total": len(rows), "rows": rows}
    payload["summary_path"] = str(_write_runner_summary(out_dir, payload))
    _write_run_state(out_dir, mode="smoke_matrix", status="completed" if passed == len(rows) else "check", payload=payload)
    return payload


def run_template(args: argparse.Namespace) -> dict:
    out_dir = Path(args.out_dir).expanduser().resolve()
    _write_run_state(out_dir, mode="template", status="running", payload={"template_id": args.template_id, "project_name": args.project_name})
    auto = WindowsOSAutomation(out_dir=out_dir)
    recipients = _loads_list(args.recipients_json)
    template_id = str(args.template_id or "").strip()
    if template_id == "codex_project_lark":
        result = auto.codex_lark_standard_demo(
            project_name=args.project_name,
            project_path=args.project_path,
            recipients=recipients,
            since_days=args.since_days,
            wait_seconds=args.wait_seconds,
            send_summary=not args.dry_run,
            remember=True,
        )
    elif template_id == "router_codex_project_lark":
        from l3_node.capability_router import choose_capability_route
        from l3_node.clarification_policy import decide_clarification
        from l3_node.mission_memory_center import apply_memory_to_intent
        from l3_node.mission_preview import build_mission_preview
        from l3_node.mission_runtime import build_plan_preview
        from l3_node.mission_template_library import select_mission_template
        from l3_node.os_mission_router import maybe_run_os_mission
        from l3_node.project_memory import remember_project
        from l3_node.semantic_intent_engine import parse_semantic_intent

        recipients_text = " 和 ".join(recipients) if recipients else "Vivian"
        if args.project_path:
            remember_project(args.project_name, args.project_path)
        user_input = f"总结 {args.project_name} 最近 {args.since_days} 天进展，按条列出来，发给 {recipients_text}"
        tools = [
            {"id": "mcp:windows_codex_lark_workflow_template"},
            {"id": "mcp:windows_project_remember"},
            {"id": "mcp:windows_lark_send_message"},
            {"id": "mcp:windows_open_app"},
        ]
        if args.dry_run:
            semantic = parse_semantic_intent(user_input)
            intent = semantic.intent
            memory_ev = apply_memory_to_intent(intent)
            route = choose_capability_route(intent, tools)
            template = select_mission_template(intent, route)
            clarification = decide_clarification(intent, route)
            plan = build_plan_preview(intent, route)
            preview = build_mission_preview(
                intent=intent,
                route=route,
                plan=plan,
                template=template,
                clarification=clarification,
                memory_evidence=memory_ev,
            )
            dry_payload = {
                "task": "os_mission_router_template_preview",
                "ok": True,
                "detail": "dry_run_router_template_ready",
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "intent": intent.to_dict(),
                "parser": semantic.meta,
                "memory": memory_ev,
                "template": template.to_dict() if template else {},
                "mission_preview": preview.to_dict(),
                "plan_preview": plan.to_dict(),
                "route": route.to_dict(),
                "clarification": clarification.to_dict(),
                "attempts": [],
                "retry": {"should_retry": False, "reason": "dry_run", "max_attempts": 0, "safe_to_retry": False},
                "metrics": {
                    "duration_ms": 0,
                    "attempt_count": 0,
                    "final_ok": True,
                    "failure_class": "none",
                    "workflow_id": route.workflow_id,
                    "tool_id": route.tool_id,
                    "task_type": intent.task_type.value,
                },
                "timeline": [
                    {
                        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "stage": "router_demo_preview",
                        "status": "done",
                        "detail": user_input,
                        "evidence": {"dry_run": True, "send_summary": False, "plan_preview": plan.to_dict()},
                    }
                ],
            }
            evidence_path = out_dir / "router_codex_project_lark_preview.evidence.json"
            dry_payload["evidence_path"] = str(evidence_path)
            evidence_path.write_text(json.dumps(dry_payload, ensure_ascii=False, indent=2), encoding="utf-8")
            result = type("DryRunResult", (), {"task": dry_payload["task"], "ok": True, "detail": dry_payload["detail"], "evidence": dry_payload})()
        else:
            reply = asyncio.run(maybe_run_os_mission(user_input=user_input, tools=tools))
            result = type(
                "RouterResult",
                (),
                {
                    "task": "router_codex_project_lark",
                    "ok": bool(reply),
                    "detail": "router_demo_completed" if reply else "router_demo_not_matched",
                    "evidence": {"reply": reply, "user_input": user_input},
                },
            )()
    elif template_id == "daily_office_briefing":
        result = auto.daily_office_briefing(
            recipients=recipients,
            paths_json=json.dumps([args.project_path] if args.project_path else [], ensure_ascii=False),
            since_days=1,
            send_summary=not args.dry_run,
            open_report=True,
        )
    elif template_id == "recent_files":
        result = auto.recent_files(paths_json=json.dumps([args.project_path] if args.project_path else [], ensure_ascii=False), since_days=1, max_results=120)
    elif template_id == "app_switch_matrix":
        result = auto.app_switch_matrix(apps_json=json.dumps(["codex", "lark", "browser", "explorer"], ensure_ascii=False))
    elif template_id == "project_memory":
        result = auto.project_remember(args.project_name, args.project_path)
    else:
        raise SystemExit(f"unknown template_id: {template_id}")
    evidence_path = _ensure_evidence_file(out_dir, result, template_id or "template")
    payload = {"mode": "template", "template_id": template_id, "result": _json_result(result)}
    payload["result"]["evidence_path"] = str(evidence_path)
    payload["summary_path"] = str(_write_runner_summary(out_dir, payload))
    _write_run_state(out_dir, mode="template", status="completed" if result.ok else "check", payload=payload)
    return payload


def run_preflight(args: argparse.Namespace) -> dict:
    out_dir = Path(args.out_dir).expanduser().resolve()
    _write_run_state(out_dir, mode="preflight", status="running", payload={"project_name": args.project_name})
    recipients = _loads_list(args.recipients_json)
    project = Path(args.project_path).expanduser() if args.project_path else None
    checks: list[dict] = [
        {
            "name": "project_path",
            "ok": bool(project and project.exists() and project.is_dir()),
            "detail": str(project or ""),
        },
        {
            "name": "recipients",
            "ok": bool(recipients),
            "detail": ", ".join(recipients),
        },
        {
            "name": "dry_run",
            "ok": not args.dry_run,
            "detail": "dry-run is on; Lark will not be sent" if args.dry_run else "send enabled",
            "warning": bool(args.dry_run),
        },
    ]
    auto = WindowsOSAutomation(out_dir=out_dir)
    for app in ("codex", "lark"):
        try:
            result = auto.ensure_app(app, timeout=4.0)
            checks.append({"name": f"app:{app}", "ok": result.ok, "detail": result.detail, "evidence": result.evidence})
        except Exception as exc:
            checks.append({"name": f"app:{app}", "ok": False, "detail": repr(exc)})
    blocking_ok = all(row.get("ok") or row.get("warning") for row in checks)
    payload = {"mode": "preflight", "ok": blocking_ok, "checks": checks}
    _write_runner_summary(out_dir, payload)
    _write_run_state(out_dir, mode="preflight", status="completed" if blocking_ok else "check", payload=payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["standard_demo", "smoke_matrix", "template", "preflight"], required=True)
    parser.add_argument("--template-id", default="")
    parser.add_argument("--project-name", default="Jachin")
    parser.add_argument("--project-path", default="")
    parser.add_argument("--recipients-json", default='["Vivian"]')
    parser.add_argument("--since-days", type=int, default=3)
    parser.add_argument("--wait-seconds", type=int, default=120)
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "output" / "os_evidence_console_runs"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.mode == "standard_demo":
        payload = run_standard_demo(args)
    elif args.mode == "smoke_matrix":
        payload = run_smoke_matrix(args)
    elif args.mode == "preflight":
        payload = run_preflight(args)
    else:
        payload = run_template(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
