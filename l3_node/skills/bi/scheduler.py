"""
BI 每日战报 — 定时调度器

支持可配置的 cron（每天固定时间 UTC+8）和 interval（每 N 分钟/小时）。
与 recruitment_scheduler 共享 APScheduler 实例，互不依赖业务逻辑。
设计规范: docs/bi_daily_report/03_SKILL_DESIGN.md
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _scheduler_audit_log(message: str) -> None:
    """
    固定写入 ~/.jachin/client_volumes/bi_data/logs/bi_scheduler_audit.log
    用于排查「定时未触发 / 未注册」：是否走了 http_server 注册、读到哪份 yaml、enabled、cron 表达式等。
    """
    try:
        p = Path.home() / ".jachin" / "client_volumes" / "bi_data" / "logs" / "bi_scheduler_audit.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {message}\n"
        with open(p, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _bi_daily_report_yaml_candidates() -> list[Path]:
    from l3_node.paths import get_app_root

    jachin_root = Path.home() / ".jachin"
    project_root = get_app_root()
    return [
        jachin_root / "config" / "skills" / "com.jachin.bi.daily_report" / "bi_daily_report.yaml",
        project_root / "config" / "skills" / "com.jachin.bi.daily_report" / "bi_daily_report.yaml",
    ]

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
    # #region agent log
    _dbg = lambda msg, d: _dbg_write("scheduler._load_schedule_config", msg, d)
    def _dbg_write(loc, msg, d):
        try:
            import json, time
            from pathlib import Path
            p = Path(__file__).resolve().parents[3] / "debug-ead14b.log"
            line = json.dumps({"sessionId":"ead14b","location":loc,"message":msg,"data":d,"timestamp":int(time.time()*1000),"hypothesisId":"H2"}, ensure_ascii=False) + "\n"
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass
    # #endregion

    candidates = _bi_daily_report_yaml_candidates()
    for path in candidates:
        if path.exists():
            try:
                import yaml
                with open(path, encoding="utf-8") as f:
                    raw = yaml.safe_load(f) or {}
                sched = raw.get("schedule") or {}
                if isinstance(sched, dict):
                    out = {**_DEFAULT_SCHEDULE, **sched}
                    # 定时开关：schedule_enabled（顶层）与 schedule.enabled 任一为 false 即关闭
                    top_ok = raw.get("schedule_enabled", True) if "schedule_enabled" in raw else True
                    sched_ok = sched.get("enabled", True)
                    out["enabled"] = bool(top_ok and sched_ok)
                    # #region agent log
                    _dbg("config_loaded", {"path": str(path), "mode": out.get("mode"), "run_at": f"{out.get('run_at_hour')}:{out.get('run_at_minute')}", "enabled": out.get("enabled")})
                    # #endregion
                    return out
            except Exception as e:
                logger.warning("[BI Scheduler] 配置加载失败 %s: %s", path, e)
    # #region agent log
    _dbg("config_default", {"reason": "no_file"})
    # #endregion
    return dict(_DEFAULT_SCHEDULE)


def _run_bi_daily_report_job() -> None:
    """定时任务回调：Windows 弹控制台跑 scripts/run_bi_daily_report.py，等同手动执行"""
    _scheduler_audit_log("JOB_FIRE bi_daily_report APScheduler callback entered")
    try:
        from l3_node.skills.bi.bi_daily_report.main_skill import run_bi_daily_report_scheduled

        result = run_bi_daily_report_scheduled()
        _scheduler_audit_log(
            f"JOB_DONE success={result.get('success')} stage={result.get('stage')} err={str(result.get('error', ''))[:200]}"
        )
        if result.get("success"):
            logger.info(
                "[BI Scheduler] 战报执行成功 stage=%s report_sent=%s lark_ok=%s email_ok=%s",
                result.get("stage"),
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
        _scheduler_audit_log(f"JOB_EXCEPTION {type(e).__name__}: {e}")
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
    _scheduler_audit_log("register_bi_daily_report_job() called")
    yaml_hit = next((p for p in _bi_daily_report_yaml_candidates() if p.exists()), None)
    _scheduler_audit_log(f"bi_daily_report yaml first_hit={yaml_hit}")

    try:
        from l3_node.hr_loader import get_recruitment_scheduler

        rs_mod = get_recruitment_scheduler()
    except Exception as e:
        _scheduler_audit_log(f"SKIP get_recruitment_scheduler failed: {e}")
        # #region agent log
        try:
            import json, time
            from pathlib import Path
            p = Path(__file__).resolve().parents[3] / "debug-ead14b.log"
            line = json.dumps({"sessionId":"ead14b","location":"scheduler.register","message":"import_failed","data":{"error": str(e)},"timestamp":int(time.time()*1000),"hypothesisId":"H5"}, ensure_ascii=False) + "\n"
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass
        # #endregion
        logger.debug("[BI Scheduler] hr_loader 加载 recruitment_scheduler 失败，跳过 BI 任务注册")
        return False

    if rs_mod is None:
        _scheduler_audit_log(
            "SKIP HR 包未找到 — 需 com.jachin.hr.recruitment 在 ~/.jachin/l3_mcp_cache 或仓库 skills_repo/plugin"
        )
        logger.debug("[BI Scheduler] recruitment_scheduler 模块不可用，跳过 BI 任务注册")
        return False

    scheduler = getattr(rs_mod, "scheduler", None)

    if scheduler is None:
        # #region agent log
        try:
            import json, time
            from pathlib import Path
            p = Path(__file__).resolve().parents[3] / "debug-ead14b.log"
            line = json.dumps({"sessionId":"ead14b","location":"scheduler.register","message":"scheduler_none","data":{},"timestamp":int(time.time()*1000),"hypothesisId":"H5"}, ensure_ascii=False) + "\n"
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass
        # #endregion
        logger.debug("[BI Scheduler] APScheduler 不可用，跳过 BI 任务注册")
        _scheduler_audit_log("SKIP scheduler is None — L3 是否未加载 HR/recruitment_scheduler？BI 定时依赖同一 APScheduler 实例")
        return False

    cfg = _load_schedule_config()
    _scheduler_audit_log(
        f"schedule effective: enabled={cfg.get('enabled')} mode={cfg.get('mode')} "
        f"hour={cfg.get('hour')} minute={cfg.get('minute')} tz={cfg.get('timezone')}"
    )
    if not cfg.get("enabled", True):
        logger.info("[BI Scheduler] schedule.enabled=false，跳过 BI 任务注册")
        _scheduler_audit_log("SKIP schedule disabled (schedule_enabled or schedule.enabled false)")
        return False

    mode = (cfg.get("mode") or "cron").lower()

    try:
        # #region agent log
        try:
            import json, time
            from pathlib import Path
            p = Path(__file__).resolve().parents[3] / "debug-ead14b.log"
            line = json.dumps({"sessionId":"ead14b","location":"scheduler.register","message":"mode_check","data":{"mode":mode,"enabled":cfg.get("enabled")},"timestamp":int(time.time()*1000),"hypothesisId":"H1"}, ensure_ascii=False) + "\n"
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception:
            pass
        # #endregion
        if mode == "loop":
            from l3_node.skills.bi.bi_daily_report.main_skill import start_bi_scheduled_loop
            started = start_bi_scheduled_loop()
            # #region agent log
            try:
                import json, time
                from pathlib import Path
                p = Path(__file__).resolve().parents[3] / "debug-ead14b.log"
                line = json.dumps({"sessionId":"ead14b","location":"scheduler.register","message":"loop_started","data":{"started":started},"timestamp":int(time.time()*1000),"hypothesisId":"H3"}, ensure_ascii=False) + "\n"
                with open(p, "a", encoding="utf-8") as f:
                    f.write(line)
            except Exception:
                pass
            # #endregion
            if started:
                logger.info(
                    "[BI Scheduler] 已启动 BI 定时循环 run_at=%s:%s 间隔=%ds",
                    cfg.get("run_at_hour", 15),
                    str(cfg.get("run_at_minute", 8)).zfill(2),
                    cfg.get("interval_seconds", 30),
                )
                _scheduler_audit_log("REGISTERED mode=loop (background thread)")
            return True
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
            _scheduler_audit_log(f"REGISTERED job_id={_BI_JOB_ID} interval {desc}")
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
            _scheduler_audit_log(
                f"REGISTERED job_id={_BI_JOB_ID} cron hour={hour} minute={minute} tz={tz_name} "
                f"(须保持本 L3 进程常驻；仅 python scripts/run_bi_daily_report.py 不会注册定时器)"
            )
        return True
    except Exception as e:
        _scheduler_audit_log(f"REGISTER_FAILED {type(e).__name__}: {e}")
        logger.warning("[BI Scheduler] 注册任务失败: %s", e)
        return False
