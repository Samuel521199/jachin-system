"""
BI 每日战报 — 定时调度器

支持可配置的 cron（每天固定时间 UTC+8）和 interval（每 N 分钟/小时）。
与 recruitment_scheduler 共享 APScheduler 实例，互不依赖业务逻辑。
设计规范: docs/bi_daily_report/03_SKILL_DESIGN.md
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_BI_JOB_ID = "bi_daily_report"

# 默认：每天 8:00 UTC+8
_DEFAULT_SCHEDULE = {
    "enabled": True,
    "mode": "cron",
    "hour": 8,
    "minute": 0,
    "timezone": "Asia/Shanghai",
}


def _load_schedule_config() -> dict[str, Any]:
    """从 bi_daily_report.yaml 加载 schedule 配置"""
    from l3_node.paths import get_app_root

    jachin_root = Path.home() / ".jachin"
    project_root = get_app_root()
    # 规范 075：优先 ~/.jachin/config/skills/，开发期回退项目 config/skills/
    candidates = [
        jachin_root / "config" / "skills" / "com.jachin.bi.daily_report" / "bi_daily_report.yaml",
        project_root / "config" / "skills" / "com.jachin.bi.daily_report" / "bi_daily_report.yaml",
    ]
    for path in candidates:
        if path.exists():
            try:
                import yaml

                with open(path, encoding="utf-8") as f:
                    raw = yaml.safe_load(f) or {}
                sched = raw.get("schedule") or {}
                if isinstance(sched, dict):
                    return {**_DEFAULT_SCHEDULE, **sched}
            except Exception as e:
                logger.warning("[BI Scheduler] 配置加载失败 %s: %s", path, e)
    return dict(_DEFAULT_SCHEDULE)


def _run_bi_daily_report_job() -> None:
    """定时任务回调：执行 BI 每日战报"""
    try:
        from l3_node.skills.bi.bi_daily_report.main_skill import run_bi_daily_report

        result = run_bi_daily_report()
        if result.get("success"):
            logger.info(
                "[BI Scheduler] 战报执行成功 report_sent=%s lark_ok=%s email_ok=%s",
                result.get("report_sent"),
                result.get("lark_ok"),
                result.get("email_ok"),
            )
        else:
            logger.warning(
                "[BI Scheduler] 战报执行失败 stage=%s error=%s",
                result.get("stage"),
                result.get("error"),
            )
    except Exception as e:
        logger.exception("[BI Scheduler] 战报任务异常: %s", e)


def _get_cron_timezone(tz_name: str):
    """解析时区字符串为 tzinfo。支持 Asia/Shanghai 或 UTC+8 等"""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(tz_name)
    except Exception:
        from datetime import timezone, timedelta

        # 兼容 UTC+8 等简单格式
        tz_lower = (tz_name or "").lower()
        if "utc+8" in tz_lower or "asia/shanghai" in tz_lower:
            return timezone(timedelta(hours=8))
        return timezone(timedelta(hours=8))  # 默认 UTC+8


def register_bi_daily_report_job() -> bool:
    """
    将 BI 每日战报注册到 APScheduler。
    根据 config/bi_daily_report.yaml 的 schedule 配置决定 cron 或 interval 模式。

    schedule 配置说明：
    - enabled: false 时跳过注册
    - mode: "cron" — 每天固定时间，需配置 hour、minute、timezone（默认 8:00 Asia/Shanghai）
    - mode: "interval" — 按间隔执行，需配置 minutes 或 hours

    Returns:
        True 表示注册成功，False 表示 APScheduler 不可用或注册失败
    """
    try:
        from l3_node.recruitment_scheduler import scheduler
    except ImportError:
        logger.debug("[BI Scheduler] recruitment_scheduler 未加载，跳过 BI 任务注册")
        return False

    if scheduler is None:
        logger.debug("[BI Scheduler] APScheduler 不可用，跳过 BI 任务注册")
        return False

    cfg = _load_schedule_config()
    if not cfg.get("enabled", True):
        logger.info("[BI Scheduler] schedule.enabled=false，跳过 BI 任务注册")
        return False

    mode = (cfg.get("mode") or "cron").lower()

    try:
        if mode == "interval":
            minutes = cfg.get("minutes")
            hours = cfg.get("hours")
            if hours is not None:
                interval_kw = {"hours": int(hours)}
                desc = f"每 {hours} 小时"
            elif minutes is not None:
                interval_kw = {"minutes": int(minutes)}
                desc = f"每 {minutes} 分钟"
            else:
                interval_kw = {"minutes": 60}
                desc = "每 60 分钟（默认）"
            scheduler.add_job(
                _run_bi_daily_report_job,
                "interval",
                id=_BI_JOB_ID,
                replace_existing=True,
                **interval_kw,
            )
            logger.info("[BI Scheduler] 已注册 BI 战报任务（interval %s）", desc)
        else:
            # cron 模式
            hour = int(cfg.get("hour", 8))
            minute = int(cfg.get("minute", 0))
            tz_name = cfg.get("timezone") or "Asia/Shanghai"
            tz = _get_cron_timezone(tz_name)
            scheduler.add_job(
                _run_bi_daily_report_job,
                "cron",
                hour=hour,
                minute=minute,
                timezone=tz,
                id=_BI_JOB_ID,
                replace_existing=True,
            )
            logger.info(
                "[BI Scheduler] 已注册 BI 战报任务（cron 每天 %02d:%02d %s）",
                hour,
                minute,
                tz_name,
            )
        return True
    except Exception as e:
        logger.warning("[BI Scheduler] 注册任务失败: %s", e)
        return False
