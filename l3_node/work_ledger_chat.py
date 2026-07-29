"""Chat adapter for the Work Ledger MVP.

This module keeps Work Ledger conversational shortcuts out of agent_core.py.
It handles only explicit ledger commands such as starting a task, adding a
note, collecting evidence, generating outputs, ending a task, or continuing a
previous task.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from l3_node.work_ledger import (
    add_ai_work_trace,
    add_manual_note,
    build_end_day_preview,
    collect_snapshot,
    end_session,
    finalize_end_day_package,
    generate_work_outputs,
    generate_multi_day_weekly_report,
    get_active_session,
    import_ai_work_process,
    list_sessions,
    recall_work_ledger,
    read_output_text,
    start_session,
)


_START_PATTERNS = (
    "开始今天工作",
    "开始今日工作",
    "开始今天任务",
    "开始工作",
    "开始任务",
    "开始任务",
    "新建任务",
    "启动任务",
    "开始工作",
    "新建工作",
)
_NOTE_PATTERNS = (
    "记录一下",
    "记一下",
    "补充记录",
    "工作记录",
    "过程记录",
)
_END_PATTERNS = (
    "结束并生成日报",
    "结束今天并生成日报",
    "结束工作生成日报",
    "结束今天工作",
    "下班总结",
    "结束今天任务",
    "结束任务",
    "完成任务",
    "下班总结",
    "结束今天工作",
    "收工总结",
)
_END_DAY_PREVIEW_PATTERNS = (
    "收工预览",
    "结束工作预览",
    "结束今天工作预览",
    "下班预览",
    "生成收工包预览",
    "生成结束工作预览",
    "preview end day",
)
_END_DAY_FINALIZE_PATTERNS = (
    "确认收工",
    "确认结束今天工作",
    "确认生成收工包",
    "确认结束工作",
    "finalize end day",
)
_CONTINUE_PATTERNS = (
    "继续昨天任务",
    "继续上次任务",
    "继续任务",
    "继续",
    "接着昨天",
    "接着上次",
    "接着",
)
_COLLECT_PATTERNS = (
    "采集证据",
    "刷新工作证据",
    "收集工作证据",
    "记录当前现场",
)
_GENERATE_PATTERNS = (
    "生成日报",
    "生成工作记录",
    "生成工作报告",
    "生成Codex任务书",
    "生成 Codex 任务书",
    "生成续写任务书",
    "生成日报",
    "生成工作记录",
    "生成工作报告",
    "生成codex任务书",
    "生成 Codex 任务书",
    "生成续写任务书",
)
_CONTEXT_PACK_PATTERNS = (
    "生成上下文包",
    "生成任务上下文",
    "生成继续任务书",
    "生成下一轮任务书",
    "整理上下文包",
    "整理 Codex 上下文",
    "整理Cursor上下文",
    "整理 Cursor 上下文",
    "明天继续用的上下文",
    "明天给 Codex 的任务书",
)
_LARK_BRIEF_PATTERNS = (
    "查看 Lark 短版",
    "查看Lark短版",
    "复制 Lark 短版",
    "复制Lark短版",
    "打开 Lark 短版",
    "打开Lark短版",
    "生成 Lark 短版",
    "生成Lark短版",
)
_WEEKLY_PATTERNS = (
    "生成这周工作周报",
    "生成本周工作周报",
    "生成最近工作周报",
    "生成周报",
    "这周周报",
    "本周周报",
)
_RECALL_PATTERNS = (
    "上次做到哪",
    "上次做到哪里",
    "之前做到哪",
    "之前做到哪里",
    "最近做了什么",
    "最近有什么可复用经验",
    "最近的可复用经验",
    "查一下工作记忆",
    "召回工作记忆",
)
_TRACE_PREFIXES = {
    "导入Codex记录": "Codex",
    "导入 Codex 记录": "Codex",
    "导入Codex过程": "Codex",
    "导入 Codex 过程": "Codex",
    "导入Cursor记录": "Cursor",
    "导入 Cursor 记录": "Cursor",
    "导入Cursor过程": "Cursor",
    "导入 Cursor 过程": "Cursor",
    "导入AI记录": "AI",
    "导入 AI 记录": "AI",
    "整理这段Codex过程": "Codex",
    "整理这段 Codex 过程": "Codex",
    "整理这段Cursor过程": "Cursor",
    "整理这段 Cursor 过程": "Cursor",
    "导入Codex记录": "Codex",
    "导入 Codex 记录": "Codex",
    "导入Cursor记录": "Cursor",
    "导入 Cursor 记录": "Cursor",
    "导入AI记录": "AI",
    "导入 AI 记录": "AI",
    "导入过程记录": "AI",
}


def parse_work_ledger_command(text: str) -> dict[str, Any] | None:
    """Return a structured Work Ledger command when the user is explicit."""

    raw = str(text or "").strip()
    if not raw:
        return None
    compact = _normalize_spacing(raw)

    if _is_exact_or_short_command(compact, _START_PATTERNS):
        return {
            "kind": "start",
            "title": _default_today_title(compact),
            "project_path": _extract_project_path(raw),
            "raw_text": raw,
        }

    matched = _match_prefix(compact, _START_PATTERNS)
    if matched:
        title = _extract_payload(compact, matched) or "未命名工作任务"
        path = _extract_project_path(raw)
        clean_title = _strip_project_path_line(title)
        return {
            "kind": "start",
            "title": clean_title or "未命名工作任务",
            "project_path": path,
            "raw_text": raw,
        }

    matched = _match_prefix(compact, _NOTE_PATTERNS)
    if matched:
        note = _extract_payload(compact, matched)
        return {"kind": "note", "text": note or raw, "raw_text": raw}

    trace_match = _match_trace_prefix(compact)
    if trace_match:
        prefix, tool_name = trace_match
        trace = _extract_payload(compact, prefix)
        return {"kind": "ai_trace", "text": trace or raw, "tool_name": tool_name, "raw_text": raw}

    process_file = _extract_process_import_file(raw)
    if process_file:
        return {
            "kind": "process_import",
            "file_path": process_file,
            "tool_name": "Terminal",
            "raw_text": raw,
        }

    if _is_exact_or_short_command(compact, _END_DAY_PREVIEW_PATTERNS):
        return {"kind": "end_day_preview", "raw_text": raw}

    if _is_exact_or_short_command(compact, _END_DAY_FINALIZE_PATTERNS):
        return {"kind": "end_day_finalize", "raw_text": raw}

    if _is_exact_or_short_command(compact, _END_PATTERNS):
        return {"kind": "end", "raw_text": raw}

    if _is_exact_or_short_command(compact, _CONTINUE_PATTERNS):
        return {"kind": "continue", "raw_text": raw}

    matched = _match_prefix(compact, _CONTINUE_PATTERNS)
    if matched:
        return {"kind": "continue", "raw_text": raw, "query": _extract_payload(compact, matched)}

    if _is_exact_or_short_command(compact, _COLLECT_PATTERNS):
        return {"kind": "collect", "raw_text": raw}

    if _is_exact_or_short_command(compact, _GENERATE_PATTERNS):
        return {"kind": "generate", "raw_text": raw}

    if _is_exact_or_short_command(compact, _CONTEXT_PACK_PATTERNS):
        return {"kind": "context_pack", "raw_text": raw}

    if _is_exact_or_short_command(compact, _LARK_BRIEF_PATTERNS):
        return {"kind": "lark_brief", "raw_text": raw}

    if _is_exact_or_short_command(compact, _WEEKLY_PATTERNS) or ("周报" in compact and "生成" in compact):
        return {"kind": "weekly", "raw_text": raw, "days": _extract_days(compact) or 7}

    if _looks_like_recall_command(compact):
        return {
            "kind": "recall",
            "raw_text": raw,
            "query": _extract_recall_query(compact),
            "days": _extract_days(compact) or 14,
            "confidence": 0.82,
            "interpreter": "work_ledger_legacy_recall_bridge",
            "reason": "recall confidence=0.82; cues=history,progress,work-memory",
        }

    try:
        from l3_node.work_ledger_goal_interpreter import interpret_work_ledger_goal

        interpreted = interpret_work_ledger_goal(raw)
        if interpreted is not None:
            return interpreted
    except Exception:
        pass

    return None


def handle_work_ledger_chat_command(
    text: str,
    *,
    source: str = "chat",
    created_from: str | None = None,
) -> str | None:
    """Execute a Work Ledger chat command and return a user-visible reply."""

    command = parse_work_ledger_command(text)
    if command is None:
        return None

    kind = str(command.get("kind") or "")
    origin = created_from or f"chat:{source or 'unknown'}"

    if kind == "start":
        detail = start_session(
            title=str(command.get("title") or "未命名工作任务"),
            project_path=str(command.get("project_path") or "").strip() or None,
            user_goal=str(command.get("raw_text") or ""),
            created_from=origin,
        )
        session = detail.get("session", {})
        ev_count = len(detail.get("evidence", []) or [])
        project_line = f"\n项目路径：{session.get('project_path')}" if session.get("project_path") else ""
        return (
            "已开始记录这项工作。\n"
            f"任务：{session.get('title') or '未命名工作任务'}\n"
            f"Session：{session.get('session_id')}{project_line}\n"
            f"已采集初始证据 {ev_count} 条。"
        )

    active = get_active_session()

    if kind == "note":
        if not active:
            return "当前没有活动工作任务。请先说“开始任务：...”再记录过程。"
        detail = add_manual_note(str(active.get("session_id")), str(command.get("text") or ""))
        return (
            "已写入工作记录。\n"
            f"任务：{active.get('title')}\n"
            f"记录：{command.get('text')}"
        )

    if kind == "ai_trace":
        if not active:
            return "当前没有活动工作任务。请先说“开始任务：...”再导入 Codex / Cursor 过程记录。"
        detail = add_ai_work_trace(
            str(active.get("session_id")),
            str(command.get("text") or ""),
            tool_name=str(command.get("tool_name") or "AI"),
            trace_kind="chat_import",
        )
        payload = detail.get("payload") if isinstance(detail, dict) else {}
        return (
            "已导入 AI 工具过程记录。\n"
            f"任务：{active.get('title')}\n"
            f"来源：{payload.get('tool_name') or command.get('tool_name') or 'AI'}\n"
            f"字数：{payload.get('char_count') or len(str(command.get('text') or ''))}"
        )

    if kind == "process_import":
        if not active:
            return "当前没有活动工作任务。请先开始一个任务，再导入终端日志或 AI 工作过程。"
        result = import_ai_work_process(
            str(active.get("session_id")),
            file_path=str(command.get("file_path") or ""),
            tool_name=str(command.get("tool_name") or "Terminal"),
            trace_kind="chat_process_file_import",
        )
        outputs = result.get("outputs") if isinstance(result.get("outputs"), dict) else {}
        import_meta = result.get("import") if isinstance(result.get("import"), dict) else {}
        return (
            "已导入工作过程并刷新输出。\n"
            f"任务：{active.get('title')}\n"
            f"文件：{command.get('file_path')}\n"
            f"选中信号行：{import_meta.get('selected_line_count', 0)} / {import_meta.get('raw_line_count', 0)}\n"
            f"Context Pack：{outputs.get('context_pack') or ''}"
        ).strip()

    if kind == "end_day_preview":
        if not active:
            return "当前没有活动工作任务，先开始一个任务后再生成收工预览。"
        result = build_end_day_preview(str(active.get("session_id")))
        preview = result.get("preview") if isinstance(result.get("preview"), dict) else {}
        candidates = preview.get("candidates") if isinstance(preview.get("candidates"), list) else []
        safety = preview.get("safety") if isinstance(preview.get("safety"), dict) else {}
        lines = [
            "已生成收工预览，暂不关闭任务。",
            f"任务：{active.get('title')}",
            f"候选证据组：{len(candidates)}",
            f"安全检查：{'通过' if not safety.get('blocked') else '需要处理敏感内容'}",
            "确认后会刷新日报、复盘、上下文包、Lark 简报和方法论候选。",
            "你可以回复：确认收工。",
        ]
        for item in candidates[:5]:
            lines.append(f"- {item.get('kind')}: {item.get('summary')}")
        return "\n".join(lines)

    if kind == "end_day_finalize":
        if not active:
            return "当前没有活动工作任务，无法确认收工。"
        result = finalize_end_day_package(str(active.get("session_id")), close_session=True)
        closed = result.get("closed") if isinstance(result.get("closed"), dict) else {}
        outputs = result.get("outputs") if isinstance(result.get("outputs"), dict) else {}
        if not outputs and isinstance(closed.get("outputs"), dict):
            outputs = closed.get("outputs")
        return _render_outputs_reply(
            f"已确认收工并生成工作包：{active.get('title')}",
            outputs,
            session_id=str(active.get("session_id") or ""),
        )

    if kind == "collect":
        if not active:
            return "当前没有活动工作任务。请先开始任务，我再采集当前工作现场。"
        detail = collect_snapshot(str(active.get("session_id")), trigger="chat")
        return (
            "已采集当前工作现场。\n"
            f"任务：{detail.get('session', {}).get('title')}\n"
            f"新增证据：{len(detail.get('evidence', []) or [])} 条。"
        )

    if kind == "generate":
        target = active or _latest_session()
        if not target:
            return "还没有可生成的工作任务记录。"
        outputs = generate_work_outputs(str(target.get("session_id")))
        return _render_outputs_reply("已生成工作输出。", outputs)

    if kind == "context_pack":
        target = (
            active
            or _session_from_project_memory(command)
            or _latest_session(status="closed")
            or _latest_session()
        )
        if not target:
            return "还没有可整理的工作任务。先开始一个任务，或导入一段 Codex / Cursor 过程记录。"
        outputs = generate_work_outputs(str(target.get("session_id")))
        context_path = str(outputs.get("context_pack") or "")
        preview = _read_preview(context_path, max_chars=1200)
        return (
            f"已生成任务上下文包：{target.get('title')}\n"
            f"Session：{target.get('session_id')}\n"
            f"Context Pack：{context_path}\n\n"
            f"{preview}"
        ).strip()

    if kind == "weekly":
        days = int(command.get("days") or 7)
        result = generate_multi_day_weekly_report(days)
        preview = str(result.get("text") or "").strip()[:1200]
        return (
            f"已生成最近 {days} 天工作周报。\n"
            f"文件：{result.get('path')}\n"
            f"聚合任务：{result.get('session_count')}\n"
            f"采纳输出：{result.get('adopted_output_count')}\n\n"
            f"{preview}"
        ).strip()

    if kind == "recall":
        query = str(command.get("query") or command.get("raw_text") or "").strip()
        days = int(command.get("days") or 14)
        result = recall_work_ledger(query, days=days, limit=6)
        return _render_recall_reply(result)

    if kind == "lark_brief":
        target = active or _latest_session(status="closed") or _latest_session()
        if not target:
            return "还没有可用的工作记录，先开始一个任务，我再帮你生成 Lark 短版。"
        outputs = generate_work_outputs(str(target.get("session_id")))
        if not outputs.get("lark_brief"):
            return "已生成工作输出，但暂时没有可用的 Lark 短版。"
        try:
            preview = read_output_text(str(target.get("session_id")), "lark_brief", max_chars=1000)
            text = str(preview.get("text") or "").strip()
        except Exception:
            text = _read_preview(str(outputs.get("lark_brief") or ""), max_chars=1000)
        return (
            "这是可直接发到 Lark 的短版：\n\n"
            f"{text}\n\n"
            f"文件：{outputs.get('lark_brief')}"
        ).strip()

    if kind == "end":
        if not active:
            return "当前没有活动工作任务；没有需要结束的任务。"
        closed = end_session(str(active.get("session_id")), generate_outputs=True)
        outputs = closed.get("outputs", {}) if isinstance(closed, dict) else {}
        return _render_outputs_reply(
            f"已结束任务：{closed.get('session', {}).get('title')}",
            outputs,
            session_id=str(closed.get("session", {}).get("session_id") or ""),
        )

    if kind == "continue":
        query = str(command.get("query") or "").strip() or _extract_continue_query(str(command.get("raw_text") or ""))
        target = (
            active
            or _session_from_project_memory(command)
            or _latest_session_for_query(query)
            or _latest_session(status="closed")
            or _latest_session()
        )
        if not target:
            return "还没有历史工作任务可继续。先开始一个任务，我会从今天开始记录。"
        outputs = generate_work_outputs(str(target.get("session_id")))
        prompt_path = str(outputs.get("codex_continuation_prompt") or "")
        prompt_preview = _read_preview(prompt_path, max_chars=900)
        return (
            f"已整理可继续的任务：{target.get('title')}\n"
            f"Session：{target.get('session_id')}\n"
            f"Codex 续写任务书：{prompt_path}\n\n"
            f"{prompt_preview}"
        ).strip()

    return None


def _normalize_spacing(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("：", ":")).strip()


def _default_today_title(seed: str = "") -> str:
    if "今天" in seed or "今日" in seed:
        return time.strftime("%Y-%m-%d 工作记录")
    return "未命名工作任务"


def _match_prefix(text: str, prefixes: tuple[str, ...]) -> str:
    lower = text.lower()
    for prefix in prefixes:
        p = prefix.lower()
        if lower == p or lower.startswith(p + ":") or lower.startswith(p + " ") or lower.startswith(p):
            return prefix
    return ""


def _match_trace_prefix(text: str) -> tuple[str, str] | None:
    lower = text.lower()
    for prefix, tool_name in _TRACE_PREFIXES.items():
        p = prefix.lower()
        if lower == p or lower.startswith(p + ":") or lower.startswith(p + " "):
            return prefix, tool_name
    return None


def _extract_payload(text: str, prefix: str) -> str:
    payload = text[len(prefix) :].strip()
    if payload.startswith(":"):
        payload = payload[1:].strip()
    return payload.strip()


def _is_exact_or_short_command(text: str, commands: tuple[str, ...]) -> bool:
    lower = text.lower().strip("。.!！ ")
    for command in commands:
        c = command.lower()
        if lower == c:
            return True
    return False


def _extract_days(text: str) -> int | None:
    t = str(text or "")
    match = re.search(r"最近\s*(\d{1,2})\s*天", t)
    if match:
        return max(1, min(60, int(match.group(1))))
    if "30天" in t or "三十天" in t or "一个月" in t:
        return 30
    if "14天" in t or "十四天" in t or "两周" in t or "2周" in t:
        return 14
    if "7天" in t or "七天" in t or "一周" in t or "这周" in t or "本周" in t:
        return 7
    return None


def _looks_like_recall_command(text: str) -> bool:
    t = str(text or "").strip()
    if _is_exact_or_short_command(t, _RECALL_PATTERNS):
        return True
    return bool(
        ("上次" in t or "之前" in t or "最近" in t)
        and any(keyword in t for keyword in ("做到", "做了", "进展", "经验", "方法论", "工作记忆", "任务"))
    )


def _extract_recall_query(text: str) -> str:
    value = str(text or "").strip()
    value = re.sub(r"^(查一下|召回|看看|帮我看看)", "", value).strip()
    value = re.sub(r"(工作记忆|最近|上次|之前|做到哪(里)?|做了什么|有什么)", " ", value).strip()
    value = re.sub(r"\s+", " ", value).strip(" ：:，,。")
    return value or str(text or "").strip()


def _extract_continue_query(text: str) -> str:
    value = str(text or "").strip()
    value = re.sub(r"^(继续|接着)(昨天|上次|之前)?(任务)?", "", value).strip()
    value = value.strip(" ：:，,。")
    return value


def _extract_project_path(raw: str) -> str:
    match = re.search(r"(?:项目路径|路径)\s*[:：]\s*([A-Za-z]:\\[^\r\n]+)", raw)
    if not match:
        return ""
    candidate = match.group(1).strip().strip('"')
    return candidate if Path(candidate).exists() else candidate


def _extract_process_import_file(raw: str) -> str:
    text = str(raw or "").strip()
    lowered = text.lower()
    if not any(keyword in lowered for keyword in ("导入", "import")):
        return ""
    if not any(keyword in lowered for keyword in ("日志", "log", "terminal", "终端", "过程", "trace")):
        return ""
    match = re.search(r"([A-Za-z]:\\[^\r\n\"']+)", text)
    if not match:
        return ""
    candidate = match.group(1).strip().strip('"').strip("'")
    return candidate


def _strip_project_path_line(title: str) -> str:
    value = str(title or "")
    value = re.sub(r"(?:项目路径|路径)\s*[:：]\s*[A-Za-z]:\\[^\r\n]+", "", value).strip()
    return value.strip(" -，,。")


def _latest_session(*, status: str | None = None) -> dict[str, Any] | None:
    for item in list_sessions(limit=20):
        if status is None or str(item.get("status") or "") == status:
            return item
    return None


def _latest_session_for_query(query: str) -> dict[str, Any] | None:
    clean = str(query or "").strip()
    if not clean:
        return None
    try:
        result = recall_work_ledger(clean, days=30, limit=5)
    except Exception:
        return None
    for hit in result.get("hits", []) or []:
        sid = str(hit.get("session_id") or "").strip()
        if not sid:
            continue
        for item in list_sessions(limit=100):
            if str(item.get("session_id") or "") == sid:
                return item
    return None


def _session_from_project_memory(command: dict[str, Any]) -> dict[str, Any] | None:
    memory = command.get("project_memory") if isinstance(command.get("project_memory"), dict) else None
    if memory is None:
        try:
            from l3_node.work_ledger_project_memory import resolve_project_reference

            memory = resolve_project_reference(str(command.get("raw_text") or command.get("query") or ""))
        except Exception:
            memory = None
    if not isinstance(memory, dict):
        return None
    sid = str(memory.get("session_id") or "").strip()
    project_path = str(memory.get("project_path") or "").strip()
    for item in list_sessions(limit=100):
        if sid and str(item.get("session_id") or "") == sid:
            return item
        if project_path and str(item.get("project_path") or "") == project_path:
            return item
    return None


def _render_recall_reply(result: dict[str, Any]) -> str:
    hits = result.get("hits") if isinstance(result.get("hits"), list) else []
    days = result.get("window_days") or "-"
    query = result.get("query") or ""
    if not hits:
        return f"最近 {days} 天没有召回到和“{query}”明显相关的工作记忆。可以换项目名、文件名或任务关键词再试。"
    lines = [
        f"最近 {days} 天召回到 {len(hits)} 条相关工作记忆：",
    ]
    for idx, hit in enumerate(hits[:6], start=1):
        kind = hit.get("kind") or "memory"
        trust = hit.get("trust_level") or "system_observed"
        title = hit.get("title") or hit.get("project_name") or hit.get("session_id")
        text = str(hit.get("text") or hit.get("path") or "").replace("\n", " ")[:220]
        lines.append(f"{idx}. [{kind}/{trust}] {title}: {text}")
    summary = result.get("index_summary") if isinstance(result.get("index_summary"), dict) else {}
    lines.append(
        f"索引依据：sessions={summary.get('session_count', 0)}，"
        f"采纳输出={summary.get('adopted_output_count', 0)}，"
        f"方法论候选={summary.get('methodology_candidate_count', 0)}。"
    )
    return "\n".join(lines)


def _render_outputs_reply(title: str, outputs: dict[str, Any], *, session_id: str = "") -> str:
    work_review = str(outputs.get("work_review") or "")
    report = str(outputs.get("daily_report") or "")
    prompt = str(outputs.get("codex_continuation_prompt") or "")
    enhanced_report = str(outputs.get("enhanced_daily_report") or "")
    enhanced_prompt = str(outputs.get("enhanced_continuation_prompt") or "")
    lark_brief = str(outputs.get("lark_brief") or "")
    team_lark = str(outputs.get("team_lark_brief") or "")
    weekly = str(outputs.get("weekly_report") or "")
    performance = str(outputs.get("performance_entries") or "")
    methodology = str(outputs.get("methodology_candidates") or "")
    quality = str(outputs.get("llm_quality_report") or "")
    context_pack = str(outputs.get("context_pack") or "")
    lines = [title]
    if work_review:
        lines.append(f"工作复盘七问：{work_review}")
    if context_pack:
        lines.append(f"Context Pack：{context_pack}")
    if session_id:
        lines.append(f"Session：{session_id}")
    if report:
        lines.append(f"日报：{report}")
    if prompt:
        lines.append(f"Codex 续写任务书：{prompt}")
    if enhanced_report:
        lines.append(f"增强日报：{enhanced_report}")
    if enhanced_prompt:
        lines.append(f"增强续写任务书：{enhanced_prompt}")
    if lark_brief:
        lines.append(f"Lark 短版：{lark_brief}")
    if team_lark:
        lines.append(f"团队简报：{team_lark}")
    if weekly:
        lines.append(f"周报草稿：{weekly}")
    if performance:
        lines.append(f"绩效条目：{performance}")
    if methodology:
        lines.append(f"方法论候选：{methodology}")
    if quality:
        lines.append(f"质量门控：{quality}")
    return "\n".join(lines)


def _read_preview(path: str, *, max_chars: int = 900) -> str:
    if not path:
        return ""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except Exception:
        return ""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n..."
