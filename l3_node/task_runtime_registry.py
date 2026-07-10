"""进程内前台 + 后台任务负载摘要（轻量 GlobalTaskRegistry，供 prompt 注入）。线程安全。"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.Lock()
# run_id -> {channel, session_key, started_at_monotonic}
_foreground: dict[str, dict[str, Any]] = {}
# job_id -> {title, schedule_summary, source, registered_at}（APScheduler 等进程内定时任务，供 prompt 一行感知）
_scheduled_hints: dict[str, dict[str, Any]] = {}


def _jachin_workspace_dir() -> Path:
    root = Path(os.environ.get("JACHIN_HOME") or Path.home() / ".jachin").expanduser()
    d = root / "workspace"
    d.mkdir(parents=True, exist_ok=True)
    return d


def external_scheduled_hints_path() -> Path:
    """与独立守护进程约定的心跳 JSON：`external_scheduled_hints.json`。"""
    return _jachin_workspace_dir() / "external_scheduled_hints.json"


def merge_external_scheduled_process_hint(
    *,
    process_key: str,
    title: str,
    schedule_summary: str,
    pid: int | None = None,
) -> None:
    """
    合并写入一条外部进程心跳（与本地文件读侧 **M** 同源）。
    HTTP：`POST /api/v1/registry/external-sched-hint`；脚本：可直接调用本函数（须能 import l3_node）。
    """
    pk = (process_key or "").strip()
    if not pk or len(pk) > 128:
        return
    path = external_scheduled_hints_path()
    try:
        proc: dict[str, Any] = {}
        if path.is_file():
            try:
                old = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(old, dict) and isinstance(old.get("processes"), dict):
                    proc = {str(k): v for k, v in old["processes"].items() if isinstance(v, dict)}
            except Exception:
                proc = {}
        entry: dict[str, Any] = {
            "title": (title or "").strip()[:240] or pk,
            "schedule_summary": (schedule_summary or "").strip()[:480],
            "heartbeat_ts": time.time(),
        }
        if pid is not None:
            try:
                entry["pid"] = int(pid)
            except (TypeError, ValueError):
                pass
        proc[pk] = entry
        payload: dict[str, Any] = {"version": 1, "processes": proc}
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        logger.debug("[task_runtime_registry] merge_external_scheduled_process_hint: %s", e)


def remove_external_scheduled_process_hint(process_key: str) -> bool:
    """从 external_scheduled_hints.json 移除一条 process_key；成功返回 True。"""
    pk = (process_key or "").strip()
    if not pk:
        return False
    path = external_scheduled_hints_path()
    if not path.is_file():
        return False
    try:
        proc: dict[str, Any] = {}
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(old, dict) and isinstance(old.get("processes"), dict):
                proc = {str(k): v for k, v in old["processes"].items() if isinstance(v, dict)}
        except Exception:
            return False
        if pk not in proc:
            return False
        del proc[pk]
        payload: dict[str, Any] = {"version": 1, "processes": proc}
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError as e:
        logger.debug("[task_runtime_registry] remove_external_scheduled_process_hint: %s", e)
        return False


def _external_sched_hints_read_disabled() -> bool:
    return os.environ.get("JACHIN_EXTERNAL_SCHED_HINTS_DISABLE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def read_external_scheduled_hints_dict() -> dict[str, Any]:
    """只读解析 `workspace/external_scheduled_hints.json`（与 **M** 同源）；供 HTTP **U**。提示注入关闭时仍可读文件。"""
    hp_disabled = _external_sched_hints_read_disabled()
    path = external_scheduled_hints_path()
    if not path.is_file():
        return {
            "hints_prompt_read_disabled": hp_disabled,
            "file_present": False,
            "version": None,
            "process_count": 0,
            "processes": {},
        }
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
    except Exception as e:
        logger.debug("[task_runtime_registry] read_external_scheduled_hints_dict: %s", e)
        return {
            "hints_prompt_read_disabled": hp_disabled,
            "file_present": True,
            "parse_error": str(e)[:240],
            "process_count": 0,
            "processes": {},
        }
    if not isinstance(data, dict):
        return {
            "hints_prompt_read_disabled": hp_disabled,
            "file_present": True,
            "error": "not_object",
            "process_count": 0,
            "processes": {},
        }
    procs = data.get("processes")
    if not isinstance(procs, dict):
        procs = {}
    out_procs: dict[str, Any] = {str(k): v for k, v in procs.items() if isinstance(v, dict)}
    return {
        "hints_prompt_read_disabled": hp_disabled,
        "file_present": True,
        "version": data.get("version"),
        "process_count": len(out_procs),
        "processes": out_procs,
    }


def _format_external_scheduled_hints_line(*, stale_sec: float = 900.0) -> str:
    """
    读取 workspace/external_scheduled_hints.json，生成一行 prompt（进程间无锁·读侧容忍竞态）。
    heartbeat_ts 距现在超过 stale_sec 则标注可能已离线。
    """
    if _external_sched_hints_read_disabled():
        return ""
    p = external_scheduled_hints_path()
    if not p.is_file():
        return ""
    try:
        raw = p.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    procs = data.get("processes")
    if not isinstance(procs, dict) or not procs:
        return ""
    now = time.time()
    bits: list[str] = []
    for _pk, rec in list(procs.items())[:8]:
        if not isinstance(rec, dict):
            continue
        t = str(rec.get("title") or "").strip()
        s = str(rec.get("schedule_summary") or "").strip()
        try:
            hb = float(rec.get("heartbeat_ts") or 0.0)
        except (TypeError, ValueError):
            hb = 0.0
        stale_note = ""
        if hb > 0 and (now - hb) > stale_sec:
            stale_note = "（心跳过期·进程可能已停）"
        elif hb > 0 and (now - hb) > stale_sec / 3:
            stale_note = "（心跳偏旧）"
        if t and s:
            bits.append(f"{t}（{s}）{stale_note}".strip())
        elif t:
            bits.append(f"{t}{stale_note}".strip())
    if not bits:
        return ""
    return (
        "【系统负载·外部定时守护（心跳文件）】"
        + "；".join(bits)
        + "。（`workspace/external_scheduled_hints.json`，由独立进程刷新；关闭读：`JACHIN_EXTERNAL_SCHED_HINTS_DISABLE=1`）"
    )


def register_foreground_task(
    *,
    run_id: str,
    channel: str,
    session_key: str = "",
    resource_tags: list[str] | None = None,
) -> None:
    rid = (run_id or "").strip()
    if not rid:
        return
    tags: list[str] = []
    if resource_tags:
        for t in resource_tags:
            if len(tags) >= 8:
                break
            s = str(t).strip()
            if s:
                tags.append(s[:64])
    with _lock:
        rec: dict[str, Any] = {
            "channel": (channel or "").strip() or "unknown",
            "session_key": (session_key or "").strip()[:96],
            "started_at": time.monotonic(),
        }
        if tags:
            rec["resource_tags"] = tags
        _foreground[rid] = rec
    logger.info(
        "[L3 Runtime] 前台任务开始 run_id=%s channel=%s session=%s tags=%s",
        rid[:16],
        rec["channel"],
        (rec["session_key"][:24] + "…") if len(rec["session_key"]) > 24 else rec["session_key"] or "-",
        tags or [],
    )


def unregister_foreground_task(run_id: str) -> None:
    rid = (run_id or "").strip()
    if not rid:
        return
    with _lock:
        rec = _foreground.pop(rid, None)
    if rec:
        try:
            elapsed = time.monotonic() - float(rec.get("started_at", time.monotonic()))
        except (TypeError, ValueError):
            elapsed = 0.0
        logger.info(
            "[L3 Runtime] 前台任务结束 run_id=%s channel=%s elapsed=%.1fs",
            rid[:16],
            str(rec.get("channel") or ""),
            max(0.0, elapsed),
        )


def register_scheduled_job_hint(
    *,
    job_id: str,
    title: str,
    schedule_summary: str,
    source: str = "",
) -> None:
    """
    由进程内 APScheduler 等在注册成功时调用，便于 format_combined_runtime_prompt_suffix 展示「有哪些定时任务」。
    外部独立守护见 `external_scheduled_hints.json`（**M**）。
    """
    jid = (job_id or "").strip()
    if not jid:
        return
    with _lock:
        _scheduled_hints[jid] = {
            "title": (title or "").strip() or jid,
            "schedule_summary": (schedule_summary or "").strip()[:240],
            "source": (source or "").strip()[:80],
            "registered_at": time.monotonic(),
        }


def unregister_scheduled_job_hint(job_id: str) -> None:
    jid = (job_id or "").strip()
    if not jid:
        return
    with _lock:
        _scheduled_hints.pop(jid, None)


def unregister_scheduled_job_hints_by_source(source: str) -> None:
    """调度器整体 shutdown 时按 source 批量移除（如 kalaroko_scheduler）。"""
    src = (source or "").strip()
    if not src:
        return
    with _lock:
        drop = [k for k, v in _scheduled_hints.items() if str(v.get("source") or "") == src]
        for k in drop:
            _scheduled_hints.pop(k, None)


def get_runtime_registry_snapshot_dict() -> dict[str, Any]:
    """进程内前台 run 登记 + 定时任务 hint + 外部心跳文案（供 HTTP 只读诊断）。线程安全。"""
    with _lock:
        now = time.monotonic()
        fg: list[dict[str, Any]] = []
        for rid, rec in list(_foreground.items())[:96]:
            try:
                elapsed = now - float(rec.get("started_at", now))
            except (TypeError, ValueError):
                elapsed = 0.0
            fg.append(
                {
                    "run_id": rid,
                    "channel": str(rec.get("channel") or ""),
                    "session_key": str(rec.get("session_key") or ""),
                    "resource_tags": list(rec.get("resource_tags") or []),
                    "elapsed_sec": round(max(0.0, elapsed), 2),
                }
            )
        sch: list[dict[str, Any]] = []
        for jid, rec in list(_scheduled_hints.items())[:64]:
            sch.append(
                {
                    "job_id": jid,
                    "title": str(rec.get("title") or ""),
                    "schedule_summary": str(rec.get("schedule_summary") or ""),
                    "source": str(rec.get("source") or ""),
                }
            )
        n_fg = len(_foreground)
        n_sch = len(_scheduled_hints)
    ext_line = (_format_external_scheduled_hints_line() or "").strip()
    return {
        "foreground_task_count": n_fg,
        "foreground_tasks": fg,
        "scheduled_job_hint_count": n_sch,
        "scheduled_job_hints": sch,
        "external_scheduled_prompt_line": ext_line or None,
    }


def format_combined_runtime_prompt_suffix() -> str:
    """拼成多行：前台路数 + 进程内定时登记 + 后台 P3 摘要（各段无则省略）。"""
    try:
        from l3_node.primitives.agent_tasks.background_task_service import format_background_tasks_prompt_suffix

        bg = (format_background_tasks_prompt_suffix() or "").strip()
    except Exception:
        bg = ""
    with _lock:
        n_fg = len(_foreground)
        chans: set[str] = set()
        for rec in _foreground.values():
            c = str(rec.get("channel") or "").strip()
            if c:
                chans.add(c)
        sched_snap = list(_scheduled_hints.items())
        n_sched = len(_scheduled_hints)
    fg = ""
    if n_fg > 0:
        ch_preview = "、".join(sorted(chans)[:5]) if chans else "mixed"
        if len(chans) > 5:
            ch_preview += "…"
        fg = (
            f"【系统负载·前台 run_agent】当前进程内登记约 **{n_fg}** 路顶层前台任务"
            f"（通道：{ch_preview}）。若用户问是否繁忙，可提及除本对话外可能还有其它会话在执行。"
        )
    sch_line = ""
    if n_sched > 0 and sched_snap:
        bits: list[str] = []
        for _jid, rec in sched_snap[:6]:
            t = str(rec.get("title") or "").strip()
            s = str(rec.get("schedule_summary") or "").strip()
            if t and s:
                bits.append(f"{t}（{s}）")
            elif t:
                bits.append(t)
        if bits:
            tail = f"…等共 **{n_sched}** 项" if n_sched > len(bits) else ""
            sch_line = (
                "【系统负载·进程内定时登记】"
                + "；".join(bits)
                + tail
                + "。（本 L3 进程内 APScheduler；外加独立守护见 external_scheduled_hints.json）"
            )
    ext_line = (_format_external_scheduled_hints_line() or "").strip()
    parts = [p for p in (fg, sch_line, ext_line, bg) if p]
    if not parts:
        return ""
    return "\n".join(parts)
