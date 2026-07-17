#!/usr/bin/env python3
"""Run OS Assistant evidence demo tasks from the desktop console.

The desktop shell only starts this runner. The real OS workflow stays in the
Windows UIA MCP skill layer, so the console remains a launch/evidence surface.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from l3_client.local_mcps.windows_uia_mcp.os_tasks import WindowsOSAutomation  # noqa: E402


def _load_dotenv_if_present() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        lines = env_path.read_text(encoding="utf-8-sig").splitlines()
    except Exception:
        return
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip().strip('"').strip("'")
        os.environ[key] = value


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


def _recipients_text(recipients: list[str]) -> str:
    clean = [str(item or "").strip() for item in recipients if str(item or "").strip()]
    if not clean:
        return "Neil"
    if len(clean) == 1:
        return clean[0]
    return " 和 ".join(clean)


def _live_recipient_allowlist() -> set[str]:
    raw = os.getenv("JACHIN_WEB_RESEARCH_LIVE_RECIPIENT_ALLOWLIST")
    if raw is None:
        raw = "Neil,测试备注冒烟草稿"
    return {item.strip().lower() for item in raw.replace("，", ",").split(",") if item.strip()}


def _live_recipients_guard(recipients: list[str]) -> dict[str, object]:
    clean = [str(item or "").strip() for item in recipients if str(item or "").strip()]
    allowlist = _live_recipient_allowlist()
    blocked = [item for item in clean if item.lower() not in allowlist]
    return {
        "ok": bool(clean) and not blocked,
        "recipients": clean,
        "allowlist": sorted(allowlist),
        "blocked": blocked,
        "reason": "" if clean and not blocked else ("missing_recipient" if not clean else "recipient_not_in_live_allowlist"),
    }


def _message_sha256(message: str) -> str:
    return hashlib.sha256(str(message or "").encode("utf-8")).hexdigest()


async def _web_research_direct_task(
    *,
    query: str,
    recipients: list[str],
    dry_run: bool,
) -> dict:
    from l3_node.cognitive_kernel.direct_mainline import try_execute_cognitive_direct_plan
    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn
    from l3_node.cognitive_kernel.ledger import current_ledger_path
    from l3_node.cognitive_kernel.pipeline import build_cognitive_turn_context

    run_id = f"os-evidence-web-research-{int(time.time() * 1000)}"
    live_guard = {"ok": True, "recipients": recipients, "allowlist": [], "blocked": [], "reason": "dry_run"}
    if not dry_run:
        live_guard = _live_recipients_guard(recipients)
        if not live_guard["ok"]:
            return {
                "ok": False,
                "reply": f"live_run_blocked:{live_guard['reason']}",
                "preview_message": "",
                "delivery_evidence": {
                    "delivery_mode": "live_run",
                    "live_guard": live_guard,
                    "post_send_verified": False,
                    "reason": live_guard["reason"],
                },
                "live_guard": live_guard,
                "user_input": "",
                "run_id": run_id,
                "ledger_path": str(current_ledger_path()),
                "plan": {},
            }

    delivery_hint = "只演练，不要发送" if dry_run else "真实发送"
    user_input = f"{delivery_hint}：搜索{query}，整理成中文简报，最后发给{_recipients_text(recipients)}"
    tools = [
        {"id": "mcp:tavily_search", "label": "Tavily Search"},
        {"id": "mcp:fetch", "label": "Fetch Web Page"},
        {"id": "core:web_research_summarize", "label": "Web Research Summarizer"},
        {"id": "mcp:windows_lark_send_message", "label": "Windows Lark Send Message"},
    ]

    ctx = await build_cognitive_turn_context(
        run_id=run_id,
        user_input=user_input,
        channel="evidence_console",
        session_id="os_evidence_console_web_research",
        desktop_companion_context={
            "evidence_template": "web_research_lark",
            "delivery_mode": "dry_run" if dry_run else "live_run",
        },
    )
    plan = plan_cognitive_turn(ctx, emit_non_execution_closure=False)
    _normalize_web_research_plan_slots(plan, query=query, recipients=recipients, dry_run=dry_run)

    async def run_tool(tool_id: str, work_order_input: str, _allowed_skills: list[str] | None = None) -> str:
        if tool_id != "mcp:windows_lark_send_message":
            return json.dumps(
                {
                    "ok": False,
                    "error": f"unexpected_transport_call:{tool_id}",
                    "note": "search/fetch/summary should be handled by RoleExecutor native path",
                },
                ensure_ascii=False,
            )
        payload = json.loads(work_order_input or "{}")
        if payload.get("dry_run") is True or payload.get("delivery_mode") == "dry_run":
            return json.dumps(
                {
                    "ok": True,
                    "dry_run": True,
                    "dry_run_preview_verified": True,
                    "message": payload.get("message") or "",
                    "recipients_json": payload.get("recipients_json") or json.dumps(recipients, ensure_ascii=False),
                    "detail": "preview_generated_no_external_send",
                },
                ensure_ascii=False,
            )
        send_guard = _live_recipients_guard(
            _loads_list(str(payload.get("recipients_json") or json.dumps(recipients, ensure_ascii=False)))
        )
        if not send_guard["ok"]:
            return json.dumps(
                {
                    "ok": False,
                    "delivery_mode": "live_run",
                    "live_guard": send_guard,
                    "error": send_guard["reason"],
                    "detail": "live_send_blocked_by_runner_guard",
                },
                ensure_ascii=False,
            )
        from l3_client.local_mcps.windows_uia_mcp import server as windows_uia_server

        return windows_uia_server.windows_lark_send_message(
            recipients_json=str(payload.get("recipients_json") or json.dumps(recipients, ensure_ascii=False)),
            message=str(payload.get("message") or ""),
            out_dir="",
            max_attempts=2,
        )

    reply = await try_execute_cognitive_direct_plan(
        plan=plan,
        tools=tools,
        allowed_skills=None,
        run_tool_func=run_tool,
        user_input=user_input,
        session_id="os_evidence_console_web_research",
        channel="evidence_console",
    )
    reply_text = str(reply or "")
    ok = bool(reply_text) and not _looks_like_direct_failure(reply_text)
    ledger_path = current_ledger_path()
    preview_message = _extract_lark_preview_message(ledger_path, run_id)
    delivery_evidence = _extract_lark_delivery_evidence(
        ledger_path,
        run_id,
        message=preview_message,
        live_guard=live_guard,
    )
    return {
        "ok": ok,
        "reply": reply_text,
        "preview_message": preview_message,
        "delivery_evidence": delivery_evidence,
        "live_guard": live_guard,
        "user_input": user_input,
        "run_id": run_id,
        "ledger_path": str(ledger_path),
        "plan": plan.to_dict(),
    }


def _extract_lark_preview_message(ledger_path: Path, turn_id: str) -> str:
    if not ledger_path.exists():
        return ""
    best = ""
    try:
        lines = ledger_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return ""
    for line in lines:
        try:
            event = json.loads(line)
        except Exception:
            continue
        if event.get("turn_id") != turn_id:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if event.get("event_type") == "work_order":
            inputs = payload.get("inputs") if isinstance(payload.get("inputs"), dict) else {}
            if inputs.get("tool") == "mcp:windows_lark_send_message":
                raw = str(inputs.get("work_order_input") or "")
                try:
                    obj = json.loads(raw)
                except Exception:
                    obj = {}
                message = str(obj.get("message") or "").strip() if isinstance(obj, dict) else ""
                if len(message) > len(best):
                    best = message
            continue
        if payload.get("tool") != "mcp:windows_lark_send_message":
            continue
        observation = str(payload.get("observation_preview") or "")
        if not observation:
            evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
            evidence_preview = str(evidence.get("send_preview") or "").strip()
            if evidence_preview:
                best = evidence_preview
            continue
        try:
            obj = json.loads(observation)
        except Exception:
            obj = {}
        if isinstance(obj, dict):
            message = str(obj.get("message") or "").strip()
            if message:
                best = message
                continue
        evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
        send_result = evidence.get("send_result") if isinstance(evidence.get("send_result"), dict) else {}
        send_reason = str(send_result.get("reason") or "").strip()
        if send_reason:
            best = send_reason
            continue
        evidence_preview = str(evidence.get("send_preview") or "").strip()
        if evidence_preview:
            best = evidence_preview
    return best


def _extract_lark_delivery_evidence(
    ledger_path: Path,
    turn_id: str,
    *,
    message: str,
    live_guard: dict[str, object],
) -> dict[str, object]:
    evidence: dict[str, object] = {
        "turn_id": turn_id,
        "message_sha256": _message_sha256(message),
        "message_len": len(str(message or "")),
        "live_guard": live_guard,
        "role_execution_found": False,
        "delivery_mode": "dry_run" if str(live_guard.get("reason") or "") == "dry_run" else "live_run",
        "post_send_verified": False,
        "dry_run_preview_verified": False,
    }
    if not ledger_path.exists():
        evidence["reason"] = "ledger_not_found"
        return evidence
    try:
        lines = ledger_path.read_text(encoding="utf-8").splitlines()
    except Exception as exc:
        evidence["reason"] = f"ledger_read_failed:{type(exc).__name__}"
        return evidence
    for line in lines:
        try:
            event = json.loads(line)
        except Exception:
            continue
        if event.get("turn_id") != turn_id or event.get("event_type") != "role_execution_finished":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if payload.get("tool") != "mcp:windows_lark_send_message":
            continue
        role_evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
        evidence.update(
            {
                "role_execution_found": True,
                "role_id": payload.get("role_id"),
                "work_order_id": payload.get("work_order_id"),
                "adapter_role": payload.get("adapter_role"),
                "elapsed_ms": payload.get("elapsed_ms"),
                "delivery_mode": role_evidence.get("delivery_mode") or evidence["delivery_mode"],
                "post_send_verified": bool(role_evidence.get("post_send_verified")),
                "dry_run_preview_verified": bool(role_evidence.get("dry_run_preview_verified")),
                "duplicate_skipped": bool(role_evidence.get("duplicate_skipped")),
                "dedupe_key": role_evidence.get("dedupe_key"),
                "send_result": role_evidence.get("send_result"),
                "quality_report": role_evidence.get("web_research_quality_report"),
                "source_quality": role_evidence.get("source_quality"),
                "observation_preview": payload.get("observation_preview"),
            }
        )
    if not evidence["role_execution_found"]:
        evidence["reason"] = "lark_role_execution_missing"
    elif evidence["delivery_mode"] == "live_run" and not evidence["post_send_verified"]:
        evidence["reason"] = "live_send_post_verification_missing"
    elif evidence["delivery_mode"] == "dry_run" and not evidence["dry_run_preview_verified"]:
        evidence["reason"] = "dry_run_preview_verification_missing"
    else:
        evidence["reason"] = "verified"
    return evidence


def _looks_like_direct_failure(text: str) -> bool:
    low = str(text or "").lower()
    return any(
        marker in low
        for marker in (
            "没有通过验证",
            "verification_failure",
            "missing_tavily_api_key",
            "missing tavily",
            "live_run_blocked",
            "recipient_not_in_live_allowlist",
            "message_post_send_verification_missing",
            "failed",
            '"ok": false',
        )
    )


def _normalize_web_research_plan_slots(plan, *, query: str, recipients: list[str], dry_run: bool) -> None:
    delivery_mode = "dry_run" if dry_run else "live_run"
    recipients_json = json.dumps(recipients or ["Neil"], ensure_ascii=False)
    try:
        plan.review_summary.target["query"] = query
        plan.review_summary.target["name"] = query
        plan.review_summary.target["recipients"] = recipients or ["Neil"]
        plan.review_summary.target["delivery_mode"] = delivery_mode
        plan.decision_contract.target["query"] = query
        plan.decision_contract.target["name"] = query
        plan.decision_contract.target["recipients"] = recipients or ["Neil"]
        plan.decision_contract.target["delivery_mode"] = delivery_mode
    except Exception:
        pass
    for work_order in getattr(plan, "work_orders", []) or []:
        tool = str(work_order.inputs.get("tool") or "")
        raw = str(work_order.inputs.get("work_order_input") or "")
        try:
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if tool == "mcp:tavily_search":
            payload["query"] = query
        elif tool == "mcp:fetch":
            payload.setdefault("query", query)
        elif tool == "core:web_research_summarize":
            payload["query"] = query
            payload["recipients_json"] = recipients_json
        elif tool == "mcp:windows_lark_send_message":
            payload["query"] = query
            payload["recipients_json"] = recipients_json
            payload["delivery_mode"] = delivery_mode
            payload["dry_run"] = dry_run
            payload["send_allowed"] = not dry_run
        if payload:
            work_order.inputs["work_order_input"] = json.dumps(payload, ensure_ascii=False)


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
    elif template_id == "web_research_lark":
        query = (args.web_query or "").strip() or "最新 AI 模型相关消息"
        direct = asyncio.run(_web_research_direct_task(query=query, recipients=recipients, dry_run=args.dry_run))
        evidence_payload = {
            "task": "web_research_lark_template",
            "ok": bool(direct.get("ok")),
            "detail": "dry_run_preview_generated" if args.dry_run else "live_run_delivery_attempted",
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "template": {
                "id": "web_research_lark",
                "workflow_id": "web_research_to_lark",
                "description": "Search -> fetch -> per-page summary -> final brief -> quality gate -> Lark preview/delivery",
                "delivery_mode": "dry_run" if args.dry_run else "live_run",
                "query": query,
                "recipients": recipients,
            },
            "message_preview": str(direct.get("preview_message") or direct.get("reply") or ""),
            "message_sha256": _message_sha256(str(direct.get("preview_message") or "")),
            "recipients": recipients,
            "live_guard": direct.get("live_guard") or {},
            "delivery_evidence": direct.get("delivery_evidence") or {},
            "control": {
                "turn_id": direct.get("run_id"),
                "ledger_path": direct.get("ledger_path"),
                "user_input": direct.get("user_input"),
            },
            "plan_preview": direct.get("plan") or {},
            "timeline": [
                {
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "stage": "web_research_template_dispatch",
                    "status": "done" if direct.get("ok") else "failed",
                    "detail": str(direct.get("reply") or "no direct reply"),
                    "evidence": {
                        "query": query,
                        "recipients": recipients,
                        "dry_run": bool(args.dry_run),
                        "ledger_path": direct.get("ledger_path"),
                        "live_guard": direct.get("live_guard") or {},
                        "delivery_evidence": direct.get("delivery_evidence") or {},
                    },
                }
            ],
        }
        evidence_path = out_dir / "web_research_lark_template.evidence.json"
        evidence_payload["evidence_path"] = str(evidence_path)
        evidence_path.write_text(json.dumps(evidence_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        result = type(
            "WebResearchTemplateResult",
            (),
            {
                "task": evidence_payload["task"],
                "ok": bool(direct.get("ok")),
                "detail": evidence_payload["detail"],
                "evidence": evidence_payload,
            },
        )()
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
    _load_dotenv_if_present()
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["standard_demo", "smoke_matrix", "template", "preflight"], required=True)
    parser.add_argument("--template-id", default="")
    parser.add_argument("--project-name", default="Jachin")
    parser.add_argument("--project-path", default="")
    parser.add_argument("--recipients-json", default='["Vivian"]')
    parser.add_argument("--since-days", type=int, default=3)
    parser.add_argument("--wait-seconds", type=int, default=120)
    parser.add_argument("--web-query", default="")
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
