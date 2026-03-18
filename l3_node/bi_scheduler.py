"""
BI 每日战报 — 定时调度器

每天 8:00 执行 run_bi_daily_report。
与 recruitment_scheduler 共享 APScheduler 实例，互不依赖业务逻辑。
设计规范: docs/bi_daily_report/03_SKILL_DESIGN.md
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_BI_JOB_ID = "bi_daily_report"


def _run_bi_daily_report_job() -> None:
    """定时任务回调：执行 BI 每日战报"""
    try:
        from l3_node.skills.bi_daily_report.main_skill import run_bi_daily_report
        result = run_bi_daily_report()
        if result.get("success"):
            logger.info("[BI Scheduler] 战报执行成功 report_sent=%s lark_ok=%s email_ok=%s",
                        result.get("report_sent"), result.get("lark_ok"), result.get("email_ok"))
        else:
            logger.warning("[BI Scheduler] 战报执行失败 stage=%s error=%s",
                            result.get("stage"), result.get("error"))
    except Exception as e:
        logger.exception("[BI Scheduler] 战报任务异常: %s", e)


def register_bi_daily_report_job() -> bool:
    """
    将 BI 每日战报注册到 APScheduler。
    每天 8:00 执行。

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

    try:
        scheduler.add_job(
            _run_bi_daily_report_job,
            "cron",
            hour=8,
            minute=0,
            id=_BI_JOB_ID,
            replace_existing=True,
        )
        logger.info("[BI Scheduler] 已注册每日 8:00 BI 战报任务")
        return True
    except Exception as e:
        logger.warning("[BI Scheduler] 注册任务失败: %s", e)
        return False
