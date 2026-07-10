"""
Jachin 深度执行日志 — 面向排障与全息监控的「全量轨迹」。

- 默认开启（环境变量 JACHIN_L3_DEEP_LOG=0/false/no/off 可关闭）。
- 使用 logger「jachin.deep」：由 l3_node/__main__.py 挂载控制台、l3_debug.log、全息 SSE。
- 内容可能极长；API Key / Bearer 等会做简单脱敏，但仍勿在不可信环境分享整段日志。

- **热路径默认不再打印完整 messages/tools/schema**（避免同步写日志阻塞主线程 / 桌面 IPC 卡顿）。
  设置 JACHIN_L3_DEEP_LOG_FULL_PAYLOAD=1（或旧名 JACHIN_L3_DEEP_LOG_FULL_REQUEST=1）恢复旧版「全量请求体」行为。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any, Mapping, Sequence

from core.utils.log_utils import truncate_large_strings_for_log

_LOG = logging.getLogger("jachin.deep")


def deep_log_enabled() -> bool:
    v = (os.environ.get("JACHIN_L3_DEEP_LOG") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def deep_log_full_payload_enabled() -> bool:
    """
    为真时：log_llm_completion 打印完整 messages（截断上限仍生效）、RoleExecutionAgent 原始输出大上限等，与旧版一致。
    为假（默认）：仅打印条数、各 role 长度、tools 数量与摘要 hash，避免巨型日志阻塞。
    兼容旧环境变量名 JACHIN_L3_DEEP_LOG_FULL_REQUEST。
    """
    for key in ("JACHIN_L3_DEEP_LOG_FULL_PAYLOAD", "JACHIN_L3_DEEP_LOG_FULL_REQUEST"):
        v = (os.environ.get(key) or "").strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
    return False


def _content_len(msg: Mapping[str, Any]) -> int:
    c = msg.get("content")
    if c is None:
        return 0
    if isinstance(c, str):
        return len(c)
    try:
        return len(json.dumps(c, ensure_ascii=False, default=str))
    except Exception:
        return len(str(c))


def summarize_messages_and_tools_for_deep(
    messages: Sequence[Mapping[str, Any]] | None,
    tools: Sequence[Mapping[str, Any]] | None,
) -> str:
    """单行可检索摘要：条数、按 role 的长度列表、tools 数与名称指纹（非全量 schema）。"""
    lines: list[str] = []
    msgs = list(messages or [])
    lines.append(f"n_messages={len(msgs)}")
    role_chunks: dict[str, list[int]] = {}
    for i, m in enumerate(msgs):
        if not isinstance(m, Mapping):
            lines.append(f"  [{i}] non_mapping={type(m).__name__}")
            continue
        role = str(m.get("role") or "?")
        L = _content_len(m)
        role_chunks.setdefault(role, []).append(L)
    for role, lens in sorted(role_chunks.items()):
        s = sum(lens)
        mx = max(lens) if lens else 0
        lines.append(f"  role={role} count={len(lens)} total_chars={s} max_msg_chars={mx}")
    if tools is not None:
        names: list[str] = []
        for t in tools:
            if not isinstance(t, Mapping):
                continue
            fn = t.get("function") if isinstance(t.get("function"), Mapping) else {}
            nm = str((fn or {}).get("name") or t.get("name") or "?")
            names.append(nm)
        blob = "\n".join(names)
        h = hashlib.sha256(blob.encode("utf-8", errors="replace")).hexdigest()[:16]
        head = [(n[:40] + "…") if len(n) > 40 else n for n in names[:20]]
        lines.append(f"n_tools={len(tools)} name_list_sha256_16={h} tool_names_head={head!r}")
    else:
        lines.append("n_tools=(omitted)")
    return "\n".join(lines)


_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-[a-zA-Z0-9]{16,}"), "sk-***REDACTED***"),
    (re.compile(r"Bearer\s+[a-zA-Z0-9._\-]{12,}", re.I), "Bearer ***REDACTED***"),
    (re.compile(r"(?i)(api[_-]?key|apikey|client_secret|refresh_token)\s*[:=]\s*\S{8,}"), "***CREDENTIAL_LINE_REDACTED***"),
    (re.compile(r"DASHSCOPE_API_KEY\s*=\s*\S+"), "DASHSCOPE_API_KEY=***"),
    (re.compile(r"OPENAI_API_KEY\s*=\s*\S+"), "OPENAI_API_KEY=***"),
)


def redact_secrets(text: str) -> str:
    if not text:
        return text
    out = text
    for pat, repl in _REDACT_PATTERNS:
        try:
            out = pat.sub(repl, out)
        except Exception:
            pass
    return out


def _truncate(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 20] + f"\n…(truncated, total_chars={len(s)})"


def format_messages_for_deep(
    messages: Sequence[Mapping[str, Any]] | None,
    *,
    max_per_content: int = 48_000,
    max_total: int = 400_000,
) -> str:
    """将 chat messages 格式化为可读块（role + content 摘要）。"""
    if not messages:
        return "(no messages)"
    parts: list[str] = []
    total = 0
    for i, m in enumerate(messages):
        if not isinstance(m, Mapping):
            parts.append(f"[{i}] (non-dict: {type(m).__name__})")
            continue
        role = str(m.get("role") or "?")
        content = m.get("content")
        if content is None:
            body = "(no content)"
        elif isinstance(content, str):
            body = redact_secrets(content)
        else:
            try:
                body = redact_secrets(json.dumps(content, ensure_ascii=False, default=str))
            except Exception:
                body = redact_secrets(str(content))
        piece = _truncate(body, max_per_content)
        block = f"--- msg[{i}] role={role} len={len(body) if isinstance(body, str) else 'n/a'} ---\n{piece}"
        total += len(block)
        if total > max_total:
            parts.append(f"(... further messages omitted, max_total={max_total})")
            break
        parts.append(block)
    return "\n".join(parts)


def format_tools_brief(tools: Sequence[Mapping[str, Any]] | None, *, max_names: int = 120) -> str:
    if not tools:
        return "(no tools[])"
    names: list[str] = []
    for t in tools[:max_names]:
        if not isinstance(t, Mapping):
            continue
        fn = (t.get("function") or {}) if isinstance(t.get("function"), Mapping) else {}
        nm = str(fn.get("name") or t.get("name") or "?")
        names.append(nm)
    extra = f" …(+{len(tools) - max_names} more)" if len(tools) > max_names else ""
    return f"n={len(tools)} names={names}{extra}"


def _compact_body_for_emit_block(body: str) -> str:
    """超长正文：优先对 JSON 做字段级截断，避免单条日志仍含数 MB 字符串。"""
    b = redact_secrets(body)
    if len(b) <= 200_000:
        return b
    try:
        if b.strip()[:1] in "{[":
            parsed = json.loads(b)
            dumped = json.dumps(
                truncate_large_strings_for_log(parsed, max_len=500),
                ensure_ascii=False,
                default=str,
            )
            return _truncate(dumped, 120_000)
    except Exception:
        pass
    return _truncate(b, 120_000)


def emit_block(title: str, body: str) -> None:
    """写入一条大块深度日志（脱敏 + 控制台/文件/SSE 由 logging 配置决定）。"""
    if not deep_log_enabled():
        return
    text = f"========== {title} ==========\n{_compact_body_for_emit_block(body)}\n========== end {title} =========="
    try:
        _LOG.info("%s", text)
    except Exception:
        pass


def log_llm_completion(
    *,
    source: str,
    purpose: str,
    phase: str,
    model: str,
    stream: bool,
    elapsed_ms: float,
    messages: Sequence[Mapping[str, Any]] | None,
    tools: Sequence[Mapping[str, Any]] | None = None,
    response_text: str | None = None,
    response_dict_summary: str | None = None,
    error: str | None = None,
) -> None:
    if not deep_log_enabled():
        return
    parts = [
        f"source={source}",
        f"purpose={purpose}",
        f"phase={phase}",
        f"model={model}",
        f"stream={stream}",
        f"elapsed_ms={elapsed_ms:.1f}",
        f"deep_full_payload={deep_log_full_payload_enabled()}",
    ]
    if error:
        parts.append(f"ERROR={error}")
    body = "\n".join(parts)
    if deep_log_full_payload_enabled():
        body += "\n\n[REQUEST messages]\n" + format_messages_for_deep(messages)
        if tools is not None:
            body += "\n\n[REQUEST tools]\n" + format_tools_brief(tools)
    else:
        body += "\n\n[REQUEST summary — 全量正文已省略，设 JACHIN_L3_DEEP_LOG_FULL_PAYLOAD=1 可恢复]\n"
        body += summarize_messages_and_tools_for_deep(messages, tools)
    if response_dict_summary:
        body += "\n\n[RESPONSE summary]\n" + redact_secrets(response_dict_summary)
    if response_text is not None:
        cap = 120_000 if deep_log_full_payload_enabled() else 12_000
        body += "\n\n[RESPONSE text]\n" + _truncate(redact_secrets(response_text), cap)
    emit_block(f"LLM {purpose} ({'stream' if stream else 'complete'})", body)


def _format_tool_payload_for_deep_log(raw: str, *, outer_cap: int) -> str:
    """仅用于深度日志展示：脱敏 + 嵌套 str 截断 + 外层总长上限。不改变调用方真实入参/出参。"""
    s = redact_secrets(raw or "")
    if not s:
        return ""
    try:
        if s.strip()[:1] in "{[":
            parsed = json.loads(s)
            dumped = json.dumps(
                truncate_large_strings_for_log(parsed, max_len=500),
                ensure_ascii=False,
                default=str,
            )
            return _truncate(dumped, outer_cap)
    except Exception:
        pass
    return _truncate(s, outer_cap)


def log_tool_execution(
    *,
    trace: str,
    run_id: str,
    tool: str,
    work_order_input: str,
    output: str,
    elapsed_ms: float,
    mcp: bool,
) -> None:
    if not deep_log_enabled():
        return
    full = deep_log_full_payload_enabled()
    ai_cap = 80_000 if full else 8_000
    out_cap = 120_000 if full else 16_000
    body = "\n".join(
        [
            f"trace={trace}",
            f"run_id={run_id}",
            f"tool={tool}",
            f"mcp={mcp}",
            f"elapsed_ms={elapsed_ms:.1f}",
            f"work_order_input_len={len(work_order_input or '')}",
            "[ACTION_INPUT]\n" + _format_tool_payload_for_deep_log(work_order_input, outer_cap=ai_cap),
            f"output_len={len(output or '')}",
            "[OUTPUT]\n" + _format_tool_payload_for_deep_log(output, outer_cap=out_cap),
        ]
    )
    emit_block(f"TOOL {tool}", body)


def log_role_execution_iteration_context(
    *,
    trace: str,
    iteration: int,
    max_iter: int,
    run_id: str,
    n_history_messages: int,
    n_skills: int,
    stream: bool,
    llm_purpose: str,
) -> None:
    if not deep_log_enabled():
        return
    body = "\n".join(
        [
            f"trace={trace}",
            f"iteration={iteration}/{max_iter}",
            f"run_id={run_id}",
            f"history_user_assistant_msgs={n_history_messages}",
            f"skills_visible={n_skills}",
            f"stream={stream}",
            f"llm_purpose={llm_purpose}",
        ]
    )
    emit_block("RoleExecutionAgent iteration (pre-LLM)", body)


def log_role_execution_llm_result(
    *,
    trace: str,
    iteration: int,
    response_len: int,
    response_full: str,
) -> None:
    if not deep_log_enabled():
        return
    cap = 200_000 if deep_log_full_payload_enabled() else 12_000
    body = "\n".join(
        [
            f"trace={trace}",
            f"iteration={iteration}",
            f"response_len={response_len}",
            f"raw_logged_max_chars={cap}",
            "[RAW_MODEL_OUTPUT]\n" + _truncate(redact_secrets(response_full or ""), cap),
        ]
    )
    emit_block("RoleExecutionAgent LLM raw output", body)


def log_role_execution_parse_result(
    *,
    trace: str,
    iteration: int,
    parsed_summary: str,
) -> None:
    if not deep_log_enabled():
        return
    emit_block(
        "RoleExecutionAgent parse",
        f"trace={trace}\niteration={iteration}\n{redact_secrets(parsed_summary)}",
    )


def log_run_agent_start(
    *,
    run_id: str,
    user_input: str,
    history_msgs: int,
    max_iterations: int,
    n_tools: int,
    channel: str,
) -> None:
    if not deep_log_enabled():
        return
    body = "\n".join(
        [
            f"run_id={run_id}",
            f"channel={channel}",
            f"max_iterations={max_iterations}",
            f"history_msgs={history_msgs}",
            f"n_tools={n_tools}",
            "[USER_INPUT]\n" + _truncate(redact_secrets(user_input or ""), 32_000),
        ]
    )
    emit_block("run_agent START", body)


def log_pipeline_phase(phase: str, detail: str) -> None:
    if not deep_log_enabled():
        return
    d = redact_secrets(detail or "")
    if d.strip()[:1] in "{[":
        try:
            d = json.dumps(
                truncate_large_strings_for_log(json.loads(d), max_len=500),
                ensure_ascii=False,
                default=str,
            )
        except Exception:
            d = _truncate(d, 16_000)
    else:
        d = _truncate(d, 16_000)
    emit_block(f"Pipeline {phase}", d)


def summarize_parsed_action(parsed: Any) -> str:
    if not parsed:
        return "parsed=None"
    if not isinstance(parsed, Mapping):
        return f"parsed_non_mapping={type(parsed).__name__!r}"
    try:
        pt = parsed.get("type")
        if pt == "native":
            return f"type=native tool={parsed.get('tool')!r} input_len={len(str(parsed.get('input') or ''))}"
        if pt == "answer":
            c = str(parsed.get("content") or "")
            return f"type=answer content_len={len(c)} preview={c[:500]!r}"
        return f"type={pt!r} keys={list(parsed.keys())}"
    except Exception as e:
        return f"(summary error: {e})"
