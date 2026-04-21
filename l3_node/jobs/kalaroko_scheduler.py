"""
Kalaroko E2E — 小时巡检 + 每日晨报（AsyncIOScheduler，L3 事件循环内）。

_toggle 状态仅内存：L3 重启后默认未启动；前端可再次开启。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_JOB_HOURLY = "kalaroko_hourly_inspection"
_JOB_DAILY = "kalaroko_daily_morning_report"

_scheduler: Any | None = None
_scheduler_started = False


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
    root = _repo_root()
    import importlib.util
    import sys

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    try:
        script = root / "scripts" / "test_kalaroko_default_scenarios_e2e.py"
        spec = importlib.util.spec_from_file_location("_kalaroko_sched_hourly", script)
        if spec is None or spec.loader is None:
            raise RuntimeError("无法加载 test_kalaroko_default_scenarios_e2e.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        run_fn = getattr(mod, "run_kalaroko_batch_test", None)
        if run_fn is None:
            raise RuntimeError("脚本缺少 run_kalaroko_batch_test")
        await run_fn(
            4,
            30,
            skip_playwright=False,
            line_sink=None,
        )
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


async def daily_morning_report_job() -> None:
    """每日 08:00：聚合 24h JSONL → LLM → Lark 晨报。"""
    root = _repo_root()
    import importlib.util
    import sys

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    try:
        script = root / "scripts" / "test_kalaroko_default_scenarios_e2e.py"
        spec = importlib.util.spec_from_file_location("_kalaroko_sched_daily", script)
        if spec is None or spec.loader is None:
            raise RuntimeError("无法加载 test_kalaroko_default_scenarios_e2e.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        gen = getattr(mod, "generate_llm_daily_report_from_jsonl", None)
        if gen is None:
            raise RuntimeError("脚本缺少 generate_llm_daily_report_from_jsonl")
        text = await gen(None, hours=24.0)
        header = (
            "🌅 **Kalaroko 24小时 E2E 运行晨报**\n\n"
            f"_生成时间 (L3): {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}_\n\n"
        )
        await _send_lark_safe(header + text, "Kalaroko · 24h 晨报")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.exception("[kalaroko_scheduler] 晨报任务失败: %s", e)
        md = (
            "🚨 **[晨报任务异常] Kalaroko 24h 晨报**\n\n"
            f"错误信息: `{e!s}`"
        )
        try:
            await _send_lark_safe(md, "晨报 · 异常")
        except Exception:
            pass


def start_scheduler() -> dict[str, Any]:
    """注册并启动 AsyncIOScheduler（幂等：已在跑则跳过）。"""
    global _scheduler, _scheduler_started

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    if _scheduler_started and _scheduler is not None:
        return {"ok": True, "active": True, "message": "已在运行"}

    sched = AsyncIOScheduler()
    sched.add_job(
        hourly_inspection_job,
        IntervalTrigger(hours=1),
        id=_JOB_HOURLY,
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
    sched.start()
    _scheduler = sched
    _scheduler_started = True
    logger.info("[kalaroko_scheduler] AsyncIOScheduler 已启动（小时巡检 + 每日08:00晨报）")
    return {"ok": True, "active": True, "message": "已启动"}


def stop_scheduler() -> dict[str, Any]:
    """关闭调度器。"""
    global _scheduler, _scheduler_started

    if _scheduler is None:
        _scheduler_started = False
        return {"ok": True, "active": False, "message": "未运行"}

    try:
        _scheduler.shutdown(wait=False)
    except Exception as e:
        logger.warning("[kalaroko_scheduler] shutdown: %s", e)
    finally:
        _scheduler = None
        _scheduler_started = False
    return {"ok": True, "active": False, "message": "已停止"}


def scheduler_status() -> dict[str, Any]:
    return {"active": bool(_scheduler_started and _scheduler is not None)}

