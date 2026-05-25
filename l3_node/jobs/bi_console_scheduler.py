"""
BI 每日战报 — L3 控制台定时调度（APScheduler + CronTrigger，北京时间）。

与 ``l3_node/primitives/skills/bi/scheduler.py``（YAML 配置）独立：
控制台「保存定时 / 开关」读写 ``bi_console_scheduler_state.json``，到点调用
``run_bi_daily_report_scheduled()``（Windows 弹出新控制台跑 ``scripts/run_bi_daily_report.py``）。

状态：``~/.jachin/data/bi_console_scheduler_state.json``；L3 重启后 ``init_bi_console_auto_start()`` 恢复。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

SCHEDULER_STATE_FILE = Path.home() / ".jachin" / "data" / "bi_console_scheduler_state.json"
_JOB_DAILY = "bi_console_daily_report"

_scheduler: Any | None = None
_scheduler_started = False
_bi_schedule_sse_loop: asyncio.AbstractEventLoop | None = None

DEFAULT_HOUR_BEIJING = 8
DEFAULT_MINUTE_BEIJING = 0

TZ_BEIJING = ZoneInfo("Asia/Shanghai")

_MAX_RING = 5000
_schedule_event_ring: deque[dict[str, Any]] = deque(maxlen=_MAX_RING)
_schedule_subscribers: set[asyncio.Queue] = set()


def register_bi_console_schedule_log_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    global _bi_schedule_sse_loop
    _bi_schedule_sse_loop = loop


def bi_scheduled_log_ring_snapshot() -> list[dict[str, Any]]:
    return list(_schedule_event_ring)


def subscribe_bi_scheduled_log() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=5000)
    _schedule_subscribers.add(q)
    return q


def unsubscribe_bi_scheduled_log(q: asyncio.Queue) -> None:
    _schedule_subscribers.discard(q)


def _fanout_schedule_log_to_queues(obj: dict[str, Any]) -> None:
    for q in list(_schedule_subscribers):
        try:
            q.put_nowait(obj)
        except asyncio.QueueFull:
            try:
                _ = q.get_nowait()
            except Exception:
                pass
            try:
                q.put_nowait(obj)
            except Exception:
                pass
        except Exception:
            pass


async def bi_scheduled_log_emit(obj: dict[str, Any]) -> None:
    _schedule_event_ring.append(obj)
    _fanout_schedule_log_to_queues(obj)


def bi_scheduled_log_emit_from_thread(obj: dict[str, Any]) -> None:
    _schedule_event_ring.append(obj)
    loop = _bi_schedule_sse_loop
    if loop is None or not loop.is_running():
        return
    try:
        loop.call_soon_threadsafe(_fanout_schedule_log_to_queues, obj)
    except Exception:
        pass


def _try_emit_schedule_log(obj: dict[str, Any]) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    try:
        loop.create_task(bi_scheduled_log_emit(obj))
    except Exception:
        pass


def _line_schedule_pattern(st: dict[str, Any]) -> str:
    h = max(0, min(23, int(st.get("hour_beijing", DEFAULT_HOUR_BEIJING))))
    m = max(0, min(59, int(st.get("minute_beijing", DEFAULT_MINUTE_BEIJING))))
    if bool(st.get("hourly_recurring")):
        return f"每小时 北京 *:{m:02d} 各触发 1 次"
    return f"每日 北京 {h:02d}:{m:02d} 到点 1 次"


def _read_state() -> dict[str, Any]:
    if not SCHEDULER_STATE_FILE.is_file():
        return {}
    try:
        return json.loads(SCHEDULER_STATE_FILE.read_text(encoding="utf-8").strip() or "{}")
    except (json.JSONDecodeError, OSError) as e:
        logger.debug("[bi_console_scheduler] 读状态失败: %s", e)
        return {}


def _write_state(data: dict[str, Any]) -> None:
    try:
        SCHEDULER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        SCHEDULER_STATE_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("[bi_console_scheduler] 写状态失败: %s", e)


async def bi_console_daily_report_job() -> None:
    """Cron 到点：执行 BI 战报（等价 scripts/run_bi_daily_report.py）。"""
    st = _read_state()
    if st.get("enabled") is not True:
        return

    await bi_scheduled_log_emit(
        {
            "type": "scheduled_start",
            "ts": time.time(),
            "pattern": _line_schedule_pattern(st),
        }
    )
    logger.info(
        "[bi_console_scheduler] 定时触发 BI 战报: %s",
        _line_schedule_pattern(st),
    )

    try:
        from l3_node.primitives.skills.bi.bi_daily_report.main_skill import (
            run_bi_daily_report_scheduled,
        )

        result = await asyncio.to_thread(run_bi_daily_report_scheduled)
        ok = bool(result.get("success"))
        await bi_scheduled_log_emit(
            {
                "type": "scheduled_done",
                "ok": ok,
                "stage": result.get("stage"),
                "error": str(result.get("error", ""))[:500],
            }
        )
        if ok:
            logger.info("[bi_console_scheduler] 定时 BI 战报完成 stage=%s", result.get("stage"))
        else:
            logger.warning(
                "[bi_console_scheduler] 定时 BI 战报失败: %s",
                result.get("error"),
            )
    except Exception as e:
        logger.exception("[bi_console_scheduler] 定时任务异常: %s", e)
        await bi_scheduled_log_emit({"type": "error", "message": str(e)})


def _cron_trigger_for_state(st: dict[str, Any]) -> Any:
    from apscheduler.triggers.cron import CronTrigger

    hour_bj = max(0, min(23, int(st.get("hour_beijing", DEFAULT_HOUR_BEIJING))))
    minute_bj = max(0, min(59, int(st.get("minute_beijing", DEFAULT_MINUTE_BEIJING))))
    if bool(st.get("hourly_recurring")):
        return CronTrigger(hour="*", minute=minute_bj, timezone=TZ_BEIJING)
    return CronTrigger(hour=hour_bj, minute=minute_bj, timezone=TZ_BEIJING)


def start_scheduler(*, from_persistent: bool = False) -> dict[str, Any]:
    global _scheduler, _scheduler_started

    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    if _scheduler_started and _scheduler is not None:
        _reschedule_if_needed()
        st = _read_state()
        st["enabled"] = True
        _write_state(st)
        _try_emit_schedule_log(
            {
                "line": f"[定时] 调度已在运行，已按当前配置重排：{_line_schedule_pattern(st)}。",
            }
        )
        return {"ok": True, "active": True, "message": "已在运行（已按当前配置重排）"}

    st = _read_state()
    trigger = _cron_trigger_for_state(st)

    sched = AsyncIOScheduler()
    sched.add_job(
        bi_console_daily_report_job,
        trigger,
        id=_JOB_DAILY,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    sched.start()
    _scheduler = sched
    _scheduler_started = True
    st = {**st, "enabled": True}
    _write_state(st)

    pattern = _line_schedule_pattern(st)
    logger.info("[bi_console_scheduler] 已启动：%s", pattern)
    if from_persistent:
        _try_emit_schedule_log({"line": f"[定时] L3 已恢复 BI 战报到点任务：{pattern}。"})
    else:
        _try_emit_schedule_log({"line": f"[定时] 已启用 BI 战报到点任务：{pattern}。"})
    return {"ok": True, "active": True, "message": "已启动"}


def _reschedule_if_needed() -> None:
    global _scheduler
    if _scheduler is None or not _scheduler_started:
        return
    st = _read_state()
    trigger = _cron_trigger_for_state(st)
    try:
        _scheduler.reschedule_job(_JOB_DAILY, trigger=trigger)
        logger.info("[bi_console_scheduler] 已重排：%s", _line_schedule_pattern(st))
    except Exception as e:
        logger.warning("[bi_console_scheduler] reschedule 失败: %s", e)


def stop_scheduler() -> dict[str, Any]:
    global _scheduler, _scheduler_started

    if _scheduler is None:
        _scheduler_started = False
        st = _read_state()
        st["enabled"] = False
        _write_state(st)
        _try_emit_schedule_log({"line": "[定时] 已关闭 BI 战报到点任务（调度器未在运行）。"})
        return {"ok": True, "active": False, "message": "未运行"}

    try:
        _scheduler.shutdown(wait=False)
    except Exception as e:
        logger.warning("[bi_console_scheduler] shutdown: %s", e)
    finally:
        _scheduler = None
        _scheduler_started = False
    st = _read_state()
    st["enabled"] = False
    _write_state(st)
    _try_emit_schedule_log({"line": "[定时] 已停止 BI 战报到点任务。"})
    return {"ok": True, "active": False, "message": "已停止"}


def scheduler_status() -> dict[str, Any]:
    st = _read_state()
    return {
        "active": bool(_scheduler_started and _scheduler is not None),
        "hour_beijing": int(st.get("hour_beijing", DEFAULT_HOUR_BEIJING)),
        "minute_beijing": int(st.get("minute_beijing", DEFAULT_MINUTE_BEIJING)),
        "hourly_recurring": bool(st.get("hourly_recurring")),
    }


def apply_bi_console_schedule(
    *,
    enabled: bool,
    hour_beijing: int | None = None,
    minute_beijing: int | None = None,
    hourly_recurring: bool | None = None,
) -> dict[str, Any]:
    st = _read_state()
    if hour_beijing is not None:
        st["hour_beijing"] = max(0, min(23, int(hour_beijing)))
    if minute_beijing is not None:
        st["minute_beijing"] = max(0, min(59, int(minute_beijing)))
    if hourly_recurring is not None:
        st["hourly_recurring"] = bool(hourly_recurring)
    st["enabled"] = bool(enabled)
    _write_state(st)
    if enabled:
        r = start_scheduler()
        _reschedule_if_needed()
        return r
    return stop_scheduler()


def init_bi_console_auto_start() -> None:
    try:
        if not SCHEDULER_STATE_FILE.is_file():
            return
        raw = SCHEDULER_STATE_FILE.read_text(encoding="utf-8").strip()
        data = json.loads(raw) if raw else {}
        if data.get("enabled") is True:
            start_scheduler(from_persistent=True)
            logger.info("[bi_console_scheduler] 已从状态恢复定时（enabled=True）")
    except Exception as e:
        logger.warning("[bi_console_scheduler] init 失败: %s", e)
