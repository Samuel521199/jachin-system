"""
/test 模拟 Skill 的 L3 进程内定时（APScheduler 一次性任务）。

与 ``util:schedule_desktop_reminder``（仅桌面右下角弹窗）不同：到点会调用
``lark_test_file_skill.run_test_file_skill()``（写 workspace txt + 发 Lark 卡片）。

识别标准（自然语言定时）：
- 消息中须出现 **带斜杠的 ``/test`` 词元**（见 ``message_contains_slash_test_token``）
- 且能解析出执行时刻（或 ``/test schedule HH:MM`` 显式命令）
- **裸 ``test``**（如「下午17:14执行test」）与本 Skill **无必然联系**，不注册定时
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from l3_node.lark_test_file_skill import (
    is_slash_test_command,
    message_contains_slash_test_token,
)

logger = logging.getLogger(__name__)

_JOB_PREFIX = "slash_test_"
_scheduler = None
_scheduler_started = False
_scheduler_lock = threading.Lock()

# 自然语言时刻（在已确认含 /test 词元后再解析）
_RE_TIME_HM = re.compile(
    r"(?:"
    r"(?:今天|今日)?\s*(?:上午|下午|晚上|凌晨)?\s*(\d{1,2})\s*[:：时]\s*(\d{1,2})\s*(?:分)?"
    r"|"
    r"(\d{1,2})\s*[:：]\s*(\d{2})"
    r")",
    re.I,
)
_RE_DELAY_MINUTES = re.compile(
    r"(\d+)\s*(?:分钟|分|min)\s*(?:后|之后|以内)?",
    re.I,
)
_RE_SLASH_TEST_SCHEDULE_CMD = re.compile(
    r"^/test\s+(?:schedule|at|cron)\s+(\d{1,2})\s*[:：]\s*(\d{2})\s*$",
    re.I,
)
# 含 /test 但无时刻时，若像在说「定时」则提示补时刻（不交给 Agent 乱设桌面提醒）
_RE_SCHEDULE_INTENT_WITHOUT_TIME = re.compile(
    r"定时|生物钟|schedule|到点|几点|什么时候|何时|稍后|晚点",
    re.I,
)


def _shanghai_tz():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo("Asia/Shanghai")
    except Exception:
        return timezone(timedelta(hours=8))


def _jachin_root() -> Path:
    import os

    return Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin"))).expanduser()


def _persist_path() -> Path:
    return _jachin_root() / "test_skill_scheduled_jobs.json"


def _get_scheduler():
    global _scheduler, _scheduler_started
    with _scheduler_lock:
        if _scheduler is None:
            try:
                from apscheduler.schedulers.background import BackgroundScheduler

                _scheduler = BackgroundScheduler(timezone=_shanghai_tz())
            except Exception as e:
                logger.warning("[test-schedule] APScheduler 不可用: %s", e)
                return None
        if not _scheduler_started and _scheduler is not None:
            _scheduler.start()
            _scheduler_started = True
            logger.info("[test-schedule] BackgroundScheduler 已启动")
            _restore_persisted_jobs()
        return _scheduler


def _fire_test_skill_job(job_id: str = "") -> None:
    try:
        from l3_node.lark_test_file_skill import run_test_file_skill

        r = run_test_file_skill()
        logger.info(
            "[test-schedule] 到点执行 /test skill ok=%s path=%s card_err=%s",
            r.get("ok"),
            (r.get("path") or "")[:80],
            (r.get("card_error") or "")[:120],
        )
    except Exception:
        logger.exception("[test-schedule] 到点执行 /test skill 失败")
    finally:
        if job_id:
            _remove_persisted_job(job_id)


def _persist_job(job_id: str, fire_at_iso: str) -> None:
    path = _persist_path()
    try:
        data: list[dict[str, str]] = []
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                data = [x for x in raw if isinstance(x, dict)]
        data.append({"id": job_id, "fire_at_iso": fire_at_iso})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.debug("[test-schedule] 持久化跳过: %s", e)


def _remove_persisted_job(job_id: str) -> None:
    path = _persist_path()
    try:
        if not path.is_file():
            return
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return
        kept = [x for x in raw if isinstance(x, dict) and x.get("id") != job_id]
        path.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _restore_persisted_jobs() -> None:
    path = _persist_path()
    if not path.is_file():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(raw, list):
        return
    now = datetime.now(_shanghai_tz())
    for row in raw:
        if not isinstance(row, dict):
            continue
        iso = str(row.get("fire_at_iso") or "").strip()
        if not iso:
            continue
        try:
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_shanghai_tz())
        except ValueError:
            continue
        if dt <= now:
            continue
        schedule_test_skill_at(dt, source="restore", persist=False)


def schedule_test_skill_at(
    fire_at: datetime, *, source: str = "im", persist: bool = True
) -> dict[str, Any]:
    """注册一次性 /test Skill 任务。"""
    tz = _shanghai_tz()
    if fire_at.tzinfo is None:
        fire_at = fire_at.replace(tzinfo=tz)
    else:
        fire_at = fire_at.astimezone(tz)

    now = datetime.now(tz)
    if fire_at <= now + timedelta(seconds=2):
        return {"ok": False, "error": f"时刻须晚于当前时间（当前 {now:%Y-%m-%d %H:%M:%S}）"}

    sched = _get_scheduler()
    if sched is None:
        return {"ok": False, "error": "APScheduler 不可用，请 pip install apscheduler"}

    job_id = f"{_JOB_PREFIX}{int(fire_at.timestamp())}"
    jid = job_id

    def _run() -> None:
        _fire_test_skill_job(jid)

    try:
        sched.add_job(
            _run,
            trigger="date",
            run_date=fire_at,
            id=job_id,
            replace_existing=True,
            misfire_grace_time=300,
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}

    iso = fire_at.isoformat()
    if persist:
        _persist_job(job_id, iso)

    logger.info("[test-schedule] 已注册 %s fire_at=%s source=%s", job_id, iso, source)
    return {
        "ok": True,
        "job_id": job_id,
        "fire_at_iso": iso,
        "fire_at_display": fire_at.strftime("%Y-%m-%d %H:%M:%S"),
        "hint": "到点将执行 /test 模拟 Skill（写 workspace 文件 + 发 Lark 卡片），非仅桌面弹窗。",
    }


def schedule_test_skill_at_unix_ms(target_ms: int, *, source: str = "reminder_hook") -> dict[str, Any]:
    tz = _shanghai_tz()
    fire_at = datetime.fromtimestamp(target_ms / 1000.0, tz=tz)
    return schedule_test_skill_at(fire_at, source=source)


def parse_today_local_hm(hour: int, minute: int) -> datetime:
    tz = _shanghai_tz()
    now = datetime.now(tz)
    dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if dt <= now:
        dt = dt + timedelta(days=1)
    return dt


def _parse_hm_from_match(m: re.Match[str], text: str) -> datetime | None:
    if m.group(1) is not None:
        h, mi = int(m.group(1)), int(m.group(2))
    else:
        h, mi = int(m.group(3)), int(m.group(4))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    if re.search(r"下午|晚上", text) and 1 <= h <= 11:
        h += 12
    if re.search(r"凌晨", text) and h == 12:
        h = 0
    return parse_today_local_hm(h, mi)


def parse_schedule_time_from_text(text: str) -> datetime | None:
    """
    从自然语言解析执行时刻。

    **前置条件**：消息须含 ``/test`` 词元（``message_contains_slash_test_token``）；
    仅含裸 ``test`` 时返回 None。
    """
    t = (text or "").strip()
    if not message_contains_slash_test_token(t):
        return None

    m_cmd = _RE_SLASH_TEST_SCHEDULE_CMD.match(t)
    if m_cmd:
        h, mi = int(m_cmd.group(1)), int(m_cmd.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return parse_today_local_hm(h, mi)

    m = _RE_TIME_HM.search(t)
    if m:
        return _parse_hm_from_match(m, t)

    dm = _RE_DELAY_MINUTES.search(t)
    if dm:
        try:
            mins = int(dm.group(1))
            if 1 <= mins <= 24 * 60:
                return datetime.now(_shanghai_tz()) + timedelta(minutes=mins)
        except (TypeError, ValueError):
            pass

    return None


def try_test_schedule_intercept(text: str) -> str | None:
    """
    自然语言注册 /test 定时：须含 ``/test`` 词元 + 可解析时刻；否则 None（或短提示）。
    """
    t = (text or "").strip()
    if is_slash_test_command(t):
        return None

    if not message_contains_slash_test_token(t):
        return None

    fire_at = parse_schedule_time_from_text(t)
    if fire_at is None:
        if _RE_SCHEDULE_INTENT_WITHOUT_TIME.search(t):
            return (
                "检测到消息中含 **/test**，但未解析出具体执行时刻。\n"
                "请补充时刻，例如：`下午17:14执行/test`，或 `/test schedule 17:14`。\n"
                "说明：仅写「执行 test」（无斜杠）不会绑定本模拟 Skill。"
            )
        return None

    r = schedule_test_skill_at(fire_at, source="lark_im")
    if not r.get("ok"):
        return f"⚠️ 无法注册 /test 定时任务：{r.get('error')}"

    return (
        "【/test 定时任务已注册（L3 进程内）】\n"
        f"计划执行时刻：{r.get('fire_at_display')}\n"
        f"任务 ID：{r.get('job_id')}\n"
        f"{r.get('hint')}\n\n"
        "识别规则：消息须含 **/test**（带斜杠）+ 时刻；裸「test」不触发本 Skill。"
    )


def ensure_test_schedule_scheduler_started() -> None:
    """L3 启动时调用，恢复未过期的持久化任务。"""
    _get_scheduler()
