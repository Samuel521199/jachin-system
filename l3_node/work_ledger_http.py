"""HTTP routes for the Jachin Work Ledger MVP."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _json_response(data: Any, status: int = 200):
    import aiohttp.web

    return aiohttp.web.json_response(data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


async def _json_body(request: Any) -> dict[str, Any]:
    try:
        if getattr(request, "body_exists", False):
            body = await request.json()
            return body if isinstance(body, dict) else {}
    except Exception:
        return {}
    return {}


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


async def handle_work_ledger_status(request: Any) -> Any:
    """GET /api/v1/work-ledger/status"""

    try:
        from l3_node.work_ledger import status

        return _json_response({"ok": True, **status()})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] status failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def handle_work_ledger_sessions(request: Any) -> Any:
    """GET /api/v1/work-ledger/sessions?limit=50"""

    try:
        from l3_node.work_ledger import list_sessions

        limit = int(request.query.get("limit") or 50)
        return _json_response({"ok": True, "sessions": list_sessions(limit)})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] sessions failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def handle_work_ledger_recall_index(request: Any) -> Any:
    """GET /api/v1/work-ledger/recall-index?days=7"""

    try:
        from l3_node.work_ledger import write_work_ledger_recall_index

        days = int(request.query.get("days") or 7)
        return _json_response({"ok": True, "index": write_work_ledger_recall_index(days)})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] recall index failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def handle_work_ledger_recall(request: Any) -> Any:
    """POST /api/v1/work-ledger/recall"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger import recall_work_ledger

        result = recall_work_ledger(
            str(body.get("query") or "").strip(),
            days=int(body.get("days") or 14),
            limit=int(body.get("limit") or 8),
        )
        return _json_response({"ok": True, **result})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] recall failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_weekly_report(request: Any) -> Any:
    """POST /api/v1/work-ledger/weekly-report"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger import generate_multi_day_weekly_report

        result = generate_multi_day_weekly_report(
            int(body.get("days") or 7),
            title=str(body.get("title") or "").strip() or None,
        )
        text = str(result.pop("text", "") or "")
        return _json_response({"ok": True, "text": text[:20000], **result})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] weekly report failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_instant_brief(request: Any) -> Any:
    """POST /api/v1/work-ledger/briefing"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger import generate_instant_work_brief

        days = int(body.get("days") or 1)
        if days not in {1, 7, 30}:
            return _json_response(
                {"ok": False, "error": "days must be one of: 1, 7, 30"},
                status=400,
            )
        result = await asyncio.to_thread(
            generate_instant_work_brief,
            days,
            title=str(body.get("title") or "").strip() or None,
            consult_codex=bool(body.get("consult_codex", False)),
            codex_wait_seconds=int(body.get("codex_wait_seconds") or 300),
        )
        text = str(result.pop("text", "") or "")
        return _json_response({"ok": True, "text": text[:30000], **result})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] instant brief failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_codex_consult(request: Any) -> Any:
    """POST /api/v1/work-ledger/codex-consult"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger_codex import consult_codex_for_scenario

        session_id = str(body.get("session_id") or "").strip()
        request_key = str(body.get("request_key") or "").strip()
        if not session_id or not request_key:
            return _json_response(
                {
                    "ok": False,
                    "error": "session_id and request_key are required",
                },
                status=400,
            )
        result = await asyncio.to_thread(
            consult_codex_for_scenario,
            session_id,
            request_key,
            wait_seconds=int(body.get("wait_seconds") or 120),
        )
        return _json_response(result)
    except Exception as e:
        logger.exception("[WorkLedger HTTP] Codex consultation failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_codex_invocations(request: Any) -> Any:
    """GET /api/v1/work-ledger/codex-invocations"""

    try:
        from l3_node.codex_invocation_manager import (
            get_codex_invocation_manager,
        )

        manager = get_codex_invocation_manager()
        limit = int(request.query.get("limit") or 50)
        session_id = str(request.query.get("session_id") or "").strip()
        status_filter = str(request.query.get("status") or "").strip()
        rows = manager.list(limit=max(limit, 100) if session_id else limit)
        if session_id:
            rows = [
                row
                for row in rows
                if str((row.get("metadata") or {}).get("session_id") or "")
                == session_id
            ]
        if status_filter:
            rows = [
                row
                for row in rows
                if str(row.get("status") or "") == status_filter
            ]
        rows = rows[: max(1, min(limit, 1000))]
        return _json_response(
            {
                "ok": True,
                "invocations": rows,
                "active_count": sum(
                    1
                    for row in rows
                    if row.get("status") in {"queued", "running", "waiting"}
                ),
            }
        )
    except Exception as e:
        logger.exception("[WorkLedger HTTP] Codex invocations failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def handle_work_ledger_codex_cancel(request: Any) -> Any:
    """POST /api/v1/work-ledger/codex-cancel"""

    body = await _json_body(request)
    try:
        from l3_node.codex_invocation_manager import (
            get_codex_invocation_manager,
        )

        invocation_id = str(body.get("invocation_id") or "").strip()
        if not invocation_id:
            return _json_response(
                {"ok": False, "error": "invocation_id is required"},
                status=400,
            )
        record = get_codex_invocation_manager().cancel(
            invocation_id,
            reason=str(body.get("reason") or "user_cancelled").strip(),
        )
        return _json_response({"ok": True, "invocation": record})
    except ValueError as e:
        return _json_response({"ok": False, "error": str(e)}, status=404)
    except Exception as e:
        logger.exception("[WorkLedger HTTP] Codex cancel failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def handle_work_ledger_session_detail(request: Any) -> Any:
    """GET /api/v1/work-ledger/sessions/{session_id}"""

    try:
        from l3_node.work_ledger import get_session_detail

        session_id = str(request.match_info.get("session_id") or "").strip()
        if not session_id:
            return _json_response({"ok": False, "error": "session_id is required"}, status=400)
        limit = int(request.query.get("evidence_limit") or 300)
        return _json_response({"ok": True, **get_session_detail(session_id, evidence_limit=limit)})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] detail failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def handle_work_ledger_output_text(request: Any) -> Any:
    """GET /api/v1/work-ledger/sessions/{session_id}/outputs/{output_key}"""

    try:
        from l3_node.work_ledger import read_output_text

        session_id = str(request.match_info.get("session_id") or "").strip()
        output_key = str(request.match_info.get("output_key") or "").strip()
        if not session_id:
            return _json_response({"ok": False, "error": "session_id is required"}, status=400)
        if not output_key:
            return _json_response({"ok": False, "error": "output_key is required"}, status=400)
        max_chars = int(request.query.get("max_chars") or 20000)
        return _json_response({"ok": True, **read_output_text(session_id, output_key, max_chars=max_chars)})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] output read failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_start(request: Any) -> Any:
    """POST /api/v1/work-ledger/start"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger import start_session

        result = start_session(
            title=str(body.get("title") or body.get("user_goal") or "").strip(),
            project_path=str(body.get("project_path") or "").strip() or None,
            user_goal=str(body.get("user_goal") or "").strip() or None,
            project_name=str(body.get("project_name") or "").strip() or None,
            tags=_string_list(body.get("tags")),
            created_from=str(body.get("created_from") or "console"),
            auto_collect=bool(body.get("auto_collect", True)),
        )
        return _json_response({"ok": True, **result})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] start failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_collect(request: Any) -> Any:
    """POST /api/v1/work-ledger/collect"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger import collect_snapshot

        result = collect_snapshot(
            str(body.get("session_id") or "").strip() or None,
            trigger=str(body.get("trigger") or "manual"),
        )
        return _json_response({"ok": True, "result": result})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] collect failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_note(request: Any) -> Any:
    """POST /api/v1/work-ledger/note"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger import add_manual_note

        result = add_manual_note(
            str(body.get("session_id") or "").strip() or None,
            str(body.get("text") or body.get("note") or "").strip(),
        )
        return _json_response({"ok": True, "evidence": result})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] note failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_ai_trace(request: Any) -> Any:
    """POST /api/v1/work-ledger/ai-trace"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger import add_ai_work_trace

        result = add_ai_work_trace(
            str(body.get("session_id") or "").strip() or None,
            str(body.get("text") or body.get("trace") or "").strip(),
            tool_name=str(body.get("tool_name") or "AI").strip() or "AI",
            trace_kind=str(body.get("trace_kind") or "console_import").strip() or "console_import",
        )
        return _json_response({"ok": True, "evidence": result})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] ai trace failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_import_process(request: Any) -> Any:
    """POST /api/v1/work-ledger/import-process"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger import get_session_detail, import_ai_work_process

        session_id = str(body.get("session_id") or "").strip() or None
        result = import_ai_work_process(
            session_id,
            text=str(body.get("text") or body.get("trace") or "").strip(),
            file_path=str(body.get("file_path") or "").strip(),
            tool_name=str(body.get("tool_name") or "").strip(),
            trace_kind=str(body.get("trace_kind") or "console_process_import").strip() or "console_process_import",
            auto_collect=bool(body.get("auto_collect", True)),
            generate_outputs_after=bool(body.get("generate_outputs", True)),
        )
        sid = str(result.get("session", {}).get("session_id") or session_id or "")
        detail = get_session_detail(sid) if sid else {}
        return _json_response({"ok": True, **result, **detail})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] import process failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_end_day_preview(request: Any) -> Any:
    """POST /api/v1/work-ledger/end-day-preview"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger import build_end_day_preview, get_session_detail

        session_id = str(body.get("session_id") or "").strip() or None
        result = build_end_day_preview(
            session_id,
            process_text=str(body.get("process_text") or body.get("text") or "").strip(),
            process_file_path=str(body.get("process_file_path") or body.get("file_path") or "").strip(),
            include_clipboard_hint=bool(body.get("include_clipboard_hint", False)),
        )
        sid = str(result.get("session", {}).get("session_id") or session_id or "")
        detail = get_session_detail(sid) if sid else {}
        return _json_response({"ok": True, **result, **detail})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] end-day preview failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_process_candidates(request: Any) -> Any:
    """POST /api/v1/work-ledger/process-candidates"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger import discover_work_process_candidates

        result = discover_work_process_candidates(
            str(body.get("session_id") or "").strip() or None,
            limit=int(body.get("limit") or 12),
        )
        return _json_response({"ok": True, **result})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] process candidates failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_candidate_quality(request: Any) -> Any:
    """POST /api/v1/work-ledger/candidate-quality"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger import build_work_process_candidate_source_quality

        days = int(body.get("days") or 30)
        quality = build_work_process_candidate_source_quality(days=days)
        return _json_response({"ok": True, "quality": quality})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] candidate quality failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_reliability(request: Any) -> Any:
    """GET /api/v1/work-ledger/reliability?days=7"""

    try:
        from l3_node.work_ledger import write_work_ledger_reliability_report

        days = int(request.query.get("days") or 7)
        reliability = write_work_ledger_reliability_report(days)
        return _json_response({"ok": True, "reliability": reliability})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] reliability failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_checkpoint(request: Any) -> Any:
    """POST /api/v1/work-ledger/checkpoint"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger import collect_work_checkpoint

        result = await asyncio.to_thread(
            collect_work_checkpoint,
            str(body.get("session_id") or "").strip() or None,
            trigger=str(body.get("trigger") or "http_manual").strip(),
            force=bool(body.get("force", False)),
        )
        return _json_response({"ok": True, **result})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] checkpoint failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_timeline(request: Any) -> Any:
    """GET /api/v1/work-ledger/sessions/{session_id}/timeline"""

    try:
        from l3_node.work_ledger import build_work_timeline

        session_id = str(request.match_info.get("session_id") or "").strip()
        limit = int(request.query.get("limit") or 200)
        return _json_response({"ok": True, "timeline": build_work_timeline(session_id, limit=limit)})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] timeline failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_process_inbox(request: Any) -> Any:
    """GET /api/v1/work-ledger/sessions/{session_id}/process-inbox"""

    try:
        from l3_node.work_ledger_sources import get_process_inbox

        session_id = str(request.match_info.get("session_id") or "").strip()
        if not session_id:
            return _json_response({"ok": False, "error": "session_id is required"}, status=400)
        return _json_response({"ok": True, "inbox": get_process_inbox(session_id)})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] process inbox failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_process_inbox_refresh(request: Any) -> Any:
    """POST /api/v1/work-ledger/process-inbox/refresh"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger_sources import refresh_process_inbox

        session_id = str(body.get("session_id") or "").strip()
        if not session_id:
            return _json_response({"ok": False, "error": "session_id is required"}, status=400)
        roots = _string_list(body.get("roots"))
        inline_sources = body.get("inline_sources") if isinstance(body.get("inline_sources"), list) else []
        inbox = await asyncio.to_thread(
            refresh_process_inbox,
            session_id,
            roots=roots or None,
            inline_sources=inline_sources,
            max_files=int(body.get("max_files") or 240),
        )
        return _json_response({"ok": True, "inbox": inbox})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] process inbox refresh failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_process_inbox_review(request: Any) -> Any:
    """POST /api/v1/work-ledger/process-inbox/review"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger_sources import review_process_inbox_event

        result = await asyncio.to_thread(
            review_process_inbox_event,
            str(body.get("session_id") or "").strip(),
            str(body.get("event_id") or "").strip(),
            str(body.get("action") or "").strip(),
            note=str(body.get("note") or "").strip(),
            generate_outputs_after=bool(body.get("generate_outputs", True)),
        )
        return _json_response({"ok": True, **result})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] process inbox review failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_project_facts(request: Any) -> Any:
    """GET /api/v1/work-ledger/sessions/{session_id}/project-facts"""

    try:
        from l3_node.work_ledger_facts import get_session_fact_context

        session_id = str(request.match_info.get("session_id") or "").strip()
        if not session_id:
            return _json_response(
                {"ok": False, "error": "session_id is required"},
                status=400,
            )
        context = await asyncio.to_thread(get_session_fact_context, session_id)
        return _json_response({"ok": True, "facts": context})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] project facts failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_project_fact_review(request: Any) -> Any:
    """POST /api/v1/work-ledger/project-facts/review"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger import append_evidence, generate_work_outputs, get_session_detail
        from l3_node.work_ledger_facts import review_fact_match

        session_id = str(body.get("session_id") or "").strip()
        candidate_id = str(body.get("candidate_id") or "").strip()
        action = str(body.get("action") or "").strip()
        if not session_id or not candidate_id:
            return _json_response(
                {
                    "ok": False,
                    "error": "session_id and candidate_id are required",
                },
                status=400,
            )
        session = get_session_detail(session_id, evidence_limit=1)["session"]
        project_path = str(session.get("project_path") or "").strip()
        result = await asyncio.to_thread(
            review_fact_match,
            project_path,
            candidate_id,
            action,
        )
        await asyncio.to_thread(
            append_evidence,
            session_id,
            source="work_project_fact_review",
            summary=f"Project fact review resolved: {action}",
            payload={
                "candidate_id": candidate_id,
                "action": action,
                "project_path": project_path,
            },
            trust_level="user_confirmed",
        )
        outputs = await asyncio.to_thread(generate_work_outputs, session_id)
        return _json_response({"ok": True, **result, "outputs": outputs})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] project fact review failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_project_fact_update(request: Any) -> Any:
    """POST /api/v1/work-ledger/project-facts/update"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger import append_evidence, generate_work_outputs
        from l3_node.work_ledger_facts import update_project_fact

        session_id = str(body.get("session_id") or "").strip()
        fact_id = str(body.get("fact_id") or "").strip()
        if not session_id or not fact_id:
            return _json_response(
                {"ok": False, "error": "session_id and fact_id are required"},
                status=400,
            )
        result = await asyncio.to_thread(
            update_project_fact,
            session_id,
            fact_id,
            target_state=str(body.get("target_state") or "").strip(),
            reason=str(body.get("reason") or "").strip(),
            decision=str(body.get("decision") or "").strip(),
            failure_reason=str(body.get("failure_reason") or "").strip(),
            next_action=str(body.get("next_action") or "").strip(),
            superseded_by_fact_id=str(
                body.get("superseded_by_fact_id") or ""
            ).strip(),
        )
        await asyncio.to_thread(
            append_evidence,
            session_id,
            source="work_project_fact_update",
            summary=(
                f"Project fact updated: {fact_id} -> "
                f"{result.get('fact', {}).get('state') or 'unchanged'}"
            ),
            payload={
                "fact_id": fact_id,
                "target_state": body.get("target_state") or "",
                "reason": body.get("reason") or "",
                "state_transition": result.get("state_transition"),
            },
            trust_level="user_confirmed",
        )
        outputs = await asyncio.to_thread(generate_work_outputs, session_id)
        return _json_response({"ok": True, **result, "outputs": outputs})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] project fact update failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_project_outcomes(request: Any) -> Any:
    """GET /api/v1/work-ledger/sessions/{session_id}/project-outcomes"""

    try:
        from l3_node.work_ledger_outcomes import get_session_outcome_context

        session_id = str(request.match_info.get("session_id") or "").strip()
        if not session_id:
            return _json_response(
                {"ok": False, "error": "session_id is required"},
                status=400,
            )
        context = await asyncio.to_thread(get_session_outcome_context, session_id)
        return _json_response({"ok": True, "outcomes": context})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] project outcomes failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_methodology_review(request: Any) -> Any:
    """POST /api/v1/work-ledger/methodology/review"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger import append_evidence, generate_work_outputs, get_session_detail
        from l3_node.work_ledger_outcomes import review_methodology_candidate

        session_id = str(body.get("session_id") or "").strip()
        candidate_id = str(body.get("candidate_id") or "").strip()
        action = str(body.get("action") or "").strip()
        if not session_id or not candidate_id:
            return _json_response(
                {
                    "ok": False,
                    "error": "session_id and candidate_id are required",
                },
                status=400,
            )
        session = get_session_detail(session_id, evidence_limit=1)["session"]
        project_path = str(session.get("project_path") or "").strip()
        result = await asyncio.to_thread(
            review_methodology_candidate,
            project_path,
            candidate_id,
            action,
            note=str(body.get("note") or "").strip(),
        )
        await asyncio.to_thread(
            append_evidence,
            session_id,
            source="work_methodology_review",
            summary=f"Methodology candidate reviewed: {action}",
            payload={
                "candidate_id": candidate_id,
                "action": action,
                "project_path": project_path,
                "note": body.get("note") or "",
            },
            trust_level="user_confirmed",
        )
        outputs = await asyncio.to_thread(generate_work_outputs, session_id)
        return _json_response({"ok": True, **result, "outputs": outputs})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] methodology review failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_value_chain(request: Any) -> Any:
    """GET /api/v1/work-ledger/sessions/{session_id}/value-chain"""

    try:
        from l3_node.work_ledger_value import get_session_value_context

        session_id = str(request.match_info.get("session_id") or "").strip()
        if not session_id:
            return _json_response(
                {"ok": False, "error": "session_id is required"},
                status=400,
            )
        context = await asyncio.to_thread(get_session_value_context, session_id)
        return _json_response({"ok": True, "value_chain": context})
    except Exception as e:
        try:
            from l3_node.work_ledger_value_diagnostics import (
                append_value_diagnostic_log,
            )

            append_value_diagnostic_log(
                "value_chain_read_error",
                status="error",
                session_id=str(request.match_info.get("session_id") or ""),
                summary=str(e),
                details={"exception_type": type(e).__name__},
            )
        except Exception:
            pass
        logger.exception("[WorkLedger HTTP] value chain failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_value_event(request: Any) -> Any:
    """POST /api/v1/work-ledger/value-events"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger import append_evidence, generate_work_outputs
        from l3_node.work_ledger_value import record_value_event

        session_id = str(body.get("session_id") or "").strip()
        event_type = str(body.get("event_type") or "").strip()
        if not session_id or not event_type:
            return _json_response(
                {"ok": False, "error": "session_id and event_type are required"},
                status=400,
            )
        outcome_ids = body.get("outcome_ids")
        if not isinstance(outcome_ids, list):
            outcome_ids = []
        evidence = await asyncio.to_thread(
            append_evidence,
            session_id,
            source="work_value_event",
            summary=f"Work value event recorded: {event_type}",
            payload={
                "event_type": event_type,
                "outcome_ids": outcome_ids,
                "output_key": body.get("output_key") or "",
                "channel": body.get("channel") or "",
                "note": body.get("note") or "",
                "impact_value": body.get("impact_value") or "",
                "related_session_id": body.get("related_session_id") or "",
                "methodology_id": body.get("methodology_id") or "",
            },
            trust_level="user_confirmed",
        )
        result = await asyncio.to_thread(
            record_value_event,
            session_id,
            event_type,
            outcome_ids=[
                str(value or "").strip()
                for value in outcome_ids
                if str(value or "").strip()
            ],
            output_key=str(body.get("output_key") or "").strip(),
            channel=str(body.get("channel") or "").strip(),
            note=str(body.get("note") or "").strip(),
            impact_value=str(body.get("impact_value") or "").strip(),
            related_session_id=str(
                body.get("related_session_id") or ""
            ).strip(),
            methodology_id=str(body.get("methodology_id") or "").strip(),
            evidence_id=str(evidence.get("evidence_id") or ""),
            idempotency_key=str(body.get("idempotency_key") or "").strip(),
        )
        outputs = await asyncio.to_thread(generate_work_outputs, session_id)
        return _json_response(
            {
                "ok": True,
                **result,
                "evidence": evidence,
                "outputs": outputs,
            }
        )
    except Exception as e:
        try:
            from l3_node.work_ledger_value_diagnostics import (
                append_value_diagnostic_log,
            )

            append_value_diagnostic_log(
                "value_event_write_error",
                status="error",
                session_id=str(body.get("session_id") or ""),
                summary=str(e),
                details={
                    "exception_type": type(e).__name__,
                    "event_type": body.get("event_type") or "",
                    "outcome_ids": body.get("outcome_ids") or [],
                },
            )
        except Exception:
            pass
        logger.exception("[WorkLedger HTTP] value event failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_value_diagnostics_run(request: Any) -> Any:
    """POST /api/v1/work-ledger/value-chain/diagnostics/run"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger_value_diagnostics import (
            run_value_chain_diagnostics,
        )

        session_id = str(body.get("session_id") or "").strip()
        if not session_id:
            return _json_response(
                {"ok": False, "error": "session_id is required"},
                status=400,
            )
        result = await asyncio.to_thread(
            run_value_chain_diagnostics,
            session_id,
        )
        return _json_response({"ok": True, "diagnostic": result})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] value diagnostics failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_value_diagnostics_logs(request: Any) -> Any:
    """GET /api/v1/work-ledger/value-chain/diagnostics/logs"""

    try:
        from l3_node.work_ledger_value_diagnostics import (
            read_value_diagnostic_logs,
        )

        limit = max(
            1,
            min(int(request.query.get("limit", "100") or 100), 500),
        )
        result = await asyncio.to_thread(read_value_diagnostic_logs, limit)
        return _json_response({"ok": True, "logs": result})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] value diagnostic logs failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_source_status(request: Any) -> Any:
    """GET /api/v1/work-ledger/sessions/{session_id}/source-status"""

    try:
        from l3_node.work_ledger_sources import get_work_source_status

        session_id = str(request.match_info.get("session_id") or "").strip()
        if not session_id:
            return _json_response({"ok": False, "error": "session_id is required"}, status=400)
        return _json_response({"ok": True, "status": get_work_source_status(session_id)})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] source status failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_source_configure(request: Any) -> Any:
    """POST /api/v1/work-ledger/source-configure"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger_sources import configure_work_source_roots

        status = configure_work_source_roots(
            str(body.get("session_id") or "").strip(),
            _string_list(body.get("roots")),
        )
        return _json_response({"ok": True, "status": status})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] source configure failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_source_control(request: Any) -> Any:
    """POST /api/v1/work-ledger/source-control"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger_sources import control_work_source

        status = control_work_source(
            str(body.get("session_id") or "").strip(),
            str(body.get("action") or "").strip(),
            source_key=str(body.get("source_key") or "").strip(),
        )
        return _json_response({"ok": True, "status": status})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] source control failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_source_revoke(request: Any) -> Any:
    """POST /api/v1/work-ledger/source-revoke"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger_sources import revoke_project_source_authorization

        status = revoke_project_source_authorization(
            str(body.get("session_id") or "").strip(),
            root=str(body.get("root") or "").strip(),
        )
        return _json_response({"ok": True, "status": status})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] source revoke failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def _work_ledger_checkpoint_loop() -> None:
    interval = max(60, int(os.environ.get("JACHIN_WORK_LEDGER_CHECKPOINT_SECONDS") or 300))
    while True:
        await asyncio.sleep(interval)
        try:
            from l3_node.work_ledger import collect_work_checkpoint, get_active_session

            active = await asyncio.to_thread(get_active_session)
            if active and active.get("session_id"):
                result = await asyncio.to_thread(
                    collect_work_checkpoint,
                    str(active["session_id"]),
                    trigger="l3_background_interval",
                    force=False,
                )
                if not result.get("deduplicated"):
                    logger.info("[WorkLedger] background checkpoint recorded session=%s", active.get("session_id"))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[WorkLedger] background checkpoint skipped: %s", e)


async def _work_ledger_source_sync_loop() -> None:
    """Incrementally refresh configured work sources without adopting candidates."""

    interval = max(30, int(os.environ.get("JACHIN_WORK_LEDGER_SOURCE_SYNC_SECONDS") or 180))
    while True:
        await asyncio.sleep(interval)
        try:
            from l3_node.work_ledger import get_active_session
            from l3_node.work_ledger_sources import refresh_process_inbox

            active = await asyncio.to_thread(get_active_session)
            if not active or not active.get("session_id"):
                continue
            session_id = str(active["session_id"])
            inbox = await asyncio.to_thread(refresh_process_inbox, session_id)
            stats = inbox.get("last_refresh") if isinstance(inbox.get("last_refresh"), dict) else {}
            high_quality = int(stats.get("high_quality_new_event_count") or 0)
            if high_quality:
                logger.info(
                    "[WorkLedger] source sync found %s high-quality event(s), awaiting review session=%s",
                    high_quality,
                    session_id,
                )
            elif int(stats.get("sources_failed") or 0):
                logger.warning(
                    "[WorkLedger] source sync completed with failures session=%s failed=%s backoff=%s",
                    session_id,
                    stats.get("sources_failed"),
                    stats.get("sources_backoff"),
                )
            else:
                logger.debug(
                    "[WorkLedger] source sync idle session=%s unchanged=%s duration_ms=%s",
                    session_id,
                    stats.get("sources_skipped_unchanged"),
                    stats.get("duration_ms"),
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[WorkLedger] background source sync skipped: %s", e)


async def _start_work_ledger_checkpoint_loop(app: Any) -> None:
    enabled = str(os.environ.get("JACHIN_WORK_LEDGER_AUTO_CHECKPOINT") or "1").strip().lower()
    if enabled not in {"0", "false", "no", "off"}:
        app["work_ledger_checkpoint_task"] = asyncio.create_task(
            _work_ledger_checkpoint_loop(),
            name="jachin-work-ledger-checkpoint",
        )
    source_sync_enabled = str(os.environ.get("JACHIN_WORK_LEDGER_AUTO_SOURCE_SYNC") or "1").strip().lower()
    if source_sync_enabled not in {"0", "false", "no", "off"}:
        app["work_ledger_source_sync_task"] = asyncio.create_task(
            _work_ledger_source_sync_loop(),
            name="jachin-work-ledger-source-sync",
        )


async def _stop_work_ledger_checkpoint_loop(app: Any) -> None:
    tasks = [
        task
        for task in (
            app.get("work_ledger_checkpoint_task"),
            app.get("work_ledger_source_sync_task"),
        )
        if task is not None
    ]
    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def handle_work_ledger_end_day_finalize(request: Any) -> Any:
    """POST /api/v1/work-ledger/end-day-finalize"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger import finalize_end_day_package, get_session_detail

        session_id = str(body.get("session_id") or "").strip() or None
        result = finalize_end_day_package(
            session_id,
            process_text=str(body.get("process_text") or body.get("text") or "").strip(),
            process_file_path=str(body.get("process_file_path") or body.get("file_path") or "").strip(),
            close_session=bool(body.get("close_session", False)),
        )
        sid = str(result.get("session", {}).get("session_id") or session_id or "")
        detail = get_session_detail(sid) if sid else {}
        return _json_response({"ok": True, **result, **detail})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] end-day finalize failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_generate(request: Any) -> Any:
    """POST /api/v1/work-ledger/generate"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger import generate_work_outputs, get_active_session, get_session_detail

        session_id = str(body.get("session_id") or "").strip()
        if not session_id:
            active = get_active_session()
            session_id = str(active.get("session_id") or "") if active else ""
        if not session_id:
            return _json_response({"ok": False, "error": "session_id is required and no active session exists"}, status=400)
        outputs = generate_work_outputs(session_id)
        detail = get_session_detail(session_id)
        return _json_response({"ok": True, "outputs": outputs, **detail})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] generate failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_adopt_output(request: Any) -> Any:
    """POST /api/v1/work-ledger/adopt-output"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger import adopt_work_output, get_active_session, get_session_detail

        session_id = str(body.get("session_id") or "").strip()
        if not session_id:
            active = get_active_session()
            session_id = str(active.get("session_id") or "") if active else ""
        output_key = str(body.get("output_key") or "").strip()
        if not session_id:
            return _json_response({"ok": False, "error": "session_id is required and no active session exists"}, status=400)
        if not output_key:
            return _json_response({"ok": False, "error": "output_key is required"}, status=400)
        evidence = adopt_work_output(
            session_id,
            output_key,
            adopted_by=str(body.get("adopted_by") or "user").strip() or "user",
            note=str(body.get("note") or "").strip(),
        )
        detail = get_session_detail(session_id)
        return _json_response({"ok": True, "evidence": evidence, **detail})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] adopt output failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_adopt_candidate(request: Any) -> Any:
    """POST /api/v1/work-ledger/adopt-candidate"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger import adopt_work_process_candidate, get_active_session, get_session_detail

        session_id = str(body.get("session_id") or "").strip()
        if not session_id:
            active = get_active_session()
            session_id = str(active.get("session_id") or "") if active else ""
        file_path = str(body.get("file_path") or "").strip()
        if not session_id:
            return _json_response({"ok": False, "error": "session_id is required and no active session exists"}, status=400)
        if not file_path:
            return _json_response({"ok": False, "error": "file_path is required"}, status=400)
        result = adopt_work_process_candidate(
            session_id,
            file_path,
            adopted_by=str(body.get("adopted_by") or "user").strip() or "user",
            note=str(body.get("note") or "").strip(),
            generate_outputs_after=bool(body.get("generate_outputs_after", True)),
        )
        detail = get_session_detail(session_id)
        return _json_response({"ok": True, "result": result, **detail})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] adopt candidate failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_reject_candidate(request: Any) -> Any:
    """POST /api/v1/work-ledger/reject-candidate"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger import get_active_session, get_session_detail, record_work_process_candidate_feedback

        session_id = str(body.get("session_id") or "").strip()
        if not session_id:
            active = get_active_session()
            session_id = str(active.get("session_id") or "") if active else ""
        file_path = str(body.get("file_path") or "").strip()
        if not session_id:
            return _json_response({"ok": False, "error": "session_id is required and no active session exists"}, status=400)
        if not file_path:
            return _json_response({"ok": False, "error": "file_path is required"}, status=400)
        evidence = record_work_process_candidate_feedback(
            session_id,
            file_path,
            action="rejected",
            note=str(body.get("note") or "rejected by user").strip(),
        )
        detail = get_session_detail(session_id)
        return _json_response({"ok": True, "evidence": evidence, **detail})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] reject candidate failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)


async def handle_work_ledger_end(request: Any) -> Any:
    """POST /api/v1/work-ledger/end"""

    body = await _json_body(request)
    try:
        from l3_node.work_ledger import end_session

        result = end_session(
            str(body.get("session_id") or "").strip() or None,
            generate_outputs=bool(body.get("generate_outputs", True)),
        )
        return _json_response({"ok": True, **result})
    except Exception as e:
        logger.exception("[WorkLedger HTTP] end failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=400)

def register_work_ledger_routes(app: Any) -> None:
    app.router.add_get("/api/v1/work-ledger/status", handle_work_ledger_status)
    app.router.add_get("/api/v1/work-ledger/sessions", handle_work_ledger_sessions)
    app.router.add_get("/api/v1/work-ledger/recall-index", handle_work_ledger_recall_index)
    app.router.add_post("/api/v1/work-ledger/recall", handle_work_ledger_recall)
    app.router.add_post("/api/v1/work-ledger/weekly-report", handle_work_ledger_weekly_report)
    app.router.add_post("/api/v1/work-ledger/briefing", handle_work_ledger_instant_brief)
    app.router.add_post("/api/v1/work-ledger/codex-consult", handle_work_ledger_codex_consult)
    app.router.add_get("/api/v1/work-ledger/codex-invocations", handle_work_ledger_codex_invocations)
    app.router.add_post("/api/v1/work-ledger/codex-cancel", handle_work_ledger_codex_cancel)
    app.router.add_get("/api/v1/work-ledger/sessions/{session_id}", handle_work_ledger_session_detail)
    app.router.add_get("/api/v1/work-ledger/sessions/{session_id}/outputs/{output_key}", handle_work_ledger_output_text)
    app.router.add_post("/api/v1/work-ledger/start", handle_work_ledger_start)
    app.router.add_post("/api/v1/work-ledger/collect", handle_work_ledger_collect)
    app.router.add_post("/api/v1/work-ledger/note", handle_work_ledger_note)
    app.router.add_post("/api/v1/work-ledger/ai-trace", handle_work_ledger_ai_trace)
    app.router.add_post("/api/v1/work-ledger/import-process", handle_work_ledger_import_process)
    app.router.add_post("/api/v1/work-ledger/end-day-preview", handle_work_ledger_end_day_preview)
    app.router.add_post("/api/v1/work-ledger/process-candidates", handle_work_ledger_process_candidates)
    app.router.add_post("/api/v1/work-ledger/candidate-quality", handle_work_ledger_candidate_quality)
    app.router.add_get("/api/v1/work-ledger/reliability", handle_work_ledger_reliability)
    app.router.add_post("/api/v1/work-ledger/checkpoint", handle_work_ledger_checkpoint)
    app.router.add_get("/api/v1/work-ledger/sessions/{session_id}/timeline", handle_work_ledger_timeline)
    app.router.add_get("/api/v1/work-ledger/sessions/{session_id}/process-inbox", handle_work_ledger_process_inbox)
    app.router.add_post("/api/v1/work-ledger/process-inbox/refresh", handle_work_ledger_process_inbox_refresh)
    app.router.add_post("/api/v1/work-ledger/process-inbox/review", handle_work_ledger_process_inbox_review)
    app.router.add_get(
        "/api/v1/work-ledger/sessions/{session_id}/project-facts",
        handle_work_ledger_project_facts,
    )
    app.router.add_post(
        "/api/v1/work-ledger/project-facts/review",
        handle_work_ledger_project_fact_review,
    )
    app.router.add_post(
        "/api/v1/work-ledger/project-facts/update",
        handle_work_ledger_project_fact_update,
    )
    app.router.add_get(
        "/api/v1/work-ledger/sessions/{session_id}/project-outcomes",
        handle_work_ledger_project_outcomes,
    )
    app.router.add_post(
        "/api/v1/work-ledger/methodology/review",
        handle_work_ledger_methodology_review,
    )
    app.router.add_get(
        "/api/v1/work-ledger/sessions/{session_id}/value-chain",
        handle_work_ledger_value_chain,
    )
    app.router.add_post(
        "/api/v1/work-ledger/value-events",
        handle_work_ledger_value_event,
    )
    app.router.add_post(
        "/api/v1/work-ledger/value-chain/diagnostics/run",
        handle_work_ledger_value_diagnostics_run,
    )
    app.router.add_get(
        "/api/v1/work-ledger/value-chain/diagnostics/logs",
        handle_work_ledger_value_diagnostics_logs,
    )
    app.router.add_get("/api/v1/work-ledger/sessions/{session_id}/source-status", handle_work_ledger_source_status)
    app.router.add_post("/api/v1/work-ledger/source-configure", handle_work_ledger_source_configure)
    app.router.add_post("/api/v1/work-ledger/source-control", handle_work_ledger_source_control)
    app.router.add_post("/api/v1/work-ledger/source-revoke", handle_work_ledger_source_revoke)
    app.router.add_post("/api/v1/work-ledger/end-day-finalize", handle_work_ledger_end_day_finalize)
    app.router.add_post("/api/v1/work-ledger/generate", handle_work_ledger_generate)
    app.router.add_post("/api/v1/work-ledger/adopt-output", handle_work_ledger_adopt_output)
    app.router.add_post("/api/v1/work-ledger/adopt-candidate", handle_work_ledger_adopt_candidate)
    app.router.add_post("/api/v1/work-ledger/reject-candidate", handle_work_ledger_reject_candidate)
    app.router.add_post("/api/v1/work-ledger/end", handle_work_ledger_end)
    app.on_startup.append(_start_work_ledger_checkpoint_loop)
    app.on_cleanup.append(_stop_work_ledger_checkpoint_loop)
