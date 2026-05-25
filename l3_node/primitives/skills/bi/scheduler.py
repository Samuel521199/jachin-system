"""
BI 每日战报 — 专属定时调度器（L3 进程内 APScheduler，不依赖 HR）。

支持可配置的 cron（每天固定时间）和 interval（每 N 分钟/小时）；loop 模式走独立后台线程。

配置来源（后者覆盖前者）：
  1) ~/.jachin/config/skills/com.jachin.bi.daily_report/bi_daily_report.yaml
  2) <项目根>/config/skills/com.jachin.bi.daily_report/bi_daily_report.yaml（若上一步不存在则用此）
  可选覆盖文件（同上目录优先级）：bi_scheduler.yaml — 仅写 schedule_enabled / schedule 即可

环境变量（可选）：BI_DAILY_REPORT_SCHEDULE=off|0|false 强制关；on|1|true 强制开（便于本机不改 YAML）
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_BI_JOB_ID = "bi_daily_report"
_bi_background_scheduler = None
_bi_scheduler_started = False

# 默认：**关闭**定时（避免与 Kalaroko 巡检整点/晨报争用本机 Chrome、9222、DashScope 与磁盘锁）。
# 需要 BI 自动跑时：在 bi_daily_report.yaml 设 schedule_enabled / schedule.enabled，或 export BI_DAILY_REPORT_SCHEDULE=on
_DEFAULT_SCHEDULE = {
    "enabled": False,
    "mode": "cron",
    "hour": 8,
    "minute": 0,
    "timezone": "Asia/Shanghai",
}


def _bi_scheduler_audit_log_path() -> Path:
    return Path.home() / ".jachin" / "client_volumes" / "bi_data" / "logs" / "bi_scheduler_audit.log"


_LEGACY_HR_SKIP_MARKER = "SKIP HR 包未找到"


def _strip_legacy_audit_log_once() -> None:
    """
    旧版依赖 HR 的调度会在日志里写 SKIP HR；与当前 BI 专属调度无关。
    若文件中仍含该标记，则裁剪为从首条「BI 专属调度」记录起保留，否则整文件清空为一条说明。
    仅在有遗留内容时改写文件，可重复调用（第二次起无 SKIP 即返回）。
    """
    p = _bi_scheduler_audit_log_path()
    try:
        if not p.exists() or p.stat().st_size == 0:
            return
        text = p.read_text(encoding="utf-8", errors="replace")
        if _LEGACY_HR_SKIP_MARKER not in text:
            return
        lines = text.splitlines()
        cut: int | None = None
        for i, line in enumerate(lines):
            if "BI BackgroundScheduler started (dedicated, not HR)" in line:
                cut = i
                break
            if "(BI dedicated scheduler)" in line:
                cut = i
                break
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        p.parent.mkdir(parents=True, exist_ok=True)
        if cut is not None:
            kept = lines[cut:]
            notice = f"{ts} | AUDIT_LEGACY_STRIPPED removed_lines={cut} reason=old_HR_coupled_scheduler"
            new_text = notice + "\n" + "\n".join(kept) + ("\n" if kept else "")
            p.write_text(new_text, encoding="utf-8")
        else:
            p.write_text(
                f"{ts} | AUDIT_LEGACY_CLEARED reason=log_only_contained_HR_skip\n",
                encoding="utf-8",
            )
    except Exception:
        pass


def _scheduler_audit_log(message: str) -> None:
    """
    固定写入 ~/.jachin/client_volumes/bi_data/logs/bi_scheduler_audit.log
    用于排查「定时未触发 / 未注册」：enabled、cron 表达式、是否使用 BI 专属调度器等。
    """
    try:
        p = _bi_scheduler_audit_log_path()
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
    rel = Path("config") / "skills" / "com.jachin.bi.daily_report" / "bi_daily_report.yaml"
    return [jachin_root / rel, project_root / rel]


def _bi_scheduler_overlay_candidates() -> list[Path]:
    """可选：仅定时片段，与 bi_daily_report.yaml 同目录约定，优先 ~/.jachin。"""
    from l3_node.paths import get_app_root

    jachin_root = Path.home() / ".jachin"
    project_root = get_app_root()
    rel = Path("config") / "skills" / "com.jachin.bi.daily_report" / "bi_scheduler.yaml"
    return [jachin_root / rel, project_root / rel]


def _apply_scheduler_overlays(merged_raw: dict[str, Any]) -> None:
    for overlay_path in _bi_scheduler_overlay_candidates():
        if not overlay_path.exists():
            continue
        try:
            import yaml

            with open(overlay_path, encoding="utf-8") as f:
                o = yaml.safe_load(f) or {}
            if not isinstance(o, dict):
                continue
            if "schedule_enabled" in o:
                merged_raw["schedule_enabled"] = o["schedule_enabled"]
            if isinstance(o.get("schedule"), dict):
                base_sched = merged_raw.get("schedule")
                if not isinstance(base_sched, dict):
                    base_sched = {}
                merged_raw["schedule"] = {**base_sched, **o["schedule"]}
        except Exception as e:
            logger.warning("[BI Scheduler] 覆盖配置读取失败 %s: %s", overlay_path, e)


def _apply_env_schedule_disable(out: dict[str, Any]) -> None:
    v = (os.environ.get("BI_DAILY_REPORT_SCHEDULE") or "").strip().lower()
    if v in ("off", "0", "false", "no", "disabled"):
        out["enabled"] = False


def _apply_env_schedule_enable(out: dict[str, Any]) -> None:
    """显式开启（后于 disable 应用，便于 ``BI_DAILY_REPORT_SCHEDULE=on`` 覆盖默认关）。"""
    v = (os.environ.get("BI_DAILY_REPORT_SCHEDULE") or "").strip().lower()
    if v in ("on", "1", "true", "yes", "enabled"):
        out["enabled"] = True


def _load_schedule_config() -> dict[str, Any]:
    """合并 bi_daily_report.yaml + 可选 bi_scheduler.yaml，再应用环境变量关闭开关。"""
    merged_raw: dict[str, Any] = {}
    for path in _bi_daily_report_yaml_candidates():
        if not path.exists():
            continue
        try:
            import yaml

            with open(path, encoding="utf-8") as f:
                merged_raw = yaml.safe_load(f) or {}
            if not isinstance(merged_raw, dict):
                merged_raw = {}
            break
        except Exception as e:
            logger.warning("[BI Scheduler] 配置加载失败 %s: %s", path, e)

    _apply_scheduler_overlays(merged_raw)

    if not merged_raw:
        out = {**_DEFAULT_SCHEDULE}
        _apply_env_schedule_disable(out)
        _apply_env_schedule_enable(out)
        return out

    sched = merged_raw.get("schedule") or {}
    if not isinstance(sched, dict):
        sched = {}
    out = {**_DEFAULT_SCHEDULE, **sched}
    top_ok = merged_raw.get("schedule_enabled", True) if "schedule_enabled" in merged_raw else True
    sched_ok = sched.get("enabled", True)
    out["enabled"] = bool(top_ok and sched_ok)
    _apply_env_schedule_disable(out)
    _apply_env_schedule_enable(out)
    return out


def get_bi_background_scheduler():
    """
    BI 专属 BackgroundScheduler 单例；首次获取时 start。
    若未安装 apscheduler，返回 None。
    """
    global _bi_background_scheduler, _bi_scheduler_started
    if _bi_background_scheduler is None:
        try:
            from apscheduler.schedulers.background import BackgroundScheduler

            _bi_background_scheduler = BackgroundScheduler()
        except Exception as e:
            logger.warning("[BI Scheduler] APScheduler 不可用: %s", e)
            _scheduler_audit_log(f"SKIP APScheduler import/start failed: {e}")
            return None
    if not _bi_scheduler_started and _bi_background_scheduler is not None:
        _bi_background_scheduler.start()
        _bi_scheduler_started = True
        _scheduler_audit_log("BI BackgroundScheduler started (dedicated, not HR)")
    return _bi_background_scheduler


def _run_bi_daily_report_job() -> None:
    """定时任务回调"""
    _scheduler_audit_log("JOB_FIRE bi_daily_report APScheduler callback entered")
    from l3_node.scheduled_global_registry import scheduled_global_task_scope

    with scheduled_global_task_scope(
        "bi_scheduler",
        _BI_JOB_ID,
        title="BI 每日战报",
    ):
        _run_bi_daily_report_job_body()


def _run_bi_daily_report_job_body() -> None:
    try:
        from l3_node.primitives.skills.bi.bi_daily_report.main_skill import run_bi_daily_report_scheduled

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

        tz_lower = (tz_name or "").lower()
        if "utc+8" in tz_lower or "asia/shanghai" in tz_lower:
            return timezone(timedelta(hours=8))
        return timezone(timedelta(hours=8))


def register_bi_daily_report_job() -> bool:
    """
    将 BI 每日战报注册到 BI 专属 APScheduler（不依赖 HR / recruitment_scheduler）。

    schedule 配置见 bi_daily_report.yaml；可用 bi_scheduler.yaml 覆盖定时字段。

    Returns:
        True 表示注册成功或 loop 模式已启动；False 表示关闭、调度器不可用或失败
    """
    _strip_legacy_audit_log_once()
    _scheduler_audit_log("register_bi_daily_report_job() called (BI dedicated scheduler)")
    yaml_hit = next((p for p in _bi_daily_report_yaml_candidates() if p.exists()), None)
    overlay_hits = [p for p in _bi_scheduler_overlay_candidates() if p.exists()]
    _scheduler_audit_log(f"bi_daily_report yaml first_hit={yaml_hit} bi_scheduler overlays={overlay_hits}")

    scheduler = get_bi_background_scheduler()
    if scheduler is None:
        _scheduler_audit_log("SKIP BI BackgroundScheduler unavailable (apscheduler missing?)")
        logger.debug("[BI Scheduler] APScheduler 不可用，跳过 BI 任务注册")
        return False

    cfg = _load_schedule_config()
    _scheduler_audit_log(
        f"schedule effective: enabled={cfg.get('enabled')} mode={cfg.get('mode')} "
        f"hour={cfg.get('hour')} minute={cfg.get('minute')} tz={cfg.get('timezone')}"
    )
    if not cfg.get("enabled", True):
        logger.info("[BI Scheduler] schedule 已关闭，跳过 BI 任务注册")
        _scheduler_audit_log("SKIP schedule disabled (schedule_enabled / schedule.enabled / env BI_DAILY_REPORT_SCHEDULE)")
        try:
            scheduler.remove_job(_BI_JOB_ID)
        except Exception:
            pass
        try:
            from l3_node.task_runtime_registry import unregister_scheduled_job_hint

            unregister_scheduled_job_hint(_BI_JOB_ID)
        except Exception:
            pass
        return False

    mode = (cfg.get("mode") or "cron").lower()

    try:
        if mode == "loop":
            try:
                scheduler.remove_job(_BI_JOB_ID)
            except Exception:
                pass
            from l3_node.primitives.skills.bi.bi_daily_report.main_skill import start_bi_scheduled_loop

            started = start_bi_scheduled_loop()
            if started:
                logger.info(
                    "[BI Scheduler] 已启动 BI 定时循环 run_at=%s:%s 间隔=%ds",
                    cfg.get("run_at_hour", 15),
                    str(cfg.get("run_at_minute", 8)).zfill(2),
                    cfg.get("interval_seconds", 30),
                )
                _scheduler_audit_log("REGISTERED mode=loop (background thread)")
                try:
                    from l3_node.task_runtime_registry import register_scheduled_job_hint

                    register_scheduled_job_hint(
                        job_id=_BI_JOB_ID,
                        title="BI 每日战报",
                        schedule_summary=(
                            f"loop 间隔 {int(cfg.get('interval_seconds', 30) or 30)}s，"
                            f"锚点 {cfg.get('run_at_hour', 15)}:"
                            f"{str(cfg.get('run_at_minute', 8)).zfill(2)}"
                        ),
                        source="bi_scheduler",
                    )
                except Exception:
                    pass
            else:
                try:
                    from l3_node.task_runtime_registry import unregister_scheduled_job_hint

                    unregister_scheduled_job_hint(_BI_JOB_ID)
                except Exception:
                    pass
            return True
        # cron / interval 使用 APScheduler
        try:
            scheduler.remove_job(_BI_JOB_ID)
        except Exception:
            pass
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
            try:
                from l3_node.task_runtime_registry import register_scheduled_job_hint

                register_scheduled_job_hint(
                    job_id=_BI_JOB_ID,
                    title="BI 每日战报",
                    schedule_summary=f"interval {desc}",
                    source="bi_scheduler",
                )
            except Exception:
                pass
        else:
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
                f"(L3 进程须常驻；仅 python scripts/run_bi_daily_report.py 不会注册定时器)"
            )
            try:
                from l3_node.task_runtime_registry import register_scheduled_job_hint

                register_scheduled_job_hint(
                    job_id=_BI_JOB_ID,
                    title="BI 每日战报",
                    schedule_summary=f"每天 {hour:02d}:{minute:02d} {tz_name}",
                    source="bi_scheduler",
                )
            except Exception:
                pass
        return True
    except Exception as e:
        _scheduler_audit_log(f"REGISTER_FAILED {type(e).__name__}: {e}")
        logger.warning("[BI Scheduler] 注册任务失败: %s", e)
        try:
            from l3_node.task_runtime_registry import unregister_scheduled_job_hint

            unregister_scheduled_job_hint(_BI_JOB_ID)
        except Exception:
            pass
        return False
