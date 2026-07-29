"""Work Ledger MVP for Jachin.

This module records durable, evidence-backed work sessions.  It deliberately
does not ask an LLM what happened; it collects local facts first and only then
builds human-readable outputs from those facts.
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from l3_node.paths import get_app_root

logger = logging.getLogger(__name__)
WORK_LEDGER_TIMEZONE = timezone(timedelta(hours=8))

EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "target",
    "dist",
    "dist_jachin_desktop",
    ".next",
    "build",
    "coverage",
}

TEXT_SNIPPET_EXTENSIONS = {
    ".bat",
    ".cmd",
    ".css",
    ".env",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mdc",
    ".ps1",
    ".py",
    ".rs",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".yaml",
    ".yml",
}

RISK_KEYWORDS = (
    "TODO",
    "FIXME",
    "HACK",
    "BUG",
    "panic",
    "error",
    "failed",
    "失败",
    "异常",
    "未实现",
    "待处理",
    "临时",
)

AI_TRACE_BUCKETS = {
    "goals": ("目标", "任务", "需求", "要做", "实现", "plan", "todo", "goal"),
    "actions": ("修改", "新增", "实现", "接入", "修复", "执行", "运行", "changed", "added", "fixed", "implemented"),
    "failures": ("失败", "报错", "异常", "错误", "不通过", "阻塞", "failed", "error", "exception", "blocked"),
    "decisions": ("结论", "决定", "确认", "采用", "放弃", "因此", "final", "decision", "decide", "decided", "confirmed"),
    "next_steps": ("下一步", "继续", "待做", "建议", "后续", "next", "follow-up"),
}


OUTPUT_TEXT_KEYS = {
    "work_review",
    "work_report_summary",
    "context_pack",
    "daily_report",
    "codex_continuation_prompt",
    "enhanced_daily_report",
    "enhanced_continuation_prompt",
    "lark_brief",
    "team_lark_brief",
    "weekly_report",
    "performance_entries",
    "methodology_candidates",
    "multi_day_weekly_report",
    "enhanced_multi_day_weekly_report",
    "llm_quality_report",
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _day() -> str:
    return time.strftime("%Y%m%d")


def _now_datetime() -> datetime:
    return datetime.now(WORK_LEDGER_TIMEZONE)


def _ms_to_iso(ms: int | float | None) -> str:
    try:
        value = float(ms or 0)
    except Exception:
        value = 0
    if value <= 0:
        return ""
    return datetime.fromtimestamp(
        value / 1000,
        tz=WORK_LEDGER_TIMEZONE,
    ).isoformat()


def _local_day_from_ms(ms: int | float | None) -> str:
    value = _ms_to_iso(ms)
    return value[:10] if value else ""


def work_ledger_home() -> Path:
    raw = (os.environ.get("JACHIN_WORK_LEDGER_HOME") or "").strip()
    if raw:
        root = Path(raw).expanduser()
    else:
        root = Path.home() / ".jachin" / "work_ledger"
    root.mkdir(parents=True, exist_ok=True)
    (root / "sessions").mkdir(parents=True, exist_ok=True)
    (root / "outputs").mkdir(parents=True, exist_ok=True)
    return root


def _sessions_dir() -> Path:
    path = work_ledger_home() / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _outputs_dir() -> Path:
    path = work_ledger_home() / "outputs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _index_path() -> Path:
    return work_ledger_home() / "sessions.json"


def _session_dir(session_id: str) -> Path:
    safe = _safe_id(session_id)
    path = _sessions_dir() / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


def _session_path(session_id: str) -> Path:
    return _session_dir(session_id) / "session.json"


def _evidence_path(session_id: str) -> Path:
    return _session_dir(session_id) / "evidence.jsonl"


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())[:120] or "unknown"


def _short_id(prefix: str) -> str:
    return f"{prefix}_{_day()}_{uuid.uuid4().hex[:10]}"


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default
    return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _load_index() -> list[dict[str, Any]]:
    raw = _read_json(_index_path(), [])
    return raw if isinstance(raw, list) else []


def _save_index(rows: list[dict[str, Any]]) -> None:
    rows = sorted(rows, key=lambda item: int(item.get("updated_at_ms") or 0), reverse=True)
    _write_json(_index_path(), rows[:500])


def _upsert_index(session: dict[str, Any]) -> None:
    rows = [row for row in _load_index() if row.get("session_id") != session.get("session_id")]
    rows.insert(0, _session_index_row(session))
    _save_index(rows)


def _session_index_row(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "session_id": session.get("session_id"),
        "title": session.get("title"),
        "project_name": session.get("project_name"),
        "project_path": session.get("project_path"),
        "status": session.get("status"),
        "start_time": session.get("start_time"),
        "end_time": session.get("end_time"),
        "created_at_ms": session.get("created_at_ms"),
        "updated_at_ms": session.get("updated_at_ms"),
        "evidence_count": session.get("evidence_count", 0),
        "output_paths": session.get("output_paths", {}),
    }


def _load_session(session_id: str) -> dict[str, Any]:
    data = _read_json(_session_path(session_id), {})
    if not isinstance(data, dict) or not data.get("session_id"):
        raise ValueError(f"work session not found: {session_id}")
    return data


def _save_session(session: dict[str, Any]) -> dict[str, Any]:
    session["updated_at_ms"] = _now_ms()
    _write_json(_session_path(str(session["session_id"])), session)
    _upsert_index(session)
    return session


def _active_state_path() -> Path:
    return work_ledger_home() / "active_session.json"


def _set_active_session(session_id: str | None) -> None:
    path = _active_state_path()
    if session_id:
        _write_json(path, {"session_id": session_id, "updated_at_ms": _now_ms()})
    elif path.exists():
        path.unlink()


def get_active_session() -> dict[str, Any] | None:
    state = _read_json(_active_state_path(), {})
    session_id = str(state.get("session_id") or "").strip() if isinstance(state, dict) else ""
    if not session_id:
        return None
    try:
        session = _load_session(session_id)
    except Exception:
        _set_active_session(None)
        return None
    return session if session.get("status") == "active" else None


def list_sessions(limit: int = 50) -> list[dict[str, Any]]:
    return _load_index()[: max(1, min(int(limit or 50), 200))]


def _recent_window_cutoff_ms(days: int, *, calendar_window: bool) -> int:
    days = max(1, min(int(days or 1), 365))
    if calendar_window:
        local_now = _now_datetime()
        window_start = (local_now - timedelta(days=days - 1)).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        return int(window_start.timestamp() * 1000)
    return _now_ms() - days * 24 * 60 * 60 * 1000


def list_recent_sessions(
    days: int = 7,
    *,
    limit: int = 200,
    calendar_window: bool = False,
) -> list[dict[str, Any]]:
    days = max(1, min(int(days or 7), 365))
    cutoff_ms = _recent_window_cutoff_ms(
        days,
        calendar_window=calendar_window,
    )
    rows = []
    for row in _load_index():
        updated = int(row.get("updated_at_ms") or row.get("created_at_ms") or 0)
        if updated >= cutoff_ms:
            rows.append(row)
    return rows[: max(1, min(int(limit or 200), 500))]


def build_work_ledger_reliability(days: int = 7, *, reference_ms: int | None = None) -> dict[str, Any]:
    """Measure whether Work Ledger produces durable, reusable work assets each day."""

    window_days = max(1, min(int(days or 7), 90))
    now_ms = int(reference_ms or _now_ms())
    cutoff_ms = now_ms - window_days * 24 * 60 * 60 * 1000
    rows = [
        row
        for row in _load_index()
        if int(row.get("updated_at_ms") or row.get("created_at_ms") or 0) >= cutoff_ms
    ][:500]
    local_now = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).astimezone()
    day_keys = [
        datetime.fromtimestamp((now_ms - offset * 24 * 60 * 60 * 1000) / 1000, tz=timezone.utc).astimezone().strftime("%Y-%m-%d")
        for offset in reversed(range(window_days))
    ]
    daily: dict[str, dict[str, Any]] = {
        day: {
            "date": day,
            "session_count": 0,
            "closed_count": 0,
            "valid_evidence_count": 0,
            "sessions_with_process_evidence": 0,
            "output_ready_count": 0,
            "output_adoption_count": 0,
            "candidate_accepted": 0,
            "candidate_rejected": 0,
            "candidate_blocked": 0,
            "continuation_opportunities": 0,
            "continuation_hits": 0,
            "health_score": 0,
            "status": "idle",
        }
        for day in day_keys
    }
    process_sources = {
        "manual_note",
        "ai_work_trace",
        "git_snapshot",
        "file_scan",
        "file_content_snippets",
        "work_checkpoint",
        "work_process_candidate_feedback",
    }
    reminders: list[dict[str, Any]] = []
    session_summaries: list[dict[str, Any]] = []
    total_output_ready = 0
    total_output_adoptions = 0
    total_valid_evidence = 0
    continuation_opportunities = 0
    continuation_hits = 0
    candidate_accepted = 0
    candidate_rejected = 0
    candidate_blocked = 0

    for row in rows:
        sid = str(row.get("session_id") or "")
        if not sid:
            continue
        try:
            session = _load_session(sid)
        except Exception:
            session = row
        created_ms = int(session.get("created_at_ms") or row.get("created_at_ms") or 0)
        if created_ms <= 0:
            created_ms = int(session.get("updated_at_ms") or row.get("updated_at_ms") or now_ms)
        day = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc).astimezone().strftime("%Y-%m-%d")
        if day not in daily:
            continue
        evidence = load_evidence(sid, 2000)
        valid_evidence = [ev for ev in evidence if str(ev.get("source") or "") in process_sources]
        output_adoptions = [ev for ev in evidence if ev.get("source") == "work_output_adoption"]
        feedback = [ev for ev in evidence if ev.get("source") == "work_process_candidate_feedback"]
        continuation = [ev for ev in evidence if ev.get("source") == "work_continuation_context"]
        output_paths = session.get("output_paths") if isinstance(session.get("output_paths"), dict) else {}
        required_outputs = ("daily_report", "context_pack", "codex_continuation_prompt", "lark_brief")
        ready_outputs = [key for key in required_outputs if _work_output_path_ready(output_paths.get(key))]
        output_ready = len(ready_outputs) == len(required_outputs)
        closed = str(session.get("status") or "") == "closed"

        accepted = sum(1 for ev in feedback if str((ev.get("payload") or {}).get("action") or "") == "accepted")
        rejected = sum(1 for ev in feedback if str((ev.get("payload") or {}).get("action") or "") == "rejected")
        blocked = sum(1 for ev in feedback if str((ev.get("payload") or {}).get("action") or "") == "blocked")
        opportunities = len(continuation)
        hits = sum(1 for ev in continuation if bool((ev.get("payload") or {}).get("hit")))

        target = daily[day]
        target["session_count"] += 1
        target["closed_count"] += 1 if closed else 0
        target["valid_evidence_count"] += len(valid_evidence)
        target["sessions_with_process_evidence"] += 1 if valid_evidence else 0
        target["output_ready_count"] += 1 if output_ready else 0
        target["output_adoption_count"] += len(output_adoptions)
        target["candidate_accepted"] += accepted
        target["candidate_rejected"] += rejected
        target["candidate_blocked"] += blocked
        target["continuation_opportunities"] += opportunities
        target["continuation_hits"] += hits

        total_valid_evidence += len(valid_evidence)
        total_output_ready += 1 if output_ready else 0
        total_output_adoptions += len(output_adoptions)
        continuation_opportunities += opportunities
        continuation_hits += hits
        candidate_accepted += accepted
        candidate_rejected += rejected
        candidate_blocked += blocked

        if valid_evidence and not output_ready:
            reminders.append(
                {
                    "kind": "recorded_without_assets",
                    "severity": "warning",
                    "session_id": sid,
                    "title": session.get("title"),
                    "message": "任务留下了过程证据，但尚未形成完整日报、上下文包和续写 Prompt。",
                }
            )
        elif output_ready and not output_adoptions:
            reminders.append(
                {
                    "kind": "outputs_not_adopted",
                    "severity": "info",
                    "session_id": sid,
                    "title": session.get("title"),
                    "message": "任务已生成工作资产，但还没有任何输出被用户采纳回流。",
                }
            )
        if not closed and now_ms - int(session.get("updated_at_ms") or created_ms) > 8 * 60 * 60 * 1000:
            reminders.append(
                {
                    "kind": "stale_active_session",
                    "severity": "warning",
                    "session_id": sid,
                    "title": session.get("title"),
                    "message": "任务已超过 8 小时仍未收工，可能缺少结束证据。",
                }
            )
        session_summaries.append(
            {
                "session_id": sid,
                "date": day,
                "title": session.get("title"),
                "project_name": session.get("project_name"),
                "status": session.get("status"),
                "valid_evidence_count": len(valid_evidence),
                "output_ready": output_ready,
                "ready_outputs": ready_outputs,
                "output_adoption_count": len(output_adoptions),
                "continuation_hit": bool(hits),
            }
        )

    daily_rows: list[dict[str, Any]] = []
    for day in day_keys:
        item = daily[day]
        sessions = int(item["session_count"] or 0)
        if sessions:
            score = 20.0
            if int(item["valid_evidence_count"] or 0) > 0:
                score += 25.0
            score += 20.0 * int(item["closed_count"] or 0) / sessions
            score += 25.0 * int(item["output_ready_count"] or 0) / sessions
            if int(item["output_adoption_count"] or 0) > 0 or int(item["candidate_accepted"] or 0) > 0:
                score += 10.0
            item["health_score"] = round(min(100.0, score), 1)
            item["status"] = "healthy" if score >= 80 else "partial" if score >= 50 else "attention"
        daily_rows.append(item)

    active_days = sum(1 for item in daily_rows if int(item["session_count"] or 0) > 0)
    current_streak = 0
    for item in reversed(daily_rows):
        if int(item["session_count"] or 0) <= 0:
            break
        current_streak += 1
    total_sessions = len(session_summaries)
    candidate_total = candidate_accepted + candidate_rejected + candidate_blocked
    overall_score = round(sum(float(item["health_score"] or 0) for item in daily_rows) / max(1, window_days), 1)
    try:
        value_window = build_work_ledger_recall_index(window_days).get(
            "value_summary",
            {},
        )
    except Exception:
        value_window = {}
    metrics = {
        "active_days": active_days,
        "current_streak": current_streak,
        "session_count": total_sessions,
        "completion_rate": round(sum(1 for row in session_summaries if row["status"] == "closed") / max(1, total_sessions), 3),
        "asset_formation_rate": round(total_output_ready / max(1, total_sessions), 3),
        "output_adoption_rate": round(sum(1 for row in session_summaries if row["output_adoption_count"] > 0) / max(1, total_output_ready), 3),
        "candidate_adoption_rate": round(candidate_accepted / max(1, candidate_total), 3),
        "continuation_hit_rate": round(continuation_hits / max(1, continuation_opportunities), 3),
        "outcome_delivery_rate": round(
            int(value_window.get("delivered_outcome_count") or 0)
            / max(1, int(value_window.get("active_outcome_count") or 0)),
            3,
        ),
        "outcome_adoption_rate": round(
            int(value_window.get("adopted_outcome_count") or 0)
            / max(1, int(value_window.get("active_outcome_count") or 0)),
            3,
        ),
        "outcome_impact_rate": round(
            int(value_window.get("impact_outcome_count") or 0)
            / max(1, int(value_window.get("active_outcome_count") or 0)),
            3,
        ),
        "continuation_use_rate": float(
            value_window.get("continuation_use_rate") or 0
        ),
        "methodology_reuse_success_rate": float(
            value_window.get("methodology_reuse_success_rate") or 0
        ),
        "average_valid_evidence": round(total_valid_evidence / max(1, total_sessions), 2),
        "overall_score": overall_score,
    }
    recommendations: list[str] = []
    if active_days < min(5, window_days):
        recommendations.append("连续使用天数偏少：每天至少开始一次工作任务，收工时生成上下文包。")
    if metrics["asset_formation_rate"] < 0.8:
        recommendations.append("工作资产形成率不足：优先处理“有记录但没有日报/Context Pack”的任务。")
    if continuation_opportunities and metrics["continuation_hit_rate"] < 0.8:
        recommendations.append("跨日续接命中率不足：检查上一任务是否生成 Context Pack 和续写 Prompt。")
    if total_output_ready and metrics["output_adoption_rate"] < 0.5:
        recommendations.append("输出采纳率偏低：确认日报、团队简报或方法论候选是否值得回流。")
    if int(value_window.get("active_outcome_count") or 0) and metrics[
        "outcome_adoption_rate"
    ] < 0.4:
        recommendations.append("成果实际采用率偏低：区分“做完了”和“真正用于工作”，补充交付或采用反馈。")
    if int(value_window.get("continuation_available_count") or 0) and metrics[
        "continuation_use_rate"
    ] < 0.7:
        recommendations.append("续作资产存在但使用不足：次日应从 Context Pack 或续写 Prompt 启动，而不是重新整理上下文。")
    if not recommendations:
        recommendations.append("七天闭环健康：继续保持每日开始、记录、收工和次日续接。")
    result = {
        "schema_version": 1,
        "window_days": window_days,
        "generated_at": local_now.isoformat(),
        "metrics": metrics,
        "daily": daily_rows,
        "candidate_feedback": {
            "accepted": candidate_accepted,
            "rejected": candidate_rejected,
            "blocked": candidate_blocked,
            "total": candidate_total,
        },
        "continuation": {
            "opportunities": continuation_opportunities,
            "hits": continuation_hits,
            "hit_rate": metrics["continuation_hit_rate"],
            "actual_use_count": int(
                value_window.get("continuation_used_count") or 0
            ),
            "actual_use_rate": metrics["continuation_use_rate"],
        },
        "value_summary": value_window,
        "reminders": reminders[:50],
        "recommendations": recommendations,
        "sessions": session_summaries,
    }
    _record_kernel_event("work_ledger_reliability_built", "work_ledger", result)
    return result


def write_work_ledger_reliability_report(days: int = 7) -> dict[str, Any]:
    report = build_work_ledger_reliability(days)
    out_dir = _outputs_dir() / "reliability"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"work_ledger_reliability_{int(report['window_days'])}d.json"
    _write_json(path, report)
    report["path"] = str(path)
    return report


def _work_output_path_ready(value: Any) -> bool:
    path_text = str(value or "").strip()
    if not path_text:
        return False
    try:
        return Path(path_text).expanduser().is_file()
    except Exception:
        return False


def build_work_ledger_recall_index(
    days: int = 7,
    *,
    limit: int = 200,
    calendar_window: bool = False,
) -> dict[str, Any]:
    cutoff_ms = _recent_window_cutoff_ms(
        days,
        calendar_window=calendar_window,
    )
    sessions = list_recent_sessions(
        days,
        limit=limit,
        calendar_window=calendar_window,
    )
    project_counts: dict[str, int] = {}
    project_paths: set[str] = set()
    adopted_outputs: list[dict[str, Any]] = []
    methodology_candidates: list[dict[str, Any]] = []
    recent_notes: list[dict[str, Any]] = []
    recent_ai_signals: list[dict[str, Any]] = []
    adopted_process_candidates: list[dict[str, Any]] = []
    rejected_process_candidates: list[dict[str, Any]] = []
    recent_changed_files: list[dict[str, Any]] = []
    recent_codex_consultations: list[dict[str, Any]] = []
    session_evidence_digests: list[dict[str, Any]] = []
    evidence_source_counts: dict[str, int] = {}
    project_names_by_path: dict[str, str] = {}
    activity_dates: set[str] = set()
    for row in sessions:
        sid = str(row.get("session_id") or "")
        if not sid:
            continue
        project = str(row.get("project_name") or row.get("project_path") or "unknown")
        project_counts[project] = project_counts.get(project, 0) + 1
        project_path = str(row.get("project_path") or "").strip()
        if project_path:
            project_paths.add(project_path)
            project_names_by_path[project_path] = project
        for field in ("created_at_ms", "updated_at_ms"):
            day_key = _local_day_from_ms(row.get(field))
            if day_key:
                activity_dates.add(day_key)
        evidence = load_evidence(sid, 1000)
        evidence_in_window = [
            ev
            for ev in evidence
            if not int(ev.get("collected_at_ms") or 0)
            or int(ev.get("collected_at_ms") or 0) >= cutoff_ms
        ]
        for ev in evidence_in_window:
            day_key = _local_day_from_ms(ev.get("collected_at_ms"))
            if day_key:
                activity_dates.add(day_key)
        session_evidence_digests.append(
            _build_session_brief_evidence_digest(row, evidence_in_window)
        )
        for ev in evidence_in_window:
            source = str(ev.get("source") or "unknown")
            evidence_source_counts[source] = evidence_source_counts.get(source, 0) + 1
            payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
            if source in {"git_snapshot", "work_checkpoint"}:
                for changed in payload.get("changed_files") or []:
                    if not isinstance(changed, dict):
                        continue
                    path = str(changed.get("path") or "").strip()
                    if not path:
                        continue
                    recent_changed_files.append(
                        {
                            "session_id": sid,
                            "project_name": row.get("project_name"),
                            "path": path,
                            "status": str(changed.get("status") or "modified"),
                            "collected_at": ev.get("collected_at"),
                        }
                    )
            if source == "work_output_adoption":
                adopted_outputs.append(
                    {
                        "session_id": sid,
                        "title": row.get("title"),
                        "project_name": row.get("project_name"),
                        "output_key": payload.get("output_key"),
                        "path": payload.get("output_path"),
                        "summary": ev.get("summary"),
                        "note": payload.get("note"),
                        "text_preview": str(payload.get("text") or "")[:600],
                        "collected_at": ev.get("collected_at"),
                        "trust_level": ev.get("trust_level"),
                    }
                )
            elif source == "manual_note":
                recent_notes.append(
                    {
                        "session_id": sid,
                        "title": row.get("title"),
                        "project_name": row.get("project_name"),
                        "summary": ev.get("summary"),
                        "collected_at": ev.get("collected_at"),
                    }
                )
            elif source == "ai_work_trace":
                payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
                analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
                buckets = analysis.get("buckets") if isinstance(analysis.get("buckets"), dict) else {}
                for key in ("actions", "decisions", "failures", "next_steps"):
                    for value in (buckets.get(key) or [])[:3]:
                        recent_ai_signals.append(
                            {
                                "session_id": sid,
                                "title": row.get("title"),
                                "project_name": row.get("project_name"),
                                "kind": key,
                                "text": value,
                                "collected_at": ev.get("collected_at"),
                            }
                        )
            elif source == "work_process_candidate_feedback":
                payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
                action = str(payload.get("action") or "").strip()
                item = {
                    "session_id": sid,
                    "title": row.get("title"),
                    "project_name": row.get("project_name"),
                    "action": action,
                    "path": payload.get("file_path"),
                    "summary": ev.get("summary"),
                    "note": payload.get("note"),
                    "text_preview": str(payload.get("text_preview") or "")[:600],
                    "collected_at": ev.get("collected_at"),
                    "trust_level": ev.get("trust_level"),
                }
                if action == "accepted":
                    adopted_process_candidates.append(item)
                elif action in {"rejected", "blocked"}:
                    rejected_process_candidates.append(item)
            elif source == "codex_work_plan_consultation":
                recent_codex_consultations.append(
                    {
                        "session_id": sid,
                        "title": row.get("title"),
                        "project_name": row.get("project_name"),
                        "project_path": row.get("project_path"),
                        "summary": ev.get("summary"),
                        "ok": bool(payload.get("ok")),
                        "prompt_hash": payload.get("prompt_hash"),
                        "conversation_name": payload.get("conversation_name"),
                        "answer": str(payload.get("answer") or "")[:8000],
                        "answer_source": payload.get("answer_source"),
                        "answer_validation": payload.get("answer_validation"),
                        "claim_fusion": payload.get("claim_fusion") or {},
                        "recovery": payload.get("recovery") or {},
                        "recovery_terminal": payload.get("recovery_terminal") or {},
                        "tool_evidence_path": payload.get("tool_evidence_path"),
                        "evidence_panel_path": payload.get("evidence_panel_path"),
                        "report_path": payload.get("report_path"),
                        "collected_at": ev.get("collected_at"),
                        "trust_level": ev.get("trust_level"),
                    }
                )
    for item in adopted_outputs:
        if item.get("output_key") == "methodology_candidates":
            methodology_candidates.append(item)
    deduplicated_changed_files: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in recent_changed_files:
        key = (
            str(item.get("project_name") or ""),
            str(item.get("path") or "").replace("\\", "/").lower(),
            str(item.get("status") or ""),
        )
        current = deduplicated_changed_files.get(key)
        if current is None or str(item.get("collected_at") or "") >= str(
            current.get("collected_at") or ""
        ):
            deduplicated_changed_files[key] = item
    recent_changed_files = list(deduplicated_changed_files.values())

    git_activity: list[dict[str, Any]] = []
    recent_project_files: list[dict[str, Any]] = []
    for project_path in sorted(project_paths):
        project_name = project_names_by_path.get(project_path) or Path(
            project_path
        ).name
        activity = collect_git_window_activity(
            Path(project_path),
            since_ms=cutoff_ms,
            max_commits=120,
        )
        activity["project_name"] = project_name
        git_activity.append(activity)
        for commit in activity.get("commits") or []:
            authored_day = str(commit.get("authored_at") or "")[:10]
            if authored_day:
                activity_dates.add(authored_day)
        file_activity = collect_recent_files(
            Path(project_path),
            since_ms=cutoff_ms,
            max_files=300,
        )
        for item in file_activity.get("recent_files") or []:
            if not isinstance(item, dict):
                continue
            recent_project_files.append(
                {
                    **item,
                    "project_name": project_name,
                    "project_path": project_path,
                }
            )
            day_key = _local_day_from_ms(item.get("mtime_ms"))
            if day_key:
                activity_dates.add(day_key)
    session_ids = {
        str(row.get("session_id") or "")
        for row in sessions
        if str(row.get("session_id") or "")
    }
    verified_outcomes_by_id: dict[str, dict[str, Any]] = {}
    graph_methodologies_by_id: dict[str, dict[str, Any]] = {}
    valued_outcomes_by_id: dict[str, dict[str, Any]] = {}
    value_events_in_window: list[dict[str, Any]] = []
    try:
        from l3_node.work_ledger_outcomes import get_project_outcome_graph

        for project_path in sorted(project_paths):
            graph = get_project_outcome_graph(project_path)
            for outcome in graph.get("outcomes") or []:
                if (
                    isinstance(outcome, dict)
                    and outcome.get("status") == "active"
                    and outcome.get("completion_session_id") in session_ids
                ):
                    verified_outcomes_by_id[str(outcome.get("outcome_id") or "")] = {
                        **outcome,
                        "project_path": project_path,
                    }
            for candidate in graph.get("methodology_candidates") or []:
                if not isinstance(candidate, dict):
                    continue
                if session_ids.intersection(candidate.get("source_session_ids") or []):
                    graph_methodologies_by_id[
                        str(candidate.get("candidate_id") or "")
                    ] = {
                        **candidate,
                        "project_path": project_path,
                    }
            try:
                from l3_node.work_ledger_value import get_project_value_chain

                value_chain = get_project_value_chain(project_path)
                project_value_events = [
                    event
                    for event in value_chain.get("events") or []
                    if event.get("session_id") in session_ids
                    or event.get("related_session_id") in session_ids
                ]
                value_events_in_window.extend(project_value_events)
                relevant_outcome_ids = {
                    str(outcome_id or "")
                    for event in project_value_events
                    for outcome_id in (event.get("outcome_ids") or [])
                    if str(outcome_id or "")
                }
                for outcome in value_chain.get("outcome_values") or []:
                    if not isinstance(outcome, dict):
                        continue
                    outcome_id = str(outcome.get("outcome_id") or "")
                    if (
                        outcome.get("completion_session_id") in session_ids
                        or outcome_id in relevant_outcome_ids
                    ):
                        valued_outcomes_by_id[outcome_id] = {
                            **outcome,
                            "project_path": project_path,
                        }
            except Exception:
                pass
    except Exception:
        pass
    valued_outcomes = sorted(
        valued_outcomes_by_id.values(),
        key=lambda row: (
            float(row.get("value_score") or 0),
            str(row.get("latest_value_at") or row.get("completed_at") or ""),
        ),
        reverse=True,
    )
    active_valued_outcomes = [
        row for row in valued_outcomes if row.get("status") == "active"
    ]
    continuation_available = sum(
        1
        for event in value_events_in_window
        if event.get("event_type") == "continuation_available"
    )
    continuation_used = sum(
        1
        for event in value_events_in_window
        if event.get("event_type") == "continuation_used"
    )
    methodology_reuse_success = sum(
        1
        for event in value_events_in_window
        if event.get("event_type") == "methodology_reused"
    )
    methodology_reuse_failed = sum(
        1
        for event in value_events_in_window
        if event.get("event_type") == "methodology_reuse_failed"
    )
    return {
        "schema_version": 1,
        "window_days": days,
        "window_mode": "calendar_days" if calendar_window else "rolling_days",
        "window_start": _ms_to_iso(cutoff_ms),
        "window_end": _now_datetime().isoformat(),
        "generated_at": _now_datetime().isoformat(),
        "session_count": len(sessions),
        "activity_dates": sorted(activity_dates),
        "activity_day_count": len(activity_dates),
        "git_commit_count": sum(
            int(item.get("commit_count") or 0) for item in git_activity
        ),
        "git_activity": git_activity,
        "recent_project_files": sorted(
            recent_project_files,
            key=lambda item: int(item.get("mtime_ms") or 0),
            reverse=True,
        )[:300],
        "sessions": sessions,
        "project_counts": project_counts,
        "evidence_source_counts": evidence_source_counts,
        "recent_changed_files": recent_changed_files[-300:],
        "session_evidence_digests": session_evidence_digests[:80],
        "adopted_outputs": adopted_outputs[-100:],
        "adopted_process_candidates": adopted_process_candidates[-100:],
        "rejected_process_candidates": rejected_process_candidates[-100:],
        "methodology_candidates": methodology_candidates[-60:],
        "verified_outcomes": list(verified_outcomes_by_id.values())[-200:],
        "valued_outcomes": active_valued_outcomes[:200],
        "value_events": value_events_in_window[-300:],
        "value_summary": {
            "active_outcome_count": len(active_valued_outcomes),
            "delivered_outcome_count": sum(
                1
                for row in active_valued_outcomes
                if row.get("value_stage") in {"delivered", "adopted", "impact"}
            ),
            "adopted_outcome_count": sum(
                1
                for row in active_valued_outcomes
                if row.get("value_stage") in {"adopted", "impact"}
            ),
            "impact_outcome_count": sum(
                1
                for row in active_valued_outcomes
                if row.get("value_stage") == "impact"
            ),
            "continuation_available_count": continuation_available,
            "continuation_used_count": continuation_used,
            "continuation_use_rate": round(
                continuation_used / max(1, continuation_available),
                3,
            ),
            "methodology_reuse_attempt_count": (
                methodology_reuse_success + methodology_reuse_failed
            ),
            "methodology_reuse_success_count": methodology_reuse_success,
            "methodology_reuse_success_rate": round(
                methodology_reuse_success
                / max(1, methodology_reuse_success + methodology_reuse_failed),
                3,
            ),
        },
        "graph_methodology_candidates": list(
            graph_methodologies_by_id.values()
        )[-100:],
        "recent_notes": recent_notes[-100:],
        "recent_ai_signals": recent_ai_signals[-120:],
        "recent_codex_consultations": recent_codex_consultations[-60:],
    }


def write_work_ledger_recall_index(days: int = 7) -> dict[str, Any]:
    index = build_work_ledger_recall_index(days)
    out_dir = _outputs_dir() / "recall"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"work_ledger_recall_{int(days)}d.json"
    _write_json(path, index)
    index["path"] = str(path)
    _record_kernel_event("work_ledger_recall_index_written", "work_ledger", {"days": days, "path": str(path)})
    return index


def recall_work_ledger(query: str, *, days: int = 14, limit: int = 8) -> dict[str, Any]:
    clean = str(query or "").strip()
    if not clean:
        raise ValueError("query is required")
    index = build_work_ledger_recall_index(days)
    query_terms = _tokenize_for_recall(clean)
    candidates: list[dict[str, Any]] = []
    for row in index.get("sessions", []):
        text = " ".join(
            str(row.get(key) or "")
            for key in ("title", "project_name", "project_path", "status", "user_goal")
        )
        candidates.append(
            {
                "kind": "session",
                "session_id": row.get("session_id"),
                "title": row.get("title"),
                "project_name": row.get("project_name"),
                "text": text,
                "path": row.get("project_path"),
                "trust_level": "system_observed",
                "updated_at_ms": row.get("updated_at_ms"),
            }
        )
    for bucket_name, kind, trust in [
        ("adopted_outputs", "adopted_output", "user_confirmed"),
        ("adopted_process_candidates", "adopted_process_candidate", "user_confirmed"),
        ("methodology_candidates", "methodology_candidate", "user_confirmed"),
        ("recent_notes", "manual_note", "user_confirmed"),
        ("recent_ai_signals", "ai_signal", "system_observed"),
    ]:
        for item in index.get(bucket_name, []):
            text = " ".join(str(item.get(key) or "") for key in ("title", "project_name", "summary", "note", "text", "text_preview"))
            candidates.append(
                {
                    "kind": kind,
                    "session_id": item.get("session_id"),
                    "title": item.get("title"),
                    "project_name": item.get("project_name"),
                    "text": text[:1000],
                    "path": item.get("path"),
                    "collected_at": item.get("collected_at"),
                    "trust_level": trust,
                }
            )
    recall_outcomes = (
        index.get("valued_outcomes")
        if isinstance(index.get("valued_outcomes"), list)
        and index.get("valued_outcomes")
        else index.get("verified_outcomes", [])
    )
    for outcome in recall_outcomes:
        if not isinstance(outcome, dict):
            continue
        candidates.append(
            {
                "kind": "verified_outcome",
                "session_id": outcome.get("completion_session_id"),
                "title": outcome.get("summary"),
                "project_name": Path(
                    str(outcome.get("project_path") or "")
                ).name,
                "text": " ".join(
                    str(outcome.get(key) or "")
                    for key in (
                        "summary",
                        "completion_reason",
                        "value_stage",
                        "latest_feedback",
                        "impact_value",
                    )
                ),
                "path": outcome.get("project_path"),
                "collected_at": outcome.get("completed_at"),
                "trust_level": "user_confirmed",
                "value_stage": outcome.get("value_stage"),
                "value_score": outcome.get("value_score"),
            }
        )
    for methodology in index.get("graph_methodology_candidates", []):
        if (
            not isinstance(methodology, dict)
            or methodology.get("status") != "approved"
        ):
            continue
        candidates.append(
            {
                "kind": "approved_methodology",
                "session_id": (
                    (methodology.get("source_session_ids") or [""])[-1]
                ),
                "title": methodology.get("title"),
                "project_name": Path(
                    str(methodology.get("project_path") or "")
                ).name,
                "text": " ".join(
                    str(methodology.get(key) or "")
                    for key in ("title", "trigger", "decision", "action", "result")
                ),
                "path": methodology.get("project_path"),
                "collected_at": methodology.get("reviewed_at"),
                "trust_level": "user_confirmed",
            }
        )
    ranked, ranking_meta = rank_work_ledger_recall_candidates(clean, query_terms, candidates)
    hits = [item for item in ranked if float(item.get("score") or 0) > 0][: max(1, min(int(limit or 8), 30))]
    return {
        "query": clean,
        "window_days": days,
        "terms": query_terms,
        "hit_count": len(hits),
        "hits": hits,
        "ranking": ranking_meta,
        "index_summary": {
            "session_count": index.get("session_count", 0),
            "project_counts": index.get("project_counts", {}),
            "adopted_output_count": len(index.get("adopted_outputs", [])),
            "adopted_process_candidate_count": len(index.get("adopted_process_candidates", [])),
            "rejected_process_candidate_count": len(index.get("rejected_process_candidates", [])),
            "methodology_candidate_count": len(index.get("methodology_candidates", [])),
            "verified_outcome_count": len(index.get("verified_outcomes", [])),
            "valued_outcome_count": len(index.get("valued_outcomes", [])),
            "value_summary": index.get("value_summary", {}),
            "approved_methodology_count": sum(
                1
                for row in index.get("graph_methodology_candidates", [])
                if row.get("status") == "approved"
            ),
        },
    }


def generate_multi_day_weekly_report(days: int = 7, *, title: str | None = None) -> dict[str, Any]:
    days = max(1, min(int(days or 7), 365))
    index = write_work_ledger_recall_index(days)
    report = build_multi_day_weekly_report(index, title=title)
    out_dir = _outputs_dir() / "weekly"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"work_ledger_weekly_{days}d_{_day()}_{uuid.uuid4().hex[:8]}.md"
    path.write_text(report.strip() + "\n", encoding="utf-8")
    enhanced = _try_generate_llm_weekly_report(index=index, baseline_report=report, out_dir=out_dir)
    payload = {
        "days": days,
        "path": str(path),
        "enhanced_path": str(enhanced.get("path") or ""),
        "quality_report_path": str(enhanced.get("quality_report_path") or ""),
        "session_count": index.get("session_count", 0),
        "adopted_output_count": len(index.get("adopted_outputs", [])),
        "methodology_candidate_count": len(
            index.get("graph_methodology_candidates", [])
        ),
        "verified_outcome_count": len(index.get("verified_outcomes", [])),
        "valued_outcome_count": len(index.get("valued_outcomes", [])),
        "value_summary": index.get("value_summary", {}),
        "source_index_path": index.get("path"),
        "llm_refinement": enhanced,
    }
    _record_kernel_event("work_ledger_multi_day_weekly_report_generated", "work_ledger", payload)
    return {"path": str(path), "text": report, "index": index, **payload}


def _build_codex_brief_execution_state(
    *,
    requested: bool,
    wait_budget_seconds: int,
    consultation: dict[str, Any],
    fusion: dict[str, Any],
    fusion_trace: dict[str, Any],
    generation_mode: str,
) -> dict[str, Any]:
    results = [
        item
        for item in (consultation.get("results") or [])
        if isinstance(item, dict)
    ]
    used_claim_count = sum(
        len(fusion_trace.get(key) or [])
        for key in (
            "used_claim_ids",
            "used_interpretation_ids",
            "used_recommendation_ids",
        )
    )
    verified_reply_count = int(fusion.get("successful_reply_count") or 0)
    usable_claim_count = int(fusion.get("usable_claim_count") or 0)
    completion_statuses = [
        str((item.get("completion_state") or {}).get("status") or "")
        for item in results
        if isinstance(item.get("completion_state"), dict)
    ]
    tool_details = [
        str(item.get("tool_detail") or "")
        for item in results
        if str(item.get("tool_detail") or "").strip()
    ]
    waited_seconds = max(
        (
            float((item.get("completion_state") or {}).get("elapsed_seconds") or 0)
            for item in results
            if isinstance(item.get("completion_state"), dict)
        ),
        default=0.0,
    )
    fallback_strategy = (
        "jachin_llm_over_local_evidence"
        if generation_mode == "llm_evidence_editor"
        else "deterministic_local_evidence_baseline"
    )

    if not requested:
        status = "not_requested"
        reason = "codex_consultation_disabled"
        degraded = False
    elif consultation.get("reason") == "no_report_evidence_gap":
        status = "not_needed"
        reason = "local_evidence_has_no_detected_gap"
        degraded = False
    elif verified_reply_count and usable_claim_count and used_claim_count:
        status = "fused"
        reason = "verified_codex_claims_fused_with_jachin_evidence"
        degraded = False
    elif verified_reply_count:
        status = "degraded"
        reason = "verified_codex_reply_not_consumed_by_final_composer"
        degraded = True
    else:
        status = "degraded"
        degraded = True
        if "permission_required" in completion_statuses:
            reason = "codex_permission_not_approved_before_deadline"
        elif "timeout" in completion_statuses or any(
            "timeout" in detail for detail in tool_details
        ):
            reason = "codex_reply_timeout"
        elif "generation_error" in completion_statuses:
            reason = "codex_generation_failed"
        elif any("unverified" in detail for detail in tool_details):
            reason = "codex_reply_failed_validation"
        else:
            reason = str(
                consultation.get("reason") or "codex_no_verified_reply"
            )

    return {
        "status": status,
        "reason": reason,
        "requested": requested,
        "degraded": degraded,
        "wait_budget_seconds": int(wait_budget_seconds),
        "waited_seconds": round(waited_seconds, 1),
        "verified_reply_count": verified_reply_count,
        "usable_claim_count": usable_claim_count,
        "used_claim_count": used_claim_count,
        "fallback_strategy": fallback_strategy if degraded else "",
        "assurance": (
            "codex claims were used only after correlation and quality validation"
            if status == "fused"
            else "no unverified Codex content entered the final brief"
        ),
    }


def generate_instant_work_brief(
    days: int = 1,
    *,
    title: str | None = None,
    consult_codex: bool = False,
    codex_wait_seconds: int = 300,
) -> dict[str, Any]:
    """Generate an evidence-backed brief on demand using natural-day windows."""

    days = max(1, min(int(days or 1), 365))
    checkpoint: dict[str, Any] | None = None
    active = get_active_session()
    if active:
        try:
            checkpoint = collect_work_checkpoint(
                str(active.get("session_id") or ""),
                trigger=f"instant_brief_{days}d",
                force=False,
            )
        except Exception as exc:
            checkpoint = {"ok": False, "error": str(exc)}
    index = build_work_ledger_recall_index(
        days,
        limit=500,
        calendar_window=True,
    )
    codex_consultation: dict[str, Any] = {
        "ok": True,
        "consulted": False,
        "reason": "disabled",
        "results": [],
    }
    codex_wait_budget = max(10, min(int(codex_wait_seconds or 300), 600))
    if consult_codex:
        try:
            from l3_node.work_ledger_codex import consult_codex_for_brief

            codex_consultation = consult_codex_for_brief(
                index,
                max_projects=3,
                wait_seconds=codex_wait_budget,
            )
            if codex_consultation.get("consulted") or codex_consultation.get(
                "success_count"
            ):
                index = build_work_ledger_recall_index(
                    days,
                    limit=500,
                    calendar_window=True,
                )
        except Exception as exc:
            logger.exception("[WorkLedger] Codex work-plan consultation failed: %s", exc)
            codex_consultation = {
                "ok": False,
                "consulted": False,
                "reason": f"consultation_failed:{type(exc).__name__}",
                "error": str(exc)[:500],
                "results": [],
            }
    baseline_brief = build_instant_work_brief(index, title=title)
    out_dir = _outputs_dir() / "briefings"
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = uuid.uuid4().hex[:8]
    path = out_dir / f"work_ledger_brief_{days}d_{_day()}_{suffix}.md"
    baseline_path = out_dir / (
        f"work_ledger_brief_{days}d_{_day()}_{suffix}.baseline.md"
    )
    index_path = out_dir / f"work_ledger_brief_{days}d_{_day()}_{suffix}.evidence.json"
    baseline_path.write_text(baseline_brief.strip() + "\n", encoding="utf-8")
    _write_json(index_path, index)
    enhanced = _try_generate_llm_instant_brief(
        index=index,
        baseline_brief=baseline_brief,
        out_dir=out_dir,
        suffix=suffix,
    )
    codex_fusion = (
        enhanced.get("codex_fusion")
        if isinstance(enhanced.get("codex_fusion"), dict)
        else {}
    )
    if not codex_fusion:
        try:
            from l3_node.work_ledger_llm import build_codex_fusion_context

            codex_fusion = build_codex_fusion_context(index)
        except Exception:
            codex_fusion = {}
    brief = str(enhanced.get("text") or baseline_brief).strip()
    path.write_text(brief + "\n", encoding="utf-8")
    generation_mode = "llm_evidence_editor" if enhanced.get("ok") else "evidence_baseline"
    fusion_trace = (
        enhanced.get("fusion_trace")
        if isinstance(enhanced.get("fusion_trace"), dict)
        else {}
    )
    used_codex_claim_ids = {
        str(value)
        for key in (
            "used_claim_ids",
            "used_interpretation_ids",
            "used_recommendation_ids",
        )
        for value in (fusion_trace.get(key) or [])
        if str(value).strip()
    }
    for result in codex_consultation.get("results") or []:
        if not isinstance(result, dict):
            continue
        claim_fusion = (
            result.get("claim_fusion")
            if isinstance(result.get("claim_fusion"), dict)
            else {}
        )
        result_claim_ids = {
            str(item.get("claim_id") or "")
            for item in (claim_fusion.get("claims") or [])
            if isinstance(item, dict) and str(item.get("claim_id") or "").strip()
        }
        adopted_claim_ids = sorted(result_claim_ids & used_codex_claim_ids)
        result["used_in_final_brief"] = bool(adopted_claim_ids)
        result["used_claim_count"] = len(adopted_claim_ids)
        result["used_claim_ids"] = adopted_claim_ids
    codex_execution = _build_codex_brief_execution_state(
        requested=consult_codex,
        wait_budget_seconds=codex_wait_budget,
        consultation=codex_consultation,
        fusion=codex_fusion,
        fusion_trace=fusion_trace,
        generation_mode=generation_mode,
    )
    codex_execution_path = out_dir / (
        f"work_ledger_brief_{days}d_{_day()}_{suffix}.codex-execution.json"
    )
    _write_json(
        codex_execution_path,
        {
            "schema_version": 1,
            "generated_at": index.get("generated_at"),
            "report_path": str(path),
            "codex_execution": codex_execution,
            "codex_fusion": codex_fusion,
            "fusion_trace": fusion_trace,
            "consultation": {
                "reason": codex_consultation.get("reason"),
                "gap_count": codex_consultation.get("gap_count"),
                "success_count": codex_consultation.get("success_count"),
                "reused_count": codex_consultation.get("reused_count"),
                "effective_count": codex_consultation.get("effective_count"),
                "results": [
                    {
                        key: item.get(key)
                        for key in (
                            "ok",
                            "deduplicated",
                            "project_name",
                            "conversation_name",
                            "tool_detail",
                            "completion_state",
                            "answer_length",
                            "used_in_final_brief",
                            "used_claim_count",
                            "tool_evidence_path",
                            "evidence_panel_path",
                        )
                    }
                    for item in (codex_consultation.get("results") or [])
                    if isinstance(item, dict)
                ],
            },
        },
    )
    payload = {
        "days": days,
        "window_mode": "calendar_days",
        "path": str(path),
        "baseline_path": str(baseline_path),
        "source_index_path": str(index_path),
        "quality_report_path": str(enhanced.get("quality_report_path") or ""),
        "session_count": int(index.get("session_count") or 0),
        "activity_day_count": int(index.get("activity_day_count") or 0),
        "git_commit_count": int(index.get("git_commit_count") or 0),
        "verified_outcome_count": len(index.get("verified_outcomes") or []),
        "changed_file_count": _brief_changed_file_count(index),
        "generated_at": index.get("generated_at"),
        "generation_mode": generation_mode,
        "model": str(enhanced.get("model") or ""),
        "llm_refinement": enhanced,
        "checkpoint": checkpoint,
        "codex_consultation": codex_consultation,
        "codex_fusion": codex_fusion,
        "fusion_trace": fusion_trace,
        "codex_execution": codex_execution,
        "codex_execution_path": str(codex_execution_path),
    }
    _record_kernel_event(
        "work_ledger_instant_brief_generated",
        "work_ledger",
        payload,
    )
    return {"text": brief, "index": index, **payload}


def _find_previous_work_context(session: dict[str, Any]) -> dict[str, Any] | None:
    project_path = str(session.get("project_path") or "").strip()
    project_name = str(session.get("project_name") or "").strip().lower()
    try:
        normalized_path = str(Path(project_path).expanduser().resolve()).lower() if project_path else ""
    except Exception:
        normalized_path = project_path.lower()
    for row in _load_index():
        sid = str(row.get("session_id") or "").strip()
        if not sid or sid == str(session.get("session_id") or ""):
            continue
        candidate_path = str(row.get("project_path") or "").strip()
        try:
            candidate_normalized = str(Path(candidate_path).expanduser().resolve()).lower() if candidate_path else ""
        except Exception:
            candidate_normalized = candidate_path.lower()
        candidate_name = str(row.get("project_name") or "").strip().lower()
        if normalized_path:
            same_project = candidate_normalized == normalized_path
        else:
            same_project = bool(project_name and candidate_name == project_name)
        if not same_project:
            continue
        try:
            previous = _load_session(sid)
        except Exception:
            previous = row
        output_paths = previous.get("output_paths") if isinstance(previous.get("output_paths"), dict) else {}
        assets = {
            key: str(output_paths.get(key) or "")
            for key in ("context_pack", "codex_continuation_prompt", "daily_report", "lark_brief")
            if _work_output_path_ready(output_paths.get(key))
        }
        required = {"context_pack", "codex_continuation_prompt"}
        return {
            "previous_session_id": sid,
            "previous_title": previous.get("title"),
            "previous_status": previous.get("status"),
            "project_name": previous.get("project_name"),
            "project_path": previous.get("project_path"),
            "available_assets": assets,
            "hit": required.issubset(assets),
            "reason": "same_project_with_context_assets" if required.issubset(assets) else "same_project_without_complete_context_assets",
        }
    return None


def start_session(
    *,
    title: str,
    project_path: str | None = None,
    user_goal: str | None = None,
    project_name: str | None = None,
    tags: list[str] | None = None,
    created_from: str = "console",
    auto_collect: bool = True,
) -> dict[str, Any]:
    title = (title or "").strip()
    if not title:
        raise ValueError("title is required")
    resolved_project_path = _resolve_project_path(project_path)
    session_id = _short_id("work")
    now = _now_ms()
    session = {
        "schema_version": 1,
        "session_id": session_id,
        "title": title[:180],
        "project_name": (project_name or _infer_project_name(resolved_project_path) or "").strip(),
        "project_path": str(resolved_project_path) if resolved_project_path else "",
        "start_time": _now_iso(),
        "end_time": None,
        "status": "active",
        "user_goal": (user_goal or title).strip(),
        "tags": tags or [],
        "created_from": created_from,
        "created_at_ms": now,
        "updated_at_ms": now,
        "evidence_count": 0,
        "output_paths": {},
    }
    continuation_context = _find_previous_work_context(session)
    if continuation_context:
        session["continuation_context"] = continuation_context
    _save_session(session)
    _set_active_session(session_id)
    append_evidence(
        session_id,
        source="work_session",
        summary="工作任务已开始",
        payload={
            "title": session["title"],
            "project_path": session["project_path"],
            "user_goal": session["user_goal"],
        },
        trust_level="user_confirmed",
    )
    if continuation_context:
        continuation_evidence = append_evidence(
            session_id,
            source="work_continuation_context",
            summary=(
                "已承接上一任务的 Context Pack 和续写 Prompt"
                if continuation_context.get("hit")
                else "发现上一任务，但缺少完整 Context Pack 或续写 Prompt"
            ),
            payload=continuation_context,
            trust_level="system_observed",
            source_refs=[
                {
                    "type": "work_session",
                    "session_id": continuation_context.get("previous_session_id"),
                }
            ],
        )
        if continuation_context.get("hit"):
            try:
                from l3_node.work_ledger_value import record_value_event

                record_value_event(
                    session_id,
                    "continuation_available",
                    related_session_id=str(
                        continuation_context.get("previous_session_id") or ""
                    ),
                    evidence_id=str(
                        continuation_evidence.get("evidence_id") or ""
                    ),
                    idempotency_key=f"continuation-available:{session_id}",
                )
            except Exception as exc:
                logger.warning(
                    "[WorkLedger] continuation value event skipped: %s",
                    exc,
                )
    if auto_collect:
        collect_snapshot(session_id, trigger="session_start")
    try:
        from l3_node.work_ledger_codex import record_codex_work_chain_plan

        record_codex_work_chain_plan(session_id, phase="task_start")
    except Exception as exc:
        logger.warning("[WorkLedger] Codex task-start planning skipped: %s", exc)
    _remember_project_context(session)
    _record_kernel_event("work_session_started", session_id, {"session": session})
    return get_session_detail(session_id)


def end_session(session_id: str | None = None, *, generate_outputs: bool = True) -> dict[str, Any]:
    session = _resolve_session(session_id)
    if session.get("status") != "active":
        raise ValueError("session is not active")
    collect_snapshot(str(session["session_id"]), trigger="session_end")
    try:
        from l3_node.work_ledger_codex import record_codex_work_chain_plan

        record_codex_work_chain_plan(
            str(session["session_id"]),
            phase="end_day",
        )
    except Exception as exc:
        logger.warning("[WorkLedger] Codex end-session planning skipped: %s", exc)
    session = _load_session(str(session["session_id"]))
    session["status"] = "closed"
    session["end_time"] = _now_iso()
    session = _save_session(session)
    if get_active_session() and get_active_session().get("session_id") == session.get("session_id"):
        _set_active_session(None)
    append_evidence(
        str(session["session_id"]),
        source="work_session",
        summary="工作任务已结束",
        payload={"end_time": session["end_time"]},
        trust_level="system_observed",
    )
    outputs = generate_work_outputs(str(session["session_id"])) if generate_outputs else {}
    _remember_project_context(session)
    _record_kernel_event("work_session_closed", str(session["session_id"]), {"session": session, "outputs": outputs})
    detail = get_session_detail(str(session["session_id"]))
    detail["outputs"] = outputs
    return detail


def add_manual_note(session_id: str | None, text: str) -> dict[str, Any]:
    session = _resolve_session(session_id)
    clean = (text or "").strip()
    if not clean:
        raise ValueError("note text is required")
    evidence = append_evidence(
        str(session["session_id"]),
        source="manual_note",
        summary=clean[:160],
        payload={"text": clean},
        trust_level="user_confirmed",
    )
    _record_kernel_event("work_manual_note_added", str(session["session_id"]), {"evidence": evidence})
    return evidence


def analyze_ai_trace_text(text: str) -> dict[str, Any]:
    """Extract durable work signals from a pasted Codex/Cursor trace."""

    clean = str(text or "").strip()
    lines = [line.strip(" -\t\r") for line in clean.splitlines() if line.strip()]
    buckets: dict[str, list[str]] = {key: [] for key in AI_TRACE_BUCKETS}
    for line in lines:
        lower = line.lower()
        for bucket, keywords in AI_TRACE_BUCKETS.items():
            if any(keyword.lower() in lower for keyword in keywords):
                buckets[bucket].append(line[:240])
    for key, values in list(buckets.items()):
        deduped: list[str] = []
        seen: set[str] = set()
        for value in values:
            norm = re.sub(r"\s+", " ", value).strip().lower()
            if norm and norm not in seen:
                seen.add(norm)
                deduped.append(value)
        buckets[key] = deduped[:8]
    signal_count = sum(len(values) for values in buckets.values())
    first_line = lines[0][:180] if lines else clean[:180]
    one_line = (
        buckets["decisions"][0]
        if buckets["decisions"]
        else buckets["actions"][0]
        if buckets["actions"]
        else first_line
    )
    return {
        "one_line": one_line,
        "line_count": len(lines),
        "char_count": len(clean),
        "signal_count": signal_count,
        "buckets": buckets,
    }


def add_ai_work_trace(
    session_id: str | None,
    text: str,
    *,
    tool_name: str = "ai_tool",
    trace_kind: str = "imported_trace",
) -> dict[str, Any]:
    """Import a user-provided Codex/Cursor/AI work trace into the ledger."""

    session = _resolve_session(session_id)
    clean = (text or "").strip()
    if not clean:
        raise ValueError("trace text is required")
    tool = (tool_name or "ai_tool").strip()[:80]
    analysis = analyze_ai_trace_text(clean)
    evidence = append_evidence(
        str(session["session_id"]),
        source="ai_work_trace",
        summary=f"{tool} 过程记录：{clean[:140]}",
        payload={
            "tool_name": tool,
            "trace_kind": trace_kind,
            "text": clean,
            "char_count": len(clean),
            "analysis": analysis,
        },
        trust_level="user_confirmed",
    )
    _record_kernel_event("work_ai_trace_imported", str(session["session_id"]), {"evidence": evidence})
    return evidence


def import_ai_work_process(
    session_id: str | None,
    *,
    text: str = "",
    file_path: str = "",
    tool_name: str = "",
    trace_kind: str = "process_import",
    auto_collect: bool = True,
    generate_outputs_after: bool = True,
) -> dict[str, Any]:
    """Import noisy AI/terminal work material and refresh downstream outputs."""

    session = _resolve_session(session_id)
    raw_text, source_meta = _load_work_process_material(text=text, file_path=file_path)
    if not raw_text.strip():
        raise ValueError("no process material to import")
    prepared = prepare_work_process_import(raw_text, source_meta=source_meta)
    tool = (tool_name or prepared.get("tool_name") or _infer_trace_tool(raw_text, file_path)).strip() or "AI"
    evidence = add_ai_work_trace(
        str(session["session_id"]),
        str(prepared.get("trace_text") or raw_text),
        tool_name=tool,
        trace_kind=trace_kind,
    )
    payload = evidence.get("payload") if isinstance(evidence.get("payload"), dict) else {}
    payload["import"] = {
        "source": source_meta,
        "prepared": {key: value for key, value in prepared.items() if key != "trace_text"},
    }
    evidence["payload"] = payload
    _rewrite_evidence_payload(str(session["session_id"]), str(evidence.get("evidence_id") or ""), payload)
    collected = collect_snapshot(str(session["session_id"]), trigger=f"{trace_kind}_after_import") if auto_collect else {}
    outputs = generate_work_outputs(str(session["session_id"])) if generate_outputs_after else {}
    _record_kernel_event(
        "work_process_imported",
        str(session["session_id"]),
        {
            "evidence_id": evidence.get("evidence_id"),
            "tool_name": tool,
            "source": source_meta,
            "prepared": {key: value for key, value in prepared.items() if key != "trace_text"},
            "outputs": outputs,
        },
    )
    return {
        "session": _load_session(str(session["session_id"])),
        "evidence": evidence,
        "collected": collected,
        "outputs": outputs,
        "import": {key: value for key, value in prepared.items() if key != "trace_text"},
    }


def build_end_day_preview(
    session_id: str | None = None,
    *,
    process_text: str = "",
    process_file_path: str = "",
    include_clipboard_hint: bool = False,
    discover_candidates: bool = True,
) -> dict[str, Any]:
    """Build a user-confirmable end-of-day package preview."""

    session = _resolve_session(session_id)
    sid = str(session["session_id"])
    existing = load_evidence(sid, 1000)
    evidence_counts: dict[str, int] = {}
    for row in existing:
        source = str(row.get("source") or "unknown")
        evidence_counts[source] = evidence_counts.get(source, 0) + 1
    candidates: list[dict[str, Any]] = []
    if session.get("project_path"):
        root = Path(str(session.get("project_path") or ""))
        git_payload = collect_git_snapshot(root)
        file_payload = collect_recent_files(root, since_ms=int(session.get("created_at_ms") or 0), max_files=80)
        changed_files = _extract_changed_files(git_payload, file_payload)
        candidates.append(
            {
                "kind": "git_snapshot",
                "summary": _git_summary(git_payload),
                "count": len(git_payload.get("changed_files", []) if isinstance(git_payload, dict) else []),
                "sample": [item.get("path") for item in changed_files[:8] if isinstance(item, dict)],
                "will_collect_on_finalize": True,
            }
        )
        candidates.append(
            {
                "kind": "recent_files",
                "summary": f"{len(file_payload.get('recent_files', []))} recent files",
                "count": len(file_payload.get("recent_files", []) if isinstance(file_payload, dict) else []),
                "sample": [item.get("path") for item in file_payload.get("recent_files", [])[:8] if isinstance(item, dict)],
                "will_collect_on_finalize": True,
            }
        )
    raw_process = ""
    process_source: dict[str, Any] = {}
    if process_text.strip() or process_file_path.strip():
        raw_process, process_source = _load_work_process_material(text=process_text, file_path=process_file_path)
        prepared = prepare_work_process_import(raw_process, source_meta=process_source)
        safety = scan_sensitive_material(raw_process)
        candidates.append(
            {
                "kind": "process_import",
                "summary": prepared.get("one_line") or "AI/terminal process import",
                "count": prepared.get("selected_line_count", 0),
                "sample": str(prepared.get("trace_text") or "").splitlines()[:8],
                "source": process_source,
                "safety": safety,
                "will_import_on_finalize": not safety.get("blocked"),
            }
        )
    elif include_clipboard_hint:
        candidates.append(
            {
                "kind": "clipboard_hint",
                "summary": "Clipboard text must be imported by the desktop UI, then confirmed here.",
                "count": 0,
                "sample": [],
                "will_import_on_finalize": False,
            }
        )
    candidate_quality: dict[str, Any] = {}
    if discover_candidates:
        discovered = discover_work_process_candidates(sid, limit=8)
        candidate_quality = discovered.get("quality") if isinstance(discovered.get("quality"), dict) else {}
        for item in discovered.get("candidates", []):
            candidates.append(item)
    preview = {
        "schema_version": 1,
        "session_id": sid,
        "title": session.get("title"),
        "project_name": session.get("project_name"),
        "project_path": session.get("project_path"),
        "evidence_counts": evidence_counts,
        "candidates": candidates,
        "candidate_quality": candidate_quality,
        "safety": summarize_candidate_safety(candidates),
        "recommended_outputs": [
            "daily_report",
            "work_review",
            "context_pack",
            "codex_continuation_prompt",
            "lark_brief",
            "team_lark_brief",
            "methodology_candidates",
        ],
        "requires_user_confirmation": True,
    }
    try:
        from l3_node.work_ledger_codex import (
            get_codex_work_chain_state,
            record_codex_work_chain_plan,
        )

        record_codex_work_chain_plan(sid, phase="end_day")
        preview["codex_work_chain"] = get_codex_work_chain_state(sid)
    except Exception as exc:
        logger.warning("[WorkLedger] Codex end-day planning skipped: %s", exc)
    evidence = append_evidence(
        sid,
        source="end_day_preview",
        summary=f"End-day preview prepared with {len(candidates)} candidate groups",
        payload=preview,
        trust_level="system_observed",
    )
    _record_kernel_event("work_ledger_end_day_preview_built", sid, {"preview": preview, "evidence_id": evidence.get("evidence_id")})
    return {"session": _load_session(sid), "preview": preview, "evidence": evidence}


def discover_work_process_candidates(session_id: str | None = None, *, limit: int = 12) -> dict[str, Any]:
    """Find likely local work-process materials without importing them."""

    session = _resolve_session(session_id)
    sid = str(session["session_id"])
    quality = build_work_process_candidate_source_quality(days=30)
    roots: list[Path] = []
    root_reasons: dict[str, str] = {}

    def add_root(path: Path, reason: str) -> None:
        try:
            resolved = path.expanduser().resolve()
        except Exception:
            return
        key = str(resolved).lower()
        if key in root_reasons or not resolved.exists():
            return
        roots.append(resolved)
        root_reasons[key] = reason

    if session.get("project_path"):
        add_root(Path(str(session.get("project_path"))), "project")
    add_root(work_ledger_home(), "work_ledger_home")
    app_root = get_app_root()
    add_root(app_root / "output", "jachin_output")
    add_root(app_root / "logs", "jachin_logs")
    add_root(Path.home() / ".jachin", "user_jachin_home")

    cutoff_ms = max(0, int(session.get("created_at_ms") or 0) - 6 * 60 * 60 * 1000)
    candidates: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for root in roots:
        if root.is_file():
            paths = [root]
        else:
            paths = _iter_candidate_process_files(root, max_files=180)
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            mtime_ms = int(stat.st_mtime * 1000)
            if cutoff_ms and mtime_ms < cutoff_ms:
                continue
            path_key = str(path.resolve()).lower()
            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)
            root_reason = root_reasons.get(str(root.resolve()).lower(), "unknown")
            score, reason = _score_work_process_candidate(path, root_reason, session)
            if score <= 0:
                continue
            quality_key = _candidate_quality_key(path, root_reason)
            quality_row = quality.get("sources", {}).get(quality_key, {}) if isinstance(quality.get("sources"), dict) else {}
            quality_adjustment = float(quality_row.get("score_adjustment") or 0.0)
            score += quality_adjustment
            if quality_adjustment:
                reason = f"{reason},quality={round(quality_adjustment, 2)}"
            excerpt = _read_candidate_file_excerpt(path, max_chars=24000)
            prepared = prepare_work_process_import(excerpt, source_meta={"type": "file", "file_path": str(path.resolve())})
            safety = scan_sensitive_material(excerpt)
            sample = str(prepared.get("trace_text") or "").splitlines()[:6]
            candidates.append(
                {
                    "kind": "discovered_process_file",
                    "summary": prepared.get("one_line") or path.name,
                    "count": prepared.get("selected_line_count", 0),
                    "sample": sample,
                    "source": {
                        "type": "file",
                        "file_path": str(path.resolve()),
                        "root_reason": root_reason,
                        "quality_key": quality_key,
                        "quality": quality_row,
                        "mtime_ms": mtime_ms,
                        "size": stat.st_size,
                    },
                    "safety": safety,
                    "score": score,
                    "reason": reason,
                    "will_import_on_finalize": False,
                    "action": "select_for_import",
                }
            )
    candidates.sort(key=lambda item: (float(item.get("score") or 0), int((item.get("source") or {}).get("mtime_ms") or 0)), reverse=True)
    candidates = _dedupe_discovered_process_candidates(candidates)
    out = {
        "session_id": sid,
        "candidate_count": len(candidates),
        "candidates": candidates[: max(1, min(int(limit or 12), 50))],
        "quality": quality,
        "roots": [{"path": str(path), "reason": root_reasons.get(str(path.resolve()).lower(), "")} for path in roots],
    }
    _record_kernel_event("work_ledger_process_candidates_discovered", sid, out)
    return out


def _dedupe_discovered_process_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        source = item.get("source") if isinstance(item.get("source"), dict) else {}
        file_path = str(source.get("file_path") or "")
        root_reason = str(source.get("root_reason") or "")
        path = Path(file_path) if file_path else Path(str(item.get("summary") or "unknown"))
        if root_reason == "work_ledger_home":
            key = f"{root_reason}:{path.name.lower()}"
        else:
            key = file_path.lower() or str(item.get("summary") or "").lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def build_work_process_candidate_source_quality(days: int = 30) -> dict[str, Any]:
    """Summarize whether each candidate source has historically been useful."""

    rows: dict[str, dict[str, Any]] = {}
    for session in list_recent_sessions(days, limit=500):
        sid = str(session.get("session_id") or "")
        if not sid:
            continue
        for ev in load_evidence(sid, 1000):
            if ev.get("source") != "work_process_candidate_feedback":
                continue
            payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
            action = str(payload.get("action") or "").strip().lower()
            file_path = str(payload.get("file_path") or "").strip()
            quality_key = str(payload.get("quality_key") or "").strip()
            if not quality_key:
                quality_key = _candidate_quality_key(Path(file_path), str(payload.get("root_reason") or "unknown"))
            if not quality_key:
                continue
            row = rows.setdefault(
                quality_key,
                {
                    "quality_key": quality_key,
                    "accepted": 0,
                    "rejected": 0,
                    "blocked": 0,
                    "total": 0,
                    "score_adjustment": 0.0,
                    "last_seen_at": "",
                },
            )
            if action == "accepted":
                row["accepted"] = int(row.get("accepted") or 0) + 1
            elif action == "blocked":
                row["blocked"] = int(row.get("blocked") or 0) + 1
            elif action == "rejected":
                row["rejected"] = int(row.get("rejected") or 0) + 1
            else:
                continue
            row["total"] = int(row.get("total") or 0) + 1
            row["last_seen_at"] = ev.get("collected_at") or row.get("last_seen_at") or ""
    for row in rows.values():
        accepted = int(row.get("accepted") or 0)
        rejected = int(row.get("rejected") or 0)
        blocked = int(row.get("blocked") or 0)
        total = max(1, int(row.get("total") or 0))
        adjustment = min(3.0, accepted * 1.2) - min(3.0, rejected * 1.4 + blocked * 2.0)
        if total == 1:
            adjustment *= 0.65
        row["score_adjustment"] = round(adjustment, 3)
        row["accept_rate"] = round(accepted / total, 3)
    ranked_sources = sorted(
        rows.values(),
        key=lambda row: (
            float(row.get("score_adjustment") or 0.0),
            float(row.get("accept_rate") or 0.0),
            int(row.get("total") or 0),
        ),
        reverse=True,
    )
    totals = {
        "accepted": sum(int(row.get("accepted") or 0) for row in rows.values()),
        "rejected": sum(int(row.get("rejected") or 0) for row in rows.values()),
        "blocked": sum(int(row.get("blocked") or 0) for row in rows.values()),
        "total": sum(int(row.get("total") or 0) for row in rows.values()),
    }
    summary = {
        "source_count": len(rows),
        "positive_sources": sum(1 for row in rows.values() if float(row.get("score_adjustment") or 0.0) > 0),
        "neutral_sources": sum(1 for row in rows.values() if float(row.get("score_adjustment") or 0.0) == 0),
        "negative_sources": sum(1 for row in rows.values() if float(row.get("score_adjustment") or 0.0) < 0),
    }
    return {
        "schema_version": 1,
        "window_days": days,
        "generated_at": _now_iso(),
        "sources": rows,
        "ranked_sources": ranked_sources,
        "totals": totals,
        "summary": summary,
    }


def _candidate_quality_key(path: Path, root_reason: str) -> str:
    raw = str(path).lower()
    name = path.name.lower()
    reason = str(root_reason or "unknown").lower()
    if "codex" in raw:
        return "codex_trace"
    if "cursor" in raw:
        return "cursor_trace"
    if "terminal" in raw or "powershell" in raw:
        return "terminal_log"
    if "l3_debug" in name or ("logs" in raw and name.endswith(".log")):
        return "jachin_runtime_log"
    if reason == "work_ledger_home":
        return "work_ledger_output"
    if name.endswith(".md"):
        return "project_markdown"
    if name.endswith(".jsonl"):
        return "structured_log"
    return f"{reason}_file"


def _infer_candidate_root_reason(path: Path, session: dict[str, Any]) -> str:
    try:
        resolved = path.expanduser().resolve()
    except Exception:
        resolved = path
    roots: list[tuple[Path, str]] = []
    if session.get("project_path"):
        roots.append((Path(str(session.get("project_path"))), "project"))
    roots.extend(
        [
            (work_ledger_home(), "work_ledger_home"),
            (get_app_root() / "output", "jachin_output"),
            (get_app_root() / "logs", "jachin_logs"),
            (Path.home() / ".jachin", "user_jachin_home"),
        ]
    )
    for root, reason in roots:
        try:
            resolved.relative_to(root.expanduser().resolve())
            return reason
        except Exception:
            continue
    return "unknown"


def finalize_end_day_package(
    session_id: str | None = None,
    *,
    process_text: str = "",
    process_file_path: str = "",
    close_session: bool = False,
) -> dict[str, Any]:
    """User-confirmed end-of-day finalization."""

    session = _resolve_session(session_id)
    sid = str(session["session_id"])
    imported: dict[str, Any] | None = None
    if process_text.strip() or process_file_path.strip():
        raw_process, _source = _load_work_process_material(text=process_text, file_path=process_file_path)
        safety = scan_sensitive_material(raw_process)
        if safety.get("blocked"):
            raise ValueError(f"process import blocked by sensitive material: {','.join(safety.get('types', []))}")
        sanitized = redact_sensitive_material(raw_process)
        imported = import_ai_work_process(
            sid,
            text=sanitized if process_text.strip() else "",
            file_path=process_file_path if not process_text.strip() else "",
            tool_name=_infer_trace_tool(raw_process, process_file_path),
            trace_kind="end_day_finalize_import",
            auto_collect=False,
            generate_outputs_after=False,
        )
        if process_file_path.strip():
            record_work_process_candidate_feedback(
                sid,
                process_file_path,
                action="accepted",
                note="accepted during end-day finalize",
                imported_evidence_id=str((imported.get("evidence") or {}).get("evidence_id") or "") if isinstance(imported, dict) else "",
            )
    collected = collect_snapshot(sid, trigger="end_day_finalize")
    outputs = generate_work_outputs(sid)
    final_evidence = append_evidence(
        sid,
        source="end_day_package",
        summary="End-day work package generated after user confirmation",
        payload={
            "output_paths": outputs,
            "imported_evidence_id": (imported or {}).get("evidence", {}).get("evidence_id") if isinstance(imported, dict) else "",
            "closed": close_session,
        },
        trust_level="user_confirmed",
    )
    result: dict[str, Any] = {
        "session": _load_session(sid),
        "imported": imported,
        "collected": collected,
        "outputs": outputs,
        "evidence": final_evidence,
    }
    if close_session:
        result["closed"] = end_session(sid, generate_outputs=True)
    _record_kernel_event("work_ledger_end_day_package_finalized", sid, result)
    return result


def scan_sensitive_material(text: str) -> dict[str, Any]:
    """Detect high-risk secrets without storing the sensitive values."""

    raw = str(text or "")
    patterns = {
        "api_key": r"(?i)\b(?:api[_-]?key|dashscope[_-]?api[_-]?key|openai[_-]?api[_-]?key|token|secret)\b\s*[:=]\s*['\"]?[^'\"\s]{12,}",
        "bearer_token": r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}",
        "private_key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        "cookie": r"(?i)\b(cookie|set-cookie)\s*[:=]",
        "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "phone_cn": r"(?<!\d)1[3-9]\d{9}(?!\d)",
    }
    hits: dict[str, int] = {}
    for key, pattern in patterns.items():
        count = len(re.findall(pattern, raw))
        if count:
            hits[key] = count
    blocked_types = {"api_key", "bearer_token", "private_key", "cookie"}
    return {
        "ok": not any(key in blocked_types for key in hits),
        "blocked": any(key in blocked_types for key in hits),
        "types": sorted(hits),
        "counts": hits,
        "char_count": len(raw),
    }


def redact_sensitive_material(text: str) -> str:
    raw = str(text or "")
    replacements = [
        (r"(?i)(\b(?:api[_-]?key|dashscope[_-]?api[_-]?key|openai[_-]?api[_-]?key|token|secret)\b\s*[:=]\s*['\"]?)[^'\"\s]{12,}", r"\1[REDACTED]"),
        (r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}", "Bearer [REDACTED]"),
        (r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", "[REDACTED_PRIVATE_KEY]"),
        (r"(?i)(\b(?:cookie|set-cookie)\s*[:=]\s*).+", r"\1[REDACTED]"),
    ]
    out = raw
    for pattern, replacement in replacements:
        out = re.sub(pattern, replacement, out, flags=re.DOTALL if "PRIVATE KEY" in pattern else 0)
    return out


def summarize_candidate_safety(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, int] = {}
    blocked = False
    for item in candidates:
        safety = item.get("safety") if isinstance(item.get("safety"), dict) else {}
        if item.get("kind") != "discovered_process_file":
            blocked = blocked or bool(safety.get("blocked"))
        counts = safety.get("counts") if isinstance(safety.get("counts"), dict) else {}
        for key, value in counts.items():
            try:
                merged[str(key)] = merged.get(str(key), 0) + int(value)
            except Exception:
                continue
    return {"ok": not blocked, "blocked": blocked, "types": sorted(merged), "counts": merged}


def prepare_work_process_import(text: str, *, source_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Reduce noisy copied logs into durable Work Ledger signal lines."""

    raw = str(text or "").replace("\x00", "").strip()
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    signal_keywords = _work_process_signal_keywords()
    selected: list[str] = []
    seen: set[str] = set()
    for line in lines:
        lower = line.lower()
        is_signal = any(keyword in lower for keyword in signal_keywords)
        is_structural = bool(re.search(r"\b[A-Za-z0-9_./\\-]+\.(py|ts|tsx|rs|md|json|yaml|yml|ps1|toml)\b", line))
        is_command = bool(re.search(r"^(python|pytest|cargo|npm|pnpm|git|npx|node|powershell|uvicorn|tauri)\b", lower))
        if not (is_signal or is_structural or is_command):
            continue
        normalized = re.sub(r"\s+", " ", line).strip().lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        selected.append(line[:500])
        if len(selected) >= 120:
            break
    if not selected:
        selected = lines[-80:] if lines else [raw[:8000]]
    trace_text = "\n".join(selected)
    if len(trace_text) > 30000:
        trace_text = trace_text[-30000:]
    analysis = analyze_ai_trace_text(trace_text)
    return {
        "tool_name": _infer_trace_tool(raw, str((source_meta or {}).get("file_path") or "")),
        "trace_text": trace_text,
        "raw_char_count": len(raw),
        "raw_line_count": len(lines),
        "selected_line_count": len(selected),
        "dropped_line_count": max(0, len(lines) - len(selected)),
        "signal_count": analysis.get("signal_count", 0),
        "one_line": analysis.get("one_line") or "",
    }


def collect_snapshot(session_id: str | None = None, *, trigger: str = "manual") -> dict[str, Any]:
    session = _resolve_session(session_id)
    sid = str(session["session_id"])
    project_path = session.get("project_path")
    results: dict[str, Any] = {"session_id": sid, "trigger": trigger, "collected": []}
    if project_path:
        root = Path(project_path)
        git_payload = collect_git_snapshot(root)
        results["git"] = append_evidence(
            sid,
            source="git_snapshot",
            summary=_git_summary(git_payload),
            payload=git_payload,
            trust_level="system_observed",
        )
        results["collected"].append("git_snapshot")
        file_payload = collect_recent_files(root, since_ms=int(session.get("created_at_ms") or 0))
        results["files"] = append_evidence(
            sid,
            source="file_scan",
            summary=f"扫描到 {len(file_payload.get('recent_files', []))} 个任务期内改动文件",
            payload=file_payload,
            trust_level="system_observed",
        )
        results["collected"].append("file_scan")
        snippet_payload = collect_file_content_snippets(root, git_payload=git_payload, file_payload=file_payload)
        results["snippets"] = append_evidence(
            sid,
            source="file_content_snippets",
            summary=(
                f"采集 {len(snippet_payload.get('snippets', []))} 个文件内容片段，"
                f"{len(snippet_payload.get('risk_candidates', []))} 条风险候选"
            ),
            payload=snippet_payload,
            trust_level="system_observed",
        )
        results["collected"].append("file_content_snippets")
    else:
        results["warning"] = "no project_path configured"
    _record_kernel_event("work_snapshot_collected", sid, results)
    return results


def collect_work_checkpoint(
    session_id: str | None = None,
    *,
    trigger: str = "auto_interval",
    force: bool = False,
) -> dict[str, Any]:
    """Capture a lightweight, deduplicated task checkpoint for Git and non-Git projects."""

    session = _resolve_session(session_id)
    sid = str(session["session_id"])
    project_path = str(session.get("project_path") or "").strip()
    if not project_path:
        return {"session_id": sid, "ok": False, "warning": "no project_path configured", "deduplicated": False}
    root = Path(project_path)
    git_payload = collect_git_snapshot(root)
    file_payload = collect_recent_files(root, since_ms=int(session.get("created_at_ms") or 0), max_files=80)
    snippet_payload = collect_file_content_snippets(
        root,
        git_payload=git_payload,
        file_payload=file_payload,
        max_files=16,
        max_chars_per_file=1600,
    )
    changed_files = [
        {
            "path": str(item.get("path") or ""),
            "status": str(item.get("status") or ""),
            "source": str(item.get("source") or ""),
        }
        for item in (git_payload.get("changed_files") or [])[:80]
        if isinstance(item, dict)
    ]
    recent_files = [
        {
            "path": str(item.get("path") or ""),
            "mtime_ms": int(item.get("mtime_ms") or 0),
            "size": int(item.get("size") or 0),
        }
        for item in (file_payload.get("recent_files") or [])[:80]
        if isinstance(item, dict)
    ]
    fingerprint_payload = {
        "project_path": str(root),
        "is_git_repo": bool(git_payload.get("is_git_repo")),
        "branch": str(git_payload.get("branch") or ""),
        "status_summary": str(git_payload.get("status_summary") or ""),
        "changed_files": changed_files,
        "recent_files": recent_files,
        "diff_sha256": hashlib.sha256(
            (
                str(
                    ((git_payload.get("commands") or {}).get("diff_patch") or {}).get(
                        "stdout"
                    )
                    or ""
                )
                + "\n"
                + str(
                    (
                        (git_payload.get("commands") or {}).get("cached_diff_patch")
                        or {}
                    ).get("stdout")
                    or ""
                )
            ).encode("utf-8")
        ).hexdigest(),
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    previous = next(
        (ev for ev in reversed(load_evidence(sid, 1000)) if ev.get("source") == "work_checkpoint"),
        None,
    )
    previous_payload = previous.get("payload") if isinstance(previous, dict) and isinstance(previous.get("payload"), dict) else {}
    if not force and fingerprint == str(previous_payload.get("fingerprint") or ""):
        result = {
            "session_id": sid,
            "ok": True,
            "deduplicated": True,
            "fingerprint": fingerprint,
            "previous_evidence_id": previous.get("evidence_id") if isinstance(previous, dict) else None,
            "trigger": trigger,
        }
        _record_kernel_event("work_checkpoint_deduplicated", sid, result)
        return result
    project_kind = "git" if git_payload.get("is_git_repo") else "filesystem"
    payload = {
        **fingerprint_payload,
        "commands": {
            key: (git_payload.get("commands") or {}).get(key) or {}
            for key in (
                "status",
                "log",
                "diff_stat",
                "diff_patch",
                "cached_diff_patch",
            )
        },
        "snippets": (snippet_payload.get("snippets") or [])[:16],
        "risk_candidates": (snippet_payload.get("risk_candidates") or [])[:24],
        "fingerprint": fingerprint,
        "trigger": trigger,
        "project_kind": project_kind,
        "changed_file_count": len(changed_files),
        "recent_file_count": len(recent_files),
    }
    evidence = append_evidence(
        sid,
        source="work_checkpoint",
        summary=(
            f"{project_kind} checkpoint：{len(changed_files)} 个 Git 改动，{len(recent_files)} 个任务期文件变化"
            if project_kind == "git"
            else f"filesystem checkpoint：{len(recent_files)} 个任务期文件变化"
        ),
        payload=payload,
        trust_level="system_observed",
    )
    result = {
        "session_id": sid,
        "ok": True,
        "deduplicated": False,
        "fingerprint": fingerprint,
        "trigger": trigger,
        "project_kind": project_kind,
        "evidence": evidence,
    }
    try:
        from l3_node.work_ledger_codex import record_codex_work_chain_plan

        result["codex_work_chain_plan"] = record_codex_work_chain_plan(
            sid,
            phase="checkpoint",
        )
    except Exception as exc:
        logger.warning("[WorkLedger] Codex checkpoint planning skipped: %s", exc)
    _record_kernel_event("work_checkpoint_recorded", sid, result)
    return result


def build_work_timeline(session_id: str, *, limit: int = 200) -> dict[str, Any]:
    session = _load_session(session_id)
    entries: list[dict[str, Any]] = []
    category_counts: dict[str, int] = {}
    source_category = {
        "work_session": "task",
        "work_continuation_context": "continuation",
        "manual_note": "user_note",
        "ai_work_trace": "ai_process",
        "work_checkpoint": "checkpoint",
        "git_snapshot": "system_observation",
        "file_scan": "system_observation",
        "file_content_snippets": "system_observation",
        "work_process_candidate_feedback": "candidate_feedback",
        "work_process_inbox_review": "candidate_feedback",
        "end_day_preview": "preview",
        "work_output": "output",
        "work_output_adoption": "adoption",
        "work_value_event": "value",
        "codex_work_chain_plan": "codex_collaboration",
        "codex_work_plan_consultation": "codex_collaboration",
    }
    for ev in load_evidence(session_id, 2000):
        source = str(ev.get("source") or "unknown")
        category = source_category.get(source, "other")
        category_counts[category] = category_counts.get(category, 0) + 1
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        actor = "ai_tool" if source == "ai_work_trace" else "user" if ev.get("trust_level") == "user_confirmed" else "system"
        entries.append(
            {
                "evidence_id": ev.get("evidence_id"),
                "source": source,
                "category": category,
                "actor": actor,
                "trust_level": ev.get("trust_level"),
                "collected_at": ev.get("collected_at"),
                "collected_at_ms": int(ev.get("collected_at_ms") or 0),
                "summary": ev.get("summary"),
                "details": {
                    "trigger": payload.get("trigger"),
                    "project_kind": payload.get("project_kind"),
                    "changed_file_count": payload.get("changed_file_count"),
                    "recent_file_count": payload.get("recent_file_count"),
                    "action": payload.get("action"),
                    "output_key": payload.get("output_key"),
                    "hit": payload.get("hit"),
                    "scenario_id": payload.get("scenario_id"),
                    "request_key": payload.get("request_key"),
                },
            }
        )
    entries.sort(key=lambda item: int(item.get("collected_at_ms") or 0))
    visible = entries[-max(1, min(int(limit or 200), 500)) :]
    return {
        "schema_version": 1,
        "session_id": session_id,
        "title": session.get("title"),
        "status": session.get("status"),
        "entry_count": len(entries),
        "category_counts": category_counts,
        "entries": visible,
    }


def append_evidence(
    session_id: str,
    *,
    source: str,
    summary: str,
    payload: dict[str, Any] | None = None,
    trust_level: str = "system_observed",
    source_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    session = _load_session(session_id)
    evidence = {
        "schema_version": 1,
        "evidence_id": _short_id("ev"),
        "session_id": session_id,
        "source": source,
        "collected_at": _now_iso(),
        "collected_at_ms": _now_ms(),
        "summary": (summary or source).strip()[:500],
        "payload": payload or {},
        "trust_level": trust_level,
        "source_refs": source_refs or [],
    }
    path = _evidence_path(session_id)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(evidence, ensure_ascii=False, default=str) + "\n")
    session["evidence_count"] = int(session.get("evidence_count") or 0) + 1
    _save_session(session)
    _append_memory_growth_raw(session, evidence)
    return evidence


def load_evidence(session_id: str, limit: int = 500) -> list[dict[str, Any]]:
    path = _evidence_path(session_id)
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
        except Exception:
            continue
    return rows[-max(1, min(int(limit or 500), 2000)) :]


def get_session_detail(session_id: str, *, evidence_limit: int = 300) -> dict[str, Any]:
    session = _load_session(session_id)
    detail = {
        "session": session,
        "evidence": load_evidence(session_id, evidence_limit),
        "paths": {
            "session": str(_session_path(session_id)),
            "evidence": str(_evidence_path(session_id)),
            "home": str(work_ledger_home()),
        },
    }
    try:
        from l3_node.work_ledger_codex import get_codex_work_chain_state

        detail["codex_work_chain"] = get_codex_work_chain_state(session_id)
    except Exception as exc:
        detail["codex_work_chain"] = {
            "request_count": 0,
            "pending_count": 0,
            "completed_count": 0,
            "requests": [],
            "error": str(exc)[:300],
        }
    return detail


def read_output_text(session_id: str, output_key: str, *, max_chars: int = 20000) -> dict[str, Any]:
    session = _load_session(session_id)
    key = str(output_key or "").strip()
    if key not in OUTPUT_TEXT_KEYS:
        raise ValueError(f"unsupported output key: {key}")
    output_paths = session.get("output_paths") if isinstance(session.get("output_paths"), dict) else {}
    path_text = str(output_paths.get(key) or "").strip()
    if not path_text:
        raise ValueError(f"output not generated: {key}")
    path = Path(path_text).expanduser().resolve()
    outputs_root = _outputs_dir().resolve()
    try:
        path.relative_to(outputs_root)
    except ValueError as exc:
        raise ValueError("output path is outside Work Ledger outputs") from exc
    if not path.is_file():
        raise ValueError(f"output file not found: {key}")
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    truncated = len(text) > max_chars
    return {
        "session_id": session_id,
        "output_key": key,
        "path": str(path),
        "text": text[:max_chars],
        "truncated": truncated,
        "char_count": len(text),
    }


def adopt_work_output(
    session_id: str,
    output_key: str,
    *,
    adopted_by: str = "user",
    note: str = "",
    max_chars: int = 8000,
) -> dict[str, Any]:
    session = _load_session(session_id)
    output = read_output_text(session_id, output_key, max_chars=max_chars)
    key = str(output_key or "").strip()
    text = str(output.get("text") or "").strip()
    if not text:
        raise ValueError(f"output is empty: {key}")
    evidence = append_evidence(
        session_id,
        source="work_output_adoption",
        summary=f"用户采纳工作输出：{key}",
        payload={
            "output_key": key,
            "output_path": output.get("path"),
            "adopted_by": (adopted_by or "user").strip()[:80],
            "note": (note or "").strip()[:1000],
            "text": text,
            "truncated": bool(output.get("truncated")),
            "char_count": int(output.get("char_count") or len(text)),
        },
        trust_level="user_confirmed",
        source_refs=[
            {
                "type": "work_output",
                "session_id": session_id,
                "output_key": key,
                "path": output.get("path"),
            }
        ],
    )
    value_result: dict[str, Any] = {}
    try:
        from l3_node.work_ledger_outcomes import get_session_outcome_context
        from l3_node.work_ledger_value import record_value_event

        outcome_context = get_session_outcome_context(session_id)
        outcome_ids = [
            str(row.get("outcome_id") or "")
            for row in outcome_context.get("outcomes_this_session") or []
            if str(row.get("outcome_id") or "")
        ]
        value_result = record_value_event(
            session_id,
            "adopted",
            outcome_ids=outcome_ids,
            output_key=key,
            note=(note or "").strip()[:1000],
            evidence_id=str(evidence.get("evidence_id") or ""),
            idempotency_key=(
                f"output-adopted:{session_id}:{key}:"
                f"{str(evidence.get('evidence_id') or '')}"
            ),
        )
        continuation = (
            session.get("continuation_context")
            if isinstance(session.get("continuation_context"), dict)
            else {}
        )
        if (
            continuation.get("hit")
            and key in {"context_pack", "codex_continuation_prompt"}
        ):
            record_value_event(
                session_id,
                "continuation_used",
                outcome_ids=outcome_ids,
                output_key=key,
                related_session_id=str(
                    continuation.get("previous_session_id") or ""
                ),
                evidence_id=str(evidence.get("evidence_id") or ""),
                idempotency_key=f"continuation-used:{session_id}:{key}",
            )
    except Exception as exc:
        logger.warning("[WorkLedger] output value event skipped: %s", exc)
    _record_kernel_event(
        "work_output_adopted",
        session_id,
        {
            "session": _session_index_row(session),
            "output_key": key,
            "output_path": output.get("path"),
            "evidence_id": evidence.get("evidence_id"),
            "adopted_by": (adopted_by or "user").strip()[:80],
            "value_event_id": (
                (value_result.get("event") or {}).get("value_event_id")
                if value_result
                else ""
            ),
        },
    )
    return {
        **evidence,
        "value_event": value_result.get("event") if value_result else None,
    }


def record_work_process_candidate_feedback(
    session_id: str,
    file_path: str,
    *,
    action: str,
    note: str = "",
    imported_evidence_id: str = "",
    max_chars: int = 4000,
) -> dict[str, Any]:
    """Record user feedback for an auto-discovered work-process candidate."""

    session = _load_session(session_id)
    clean_action = str(action or "").strip().lower()
    if clean_action not in {"accepted", "rejected", "blocked"}:
        raise ValueError("candidate feedback action must be accepted, rejected, or blocked")
    clean_path = str(file_path or "").strip()
    if not clean_path:
        raise ValueError("candidate file_path is required")
    path = Path(clean_path).expanduser()
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path
    text_preview = ""
    safety: dict[str, Any] = {"ok": True, "blocked": False, "types": [], "counts": {}}
    if resolved.is_file():
        excerpt = _read_candidate_file_excerpt(resolved, max_chars=max_chars)
        safety = scan_sensitive_material(excerpt)
        text_preview = redact_sensitive_material(excerpt)[:max_chars]
    root_reason = _infer_candidate_root_reason(resolved, session)
    quality_key = _candidate_quality_key(resolved, root_reason)
    trust_level = "user_confirmed" if clean_action == "accepted" else "user_rejected"
    evidence = append_evidence(
        session_id,
        source="work_process_candidate_feedback",
        summary=f"Work process candidate {clean_action}: {resolved.name}",
        payload={
            "action": clean_action,
            "file_path": str(resolved),
            "root_reason": root_reason,
            "quality_key": quality_key,
            "note": (note or "").strip()[:1000],
            "imported_evidence_id": imported_evidence_id,
            "text_preview": text_preview,
            "safety": safety,
        },
        trust_level=trust_level,
        source_refs=[
            {
                "type": "work_process_candidate",
                "session_id": session_id,
                "path": str(resolved),
                "imported_evidence_id": imported_evidence_id,
            }
        ],
    )
    _record_kernel_event(
        "work_process_candidate_feedback_recorded",
        session_id,
        {
            "session": _session_index_row(session),
            "action": clean_action,
            "file_path": str(resolved),
            "quality_key": quality_key,
            "evidence_id": evidence.get("evidence_id"),
            "imported_evidence_id": imported_evidence_id,
        },
    )
    return evidence


def adopt_work_process_candidate(
    session_id: str,
    file_path: str,
    *,
    adopted_by: str = "user",
    note: str = "",
    generate_outputs_after: bool = True,
) -> dict[str, Any]:
    """Import a discovered candidate and mark it as user-confirmed evidence."""

    session = _load_session(session_id)
    clean_path = str(file_path or "").strip()
    if not clean_path:
        raise ValueError("candidate file_path is required")
    raw_process, _source = _load_work_process_material(text="", file_path=clean_path)
    safety = scan_sensitive_material(raw_process)
    if safety.get("blocked"):
        blocked = record_work_process_candidate_feedback(
            session_id,
            clean_path,
            action="blocked",
            note=f"blocked before adoption by {adopted_by}: {','.join(safety.get('types', []))}",
        )
        raise ValueError(f"candidate import blocked by sensitive material: {','.join(safety.get('types', []))}; evidence={blocked.get('evidence_id')}")
    sanitized = redact_sensitive_material(raw_process)
    imported = import_ai_work_process(
        session_id,
        text=sanitized,
        file_path="",
        tool_name=_infer_trace_tool(raw_process, clean_path),
        trace_kind="candidate_adoption_import",
        auto_collect=False,
        generate_outputs_after=False,
    )
    imported_evidence_id = str((imported.get("evidence") or {}).get("evidence_id") or "") if isinstance(imported, dict) else ""
    feedback = record_work_process_candidate_feedback(
        session_id,
        clean_path,
        action="accepted",
        note=(note or f"accepted by {adopted_by}").strip(),
        imported_evidence_id=imported_evidence_id,
    )
    outputs = generate_work_outputs(session_id) if generate_outputs_after else {}
    _record_kernel_event(
        "work_process_candidate_adopted",
        session_id,
        {
            "session": _session_index_row(session),
            "file_path": clean_path,
            "imported_evidence_id": imported_evidence_id,
            "feedback_evidence_id": feedback.get("evidence_id"),
            "output_keys": sorted(outputs.keys()),
        },
    )
    return {"session": _load_session(session_id), "imported": imported, "feedback": feedback, "outputs": outputs}


def status() -> dict[str, Any]:
    active = get_active_session()
    sessions = list_sessions(30)
    try:
        from l3_node.work_ledger_project_memory import project_memory_status

        project_memory = project_memory_status()
    except Exception:
        project_memory = {}
    return {
        "home": str(work_ledger_home()),
        "active_session": active,
        "recent_sessions": sessions,
        "project_memory": project_memory,
        "counts": {
            "sessions": len(_load_index()),
            "active": 1 if active else 0,
        },
    }


def generate_work_outputs(session_id: str | None = None) -> dict[str, str]:
    session = _resolve_session(session_id)
    sid = str(session["session_id"])
    try:
        from l3_node.work_ledger_codex import record_codex_work_chain_plan

        record_codex_work_chain_plan(sid, phase="continuation")
    except Exception as exc:
        logger.warning("[WorkLedger] Codex continuation planning skipped: %s", exc)
    evidence = load_evidence(sid, 1000)
    work_review = build_work_review(session, evidence)
    work_report_summary = build_itemized_work_report(session, evidence)
    context_pack = build_task_context_pack(session, evidence)
    report = build_daily_report(session, evidence)
    prompt = build_continuation_prompt(session, evidence)
    git_latest = _latest_payload(evidence, "git_snapshot")
    files_latest = _latest_payload(evidence, "file_scan")
    notes = [ev for ev in evidence if ev.get("source") == "manual_note"]
    team_lark_brief = build_team_lark_brief(session, evidence)
    weekly_report = build_weekly_report(session, evidence)
    performance_entries = build_performance_entries(session, evidence)
    methodology_candidates = build_methodology_candidates(session, evidence)
    lark_brief = _build_lark_short(
        session,
        evidence,
        _extract_changed_files(git_latest, files_latest),
        notes,
    )
    out_dir = _outputs_dir() / sid
    out_dir.mkdir(parents=True, exist_ok=True)
    work_review_path = out_dir / "work_review.md"
    work_report_summary_path = out_dir / "work_report_summary.md"
    context_pack_path = out_dir / "context_pack.md"
    report_path = out_dir / "daily_report.md"
    prompt_path = out_dir / "codex_continuation_prompt.md"
    lark_brief_path = out_dir / "lark_brief.txt"
    team_lark_path = out_dir / "team_lark_brief.md"
    weekly_path = out_dir / "weekly_report.md"
    performance_path = out_dir / "performance_entries.md"
    methodology_path = out_dir / "methodology_candidates.md"
    work_review_path.write_text(work_review.strip() + "\n", encoding="utf-8")
    work_report_summary_path.write_text(
        work_report_summary.strip() + "\n",
        encoding="utf-8",
    )
    context_pack_path.write_text(context_pack.strip() + "\n", encoding="utf-8")
    report_path.write_text(report, encoding="utf-8")
    prompt_path.write_text(prompt, encoding="utf-8")
    lark_brief_path.write_text(lark_brief.strip() + "\n", encoding="utf-8")
    team_lark_path.write_text(team_lark_brief.strip() + "\n", encoding="utf-8")
    weekly_path.write_text(weekly_report.strip() + "\n", encoding="utf-8")
    performance_path.write_text(performance_entries.strip() + "\n", encoding="utf-8")
    methodology_path.write_text(methodology_candidates.strip() + "\n", encoding="utf-8")
    session["output_paths"] = {
        "work_review": str(work_review_path),
        "work_report_summary": str(work_report_summary_path),
        "context_pack": str(context_pack_path),
        "daily_report": str(report_path),
        "codex_continuation_prompt": str(prompt_path),
        "lark_brief": str(lark_brief_path),
        "team_lark_brief": str(team_lark_path),
        "weekly_report": str(weekly_path),
        "performance_entries": str(performance_path),
        "methodology_candidates": str(methodology_path),
    }
    llm_result = _try_generate_llm_refined_outputs(
        session=session,
        evidence=evidence,
        baseline_report=report,
        baseline_prompt=prompt,
        out_dir=out_dir,
    )
    if llm_result.get("ok") and isinstance(llm_result.get("paths"), dict):
        session["output_paths"].update(llm_result["paths"])
    elif llm_result:
        session["output_paths"]["llm_quality_report"] = str(llm_result.get("quality_report_path") or "")
    _save_session(session)
    append_evidence(
        sid,
        source="work_output",
        summary="已生成工作日报和 Codex/Cursor 续写 Prompt",
        payload={"output_paths": session["output_paths"], "llm_refinement": llm_result},
        trust_level="system_observed",
    )
    return dict(session["output_paths"])


def build_work_review(session: dict[str, Any], evidence: list[dict[str, Any]]) -> str:
    git_latest = _latest_payload(evidence, "git_snapshot")
    files_latest = _latest_payload(evidence, "file_scan")
    snippets_latest = _latest_payload(evidence, "file_content_snippets")
    notes = [ev for ev in evidence if ev.get("source") == "manual_note"]
    ai_traces = [ev for ev in evidence if ev.get("source") == "ai_work_trace"]
    changed_files = _extract_changed_files(git_latest, files_latest)
    risks = snippets_latest.get("risk_candidates", []) if isinstance(snippets_latest, dict) else []
    ai_buckets = _collect_ai_trace_buckets(ai_traces)
    title = session.get("title") or "未命名工作"
    goal = session.get("user_goal") or title
    completed = _dedupe_nonempty(
        ai_buckets.get("actions", [])[-8:]
        + ai_buckets.get("decisions", [])[-8:]
        + [str(ev.get("summary") or "") for ev in notes[-8:]]
    )
    blockers = _dedupe_nonempty(
        ai_buckets.get("failures", [])[-8:]
        + [f"{item.get('path')}:{item.get('line')} {item.get('text')}" for item in risks[:8]]
    )
    next_steps = _dedupe_nonempty(ai_buckets.get("next_steps", [])[-8:])
    if not next_steps:
        next_steps = [
            "先核对今天的文件改动和用户补充记录是否完整。",
            "补充关键验证结果，再把可复用经验沉淀到方法论候选。",
        ]
    methodology = _build_methodology_review_points(notes, ai_buckets, risks)
    lines = [
        f"# 工作复盘七问：{title}",
        "",
        f"- 任务 ID：`{session.get('session_id')}`",
        f"- 项目：{session.get('project_name') or '未绑定'}",
        f"- 路径：`{session.get('project_path') or '未绑定'}`",
        f"- 证据边界：只基于 Git / 文件扫描 / 用户补充 / AI 过程导入 / 风险候选生成。",
        "",
        "## 1. 今天主要做了什么",
        "",
        f"- {goal}",
        "",
        "## 2. 改了哪些模块",
        "",
    ]
    if changed_files:
        for item in changed_files[:40]:
            lines.append(f"- `{item.get('status') or 'modified'}` `{item.get('path')}`")
        if len(changed_files) > 40:
            lines.append(f"- 另有 {len(changed_files) - 40} 个文件未展开。")
    else:
        lines.append("- 暂未从 Git / 文件扫描中发现明确改动。")
    lines.extend(["", "## 3. 哪些任务完成了", ""])
    if completed:
        for item in completed[:16]:
            lines.append(f"- {item}")
    else:
        lines.append("- 暂未发现明确完成记录；建议结束任务前补充一句“今天确认完成了什么”。")
    lines.extend(["", "## 4. 哪些问题卡住了", ""])
    if blockers:
        for item in blockers[:16]:
            lines.append(f"- {item}")
    else:
        lines.append("- 暂未发现明确阻塞；仍需结合测试结果确认。")
    lines.extend(["", "## 5. 明天接着做什么", ""])
    for item in next_steps[:12]:
        lines.append(f"- {item}")
    lines.extend(["", "## 6. 这段内容怎么发日报", ""])
    lines.append(_build_lark_short(session, evidence, changed_files, notes))
    lines.extend(["", "## 7. 这段内容怎么沉淀成方法论", ""])
    for item in methodology:
        lines.append(f"- {item}")
    return "\n".join(lines)


def build_task_context_pack(session: dict[str, Any], evidence: list[dict[str, Any]]) -> str:
    """Build a compact handoff pack for tomorrow's Codex/Cursor continuation."""

    git_latest = _latest_payload(evidence, "git_snapshot")
    files_latest = _latest_payload(evidence, "file_scan")
    snippets_latest = _latest_payload(evidence, "file_content_snippets")
    notes = [ev for ev in evidence if ev.get("source") == "manual_note"]
    ai_traces = [ev for ev in evidence if ev.get("source") == "ai_work_trace"]
    ai_buckets = _collect_ai_trace_buckets(ai_traces)
    changed_files = _extract_changed_files(git_latest, files_latest)
    risks = snippets_latest.get("risk_candidates", []) if isinstance(snippets_latest, dict) else []
    snippets = snippets_latest.get("snippets", []) if isinstance(snippets_latest, dict) else []
    title = session.get("title") or "未命名工作任务"
    goal = session.get("user_goal") or title
    completed = _dedupe_nonempty(
        ai_buckets.get("actions", [])[-10:]
        + ai_buckets.get("decisions", [])[-8:]
        + [str(ev.get("summary") or "") for ev in notes[-8:]]
    )
    blockers = _dedupe_nonempty(
        ai_buckets.get("failures", [])[-8:]
        + [f"{item.get('path')}:{item.get('line')} {item.get('text')}" for item in risks[:8]]
    )
    next_steps = _dedupe_nonempty(ai_buckets.get("next_steps", [])[-10:])
    if not next_steps:
        next_steps = [
            "先核对当前 Git / 文件证据是否完整。",
            "再补齐缺失的测试、日志或用户确认记录。",
            "最后把可复用经验采纳回 Work Ledger，供后续召回。",
        ]
    project_memory = _project_memory_for_session(session)
    lines = [
        f"# Task Context Pack: {title}",
        "",
        "这份上下文包用于让明天的自己、Codex 或 Cursor 快速续上任务；内容只基于 Work Ledger 已记录证据。",
        "",
        "## 1. 当前任务",
        "",
        f"- Session: `{session.get('session_id')}`",
        f"- Project: {session.get('project_name') or '未绑定'}",
        f"- Path: `{session.get('project_path') or '未绑定'}`",
        f"- Goal: {goal}",
        f"- Status: {session.get('status') or '-'}",
        "",
        "## 2. 项目记忆",
        "",
    ]
    if project_memory:
        lines.extend(
            [
                f"- Alias: {project_memory.get('alias') or project_memory.get('project_name') or '-'}",
                f"- Remembered path: `{project_memory.get('project_path') or '-'}`",
                f"- Last session: `{project_memory.get('session_id') or project_memory.get('last_session_id') or '-'}`",
                f"- Confidence: {project_memory.get('confidence', '-')}",
            ]
        )
    else:
        lines.append("- 暂无可用项目记忆；本次任务会在开始/结束时继续沉淀。")
    lines.extend(["", "## 3. 已经推进的内容", ""])
    if completed:
        for item in completed[:14]:
            lines.append(f"- {item}")
    else:
        lines.append("- 还没有足够的用户确认或 AI trace 来判断已完成事项。")
    lines.extend(["", "## 4. 关键文件线索", ""])
    if changed_files:
        for item in changed_files[:30]:
            lines.append(f"- `{item.get('status') or 'modified'}` `{item.get('path')}`")
    else:
        lines.append("- Git / 文件扫描暂未发现明确改动文件。")
    lines.extend(["", "## 5. 代码/文档片段线索", ""])
    if snippets:
        for item in snippets[:8]:
            excerpt = str(item.get("excerpt") or "").strip().replace("\n", " ")[:260]
            lines.append(f"- `{item.get('path')}`: {excerpt}")
    else:
        lines.append("- 暂无可读片段。")
    lines.extend(["", "## 6. 风险和阻塞", ""])
    if blockers:
        for item in blockers[:12]:
            lines.append(f"- {item}")
    else:
        lines.append("- 暂无明确阻塞；仍需以后续测试和用户确认补强。")
    lines.extend(["", "## 7. 下一轮建议动作", ""])
    for index, item in enumerate(next_steps[:10], start=1):
        lines.append(f"{index}. {item}")
    lines.extend(
        [
            "",
            "## 8. 可直接发给 Codex/Cursor 的下一轮任务书",
            "",
            "请继续本地项目任务，严格基于以下事实推进，不要编造：",
            f"- 项目路径：`{session.get('project_path') or '未绑定'}`",
            f"- 当前目标：{goal}",
            f"- 相关文件：{', '.join(str(item.get('path') or '') for item in changed_files[:12] if item.get('path')) or '暂无明确文件，先读取 Git 状态和最近文件'}",
            f"- 已知风险：{'; '.join(blockers[:6]) if blockers else '暂无明确阻塞，先补测试证据'}",
            "",
            "请先读取 Git 状态、相关文件和现有测试，再给出最小可验证修改；完成后说明验证命令和结果。",
            "",
            "## 9. 证据边界",
            "",
            "- user_confirmed 内容优先可信。",
            "- system_observed 只代表本机事实扫描。",
            "- AI trace 代表用户导入的过程记录，不等同于最终事实，关键结论仍需代码、文件、日志或用户确认支撑。",
        ]
    )
    return "\n".join(lines)


def _safe_session_fact_context(session: dict[str, Any]) -> dict[str, Any]:
    session_id = str(session.get("session_id") or "").strip()
    if not session_id:
        return {
            "all_facts": [],
            "new_facts": [],
            "continued_facts": [],
            "state_changed_facts": [],
            "predecessor_facts": [],
            "completed_this_session": [],
            "reopened_this_session": [],
            "superseded_this_session": [],
            "prior_open_facts": [],
            "review_pending": [],
            "summary": {},
        }
    try:
        from l3_node.work_ledger_facts import get_session_fact_context

        return get_session_fact_context(session_id)
    except Exception:
        return {
            "all_facts": [],
            "new_facts": [],
            "continued_facts": [],
            "state_changed_facts": [],
            "predecessor_facts": [],
            "completed_this_session": [],
            "reopened_this_session": [],
            "superseded_this_session": [],
            "prior_open_facts": [],
            "review_pending": [],
            "summary": {},
        }


def _safe_session_outcome_context(session: dict[str, Any]) -> dict[str, Any]:
    session_id = str(session.get("session_id") or "").strip()
    project_path = str(session.get("project_path") or "").strip()
    if not session_id or not project_path:
        return {
            "graph": {},
            "active_outcomes": [],
            "outcomes_this_session": [],
            "methodology_pending": [],
            "methodology_approved": [],
            "summary": {},
        }
    try:
        from l3_node.work_ledger_outcomes import get_session_outcome_context

        return get_session_outcome_context(session_id)
    except Exception:
        return {
            "graph": {},
            "active_outcomes": [],
            "outcomes_this_session": [],
            "methodology_pending": [],
            "methodology_approved": [],
            "summary": {},
        }


def _safe_session_value_context(session: dict[str, Any]) -> dict[str, Any]:
    session_id = str(session.get("session_id") or "").strip()
    project_path = str(session.get("project_path") or "").strip()
    if not session_id or not project_path:
        return {
            "events_this_session": [],
            "outcome_values_this_session": [],
            "summary": {},
        }
    try:
        from l3_node.work_ledger_value import get_session_value_context

        return get_session_value_context(session_id)
    except Exception:
        return {
            "events_this_session": [],
            "outcome_values_this_session": [],
            "summary": {},
        }


def build_daily_report(session: dict[str, Any], evidence: list[dict[str, Any]]) -> str:
    git_latest = _latest_payload(evidence, "git_snapshot")
    files_latest = _latest_payload(evidence, "file_scan")
    snippets_latest = _latest_payload(evidence, "file_content_snippets")
    notes = [ev for ev in evidence if ev.get("source") == "manual_note"]
    ai_traces = [ev for ev in evidence if ev.get("source") == "ai_work_trace"]
    trust_summary = _summarize_evidence_trust(evidence)
    fact_context = _safe_session_fact_context(session)
    outcome_context = _safe_session_outcome_context(session)
    value_context = _safe_session_value_context(session)
    changed_files = _extract_changed_files(git_latest, files_latest)
    title = session.get("title") or "未命名任务"
    lines = [
        f"# 工作日报：{title}",
        "",
        f"- 任务 ID：`{session.get('session_id')}`",
        f"- 项目：{session.get('project_name') or '未绑定'}",
        f"- 路径：`{session.get('project_path') or '未绑定'}`",
        f"- 开始：{session.get('start_time') or '-'}",
        f"- 结束：{session.get('end_time') or '进行中'}",
        "",
        "## 1. 今日目标",
        "",
        session.get("user_goal") or session.get("title") or "未填写",
        "",
        "## 2. 真实证据摘要",
        "",
        f"- Git 分支：{git_latest.get('branch') or '未知'}",
        f"- Git 状态：{git_latest.get('status_summary') or '未采集'}",
        f"- 任务期内文件扫描：{len(files_latest.get('recent_files', [])) if isinstance(files_latest, dict) else 0} 个最近修改文件",
        f"- 文件内容片段：{len(snippets_latest.get('snippets', [])) if isinstance(snippets_latest, dict) else 0} 个",
        f"- 风险候选：{len(snippets_latest.get('risk_candidates', [])) if isinstance(snippets_latest, dict) else 0} 条",
        f"- 手动补充：{len(notes)} 条",
        f"- AI 工具过程记录：{len(ai_traces)} 条",
        "",
        "证据可信度分布：",
        f"- 用户明确确认：{trust_summary.get('user_confirmed', 0)} 条",
        f"- 系统观察事实：{trust_summary.get('system_observed', 0)} 条",
        f"- 系统推断：{trust_summary.get('system_inferred', 0)} 条",
        f"- 待确认：{trust_summary.get('pending_confirmation', 0)} 条",
    ]
    lines.extend(["", "## 2.1 本次确认的项目事实", ""])
    new_facts = fact_context.get("new_facts") or []
    continued_facts = fact_context.get("continued_facts") or []
    if new_facts:
        for fact in new_facts[:20]:
            state = str(fact.get("state") or "completed")
            label = "新增完成" if state == "completed" else "新增未闭环"
            lines.append(
                f"- [{label}] {fact.get('canonical_summary')} "
                f"（来源 {len(fact.get('source_types') or [])} 类，"
                f"证据出现 {fact.get('occurrence_count') or 1} 次）"
            )
    else:
        lines.append("- 本次暂无新确认的项目事实。")
    if continued_facts:
        lines.extend(["", "本次再次出现、但不重复计为新成果的历史事实："])
        for fact in continued_facts[:12]:
            lines.append(
                f"- [持续事实] {fact.get('canonical_summary')} "
                f"（累计出现 {fact.get('occurrence_count') or 1} 次）"
            )
    completed_this_session = [
        fact
        for fact in fact_context.get("completed_this_session") or []
        if fact.get("first_session_id") != session.get("session_id")
    ]
    reopened_this_session = fact_context.get("reopened_this_session") or []
    superseded_this_session = fact_context.get("superseded_this_session") or []
    if completed_this_session or reopened_this_session or superseded_this_session:
        lines.extend(["", "本次事实状态变化："])
        for fact in completed_this_session[:12]:
            lines.append(f"- [本次完成] {fact.get('canonical_summary')}")
        for fact in reopened_this_session[:12]:
            lines.append(f"- [回归重开] {fact.get('canonical_summary')}")
        for fact in superseded_this_session[:12]:
            lines.append(
                f"- [已被替代] {fact.get('canonical_summary')}"
                f" -> {fact.get('superseded_by_fact_id') or '新方案'}"
            )
    review_pending = fact_context.get("review_pending") or []
    if review_pending:
        lines.append(
            f"- 有 {len(review_pending)} 组相似事实等待确认，"
            "在确认前不会静默合并或重复计算。"
        )
    lines.extend(["", "## 2.2 本次可计入成果的验证结果", ""])
    outcomes_this_session = outcome_context.get("outcomes_this_session") or []
    if outcomes_this_session:
        for outcome in outcomes_this_session[:20]:
            lines.append(
                f"- [已验证成果] {outcome.get('summary')} "
                f"（完成依据：{outcome.get('completion_reason') or '用户确认完成'}）"
            )
    else:
        lines.append(
            "- 本次没有同时满足“用户确认、状态完成、存在完成 transition”的成果；"
            "过程记录不会冒充成果。"
        )
    lines.extend(["", "## 2.3 成果交付与实际价值", ""])
    outcome_values = value_context.get("outcome_values_this_session") or []
    valued = [
        row
        for row in outcome_values
        if row.get("value_stage") != "completed" or row.get("latest_feedback")
    ]
    if valued:
        stage_labels = {
            "impact": "已产生影响",
            "adopted": "已采用",
            "delivered": "已交付",
            "completed": "已完成",
        }
        for outcome in valued[:20]:
            stage = str(outcome.get("value_stage") or "completed")
            feedback = str(outcome.get("latest_feedback") or "")
            lines.append(
                f"- [{stage_labels.get(stage, '已完成')}] "
                f"{outcome.get('summary')}"
                f"{f'（用户反馈：{feedback}）' if feedback else ''}"
            )
    else:
        lines.append("- 本次成果尚未收到交付、采用或影响反馈。")
    lines.extend(["", "## 3. 涉及文件", ""])
    if changed_files:
        for item in changed_files[:80]:
            status_text = item.get("status") or "modified"
            path_text = item.get("path") or ""
            lines.append(f"- `{status_text}` `{path_text}`")
        if len(changed_files) > 80:
            lines.append(f"- 另有 {len(changed_files) - 80} 个文件未展开。")
    else:
        lines.append("- 暂未从 Git / 文件扫描中发现明确改动。")
    lines.extend(["", "## 4. 文件内容线索", ""])
    snippets = snippets_latest.get("snippets", []) if isinstance(snippets_latest, dict) else []
    if snippets:
        for item in snippets[:12]:
            excerpt = str(item.get("excerpt") or "").strip()
            preview = excerpt.replace("\n", " ")[:220]
            risk = int(item.get("risk_line_count") or 0)
            suffix = f"；风险候选 {risk} 条" if risk else ""
            lines.append(f"- `{item.get('path')}`：{preview}{suffix}")
    else:
        lines.append("- 暂无可读文件片段。")
    risks = snippets_latest.get("risk_candidates", []) if isinstance(snippets_latest, dict) else []
    lines.extend(["", "## 5. 风险候选", ""])
    if risks:
        for item in risks[:20]:
            lines.append(f"- `{item.get('path')}:{item.get('line')}` {item.get('text')}")
    else:
        lines.append("- 暂未从文件片段中发现 TODO / 失败 / 异常等风险词。")
    lines.extend(["", "## 6. 关键过程记录", ""])
    if notes:
        for ev in notes[-20:]:
            lines.append(f"- {ev.get('summary')}")
    else:
        lines.append("- 暂无用户手动补充。")
    lines.extend(["", "## 7. AI 工具过程导入", ""])
    if ai_traces:
        for ev in ai_traces[-12:]:
            payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
            tool = payload.get("tool_name") or "AI 工具"
            text = str(payload.get("text") or ev.get("summary") or "").replace("\n", " ")[:240]
            analysis_lines = _format_ai_trace_analysis_lines(payload.get("analysis"), indent="  ")
            if analysis_lines:
                lines.extend(analysis_lines)
            lines.append(f"- {tool}：{text}")
    else:
        lines.append("- 暂无 Codex / Cursor / 其他 AI 工具过程导入。")
    lines.extend(
        [
            "",
            "## 8. 风险与未完成点",
            "",
            "- 本报告为证据驱动基础版；未从测试命令或用户补充中识别到的风险不会被编造。",
            "- 如果需要更完整复盘，请在任务过程中补充关键失败原因、取舍和验证结论。",
            "",
            "## 9. 明日建议",
            "",
            "- 先根据上方文件清单和手动记录确认今天产出是否完整。",
            "- 对未完成风险补充验证命令或日志证据。",
            "- 使用下方续写 Prompt 让 Codex / Cursor 接着推进。",
            "",
            "## 10. 可发 Lark 短版",
            "",
            _build_lark_short(session, evidence, changed_files, notes),
            "",
        ]
    )
    return "\n".join(lines)


def build_continuation_prompt(session: dict[str, Any], evidence: list[dict[str, Any]]) -> str:
    git_latest = _latest_payload(evidence, "git_snapshot")
    files_latest = _latest_payload(evidence, "file_scan")
    snippets_latest = _latest_payload(evidence, "file_content_snippets")
    notes = [ev for ev in evidence if ev.get("source") == "manual_note"]
    ai_traces = [ev for ev in evidence if ev.get("source") == "ai_work_trace"]
    fact_context = _safe_session_fact_context(session)
    changed_files = _extract_changed_files(git_latest, files_latest)
    lines = [
        f"请继续本地项目任务：{session.get('title')}",
        "",
        f"项目路径：{session.get('project_path') or '未绑定'}",
        "",
        "当前目标：",
        session.get("user_goal") or session.get("title") or "未填写",
        "",
        "已观察到的真实证据：",
        f"- Git 分支：{git_latest.get('branch') or '未知'}",
        f"- Git 状态：{git_latest.get('status_summary') or '未采集'}",
        "",
        "相关文件：",
    ]
    if changed_files:
        for item in changed_files[:60]:
            lines.append(f"- {item.get('status') or 'modified'} {item.get('path')}")
    else:
        lines.append("- 暂未发现明确改动文件。")
    lines.extend(["", "项目事实链："])
    current_facts = (
        list(fact_context.get("new_facts") or [])
        + list(fact_context.get("continued_facts") or [])
    )
    if current_facts:
        for fact in current_facts[:16]:
            lines.append(
                f"- [{fact.get('state') or 'completed'}] "
                f"{fact.get('canonical_summary')} "
                f"（累计证据 {fact.get('occurrence_count') or 1} 次）"
            )
            if fact.get("failure_attempts"):
                latest_failure = fact.get("failure_attempts")[-1]
                lines.append(f"  - 最近失败：{latest_failure.get('text')}")
            if fact.get("decisions"):
                latest_decision = fact.get("decisions")[-1]
                lines.append(f"  - 已确认决策：{latest_decision.get('text')}")
            if fact.get("next_actions"):
                latest_action = fact.get("next_actions")[-1]
                lines.append(f"  - 后续动作：{latest_action.get('text')}")
    else:
        lines.append("- 当前任务暂无用户确认的项目事实。")
    prior_open_facts = fact_context.get("prior_open_facts") or []
    if prior_open_facts:
        lines.extend(["", "以前任务尚未闭环的事实："])
        for fact in prior_open_facts[:12]:
            lines.append(f"- {fact.get('canonical_summary')}")
    predecessor_facts = fact_context.get("predecessor_facts") or []
    if predecessor_facts:
        lines.extend(["", "当前方案替代的历史事实与决策："])
        for fact in predecessor_facts[:12]:
            lines.append(
                f"- [{fact.get('state') or 'superseded'}] "
                f"{fact.get('canonical_summary')}"
            )
            if fact.get("decisions"):
                lines.append(f"  - 历史决策：{fact.get('decisions')[-1].get('text')}")
            if fact.get("failure_attempts"):
                lines.append(
                    f"  - 历史失败：{fact.get('failure_attempts')[-1].get('text')}"
                )
            if fact.get("next_actions"):
                lines.append(
                    f"  - 原后续动作：{fact.get('next_actions')[-1].get('text')}"
                )
    if fact_context.get("review_pending"):
        lines.append(
            f"- 有 {len(fact_context.get('review_pending') or [])} 组相似事实待用户确认，"
            "不要自行合并。"
        )
    lines.extend(["", "用户确认过的关键记录："])
    if notes:
        for ev in notes[-20:]:
            lines.append(f"- {ev.get('summary')}")
    else:
        lines.append("- 暂无。")
    lines.extend(["", "文件内容片段："])
    snippets = snippets_latest.get("snippets", []) if isinstance(snippets_latest, dict) else []
    if snippets:
        for item in snippets[:10]:
            excerpt = str(item.get("excerpt") or "").strip().replace("\n", " ")[:320]
            lines.append(f"- {item.get('path')}: {excerpt}")
    else:
        lines.append("- 暂无。")
    lines.extend(["", "风险候选："])
    risks = snippets_latest.get("risk_candidates", []) if isinstance(snippets_latest, dict) else []
    if risks:
        for item in risks[:16]:
            lines.append(f"- {item.get('path')}:{item.get('line')} {item.get('text')}")
    else:
        lines.append("- 暂无。")
    lines.extend(["", "已导入的 Codex / Cursor / AI 工具过程："])
    if ai_traces:
        for ev in ai_traces[-10:]:
            payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
            tool = payload.get("tool_name") or "AI 工具"
            text = str(payload.get("text") or ev.get("summary") or "").replace("\n", " ")[:360]
            analysis_lines = _format_ai_trace_analysis_lines(payload.get("analysis"), indent="  ")
            lines.append(f"- {tool}: {text}")
            if analysis_lines:
                lines.extend(analysis_lines)
    else:
        lines.append("- 暂无。")
    lines.extend(
        [
            "",
            "下一步请做：",
            "1. 先读取上述相关文件和 Git 状态，不要凭空假设。",
            "2. 判断当前任务完成到哪一步。",
            "3. 找出未完成点和风险。",
            "4. 给出最小、可验证的下一步实现方案。",
            "5. 修改代码后运行针对性测试，并说明验证结果。",
            "",
            "要求：所有结论必须基于本地文件、Git diff、日志或用户确认记录；不要编造。",
        ]
    )
    return "\n".join(lines)


def build_team_lark_brief(session: dict[str, Any], evidence: list[dict[str, Any]]) -> str:
    git_latest = _latest_payload(evidence, "git_snapshot")
    files_latest = _latest_payload(evidence, "file_scan")
    snippets_latest = _latest_payload(evidence, "file_content_snippets")
    notes = [ev for ev in evidence if ev.get("source") == "manual_note"]
    ai_traces = [ev for ev in evidence if ev.get("source") == "ai_work_trace"]
    fact_context = _safe_session_fact_context(session)
    changed_files = _extract_changed_files(git_latest, files_latest)
    risks = snippets_latest.get("risk_candidates", []) if isinstance(snippets_latest, dict) else []
    title = session.get("title") or "今日工作"
    lines = [
        f"【{title}｜工作简报】",
        f"目标：{session.get('user_goal') or title}",
        f"证据：{len(changed_files)} 个文件变化，{len(notes)} 条人工记录，{len(ai_traces)} 条 AI 过程记录。",
    ]
    new_facts = fact_context.get("new_facts") or []
    completed_facts = [
        fact
        for fact in (
            list(new_facts)
            + list(fact_context.get("completed_this_session") or [])
        )
        if fact.get("state") == "completed"
    ]
    completed_facts = list(
        {
            str(fact.get("fact_id") or index): fact
            for index, fact in enumerate(completed_facts)
        }.values()
    )
    if completed_facts:
        lines.append("本次确认成果：")
        for fact in completed_facts[:6]:
            lines.append(f"- {fact.get('canonical_summary')}")
    reopened_facts = fact_context.get("reopened_this_session") or []
    if reopened_facts:
        lines.append("风险变化：")
        for fact in reopened_facts[:4]:
            lines.append(f"- 已重新打开：{fact.get('canonical_summary')}")
    if changed_files:
        lines.append("主要改动：")
        for item in changed_files[:6]:
            lines.append(f"- {item.get('status') or 'modified'} {item.get('path')}")
    if notes:
        lines.append("关键记录：")
        for ev in notes[-4:]:
            lines.append(f"- {ev.get('summary')}")
    if risks:
        lines.append("风险提醒：")
        for item in risks[:4]:
            lines.append(f"- {item.get('path')}:{item.get('line')} {item.get('text')}")
    else:
        lines.append("风险提醒：暂未从证据中发现明确 TODO / failed / error 等风险词。")
    lines.append("依据：Git / 文件扫描 / 用户确认记录 / AI 过程导入。")
    return "\n".join(lines)


def build_weekly_report(session: dict[str, Any], evidence: list[dict[str, Any]]) -> str:
    snippets_latest = _latest_payload(evidence, "file_content_snippets")
    notes = [ev for ev in evidence if ev.get("source") == "manual_note"]
    ai_traces = [ev for ev in evidence if ev.get("source") == "ai_work_trace"]
    risks = snippets_latest.get("risk_candidates", []) if isinstance(snippets_latest, dict) else []
    outcome_context = _safe_session_outcome_context(session)
    outcomes = outcome_context.get("outcomes_this_session") or []
    fact_context = _safe_session_fact_context(session)
    title = session.get("title") or "工作任务"
    lines = [
        f"# 周报草稿：{session.get('project_name') or title}",
        "",
        "## 本周核心目标",
        "",
        f"- {session.get('user_goal') or title}",
        "",
        "## 已完成 / 推进事项",
        "",
    ]
    if outcomes:
        for outcome in outcomes[:20]:
            lines.append(
                f"- [已验证完成] {outcome.get('summary')} "
                f"（{outcome.get('completion_reason') or '用户确认完成'}）"
            )
    else:
        lines.append(
            "- 本任务没有可计入成果的已验证完成事实；文件变化和过程记录不作为成果。"
        )
    active_work = [
        fact
        for fact in fact_context.get("all_facts") or []
        if fact.get("state") in {"open", "in_progress", "reopened"}
    ]
    if active_work:
        lines.extend(["", "## 仍在推进", ""])
        for fact in active_work[:16]:
            lines.append(
                f"- [{fact.get('state')}] {fact.get('canonical_summary')}"
            )
    if notes:
        lines.extend(["", "## 关键过程与结论", ""])
        for ev in notes[-12:]:
            lines.append(f"- {ev.get('summary')}")
    if ai_traces:
        lines.extend(["", "## AI 协作过程", ""])
        for ev in ai_traces[-6:]:
            payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
            tool = payload.get("tool_name") or "AI 工具"
            text = str(payload.get("text") or ev.get("summary") or "").replace("\n", " ")[:180]
            lines.append(f"- {tool}: {text}")
            lines.extend(_format_ai_trace_analysis_lines(payload.get("analysis"), indent="  "))
    lines.extend(["", "## 风险与未完成", ""])
    if risks:
        for item in risks[:12]:
            lines.append(f"- `{item.get('path')}:{item.get('line')}` {item.get('text')}")
    else:
        lines.append("- 暂无明确风险候选；仍需补充真实测试结果。")
    lines.extend(["", "## 下周建议", ""])
    lines.append("- 先补齐本周关键改动的验证结果，再把可复用经验沉淀到知识库。")
    lines.append("- 对仍未闭环的风险点建立下一轮任务，并关联本周证据。")
    return "\n".join(lines)


def build_performance_entries(session: dict[str, Any], evidence: list[dict[str, Any]]) -> str:
    outcome_context = _safe_session_outcome_context(session)
    outcomes = outcome_context.get("outcomes_this_session") or []
    title = session.get("title") or "工作任务"
    lines = [
        f"# 绩效材料条目：{title}",
        "",
        "仅列出用户确认且已验证完成的项目事实；文件数量、聊天数量和原始证据数量不作为成果。",
        "",
    ]
    if outcomes:
        for outcome in outcomes[:20]:
            lines.append(
                f"- {outcome.get('summary')}。"
                f"验证结论：{outcome.get('completion_reason') or '用户确认完成'}。"
            )
    else:
        lines.append("- 本任务暂无符合绩效成果口径的已验证完成事实。")
    return "\n".join(lines)


def build_methodology_candidates(session: dict[str, Any], evidence: list[dict[str, Any]]) -> str:
    outcome_context = _safe_session_outcome_context(session)
    candidates = outcome_context.get("methodology_pending") or []
    approved = outcome_context.get("methodology_approved") or []
    lines = [
        f"# 可复用经验候选：{session.get('title') or '工作任务'}",
        "",
        "候选只来自可追溯的“失败 -> 决策 -> 动作 -> 再次完成”事实链。"
        "未经用户审查，不会进入长期方法论。",
        "",
    ]
    lines.extend(["## 待用户确认", ""])
    if candidates:
        for candidate in candidates[:16]:
            lines.extend(
                [
                    f"- {candidate.get('title')}",
                    f"  - 触发条件：{candidate.get('trigger')}",
                    f"  - 关键决策：{candidate.get('decision')}",
                    f"  - 执行动作：{candidate.get('action')}",
                    f"  - 验证结果：{candidate.get('result')}",
                    f"  - 候选 ID：`{candidate.get('candidate_id')}`",
                ]
            )
    else:
        lines.append("- 暂无同时满足可追溯和重复成功条件的方法论候选。")
    lines.extend(["", "## 已批准方法论", ""])
    if approved:
        for candidate in approved[:16]:
            lines.append(
                f"- {candidate.get('title')}：{candidate.get('decision')}；"
                f"{candidate.get('action')}"
            )
    else:
        lines.append("- 暂无用户批准的方法论。")
    return "\n".join(lines)


def build_multi_day_weekly_report(index: dict[str, Any], *, title: str | None = None) -> str:
    days = int(index.get("window_days") or 7)
    sessions = index.get("sessions") if isinstance(index.get("sessions"), list) else []
    adopted_outputs = index.get("adopted_outputs") if isinstance(index.get("adopted_outputs"), list) else []
    verified_outcomes = index.get("verified_outcomes") if isinstance(index.get("verified_outcomes"), list) else []
    valued_outcomes = index.get("valued_outcomes") if isinstance(index.get("valued_outcomes"), list) else []
    value_summary = index.get("value_summary") if isinstance(index.get("value_summary"), dict) else {}
    methodology = index.get("graph_methodology_candidates") if isinstance(index.get("graph_methodology_candidates"), list) else []
    approved_methodology = [row for row in methodology if row.get("status") == "approved"]
    pending_methodology = [row for row in methodology if row.get("status") == "pending_review"]
    notes = index.get("recent_notes") if isinstance(index.get("recent_notes"), list) else []
    ai_signals = index.get("recent_ai_signals") if isinstance(index.get("recent_ai_signals"), list) else []
    project_counts = index.get("project_counts") if isinstance(index.get("project_counts"), dict) else {}
    heading = title or f"最近 {days} 天工作周报"
    lines = [
        f"# {heading}",
        "",
        f"- 时间窗口：最近 {days} 天",
        f"- 聚合任务：{len(sessions)} 个",
        f"- 已验证成果：{len(verified_outcomes)} 条",
        f"- 已交付 / 已采用 / 有影响："
        f"{int(value_summary.get('delivered_outcome_count') or 0)} / "
        f"{int(value_summary.get('adopted_outcome_count') or 0)} / "
        f"{int(value_summary.get('impact_outcome_count') or 0)} 条",
        f"- 续作上下文实际使用率：{round(float(value_summary.get('continuation_use_rate') or 0) * 100)}%",
        f"- 用户采纳输出：{len(adopted_outputs)} 条（只作输出使用记录）",
        f"- 待审查方法论：{len(pending_methodology)} 条",
        f"- 生成时间：{index.get('generated_at') or _now_datetime().isoformat()}",
        "",
        "## 1. 工作分布",
        "",
    ]
    if project_counts:
        for name, count in sorted(project_counts.items(), key=lambda item: int(item[1]), reverse=True)[:12]:
            lines.append(f"- {name}: {count} 个任务")
    else:
        lines.append("- 暂无项目分布数据。")
    lines.extend(["", "## 2. 本期任务", ""])
    if sessions:
        for row in sessions[:30]:
            status = row.get("status") or "-"
            lines.append(
                f"- {row.get('title') or row.get('session_id')}（{status}，证据 {row.get('evidence_count') or 0} 条，"
                f"更新 {_ms_to_iso(row.get('updated_at_ms')) or '-'}）"
            )
    else:
        lines.append("- 暂无任务记录。")
    lines.extend(["", "## 3. 已验证成果与实际价值", ""])
    if valued_outcomes:
        stage_labels = {
            "impact": "已产生影响",
            "adopted": "已采用",
            "delivered": "已交付",
            "completed": "已完成",
        }
        for outcome in valued_outcomes[:30]:
            stage = str(outcome.get("value_stage") or "completed")
            feedback = str(outcome.get("latest_feedback") or "")
            lines.append(
                f"- [{stage_labels.get(stage, '已完成')}] {outcome.get('summary')} "
                f"（{outcome.get('completion_reason') or '用户确认完成'}"
                f"{f'；用户反馈 {feedback}' if feedback else ''}）"
            )
    elif verified_outcomes:
        for outcome in verified_outcomes[-30:]:
            lines.append(
                f"- [已完成] {outcome.get('summary')} "
                f"（{outcome.get('completion_reason') or '用户确认完成'}）"
            )
    else:
        lines.append("- 本期暂无满足用户确认与完成验证条件的成果。")
    lines.extend(["", "## 4. 关键过程记录", ""])
    if notes:
        for item in notes[-16:]:
            lines.append(f"- {item.get('title') or item.get('session_id')}: {item.get('summary')}")
    else:
        lines.append("- 暂无手动过程记录。")
    lines.extend(["", "## 5. 风险、决策与下一步", ""])
    if ai_signals:
        for item in ai_signals[-18:]:
            kind = item.get("kind") or "signal"
            lines.append(f"- [{kind}] {item.get('title') or item.get('session_id')}: {item.get('text')}")
    else:
        lines.append("- 暂无 AI 过程信号。")
    lines.extend(["", "## 6. 已批准方法论", ""])
    if approved_methodology:
        for item in approved_methodology[-10:]:
            lines.append(
                f"- {item.get('title')}：{item.get('decision')}；"
                f"{item.get('action')}"
            )
    else:
        lines.append("- 暂无用户批准的方法论。")
    if pending_methodology:
        lines.extend(["", "待审查方法论候选："])
        for item in pending_methodology[-10:]:
            lines.append(f"- {item.get('title')}（`{item.get('candidate_id')}`）")
    lines.extend(["", "## 7. 证据边界", ""])
    lines.append("- 成果只来自 user_confirmed 且存在 completed transition 的项目事实。")
    lines.append("- 周报按“影响 > 采用 > 交付 > 完成”排序；负面反馈只降低价值排序，不改写原始事实。")
    lines.append("- 续作命中只有在 Context Pack 或续写 Prompt 被实际采用后，才计入续作使用率。")
    lines.append("- 文件变化、聊天条数、证据数量和仅采纳的输出不会被当作成果。")
    lines.append("- 方法论必须可追溯到失败、决策、动作和完成结果，并经用户批准。")
    lines.append("- 未采集到的工作不会被编造，需要用户手动补充或导入 AI 过程记录。")
    return "\n".join(lines)


def _try_generate_llm_refined_outputs(
    *,
    session: dict[str, Any],
    evidence: list[dict[str, Any]],
    baseline_report: str,
    baseline_prompt: str,
    out_dir: Path,
) -> dict[str, Any]:
    try:
        from l3_node.work_ledger_llm import llm_refinement_enabled, refine_work_outputs_with_llm

        if not llm_refinement_enabled():
            return {"ok": False, "skipped": True, "reason": "llm_refinement_disabled_or_missing_key"}
        result = refine_work_outputs_with_llm(
            session=session,
            evidence=evidence,
            baseline_report=baseline_report,
            baseline_prompt=baseline_prompt,
        )
        quality_path = out_dir / "llm_quality_report.json"
        quality_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        result["quality_report_path"] = str(quality_path)
        if not result.get("ok"):
            return result
        outputs = result.get("outputs") if isinstance(result.get("outputs"), dict) else {}
        enhanced_report_path = out_dir / "enhanced_daily_report.md"
        enhanced_prompt_path = out_dir / "enhanced_continuation_prompt.md"
        lark_brief_path = out_dir / "lark_brief.txt"
        enhanced_report_path.write_text(str(outputs.get("daily_report") or "").strip() + "\n", encoding="utf-8")
        enhanced_prompt_path.write_text(str(outputs.get("continuation_prompt") or "").strip() + "\n", encoding="utf-8")
        lark_brief_path.write_text(str(outputs.get("lark_brief") or "").strip() + "\n", encoding="utf-8")
        result["paths"] = {
            "enhanced_daily_report": str(enhanced_report_path),
            "enhanced_continuation_prompt": str(enhanced_prompt_path),
            "lark_brief": str(lark_brief_path),
            "llm_quality_report": str(quality_path),
        }
        quality_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:500], "reason": f"llm_refinement_exception:{type(exc).__name__}"}


def _try_generate_llm_weekly_report(*, index: dict[str, Any], baseline_report: str, out_dir: Path) -> dict[str, Any]:
    try:
        from l3_node.work_ledger_llm import llm_refinement_enabled, refine_weekly_report_with_llm

        if not llm_refinement_enabled():
            return {"ok": False, "skipped": True, "reason": "llm_refinement_disabled_or_missing_key"}
        result = refine_weekly_report_with_llm(index=index, baseline_report=baseline_report)
        quality_path = out_dir / f"weekly_quality_report_{index.get('window_days') or 7}d_{_day()}_{uuid.uuid4().hex[:8]}.json"
        quality_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        result["quality_report_path"] = str(quality_path)
        if not result.get("ok"):
            return result
        text = str((result.get("outputs") or {}).get("weekly_report") or "").strip()
        if not text:
            result["ok"] = False
            result["quality"] = {"ok": False, "issues": ["missing_weekly_report"], "warnings": []}
            quality_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            return result
        enhanced_path = out_dir / f"enhanced_work_ledger_weekly_{index.get('window_days') or 7}d_{_day()}_{uuid.uuid4().hex[:8]}.md"
        enhanced_path.write_text(text + "\n", encoding="utf-8")
        result["path"] = str(enhanced_path)
        quality_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:500], "reason": f"llm_weekly_refinement_exception:{type(exc).__name__}"}


def _try_generate_llm_instant_brief(
    *,
    index: dict[str, Any],
    baseline_brief: str,
    out_dir: Path,
    suffix: str,
) -> dict[str, Any]:
    quality_path = out_dir / (
        f"work_ledger_brief_{index.get('window_days') or 1}d_{_day()}_{suffix}.quality.json"
    )
    try:
        from l3_node.work_ledger_llm import (
            llm_refinement_enabled,
            refine_instant_work_brief_with_llm,
        )

        if not llm_refinement_enabled():
            result = {
                "ok": False,
                "skipped": True,
                "reason": "llm_refinement_disabled_or_missing_key",
                "text": "",
            }
        else:
            result = refine_instant_work_brief_with_llm(
                index=index,
                baseline_brief=baseline_brief,
            )
        result["quality_report_path"] = str(quality_path)
        quality_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return result
    except Exception as exc:
        result = {
            "ok": False,
            "text": "",
            "error": str(exc)[:500],
            "reason": f"llm_instant_refinement_exception:{type(exc).__name__}",
            "quality_report_path": str(quality_path),
        }
        quality_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return result


def collect_git_snapshot(root: Path) -> dict[str, Any]:
    root = root.expanduser()
    payload: dict[str, Any] = {
        "project_path": str(root),
        "is_git_repo": False,
        "branch": "",
        "status_summary": "not_git_repo",
        "commands": {},
        "changed_files": [],
    }
    if not root.is_dir():
        payload["status_summary"] = "project_path_not_found"
        return payload
    top = _run_git(root, ["rev-parse", "--show-toplevel"])
    if top["returncode"] != 0:
        payload["commands"]["rev_parse"] = top
        return payload
    payload["is_git_repo"] = True
    payload["git_root"] = top["stdout"].strip()
    branch = _run_git(root, ["branch", "--show-current"])
    status = _run_git(root, ["status", "--short", "--branch"])
    porcelain = _run_git(root, ["status", "--porcelain"])
    log = _run_git(root, ["log", "-8", "--oneline", "--decorate", "--no-merges"])
    diff_stat = _run_git(root, ["diff", "--stat"])
    diff_patch = _run_git(root, ["diff", "--no-ext-diff", "--unified=2"])
    cached_diff_patch = _run_git(
        root,
        ["diff", "--cached", "--no-ext-diff", "--unified=2"],
    )
    name_status = _run_git(root, ["diff", "--name-status"])
    cached_name_status = _run_git(root, ["diff", "--cached", "--name-status"])
    payload["branch"] = branch["stdout"].strip()
    payload["commands"] = {
        "branch": branch,
        "status": status,
        "porcelain": porcelain,
        "log": log,
        "diff_stat": diff_stat,
        "diff_patch": {
            **diff_patch,
            "stdout": str(diff_patch.get("stdout") or "")[:18000],
        },
        "cached_diff_patch": {
            **cached_diff_patch,
            "stdout": str(cached_diff_patch.get("stdout") or "")[:12000],
        },
        "name_status": name_status,
        "cached_name_status": cached_name_status,
    }
    changed = _parse_name_status(name_status.get("stdout", ""), "worktree")
    changed.extend(_parse_name_status(cached_name_status.get("stdout", ""), "cached"))
    known_paths = {
        str(item.get("path") or "").replace("\\", "/").lower()
        for item in changed
    }
    for item in _parse_porcelain(porcelain.get("stdout", "")):
        path_key = str(item.get("path") or "").replace("\\", "/").lower()
        if path_key and path_key not in known_paths:
            changed.append(item)
            known_paths.add(path_key)
    payload["changed_files"] = changed
    count = len(changed)
    payload["status_summary"] = "clean" if count == 0 else f"{count} changed files"
    return payload


def collect_git_window_activity(
    root: Path,
    *,
    since_ms: int,
    max_commits: int = 120,
) -> dict[str, Any]:
    """Collect dated commit evidence for a report window, independent of sessions."""

    root = root.expanduser()
    result: dict[str, Any] = {
        "project_path": str(root),
        "is_git_repo": False,
        "commit_count": 0,
        "commits": [],
    }
    if not root.is_dir():
        result["error"] = "project_path_not_found"
        return result
    top = _run_git(root, ["rev-parse", "--show-toplevel"])
    if top.get("returncode") != 0:
        result["command"] = top
        return result
    result["is_git_repo"] = True
    result["git_root"] = str(top.get("stdout") or "").strip()
    since_iso = datetime.fromtimestamp(
        max(0, int(since_ms or 0)) / 1000,
        tz=timezone.utc,
    ).isoformat()
    command = _run_git(
        root,
        [
            "log",
            f"--since={since_iso}",
            f"--max-count={max(1, min(int(max_commits or 120), 500))}",
            "--date=iso-strict",
            "--pretty=format:%x1e%H%x1f%h%x1f%aI%x1f%an%x1f%s",
            "--name-status",
            "--no-renames",
            "--no-merges",
        ],
    )
    result["command"] = {
        **command,
        "stdout": str(command.get("stdout") or "")[:120000],
    }
    if command.get("returncode") != 0:
        return result
    commits: list[dict[str, Any]] = []
    for chunk in str(command.get("stdout") or "").split("\x1e"):
        lines = chunk.strip("\r\n").splitlines()
        if not lines:
            continue
        header = lines[0].split("\x1f", 4)
        if len(header) != 5:
            continue
        commit = {
            "commit": header[0].strip(),
            "short_commit": header[1].strip(),
            "authored_at": header[2].strip(),
            "author": header[3].strip(),
            "subject": header[4].strip(),
            "changed_files": [],
        }
        for line in lines[1:]:
            parts = line.split("\t", 1)
            if len(parts) != 2:
                continue
            status, path = parts
            path = path.strip()
            if path:
                commit["changed_files"].append(
                    {"status": status.strip(), "path": path}
                )
        commits.append(commit)
    result["commits"] = commits
    result["commit_count"] = len(commits)
    return result


def collect_recent_files(root: Path, *, since_ms: int, max_files: int = 200) -> dict[str, Any]:
    root = root.expanduser()
    payload: dict[str, Any] = {
        "project_path": str(root),
        "since_ms": since_ms,
        "recent_files": [],
        "skipped": [],
    }
    if not root.is_dir():
        payload["error"] = "project_path_not_found"
        return payload
    cutoff = since_ms / 1000 if since_ms else 0
    rows: list[dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS and not d.startswith(".cache")]
        current = Path(dirpath)
        for name in filenames:
            path = current / name
            try:
                stat = path.stat()
            except OSError:
                continue
            if cutoff and stat.st_mtime < cutoff:
                continue
            try:
                rel = str(path.relative_to(root))
            except ValueError:
                rel = str(path)
            rows.append(
                {
                    "path": rel.replace("\\", "/"),
                    "mtime_ms": int(stat.st_mtime * 1000),
                    "size": stat.st_size,
                }
            )
            if len(rows) >= max_files * 3:
                break
        if len(rows) >= max_files * 3:
            break
    rows.sort(key=lambda item: int(item.get("mtime_ms") or 0), reverse=True)
    payload["recent_files"] = rows[:max_files]
    payload["truncated"] = len(rows) > max_files
    return payload


def collect_file_content_snippets(
    root: Path,
    *,
    git_payload: dict[str, Any],
    file_payload: dict[str, Any],
    max_files: int = 24,
    max_chars_per_file: int = 2200,
) -> dict[str, Any]:
    """Collect small, readable snippets from changed/recent text files."""

    root = root.expanduser()
    payload: dict[str, Any] = {
        "project_path": str(root),
        "snippets": [],
        "risk_candidates": [],
        "skipped": [],
    }
    if not root.is_dir():
        payload["error"] = "project_path_not_found"
        return payload
    candidates = _extract_changed_files(git_payload, file_payload)
    for item in candidates:
        if len(payload["snippets"]) >= max_files:
            break
        rel = str(item.get("path") or "").strip().replace("/", os.sep)
        if not rel:
            continue
        path = (root / rel).resolve()
        try:
            path.relative_to(root.resolve())
        except Exception:
            payload["skipped"].append({"path": rel, "reason": "outside_project"})
            continue
        if not path.is_file():
            payload["skipped"].append({"path": rel, "reason": "not_file"})
            continue
        if path.suffix.lower() not in TEXT_SNIPPET_EXTENSIONS:
            payload["skipped"].append({"path": rel, "reason": "unsupported_extension"})
            continue
        try:
            stat = path.stat()
        except OSError as e:
            payload["skipped"].append({"path": rel, "reason": f"stat_failed:{type(e).__name__}"})
            continue
        if stat.st_size > 512_000:
            payload["skipped"].append({"path": rel, "reason": "file_too_large", "size": stat.st_size})
            continue
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except Exception as e:
            payload["skipped"].append({"path": rel, "reason": f"read_failed:{type(e).__name__}"})
            continue
        excerpt = _clean_text_excerpt(text, max_chars=max_chars_per_file)
        risk_lines = _extract_risk_lines(text)
        payload["snippets"].append(
            {
                "path": rel.replace("\\", "/"),
                "status": item.get("status") or "recent",
                "size": stat.st_size,
                "excerpt": excerpt,
                "risk_line_count": len(risk_lines),
            }
        )
        for risk in risk_lines[:8]:
            payload["risk_candidates"].append({"path": rel.replace("\\", "/"), **risk})
    payload["snippet_count"] = len(payload["snippets"])
    payload["risk_candidate_count"] = len(payload["risk_candidates"])
    return payload


def _run_git(root: Path, args: list[str]) -> dict[str, Any]:
    return _run_command(["git", *args], cwd=root, timeout=10)


def _run_command(cmd: list[str], *, cwd: Path, timeout: int = 15) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
        return {
            "cmd": cmd,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-20000:],
            "stderr": proc.stderr[-8000:],
        }
    except Exception as e:
        return {"cmd": cmd, "returncode": -1, "stdout": "", "stderr": str(e)}


def _parse_name_status(text: str, origin: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw in (text or "").splitlines():
        parts = raw.split("\t")
        if len(parts) >= 2:
            rows.append({"status": parts[0].strip(), "path": parts[-1].strip(), "origin": origin})
    return rows


def _parse_porcelain(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for raw in (text or "").splitlines():
        if len(raw) >= 4:
            rows.append({"status": raw[:2].strip() or "M", "path": raw[3:].strip(), "origin": "status"})
    return rows


def _git_summary(payload: dict[str, Any]) -> str:
    if not payload.get("is_git_repo"):
        return f"Git 未就绪：{payload.get('status_summary')}"
    branch = payload.get("branch") or "unknown"
    return f"Git {branch}: {payload.get('status_summary')}"


def _latest_payload(evidence: list[dict[str, Any]], source: str) -> dict[str, Any]:
    for ev in reversed(evidence):
        if ev.get("source") == source and isinstance(ev.get("payload"), dict):
            return ev["payload"]
    return {}


def _extract_changed_files(git_payload: dict[str, Any], file_payload: dict[str, Any]) -> list[dict[str, str]]:
    seen: set[str] = set()
    rows: list[dict[str, str]] = []
    for item in git_payload.get("changed_files") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        rows.append({"status": str(item.get("status") or "M"), "path": path})
    for item in file_payload.get("recent_files") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        rows.append({"status": "recent", "path": path})
    return rows


def _tokenize_for_recall(text: str) -> list[str]:
    raw = str(text or "").lower()
    parts = re.findall(r"[a-z0-9_.:/\\-]+|[\u4e00-\u9fff]{1,}", raw)
    terms: list[str] = []
    seen: set[str] = set()
    for part in parts:
        part = part.strip(" .,:;，。；：`'\"[](){}")
        if len(part) < 2:
            continue
        if part not in seen:
            seen.add(part)
            terms.append(part)
    return terms[:24]


def _score_recall_text(query: str, query_terms: list[str], text: str, *, trust_level: str = "system_observed") -> float:
    haystack = str(text or "").lower()
    if not haystack:
        return 0.0
    score = 0.0
    if str(query or "").strip().lower() in haystack:
        score += 6.0
    for term in query_terms:
        if term in haystack:
            score += 2.0
        elif len(term) >= 4 and any(chunk in haystack for chunk in (term[:4], term[-4:])):
            score += 0.6
    if trust_level == "user_confirmed":
        score += 2.0
    elif trust_level == "pending_confirmation":
        score -= 1.0
    return max(score, 0.0)


def rank_work_ledger_recall_candidates(
    query: str,
    query_terms: list[str],
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rank recall candidates with transparent 3-stage scoring.

    Stage 1: keyword lexical recall.
    Stage 2: trust/type/recency rules.
    Stage 3: local normalized vector dot-product rerank.
    """

    now_ms = _now_ms()
    ranked: list[dict[str, Any]] = []
    for raw in candidates:
        item = dict(raw)
        text = str(item.get("text") or "")
        trust = str(item.get("trust_level") or "system_observed")
        if trust in {"user_rejected", "rejected"}:
            item["score"] = -100.0
            item["score_parts"] = {"keyword": 0.0, "rule": -100.0, "vector": 0.0}
            item["ranking_reason"] = "user_rejected_filtered"
            ranked.append(item)
            continue
        lexical = _score_recall_text(query, query_terms, text, trust_level="system_observed")
        rule = _rule_recall_boost(item, trust_level=trust, now_ms=now_ms)
        vector = _local_vector_similarity(query, text)
        score = lexical + rule + vector * 4.0
        reasons: list[str] = []
        if lexical > 0:
            reasons.append(f"keyword={round(lexical, 2)}")
        if rule > 0:
            reasons.append(f"rule={round(rule, 2)}")
        if vector > 0:
            reasons.append(f"vector={round(vector, 3)}")
        if trust == "user_confirmed":
            reasons.append("user_confirmed")
        item["score"] = round(score, 4)
        item["score_parts"] = {
            "keyword": round(lexical, 4),
            "rule": round(rule, 4),
            "vector": round(vector, 4),
        }
        item["ranking_reason"] = "；".join(reasons) if reasons else "no_positive_signal"
        ranked.append(item)
    ranked.sort(
        key=lambda item: (
            float(item.get("score") or 0),
            str(item.get("collected_at") or ""),
            int(item.get("updated_at_ms") or 0),
        ),
        reverse=True,
    )
    return ranked, {
        "stages": ["keyword_recall", "rule_score", "normalized_vector_dot"],
        "candidate_count": len(candidates),
        "positive_count": sum(1 for item in ranked if float(item.get("score") or 0) > 0),
    }


def _rule_recall_boost(item: dict[str, Any], *, trust_level: str, now_ms: int) -> float:
    score = 0.0
    if trust_level == "user_confirmed":
        score += 2.0
    elif trust_level == "pending_confirmation":
        score -= 0.8
    kind = str(item.get("kind") or "")
    if kind in {"adopted_output", "methodology_candidate", "manual_note"}:
        score += 1.0
    elif kind == "ai_signal":
        score += 0.5
    updated_ms = int(item.get("updated_at_ms") or 0)
    if updated_ms:
        age_days = max(0.0, (now_ms - updated_ms) / (24 * 60 * 60 * 1000))
        if age_days <= 3:
            score += 0.8
        elif age_days <= 14:
            score += 0.4
    return max(score, 0.0)


def _local_vector_similarity(a: str, b: str) -> float:
    va = _text_hash_vector(a)
    vb = _text_hash_vector(b)
    if not va or not vb:
        return 0.0
    # Vectors are already L2-normalized, so dot product equals cosine similarity.
    return sum(value * vb.get(key, 0.0) for key, value in va.items())


def _text_hash_vector(text: str) -> dict[str, float]:
    raw = str(text or "").lower()
    tokens = _tokenize_for_recall(raw)
    grams: list[str] = []
    grams.extend(tokens)
    compact = re.sub(r"\s+", "", raw)
    if compact:
        grams.extend(compact[i : i + 2] for i in range(max(0, len(compact) - 1)) if len(compact[i : i + 2]) == 2)
        grams.extend(compact[i : i + 3] for i in range(max(0, len(compact) - 2)) if len(compact[i : i + 3]) == 3)
    counts: dict[str, float] = {}
    for gram in grams[:500]:
        counts[gram] = counts.get(gram, 0.0) + 1.0
    norm = sum(value * value for value in counts.values()) ** 0.5
    if norm <= 0:
        return {}
    return {key: value / norm for key, value in counts.items()}


def _clean_text_excerpt(text: str, *, max_chars: int) -> str:
    clean = (text or "").replace("\x00", "")
    clean = re.sub(r"\r\n?", "\n", clean)
    clean = re.sub(r"\n{4,}", "\n\n\n", clean)
    clean = clean.strip()
    if len(clean) <= max_chars:
        return clean
    return clean[:max_chars].rstrip() + "\n..."


def _extract_risk_lines(text: str, *, max_line_chars: int = 240) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx, line in enumerate((text or "").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        upper = stripped.upper()
        if any(keyword.upper() in upper for keyword in RISK_KEYWORDS):
            rows.append(
                {
                    "line": idx,
                    "text": stripped[:max_line_chars],
                    "matched_keywords": [
                        keyword for keyword in RISK_KEYWORDS if keyword.upper() in upper
                    ][:6],
                }
            )
    return rows[:80]


def _summarize_evidence_trust(evidence: list[dict[str, Any]]) -> dict[str, int]:
    out = {
        "user_confirmed": 0,
        "system_observed": 0,
        "system_inferred": 0,
        "pending_confirmation": 0,
    }
    for ev in evidence:
        key = str(ev.get("trust_level") or "system_observed")
        if key not in out:
            out[key] = 0
        out[key] += 1
    return out


def _dedupe_nonempty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = re.sub(r"\s+", " ", str(value or "").strip())
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _collect_ai_trace_buckets(ai_traces: list[dict[str, Any]]) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {key: [] for key in AI_TRACE_BUCKETS}
    for ev in ai_traces:
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
        raw_buckets = analysis.get("buckets") if isinstance(analysis.get("buckets"), dict) else {}
        for key in buckets:
            values = raw_buckets.get(key)
            if isinstance(values, list):
                buckets[key].extend(str(item) for item in values if str(item or "").strip())
    return {key: _dedupe_nonempty(values) for key, values in buckets.items()}


def _project_memory_for_session(session: dict[str, Any]) -> dict[str, Any] | None:
    query_parts = [
        str(session.get("project_name") or ""),
        str(session.get("project_path") or ""),
        str(session.get("title") or ""),
    ]
    query = " ".join(part for part in query_parts if part).strip()
    if not query:
        return None
    try:
        from l3_node.work_ledger_project_memory import resolve_project_reference

        return resolve_project_reference(query)
    except Exception:
        return None


def _iter_candidate_process_files(root: Path, *, max_files: int = 200) -> list[Path]:
    wanted_ext = {".log", ".txt", ".md", ".jsonl", ".out", ".err"}
    out: list[Path] = []
    if not root.exists():
        return out
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in EXCLUDED_DIRS
            and not name.startswith(".cache")
            and name not in {"site-packages", "__pypackages__", ".turbo"}
        ]
        current = Path(dirpath)
        for name in filenames:
            path = current / name
            suffix = path.suffix.lower()
            if suffix not in wanted_ext:
                continue
            lower_name = name.lower()
            parent_text = str(current).lower()
            name_signal = any(
                token in lower_name or token in parent_text
                for token in (
                    "codex",
                    "cursor",
                    "terminal",
                    "powershell",
                    "work_ledger",
                    "ledger",
                    "smoke",
                    "debug",
                    "trace",
                    "report",
                    "brief",
                    "context",
                    "daily",
                    "weekly",
                    "log",
                )
            )
            if not name_signal and suffix not in {".log", ".jsonl"}:
                continue
            out.append(path)
            if len(out) >= max_files:
                return out
    return out


def _score_work_process_candidate(path: Path, root_reason: str, session: dict[str, Any]) -> tuple[float, str]:
    name = path.name.lower()
    full = str(path).lower()
    score = 0.0
    reasons: list[str] = []
    signals = {
        "codex": 5.0,
        "cursor": 5.0,
        "work_ledger": 4.0,
        "context": 3.0,
        "terminal": 3.0,
        "powershell": 3.0,
        "debug": 2.0,
        "trace": 3.0,
        "daily": 2.0,
        "weekly": 2.0,
        "report": 2.0,
        "brief": 2.0,
        "smoke": 2.0,
    }
    for token, value in signals.items():
        if token in name or token in full:
            score += value
            reasons.append(token)
    if path.suffix.lower() in {".log", ".jsonl"}:
        score += 2.0
        reasons.append(path.suffix.lower())
    if root_reason in {"project", "work_ledger_home"}:
        score += 2.0
        reasons.append(root_reason)
    project_name = str(session.get("project_name") or "").lower()
    if project_name and project_name in full:
        score += 2.0
        reasons.append("project_name")
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    if size <= 0:
        return 0.0, "empty"
    if size > 8_000_000:
        score -= 4.0
        reasons.append("large_file")
    return score, ",".join(dict.fromkeys(reasons)) or "recent_candidate"


def _read_candidate_file_excerpt(path: Path, *, max_chars: int = 24000) -> str:
    try:
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
    except Exception:
        return ""
    if len(raw) > max_chars:
        return raw[-max_chars:]
    return raw


def _load_work_process_material(*, text: str = "", file_path: str = "") -> tuple[str, dict[str, Any]]:
    inline = str(text or "").strip()
    path_text = str(file_path or "").strip().strip('"')
    if path_text:
        path = Path(path_text).expanduser()
        if not path.is_file():
            raise ValueError(f"process file not found: {path_text}")
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
        max_chars = 300_000
        clipped = len(raw) > max_chars
        if clipped:
            raw = raw[-max_chars:]
        if inline:
            raw = inline + "\n\n--- imported file tail ---\n" + raw
        return raw, {
            "type": "file",
            "file_path": str(path.resolve()),
            "file_size": path.stat().st_size,
            "tail_clipped": clipped,
            "inline_char_count": len(inline),
        }
    return inline, {"type": "text", "char_count": len(inline)}


def _infer_trace_tool(text: str, file_path: str = "") -> str:
    haystack = f"{file_path}\n{text[:8000]}".lower()
    if "cursor" in haystack:
        return "Cursor"
    if "codex" in haystack:
        return "Codex"
    if "claude" in haystack:
        return "Claude"
    if "powershell" in haystack or ".ps1" in haystack or "pytest" in haystack or "cargo" in haystack or "npm " in haystack:
        return "Terminal"
    if "git diff" in haystack or "git status" in haystack:
        return "Git"
    return "AI"


def _work_process_signal_keywords() -> tuple[str, ...]:
    base: list[str] = []
    for values in AI_TRACE_BUCKETS.values():
        base.extend(str(item).lower() for item in values)
    base.extend(
        [
            "todo",
            "fixme",
            "changed",
            "added",
            "removed",
            "created",
            "updated",
            "implemented",
            "verified",
            "failed",
            "error",
            "exception",
            "warning",
            "passed",
            "skipped",
            "commit",
            "diff",
            "test",
            "pytest",
            "cargo",
            "npm",
            "npx",
            "tsc",
            "build",
            "deploy",
            "evidence",
            "decision",
            "next",
            "risk",
            "blocked",
            "fallback",
            "recovery",
            "work ledger",
            "context pack",
        ]
    )
    return tuple(dict.fromkeys(item for item in base if item))


def _rewrite_evidence_payload(session_id: str, evidence_id: str, payload: dict[str, Any]) -> None:
    if not evidence_id:
        return
    path = _evidence_path(session_id)
    if not path.is_file():
        return
    rows: list[str] = []
    changed = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except Exception:
            rows.append(line)
            continue
        if isinstance(row, dict) and str(row.get("evidence_id") or "") == evidence_id:
            row["payload"] = payload
            changed = True
        rows.append(json.dumps(row, ensure_ascii=False, default=str))
    if changed:
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _build_methodology_review_points(
    notes: list[dict[str, Any]],
    ai_buckets: dict[str, list[str]],
    risks: list[dict[str, Any]],
) -> list[str]:
    points: list[str] = []
    decisions = ai_buckets.get("decisions", [])
    failures = ai_buckets.get("failures", [])
    next_steps = ai_buckets.get("next_steps", [])
    if decisions:
        points.append("把已确认有效的决策写成“以后遇到同类任务优先怎么做”的规则。")
    if failures or risks:
        points.append("把失败原因、触发条件、恢复动作整理成 failure playbook，供下次自动重试参考。")
    if next_steps:
        points.append("把下一步拆成可复用 checklist：前置条件、执行动作、验证标准、失败处理。")
    if notes:
        points.append("用户明确补充或确认的记录优先进入长期记忆；系统推断内容保持待确认。")
    if not points:
        points.append("当前证据不足以沉淀方法论，建议补充关键决策、失败原因和验收结论。")
    return points


def _format_ai_trace_analysis_lines(analysis: Any, *, indent: str = "") -> list[str]:
    if not isinstance(analysis, dict):
        return []
    buckets = analysis.get("buckets") if isinstance(analysis.get("buckets"), dict) else {}
    labels = {
        "goals": "任务/目标",
        "actions": "动作/改动",
        "failures": "失败/阻塞",
        "decisions": "结论/决策",
        "next_steps": "下一步",
    }
    lines: list[str] = []
    for key, label in labels.items():
        values = buckets.get(key)
        if not isinstance(values, list) or not values:
            continue
        preview = "；".join(str(item).strip() for item in values[:3] if str(item).strip())
        if preview:
            lines.append(f"{indent}- {label}: {preview}")
    return lines[:10]


def _clean_report_item(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = re.sub(r"^(?:[-*•]\s*|\d+[.、)]\s*)", "", text)
    return text[:360]


def _append_report_item(target: list[str], value: Any, *, limit: int) -> None:
    text = _clean_report_item(value)
    if not text:
        return
    fingerprint = re.sub(r"[\s，。；：、,.!！?？`'\"]+", "", text).lower()
    if not fingerprint:
        return
    existing = {
        re.sub(r"[\s，。；：、,.!！?？`'\"]+", "", item).lower()
        for item in target
    }
    if fingerprint in existing or len(target) >= limit:
        return
    target.append(text)


def _work_report_sections(
    session: dict[str, Any],
    evidence: list[dict[str, Any]],
    *,
    changed_files: list[dict[str, str]] | None = None,
    notes: list[dict[str, Any]] | None = None,
) -> dict[str, list[str]]:
    git_latest = _latest_payload(evidence, "git_snapshot")
    files_latest = _latest_payload(evidence, "file_scan")
    snippets_latest = _latest_payload(evidence, "file_content_snippets")
    changed = changed_files or _extract_changed_files(git_latest, files_latest)
    manual_notes = notes or [
        item for item in evidence if item.get("source") == "manual_note"
    ]
    ai_traces = [
        item for item in evidence if item.get("source") == "ai_work_trace"
    ]
    fact_context = _safe_session_fact_context(session)
    outcome_context = _safe_session_outcome_context(session)

    progress: list[str] = []
    modules: list[str] = []
    blockers: list[str] = []
    next_steps: list[str] = []

    for outcome in outcome_context.get("outcomes_this_session") or []:
        _append_report_item(
            progress,
            f"已完成并验证：{outcome.get('summary')}",
            limit=20,
        )

    facts = list(fact_context.get("new_facts") or []) + list(
        fact_context.get("completed_this_session") or []
    )
    for fact in facts:
        state = str(fact.get("state") or "")
        prefix = "已完成" if state == "completed" else "持续推进"
        _append_report_item(
            progress,
            f"{prefix}：{fact.get('canonical_summary')}",
            limit=20,
        )

    for item in manual_notes[-20:]:
        _append_report_item(progress, item.get("summary"), limit=20)

    for trace in ai_traces[-16:]:
        payload = trace.get("payload") if isinstance(trace.get("payload"), dict) else {}
        analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
        buckets = analysis.get("buckets") if isinstance(analysis.get("buckets"), dict) else {}
        for action in buckets.get("actions") or []:
            _append_report_item(progress, f"推进：{action}", limit=20)
        for failure in buckets.get("failures") or []:
            _append_report_item(blockers, failure, limit=12)
        for action in buckets.get("next_steps") or []:
            _append_report_item(next_steps, action, limit=12)

    for label in _work_capability_labels(changed):
        _append_report_item(
            modules,
            f"能力建设：{label}",
            limit=12,
        )

    open_facts = list(fact_context.get("reopened_this_session") or []) + list(
        fact_context.get("prior_open_facts") or []
    )
    for fact in open_facts:
        _append_report_item(
            blockers,
            fact.get("canonical_summary"),
            limit=12,
        )
        for action in fact.get("next_actions") or []:
            _append_report_item(
                next_steps,
                action.get("text") if isinstance(action, dict) else action,
                limit=12,
            )

    risks = (
        snippets_latest.get("risk_candidates", [])
        if isinstance(snippets_latest, dict)
        else []
    )
    for item in risks[:8]:
        _append_report_item(
            blockers,
            f"待核查：{item.get('text')}",
            limit=12,
        )

    if not progress:
        if changed:
            _append_report_item(
                progress,
                "证据不足：已采集本次工作过程，但尚待提炼并验证具体成果。",
                limit=20,
            )
        else:
            _append_report_item(
                progress,
                "本次尚未形成用户确认并完成验证的工作事项。",
                limit=20,
            )
    if not modules:
        _append_report_item(
            modules,
            "暂未从当前证据中识别到明确的能力建设范围。",
            limit=12,
        )
    if not blockers:
        _append_report_item(
            blockers,
            "暂未发现明确阻塞，仍需结合最终测试结果确认。",
            limit=12,
        )
    if not next_steps:
        _append_report_item(
            next_steps,
            "补充最终验证结果，并确认未完成事项是否需要进入下一任务。",
            limit=12,
        )

    return {
        "progress": progress,
        "modules": modules,
        "blockers": blockers,
        "next_steps": next_steps,
    }


def _numbered_report_lines(items: list[str]) -> list[str]:
    return [f"{index}. {item}" for index, item in enumerate(items, start=1)]


def _build_session_brief_evidence_digest(
    session: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    git_payload = _latest_payload(evidence, "git_snapshot")
    checkpoint_payload = _latest_payload(evidence, "work_checkpoint")
    snippet_payload = _latest_payload(evidence, "file_content_snippets")
    source_payload = checkpoint_payload or git_payload
    commands = (
        source_payload.get("commands")
        if isinstance(source_payload.get("commands"), dict)
        else {}
    )
    snippets = checkpoint_payload.get("snippets") or snippet_payload.get("snippets") or []
    risks = (
        checkpoint_payload.get("risk_candidates")
        or snippet_payload.get("risk_candidates")
        or []
    )
    notes = [
        str(ev.get("summary") or "").strip()
        for ev in evidence
        if ev.get("source") == "manual_note" and str(ev.get("summary") or "").strip()
    ]
    traces: list[dict[str, Any]] = []
    codex_consultations: list[dict[str, Any]] = []
    for ev in evidence:
        if ev.get("source") == "codex_work_plan_consultation":
            payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
            codex_consultations.append(
                {
                    "summary": str(ev.get("summary") or "")[:500],
                    "ok": bool(payload.get("ok")),
                    "conversation_name": payload.get("conversation_name"),
                    "answer": str(payload.get("answer") or "")[:8000],
                    "answer_source": payload.get("answer_source"),
                    "answer_validation": payload.get("answer_validation"),
                    "claim_fusion": payload.get("claim_fusion") or {},
                    "recovery": payload.get("recovery") or {},
                    "recovery_terminal": payload.get("recovery_terminal") or {},
                    "trust_level": ev.get("trust_level"),
                    "tool_evidence_path": payload.get("tool_evidence_path"),
                }
            )
            continue
        if ev.get("source") != "ai_work_trace":
            continue
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
        traces.append(
            {
                "summary": str(ev.get("summary") or "")[:500],
                "tool_name": str(payload.get("tool_name") or ""),
                "buckets": analysis.get("buckets") or {},
            }
        )
    return {
        "session_id": session.get("session_id"),
        "title": session.get("title"),
        "status": session.get("status"),
        "project_name": session.get("project_name"),
        "project_path": session.get("project_path"),
        "user_goal": session.get("user_goal"),
        "git": {
            "branch": source_payload.get("branch"),
            "status_summary": source_payload.get("status_summary"),
            "changed_files": (source_payload.get("changed_files") or [])[:80],
            "diff_stat": str(
                ((commands.get("diff_stat") or {}).get("stdout") or "")
            )[:3000],
            "diff_patch": str(
                ((commands.get("diff_patch") or {}).get("stdout") or "")
            )[:12000],
            "cached_diff_patch": str(
                ((commands.get("cached_diff_patch") or {}).get("stdout") or "")
            )[:8000],
        },
        "file_snippets": [
            {
                "path": item.get("path"),
                "status": item.get("status"),
                "excerpt": str(item.get("excerpt") or "")[:1600],
            }
            for item in snippets[:16]
            if isinstance(item, dict)
        ],
        "risk_candidates": risks[:24],
        "manual_notes": notes[-20:],
        "ai_work_traces": traces[-12:],
        "codex_work_plan_consultations": codex_consultations[-6:],
        "daily_checkpoints": _build_daily_checkpoint_history(evidence),
    }


def _build_daily_checkpoint_history(
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    latest_by_day: dict[str, dict[str, Any]] = {}
    for ev in evidence:
        if ev.get("source") not in {"work_checkpoint", "git_snapshot"}:
            continue
        day_key = str(ev.get("collected_at") or "")[:10] or _local_day_from_ms(
            ev.get("collected_at_ms")
        )
        if not day_key:
            continue
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        commands = payload.get("commands") if isinstance(payload.get("commands"), dict) else {}
        changed_files = [
            {
                "path": item.get("path"),
                "status": item.get("status"),
            }
            for item in (payload.get("changed_files") or [])[:80]
            if isinstance(item, dict) and str(item.get("path") or "").strip()
        ]
        snippets = [
            {
                "path": item.get("path"),
                "excerpt": str(item.get("excerpt") or "")[:700],
            }
            for item in (payload.get("snippets") or [])[:6]
            if isinstance(item, dict)
        ]
        candidate = {
            "date": day_key,
            "collected_at": ev.get("collected_at"),
            "collected_at_ms": int(ev.get("collected_at_ms") or 0),
            "changed_file_count": int(
                payload.get("changed_file_count") or len(changed_files)
            ),
            "recent_file_count": int(payload.get("recent_file_count") or 0),
            "changed_files": changed_files,
            "diff_stat": str(
                ((commands.get("diff_stat") or {}).get("stdout") or "")
            )[:1800],
            "diff_patch": str(
                ((commands.get("diff_patch") or {}).get("stdout") or "")
            )[:4500],
            "cached_diff_patch": str(
                ((commands.get("cached_diff_patch") or {}).get("stdout") or "")
            )[:2200],
            "file_snippets": snippets,
        }
        previous = latest_by_day.get(day_key)
        if previous is None or candidate["collected_at_ms"] >= int(
            previous.get("collected_at_ms") or 0
        ):
            latest_by_day[day_key] = candidate
    return [latest_by_day[key] for key in sorted(latest_by_day)]


def build_instant_work_brief(
    index: dict[str, Any],
    *,
    title: str | None = None,
) -> str:
    days = max(1, int(index.get("window_days") or 1))
    sessions = index.get("sessions") if isinstance(index.get("sessions"), list) else []
    outcomes = (
        index.get("valued_outcomes")
        if isinstance(index.get("valued_outcomes"), list)
        else []
    )
    if not outcomes:
        outcomes = (
            index.get("verified_outcomes")
            if isinstance(index.get("verified_outcomes"), list)
            else []
        )
    notes = (
        index.get("recent_notes")
        if isinstance(index.get("recent_notes"), list)
        else []
    )
    signals = (
        index.get("recent_ai_signals")
        if isinstance(index.get("recent_ai_signals"), list)
        else []
    )
    changed_files = (
        index.get("recent_changed_files")
        if isinstance(index.get("recent_changed_files"), list)
        else []
    )
    project_counts = (
        index.get("project_counts")
        if isinstance(index.get("project_counts"), dict)
        else {}
    )
    git_activity = (
        index.get("git_activity")
        if isinstance(index.get("git_activity"), list)
        else []
    )
    recent_project_files = (
        index.get("recent_project_files")
        if isinstance(index.get("recent_project_files"), list)
        else []
    )

    progress: list[str] = []
    modules: list[str] = []
    blockers: list[str] = []
    next_steps: list[str] = []

    commit_days: set[str] = set()
    for project_activity in git_activity:
        project_name = str(
            project_activity.get("project_name")
            or Path(str(project_activity.get("project_path") or "")).name
            or "项目"
        )
        for commit in project_activity.get("commits") or []:
            if not isinstance(commit, dict):
                continue
            authored_day = str(commit.get("authored_at") or "")[:10] or "日期未知"
            if authored_day != "日期未知":
                commit_days.add(authored_day)
            subject = str(commit.get("subject") or "").strip()
            if not subject:
                continue
            capability_text = "、".join(
                _work_capability_labels(commit.get("changed_files") or [])[:5]
            )
            suffix = f"，覆盖 {capability_text}" if capability_text else ""
            _append_report_item(
                progress,
                f"完成版本节点（{authored_day}，{project_name}）：{subject}{suffix}。",
                limit=24,
            )
    for digest in index.get("session_evidence_digests") or []:
        if not isinstance(digest, dict):
            continue
        project_name = str(digest.get("project_name") or "项目")
        for checkpoint in digest.get("daily_checkpoints") or []:
            if not isinstance(checkpoint, dict):
                continue
            day_key = str(checkpoint.get("date") or "")
            if not day_key or day_key in commit_days:
                continue
            capability_text = "、".join(
                _work_capability_labels(checkpoint.get("changed_files") or [])[:5]
            ) or "项目核心功能与工程实现"
            _append_report_item(
                progress,
                (
                    f"推进工作能力建设（{day_key}，{project_name}）："
                    f"{capability_text}；当前按过程证据记录，最终完成边界以验证结果为准。"
                ),
                limit=24,
            )
    for outcome in outcomes[:20]:
        stage = str(outcome.get("value_stage") or "completed")
        stage_label = {
            "impact": "已产生影响",
            "adopted": "已被采用",
            "delivered": "已交付",
            "completed": "已完成并验证",
        }.get(stage, "已完成并验证")
        _append_report_item(
            progress,
            f"{stage_label}：{outcome.get('summary')}",
            limit=24,
        )
    for note in notes[-16:]:
        _append_report_item(
            progress,
            f"过程记录：{note.get('summary')}",
            limit=24,
        )
    for signal in signals:
        if str(signal.get("kind") or "") != "actions":
            continue
        _append_report_item(
            progress,
            signal.get("text"),
            limit=24,
        )

    capability_items: list[dict[str, Any]] = list(changed_files[-80:])
    for project_activity in git_activity:
        for commit in project_activity.get("commits") or []:
            if not isinstance(commit, dict):
                continue
            capability_items.extend(
                item
                for item in (commit.get("changed_files") or [])
                if isinstance(item, dict)
            )
    if not modules:
        capability_items.extend(
            item for item in recent_project_files[:80] if isinstance(item, dict)
        )
        for label in _work_capability_labels(capability_items):
            _append_report_item(
                modules,
                f"能力建设：{label}",
                limit=20,
            )
    if not modules:
        for project, count in sorted(
            project_counts.items(),
            key=lambda item: int(item[1]),
            reverse=True,
        )[:12]:
            _append_report_item(
                modules,
                f"记录范围涉及 {project}，共关联 {count} 个任务；当前证据未细化到具体模块。",
                limit=20,
            )

    for signal in signals:
        kind = str(signal.get("kind") or "")
        if kind == "failures":
            _append_report_item(blockers, signal.get("text"), limit=16)
        elif kind == "next_steps":
            _append_report_item(next_steps, signal.get("text"), limit=16)
    if not progress:
        _append_report_item(
            progress,
            "证据不足：当前只记录到任务或文件变化，尚不能确认具体工作成果。",
            limit=24,
        )
    if not modules:
        _append_report_item(
            modules,
            "当前记录中尚未识别到明确的项目或变更模块。",
            limit=20,
        )
    if not blockers:
        _append_report_item(
            blockers,
            "当前证据未记录明确风险；这不等于相关改动已经完成验证。",
            limit=16,
        )
    if not next_steps:
        _append_report_item(
            next_steps,
            "当前记录中尚未写入明确的下一步计划。",
            limit=16,
        )

    heading = title or ("今日工作简报" if days == 1 else f"最近 {days} 天工作简报")
    range_label = "今天 00:00 至当前" if days == 1 else f"最近 {days} 个自然日"
    activity_day_count = int(index.get("activity_day_count") or 0)
    git_commit_count = int(index.get("git_commit_count") or 0)
    lines = [
        f"# {heading}",
        "",
        f"- 统计范围：{range_label}",
        f"- 账本任务：{len(sessions)} 个",
        f"- 活跃工作日：{activity_day_count} 天",
        f"- Git 提交：{git_commit_count} 个",
        f"- 生成时间：{index.get('generated_at') or _now_datetime().isoformat()}",
        "",
        "## 一、完成与推进",
        "",
        *_numbered_report_lines(progress),
        "",
        "## 二、涉及项目与模块",
        "",
        *_numbered_report_lines(modules),
        "",
        "## 三、风险与未完成",
        "",
        *_numbered_report_lines(blockers),
        "",
        "## 四、下一步计划",
        "",
        *_numbered_report_lines(next_steps),
        "",
        "## 依据边界",
        "",
        "1. 简报正文按工作成果、能力建设、问题与下一步组织；文件路径和 M/A/D 状态只保留在后台证据中。",
        "2. Git 提交可证明版本节点；未提交文件只作为改动证据，不自动等同于功能完成。",
        "3. 未经记录或验证的事项不会被补写成已完成成果。",
    ]
    return "\n".join(lines)


def _work_capability_label(path_value: Any) -> str:
    if isinstance(path_value, dict):
        path_value = path_value.get("path") or path_value.get("name") or ""
    path_text = str(path_value or "").replace("\\", "/").lower()
    rules = (
        (("work_ledger",), "AI 工作账本、工作记忆与复盘"),
        (("codex",), "Codex 协作、上下文补全与任务续接"),
        (
            ("voice", "stt", "tts", "wake_", "speech"),
            "常开语音、中英文识别与打断控制",
        ),
        (
            ("windows_uia", "os_tasks", "os_evidence"),
            "Windows 桌面执行、跨应用操作与证据链",
        ),
        (
            ("cognitive_kernel", "agent_core", "task_decomposer"),
            "意图理解、任务拆解与智能执行主链路",
        ),
        (
            ("english_vocab", "english-tutor", "english_learning"),
            "英语学习、词汇与例句体验",
        ),
        (
            ("capability", "skills_repo", "plugin.json", "local_mcps", "/mcp"),
            "Skill/MCP 能力发布、安装与运行",
        ),
        (
            ("test_", "tests/", "/tests/", "smoke", "stress"),
            "自动化测试、压力验证与可靠性保障",
        ),
        (("docs/", "/docs/", "readme"), "架构文档、测试记录与知识沉淀"),
        (
            ("clients/desktop", "console/", "src-tauri"),
            "桌面客户端、控制台与交互体验",
        ),
        (("memory", "recall", "knowledge"), "长期记忆、召回与知识治理"),
        (("lark",), "Lark 消息、办公协作与结果交付"),
    )
    for markers, label in rules:
        if any(marker in path_text for marker in markers):
            return label
    return "项目核心功能与工程实现"


def _work_capability_labels(items: list[Any]) -> list[str]:
    labels: list[str] = []
    for item in items:
        label = _work_capability_label(item)
        if label not in labels:
            labels.append(label)
    return labels


def _brief_changed_file_count(index: dict[str, Any]) -> int:
    keys: set[tuple[str, str]] = set()
    for item in index.get("recent_changed_files") or []:
        if isinstance(item, dict) and str(item.get("path") or "").strip():
            keys.add(
                (
                    str(item.get("project_name") or ""),
                    str(item.get("path") or "").replace("\\", "/").lower(),
                )
            )
    for activity in index.get("git_activity") or []:
        if not isinstance(activity, dict):
            continue
        project = str(activity.get("project_name") or "")
        for commit in activity.get("commits") or []:
            if not isinstance(commit, dict):
                continue
            for item in commit.get("changed_files") or []:
                if isinstance(item, dict) and str(item.get("path") or "").strip():
                    keys.add(
                        (
                            project,
                            str(item.get("path") or "")
                            .replace("\\", "/")
                            .lower(),
                        )
                    )
    return len(keys)


def build_itemized_work_report(
    session: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> str:
    title = session.get("title") or "今日任务"
    sections = _work_report_sections(session, evidence)
    lines = [
        f"# 工作汇报：{title}",
        "",
        "## 一、今日完成与推进",
        "",
        *_numbered_report_lines(sections["progress"]),
        "",
        "## 二、涉及模块",
        "",
        *_numbered_report_lines(sections["modules"]),
        "",
        "## 三、风险与未完成",
        "",
        *_numbered_report_lines(sections["blockers"]),
        "",
        "## 四、下一步计划",
        "",
        *_numbered_report_lines(sections["next_steps"]),
        "",
        "## 依据边界",
        "",
        "1. 内容仅来自 Git、文件扫描、用户记录、已验证成果和 AI 过程导入。",
        "2. 未采集、未确认或未验证的事项不会写成已完成成果。",
    ]
    return "\n".join(lines)


def _build_lark_short(
    session: dict[str, Any],
    evidence: list[dict[str, Any]],
    changed_files: list[dict[str, str]],
    notes: list[dict[str, Any]],
) -> str:
    title = session.get("title") or "今日任务"
    sections = _work_report_sections(
        session,
        evidence,
        changed_files=changed_files,
        notes=notes,
    )
    lines = [f"【{title}｜工作汇报】", "完成与推进："]
    lines.extend(_numbered_report_lines(sections["progress"][:5]))
    lines.append("风险与未完成：")
    lines.extend(_numbered_report_lines(sections["blockers"][:3]))
    lines.append("下一步：")
    lines.extend(_numbered_report_lines(sections["next_steps"][:3]))
    return "\n".join(lines)


def _resolve_session(session_id: str | None) -> dict[str, Any]:
    sid = (session_id or "").strip()
    if sid:
        return _load_session(sid)
    active = get_active_session()
    if active:
        return active
    raise ValueError("no active work session")


def _resolve_project_path(project_path: str | None) -> Path | None:
    raw = (project_path or "").strip()
    if not raw:
        return get_app_root()
    return Path(raw).expanduser().resolve()


def _infer_project_name(project_path: Path | None) -> str:
    if not project_path:
        return ""
    return project_path.name


def _record_kernel_event(event_type: str, session_id: str, payload: dict[str, Any]) -> None:
    try:
        from l3_node.cognitive_kernel.ledger import append_event

        append_event(event_type, session_id, payload)
    except Exception:
        pass


def _remember_project_context(session: dict[str, Any]) -> None:
    try:
        from l3_node.work_ledger_project_memory import remember_project_from_session

        remember_project_from_session(session)
    except Exception:
        pass


def _append_memory_growth_raw(session: dict[str, Any], evidence: dict[str, Any]) -> None:
    try:
        from l3_node.cognitive_kernel.memory_growth import append_raw_event

        append_raw_event(
            category="evidence",
            source="work_ledger",
            stream="work_ledger",
            payload={"session": _session_index_row(session), "evidence": evidence},
            source_refs=[
                {
                    "type": "work_ledger",
                    "session_id": session.get("session_id"),
                    "evidence_id": evidence.get("evidence_id"),
                }
            ],
            review={
                "review_candidate": evidence.get("source") in {"manual_note", "work_output", "work_output_adoption", "work_process_candidate_feedback"},
                "promotion_targets": ["concepts", "playbooks", "outputs"],
                "priority": "high" if evidence.get("source") in {"manual_note", "work_output_adoption", "work_process_candidate_feedback"} else "normal",
                "reason": "work_ledger_evidence",
            },
        )
    except Exception:
        pass
