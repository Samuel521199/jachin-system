"""
Kalaroko E2E — 小时巡检 + 每日晨报（本地 08:15 后状态机补偿）+ 每周统帅 Persona 侧写（AsyncIOScheduler，L3 事件循环内）。

- 小时巡检前做 **TCP 探活**（默认 ``8.8.8.8:53``），无网则静默跳过、不发飞书。
- 晨报不再依赖单次 UTC cron：``~/.jachin/data/.last_daily_report.txt`` 记录已成功发送的本地日期，
  周期任务在 **本地已过 08:15** 且当日未记录时补发（休眠错过晨间窗口后唤醒可追上）。

调度「开/关」持久化到 ``kalaroko_scheduler_state.json``；L3 重启后 ``init_auto_start_scheduler()`` 可恢复。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from core.kalaroko_e2e_jsonl_store import (
    KALAROKO_E2E_JSONL_PATH,
    atomic_replace_path,
    kalaroko_e2e_jsonl_lock,
)
from l3_node.paths import kalaroko_default_e2e_script_path

logger = logging.getLogger(__name__)


def _sched_env_bool(name: str, *, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


SCHEDULER_STATE_FILE = Path.home() / ".jachin" / "data" / "kalaroko_scheduler_state.json"
_LAST_DAILY_REPORT_FILE = Path.home() / ".jachin" / "data" / ".last_daily_report.txt"
_KALAROKO_E2E_JSONL = KALAROKO_E2E_JSONL_PATH

_JOB_HOURLY = "kalaroko_hourly_inspection"
_JOB_DAILY = "kalaroko_daily_morning_report"
_JOB_WEEKLY_PERSONA = "weekly_persona_profile"

_scheduler: Any | None = None
_scheduler_started = False

# 晨报状态机：防 Interval 重叠触发导致的并发重入与重复投递（单进程内互斥）
_DAILY_REPORT_LOCK = asyncio.Lock()


def _write_scheduler_state(enabled: bool) -> None:
    try:
        SCHEDULER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        SCHEDULER_STATE_FILE.write_text(
            json.dumps({"enabled": enabled}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.debug("[kalaroko_scheduler] 状态已写入 enabled=%s", enabled)
    except Exception as e:
        logger.warning("[kalaroko_scheduler] 写入状态文件失败（忽略）: %s", e)


def init_auto_start_scheduler() -> None:
    """L3 HTTP on_startup：若上次用户开启了定时守护，进程重启后自动 ``start_scheduler()``。"""
    try:
        if not SCHEDULER_STATE_FILE.is_file():
            logger.debug("[kalaroko_scheduler] 无状态文件，跳过自启")
            return
        raw = SCHEDULER_STATE_FILE.read_text(encoding="utf-8").strip()
        data = json.loads(raw) if raw else {}
        if data.get("enabled") is True:
            start_scheduler()
            logger.info("[kalaroko_scheduler] 已从状态文件恢复定时守护（enabled=True）")
        else:
            logger.debug("[kalaroko_scheduler] 状态 enabled=%s，不自启", data.get("enabled"))
    except Exception as e:
        logger.warning("[kalaroko_scheduler] init_auto_start_scheduler 失败（忽略）: %s", e)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _daily_catchup_interval_minutes() -> int:
    """晨报状态机轮询间隔（分钟）；环境变量 ``KALAROKO_DAILY_REPORT_CATCHUP_MINUTES``，默认 10，范围 1～60。"""
    try:
        v = int(os.environ.get("KALAROKO_DAILY_REPORT_CATCHUP_MINUTES", "10") or "10")
        return max(1, min(60, v))
    except ValueError:
        return 10


def _daily_report_local_tz() -> ZoneInfo:
    """晨报日期边界与本地时钟所用时区（默认菲律宾办公时区）；放行时刻见 ``smart_trigger_daily_report_job``（08:15 后）。"""
    raw = (
        os.environ.get("KALAROKO_DAILY_REPORT_TZ")
        or os.environ.get("KALAROKO_REPORT_TZ")
        or "Asia/Manila"
    ).strip()
    try:
        return ZoneInfo(raw)
    except Exception:
        return ZoneInfo("Asia/Manila")


def is_network_connected(
    host: str = "8.8.8.8",
    port: int = 53,
    timeout: float = 3.0,
) -> bool:
    """检查是否已连通互联网（防休眠唤醒后栈未就绪 / 无网误报巡检）。"""
    prev = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        try:
            socket.setdefaulttimeout(prev)
        except Exception:
            pass


async def wait_for_network_or_skip() -> bool:
    """异步包装探活；无网返回 False（调用方静默跳过），不抛异常。"""
    return await asyncio.to_thread(is_network_connected)


async def _send_lark_safe(markdown: str, title: str) -> None:
    try:
        from l3_node.channels.lark.kalaroko_inspection_notify import (
            inspection_lark_open_api_ready,
            send_lark_alert_card_and_thread,
        )
    except Exception as e:
        logger.warning("[kalaroko_scheduler] Lark 模块加载失败: %s", e)
        return

    if not inspection_lark_open_api_ready():
        logger.warning(
            "[kalaroko_scheduler] 未配置飞书 Open API（FEISHU_APP_SECRET 等），跳过推送"
        )
        return

    try:
        await send_lark_alert_card_and_thread(title=title, markdown=markdown)
    except Exception as e:
        logger.warning("[kalaroko_scheduler] Lark Open API 推送失败: %s", e)


async def hourly_inspection_job() -> None:
    """每小时：4 轮 ×30s 间隔；异常则 Lark 严重预警。"""
    from l3_node.scheduled_global_registry import scheduled_global_task_scope_async

    async with scheduled_global_task_scope_async(
        "kalaroko_scheduler",
        _JOB_HOURLY,
        title="Kalaroko 小时巡检",
    ):
        await _hourly_inspection_job_body()


async def _hourly_inspection_job_body() -> None:
    if not await wait_for_network_or_skip():
        logger.info(
            "[kalaroko_scheduler] [网络防抖] 当前无网或探活失败，本次小时巡检静默跳过（不发飞书）。"
        )
        return
    logger.info(
        "[kalaroko_scheduler] 小时巡检开始（4 轮×30s，超时上限 2700s）"
    )
    root = _repo_root()
    import importlib.util
    import sys

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    try:
        script = kalaroko_default_e2e_script_path()
        if not script.is_file():
            raise RuntimeError(
                f"缺少 test_kalaroko_default_scenarios_e2e.py: {script} "
                "（打包侧车请确认 build_l3_sidecar 已 --add-data 该脚本）"
            )
        spec = importlib.util.spec_from_file_location("_kalaroko_sched_hourly", script)
        if spec is None or spec.loader is None:
            raise RuntimeError("无法加载 test_kalaroko_default_scenarios_e2e.py")
        sys.modules.pop(spec.name, None)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        run_fn = getattr(mod, "run_kalaroko_batch_test", None)
        if run_fn is None:
            raise RuntimeError("脚本缺少 run_kalaroko_batch_test")

        def _sched_e2e_line_sink(line: str) -> None:
            """与 CLI/SSE 同源：``run_kalaroko_batch_test`` 的进度行写入 L3 日志（含 ``[E2E progress]``）。"""
            try:
                logger.info("%s", (line or "").rstrip("\n"))
            except Exception:
                pass

        async def _run_batch() -> None:
            await run_fn(
                4,
                30,
                skip_playwright=False,
                line_sink=_sched_e2e_line_sink,
            )

        await asyncio.wait_for(_run_batch(), timeout=2700.0)
        logger.info("[kalaroko_scheduler] 小时巡检正常结束（本小时批次已完成）")
        # Healthchecks 真实心跳：仅在 E2E 内 send_kalaroko_inspection_to_lark 完整成功后触发（无独立周期线程）
    except asyncio.TimeoutError:
        logger.error(
            "[kalaroko_scheduler] 小时巡检超过 asyncio 上限 2700s（45 分钟），已终止；"
            "尝试中止脚本并释放 Playwright/CDP 会话"
        )
        try:
            from l3_node.kalaroko_e2e_control import stop_manual_run

            stop_manual_run()
        except Exception as se:
            logger.warning("[kalaroko_scheduler] stop_manual_run: %s", se)
        try:
            from l3_client.local_mcps.kalaroko_monitor.mcp_kalaroko_monitor import (
                emergency_kalaroko_playwright_cleanup,
            )

            await emergency_kalaroko_playwright_cleanup()
        except Exception as ce:
            logger.warning("[kalaroko_scheduler] emergency_kalaroko_playwright_cleanup: %s", ce)
        md = (
            "🚨 **[严重超时] Kalaroko 巡检任务挂起超过 2700s（45 分钟），已被调度器终止；"
            "已尝试释放 Playwright。**\n\n"
            "_请检查 CDP Chrome、网络或串行锁上是否仍有未结束的手动巡检。_"
        )
        try:
            await _send_lark_safe(md, "巡检 · 严重超时")
        except Exception as push_e:
            logger.warning("[kalaroko_scheduler] 超时告警推送失败（已吞）: %s", push_e)
        return
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception("[kalaroko_scheduler] 小时巡检崩溃: %s", e)
        md = (
            "🚨 **[严重预警] Kalaroko 小时级巡检崩溃**\n\n"
            f"错误信息: `{e!s}`"
        )
        try:
            await _send_lark_safe(md, "巡检 · 严重告警")
        except Exception as push_e:
            logger.warning("[kalaroko_scheduler] 告警推送失败（已吞）: %s", push_e)


def _parse_captured_at_iso(ts: Any) -> datetime | None:
    if not ts or not isinstance(ts, str):
        return None
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _prune_kalaroko_e2e_jsonl_retention_days(retention_days: int = 7) -> dict[str, Any]:
    """丢弃 ``captured_at`` 早于 ``retention_days`` 的行；先写临时文件再 ``os.replace`` 原子替换。"""
    p = _KALAROKO_E2E_JSONL
    if not p.is_file():
        return {"ok": True, "skipped": True, "reason": "文件不存在"}

    with kalaroko_e2e_jsonl_lock():
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, retention_days))
        removed = 0
        kept_lines: list[str] = []

        try:
            raw_text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return {"ok": False, "error": repr(e)}

        for line in raw_text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                removed += 1
                continue
            dt = _parse_captured_at_iso(rec.get("captured_at"))
            if dt is None:
                kept_lines.append(json.dumps(rec, ensure_ascii=False))
                continue
            if dt >= cutoff:
                kept_lines.append(json.dumps(rec, ensure_ascii=False))
            else:
                removed += 1

        tmp = p.with_suffix(".jsonl.tmp")
        try:
            tmp.write_text(
                "\n".join(kept_lines) + ("\n" if kept_lines else ""),
                encoding="utf-8",
            )
            atomic_replace_path(tmp, p)
        except OSError as e:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            return {"ok": False, "error": repr(e)}

        logger.info(
            "[kalaroko_scheduler] JSONL 滚动清理完成 retained=%s removed_est=%s path=%s",
            len(kept_lines),
            removed,
            p,
        )
        return {"ok": True, "retained_lines": len(kept_lines), "removed_rows": removed}


async def weekly_persona_profile_job() -> None:
    """每周 UTC 周日 02:00：从 General_Chat 提炼统帅 Persona → Core_Profile。"""
    from l3_node.scheduled_global_registry import scheduled_global_task_scope_async

    async with scheduled_global_task_scope_async(
        "kalaroko_scheduler",
        _JOB_WEEKLY_PERSONA,
        title="周 Persona 侧写",
    ):
        await _weekly_persona_profile_job_body()


async def _weekly_persona_profile_job_body() -> None:
    try:
        from l3_node.jobs.persona_profiler import generate_weekly_persona_profile

        out = await generate_weekly_persona_profile()
        if out.get("ok"):
            if out.get("skipped"):
                logger.info("[kalaroko_scheduler] 周侧写跳过: %s", out)
            else:
                logger.info("[kalaroko_scheduler] 周侧写完成: %s", out)
            return
        err = out.get("error") or "unknown"
        md = (
            "🚨 **[Persona 侧写失败] weekly Persona 任务未成功**\n\n"
            f"阶段: `{out.get('stage', '?')}`  \n错误: `{err}`"
        )
        try:
            await _send_lark_safe(md, "Persona · 周侧写告警")
        except Exception as push_e:
            logger.warning("[kalaroko_scheduler] Persona 告警推送失败: %s", push_e)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        tb = traceback.format_exc()
        logger.exception("[kalaroko_scheduler] 周侧写任务致命失败")
        md = (
            "🚨 **[严重预警] 统帅 Persona 周侧写任务崩溃**\n\n"
            f"异常: `{type(e).__name__}` — `{e!s}`\n\n"
            f"```\n{tb[:14000]}\n```"
        )
        try:
            await _send_lark_safe(md[:24000], "Persona · 严重告警")
        except Exception as push_e:
            logger.warning("[kalaroko_scheduler] Persona 宕机告警推送失败: %s", push_e)


async def daily_morning_report_job() -> bool:
    """24h 晨报 → Lark；成功后滚动清理 JSONL。返回 True 表示已向 Lark 投递晨报正文（可写日期状态机）。

    无时区「整点 cron」依赖：由 ``smart_trigger_daily_report_job`` 在本地 08:15 后补偿触发。
    """
    root = _repo_root()
    import importlib.util
    import sys

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    try:
        script = kalaroko_default_e2e_script_path()
        if not script.is_file():
            raise RuntimeError(
                f"缺少 test_kalaroko_default_scenarios_e2e.py: {script} "
                "（打包侧车请确认 build_l3_sidecar 已 --add-data 该脚本）"
            )
        spec = importlib.util.spec_from_file_location("_kalaroko_sched_daily", script)
        if spec is None or spec.loader is None:
            raise RuntimeError("无法加载 test_kalaroko_default_scenarios_e2e.py")
        sys.modules.pop(spec.name, None)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        extract_cmp = getattr(mod, "_extract_comparison_metrics", None)
        gen_summary = getattr(mod, "_generate_llm_summary", None)
        if extract_cmp is None or gen_summary is None:
            raise RuntimeError("脚本缺少 _extract_comparison_metrics 或 _generate_llm_summary")

        jsonl_path = _KALAROKO_E2E_JSONL
        now_utc = datetime.now(timezone.utc)
        cutoff_time = now_utc - timedelta(hours=24)

        recent_24h_records: list[dict[str, Any]] = []
        if jsonl_path.is_file():
            try:
                with kalaroko_e2e_jsonl_lock():
                    with open(jsonl_path, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                record = json.loads(line)
                                cap_time = _parse_captured_at_iso(
                                    record.get("captured_at")
                                )
                                if cap_time is None:
                                    continue
                                if cap_time < cutoff_time:
                                    continue
                                if cap_time > now_utc + timedelta(minutes=10):
                                    continue
                                recent_24h_records.append(record)
                            except Exception as parse_err:
                                logger.warning(
                                    "[kalaroko_scheduler] 晨报解析单行日志失败: %s",
                                    parse_err,
                                )
                                continue
            except OSError as oe:
                raise RuntimeError(f"无法读取晨报 JSONL: {jsonl_path}") from oe

        recent_24h_records.sort(
            key=lambda r: _parse_captured_at_iso(r.get("captured_at"))
            or datetime.min.replace(tzinfo=timezone.utc),
        )

        if not recent_24h_records:
            logger.info("[kalaroko_scheduler] 过去 24 小时无巡检数据，跳过晨报生成。")
            return False

        metrics_for_llm = [extract_cmp(rec) for rec in recent_24h_records]
        text = await gen_summary(metrics_for_llm, mode="multi_round")
        header = (
            "🌅 **Kalaroko 24小时 E2E 运行晨报**\n\n"
            f"_生成时间 (L3): {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_\n\n"
        )
        await _send_lark_safe(header + text, "Kalaroko · 24h 晨报")

        briefing_body = header + text
        try:

            def _commit_daily_briefing() -> str:
                from l3_client.local_mcps.jachin_memory_nexus.memory_backend import (
                    commit_drawer,
                )

                return commit_drawer(
                    text=briefing_body,
                    wing="E2E_Monitors",
                    room="Kalaroko_Daily_Briefings",
                    extra_meta={
                        "type": "daily_report",
                        "source": "kalaroko_scheduler",
                    },
                )

            drawer_id = await asyncio.to_thread(_commit_daily_briefing)
            logger.info(
                "[kalaroko_scheduler] 晨报已归档 Memory Nexus "
                "(wing=E2E_Monitors room=Kalaroko_Daily_Briefings drawer_id=%s)",
                drawer_id,
            )
        except Exception as mem_e:
            logger.warning(
                "[kalaroko_scheduler] 晨报归档 Memory Nexus 失败（不影响 JSONL 修剪）: %s",
                mem_e,
            )

        try:
            pr = await asyncio.to_thread(_prune_kalaroko_e2e_jsonl_retention_days, 7)
            logger.info("[kalaroko_scheduler] 晨报后 JSONL 清理: %s", pr)
        except Exception as pe:
            logger.warning("[kalaroko_scheduler] JSONL 清理失败（不影响晨报成功）: %s", pe)

        return True

    except asyncio.CancelledError:
        raise
    except BaseException as e:
        tb = traceback.format_exc()
        logger.exception("[kalaroko_scheduler] 晨报任务致命失败")
        md = (
            "🚨 **[严重预警] Kalaroko 每日晨报生成失败**\n\n"
            f"异常类型: `{type(e).__name__}`  \n摘要: `{e!s}`\n\n"
            "```\n"
            f"{tb[:14000]}\n"
            "```"
        )
        try:
            await _send_lark_safe(md[:24000], "晨报 · 严重告警")
        except Exception as push_e:
            logger.warning("[kalaroko_scheduler] 晨报宕机告警推送失败: %s", push_e)
        return False


async def smart_trigger_daily_report_job() -> None:
    """本地 08:15 后：若今日尚未在状态机落盘成功发晨报，且网络可用，则执行 ``daily_morning_report_job`` 并写日期。

    用于办公机休眠错过原 UTC 定点后的 **catch-up**；无网静默等待下周期，不抛异常、不为此单独发飞书。
    """
    from l3_node.scheduled_global_registry import scheduled_global_task_scope_async

    async with scheduled_global_task_scope_async(
        "kalaroko_scheduler",
        _JOB_DAILY,
        title="Kalaroko 晨报补偿",
    ):
        await _smart_trigger_daily_report_job_body()


async def _smart_trigger_daily_report_job_body() -> None:
    if _DAILY_REPORT_LOCK.locked():
        logger.info(
            "[kalaroko_scheduler] [日报调度] 前序晨报任务正在执行，本次轮询跳过（防重入）。"
        )
        return

    try:
        tz = _daily_report_local_tz()
        now_local = datetime.now(tz)
        is_time_to_fire = now_local.hour > 8 or (
            now_local.hour == 8 and now_local.minute >= 15
        )
        if not is_time_to_fire:
            return

        today_str = now_local.date().isoformat()
        state_path = _LAST_DAILY_REPORT_FILE
        last_run = ""
        if state_path.is_file():
            try:
                last_run = state_path.read_text(encoding="utf-8").strip()
            except OSError:
                last_run = ""

        if last_run == today_str:
            return

        if not await wait_for_network_or_skip():
            logger.info(
                "[kalaroko_scheduler] [日报调度] 本地已过 08:15 且今日尚未发晨报，但当前无网，下周期重试。"
            )
            return

        async with _DAILY_REPORT_LOCK:
            last_after = ""
            if state_path.is_file():
                try:
                    last_after = state_path.read_text(encoding="utf-8").strip()
                except OSError:
                    last_after = ""
            if last_after == today_str:
                logger.info(
                    "[kalaroko_scheduler] [日报调度] 持锁双检：%s 已为 %r，跳过重复发送。",
                    state_path,
                    today_str,
                )
                return

            logger.info(
                "[kalaroko_scheduler] [日报调度] 已持锁，触发晨报发送逻辑（目标日=%s，预检 last=%r）。",
                today_str,
                last_run,
            )
            try:
                sent = await daily_morning_report_job()
                if not sent:
                    logger.info(
                        "[kalaroko_scheduler] [日报调度] 晨报任务返回 False（可能无 24h 数据），"
                        "状态机未更新，等待下轮重试。"
                    )
                    return
                try:
                    state_path.parent.mkdir(parents=True, exist_ok=True)
                    state_path.write_text(today_str, encoding="utf-8")
                except OSError as oe:
                    logger.warning(
                        "[kalaroko_scheduler] [日报调度] 晨报已投递但状态机落盘失败（存在重复发送风险）: %s",
                        oe,
                    )
                else:
                    logger.info(
                        "[kalaroko_scheduler] [日报调度] 晨报发送成功；状态机落盘完成 path=%s "
                        "date=%r encoding=utf-8 payload_len=%s",
                        state_path,
                        today_str,
                        len(today_str.encode("utf-8")),
                    )
            except Exception as e:
                logger.warning(
                    "[kalaroko_scheduler] [日报调度] 晨报链路异常，状态机未更新: %s",
                    e,
                )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(
            "[kalaroko_scheduler] [日报调度] smart_trigger 外层异常（不更新状态）: %s",
            e,
        )


def start_scheduler() -> dict[str, Any]:
    """注册并启动 AsyncIOScheduler（幂等：已在跑则跳过）。"""
    global _scheduler, _scheduler_started

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    if _scheduler_started and _scheduler is not None:
        return {"ok": True, "active": True, "message": "已在运行"}

    sched = AsyncIOScheduler()
    # max_instances=1 + coalesce=True：上一小时任务未结束时绝不并行；积压合并为一次
    # IntervalTrigger 默认「首次」在整段间隔之后（即启动后需等 1h）；显式 next_run_time 使首跑尽快开始，之后仍按每小时。
    _now_utc = datetime.now(timezone.utc)
    sched.add_job(
        hourly_inspection_job,
        IntervalTrigger(hours=1),
        id=_JOB_HOURLY,
        next_run_time=_now_utc,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
    )
    # 晨报：周期轮询 + 本地 08:15 后状态机（``_LAST_DAILY_REPORT_FILE``），休眠错过定点仍可补发；关闭：KALAROKO_DAILY_MORNING_REPORT=0
    if _sched_env_bool("KALAROKO_DAILY_MORNING_REPORT", default=True):
        _catch_m = _daily_catchup_interval_minutes()
        sched.add_job(
            smart_trigger_daily_report_job,
            IntervalTrigger(minutes=_catch_m),
            id=_JOB_DAILY,
            next_run_time=_now_utc,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )
    else:
        logger.info(
            "[kalaroko_scheduler] 已跳过每日晨报任务注册（KALAROKO_DAILY_MORNING_REPORT=0）"
        )
    sched.add_job(
        weekly_persona_profile_job,
        CronTrigger(day_of_week="sun", hour=2, minute=0, timezone=timezone.utc),
        id=_JOB_WEEKLY_PERSONA,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
    )
    sched.start()
    _scheduler = sched
    _scheduler_started = True
    _write_scheduler_state(True)
    logger.info(
        "[kalaroko_scheduler] AsyncIOScheduler 已启动（小时巡检 + 晨报状态机补偿（可关）+ 每周日 UTC 02:00 Persona）"
    )
    try:
        from l3_node.task_runtime_registry import register_scheduled_job_hint

        register_scheduled_job_hint(
            job_id=_JOB_HOURLY,
            title="Kalaroko 小时巡检",
            schedule_summary="约每 1 小时",
            source="kalaroko_scheduler",
        )
        if _sched_env_bool("KALAROKO_DAILY_MORNING_REPORT", default=True):
            _reg_m = _daily_catchup_interval_minutes()
            register_scheduled_job_hint(
                job_id=_JOB_DAILY,
                title="Kalaroko 晨报状态机",
                schedule_summary=f"每 {_reg_m} 分钟轮询（本地 08:15 后补发）",
                source="kalaroko_scheduler",
            )
        register_scheduled_job_hint(
            job_id=_JOB_WEEKLY_PERSONA,
            title="统帅 Persona 侧写",
            schedule_summary="每周日 UTC 02:00",
            source="kalaroko_scheduler",
        )
    except Exception:
        pass
    try:
        jh = sched.get_job(_JOB_HOURLY)
        jd = sched.get_job(_JOB_DAILY)
        if jh and getattr(jh, "next_run_time", None):
            logger.info(
                "[kalaroko_scheduler] 下次小时巡检计划时间: %s (UTC)",
                jh.next_run_time,
            )
        if jd and getattr(jd, "next_run_time", None):
            logger.info(
                "[kalaroko_scheduler] 下次晨报状态机轮询: %s (UTC)",
                jd.next_run_time,
            )
    except Exception as e:
        logger.debug("[kalaroko_scheduler] 打印计划时间失败（忽略）: %s", e)
    return {"ok": True, "active": True, "message": "已启动"}


def stop_scheduler() -> dict[str, Any]:
    """关闭调度器。"""
    global _scheduler, _scheduler_started

    if _scheduler is None:
        _scheduler_started = False
        _write_scheduler_state(False)
        return {"ok": True, "active": False, "message": "未运行"}

    try:
        _scheduler.shutdown(wait=False)
    except Exception as e:
        logger.warning("[kalaroko_scheduler] shutdown: %s", e)
    finally:
        _scheduler = None
        _scheduler_started = False
        _write_scheduler_state(False)
        try:
            from l3_node.task_runtime_registry import unregister_scheduled_job_hints_by_source

            unregister_scheduled_job_hints_by_source("kalaroko_scheduler")
        except Exception:
            pass
    return {"ok": True, "active": False, "message": "已停止"}


def scheduler_status() -> dict[str, Any]:
    return {"active": bool(_scheduler_started and _scheduler is not None)}
