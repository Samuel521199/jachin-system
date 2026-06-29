"""
PMO 多维表变更监控调度器

mode=webhook（默认）：
  - 飞书 Lark 长连接 / Webhook 推送 drive.file.bitable_record_changed_v1
  - 本调度器仅每 debounce_check_seconds 检查 idle 是否到期 → finalize（不拉全表）

mode=poll（兜底）：
  - 每 poll_interval_seconds 全表 diff（旧行为，无 Lark 事件时用）

L3 HTTP on_startup → init_pmo_bitable_watch_auto_start()
长连接：scripts/run_pmo_bitable_watch_long_connection.py
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_CHANNEL = "pmo_bitable_watch_scheduler"
_JOB_DEBOUNCE = "pmo_bitable_watch_debounce"
_JOB_POLL = "pmo_bitable_watch_poll"

_scheduler: Any | None = None
_scheduler_started = False


def _watch_mode() -> str:
    try:
        from l3_node.tools.pmo_bitable_watch import _load_watch_config

        return str(_load_watch_config().get("mode") or "webhook").strip().lower()
    except Exception:
        return "webhook"


def _use_poll_tick() -> bool:
    """poll / hybrid 会拉表 diff；webhook 仅 debounce 检查。"""
    return _watch_mode() in ("poll", "hybrid")


def _is_disabled() -> bool:
    raw = (os.environ.get("PMO_BITABLE_WATCH_ENABLED") or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return True
    if raw in ("1", "true", "yes", "on"):
        return False
    try:
        from l3_node.tools.pmo_bitable_watch import _load_watch_config

        return not bool(_load_watch_config().get("enabled", False))
    except Exception:
        return False


def _interval_seconds() -> int:
    from l3_node.tools.pmo_bitable_watch import _load_watch_config

    cfg = _load_watch_config()
    if _use_poll_tick():
        return max(5, int(cfg.get("poll_interval_seconds") or 15))
    return max(5, int(cfg.get("debounce_check_seconds") or 10))


def _job_tick_body() -> None:
    try:
        mode = _watch_mode()
        if _use_poll_tick():
            from l3_node.tools.pmo_bitable_watch import run_bitable_watch_tick

            out = run_bitable_watch_tick()
        else:
            from l3_node.tools.pmo_bitable_watch import run_bitable_watch_debounce_tick

            out = run_bitable_watch_debounce_tick()

        action = out.get("action") or ""
        if action in (
            "session_finalized_notify",
            "session_active",
            "waiting_debounce",
            "fetch_failed",
            "baseline_initialized",
        ):
            logger.info(
                "[pmo_bitable_watch_scheduler] mode=%s tick action=%s status=%s msg=%s",
                mode,
                action,
                out.get("status"),
                out.get("message") or out.get("error"),
            )
        if action == "session_finalized_notify" and out.get("local_paths"):
            logger.info("[pmo_bitable_watch_scheduler] 本机落盘 %s", out.get("local_paths"))
    except Exception as e:
        logger.warning("[pmo_bitable_watch_scheduler] tick 异常: %s", e)


def _job_tick() -> None:
    """在后台线程执行 tick，避免 finalize+LLM 阻塞下一轮 poll/debounce。"""
    import threading

    threading.Thread(target=_job_tick_body, daemon=True, name="pmo_bitable_watch_tick").start()


def start_pmo_bitable_watch_scheduler() -> dict[str, Any]:
    """启动 APScheduler（幂等）。"""
    global _scheduler, _scheduler_started

    if _scheduler_started and _scheduler is not None:
        return {"ok": True, "active": True, "message": "已在运行"}

    if _is_disabled():
        logger.info("[pmo_bitable_watch_scheduler] 已禁用")
        return {"ok": True, "active": False, "message": "已禁用"}

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.interval import IntervalTrigger
    except ImportError as e:
        logger.warning("[pmo_bitable_watch_scheduler] 缺少 APScheduler: %s", e)
        return {"ok": False, "active": False, "error": str(e)}

    mode = _watch_mode()
    interval = _interval_seconds()
    job_id = _JOB_POLL if mode == "poll" else _JOB_DEBOUNCE

    sched = BackgroundScheduler(timezone="Asia/Shanghai")
    sched.add_job(
        _job_tick,
        IntervalTrigger(seconds=interval),
        id=job_id,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=interval * 2,
    )
    sched.start()
    _scheduler = sched
    _scheduler_started = True

    try:
        _job_tick()
    except Exception as e:
        logger.warning("[pmo_bitable_watch_scheduler] 启动首轮 tick 失败: %s", e)

    logger.info(
        "[pmo_bitable_watch_scheduler] 已启动 mode=%s 每 %ds（channel=%s）",
        mode,
        interval,
        _CHANNEL,
    )
    return {"ok": True, "active": True, "mode": mode, "interval_seconds": interval}


def init_pmo_bitable_watch_auto_start() -> None:
    """L3 HTTP on_startup 调用。"""
    try:
        start_pmo_bitable_watch_scheduler()
    except Exception as e:
        logger.warning("[pmo_bitable_watch_scheduler] auto-start 失败（已忽略）: %s", e)


def stop_pmo_bitable_watch_scheduler() -> dict[str, Any]:
    global _scheduler, _scheduler_started
    if _scheduler is None:
        _scheduler_started = False
        return {"ok": True, "active": False, "message": "未运行"}
    try:
        _scheduler.shutdown(wait=False)
    except Exception as e:
        logger.warning("[pmo_bitable_watch_scheduler] shutdown: %s", e)
    finally:
        _scheduler = None
        _scheduler_started = False
    return {"ok": True, "active": False, "message": "已停止"}


def run_pmo_bitable_watch_once(*, force_finalize: bool = False) -> dict[str, Any]:
    """手动触发一次 tick（poll / hybrid 拉表 diff；webhook 仅 debounce）。"""
    if _use_poll_tick():
        from l3_node.tools.pmo_bitable_watch import run_bitable_watch_tick

        return run_bitable_watch_tick(force_finalize=force_finalize)
    from l3_node.tools.pmo_bitable_watch import run_bitable_watch_debounce_tick

    return run_bitable_watch_debounce_tick(force_finalize=force_finalize)


def run_pmo_bitable_watch_daemon(*, block: bool = True) -> dict[str, Any]:
    """独立守护：仅 debounce/poll 检查器（不含 Lark 长连接）。"""
    import time

    started = start_pmo_bitable_watch_scheduler()
    if not started.get("active"):
        return started
    if not block:
        return started
    logger.info("[pmo_bitable_watch_scheduler] debounce 守护运行中（Ctrl+C 退出）…")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop_pmo_bitable_watch_scheduler()
        return {"ok": True, "active": False, "message": "用户中断，已停止"}
