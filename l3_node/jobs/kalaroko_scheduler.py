"""
Kalaroko E2E — 小时巡检 + 每日晨报 + 每周统帅 Persona 侧写（AsyncIOScheduler，L3 事件循环内）。

调度「开/关」持久化到 ``kalaroko_scheduler_state.json``；L3 重启后 ``init_auto_start_scheduler()`` 可恢复。
"""

from __future__ import annotations

import asyncio
import json
import logging
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.kalaroko_e2e_jsonl_store import (
    KALAROKO_E2E_JSONL_PATH,
    atomic_replace_path,
    kalaroko_e2e_jsonl_lock,
)
from l3_node.paths import kalaroko_default_e2e_script_path

logger = logging.getLogger(__name__)

SCHEDULER_STATE_FILE = Path.home() / ".jachin" / "data" / "kalaroko_scheduler_state.json"
_KALAROKO_E2E_JSONL = KALAROKO_E2E_JSONL_PATH

_JOB_HOURLY = "kalaroko_hourly_inspection"
_JOB_DAILY = "kalaroko_daily_morning_report"
_JOB_WEEKLY_PERSONA = "weekly_persona_profile"

_scheduler: Any | None = None
_scheduler_started = False


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


def _chunk_md(text: str, max_chars: int = 2400) -> list[str]:
    s = (text or "").strip()
    if not s:
        return []
    out: list[str] = []
    rest = s
    while rest:
        if len(rest) <= max_chars:
            out.append(rest)
            break
        window = rest[:max_chars]
        cut = window.rfind("\n\n")
        if cut < max_chars // 4:
            cut = window.rfind("\n")
        if cut < max_chars // 4:
            cut = max_chars
        chunk = rest[:cut].strip()
        if chunk:
            out.append(chunk)
        rest = rest[cut:].strip()
    return out


async def _send_lark_safe(markdown: str, title: str) -> None:
    from l3_node.channels.lark.webhook import send_markdown

    url = ""
    try:
        from l3_node.channels.lark.kalaroko_inspection_notify import (
            inspection_lark_webhook_url,
        )

        url = inspection_lark_webhook_url() or ""
    except Exception:
        url = ""

    if not url:
        logger.warning("[kalaroko_scheduler] 未配置 Lark Webhook，跳过推送")
        return

    parts = _chunk_md(markdown)
    total = len(parts) or 1
    for i, part in enumerate(parts or [markdown]):
        sub = title if total == 1 else f"{title} ({i + 1}/{total})"

        def _sync() -> dict[str, Any]:
            return send_markdown(webhook_url=url, markdown_content=part, title=sub)

        try:
            await asyncio.to_thread(_sync)
        except Exception as e:
            logger.warning("[kalaroko_scheduler] Lark 单条发送失败: %s", e)
        await asyncio.sleep(0.35)


async def hourly_inspection_job() -> None:
    """每小时：4 轮 ×30s 间隔；异常则 Lark 严重预警。"""
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

        async def _run_batch() -> None:
            await run_fn(
                4,
                30,
                skip_playwright=False,
                line_sink=None,
            )

        await asyncio.wait_for(_run_batch(), timeout=2700.0)
        logger.info("[kalaroko_scheduler] 小时巡检正常结束（本小时批次已完成）")
    except asyncio.TimeoutError:
        logger.error("[kalaroko_scheduler] 小时巡检超过 45 分钟，已终止")
        md = (
            "🚨 **[严重超时] Kalaroko 巡检任务挂起超过 45 分钟，已被调度器强制猎杀销毁！**\n\n"
            "_单次任务上限 2700s；请检查 CDP Chrome、Playwright 或网络是否阻塞。_"
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


async def daily_morning_report_job() -> None:
    """每日（UTC 0:00 = 北京 8:00）：24h 晨报 → Lark；成功后滚动清理 JSONL。"""
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
            return

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
    # 北京 08:00 = 当日 00:00 UTC（不依赖 IANA 时区库，避免部分 Windows 环境缺 Asia/Shanghai）
    sched.add_job(
        daily_morning_report_job,
        CronTrigger(hour=0, minute=0, timezone=timezone.utc),
        id=_JOB_DAILY,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
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
        "[kalaroko_scheduler] AsyncIOScheduler 已启动（小时巡检 + 每日 UTC0:00 晨报 + 每周日 UTC 02:00 Persona）"
    )
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
                "[kalaroko_scheduler] 下次每日晨报计划时间: %s (UTC)",
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
    return {"ok": True, "active": False, "message": "已停止"}


def scheduler_status() -> dict[str, Any]:
    return {"active": bool(_scheduler_started and _scheduler is not None)}

