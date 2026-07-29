"""Completion and extraction protocol for Codex desktop replies.

This module is deliberately UI-framework agnostic. The Windows automation
layer supplies OCR text and accessibility controls; the protocol decides
whether generation is complete and whether an extracted reply is trustworthy.
"""
from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from typing import Any, Iterable


_ACTIVE_MARKERS = (
    "running",
    "reconnecting",
    "regenerating",
    "generating",
    "stopgenerating",
    "stopgeneration",
    "正在运行",
    "正在重新连接",
    "重新连接",
    "正在生成",
    "停止生成",
)
_STOP_LABELS = (
    "stop generating",
    "stop generation",
    "停止生成",
    "停止回答",
)
_COPY_LABELS = (
    "copy response",
    "copy answer",
    "copy",
    "复制回答",
    "复制回复",
    "复制",
)
_PERMISSION_MARKERS = (
    "approval required",
    "permission required",
    "confirm to continue",
    "需要批准",
    "等待批准",
    "是否允许",
    "允许此操作",
    "需要确认",
    "确认后继续",
)
_APPROVE_ACTION_MARKERS = (
    "approve",
    "allow",
    "allow once",
    "批准",
    "允许",
)
_DENY_ACTION_MARKERS = (
    "deny",
    "reject",
    "cancel",
    "拒绝",
    "取消",
)
_ERROR_MARKERS = (
    "network error",
    "connection failed",
    "request failed",
    "something went wrong",
    "rate limit",
    "context window",
    "failed to generate",
    "网络错误",
    "连接失败",
    "请求失败",
    "生成失败",
    "出了点问题",
    "达到速率限制",
    "上下文长度",
)
_RETRY_ACTION_MARKERS = (
    "retry",
    "try again",
    "重试",
    "再试一次",
)
_DONE_STATUS_PATTERNS = (
    re.compile(r"^\s*已处理\s*[<>]?\s*(?:\d+\s*[分秒ms])+.*$", re.I),
    re.compile(
        r"^\s*(?:worked|completed|processed)\s+(?:for|in)\s+"
        r"(?:\d+\s*(?:h|m|s|ms|hours?|minutes?|seconds?))+.*$",
        re.I,
    ),
)
_TRUNCATED_SUFFIXES = (
    "...",
    "…",
    "，",
    ",",
    "：",
    ":",
    "；",
    ";",
    "、",
    " and",
    " or",
    " but",
    "以及",
    "并且",
    "或者",
    "但是",
)


def _compact(value: Any) -> str:
    return re.sub(r"[\W_]+", "", str(value or "").casefold(), flags=re.UNICODE)


def _control_labels(controls: Iterable[dict[str, Any]] | None) -> list[str]:
    labels: list[str] = []
    for control in controls or []:
        if not isinstance(control, dict):
            continue
        for key in ("name", "label", "automation_id", "help_text"):
            value = str(control.get(key) or "").strip()
            if value:
                labels.append(value)
    return labels


def _short_marker_visible(
    lines: Iterable[str],
    markers: Iterable[str],
    *,
    extra_chars: int,
) -> bool:
    for line in lines:
        line_compact = _compact(line)
        if not line_compact:
            continue
        for marker in markers:
            marker_compact = _compact(marker)
            if not marker_compact:
                continue
            if line_compact == marker_compact:
                return True
            if (
                len(line_compact) <= len(marker_compact) + max(0, extra_chars)
                and (
                    line_compact.startswith(marker_compact)
                    or line_compact.endswith(marker_compact)
                )
            ):
                return True
    return False


def text_fingerprint(text: str) -> str:
    normalized = "\n".join(
        re.sub(r"\s+", " ", line).strip()
        for line in str(text or "").splitlines()
        if line.strip()
    )
    return hashlib.sha256(normalized.casefold().encode("utf-8")).hexdigest()


def advance_wait_state(
    previous: dict[str, Any] | None,
    *,
    ocr_text: str,
    controls: Iterable[dict[str, Any]] | None = None,
    invocation_marker: str = "",
    elapsed_seconds: float = 0.0,
    minimum_reply_chars: int = 80,
    stable_samples_required: int = 1,
    allow_deferred_marker_completion: bool = False,
    deferred_marker_min_seconds: float = 20.0,
    deferred_marker_stable_samples: int = 3,
) -> dict[str, Any]:
    """Advance the multi-signal Codex generation state machine."""

    previous = dict(previous or {})
    text = str(ocr_text or "")
    labels = _control_labels(controls)
    labels_compact = _compact("\n".join(labels))
    observable = "\n".join([text, *labels])
    observable_compact = _compact(observable)
    # Permission phrases inside an older answer are not active permission UI.
    # Prefer accessibility controls; when UIA is unavailable, only inspect the
    # current bottom action area represented by the final OCR lines.
    status_lines = labels if labels else text.splitlines()[-14:]
    fingerprint = text_fingerprint(text)
    previous_fingerprint = str(previous.get("fingerprint") or "")
    stable_samples = (
        int(previous.get("stable_samples") or 0) + 1
        if fingerprint and fingerprint == previous_fingerprint
        else 0
    )

    # Generation controls are UI state, not answer content. Looking for words
    # such as "running" in the whole OCR transcript makes completed technical
    # answers appear permanently active.
    stop_visible = any(_compact(marker) in labels_compact for marker in _STOP_LABELS)
    generation_text_visible = any(
        _compact(marker) in labels_compact for marker in _ACTIVE_MARKERS
    )
    copy_visible = _short_marker_visible(
        status_lines,
        _COPY_LABELS,
        extra_chars=8,
    )
    explicit_permission_required = _short_marker_visible(
        status_lines,
        _PERMISSION_MARKERS,
        extra_chars=12,
    )
    approval_action_visible = _short_marker_visible(
        status_lines,
        _APPROVE_ACTION_MARKERS,
        extra_chars=8,
    )
    deny_action_visible = _short_marker_visible(
        status_lines,
        _DENY_ACTION_MARKERS,
        extra_chars=8,
    )
    permission_required = bool(
        explicit_permission_required
        or (approval_action_visible and deny_action_visible)
    )
    explicit_error_visible = _short_marker_visible(
        status_lines,
        _ERROR_MARKERS,
        extra_chars=32,
    )
    retry_action_visible = _short_marker_visible(
        status_lines,
        _RETRY_ACTION_MARKERS,
        extra_chars=8,
    )
    error_visible = bool(explicit_error_visible)
    # Codex renders the elapsed-time completion badge near the top of the
    # current response, while action controls live at the bottom. It is safe
    # to inspect all OCR lines here because the pattern requires a complete,
    # short status line with a duration rather than a word from answer text.
    done_visible = any(
        pattern.match(str(line or "").strip())
        for line in text.splitlines()
        for pattern in _DONE_STATUS_PATTERNS
    )
    exact_marker_found = bool(
        invocation_marker and invocation_marker in observable
    )
    compact_marker = _compact(invocation_marker)
    compact_marker_occurrences = (
        observable_compact.count(compact_marker) if compact_marker else 0
    )
    # OCR commonly confuses the colon in ``[JACHIN_REF:...]`` with a
    # semicolon. A fuzzy marker is accepted only when the same invocation id
    # is visible twice: once in the submitted prompt and once in the answer.
    # Extracted reply validation remains exact.
    fuzzy_marker_found = bool(
        not exact_marker_found and compact_marker_occurrences >= 2
    )
    marker_visible = bool(exact_marker_found or fuzzy_marker_found)
    marker_seen = bool(previous.get("marker_seen") or marker_visible)
    previous_marker_mode = str(previous.get("marker_match_mode") or "")
    marker_match_mode = (
        "exact"
        if exact_marker_found
        else "ocr_tolerant"
        if fuzzy_marker_found
        else previous_marker_mode
        if marker_seen
        else "missing"
    )
    reply_long_enough = len(text.strip()) >= max(1, int(minimum_reply_chars))
    stable = stable_samples >= max(1, int(stable_samples_required))
    deferred_marker_ready = bool(
        allow_deferred_marker_completion
        and not marker_seen
        and float(elapsed_seconds or 0.0)
        >= max(0.0, float(deferred_marker_min_seconds or 0.0))
        and stable_samples
        >= max(1, int(deferred_marker_stable_samples or 1))
        and reply_long_enough
    )
    active = bool(stop_visible or generation_text_visible)
    copy_ready = bool(
        previous.get("marker_seen")
        and marker_seen
        and copy_visible
        and not active
        and reply_long_enough
    )

    if permission_required:
        status = "permission_required"
    elif error_visible:
        status = "generation_error"
    elif active:
        status = "generating"
    elif (
        marker_seen
        and done_visible
        and not active
        and reply_long_enough
    ) or copy_ready or (
        stable and reply_long_enough and (marker_seen or deferred_marker_ready)
    ):
        status = "complete"
    elif marker_seen:
        status = "reply_observed"
    else:
        status = "waiting"

    return {
        "status": status,
        "complete": status == "complete",
        "active": active,
        "stop_visible": stop_visible,
        "copy_visible": copy_visible,
        "done_visible": done_visible,
        "permission_required": permission_required,
        "error_visible": error_visible,
        "retry_action_visible": retry_action_visible,
        "marker_found": marker_seen,
        "marker_seen": marker_seen,
        "marker_visible": marker_visible,
        "marker_match_mode": marker_match_mode,
        "marker_validation_deferred": bool(
            deferred_marker_ready and not marker_seen
        ),
        "marker_occurrences": compact_marker_occurrences,
        "reply_long_enough": reply_long_enough,
        "stable": stable,
        "stable_samples": stable_samples,
        "stable_samples_required": max(1, int(stable_samples_required)),
        "fingerprint": fingerprint,
        "text_length": len(text.strip()),
        "control_label_count": len(labels),
        "elapsed_seconds": round(float(elapsed_seconds or 0.0), 2),
        "completion_signals": {
            "generation_inactive": not active,
            "reply_stable": stable,
            "invocation_marker_found": marker_seen,
            "invocation_marker_visible": marker_visible,
            "invocation_marker_validation_deferred": bool(
                deferred_marker_ready and not marker_seen
            ),
            "reply_long_enough": reply_long_enough,
            "copy_control_visible": copy_visible,
            "done_status_visible": done_visible,
            "copy_ready": copy_ready,
        },
    }


def _balanced(text: str, opening: str, closing: str) -> bool:
    return text.count(opening) == text.count(closing)


def _looks_truncated(text: str) -> tuple[bool, list[str]]:
    stripped = str(text or "").rstrip()
    issues: list[str] = []
    if not stripped:
        return True, ["reply_empty"]
    if stripped.casefold().endswith(tuple(s.casefold() for s in _TRUNCATED_SUFFIXES)):
        issues.append("reply_truncated_suffix")
    if stripped.count("```") % 2:
        issues.append("reply_unclosed_code_fence")
    for opening, closing, label in (
        ("(", ")", "parentheses"),
        ("[", "]", "brackets"),
        ("{", "}", "braces"),
        ("（", "）", "cjk_parentheses"),
        ("【", "】", "cjk_brackets"),
    ):
        if not _balanced(stripped, opening, closing):
            issues.append(f"reply_unbalanced_{label}")
    return bool(issues), issues


def _schema_checks(text: str, schema: str) -> dict[str, bool]:
    if schema != "work_plan":
        return {"generic_answer": len(text.strip()) >= 40}
    groups = {
        "progress": bool(re.search(r"(完成|修改|新增|进展|实现|推进|changed|implemented|completed)", text, re.I)),
        "evidence": bool(re.search(r"(文件|路径|diff|commit|测试|证据|日志|file|test|evidence)", text, re.I)),
        "risk": bool(re.search(r"(风险|未完成|阻塞|问题|不确定|risk|blocked|unknown)", text, re.I)),
        "next_step": bool(re.search(r"(下一步|建议|后续|计划|next step|recommend)", text, re.I)),
    }
    groups["minimum_sections"] = sum(bool(value) for value in groups.values()) >= 2
    return groups


def validate_reply_candidate(
    text: str,
    *,
    source: str,
    prompt: str = "",
    invocation_marker: str = "",
    schema: str = "generic",
) -> dict[str, Any]:
    """Validate one extracted reply without trusting its extraction source."""

    raw = str(text or "").strip()
    marker_found = bool(invocation_marker and invocation_marker in raw)
    other_markers = re.findall(r"\[JACHIN_REF:[A-Za-z0-9_-]+\]", raw)
    wrong_marker = bool(
        invocation_marker
        and other_markers
        and invocation_marker not in other_markers
    )
    clean = raw.replace(invocation_marker, "", 1).strip() if marker_found else raw
    compact_clean = _compact(clean)
    compact_prompt = _compact(prompt)
    prompt_echo = bool(
        compact_prompt
        and (
            compact_clean == compact_prompt
            or compact_prompt in compact_clean
        )
    )
    observable_compact = _compact(raw)
    permission_required = any(
        _compact(marker) in observable_compact for marker in _PERMISSION_MARKERS
    )
    error_visible = any(
        _compact(marker) in observable_compact for marker in _ERROR_MARKERS
    )
    truncated, truncation_issues = _looks_truncated(clean)
    schema_checks = _schema_checks(clean, schema)
    requested_minimum = 0
    minimum_patterns = (
        r"(?:不少于|至少)\s*(\d{2,6})\s*(?:个)?(?:中文)?(?:字符|字)",
        r"(?:minimum|at least)\s*(\d{2,6})\s*(?:characters|chars|words)",
    )
    for pattern in minimum_patterns:
        for value in re.findall(pattern, str(prompt or ""), flags=re.I):
            try:
                requested_minimum = max(requested_minimum, int(value))
            except (TypeError, ValueError):
                continue

    issues: list[str] = []
    if len(clean) < 40:
        issues.append("reply_too_short")
    if invocation_marker and not marker_found:
        issues.append("invocation_marker_missing")
    if wrong_marker:
        issues.append("invocation_marker_mismatch")
    if prompt_echo:
        issues.append("prompt_echo")
    if permission_required:
        issues.append("permission_request_not_answer")
    if error_visible:
        issues.append("generation_error_not_answer")
    if requested_minimum and len(clean) < requested_minimum:
        issues.append("requested_minimum_length_not_met")
    issues.extend(truncation_issues)
    if not all(schema_checks.values()):
        issues.append(f"{schema}_schema_incomplete")

    return {
        "ok": not issues,
        "source": source,
        "raw_length": len(raw),
        "clean_length": len(clean),
        "marker_found": marker_found,
        "wrong_marker": wrong_marker,
        "prompt_echo": prompt_echo,
        "permission_required": permission_required,
        "error_visible": error_visible,
        "truncated": truncated,
        "schema": schema,
        "schema_checks": schema_checks,
        "requested_minimum_length": requested_minimum,
        "issues": list(dict.fromkeys(issues)),
        "clean_answer": clean if not wrong_marker else "",
    }


def _candidate_similarity(left: str, right: str) -> float:
    a = _compact(left)
    b = _compact(right)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return min(len(a), len(b)) / max(len(a), len(b))
    return SequenceMatcher(None, a, b, autojunk=False).ratio()


def select_reply(
    candidates: Iterable[dict[str, Any]],
    *,
    prompt: str = "",
    invocation_marker: str = "",
    schema: str = "generic",
) -> dict[str, Any]:
    """Choose a complete correlated reply, preferring native copy output."""

    source_priority = {
        "clipboard": 0,
        "accessibility": 1,
        "qwen_vision": 2,
        "vision": 2,
        "ocr": 3,
        "ocr_fallback": 3,
    }
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        source = str(candidate.get("source") or "unknown")
        text = str(candidate.get("text") or "").strip()
        validation = validate_reply_candidate(
            text,
            source=source,
            prompt=prompt,
            invocation_marker=invocation_marker,
            schema=schema,
        )
        rows.append(
            {
                "source": source,
                "text": text,
                "validation": validation,
                "priority": source_priority.get(source, 99),
            }
        )

    final_sources = {"clipboard", "accessibility", "qwen_vision", "vision"}
    for row in rows:
        if (
            row["validation"].get("ok")
            and row["source"] not in final_sources
        ):
            row["validation"]["ok"] = False
            row["validation"]["issues"] = list(
                dict.fromkeys(
                    [
                        *(row["validation"].get("issues") or []),
                        "untrusted_final_reply_source",
                    ]
                )
            )

    valid = sorted(
        (row for row in rows if row["validation"].get("ok")),
        key=lambda row: (row["priority"], -len(row["text"])),
    )
    conflicts: list[dict[str, Any]] = []
    strong = [row for row in valid if row["source"] in {"clipboard", "accessibility", "qwen_vision", "vision"}]
    for index, left in enumerate(strong):
        for right in strong[index + 1 :]:
            similarity = _candidate_similarity(
                left["validation"].get("clean_answer") or "",
                right["validation"].get("clean_answer") or "",
            )
            if similarity < 0.45:
                conflicts.append(
                    {
                        "left": left["source"],
                        "right": right["source"],
                        "similarity": round(similarity, 4),
                    }
                )

    chosen = valid[0] if valid and not conflicts else None
    return {
        "ok": bool(chosen),
        "source": chosen["source"] if chosen else "",
        "answer": (
            str(chosen["validation"].get("clean_answer") or "").strip()
            if chosen
            else ""
        ),
        "validation": (
            chosen["validation"]
            if chosen
            else {
                "ok": False,
                "issues": (
                    ["reply_source_conflict"]
                    if conflicts
                    else ["no_valid_reply_candidate"]
                ),
            }
        ),
        "conflicts": conflicts,
        "candidates": [
            {
                "source": row["source"],
                "text_length": len(row["text"]),
                "validation": row["validation"],
            }
            for row in rows
        ],
    }
