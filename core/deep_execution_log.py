"""
Jachin 深度执行日志 — 面向排障与全息监控的「全量轨迹」。

- 默认开启（环境变量 JACHIN_L3_DEEP_LOG=0/false/no/off 可关闭）。
- 使用 logger「jachin.deep」：由 l3_node/__main__.py 挂载控制台、l3_debug.log、全息 SSE。
- 内容可能极长；API Key / Bearer 等会做简单脱敏，但仍勿在不可信环境分享整段日志。
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Mapping, Sequence

_LOG = logging.getLogger("jachin.deep")


def deep_log_enabled() -> bool:
    v = (os.environ.get("JACHIN_L3_DEEP_LOG") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


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


def emit_block(title: str, body: str) -> None:
    """写入一条大块深度日志（脱敏 + 控制台/文件/SSE 由 logging 配置决定）。"""
    if not deep_log_enabled():
        return
    text = f"========== {title} ==========\n{redact_secrets(body)}\n========== end {title} =========="
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
    ]
    if error:
        parts.append(f"ERROR={error}")
    body = "\n".join(parts)
    body += "\n\n[REQUEST messages]\n" + format_messages_for_deep(messages)
    if tools is not None:
        body += "\n\n[REQUEST tools]\n" + format_tools_brief(tools)
    if response_dict_summary:
        body += "\n\n[RESPONSE summary]\n" + redact_secrets(response_dict_summary)
    if response_text is not None:
        body += "\n\n[RESPONSE text]\n" + _truncate(redact_secrets(response_text), 120_000)
    emit_block(f"LLM {purpose} ({'stream' if stream else 'complete'})", body)


def log_tool_execution(
    *,
    trace: str,
    run_id: str,
    tool: str,
    action_input: str,
    output: str,
    elapsed_ms: float,
    mcp: bool,
) -> None:
    if not deep_log_enabled():
        return
    body = "\n".join(
        [
            f"trace={trace}",
            f"run_id={run_id}",
            f"tool={tool}",
            f"mcp={mcp}",
            f"elapsed_ms={elapsed_ms:.1f}",
            f"action_input_len={len(action_input or '')}",
            "[ACTION_INPUT]\n" + _truncate(redact_secrets(action_input or ""), 80_000),
            f"output_len={len(output or '')}",
            "[OUTPUT]\n" + _truncate(redact_secrets(output or ""), 120_000),
        ]
    )
    emit_block(f"TOOL {tool}", body)


def log_react_iteration_context(
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
    emit_block("ReAct iteration (pre-LLM)", body)


def log_react_llm_result(
    *,
    trace: str,
    iteration: int,
    response_len: int,
    response_full: str,
) -> None:
    if not deep_log_enabled():
        return
    body = "\n".join(
        [
            f"trace={trace}",
            f"iteration={iteration}",
            f"response_len={response_len}",
            "[RAW_MODEL_OUTPUT]\n" + _truncate(redact_secrets(response_full or ""), 200_000),
        ]
    )
    emit_block("ReAct LLM raw output", body)


def log_react_parse_result(
    *,
    trace: str,
    iteration: int,
    parsed_summary: str,
) -> None:
    if not deep_log_enabled():
        return
    emit_block(
        "ReAct parse",
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
    emit_block(f"Pipeline {phase}", redact_secrets(detail))


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
