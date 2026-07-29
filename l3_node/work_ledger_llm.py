"""LLM organizer for Work Ledger outputs.

The Work Ledger collects evidence deterministically.  This module lets an LLM
only rewrite that evidence into clearer reports, then applies a lightweight
quality gate so bad generations do not replace the baseline outputs.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


def llm_refinement_enabled() -> bool:
    value = (os.environ.get("JACHIN_WORK_LEDGER_LLM_ENABLED") or "").strip().lower()
    if value in {"0", "false", "no", "off"}:
        return False
    return bool(_dashscope_key())


def build_evidence_digest(session: dict[str, Any], evidence: list[dict[str, Any]], *, max_chars: int = 18000) -> dict[str, Any]:
    git_payload = _latest_payload(evidence, "git_snapshot")
    file_payload = _latest_payload(evidence, "file_scan")
    snippet_payload = _latest_payload(evidence, "file_content_snippets")
    notes = [ev for ev in evidence if ev.get("source") == "manual_note"]
    ai_traces = [ev for ev in evidence if ev.get("source") == "ai_work_trace"]
    codex_consultations = [
        ev for ev in evidence if ev.get("source") == "codex_work_plan_consultation"
    ]
    digest: dict[str, Any] = {
        "fact_fusion_policy": _fact_fusion_policy(),
        "session": {
            "session_id": session.get("session_id"),
            "title": session.get("title"),
            "project_name": session.get("project_name"),
            "project_path": session.get("project_path"),
            "start_time": session.get("start_time"),
            "end_time": session.get("end_time"),
            "user_goal": session.get("user_goal"),
        },
        "trust_summary": _summarize_trust(evidence),
        "git": {
            "branch": git_payload.get("branch"),
            "status_summary": git_payload.get("status_summary"),
            "changed_files": (git_payload.get("changed_files") or [])[:80],
            "diff_stat": ((git_payload.get("commands") or {}).get("diff_stat") or {}).get("stdout", "")[:3000],
            "recent_log": ((git_payload.get("commands") or {}).get("log") or {}).get("stdout", "")[:3000],
        },
        "recent_files": (file_payload.get("recent_files") or [])[:80],
        "file_snippets": (snippet_payload.get("snippets") or [])[:20],
        "risk_candidates": (snippet_payload.get("risk_candidates") or [])[:30],
        "manual_notes": [
            {
                "summary": ev.get("summary"),
                "trust_level": ev.get("trust_level"),
                "text": (ev.get("payload") or {}).get("text") if isinstance(ev.get("payload"), dict) else "",
            }
            for ev in notes[-30:]
        ],
        "ai_work_traces": [
            {
                "summary": ev.get("summary"),
                "tool_name": (ev.get("payload") or {}).get("tool_name") if isinstance(ev.get("payload"), dict) else "",
                "text": (ev.get("payload") or {}).get("text") if isinstance(ev.get("payload"), dict) else "",
            }
            for ev in ai_traces[-16:]
        ],
        "codex_work_plan_consultations": [
            {
                "summary": ev.get("summary"),
                "trust_level": ev.get("trust_level"),
                "scenario_id": (ev.get("payload") or {}).get("scenario_id")
                if isinstance(ev.get("payload"), dict)
                else "",
                "answer": str((ev.get("payload") or {}).get("answer") or "")[:5000]
                if isinstance(ev.get("payload"), dict)
                else "",
                "answer_validation": (ev.get("payload") or {}).get(
                    "answer_validation"
                )
                if isinstance(ev.get("payload"), dict)
                else {},
                "claim_fusion": (ev.get("payload") or {}).get("claim_fusion")
                if isinstance(ev.get("payload"), dict)
                else {},
                "recovery": (ev.get("payload") or {}).get("recovery")
                if isinstance(ev.get("payload"), dict)
                else {},
                "recovery_terminal": (ev.get("payload") or {}).get(
                    "recovery_terminal"
                )
                if isinstance(ev.get("payload"), dict)
                else {},
            }
            for ev in codex_consultations[-8:]
        ],
    }
    text = json.dumps(digest, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return digest
    # Keep the most important sections when the digest grows.
    digest["file_snippets"] = digest["file_snippets"][:8]
    digest["risk_candidates"] = digest["risk_candidates"][:16]
    digest["recent_files"] = digest["recent_files"][:30]
    digest["ai_work_traces"] = [
        {**item, "text": str(item.get("text") or "")[:600]} for item in digest["ai_work_traces"][:8]
    ]
    digest["manual_notes"] = digest["manual_notes"][-16:]
    digest["codex_work_plan_consultations"] = digest[
        "codex_work_plan_consultations"
    ][-4:]
    return digest


def refine_work_outputs_with_llm(
    *,
    session: dict[str, Any],
    evidence: list[dict[str, Any]],
    baseline_report: str,
    baseline_prompt: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    digest = build_evidence_digest(session, evidence)
    model = _work_ledger_model()
    prompt = _build_prompt(digest, baseline_report=baseline_report, baseline_prompt=baseline_prompt)
    try:
        content = _call_dashscope(prompt, model=model, timeout=_timeout_seconds())
        parsed = _parse_json_object(content)
        quality = validate_refined_outputs(parsed, evidence)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": bool(quality.get("ok")),
            "model": model,
            "elapsed_ms": elapsed_ms,
            "outputs": parsed if quality.get("ok") else {},
            "quality": quality,
            "raw_preview": str(content or "")[:1200],
        }
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False,
            "model": model,
            "elapsed_ms": elapsed_ms,
            "outputs": {},
            "quality": {"ok": False, "issues": [f"llm_call_failed:{type(exc).__name__}"], "warnings": []},
            "error": str(exc)[:500],
        }


def refine_weekly_report_with_llm(*, index: dict[str, Any], baseline_report: str) -> dict[str, Any]:
    started = time.perf_counter()
    digest = build_weekly_digest(index)
    model = _work_ledger_model()
    prompt = _build_weekly_prompt(digest, baseline_report=baseline_report)
    try:
        content = _call_dashscope(prompt, model=model, timeout=_timeout_seconds())
        parsed = _parse_json_object(content)
        quality = validate_weekly_report_outputs(parsed, index)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": bool(quality.get("ok")),
            "model": model,
            "elapsed_ms": elapsed_ms,
            "outputs": parsed if quality.get("ok") else {},
            "quality": quality,
            "raw_preview": str(content or "")[:1200],
        }
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False,
            "model": model,
            "elapsed_ms": elapsed_ms,
            "outputs": {},
            "quality": {"ok": False, "issues": [f"llm_call_failed:{type(exc).__name__}"], "warnings": []},
            "error": str(exc)[:500],
        }


def refine_instant_work_brief_with_llm(
    *,
    index: dict[str, Any],
    baseline_brief: str,
) -> dict[str, Any]:
    """Turn current Work Ledger evidence into a concrete, human-readable brief."""

    started = time.perf_counter()
    digest = build_instant_brief_digest(index)
    model = _work_ledger_model()
    prompt = _build_instant_brief_prompt(digest, baseline_brief=baseline_brief)
    attempts: list[dict[str, Any]] = []
    try:
        parsed: dict[str, Any] = {}
        quality: dict[str, Any] = {
            "ok": False,
            "issues": ["brief_generation_not_started"],
            "warnings": [],
        }
        content = ""
        current_prompt = prompt
        for attempt_number in range(1, 3):
            content = _call_dashscope(
                current_prompt,
                model=model,
                timeout=_timeout_seconds(),
            )
            parsed = _parse_json_object(content)
            parsed["brief"] = _render_instant_brief_value(parsed.get("brief"))
            quality = validate_instant_brief_output(parsed, index)
            attempts.append(
                {
                    "attempt": attempt_number,
                    "ok": bool(quality.get("ok")),
                    "issues": list(quality.get("issues") or []),
                    "raw_preview": str(content or "")[:800],
                }
            )
            if quality.get("ok"):
                break
            current_prompt = _build_instant_brief_repair_prompt(
                original_prompt=prompt,
                rejected_output=str(parsed.get("brief") or ""),
                issues=list(quality.get("issues") or []),
            )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": bool(quality.get("ok")),
            "model": model,
            "elapsed_ms": elapsed_ms,
            "text": str(parsed.get("brief") or "").strip()
            if quality.get("ok")
            else "",
            "outputs": parsed if quality.get("ok") else {},
            "quality": quality,
            "raw_preview": str(content or "")[:1200],
            "attempts": attempts,
            "fusion_trace": parsed.get("fusion_trace") or {},
            "codex_fusion": digest.get("codex_fusion_context") or {},
        }
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "ok": False,
            "model": model,
            "elapsed_ms": elapsed_ms,
            "text": "",
            "outputs": {},
            "quality": {
                "ok": False,
                "issues": [f"llm_call_failed:{type(exc).__name__}"],
                "warnings": [],
            },
            "error": str(exc)[:500],
            "attempts": attempts,
            "fusion_trace": {},
            "codex_fusion": digest.get("codex_fusion_context") or {},
        }


def _collect_codex_consultations(index: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in index.get("recent_codex_consultations") or []:
        if isinstance(item, dict):
            rows.append(item)
    for digest in index.get("session_evidence_digests") or []:
        if not isinstance(digest, dict):
            continue
        for item in digest.get("codex_work_plan_consultations") or []:
            if isinstance(item, dict):
                rows.append(item)
    deduplicated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in reversed(rows):
        identities = {
            f"{key}:{str(item.get(key) or '').strip()}"
            for key in (
                "invocation_id",
                "prompt_hash",
                "tool_evidence_path",
                "report_path",
            )
            if str(item.get(key) or "").strip()
        }
        if not identities:
            identities = {
                "summary:"
                + str(item.get("summary") or id(item)).strip()
            }
        if identities & seen:
            continue
        seen.update(identities)
        deduplicated.append(item)
    deduplicated.reverse()
    return deduplicated


def _valid_codex_consultation(item: Any) -> bool:
    return bool(
        isinstance(item, dict)
        and item.get("ok")
        and str(item.get("answer") or "").strip()
    )


def build_codex_fusion_context(index: dict[str, Any]) -> dict[str, Any]:
    consultations = _collect_codex_consultations(index)
    successful = [item for item in consultations if _valid_codex_consultation(item)]
    accepted: list[dict[str, Any]] = []
    interpretations: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for item in successful:
        fusion = (
            item.get("claim_fusion")
            if isinstance(item.get("claim_fusion"), dict)
            else {}
        )
        for claim in fusion.get("claims") or []:
            if not isinstance(claim, dict):
                continue
            row = {
                "claim_id": claim.get("claim_id"),
                "text": claim.get("text"),
                "disposition": claim.get("disposition"),
                "evidence_refs": claim.get("evidence_refs") or [],
                "project_name": item.get("project_name"),
            }
            disposition = str(claim.get("disposition") or "")
            if disposition == "accepted_fact":
                accepted.append(row)
            elif disposition == "supported_interpretation":
                interpretations.append(row)
            elif disposition == "recommendation":
                recommendations.append(row)
            else:
                blocked.append(row)
    return {
        "consultation_count": len(consultations),
        "successful_reply_count": len(successful),
        "failed_reply_count": max(0, len(consultations) - len(successful)),
        "accepted_facts": accepted[:60],
        "supported_interpretations": interpretations[:60],
        "recommendations": recommendations[:40],
        "blocked_claims": blocked[:60],
        "usable_claim_count": len(accepted) + len(interpretations) + len(recommendations),
        "available_for_final_synthesis": bool(
            accepted or interpretations or recommendations
        ),
    }


def build_instant_brief_digest(
    index: dict[str, Any],
    *,
    max_chars: int = 30000,
) -> dict[str, Any]:
    codex_fusion_context = build_codex_fusion_context(index)
    session_digests: list[dict[str, Any]] = []
    for row in (index.get("session_evidence_digests") or [])[:30]:
        if not isinstance(row, dict):
            continue
        sanitized = dict(row)
        sanitized["codex_work_plan_consultations"] = [
            item
            for item in (row.get("codex_work_plan_consultations") or [])
            if _valid_codex_consultation(item)
        ]
        session_digests.append(sanitized)
    digest = {
        "fact_fusion_policy": _fact_fusion_policy(),
        "window_days": index.get("window_days"),
        "window_mode": index.get("window_mode"),
        "generated_at": index.get("generated_at"),
        "session_count": index.get("session_count"),
        "activity_dates": index.get("activity_dates") or [],
        "activity_day_count": index.get("activity_day_count") or 0,
        "git_commit_count": index.get("git_commit_count") or 0,
        "git_activity": index.get("git_activity") or [],
        "recent_project_files": (index.get("recent_project_files") or [])[:120],
        "project_counts": index.get("project_counts") or {},
        "verified_outcomes": (index.get("verified_outcomes") or [])[-60:],
        "valued_outcomes": (index.get("valued_outcomes") or [])[:60],
        "recent_notes": (index.get("recent_notes") or [])[-40:],
        "recent_ai_signals": (index.get("recent_ai_signals") or [])[-60:],
        "codex_fusion_context": codex_fusion_context,
        "recent_codex_consultations": [
            item
            for item in (index.get("recent_codex_consultations") or [])
            if _valid_codex_consultation(item)
        ][-30:],
        "session_evidence_digests": session_digests,
    }
    if len(json.dumps(digest, ensure_ascii=False, default=str)) <= max_chars:
        return digest

    compact_sessions: list[dict[str, Any]] = []
    for row in digest["session_evidence_digests"][:16]:
        if not isinstance(row, dict):
            continue
        git = row.get("git") if isinstance(row.get("git"), dict) else {}
        compact_sessions.append(
            {
                "session_id": row.get("session_id"),
                "title": row.get("title"),
                "status": row.get("status"),
                "project_name": row.get("project_name"),
                "user_goal": row.get("user_goal"),
                "git": {
                    "branch": git.get("branch"),
                    "status_summary": git.get("status_summary"),
                    "changed_files": (git.get("changed_files") or [])[:40],
                    "diff_stat": str(git.get("diff_stat") or "")[:1800],
                    "diff_patch": str(git.get("diff_patch") or "")[:6500],
                    "cached_diff_patch": str(
                        git.get("cached_diff_patch") or ""
                    )[:3500],
                },
                "file_snippets": (row.get("file_snippets") or [])[:8],
                "risk_candidates": (row.get("risk_candidates") or [])[:10],
                "manual_notes": (row.get("manual_notes") or [])[-10:],
                "ai_work_traces": (row.get("ai_work_traces") or [])[-6:],
                "codex_work_plan_consultations": (
                    [
                        item
                        for item in (row.get("codex_work_plan_consultations") or [])
                        if _valid_codex_consultation(item)
                    ]
                )[-3:],
                "daily_checkpoints": [
                    {
                        **checkpoint,
                        "changed_files": (checkpoint.get("changed_files") or [])[:30],
                        "diff_patch": str(checkpoint.get("diff_patch") or "")[:3000],
                        "cached_diff_patch": str(
                            checkpoint.get("cached_diff_patch") or ""
                        )[:1400],
                        "file_snippets": (
                            checkpoint.get("file_snippets") or []
                        )[:4],
                    }
                    for checkpoint in (row.get("daily_checkpoints") or [])[-14:]
                    if isinstance(checkpoint, dict)
                ],
            }
        )
    digest["session_evidence_digests"] = compact_sessions
    digest["verified_outcomes"] = digest["verified_outcomes"][-30:]
    digest["valued_outcomes"] = digest["valued_outcomes"][:30]
    digest["recent_notes"] = digest["recent_notes"][-20:]
    digest["recent_ai_signals"] = digest["recent_ai_signals"][-30:]
    digest["recent_codex_consultations"] = digest[
        "recent_codex_consultations"
    ][-12:]
    return digest


def validate_instant_brief_output(
    outputs: dict[str, Any],
    index: dict[str, Any],
) -> dict[str, Any]:
    issues: list[str] = []
    warnings: list[str] = []
    if not isinstance(outputs, dict):
        return {
            "ok": False,
            "issues": ["output_not_json_object"],
            "warnings": warnings,
        }
    text = str(outputs.get("brief") or "").strip()
    if not text:
        issues.append("missing_brief")
    if "```" in text:
        issues.append("brief_contains_code_fence")
    if "\x00" in text:
        issues.append("brief_contains_control_char")
    if "..." in text or "……" in text:
        issues.append("brief_may_be_truncated")
    if len(text) > 9000:
        issues.append("brief_too_long")
    required_sections = (
        "完成与推进",
        "涉及项目与模块",
        "风险与未完成",
        "下一步计划",
    )
    missing_sections = [section for section in required_sections if section not in text]
    if missing_sections:
        issues.append("brief_missing_sections:" + ",".join(missing_sections))
    if len(re.findall(r"(?m)^\s*\d+[.、)]\s+\S+", text)) < 4:
        issues.append("brief_not_itemized")
    git_activity = index.get("git_activity") or []
    commit_dates = {
        str(commit.get("authored_at") or "")[:10]
        for activity in git_activity
        if isinstance(activity, dict)
        for commit in (activity.get("commits") or [])
        if isinstance(commit, dict) and str(commit.get("authored_at") or "")[:10]
    }
    if commit_dates and not any(date in text for date in commit_dates):
        issues.append("brief_omits_git_window_activity")
    generic_patterns = (
        r"正在推进[：:]\s*\d{4}[-/]\d{1,2}[-/]\d{1,2}\s*工作记录",
        r"已记录任务[：:]",
        r"仍在进行[：:]\s*\d{4}[-/]\d{1,2}[-/]\d{1,2}\s*工作记录",
    )
    if any(re.search(pattern, text) for pattern in generic_patterns):
        issues.append("brief_uses_session_status_as_accomplishment")
    numbered_lines = re.findall(r"(?m)^\s*\d+[.、)]\s+(.+)$", text)
    path_inventory_lines = [
        line
        for line in numbered_lines
        if _extract_path_like_tokens(line)
        or re.search(
            r"(?:^|[（(])\s*(?:M|A|D|R|U|\?\?)\s*(?:[）)]|$)",
            line,
        )
    ]
    if path_inventory_lines:
        issues.append("brief_is_file_inventory")
    if (
        "变更证据：" in text
        or "提交证据：" in text
        or "文件时间证据：" in text
    ):
        issues.append("brief_exposes_raw_change_inventory")
    fusion_context = build_codex_fusion_context(index)
    fusion_trace = (
        outputs.get("fusion_trace")
        if isinstance(outputs.get("fusion_trace"), dict)
        else {}
    )
    accepted_ids = {
        str(item.get("claim_id") or "")
        for item in fusion_context.get("accepted_facts") or []
        if str(item.get("claim_id") or "")
    }
    interpretation_ids = {
        str(item.get("claim_id") or "")
        for item in fusion_context.get("supported_interpretations") or []
        if str(item.get("claim_id") or "")
    }
    recommendation_ids = {
        str(item.get("claim_id") or "")
        for item in fusion_context.get("recommendations") or []
        if str(item.get("claim_id") or "")
    }
    blocked_ids = {
        str(item.get("claim_id") or "")
        for item in fusion_context.get("blocked_claims") or []
        if str(item.get("claim_id") or "")
    }
    used_fact_ids = {
        str(value)
        for value in fusion_trace.get("used_claim_ids") or []
        if str(value).strip()
    }
    used_interpretation_ids = {
        str(value)
        for value in fusion_trace.get("used_interpretation_ids") or []
        if str(value).strip()
    }
    used_recommendation_ids = {
        str(value)
        for value in fusion_trace.get("used_recommendation_ids") or []
        if str(value).strip()
    }
    used_ids = used_fact_ids | used_interpretation_ids | used_recommendation_ids
    if fusion_context.get("available_for_final_synthesis") and not used_ids:
        issues.append("codex_fusion_not_consumed")
    if not fusion_context.get("available_for_final_synthesis") and used_ids:
        issues.append("codex_fusion_claims_fabricated")
    if used_fact_ids - accepted_ids:
        issues.append("codex_fusion_invalid_fact_ids")
    if used_interpretation_ids - interpretation_ids:
        issues.append("codex_fusion_invalid_interpretation_ids")
    if used_recommendation_ids - recommendation_ids:
        issues.append("codex_fusion_invalid_recommendation_ids")
    if used_ids & blocked_ids:
        issues.append("codex_fusion_uses_blocked_claim")
    copied = _find_codex_verbatim_copy(
        text,
        _codex_answers_from_index(index),
    )
    if copied:
        issues.append("brief_copies_codex_verbatim:" + copied[:80])
    blocked_claim = _find_disallowed_codex_claim_reference(
        text,
        _codex_claim_fusions_from_index(index),
    )
    if blocked_claim:
        issues.append(
            "brief_uses_disallowed_codex_claim:"
            + str(blocked_claim.get("claim_id") or blocked_claim.get("disposition"))
        )

    known_paths = _known_index_paths(index)
    unknown_paths = sorted(
        path
        for path in _extract_path_like_tokens(text)
        if not _path_is_known(path, known_paths)
    )
    if unknown_paths:
        issues.append("unknown_file_paths:" + ",".join(unknown_paths[:8]))

    progress_match = re.search(
        r"完成与推进(?P<body>.*?)(?:涉及项目与模块|风险与未完成)",
        text,
        flags=re.S,
    )
    progress_text = progress_match.group("body") if progress_match else ""
    concrete_verbs = (
        "新增",
        "实现",
        "修复",
        "调整",
        "重构",
        "接入",
        "补充",
        "迁移",
        "优化",
        "验证",
        "生成",
        "支持",
        "移除",
        "更新",
        "完成",
    )
    evidence_is_sparse = not any(
        (
            index.get("valued_outcomes"),
            index.get("verified_outcomes"),
            index.get("recent_notes"),
            index.get("recent_ai_signals"),
            _index_has_diff_or_snippets(index),
        )
    )
    if (
        not evidence_is_sparse
        and "证据不足" not in progress_text
        and not any(verb in progress_text for verb in concrete_verbs)
    ):
        issues.append("brief_progress_lacks_concrete_action")
    if "（M）" in text or re.search(r"（(?:A|M|D|R|U|\?\?)）", text):
        issues.append("brief_exposes_raw_git_status_as_result")
    if "证据" not in text and "依据" not in text:
        warnings.append("brief_missing_evidence_boundary")
    return {"ok": not issues, "issues": issues, "warnings": warnings}


def build_weekly_digest(index: dict[str, Any], *, max_chars: int = 22000) -> dict[str, Any]:
    digest = {
        "fact_fusion_policy": _fact_fusion_policy(),
        "window_days": index.get("window_days"),
        "generated_at": index.get("generated_at"),
        "session_count": index.get("session_count"),
        "project_counts": index.get("project_counts", {}),
        "evidence_source_counts": index.get("evidence_source_counts", {}),
        "sessions": (index.get("sessions") or [])[:80],
        "adopted_outputs": (index.get("adopted_outputs") or [])[-40:],
        "methodology_candidates": (index.get("methodology_candidates") or [])[-30:],
        "verified_outcomes": (index.get("verified_outcomes") or [])[-80:],
        "valued_outcomes": (index.get("valued_outcomes") or [])[:80],
        "value_summary": index.get("value_summary") or {},
        "graph_methodology_candidates": (
            index.get("graph_methodology_candidates") or []
        )[-40:],
        "recent_notes": (index.get("recent_notes") or [])[-50:],
        "recent_ai_signals": (index.get("recent_ai_signals") or [])[-60:],
        "recent_codex_consultations": (
            index.get("recent_codex_consultations") or []
        )[-30:],
    }
    text = json.dumps(digest, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return digest
    digest["sessions"] = digest["sessions"][:40]
    digest["adopted_outputs"] = digest["adopted_outputs"][-20:]
    digest["methodology_candidates"] = digest["methodology_candidates"][-16:]
    digest["verified_outcomes"] = digest["verified_outcomes"][-40:]
    digest["valued_outcomes"] = digest["valued_outcomes"][:40]
    digest["graph_methodology_candidates"] = digest[
        "graph_methodology_candidates"
    ][-20:]
    digest["recent_notes"] = digest["recent_notes"][-24:]
    digest["recent_ai_signals"] = digest["recent_ai_signals"][-28:]
    digest["recent_codex_consultations"] = digest[
        "recent_codex_consultations"
    ][-12:]
    return digest


def validate_weekly_report_outputs(outputs: dict[str, Any], index: dict[str, Any]) -> dict[str, Any]:
    issues: list[str] = []
    warnings: list[str] = []
    if not isinstance(outputs, dict):
        return {"ok": False, "issues": ["output_not_json_object"], "warnings": warnings}
    text = str(outputs.get("weekly_report") or "").strip()
    if not text:
        issues.append("missing_weekly_report")
    if "```" in text:
        issues.append("weekly_report_contains_code_fence")
    if "\x00" in text:
        issues.append("weekly_report_contains_control_char")
    if "..." in text or "……" in text:
        issues.append("weekly_report_may_be_truncated")
    if len(text) > 8000:
        issues.append("weekly_report_too_long")
    known_sessions = {str(item.get("session_id") or "") for item in (index.get("sessions") or []) if isinstance(item, dict)}
    mentioned_sessions = set(re.findall(r"work_\d{8}_[A-Za-z0-9]+", text))
    unknown_sessions = sorted(item for item in mentioned_sessions if item not in known_sessions)
    if unknown_sessions:
        issues.append("unknown_session_ids:" + ",".join(unknown_sessions[:8]))
    if not any(word in text for word in ("证据", "依据", "来源", "边界")):
        warnings.append("weekly_report_missing_evidence_boundary")
    if not any(word in text for word in ("风险", "未完成", "下一步")):
        warnings.append("weekly_report_missing_risk_or_next_step")
    copied = _find_codex_verbatim_copy(
        text,
        _codex_answers_from_index(index),
    )
    if copied:
        issues.append("weekly_report_copies_codex_verbatim:" + copied[:80])
    blocked_claim = _find_disallowed_codex_claim_reference(
        text,
        _codex_claim_fusions_from_index(index),
    )
    if blocked_claim:
        issues.append(
            "weekly_report_uses_disallowed_codex_claim:"
            + str(blocked_claim.get("claim_id") or blocked_claim.get("disposition"))
        )
    return {"ok": not issues, "issues": issues, "warnings": warnings}


def validate_refined_outputs(outputs: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    issues: list[str] = []
    warnings: list[str] = []
    if not isinstance(outputs, dict):
        return {"ok": False, "issues": ["output_not_json_object"], "warnings": warnings}
    required = ("daily_report", "continuation_prompt", "lark_brief")
    for key in required:
        if not str(outputs.get(key) or "").strip():
            issues.append(f"missing_{key}")
    for key in required:
        text = str(outputs.get(key) or "")
        if "```" in text:
            issues.append(f"{key}_contains_code_fence")
        if "\x00" in text:
            issues.append(f"{key}_contains_control_char")
    lark = str(outputs.get("lark_brief") or "").strip()
    if len(lark) > 520:
        issues.append("lark_brief_too_long")
    if "..." in lark or "……" in lark:
        issues.append("lark_brief_may_be_truncated")
    if re.search(r"\|[^|\n]+\|", lark):
        issues.append("lark_brief_contains_table_fragment")
    if len(re.findall(r"(?m)^\s*\d+[.、)]\s+\S+", lark)) < 2:
        issues.append("lark_brief_not_itemized")
    daily_report = str(outputs.get("daily_report") or "").strip()
    if len(re.findall(r"(?m)^\s*(?:[-*]\s+|\d+[.、)]\s+)\S+", daily_report)) < 3:
        issues.append("daily_report_not_itemized")
    known_paths = _known_evidence_paths(evidence)
    unknown_paths = sorted(_extract_path_like_tokens("\n".join(str(outputs.get(k) or "") for k in required)) - known_paths)
    if unknown_paths:
        issues.append("unknown_file_paths:" + ",".join(unknown_paths[:8]))
    risk_text = str(outputs.get("daily_report") or "")
    if ("已确认问题" in risk_text or "确定存在" in risk_text) and _risk_candidates(evidence):
        warnings.append("risk_candidates_should_not_be_overstated")
    copied = _find_codex_verbatim_copy(
        "\n".join(str(outputs.get(key) or "") for key in required),
        _codex_answers_from_evidence(evidence),
    )
    if copied:
        issues.append("work_outputs_copy_codex_verbatim:" + copied[:80])
    blocked_claim = _find_disallowed_codex_claim_reference(
        "\n".join(str(outputs.get(key) or "") for key in required),
        _codex_claim_fusions_from_evidence(evidence),
    )
    if blocked_claim:
        issues.append(
            "work_outputs_use_disallowed_codex_claim:"
            + str(blocked_claim.get("claim_id") or blocked_claim.get("disposition"))
        )
    return {"ok": not issues, "issues": issues, "warnings": warnings}


def _build_prompt(digest: dict[str, Any], *, baseline_report: str, baseline_prompt: str) -> str:
    return (
        "你是 Jachin Work Ledger 的工作复盘编辑器。只允许基于给定证据整理表达，禁止编造。\n"
        "请输出严格 JSON，不要 Markdown 代码块。JSON keys 必须是：daily_report, continuation_prompt, lark_brief。\n"
        "规则：\n"
        "1. daily_report 用中文，像真实工作日报；必须按“完成与推进、涉及模块、风险与未完成、下一步”分节，每节逐条编号，禁止整段叙述。\n"
        "2. continuation_prompt 是明天交给 Codex/Cursor 的任务书，必须提醒先读真实文件和 git 状态。\n"
        "3. lark_brief <= 500 中文字，语气自然，可以直接发同事；必须逐条编号，至少包含完成与推进、风险与未完成、下一步三部分。\n"
        "4. 只能引用 evidence_digest 中出现的文件、风险、记录；风险候选不能写成已确认问题。\n"
        "5. codex_work_plan_consultations 是解释性协作记录，只能辅助说明改动含义、决策、风险和下一步；"
        "不能单独证明完成、测试通过或交付，也禁止直接复制或轻微改写 Codex 原句。\n"
        "6. 只允许使用 claim_fusion 中 disposition=accepted_fact 的 claim 陈述事实；"
        "supported_interpretation 只能帮助理解，recommendation 只能进入下一步，"
        "unknown_requires_confirmation 和 rejected_conflict 禁止写入成果。\n"
        "7. 生成前必须先在内部完成事实融合：汇总全部来源、按信任级别解决冲突、区分事实与解释，"
        "然后以 Jachin 的口吻重新组织。最终文本不能表现为 Codex 回复的转发或摘录。\n"
        "8. 不要输出省略号、半句话、表格残片、代码块。\n\n"
        "evidence_digest:\n"
        f"{json.dumps(digest, ensure_ascii=False, default=str)}\n\n"
        "baseline_report:\n"
        f"{baseline_report[:10000]}\n\n"
        "baseline_continuation_prompt:\n"
        f"{baseline_prompt[:8000]}"
    )


def _build_weekly_prompt(digest: dict[str, Any], *, baseline_report: str) -> str:
    return (
        "你是 Jachin Work Ledger 的周报编辑器。只允许基于给定 weekly_digest 和 baseline_report 整理表达，禁止编造。\n"
        "请输出严格 JSON，不要 Markdown 代码块。JSON keys 必须包含 weekly_report。\n"
        "要求：\n"
        "1. weekly_report 用中文，像真实可发给领导/团队的周报，结构清楚，有重点和价值说明。\n"
        "2. 成果只能来自 verified_outcomes；优先按 valued_outcomes 的 impact > adopted > delivered > completed 顺序表达。\n"
        "3. 文件数量、聊天数量、证据数量和 adopted_outputs 不能写成成果；负面价值反馈不得改写原始完成事实。\n"
        "4. 只有 status=approved 的 graph_methodology_candidates 才能写成已沉淀方法论，pending_review 只能列为待确认。\n"
        "5. 必须保留证据边界：说明来自 Work Ledger session、用户采纳输出、手动记录和 AI 过程导入。\n"
        "6. user_confirmed 可以作为成果重点；system_observed 只能作为事实线索。\n"
        "7. recent_codex_consultations 只能作为解释性信息，必须和其他证据融合后由 Jachin 重新表达；"
        "禁止直接复制或轻微改写 Codex 原句。\n"
        "8. claim_fusion 中 accepted_fact 才能写作事实；supported_interpretation 只能解释，"
        "recommendation 只能进入下一步；待确认和冲突 claim 不得进入成果。\n"
        "9. 生成前必须先整合全部项目、事实、用户记录、验证结果、风险和 Codex 信息，再开始撰写最终周报。\n"
        "10. 不要输出省略号、半句话、表格残片、代码块；不要编造未出现的项目、文件、session 或风险。\n"
        "11. 控制在 1800 中文字以内。\n\n"
        "weekly_digest:\n"
        f"{json.dumps(digest, ensure_ascii=False, default=str)}\n\n"
        "baseline_report:\n"
        f"{baseline_report[:12000]}"
    )


def _build_instant_brief_prompt(
    digest: dict[str, Any],
    *,
    baseline_brief: str,
) -> str:
    return (
        "你是 Jachin Work Ledger 的高级工作汇报编辑器。任务是把真实工作证据整理成让同事和领导能看懂的中文简报。\n"
        "只输出合法 JSON，必须包含 brief 和 fusion_trace 两个 key；brief 是完整 Markdown 字符串；"
        "fusion_trace 是对象，包含 used_claim_ids、used_interpretation_ids、used_recommendation_ids、ignored_claim_ids 四个字符串数组。"
        "不要返回分节对象，不要输出 Markdown 代码围栏。\n"
        "硬性规则：\n"
        "1. brief 必须包含“完成与推进、涉及项目与模块、风险与未完成、下一步计划、依据边界”五部分，前四部分逐条编号。\n"
        "2. “完成与推进”必须是工作成果清单，每条写清楚完成或推进了什么能力、解决了什么问题、产生了什么结果或价值；"
        "优先使用“完成、实现、修复、接入、优化、验证、沉淀”等动作开头，风格类似可直接填写到日报/周报的工作事项。\n"
        "2.1 禁止把文件路径、文件名、目录名、M/A/D 状态、行数或“修改了若干文件”写成工作成果。"
        "文件和 diff 只能作为判断成果的后台证据；多个相关文件必须合并概括成一条能力级成果。\n"
        "2.2 在证据足够时输出 10-30 条不重复的成果；证据不足时宁可减少条目并明确边界，禁止为了数量凑泛化句。\n"
        "2.3 “涉及项目与模块”只能写业务模块或能力域名称，例如“常开语音与指令理解”“工作账本与复盘”；"
        "禁止在这一部分列源文件路径。\n"
        "3. 必须读取 git.diff_patch、cached_diff_patch、file_snippets、manual_notes、ai_work_traces 后再概括；不能只复述文件名、任务标题或 Git 的 M/A/D 状态。\n"
        "3.1 多日简报必须同时读取 git_activity：按日期说明窗口内真实提交；即使 Work Ledger 只有一个跨日 session，也不能漏掉此前几天的 Git 提交。\n"
        "4. 文件变化只能证明发生过改动，不能单独证明功能已完成；没有测试证据时不得声称测试通过，没有用户确认时不得声称已交付或已产生业务价值。\n"
        "5. 如果证据不足以判断改动含义，明确写“证据不足”，不要把“工作记录”“任务进行中”包装成成果。\n"
        "6. 风险候选只能写成待核查风险；下一步必须来自现有 next_steps、未完成证据或与当前改动直接相关的可执行验证动作。\n"
        "7. 禁止编造文件、项目、模块、数字、测试结果、用户反馈和完成状态；禁止省略号、半句话、表格残片。\n"
        "8. codex_work_plan_consultations 是 Codex 对本地证据的解释性补充，只能用于说明改动含义、风险和建议；"
        "它的 trust_level=system_observed，不能单独证明功能完成、测试通过或已经交付，也禁止直接复制或轻微改写其原句。\n"
        "9. Codex claim 只能按 claim_fusion 处置：accepted_fact 可写事实，supported_interpretation 只作解释，"
        "recommendation 只进下一步，unknown_requires_confirmation 与 rejected_conflict 禁止进入成果。\n"
        "9.1 如果 codex_fusion_context.available_for_final_synthesis=true，必须重新分析其中可用结论，并在 fusion_trace 中准确列出实际使用的 claim ID；"
        "不得照搬 Codex 原文。若没有采用某条结论，将其 ID 写入 ignored_claim_ids。\n"
        "9.2 如果没有经过验证的 Codex 回复，fusion_trace 四个数组必须为空，禁止假装已结合 Codex。\n"
        "10. 开始撰写前必须完成统一事实融合：把用户确认、成果验证、Git/文件/运行证据、过程记录和 Codex 解释"
        "放在一起核对，解决冲突并区分事实与推断；然后由 Jachin 重新生成完整简报，不能把任何单一来源直接拼接成正文。\n"
        "11. 语言自然、简洁，像真实工作汇报，不要暴露内部 JSON 字段名。全文建议 500-1800 中文字。\n\n"
        "evidence_digest:\n"
        f"{json.dumps(digest, ensure_ascii=False, default=str)}\n\n"
        "baseline_brief（仅用于统计范围和证据边界，不得照抄其中的泛化句）：\n"
        f"{baseline_brief[:9000]}"
    )


def _build_instant_brief_repair_prompt(
    *,
    original_prompt: str,
    rejected_output: str,
    issues: list[str],
) -> str:
    return (
        f"{original_prompt}\n\n"
        "上一版输出未通过 Jachin 质量门禁。请吸收失败原因后完整重写，不要解释错误，也不要只修局部。\n"
        f"quality_gate_issues:\n{json.dumps(issues, ensure_ascii=False)}\n\n"
        "rejected_output:\n"
        f"{rejected_output[:9000]}\n\n"
        "重写要求：正文必须是能力级工作成果清单，删除所有源文件路径、Git M/A/D 状态和内部字段名；"
        "Codex 内容只能按 codex_fusion_context 与 fusion_trace 协议融合。"
    )


def _call_dashscope(prompt: str, *, model: str, timeout: int) -> str:
    api_key = _dashscope_key()
    if not api_key:
        raise RuntimeError("missing_dashscope_api_key")
    api_base = (
        os.environ.get("JACHIN_WORK_LEDGER_API_BASE")
        or os.environ.get("DASHSCOPE_API_BASE_CN")
        or os.environ.get("DASHSCOPE_API_BASE")
        or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ).rstrip("/")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是严谨的工作复盘编辑器，只输出合法 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.25,
        "max_tokens": 2200,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        f"{api_base}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    return str(data["choices"][0]["message"]["content"])


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            raise
        obj = json.loads(match.group(0))
    if not isinstance(obj, dict):
        raise ValueError("llm_json_not_object")
    return obj


def _dashscope_key() -> str:
    try:
        from core.config import get_effective_qwen_api_key

        value = get_effective_qwen_api_key()
        if value:
            return str(value).strip()
    except Exception:
        pass
    for key in ("DASHSCOPE_API_KEY_CN", "DASHSCOPE_API_KEY", "QWEN_API_KEY", "QWEN_AI_API_KEY"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    return ""


def _work_ledger_model() -> str:
    raw = (
        os.environ.get("JACHIN_WORK_LEDGER_LLM_MODEL")
        or os.environ.get("LLM_COMPLEX_MODEL")
        or "qwen-max"
    ).strip()
    return raw.split("/", 1)[1] if raw.startswith("dashscope/") else raw


def _timeout_seconds() -> int:
    try:
        return max(8, min(int(os.environ.get("JACHIN_WORK_LEDGER_LLM_TIMEOUT") or "60"), 180))
    except ValueError:
        return 60


def _latest_payload(evidence: list[dict[str, Any]], source: str) -> dict[str, Any]:
    for ev in reversed(evidence):
        if ev.get("source") == source and isinstance(ev.get("payload"), dict):
            return ev["payload"]
    return {}


def _summarize_trust(evidence: list[dict[str, Any]]) -> dict[str, int]:
    rows: dict[str, int] = {}
    for ev in evidence:
        key = str(ev.get("trust_level") or "system_observed")
        rows[key] = rows.get(key, 0) + 1
    return rows


def _fact_fusion_policy() -> dict[str, Any]:
    return {
        "final_author": "jachin",
        "required_process": [
            "collect_all_sources",
            "resolve_trust_and_conflicts",
            "separate_fact_from_interpretation",
            "compose_new_brief",
            "run_quality_gate",
        ],
        "source_precedence": [
            "user_confirmed",
            "verified_outcome_or_test",
            "git_file_and_runtime_evidence",
            "system_observed",
            "codex_interpretation",
        ],
        "codex_role": "context_only",
        "codex_direct_quote_allowed": False,
        "codex_claim_dispositions": {
            "accepted_fact": "may_support factual output",
            "supported_interpretation": "context only; cannot prove completion",
            "recommendation": "next-step section only",
            "unknown_requires_confirmation": "blocked from output",
            "rejected_conflict": "blocked from output and retained as conflict",
        },
        "rule": (
            "Codex 回复只能作为待核验的解释性信息。Jachin 必须与用户记录、"
            "Git、文件、测试、运行结果和其他工作证据融合后重新组织表达。"
        ),
    }


def _codex_answers_from_evidence(evidence: list[dict[str, Any]]) -> list[str]:
    answers: list[str] = []
    for item in evidence:
        if item.get("source") != "codex_work_plan_consultation":
            continue
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        answer = str(payload.get("answer") or "").strip()
        if answer:
            answers.append(answer)
    return answers


def _codex_answers_from_index(index: dict[str, Any]) -> list[str]:
    answers: list[str] = []
    for item in index.get("recent_codex_consultations") or []:
        if isinstance(item, dict):
            answer = str(item.get("answer") or "").strip()
            if answer:
                answers.append(answer)
    for row in index.get("session_evidence_digests") or []:
        if not isinstance(row, dict):
            continue
        for item in row.get("codex_work_plan_consultations") or []:
            if not isinstance(item, dict):
                continue
            answer = str(item.get("answer") or "").strip()
            if answer:
                answers.append(answer)
    return list(dict.fromkeys(answers))


def _codex_claim_fusions_from_evidence(
    evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    fusions: list[dict[str, Any]] = []
    for item in evidence:
        if item.get("source") != "codex_work_plan_consultation":
            continue
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        fusion = payload.get("claim_fusion")
        if isinstance(fusion, dict):
            fusions.append(fusion)
    return fusions


def _codex_claim_fusions_from_index(index: dict[str, Any]) -> list[dict[str, Any]]:
    fusions: list[dict[str, Any]] = []
    for item in index.get("recent_codex_consultations") or []:
        if not isinstance(item, dict):
            continue
        fusion = item.get("claim_fusion")
        if isinstance(fusion, dict):
            fusions.append(fusion)
    for row in index.get("session_evidence_digests") or []:
        if not isinstance(row, dict):
            continue
        for item in row.get("codex_work_plan_consultations") or []:
            if not isinstance(item, dict):
                continue
            fusion = item.get("claim_fusion")
            if isinstance(fusion, dict):
                fusions.append(fusion)
    return fusions


def _normalize_copy_text(value: str) -> str:
    text = re.sub(r"`[^`]*`", lambda match: match.group(0).strip("`"), value or "")
    text = re.sub(r"(?m)^\s*(?:[-*•]\s+|\d+[.、)]\s*)", "", text)
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE).lower()


def _copy_segments(value: str) -> list[str]:
    segments = re.split(r"[\n。！？!?；;]+", str(value or ""))
    return [
        segment.strip()
        for segment in segments
        if len(_normalize_copy_text(segment)) >= 24
    ]


def _find_codex_verbatim_copy(output: str, answers: list[str]) -> str:
    output_segments = _copy_segments(output)
    normalized_output = _normalize_copy_text(output)
    if not output_segments or not normalized_output:
        return ""
    for answer in answers:
        for source_segment in _copy_segments(answer):
            normalized_source = _normalize_copy_text(source_segment)
            if normalized_source in normalized_output:
                return source_segment
            for output_segment in output_segments:
                normalized_candidate = _normalize_copy_text(output_segment)
                if abs(len(normalized_source) - len(normalized_candidate)) > max(
                    18,
                    int(len(normalized_source) * 0.35),
                ):
                    continue
                if (
                    SequenceMatcher(
                        None,
                        normalized_source,
                        normalized_candidate,
                        autojunk=False,
                    ).ratio()
                    >= 0.9
                ):
                    return source_segment
    return ""


def _find_disallowed_codex_claim_reference(
    output: str,
    fusions: list[dict[str, Any]],
) -> dict[str, str] | None:
    blocked = {"unknown_requires_confirmation", "rejected_conflict"}
    output_segments = _copy_segments(output)
    if not output_segments:
        output_segments = [
            line.strip()
            for line in str(output or "").splitlines()
            if len(_normalize_copy_text(line)) >= 10
        ]
    for fusion in fusions:
        for claim in fusion.get("claims") or []:
            if not isinstance(claim, dict):
                continue
            disposition = str(claim.get("disposition") or "")
            if disposition not in blocked:
                continue
            claim_text = str(claim.get("text") or "").strip()
            normalized_claim = _normalize_copy_text(claim_text)
            if len(normalized_claim) < 10:
                continue
            for segment in output_segments:
                normalized_segment = _normalize_copy_text(segment)
                if len(normalized_segment) < 10:
                    continue
                contains_claim = (
                    normalized_claim in normalized_segment
                    or normalized_segment in normalized_claim
                )
                similar = (
                    abs(len(normalized_claim) - len(normalized_segment))
                    <= max(24, int(len(normalized_claim) * 0.55))
                    and SequenceMatcher(
                        None,
                        normalized_claim,
                        normalized_segment,
                        autojunk=False,
                    ).ratio()
                    >= 0.74
                )
                if contains_claim or similar:
                    return {
                        "claim_id": str(claim.get("claim_id") or ""),
                        "text": claim_text,
                        "disposition": disposition,
                    }
    return None


def _known_evidence_paths(evidence: list[dict[str, Any]]) -> set[str]:
    paths: set[str] = set()
    for ev in evidence:
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        for key in ("changed_files", "recent_files", "snippets", "risk_candidates"):
            for item in payload.get(key) or []:
                if isinstance(item, dict) and item.get("path"):
                    paths.add(str(item["path"]).replace("\\", "/"))
    return paths


def _extract_path_like_tokens(text: str) -> set[str]:
    tokens = set(
        re.findall(
            r"[\w./-]+\.(?:tsx|ts|py|rs|md|json|yaml|yml|ps1|toml|vue)",
            text or "",
            flags=re.I,
        )
    )
    return {token.strip("`'\"，,。；;:：()[]{}").replace("\\", "/") for token in tokens}


def _risk_candidates(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = _latest_payload(evidence, "file_content_snippets")
    return [x for x in payload.get("risk_candidates") or [] if isinstance(x, dict)]


def _known_index_paths(index: dict[str, Any]) -> set[str]:
    paths = {
        str(item.get("path") or "").replace("\\", "/")
        for item in (index.get("recent_changed_files") or [])
        if isinstance(item, dict) and item.get("path")
    }
    for digest in index.get("session_evidence_digests") or []:
        if not isinstance(digest, dict):
            continue
        git = digest.get("git") if isinstance(digest.get("git"), dict) else {}
        for item in git.get("changed_files") or []:
            if isinstance(item, dict) and item.get("path"):
                paths.add(str(item["path"]).replace("\\", "/"))
        for key in ("file_snippets", "risk_candidates"):
            for item in digest.get(key) or []:
                if isinstance(item, dict) and item.get("path"):
                    paths.add(str(item["path"]).replace("\\", "/"))
    return {path for path in paths if path}


def _index_has_diff_or_snippets(index: dict[str, Any]) -> bool:
    for digest in index.get("session_evidence_digests") or []:
        if not isinstance(digest, dict):
            continue
        git = digest.get("git") if isinstance(digest.get("git"), dict) else {}
        if str(git.get("diff_patch") or "").strip():
            return True
        if str(git.get("cached_diff_patch") or "").strip():
            return True
        if digest.get("file_snippets"):
            return True
    return False


def _path_is_known(path: str, known_paths: set[str]) -> bool:
    normalized = path.replace("\\", "/").strip("./")
    if normalized in known_paths:
        return True
    basename = normalized.rsplit("/", 1)[-1]
    return any(
        known.replace("\\", "/").strip("./").rsplit("/", 1)[-1] == basename
        for known in known_paths
    )


def _render_instant_brief_value(value: Any) -> str:
    if isinstance(value, str):
        text = re.sub(r"\\+r\\+n", "\n", value)
        text = re.sub(r"\\+n", "\n", text)
        return text.strip()
    if not isinstance(value, dict):
        return ""
    aliases = (
        ("完成与推进", ("完成与推进", "一、完成与推进")),
        ("涉及项目与模块", ("涉及项目与模块", "二、涉及项目与模块")),
        ("风险与未完成", ("风险与未完成", "三、风险与未完成")),
        ("下一步计划", ("下一步计划", "四、下一步计划")),
    )
    lines = ["# 工作简报", ""]
    for index, (heading, keys) in enumerate(aliases, start=1):
        raw = next((value.get(key) for key in keys if key in value), [])
        if isinstance(raw, str):
            items = [raw]
        elif isinstance(raw, list):
            items = [str(item).strip() for item in raw if str(item).strip()]
        else:
            items = []
        lines.extend([f"## {_section_index_label(index)}、{heading}", ""])
        lines.extend(
            f"{item_index}. {item}"
            for item_index, item in enumerate(items or ["证据不足。"], start=1)
        )
        lines.append("")
    boundary = value.get("依据边界") or value.get("证据边界") or ""
    if isinstance(boundary, list):
        boundary_items = [str(item).strip() for item in boundary if str(item).strip()]
    else:
        boundary_items = [str(boundary).strip()] if str(boundary).strip() else []
    lines.extend(["## 依据边界", ""])
    lines.extend(
        f"{item_index}. {item}"
        for item_index, item in enumerate(
            boundary_items
            or ["结论仅来自 Work Ledger 保存的任务证据，未记录事项不作完成判断。"],
            start=1,
        )
    )
    return "\n".join(lines).strip()


def _section_index_label(value: int) -> str:
    return {1: "一", 2: "二", 3: "三", 4: "四"}.get(value, str(value))
