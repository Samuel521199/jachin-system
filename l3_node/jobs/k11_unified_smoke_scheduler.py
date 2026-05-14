"""
K11 统合平台冒烟 — 按北京时间调度（APScheduler + CronTrigger）。

- **到点每触发 1 轮子进程**（**不**用状态里的 ``runs``/``interval_sec``；二项**仅**供控制台「手动脉冲 / 本页统合 SSE」用）。
- **每日一次**：北京 ``时:分`` 各触发 1 轮。
- **每小时定点**（``hourly_recurring=true``）：自每个整点小时对齐「分」触发 1 轮；``时`` 仍落盘，切回「每日一次」时再用。

状态持久化 ``k11_unified_smoke_scheduler_state.json``；L3 重启后 ``init_k11_unified_smoke_auto_start()`` 可恢复。开关/重排时向 ``k11_scheduled_log_emit`` 写行，供桌面 MIND STREAM 与 ``/schedule/log-stream`` 可见。

与 kalaroko_scheduler 独立，避免与巡检中枢调度混用。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from l3_node.k11_subprocess_cli import build_k11_l3_subprocess_cmd
from l3_node.paths import get_app_root, k11_unified_smoke_script_path

logger = logging.getLogger(__name__)

SCHEDULER_STATE_FILE = Path.home() / ".jachin" / "data" / "k11_unified_smoke_scheduler_state.json"
_JOB_DAILY = "k11_unified_smoke_daily"

_scheduler: Any | None = None
_scheduler_started = False

# 供 cron_thinker 等非 asyncio 线程：`k11_scheduled_log_emit_from_thread` 投递到本循环
_k11_schedule_sse_loop: asyncio.AbstractEventLoop | None = None


def register_k11_schedule_log_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    """在 aiohttp on_startup 中注册主事件循环，便于生物钟等线程内推送 MIND STREAM / schedule SSE。"""
    global _k11_schedule_sse_loop
    _k11_schedule_sse_loop = loop

DEFAULT_HOUR_BEIJING = 9
DEFAULT_MINUTE_BEIJING = 0
DEFAULT_RUNS = 4
DEFAULT_INTERVAL_SEC = 30

TZ_BEIJING = ZoneInfo("Asia/Shanghai")

# 定时批跑 → 前端 SSE 与 Mind Stream 广播（同进程、事件循环内）
_MAX_RING = 5000
_schedule_event_ring: deque[dict[str, Any]] = deque(maxlen=_MAX_RING)
_schedule_subscribers: set[asyncio.Queue] = set()


def k11_scheduled_log_ring_snapshot() -> list[dict[str, Any]]:
    return list(_schedule_event_ring)


def subscribe_k11_scheduled_log() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=5000)
    _schedule_subscribers.add(q)
    return q


def unsubscribe_k11_scheduled_log(q: asyncio.Queue) -> None:
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


async def k11_scheduled_log_emit(obj: dict[str, Any]) -> None:
    """将定时任务行/元事件推送给所有已连接的 /schedule/log-stream SSE，并落环形缓冲供新连接重放。"""
    _schedule_event_ring.append(obj)
    _fanout_schedule_log_to_queues(obj)


def k11_scheduled_log_emit_from_thread(obj: dict[str, Any]) -> None:
    """
    任意线程安全写入（如 APScheduler、cron_thinker）：先入环，再 threadsafe 投递到订阅队列。
    须已由 ``register_k11_schedule_log_loop`` 绑定 L3 HTTP 主循环；未注册时仅环形缓冲、新连 SSE 可回放部分历史。
    """
    _schedule_event_ring.append(obj)
    loop = _k11_schedule_sse_loop
    if loop is None or not loop.is_running():
        return
    try:
        loop.call_soon_threadsafe(_fanout_schedule_log_to_queues, obj)
    except Exception:
        pass


def _try_emit_schedule_log(obj: dict[str, Any]) -> None:
    """从 sync 的调度启停/重排 中推一条到 SSE 环形缓冲；无运行中事件循环则跳过。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    try:
        loop.create_task(k11_scheduled_log_emit(obj))
    except Exception:
        pass


def _line_schedule_pattern(st: dict[str, Any]) -> str:
    h = max(0, min(23, int(st.get("hour_beijing", DEFAULT_HOUR_BEIJING))))
    m = max(0, min(59, int(st.get("minute_beijing", DEFAULT_MINUTE_BEIJING))))
    if bool(st.get("hourly_recurring")):
        return f"每小时 北京 *:{m:02d} 各触发 1 轮"
    return f"每日 北京 {h:02d}:{m:02d} 到点 1 轮"


def _read_state() -> dict[str, Any]:
    if not SCHEDULER_STATE_FILE.is_file():
        return {}
    try:
        return json.loads(SCHEDULER_STATE_FILE.read_text(encoding="utf-8").strip() or "{}")
    except (json.JSONDecodeError, OSError) as e:
        logger.debug("[k11_unified_smoke_scheduler] 读状态失败: %s", e)
        return {}


def _write_state(data: dict[str, Any]) -> None:
    try:
        SCHEDULER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        SCHEDULER_STATE_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("[k11_unified_smoke_scheduler] 写状态失败: %s", e)


async def k11_daily_unified_smoke_job() -> None:
    """
    由 Cron 触发时执行：每触发 1 轮子进程（不读 state 的 runs/interval，二者仅用于手动脉冲/本页 SSE）。

    子进程 stdout 广播到 /schedule/log-stream，供 MIND STREAM 显示。
    """
    st = _read_state()
    if st.get("enabled") is not True:
        return
    # 到点与「执行轮数 / 轮次间隔」手动脉冲解耦：定时侧固定单次执行
    runs = 1
    interval_sec = 0
    root = get_app_root().resolve()
    script = k11_unified_smoke_script_path()
    if not script.is_file():
        logger.error("[k11_unified_smoke_scheduler] 缺少统合冒烟脚本: %s", script)
        await k11_scheduled_log_emit(
            {"type": "error", "message": f"缺少脚本: {script}"}
        )
        return

    env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "JACHIN_APP_ROOT": str(root),
    }
    passthrough: list[str] = ["-v"]
    if str(os.environ.get("K11_SCHEDULED_SMOKE_NO_LARK", "")).lower() in ("1", "true", "yes", "on"):
        passthrough.append("--no-lark-report")
    # frozen 下须 ``l3_node.exe --jachin-k11-unified-smoke-subprocess ...``，禁止 ``exe scripts/....py``（会拖垮主进程/临时目录）
    cmd_base = build_k11_l3_subprocess_cmd("--jachin-k11-unified-smoke-subprocess", passthrough)
    try:
        from l3_node.k11_smoke_debug_log import k11_smoke_debug_init_once, k11_smoke_debug_line

        k11_smoke_debug_init_once()
        k11_smoke_debug_line(
            "CRON 到点统合冒烟 | cmd=%r | cwd=%s | script=%s | pattern=%s",
            cmd_base,
            str(root),
            str(script),
            _line_schedule_pattern(st),
        )
    except Exception:
        pass

    logger.info(
        "[k11_unified_smoke_scheduler] 定时批跑开始(每触发 %d 轮, 间 %ds): pattern=%s, script=%s",
        runs,
        interval_sec,
        _line_schedule_pattern(st),
        script.name,
    )
    await k11_scheduled_log_emit(
        {
            "type": "scheduled_start",
            "ts": time.time(),
            "runs": runs,
            "interval_sec": interval_sec,
            "script": script.name,
        }
    )

    all_ok = True
    for i in range(1, runs + 1):
        proc = await asyncio.create_subprocess_exec(
            *cmd_base,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(root),
            env=env,
        )
        await k11_scheduled_log_emit(
            {
                "line": f"══ 定时第 {i}/{runs} 轮 子进程已启动 (pid 见系统任务管理器) ══",
            }
        )
        if proc.stdout is not None:
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                text = raw.decode("utf-8", errors="replace").rstrip("\n\r")
                if text:
                    await k11_scheduled_log_emit({"line": text})
        code = int(await proc.wait())
        if code != 0:
            all_ok = False
        try:
            from l3_node.k11_smoke_debug_log import k11_smoke_debug_line

            k11_smoke_debug_line("定时子进程第 %d/%d 轮 exit_code=%s", i, runs, code)
        except Exception:
            pass
        await k11_scheduled_log_emit(
            {
                "type": "scheduled_progress",
                "round": i,
                "total": runs,
                "exit_code": code,
            }
        )
        logger.info(
            "[k11_unified_smoke_scheduler] 第 %d/%d 轮结束 exit=%s",
            i,
            runs,
            code,
        )
        if i < runs and interval_sec > 0:
            await k11_scheduled_log_emit(
                {"line": f"> 第 {i} 轮完成 (exit {code})，{interval_sec} 秒后开始下一…"}
            )
            await asyncio.sleep(float(interval_sec))
        elif i < runs:
            await k11_scheduled_log_emit(
                {"line": f"> 第 {i} 轮完成 (exit {code})，立即开始下一…"}
            )

    await k11_scheduled_log_emit(
        {
            "type": "scheduled_done",
            "ok": all_ok,
            "runs": runs,
        }
    )
    logger.info("[k11_unified_smoke_scheduler] 定时批跑全部结束")


def _cron_trigger_for_state(st: dict[str, Any]) -> Any:
    """根据状态生成 CronTrigger：每日一次或每小时（对齐「分」）。"""
    from apscheduler.triggers.cron import CronTrigger

    hour_bj = max(0, min(23, int(st.get("hour_beijing", DEFAULT_HOUR_BEIJING))))
    minute_bj = max(0, min(59, int(st.get("minute_beijing", DEFAULT_MINUTE_BEIJING))))
    hourly = bool(st.get("hourly_recurring"))
    if hourly:
        return CronTrigger(hour="*", minute=minute_bj, timezone=TZ_BEIJING)
    return CronTrigger(hour=hour_bj, minute=minute_bj, timezone=TZ_BEIJING)


def start_scheduler(*, from_persistent: bool = False) -> dict[str, Any]:
    global _scheduler, _scheduler_started

    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    if _scheduler_started and _scheduler is not None:
        _reschedule_daily_if_needed()
        st = _read_state()
        st["enabled"] = True
        _write_state(st)
        _try_emit_schedule_log(
            {
                "line": f'[定时] 调度已在运行，已按当前配置重排：{ _line_schedule_pattern(st) }（到点每触发 1 轮，与「执行轮数/间隔」手动脉冲无关）。',
            }
        )
        return {"ok": True, "active": True, "message": "已在运行（已按当前配置重排）"}

    st = _read_state()
    hour_bj = max(0, min(23, int(st.get("hour_beijing", DEFAULT_HOUR_BEIJING))))
    minute_bj = max(0, min(59, int(st.get("minute_beijing", DEFAULT_MINUTE_BEIJING))))
    hourly = bool(st.get("hourly_recurring"))
    trigger = _cron_trigger_for_state(st)

    sched = AsyncIOScheduler()
    sched.add_job(
        k11_daily_unified_smoke_job,
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
    if hourly:
        logger.info(
            "[k11_unified_smoke_scheduler] 已启动：每小时北京时间 *:%02d 到点 1 轮 K11 统合冒烟（保存的时=%02d 供每日模式）",
            minute_bj,
            hour_bj,
        )
    else:
        logger.info(
            "[k11_unified_smoke_scheduler] 已启动：每日北京时间 %02d:%02d 到点 1 轮 K11 统合冒烟",
            hour_bj,
            minute_bj,
        )
    if from_persistent:
        _try_emit_schedule_log(
            {
                "line": f"[定时] L3 已从本机状态恢复并启用到点批跑：{_line_schedule_pattern(st)}（到点每触发 1 轮，与「执行轮数/间隔」手动脉冲无关）。",
            }
        )
    else:
        _try_emit_schedule_log(
            {
                "line": f'[定时] 已启用到点批跑：{ _line_schedule_pattern(st) }。输出见本页 MIND STREAM 定时流；到点每触发 1 轮。',
            }
        )
    return {"ok": True, "active": True, "message": "已启动"}


def _reschedule_daily_if_needed() -> None:
    """在运行中若用户改了时刻或每小时开关，重建 Cron。"""
    global _scheduler, _scheduler_started
    if _scheduler is None or not _scheduler_started:
        return
    st = _read_state()
    hour_bj = max(0, min(23, int(st.get("hour_beijing", DEFAULT_HOUR_BEIJING))))
    minute_bj = max(0, min(59, int(st.get("minute_beijing", DEFAULT_MINUTE_BEIJING))))
    hourly = bool(st.get("hourly_recurring"))
    trigger = _cron_trigger_for_state(st)

    try:
        _scheduler.reschedule_job(
            _JOB_DAILY,
            trigger=trigger,
        )
        if hourly:
            logger.info(
                "[k11_unified_smoke_scheduler] 已重排：每小时 *:%02d（北京）到点 1 轮",
                minute_bj,
            )
        else:
            logger.info(
                "[k11_unified_smoke_scheduler] 已重排每日任务：北京 %02d:%02d 到点 1 轮",
                hour_bj,
                minute_bj,
            )
    except Exception as e:
        logger.warning("[k11_unified_smoke_scheduler] reschedule 失败: %s", e)


def stop_scheduler() -> dict[str, Any]:
    global _scheduler, _scheduler_started

    if _scheduler is None:
        _scheduler_started = False
        st = _read_state()
        st["enabled"] = False
        _write_state(st)
        _try_emit_schedule_log(
            {
                "line": "[定时] 已关闭到点批跑（调度器未在运行，仅同步状态）。",
            }
        )
        return {"ok": True, "active": False, "message": "未运行"}

    try:
        _scheduler.shutdown(wait=False)
    except Exception as e:
        logger.warning("[k11_unified_smoke_scheduler] shutdown: %s", e)
    finally:
        _scheduler = None
        _scheduler_started = False
    st = _read_state()
    st["enabled"] = False
    _write_state(st)
    _try_emit_schedule_log(
        {
            "line": "[定时] 已停止到点批跑。",
        }
    )
    return {"ok": True, "active": False, "message": "已停止"}


def scheduler_status() -> dict[str, Any]:
    st = _read_state()
    return {
        "active": bool(_scheduler_started and _scheduler is not None),
        "hour_beijing": int(st.get("hour_beijing", DEFAULT_HOUR_BEIJING)),
        "minute_beijing": int(st.get("minute_beijing", DEFAULT_MINUTE_BEIJING)),
        "runs": int(st.get("runs", DEFAULT_RUNS)),
        "interval_sec": int(st.get("interval_sec", DEFAULT_INTERVAL_SEC)),
        "hourly_recurring": bool(st.get("hourly_recurring")),
    }


def apply_k11_unified_smoke_schedule(
    *,
    enabled: bool,
    hour_beijing: int | None = None,
    minute_beijing: int | None = None,
    runs: int | None = None,
    interval_sec: int | None = None,
    hourly_recurring: bool | None = None,
) -> dict[str, Any]:
    """
    合并写入状态并启停调度；供 HTTP toggle 使用。
    """
    st = _read_state()
    if hour_beijing is not None:
        st["hour_beijing"] = max(0, min(23, int(hour_beijing)))
    if minute_beijing is not None:
        st["minute_beijing"] = max(0, min(59, int(minute_beijing)))
    if runs is not None:
        st["runs"] = max(1, min(99, int(runs)))
    if interval_sec is not None:
        st["interval_sec"] = max(0, min(3600, int(interval_sec)))
    if hourly_recurring is not None:
        st["hourly_recurring"] = bool(hourly_recurring)
    st["enabled"] = bool(enabled)
    _write_state(st)
    if enabled:
        return start_scheduler()
    return stop_scheduler()


def init_k11_unified_smoke_auto_start() -> None:
    """L3 on_startup：若上次用户开启了定时，恢复调度。"""
    try:
        if not SCHEDULER_STATE_FILE.is_file():
            return
        raw = SCHEDULER_STATE_FILE.read_text(encoding="utf-8").strip()
        data = json.loads(raw) if raw else {}
        if data.get("enabled") is True:
            start_scheduler(from_persistent=True)
            logger.info("[k11_unified_smoke_scheduler] 已从状态恢复定时（enabled=True）")
    except Exception as e:
        logger.warning("[k11_unified_smoke_scheduler] init 失败: %s", e)
