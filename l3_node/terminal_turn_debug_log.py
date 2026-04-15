"""
终端 / WebSocket 每轮用户提问的本地调试日志：**默认每次新对话单独一个文件**（不覆盖）。

文件名形如 ``terminal_turn_20260410T083905Z_ab12cd_ef4091a2.log``（UTC 时间 + run_id 摘要 + 随机后缀），
与当轮内所有 ``append_*`` / ReAct 轨迹写入同一文件。多 WebSocket 并发时按 asyncio 任务隔离路径
（``contextvars``），避免互相串写。

若需恢复旧行为（始终写入并覆盖 ``terminal_turn_debug.log``），设置
``JACHIN_TERMINAL_DEBUG_OVERWRITE=1``。

默认目录（Windows）：``%USERPROFILE%\\.jachin\\jachin_debug``；非 Windows：``~/.jachin/terminal_debug``。

环境变量：
- ``JACHIN_TERMINAL_DEBUG_LOG``：``0`` / ``false`` 关闭
- ``JACHIN_TERMINAL_DEBUG_OVERWRITE``：``1`` / ``true`` 时单文件覆盖模式（``terminal_turn_debug.log``）
- ``JACHIN_TERMINAL_DEBUG_DIR``：目录覆盖
- ``JACHIN_TERMINAL_DEBUG_MAX_CHARS``：单块文本最大字符数（默认 3000000）
- ``JACHIN_TERMINAL_DEBUG_STREAM_MAX``：本轮流式 chunk 累计最大字符（默认 800000），超出则停止记入 chunk
- ``JACHIN_TERMINAL_DEBUG_REDACT``：默认 1，对疑似密钥做简单脱敏（复用 core.deep_execution_log）
"""
from __future__ import annotations

import contextvars
import json
import os
import platform
import re
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ENV_KEYS_FOR_SNAPSHOT = (
    "LLM_MODEL",
    "LLM_COMPLEX_MODEL",
    "LLM_CODER_MODEL",
    "JACHIN_LLM_COMPLEX_DISABLE",
    "JACHIN_L3_DEEP_LOG",
    "JACHIN_TERMINAL_DEBUG_LOG",
    "JACHIN_REACT_STREAM_DISABLE_TOOLS",
    "JACHIN_TERMINAL_DEBUG_MAX_CHARS",
    "JACHIN_TERMINAL_DEBUG_STREAM_MAX",
    "JACHIN_TERMINAL_DEBUG_OVERWRITE",
)

_lock = threading.Lock()
_LEGACY_FILE_NAME = "terminal_turn_debug.log"
# 当前 asyncio 任务对应的日志文件（per-turn 模式）；单文件模式不使用
_turn_log_path: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "jachin_terminal_turn_log_path",
    default=None,
)
_stream_accumulator: list[str] = []
_stream_total: int = 0


def _enabled() -> bool:
    v = (os.environ.get("JACHIN_TERMINAL_DEBUG_LOG") or "").strip().lower()
    return v not in ("0", "false", "no", "off")


def _single_file_overwrite_mode() -> bool:
    """为 true 时与旧版一致：只写 ``terminal_turn_debug.log`` 且每轮覆盖。"""
    return (os.environ.get("JACHIN_TERMINAL_DEBUG_OVERWRITE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _safe_filename_slug(s: str, *, max_len: int = 24) -> str:
    t = (s or "").strip()[:max_len]
    t = re.sub(r"[^\w\-.]+", "_", t, flags=re.ASCII)
    return t or "norun"


def _dir() -> Path:
    override = (os.environ.get("JACHIN_TERMINAL_DEBUG_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        return Path.home() / ".jachin" / "jachin_debug"
    return Path.home() / ".jachin" / "terminal_debug"


def _legacy_path() -> Path:
    return _dir() / _LEGACY_FILE_NAME


def _lazy_orphan_turn_path() -> Path:
    """未调用 begin_turn 即 append（如非 WS 入口）：为本任务自动建独立文件。"""
    root = _dir()
    root.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    fname = f"terminal_turn_{ts}_orphan_{uuid.uuid4().hex[:10]}.log"
    p = root / fname
    _turn_log_path.set(p)
    try:
        p.write_text(
            "=== terminal turn debug（未调用 begin_turn，首次写入时自动创建本文件）===\n",
            encoding="utf-8",
            newline="\n",
        )
    except OSError:
        pass
    return p


def _path() -> Path | None:
    """当前应追加写入的日志路径（不在持锁状态下调用 _lazy_orphan，避免与 append_* 死锁）。"""
    if not _enabled():
        return None
    if _single_file_overwrite_mode():
        return _legacy_path()
    p = _turn_log_path.get()
    if p is not None:
        return p
    return _lazy_orphan_turn_path()


def _max_field() -> int:
    try:
        return max(10_000, int(os.environ.get("JACHIN_TERMINAL_DEBUG_MAX_CHARS") or "3000000"))
    except ValueError:
        return 3_000_000


def _stream_max() -> int:
    try:
        return max(0, int(os.environ.get("JACHIN_TERMINAL_DEBUG_STREAM_MAX") or "800000"))
    except ValueError:
        return 800_000


def _redact(text: str) -> str:
    if (os.environ.get("JACHIN_TERMINAL_DEBUG_REDACT") or "1").strip().lower() in ("0", "false", "no", "off"):
        return text or ""
    try:
        from core.deep_execution_log import redact_secrets

        return redact_secrets(text or "")
    except Exception:
        return text or ""


def _truncate(s: str, max_len: int | None = None) -> str:
    max_len = max_len or _max_field()
    if len(s) <= max_len:
        return s
    return s[:max_len] + f"\n\n... [truncated: total {len(s)} chars, cap={max_len}]"


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _host_snapshot_dict() -> dict[str, Any]:
    """进程与环境快照（不含密钥类 env）。"""
    out: dict[str, Any] = {
        "pid": os.getpid(),
        "cwd": os.getcwd(),
        "platform": platform.platform(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "executable": sys.executable,
    }
    env_bits: dict[str, str] = {}
    for key in _ENV_KEYS_FOR_SNAPSHOT:
        v = (os.environ.get(key) or "").strip()
        if v:
            env_bits[key] = v
    if env_bits:
        out["env"] = env_bits
    return out


def reset_stream_accumulator() -> None:
    """新一轮用户提问时由 begin_turn 调用，清空流式累计。"""
    global _stream_accumulator, _stream_total
    with _lock:
        _stream_accumulator = []
        _stream_total = 0


def begin_turn(user_text: str, *, extra: dict[str, Any] | None = None) -> None:
    """新一轮用户提问：新建日志文件并写文件头（默认每轮独立文件；单文件模式则覆盖 legacy 文件）。"""
    if not _enabled():
        return
    reset_stream_accumulator()
    text = (user_text or "").strip()
    ts = _utc()
    lines = [
        "=== terminal turn debug（本轮独立日志文件）===",
        f"utc={ts}",
        f"log_file_mode={'single_overwrite' if _single_file_overwrite_mode() else 'per_turn_new_file'}",
        f"user_message:\n{_redact(text)}\n",
    ]
    if extra:
        try:
            lines.append("extra:\n" + json.dumps(extra, ensure_ascii=False, indent=2) + "\n")
        except Exception:
            lines.append(f"extra: {extra!r}\n")
    try:
        lines.append(
            "host_snapshot:\n"
            + json.dumps(_host_snapshot_dict(), ensure_ascii=False, indent=2)
            + "\n"
        )
    except Exception as _e:
        lines.append(f"host_snapshot: (failed: {_e!r})\n")
    lines.append(
        "说明：以下为 L3 ReAct 深度轨迹 — 轮次上下文 / llm_round_meta / llm_raw / parsed / "
        "tool_input_full / tool_dispatch_timing / observation_full / stream_chunk / on_step\n"
    )
    lines.append("=" * 72 + "\n")
    try:
        root = _dir()
        root.mkdir(parents=True, exist_ok=True)
        if _single_file_overwrite_mode():
            p = _legacy_path()
            _turn_log_path.set(None)
            lines[0] = f"=== terminal turn debug | log_path={p.name} (overwrite) ==="
        else:
            ts_compact = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            run_slug = _safe_filename_slug(str((extra or {}).get("run_id") or ""))
            uniq = uuid.uuid4().hex[:8]
            p = root / f"terminal_turn_{ts_compact}_{run_slug}_{uniq}.log"
            _turn_log_path.set(p)
            lines[0] = f"=== terminal turn debug | log_path={p.name} ==="
        blob = "\n".join(lines)
        with _lock:
            p.write_text(blob, encoding="utf-8", newline="\n")
    except OSError:
        pass


def append_section(heading: str, body: str) -> None:
    """大块分区写入（脱敏 + 截断）。"""
    if not _enabled():
        return
    body = _redact(_truncate(body or ""))
    block = f"\n{'=' * 72}\n{heading}\nutc={_utc()}\n{'-' * 72}\n{body}\n"
    try:
        p = _path()
        if p is None:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            if not p.is_file():
                p.write_text("", encoding="utf-8")
            with p.open("a", encoding="utf-8", newline="\n") as f:
                f.write(block)
    except OSError:
        pass


def append_line(step_type: str, content: str, *, max_chars: int | None = None) -> None:
    """on_step 等事件：thought / action / observation / answer / chunk / system_status / error …"""
    if not _enabled():
        return
    cap = max_chars if max_chars is not None else _max_field()
    raw_len = len(content or "")
    c = _redact(content or "")
    if len(c) > cap:
        c = c[:cap] + f"\n... [truncated, total {raw_len} chars]"
    line = f"\n>>> [{step_type}] utc={_utc()} content_chars={raw_len}\n{c}\n"
    try:
        p = _path()
        if p is None:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            if not p.is_file():
                p.write_text("", encoding="utf-8")
            with p.open("a", encoding="utf-8", newline="\n") as f:
                f.write(line)
    except OSError:
        pass


def append_stream_chunk(chunk: str) -> None:
    """流式输出片段（仅内存累计；本轮结束时 flush_stream_summary 一次性写入日志）。"""
    if not _enabled():
        return
    smax = _stream_max()
    if smax <= 0:
        return
    ch = chunk or ""
    if not ch:
        return
    global _stream_total
    with _lock:
        if _stream_total >= smax:
            return
        take = ch[: max(0, smax - _stream_total)]
        if not take:
            return
        _stream_total += len(take)
        _stream_accumulator.append(take)


def flush_stream_summary() -> None:
    """本轮结束：把流式累计全文写入日志。"""
    if not _enabled():
        return
    with _lock:
        if not _stream_accumulator:
            blob = ""
        else:
            blob = "".join(_stream_accumulator)
    if not blob:
        return
    smax = _stream_max()
    note = f"（累计 {len(blob)} 字符，上限配置 JACHIN_TERMINAL_DEBUG_STREAM_MAX={smax}）"
    append_section(f"[流式输出累计全文] {note}", blob)


def log_react_iteration_start(
    iteration: int,
    trace: str,
    *,
    context: dict[str, Any] | None = None,
) -> None:
    body = f"trace={trace}"
    if context:
        try:
            body += "\n\n--- 轮次上下文 (JSON) ---\n" + json.dumps(
                context, ensure_ascii=False, indent=2, default=str
            )
        except Exception:
            body += f"\n\ncontext={context!r}"
    append_section(f"[ReAct 第 {iteration} 轮] 开始", body)


def log_llm_assistant_raw(iteration: int, trace: str, response: str) -> None:
    append_section(
        f"[ReAct 第 {iteration} 轮] 模型完整输出（含 Thought / Action / Final Answer 等，未截断）",
        f"trace={trace}\n\n{response or ''}",
    )


def log_llm_round_summary(
    iteration: int,
    trace: str,
    *,
    purpose: str,
    stream: bool,
    model_effective: str,
    model_session_default: str,
    elapsed_ms: float,
    n_full_messages: int,
    system_prompt_chars: int,
    openai_tools: bool,
    openai_tool_count: int,
    response_chars: int,
    error: str | None = None,
) -> None:
    """单次 LLM 调用元数据（与 log_llm_assistant_raw 配套，便于按轮 grep）。"""
    meta: dict[str, Any] = {
        "trace": trace,
        "purpose": purpose,
        "stream": stream,
        "model_effective": model_effective,
        "model_session_default": model_session_default,
        "elapsed_ms": round(elapsed_ms, 2),
        "n_messages_including_system": n_full_messages,
        "system_prompt_chars": system_prompt_chars,
        "openai_native_tools": openai_tools,
        "openai_tool_count": openai_tool_count,
        "response_chars": response_chars,
    }
    if error:
        meta["error"] = error
    try:
        body = json.dumps(meta, ensure_ascii=False, indent=2)
    except Exception:
        body = repr(meta)
    append_section(f"[ReAct 第 {iteration} 轮] LLM 调用摘要", body)


def log_tool_dispatch_summary(
    iteration: int,
    trace: str,
    *,
    tool: str,
    mcp: bool,
    elapsed_ms: float,
    output_len: int,
    action_input_len: int,
    used_foreground_timeout: bool,
    sync_timeout_sec: float | None,
) -> None:
    """工具真正执行完毕后的耗时与路由信息（接在 tool_input_full 之后便于对照）。"""
    meta = {
        "trace": trace,
        "tool_id": tool,
        "mcp": mcp,
        "elapsed_ms": round(elapsed_ms, 2),
        "action_input_len": action_input_len,
        "output_len": output_len,
        "foreground_timeout_enforced": used_foreground_timeout,
        "sync_timeout_sec": sync_timeout_sec,
    }
    try:
        body = json.dumps(meta, ensure_ascii=False, indent=2)
    except Exception:
        body = repr(meta)
    append_section(f"[ReAct 第 {iteration} 轮] 工具调度完成（耗时与通道）", body)


def log_event(iteration: int | None, title: str, detail: str) -> None:
    """通用打点：iteration 可为 None 表示与轮次无关。"""
    tag = f"[{title}]" if iteration is None else f"[ReAct 第 {iteration} 轮] {title}"
    append_section(tag, detail)


def log_parsed_action_detail(
    iteration: int,
    parsed: Any,
    summary: str,
    *,
    thought_excerpt: str = "",
    trace: str = "",
) -> None:
    raw = "(null)"
    if parsed is not None:
        try:
            if isinstance(parsed, dict):
                raw = json.dumps(parsed, ensure_ascii=False, indent=2, default=str)
            else:
                raw = json.dumps(parsed, ensure_ascii=False, default=str)
        except Exception:
            raw = repr(parsed)
    lines = [f"trace={trace or '(n/a)'}", f"summary={summary or '(empty)'}"]
    if (thought_excerpt or "").strip():
        lines.append("--- Thought 节选 ---")
        lines.append(_truncate(_redact(thought_excerpt.strip()), 24_000))
    lines.append("--- raw parsed ---")
    lines.append(raw)
    body = "\n".join(lines)
    append_section(f"[ReAct 第 {iteration} 轮] 解析结果 parsed_action", body)


def log_tool_call_full(iteration: int, tool: str, action_input: str, *, note: str = "") -> None:
    body = f"tool_id={tool or ''}\n{note}\n\n--- Action Input 全文 ---\n{action_input or ''}"
    append_section(f"[ReAct 第 {iteration} 轮] 工具调用（完整入参）", body)


def log_observation_full(
    iteration: int,
    tool: str,
    observation_full: str,
    *,
    sent_to_llm_len: int,
    truncated_from_len: int | None = None,
) -> None:
    note = f"tool_id={tool or ''}\nobservation 原始长度={len(observation_full or '')}"
    if truncated_from_len is not None:
        note += f"\n截断后供 LLM 长度={sent_to_llm_len}（原始 {truncated_from_len}）"
    else:
        note += f"\n传入 LLM 的 observation 长度={sent_to_llm_len}"
    body = f"{note}\n\n--- Observation 全文（写入日志前未做截断） ---\n{observation_full or ''}"
    append_section(f"[ReAct 第 {iteration} 轮] 工具返回 Observation", body)


def append_final(tag: str, text: str, *, extra: dict[str, Any] | None = None) -> None:
    try:
        flush_stream_summary()
    except Exception:
        pass
    body = text or ""
    if extra:
        try:
            body = (
                (body + "\n\n" if body else "")
                + "--- 结束阶段元数据 ---\n"
                + json.dumps(extra, ensure_ascii=False, indent=2, default=str)
            )
        except Exception:
            body = f"{body}\n\nextra={extra!r}"
    append_section(f"[本轮结束] {tag}", body)
    if not _single_file_overwrite_mode():
        _turn_log_path.set(None)
