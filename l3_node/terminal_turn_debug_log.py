"""
终端 / WebSocket 每轮用户提问的本地调试日志：**默认每次新对话单独一个文件**（不覆盖）。

文件名形如 ``terminal_turn_20260410T083905Z_ab12cd_ef4091a2.log``（UTC 时间 + run_id 摘要 + 随机后缀），
与当轮内所有 ``append_*`` / ReAct 轨迹写入同一文件。多 WebSocket 并发时按 asyncio 任务隔离路径
（``contextvars``），避免互相串写。

**人类可读分区**（搜索 ``【人类可读】``）：
- 会话导读：用户问了什么、渠道、最多几轮
- 按轮展开：模型在想什么 → 调什么工具、在哪执行 → 返回摘要
- 会话复盘：共几轮、用了哪些工具、最终回答、效果简述
- 其下 ``[ReAct 第 N 轮]`` 等为技术细节（完整 JSON / Observation）

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
from dataclasses import dataclass, field
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
# IM 分发器在独立线程 send_reply 时无法读到 asyncio 的 contextvar；按 run_id / chat_id 索引日志路径。
_turn_log_by_run_id: dict[str, Path] = {}
_turn_log_by_lark_chat: dict[str, Path] = {}
_LEGACY_FILE_NAME = "terminal_turn_debug.log"
# 当前 asyncio 任务对应的日志文件（per-turn 模式）；单文件模式不使用
_turn_log_path: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "jachin_terminal_turn_log_path",
    default=None,
)
# append_final 后仍允许同轮追加技术尾注（如 WS 与 run_agent 双写结束块）
_turn_log_path_settled: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "jachin_terminal_turn_log_path_settled",
    default=None,
)
_stream_accumulator: list[str] = []
_stream_total: int = 0

_human_journal: contextvars.ContextVar["_HumanJournal | None"] = contextvars.ContextVar(
    "jachin_terminal_human_journal",
    default=None,
)

_TOOL_WHERE_HINTS: dict[str, str] = {
    "core:db_query": "查询本机 SQLite / 结构化数据",
    "core:fs_read": "读取工作区文件",
    "core:fs_write": "写入工作区文件",
    "core:pmo_personnel_report": "汇总 PMO 人员任务矩阵",
    "core:pmo_sprint_epic_report": "汇总 PMO 大需求与 Sprint 进度",
    "core:pmo_release_epic_mapping": "发版邮件窗内已完成 Epic（Worker D）",
    "core:pmo_macro_dashboard_push": "组装战报并推送飞书",
    "core:pmo_mirror_import": "把飞书拉盘 md 镜像进 SQLite",
    "mcp:atom_bi_project_context": "从飞书 Wiki/多维表拉取项目上下文",
    "mcp:atom_lark_notifier": "向飞书群发送消息卡片",
    "mcp:fetch": "抓取网页内容",
    "delegate": "拆成子 Agent 并行执行",
    "recall_memory": "检索长期记忆（Memory Nexus）",
    "coordinate": "分布式多节点协调（L2）",
}


@dataclass
class _HumanRound:
    iteration: int = 0
    thought: str = ""
    decision: str = ""
    tool_id: str = ""
    tool_where: str = ""
    tool_input_brief: str = ""
    tool_elapsed_ms: float | None = None
    observation_brief: str = ""
    ended_with_answer: bool = False
    final_answer_preview: str = ""


def _lark_chat_id_from_extra(ex: dict[str, Any] | None) -> str:
    if not ex:
        return ""
    return str(ex.get("lark_chat_id") or ex.get("chat_id") or "").strip()


def _lark_reply_chat_id_from_extra(ex: dict[str, Any] | None) -> str:
    if not ex:
        return ""
    return str(
        ex.get("lark_reply_chat_id") or ex.get("reply_chat_id") or ex.get("lark_chat_id") or ex.get("chat_id") or ""
    ).strip()


def _is_lark_im_channel(channel: str) -> bool:
    ch = (channel or "").strip().lower()
    return "lark" in ch


def _apply_lark_chat_id_to_journal(j: _HumanJournal, ex: dict[str, Any] | None) -> None:
    inbound = _lark_chat_id_from_extra(ex)
    if inbound:
        j.lark_chat_id = inbound
    reply = _lark_reply_chat_id_from_extra(ex)
    if reply:
        j.lark_reply_chat_id = reply
    elif inbound and not j.lark_reply_chat_id:
        j.lark_reply_chat_id = inbound


def _lark_routing_human_lines(j: _HumanJournal) -> list[str]:
    """飞书 IM 来源/回推 chat_id 人话块（来源与目标相同时仍显式各写一行）。"""
    inbound = (j.lark_chat_id or "").strip()
    reply = (j.lark_reply_chat_id or inbound or "").strip()
    if not inbound and not reply:
        return []
    lines = [
        "【飞书会话路由】",
        f"  来源会话 chat_id（用户从哪发消息）：{inbound or '（未记录）'}",
        f"  回复目标 chat_id（须发回哪）：{reply or '（未记录）'}",
    ]
    if inbound and reply and inbound != reply:
        lines.append("  （来源与回推目标不同：例如镜像终端/跨群转发场景）")
    return lines


@dataclass
class _HumanJournal:
    user_message: str = ""
    run_id: str = ""
    channel: str = ""
    lark_chat_id: str = ""
    lark_reply_chat_id: str = ""
    lark_reply_sent: bool = False
    lark_reply_send_ok: bool | None = None
    max_iterations: int = 0
    model_hint: str = ""
    execution_tier: str = ""
    rounds: list[_HumanRound] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    final_answer: str = ""
    end_tag: str = ""
    voice_diagnostics: dict[str, Any] = field(default_factory=dict)
    file_started: bool = False
    recap_written: bool = False
    config_logged: bool = False

    def current_round(self, iteration: int) -> _HumanRound:
        for r in self.rounds:
            if r.iteration == iteration:
                return r
        nr = _HumanRound(iteration=iteration)
        self.rounds.append(nr)
        return nr


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
    _reset_human_journal()
    try:
        p.write_text(
            "=== terminal turn debug（未调用 begin_turn，首次写入时自动创建本文件）===\n",
            encoding="utf-8",
            newline="\n",
        )
    except OSError:
        pass
    j = _journal()
    if j is not None and (j.user_message or "").strip() and not j.file_started:
        _write_human_session_intro(j)
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
    settled = _turn_log_path_settled.get()
    if settled is not None:
        return settled
    return _lazy_orphan_turn_path()


def _register_turn_log_path(
    path: Path,
    *,
    run_id: str = "",
    lark_chat_id: str = "",
) -> None:
    """供 IM 分发器等跨线程追加写入同一 turn 日志。"""
    rid = (run_id or "").strip()
    cid = (lark_chat_id or "").strip()
    if not rid and not cid:
        return
    with _lock:
        if rid:
            _turn_log_by_run_id[rid] = path
        if cid:
            _turn_log_by_lark_chat[cid] = path


def _resolve_turn_log_path(
    *,
    run_id: str = "",
    lark_chat_id: str = "",
) -> Path | None:
    rid = (run_id or "").strip()
    cid = (lark_chat_id or "").strip()
    with _lock:
        if rid and rid in _turn_log_by_run_id:
            return _turn_log_by_run_id[rid]
        if cid and cid in _turn_log_by_lark_chat:
            return _turn_log_by_lark_chat[cid]
    p = _turn_log_path.get()
    if p is not None:
        return p
    settled = _turn_log_path_settled.get()
    if settled is not None:
        return settled
    return None


def _append_section_to_path(path: Path, heading: str, body: str) -> None:
    body = _redact(_truncate(body or ""))
    block = f"\n{'=' * 72}\n{heading}\nutc={_utc()}\n{'-' * 72}\n{body}\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(block)
    except OSError:
        pass


def append_section_cross_thread(
    heading: str,
    body: str,
    *,
    run_id: str = "",
    lark_chat_id: str = "",
) -> None:
    """跨线程写入 turn 日志（优先 run_id，其次 lark_chat_id 索引）。"""
    if not _enabled():
        return
    p = _resolve_turn_log_path(run_id=run_id, lark_chat_id=lark_chat_id)
    if p is None:
        return
    _append_section_to_path(p, heading, body)


def log_lark_im_session_routing(
    *,
    inbound_chat_id: str,
    reply_chat_id: str = "",
    channel: str = "",
    user_id: str = "",
    run_id: str = "",
) -> None:
    """飞书 IM 入站：记录来源会话与须回推的目标 chat_id。"""
    if not _enabled():
        return
    inbound = (inbound_chat_id or "").strip()
    reply = (reply_chat_id or inbound or "").strip()
    if not inbound and not reply:
        return
    j = _journal()
    if j is not None:
        if inbound:
            j.lark_chat_id = inbound
        if reply:
            j.lark_reply_chat_id = reply
    ex = {
        "inbound_chat_id": inbound,
        "reply_chat_id": reply,
        "channel": channel or "",
        "user_id": user_id or "",
        "run_id": run_id or "",
    }
    body_lines = [
        f"来源会话 chat_id（用户从哪发消息）：{inbound or '（未记录）'}",
        f"回复目标 chat_id（须发回哪）：{reply or '（未记录）'}",
    ]
    if inbound and reply and inbound != reply:
        body_lines.append("说明：来源与回推目标不同（例如镜像终端/跨群转发）。")
    append_section_cross_thread(
        "[飞书 IM] 会话路由",
        "\n".join(body_lines) + "\n\n" + json.dumps(ex, ensure_ascii=False, indent=2),
        run_id=run_id,
        lark_chat_id=inbound or reply,
    )
    if j is not None and j.file_started:
        _append_human_block([""] + _lark_routing_human_lines(j))


def log_lark_im_reply_dispatch(
    *,
    reply_chat_id: str,
    inbound_chat_id: str = "",
    ok: bool,
    reply_preview: str = "",
    run_id: str = "",
) -> None:
    """飞书 IM 出站：记录实际发回哪条 chat_id 及发送结果。"""
    if not _enabled():
        return
    reply = (reply_chat_id or "").strip()
    inbound = (inbound_chat_id or reply or "").strip()
    if not reply:
        return
    j = _journal()
    if j is not None:
        if inbound:
            j.lark_chat_id = inbound
        j.lark_reply_chat_id = reply
        j.lark_reply_sent = True
        j.lark_reply_send_ok = ok
    preview = _truncate((reply_preview or "").strip().replace("\n", " "), 240)
    ok_txt = "成功" if ok else "失败"
    body = (
        f"来源会话 chat_id：{inbound or '（未记录）'}\n"
        f"回复目标 chat_id：{reply}\n"
        f"发送结果：{ok_txt}\n"
    )
    if preview:
        body += f"回复预览：{preview}\n"
    append_section_cross_thread(
        f"[飞书 IM] 回推发送 ({ok_txt})",
        body,
        run_id=run_id,
        lark_chat_id=inbound or reply,
    )


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


def _journal() -> _HumanJournal | None:
    return _human_journal.get()


def _reset_human_journal() -> _HumanJournal:
    j = _HumanJournal()
    _human_journal.set(j)
    return j


def _append_human_block(lines: list[str]) -> None:
    """写入「人类可读」分区（不截断到极小，单块上限与 _max_field 一致）。"""
    if not _enabled():
        return
    body = _redact(_truncate("\n".join(lines)))
    block = f"\n{'─' * 72}\n【人类可读】\n{body}\n"
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


def _tool_where_line(tool_id: str, *, mcp: bool = False) -> str:
    tid = (tool_id or "").strip()
    if tid == "delegate":
        return "子 Agent 编排（宿主内再开一轮 ReAct）"
    if tid.startswith("core:"):
        hint = _TOOL_WHERE_HINTS.get(tid, "宿主本机 Python 执行（Native 工具）")
        return f"{hint} · 路径：L3 进程内 run_tool"
    if tid.startswith("mcp:") or mcp:
        hint = _TOOL_WHERE_HINTS.get(tid, "MCP 外部服务")
        return f"{hint} · 路径：MCP 子进程 stdio/HTTP"
    if tid.startswith("util:"):
        return "宿主内置实用工具 · 路径：L3 进程内"
    return "工具运行时"


def _brief_action_input(tool_id: str, action_input: str, *, max_len: int = 900) -> str:
    raw = (action_input or "").strip()
    if not raw:
        return "（无参数）"
    tid = (tool_id or "").strip()
    if tid == "core:db_query":
        try:
            obj = json.loads(raw)
            sql = str(obj.get("sql") or obj.get("query") or "").strip()
            if sql:
                sql = re.sub(r"\s+", " ", sql)
                return _truncate(sql, max_len)
        except (json.JSONDecodeError, TypeError):
            pass
        if raw.upper().startswith("SELECT"):
            return _truncate(re.sub(r"\s+", " ", raw), max_len)
    if raw.startswith("{") or raw.startswith("["):
        try:
            obj = json.loads(raw)
            return _truncate(json.dumps(obj, ensure_ascii=False), max_len)
        except (json.JSONDecodeError, TypeError):
            pass
    return _truncate(raw.replace("\n", " "), max_len)


def _brief_observation(tool_id: str, observation: str, *, max_len: int = 1200) -> str:
    text = (observation or "").strip()
    if not text:
        return "工具没有返回内容。"
    low = text.lower()
    if "[工具执行失败]" in text or '"status": "error"' in low or '"status":"error"' in low:
        head = "执行失败。"
    elif '"status": "ok"' in low or '"status":"ok"' in low or "success" in low[:200].lower():
        head = "执行成功。"
    else:
        head = "已返回结果。"
    preview = re.sub(r"\s+", " ", text[: max_len - len(head)])
    if len(text) > len(preview):
        preview += f"…（共 {len(text)} 字，悬停/下文技术区可看全文）"
    return head + preview


def _write_human_session_intro(j: _HumanJournal) -> None:
    ch = (j.channel or "未知").strip()
    ch_human = {
        "websocket_terminal": "桌面终端 WebSocket",
        "websocket_lark": "飞书 WebSocket 桥",
        "lark_im_dispatcher": "飞书 IM 消息分发",
        "pmo_copilot_cli": "PMO Copilot 命令行",
    }.get(ch, ch or "（未标注）")
    max_it = j.max_iterations or "（未设置）"
    lines = [
        "╔══════════════════════════════════════════════════════════════════════╗",
        "║  会话导读 — 帮你快速看懂「问了什么 → 怎么做的 → 答了什么」              ║",
        "╚══════════════════════════════════════════════════════════════════════╝",
        "",
        "【用户这次问了什么】",
        f"  {j.user_message or '（启动时未记录用户句，见下方 messages 快照）'}",
        "",
        "【系统怎么接这个活】",
        "  采用 ReAct 多轮推理：每一轮模型先「想一步」，再决定「调工具拿数据」或「直接回答」。",
        f"  · 来源渠道：{ch_human}",
        f"  · 本轮最多推理：{max_it} 轮",
    ]
    if j.run_id:
        lines.append(f"  · 运行 ID：{j.run_id}")
    lark_routing = _lark_routing_human_lines(j)
    if lark_routing:
        lines.extend([""] + lark_routing)
    elif j.lark_chat_id:
        lines.append(f"  · 飞书会话 chat_id（回复须发回该群）：{j.lark_chat_id}")
    elif _is_lark_im_channel(ch):
        lines.extend(
            [
                "",
                "【飞书会话路由】",
                "  来源会话 chat_id：（未记录 — 检查 implicit_attribution.lark_chat_id）",
                "  回复目标 chat_id：（未记录）",
            ]
        )
    if j.execution_tier:
        lines.append(f"  · 任务复杂度档位：{j.execution_tier}")
    if j.model_hint:
        lines.append(f"  · 主模型：{j.model_hint}")
    lines.extend(
        [
            "",
            "【日志怎么读】",
            "  下面按「第 1 轮 / 第 2 轮 …」展开：每轮包含模型想法、调了什么工具、在哪执行、得到什么。",
            "  更底层的 JSON / 完整 Observation 在「技术细节」分区（搜索 [ReAct 第 N 轮]）。",
            "",
        ]
    )
    _append_human_block(lines)
    if j.voice_diagnostics:
        _write_voice_diagnostics_human(j.voice_diagnostics, title="【本轮语音链路（进入 L3 前）】")
    j.file_started = True


def _fmt_voice_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            return str(value)
    return str(value)


def _voice_line(label: str, data: dict[str, Any], *keys: str) -> str | None:
    bits: list[str] = []
    for key in keys:
        if key in data and data.get(key) not in (None, ""):
            bits.append(f"{key}={_fmt_voice_value(data.get(key))}")
    if not bits:
        return None
    return f"  · {label}：" + "；".join(bits)


def _write_voice_diagnostics_human(diag: dict[str, Any] | None, *, title: str = "【本轮语音链路】") -> None:
    lines = _voice_diagnostics_human_lines(diag, title=title)
    if lines:
        _append_human_block(lines)


def _voice_diagnostics_human_lines(diag: dict[str, Any] | None, *, title: str = "【本轮语音链路】") -> list[str]:
    if not isinstance(diag, dict) or not diag:
        return []
    lines = [
        title,
        f"  trace_id={diag.get('traceId') or diag.get('trace_id') or '（未记录）'}；profile={diag.get('profile') or '（未记录）'}；elapsed_ms={diag.get('elapsedMs') or diag.get('elapsed_ms') or '（未记录）'}；events={diag.get('eventCount') or len(diag.get('events') or [])}",
    ]
    stt = diag.get("stt") if isinstance(diag.get("stt"), dict) else {}
    sv = diag.get("sv") if isinstance(diag.get("sv"), dict) else {}
    tts = diag.get("tts") if isinstance(diag.get("tts"), dict) else {}
    l3 = diag.get("l3") if isinstance(diag.get("l3"), dict) else {}
    for line in [
        _voice_line("STT 识别", stt, "last_stage", "text", "rawText", "correctedText", "confidence", "backend", "durationMs", "latencyMs", "pipelineMs", "source", "hotwordStatus", "hotwordCount", "hotwordDominated"),
        _voice_line("SV 声纹/主人声道", sv, "last_stage", "accepted", "usedOwnerTrack", "reason", "ownerDurationMs", "skippedSegmentsCount", "latencyMs", "error"),
        _voice_line("L3 发送/路由", l3, "last_stage", "recognizedText", "wireText", "intentPreview", "answerPreview", "answerLen", "latencyMs", "sessionId", "sensoryConnected", "l2Available"),
        _voice_line("TTS 合成/播放", tts, "last_stage", "sentence", "text", "kind", "reason", "voice", "status", "ok", "latencyMs", "serverSynthMs", "audioDurationMs", "quality", "ttsKind", "styleIndex", "styleMode", "rawDurationMs", "trimLeadingMs", "trimTrailingMs", "bytes", "totalMs", "err"),
    ]:
        if line:
            lines.append(line)
    errors = diag.get("errors") if isinstance(diag.get("errors"), list) else []
    if errors:
        lines.append("  · 语音错误/异常：")
        for item in errors[-8:]:
            if isinstance(item, dict):
                lines.append("    - " + _truncate(json.dumps(item, ensure_ascii=False, default=str), 1000))
            else:
                lines.append(f"    - {item}")
    events = diag.get("events") if isinstance(diag.get("events"), list) else []
    if events:
        lines.append("  · 关键时间线（最近事件）：")
        for ev in events[-16:]:
            if not isinstance(ev, dict):
                continue
            payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
            preview_keys = ("latencyMs", "pipelineMs", "durationMs", "audioDurationMs", "serverSynthMs", "status", "ok", "error", "err", "text", "sentence")
            preview = {k: payload.get(k) for k in preview_keys if k in payload and payload.get(k) not in (None, "")}
            lines.append(
                f"    - {ev.get('stage')} elapsed={ev.get('elapsedMs')}ms since_prev={ev.get('sincePrevMs')}ms "
                + (_truncate(json.dumps(preview, ensure_ascii=False, default=str), 500) if preview else "")
            )
    return lines


def _write_human_round_header(iteration: int, *, max_iterations: int = 0) -> None:
    j = _journal()
    if j is None:
        return
    cap = max_iterations or j.max_iterations or "?"
    lines = [
        "",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"▶ 第 {iteration} 轮（上限 {cap} 轮）",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    _append_human_block(lines)


def _write_human_session_recap(j: _HumanJournal) -> None:
    if j.rounds:
        n_rounds = max(r.iteration for r in j.rounds)
    else:
        n_rounds = 0
    tools = []
    seen: set[str] = set()
    for t in j.tools_used:
        if t and t not in seen:
            seen.add(t)
            tools.append(t)
    lines = [
        "",
        "╔══════════════════════════════════════════════════════════════════════╗",
        "║  本轮会话复盘                                                         ║",
        "╚══════════════════════════════════════════════════════════════════════╝",
        "",
        "【用户问了什么】",
        f"  {j.user_message or '（未记录）'}",
        "",
    ]
    if j.lark_chat_id or j.lark_reply_chat_id:
        lark_routing = _lark_routing_human_lines(j)
        if lark_routing:
            lines.extend([""] + lark_routing + [""])
        else:
            lines.extend(
                [
                    "【飞书回推目标】",
                    f"  chat_id={j.lark_reply_chat_id or j.lark_chat_id}",
                    "",
                ]
            )
    if j.lark_reply_sent:
        ok_txt = "成功" if j.lark_reply_send_ok else "失败"
        lines.extend(
            [
                "【飞书回推执行】",
                f"  已向回复目标 chat_id={j.lark_reply_chat_id or j.lark_chat_id or '（未记录）'} 发送 IM 回复：{ok_txt}",
                "",
            ]
        )
    lines.extend(
        [
            "【一共跑了几轮】",
            f"  共 {n_rounds} 轮 ReAct 步骤。",
            "",
            "【调用了哪些工具】",
        ]
    )
    if tools:
        for t in tools:
            lines.append(f"  · {t} — {_TOOL_WHERE_HINTS.get(t, _tool_where_line(t))}")
    else:
        lines.append("  · 本轮未调用工具（模型凭上下文/记忆直接作答）。")
    lines.extend(["", "【最终回答用户什么】"])
    ans = (j.final_answer or "").strip()
    if ans:
        preview = _truncate(ans.replace("\n", "\n  "), 4000)
        lines.append(f"  {preview}")
    else:
        lines.append("  （未捕获 Final Answer，见技术区 [本轮结束]）")
    lines.extend(["", "【效果简述】"])
    if j.end_tag and "exception" in j.end_tag.lower():
        lines.append("  运行异常结束，请查看技术区错误栈。")
    elif tools:
        lines.append("  先通过工具取数/执行，再组织成自然语言回复用户。")
    else:
        lines.append("  未再查库或调工具，直接基于会话历史回答（响应较快，但可能未刷新最新数据）。")
    _append_human_block(lines)


def ensure_turn_started(user_text: str, *, extra: dict[str, Any] | None = None) -> None:
    """
    保证本轮有独立日志文件 + 人类可读导读。
    WebSocket 已 begin_turn 时仅补全 journal；Lark/HTTP 等入口在 run_agent 首行调用。
    """
    if not _enabled():
        return
    ex = extra or {}
    j = _journal()
    path_exists = _turn_log_path.get() is not None or _single_file_overwrite_mode()
    if j is None:
        j = _reset_human_journal()
    j.user_message = (user_text or j.user_message or "").strip()
    j.run_id = str(ex.get("run_id") or j.run_id or "")
    j.channel = str(ex.get("channel") or j.channel or "")
    if isinstance(ex.get("voice_diagnostics"), dict):
        j.voice_diagnostics = dict(ex.get("voice_diagnostics") or {})
    _apply_lark_chat_id_to_journal(j, ex)
    try:
        j.max_iterations = int(ex.get("max_iterations") or j.max_iterations or 0)
    except (TypeError, ValueError):
        pass
    if not path_exists:
        begin_turn(user_text, extra=extra)
        j = _journal()
        if j is None:
            return
    elif not j.file_started:
        _write_human_session_intro(j)


def log_human_run_config(meta: dict[str, Any]) -> None:
    """run_agent 进入主流程后补全模型/档位等人话说明。"""
    j = _journal()
    if j is None:
        return
    j.execution_tier = str(meta.get("execution_tier") or j.execution_tier or "")
    try:
        j.max_iterations = int(meta.get("max_iterations") or j.max_iterations or 0)
    except (TypeError, ValueError):
        pass
    if not j.run_id:
        j.run_id = str(meta.get("run_id") or "")
    ch = str(meta.get("channel") or "")
    if ch:
        j.channel = ch
    _apply_lark_chat_id_to_journal(j, meta)
    if not j.config_logged and j.file_started:
        tier = (j.execution_tier or "默认").strip()
        lines = [
            "【系统接活后的配置】",
            f"  任务复杂度档位：{tier}",
            f"  本轮最多推理：{j.max_iterations or '（未设置）'} 轮",
        ]
        if j.model_hint:
            lines.append(f"  主模型：{j.model_hint}")
        if j.channel:
            lines.append(f"  来源渠道：{j.channel}")
        lark_routing = _lark_routing_human_lines(j)
        if lark_routing:
            lines.extend(lark_routing)
        elif j.lark_chat_id:
            lines.append(f"  飞书会话 chat_id：{j.lark_chat_id}")
        _append_human_block(lines)
        j.config_logged = True


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
    _reset_human_journal()
    _turn_log_path_settled.set(None)


def begin_turn(user_text: str, *, extra: dict[str, Any] | None = None) -> None:
    """新一轮用户提问：新建日志文件并写文件头（默认每轮独立文件；单文件模式则覆盖 legacy 文件）。"""
    if not _enabled():
        return
    reset_stream_accumulator()
    ex = extra or {}
    j = _journal() or _reset_human_journal()
    j.user_message = (user_text or "").strip()
    j.run_id = str(ex.get("run_id") or "")
    j.channel = str(ex.get("channel") or "")
    if isinstance(ex.get("voice_diagnostics"), dict):
        j.voice_diagnostics = dict(ex.get("voice_diagnostics") or {})
    _apply_lark_chat_id_to_journal(j, ex)
    try:
        j.max_iterations = int(ex.get("max_iterations") or 0)
    except (TypeError, ValueError):
        pass
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
        "说明：上方为【人类可读】导读；以下为【技术细节】— llm_raw / parsed / tool_input / observation 等\n"
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
        _register_turn_log_path(
            p,
            run_id=j.run_id,
            lark_chat_id=j.lark_chat_id or j.lark_reply_chat_id,
        )
        _write_human_session_intro(j)
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
    ctx = context or {}
    try:
        mi = int(ctx.get("max_iterations") or 0)
    except (TypeError, ValueError):
        mi = 0
    j = _journal()
    if j is not None and mi:
        j.max_iterations = mi
    _write_human_round_header(iteration, max_iterations=mi)
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
    j = _journal()
    if j is not None and meta.get("model_effective"):
        j.model_hint = str(meta.get("model_effective") or j.model_hint)
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
    try:
        j = _journal()
        if j is not None:
            rd = j.current_round(iteration)
            rd.tool_elapsed_ms = float(elapsed_ms)
            where = _tool_where_line(tool, mcp=mcp)
            _append_human_block([
                "",
                "【工具执行完毕】",
                f"  工具：`{tool}`",
                f"  耗时：{elapsed_ms:.0f} ms",
                f"  通道：{'MCP' if mcp else 'Native'}",
                f"  说明：{where}",
                f"  返回数据约 {output_len} 字（详见下方 Observation）",
            ])
    except Exception:
        pass


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
    try:
        j = _journal()
        if j is not None:
            rd = j.current_round(iteration)
            if (thought_excerpt or "").strip():
                rd.thought = _truncate(_redact(thought_excerpt.strip()), 800)
            if isinstance(parsed, dict):
                ptype = str(parsed.get("type") or "")
                if ptype == "answer":
                    rd.ended_with_answer = True
                    content = str(parsed.get("content") or "")
                    rd.final_answer_preview = _truncate(content, 500)
                    rd.decision = "模型认为信息已足够，本轮直接作答（不再调工具）。"
                    human_lines = [
                        "",
                        "【模型在想什么】",
                        f"  {rd.thought or '（本轮回合未单独写出 Thought）'}",
                        "",
                        "【这一步决定做什么】",
                        f"  {rd.decision}",
                    ]
                    if content.strip():
                        human_lines.extend(["", "【答复预览】", f"  {_truncate(content.replace(chr(10), chr(10) + '  '), 1500)}"])
                    _append_human_block(human_lines)
                elif ptype == "native":
                    tool = str(parsed.get("tool") or "")
                    rd.tool_id = tool
                    rd.tool_where = _tool_where_line(tool)
                    rd.decision = f"调用工具 `{tool}`"
                    if tool not in j.tools_used:
                        j.tools_used.append(tool)
                    human_lines = [
                        "",
                        "【模型在想什么】",
                        f"  {rd.thought or '（本轮回合未单独写出 Thought）'}",
                        "",
                        "【这一步决定做什么】",
                        f"  {rd.decision}",
                        "",
                        "【工具在哪执行】",
                        f"  {rd.tool_where}",
                    ]
                    _append_human_block(human_lines)
                elif ptype == "delegate":
                    rd.tool_id = "delegate"
                    rd.decision = "把任务拆给多个子 Agent 并行处理。"
                    j.tools_used.append("delegate")
                    _append_human_block([
                        "",
                        "【模型在想什么】",
                        f"  {rd.thought or '（未写 Thought）'}",
                        "",
                        "【这一步决定做什么】",
                        f"  {rd.decision}",
                    ])
    except Exception:
        pass


def log_tool_call_full(iteration: int, tool: str, action_input: str, *, note: str = "") -> None:
    body = f"tool_id={tool or ''}\n{note}\n\n--- Action Input 全文 ---\n{action_input or ''}"
    append_section(f"[ReAct 第 {iteration} 轮] 工具调用（完整入参）", body)
    try:
        j = _journal()
        if j is not None:
            rd = j.current_round(iteration)
            rd.tool_id = tool or rd.tool_id
            rd.tool_input_brief = _brief_action_input(tool, action_input)
            if tool and tool not in j.tools_used:
                j.tools_used.append(tool)
            _append_human_block([
                "",
                "【传给工具的参数（摘要）】",
                f"  {rd.tool_input_brief}",
            ])
    except Exception:
        pass


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
    try:
        j = _journal()
        if j is not None:
            rd = j.current_round(iteration)
            rd.observation_brief = _brief_observation(tool, observation_full or "")
            _append_human_block([
                "",
                "【工具返回了什么（人话摘要）】",
                f"  {rd.observation_brief}",
                "",
                "  → 以上内容会作为「Observation」喂回模型，进入下一轮推理。",
            ])
    except Exception:
        pass


def finalize_top_level_turn(
    answer: str,
    *,
    delegate_depth: int = 0,
    run_id: str = "",
    channel: str = "",
    tag: str = "final_answer",
    extra: dict[str, Any] | None = None,
) -> None:
    """顶层 run_agent 任意出口调用；子 Agent（delegate_depth>0）不写复盘。"""
    if delegate_depth != 0:
        return
    ex = dict(extra or {})
    if run_id:
        ex.setdefault("run_id", run_id)
    if channel:
        ex.setdefault("channel", channel)
    append_final(tag, answer or "", extra=ex or None)


def append_final(tag: str, text: str, *, extra: dict[str, Any] | None = None) -> None:
    try:
        flush_stream_summary()
    except Exception:
        pass
    j = _journal()
    if j is not None:
        if (text or "").strip() and not j.final_answer:
            j.final_answer = text.strip()
        j.end_tag = tag
        if extra:
            _apply_lark_chat_id_to_journal(j, extra)
            if isinstance(extra.get("voice_diagnostics"), dict):
                j.voice_diagnostics = dict(extra.get("voice_diagnostics") or {})
                _write_voice_diagnostics_human(j.voice_diagnostics, title="【本轮语音链路（结束快照）】")
        if not j.recap_written:
            try:
                _write_human_session_recap(j)
                j.recap_written = True
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
        p = _turn_log_path.get()
        if p is not None:
            _turn_log_path_settled.set(p)
        _turn_log_path.set(None)
        _human_journal.set(None)


def append_voice_diagnostics(
    diagnostics: dict[str, Any],
    *,
    run_id: str = "",
    lark_chat_id: str = "",
    title: str = "[voice] 本轮语音链路追加快照",
) -> None:
    """前端在 TTS/播放结束后追加同一语音 turn 的诊断信息。"""
    if not _enabled() or not isinstance(diagnostics, dict) or not diagnostics:
        return
    try:
        body = json.dumps(diagnostics, ensure_ascii=False, indent=2, default=str)
    except Exception:
        body = repr(diagnostics)
    human_lines = _voice_diagnostics_human_lines(diagnostics, title="【本轮语音链路（追加快照）】")
    human_body = "\n".join(human_lines)
    if run_id or lark_chat_id:
        append_section_cross_thread(title, body, run_id=run_id, lark_chat_id=lark_chat_id)
        if human_body:
            append_section_cross_thread(
                "[voice] 人类可读语音链路追加快照",
                human_body,
                run_id=run_id,
                lark_chat_id=lark_chat_id,
            )
        return
    else:
        append_section(title, body)
    if human_body:
        _append_human_block(human_lines)
