"""Tool quality gates for Cognitive Kernel verification.

This layer is intentionally separate from role executors.  Executors report
what happened; this module judges whether the observation is good enough to be
trusted by the next WorkOrder.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .contracts import WorkOrder


@dataclass(slots=True)
class ToolQualityReport:
    tool: str
    score: float
    quality_level: str
    blocks_execution: bool = False
    issues: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_tool_observation(
    *,
    work_order: WorkOrder,
    observation: str,
    extra_evidence: list[dict[str, Any]] | None = None,
) -> ToolQualityReport:
    tool = str((work_order.inputs or {}).get("tool") or "").strip()
    text = str(observation or "")
    obj = _json_obj(text)
    issues: list[str] = []
    evidence: dict[str, Any] = {
        "observation_length": len(text),
        "json_observation": isinstance(obj, dict),
    }

    if not text.strip():
        issues.append("empty_observation")
    if isinstance(obj, dict) and (obj.get("ok") is False or obj.get("success") is False):
        issues.append("tool_reported_failure")
        evidence["tool_error"] = str(obj.get("error") or obj.get("reason") or "")[:300]

    low_tool = tool.lower()
    if low_tool == "mcp:tavily_search":
        _check_search_result_quality(obj, issues, evidence)
    elif low_tool == "mcp:fetch":
        _check_fetch_result_quality(obj, issues, evidence)
    elif low_tool == "core:web_research_summarize":
        _check_web_summary_quality(text, obj, issues, evidence)
    elif "lark" in low_tool and "send" in low_tool:
        _check_message_send_quality(obj, extra_evidence or [], issues, evidence)

    score = _score_from_issues(issues)
    return ToolQualityReport(
        tool=tool,
        score=score,
        quality_level=_quality_level(score),
        blocks_execution=_blocks_execution(tool, issues),
        issues=issues,
        evidence=evidence,
    )


def _json_obj(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _check_search_result_quality(obj: dict[str, Any] | None, issues: list[str], evidence: dict[str, Any]) -> None:
    results = obj.get("results") if isinstance(obj, dict) else None
    if not isinstance(results, list) or not results:
        issues.append("search_results_missing")
        evidence["result_count"] = 0
        return
    urls = [str(item.get("url") or "").strip() for item in results if isinstance(item, dict)]
    titles = [str(item.get("title") or "").strip() for item in results if isinstance(item, dict)]
    evidence["result_count"] = len(results)
    evidence["url_count"] = len([u for u in urls if u.startswith(("http://", "https://"))])
    evidence["title_count"] = len([title for title in titles if title])
    if evidence["url_count"] == 0:
        issues.append("search_result_urls_missing")
    if evidence["title_count"] == 0:
        issues.append("search_result_titles_missing")


def _check_fetch_result_quality(obj: dict[str, Any] | None, issues: list[str], evidence: dict[str, Any]) -> None:
    pages = obj.get("pages") if isinstance(obj, dict) else None
    if not isinstance(pages, list) or not pages:
        issues.append("fetch_pages_missing")
        evidence["page_count"] = 0
        return
    readable = 0
    access_or_bot_wall = 0
    for page in pages:
        text = str(page.get("text") or page.get("content") or "") if isinstance(page, dict) else ""
        if len(text) >= 120:
            readable += 1
        low = text.lower()
        if any(
            marker in low
            for marker in (
                "access denied",
                "403 forbidden",
                "forbidden",
                "please enable javascript",
                "captcha",
                "robot check",
                "verify you are human",
                "login required",
                "sign in to continue",
                "请求被拒绝",
                "访问被拒绝",
                "需要登录",
                "请登录",
                "验证码",
            )
        ):
            access_or_bot_wall += 1
    evidence["page_count"] = len(pages)
    evidence["readable_page_count"] = readable
    evidence["access_or_bot_wall_count"] = access_or_bot_wall
    if readable == 0:
        issues.append("fetch_readable_content_missing")
    if access_or_bot_wall and access_or_bot_wall >= readable:
        issues.append("fetch_access_or_bot_wall")


def _check_web_summary_quality(
    raw_text: str,
    obj: dict[str, Any] | None,
    issues: list[str],
    evidence: dict[str, Any],
) -> None:
    message = _summary_text(raw_text, obj)
    evidence["summary_length"] = len(message)
    evidence["source_url_count"] = len(re.findall(r"https?://\S+", message))
    if len(message.strip()) < 60:
        issues.append("summary_too_short")
    if evidence["source_url_count"] == 0:
        issues.append("summary_missing_source_urls")
    noisy_markers = (
        "%3c",
        "%20",
        "<defs",
        "</style",
        "fill:",
        "st0",
        "function(",
        "undefined",
    )
    low = message.lower()
    if any(marker in low for marker in noisy_markers):
        issues.append("summary_contains_web_noise")
    markdown_artifact_markers = (
        "]([http",
        "[###",
        "[####",
        "]( [http",
        "```",
        "|---",
    )
    if any(marker in low for marker in markdown_artifact_markers):
        issues.append("summary_contains_markdown_artifact")
    placeholder_markers = (
        "正在生成",
        "稍后会自动刷新",
        "暂未生成",
        "请点击",
        "placeholder",
        "not ready",
    )
    if any(marker in low or marker in message for marker in placeholder_markers):
        issues.append("summary_placeholder_text")
    if "..." in message or "…" in message:
        issues.append("summary_has_ellipsis_truncation")
    bullet_lines = [line.strip() for line in message.splitlines() if re.match(r"^\d+[.、]\s*", line.strip())]
    evidence["bullet_count"] = len(bullet_lines)
    incomplete = [line for line in bullet_lines if line and line[-1] not in "。.!！?？）)"]
    if incomplete:
        issues.append("summary_incomplete_sentence")
        evidence["incomplete_bullet_preview"] = incomplete[0][:180]


def _summary_text(raw_text: str, obj: dict[str, Any] | None) -> str:
    if isinstance(obj, dict):
        for key in ("message", "summary", "content", "text"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return raw_text


def _check_message_send_quality(
    obj: dict[str, Any] | None,
    extra_evidence: list[dict[str, Any]],
    issues: list[str],
    evidence: dict[str, Any],
) -> None:
    role_evidence = next((item for item in extra_evidence if item.get("type") == "role_execution"), {})
    adapter = role_evidence.get("adapter_evidence") if isinstance(role_evidence.get("adapter_evidence"), dict) else {}
    evidence["post_send_verified"] = adapter.get("post_send_verified")
    evidence["adapter_ok"] = role_evidence.get("adapter_ok")
    if adapter.get("duplicate_skipped") is True:
        issues.append("message_duplicate_skipped")
    if role_evidence and role_evidence.get("adapter_ok") is not True:
        issues.append("message_adapter_failed")
    if role_evidence and adapter.get("post_send_verified") is not True:
        issues.append("message_post_send_unverified")
    if isinstance(obj, dict) and not any(str(obj.get(key) or "").strip() for key in ("message_id", "screenshot", "detail")):
        evidence["message_result_minimal"] = True


def _score_from_issues(issues: list[str]) -> float:
    score = 1.0
    severe = {
        "empty_observation",
        "tool_reported_failure",
        "search_results_missing",
        "search_result_urls_missing",
        "fetch_pages_missing",
        "fetch_readable_content_missing",
        "fetch_access_or_bot_wall",
        "summary_missing_source_urls",
        "summary_placeholder_text",
        "message_adapter_failed",
        "message_post_send_unverified",
    }
    medium = {
        "summary_too_short",
        "summary_contains_web_noise",
        "summary_contains_markdown_artifact",
        "summary_has_ellipsis_truncation",
        "summary_incomplete_sentence",
        "search_result_titles_missing",
        "message_duplicate_skipped",
    }
    for issue in issues:
        if issue in severe:
            score -= 0.45
        elif issue in medium:
            score -= 0.25
        else:
            score -= 0.12
    return max(0.0, round(score, 3))


def _quality_level(score: float) -> str:
    if score >= 0.82:
        return "production"
    if score >= 0.62:
        return "usable_with_caution"
    if score >= 0.38:
        return "weak"
    return "blocked"


def _blocks_execution(tool: str, issues: list[str]) -> bool:
    if "empty_observation" in issues or "tool_reported_failure" in issues:
        return True
    tool = tool.lower()
    blocking_by_tool = {
        "mcp:tavily_search": {"search_results_missing", "search_result_urls_missing"},
        "mcp:fetch": {"fetch_pages_missing", "fetch_readable_content_missing", "fetch_access_or_bot_wall"},
        "core:web_research_summarize": {
            "summary_missing_source_urls",
            "summary_placeholder_text",
            "summary_contains_web_noise",
            "summary_contains_markdown_artifact",
            "summary_has_ellipsis_truncation",
            "summary_incomplete_sentence",
        },
    }
    if tool in blocking_by_tool and any(issue in blocking_by_tool[tool] for issue in issues):
        return True
    if "lark" in tool and "send" in tool:
        return any(issue in {"message_adapter_failed", "message_post_send_unverified", "message_duplicate_skipped"} for issue in issues)
    return False
