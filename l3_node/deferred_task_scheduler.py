"""
通用定时任务调度器（L3 进程内）

供模型通过 ``util:schedule_task`` 工具将**任意**用户意图注册为一次性未来任务。
到点时通过 ``core:submit_background_task`` 的后台 Worker 真实执行（run_agent + 全工具池），
无需桌面客户端，仅需 L3 进程在跑。

典型场景：
  用户：「上午11:23帮我在 workspace 创建一个 txt 写今日游戏推荐」
  模型：调用 util:schedule_task → fire_at=11:23, intent="..."
  到点：APScheduler 触发 → 提交 background_task → run_agent 执行写文件
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_JOB_PREFIX = "deferred_task_"
_scheduler = None
_scheduler_started = False
_scheduler_lock = threading.RLock()  # RLock: _get_scheduler → _restore_persisted_jobs → _register_job → _get_scheduler 同线程重入
_PERSIST_PATH = Path.home() / ".jachin" / "deferred_scheduled_tasks.json"


# ──────────────────────────────────────────────────────────────────────────────
# 时区
# ──────────────────────────────────────────────────────────────────────────────

def _shanghai_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Asia/Shanghai")
    except Exception:
        return timezone(timedelta(hours=8))


def _now_sh() -> datetime:
    return datetime.now(_shanghai_tz())


# ──────────────────────────────────────────────────────────────────────────────
# APScheduler 单例
# ──────────────────────────────────────────────────────────────────────────────

def _get_scheduler():
    global _scheduler, _scheduler_started
    with _scheduler_lock:
        if _scheduler is None:
            try:
                from apscheduler.schedulers.background import BackgroundScheduler
                _scheduler = BackgroundScheduler(timezone=_shanghai_tz())
            except Exception as e:
                logger.warning("[deferred-sched] APScheduler 不可用: %s", e)
                return None
        if not _scheduler_started and _scheduler is not None:
            _scheduler.start()
            _scheduler_started = True
            logger.info("[deferred-sched] BackgroundScheduler 已启动（通用定时任务）")
            _restore_persisted_jobs()
        return _scheduler


# ──────────────────────────────────────────────────────────────────────────────
# 持久化
# ──────────────────────────────────────────────────────────────────────────────

def _persist_job(job_id: str, fire_at_iso: str, intent: str, lark_chat_id: str | None = None) -> None:
    try:
        data: list[dict[str, str]] = []
        if _PERSIST_PATH.is_file():
            raw = json.loads(_PERSIST_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                data = [x for x in raw if isinstance(x, dict)]
        row: dict[str, Any] = {"id": job_id, "fire_at_iso": fire_at_iso, "intent": intent}
        if lark_chat_id:
            row["lark_chat_id"] = lark_chat_id
        data.append(row)
        _PERSIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PERSIST_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.debug("[deferred-sched] 持久化跳过: %s", e)


def _remove_persisted_job(job_id: str) -> None:
    try:
        if not _PERSIST_PATH.is_file():
            return
        raw = json.loads(_PERSIST_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return
        kept = [x for x in raw if isinstance(x, dict) and x.get("id") != job_id]
        _PERSIST_PATH.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _restore_persisted_jobs() -> None:
    if not _PERSIST_PATH.is_file():
        return
    try:
        raw = json.loads(_PERSIST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(raw, list):
        return
    now = _now_sh()
    restored = 0
    for row in raw:
        if not isinstance(row, dict):
            continue
        iso = str(row.get("fire_at_iso") or "").strip()
        intent = str(row.get("intent") or "").strip()
        if not iso or not intent:
            continue
        try:
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_shanghai_tz())
        except ValueError:
            continue
        if dt <= now:
            continue
        lark_chat_id = str(row.get("lark_chat_id") or "").strip() or None
        _register_job(dt, intent, persist=False, lark_chat_id=lark_chat_id)
        restored += 1
    if restored:
        logger.info("[deferred-sched] 恢复 %d 个未过期定时任务", restored)


# ──────────────────────────────────────────────────────────────────────────────
# 任务执行
# ──────────────────────────────────────────────────────────────────────────────

def _build_enriched_intent(intent: str, lark_chat_id: str | None) -> str:
    """
    到点执行时对 intent 做两件事：
    1. 剔除时间前缀（避免 LLM 把「下午14:25」再次理解为调度指令而无限推到明天）。
    2. 注入「立即执行」指令头 + 可选 Lark 推送要求。
    """
    clean = _strip_time_prefix(intent)

    prefix = (
        "【⚠️ 定时任务·立即执行】已到预定时刻，请直接完成下列任务，"
        "**禁止**再次调用 util:schedule_task / util:schedule_desktop_reminder 重新注册定时任务。\n\n"
    )

    if not lark_chat_id:
        return prefix + clean

    suffix = (
        f"\n\n【回馈渠道｜宿主已绑定 originating_lark_chat_id】`{lark_chat_id}`\n"
        "请把提醒或可交付结果写在 **User-facing result**（面向用户、简短可读）。\n"
        "**禁止**调用 util:lark_send_text / util:desktop_message_box（除非你被要求发到**别的**会话）；\n"
        "本轮结束后 **宿主会自动**把你的 User-facing result 推送到上述会话。\n"
    )
    return prefix + clean + suffix


def _send_deferred_result_to_lark(text: str, chat_id: str) -> None:
    """
    定时任务完成后由宿主推送 User-facing result，不经 LLM 自行选收件人。
    直接调用 lark.im.send_text，避免 util:lark_send_text 被 LARK_USER_OPEN_ID 等覆盖。
    """
    if not (chat_id or "").strip():
        logger.warning("[deferred-sched] Lark 推送跳过：chat_id 为空")
        return
    cid = chat_id.strip()
    try:
        from l3_node.channels.lark.im import send_text as _lark_send

        result = _lark_send(
            receive_id=cid,
            text=(text or "")[:4000],
            receive_id_type="chat_id",
        )
        if result.get("status") == "success":
            logger.info("[deferred-sched] User-facing result 已推送到 Lark chat_id …%s", cid[-16:])
        else:
            logger.warning(
                "[deferred-sched] Lark 推送失败 chat_id …%s: %s",
                cid[-16:],
                result.get("error") or result,
            )
    except Exception as e:
        logger.warning("[deferred-sched] Lark 推送异常: %s", e)


def _execute_deferred_job(intent: str, job_id: str, lark_chat_id: str | None = None) -> None:
    """到点由 APScheduler 调用；通过 background_task_service 异步执行。"""
    logger.info(
        "[deferred-sched] 到点触发 job_id=%s lark_chat_id=%s intent=%s",
        job_id, lark_chat_id or "-", intent[:80],
    )
    enriched = _build_enriched_intent(intent, lark_chat_id)
    try:
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # APScheduler 后台线程：直接 asyncio.run
            result_str = asyncio.run(_submit_via_new_loop(enriched, lark_chat_id=lark_chat_id))
            logger.info("[deferred-sched] asyncio.run 提交结果: %s", result_str[:200])
            return

        from l3_node.primitives.agent_tasks.background_task_service import submit_background_task_sync
        _payload: dict[str, Any] = {"intent": enriched, "max_iterations": 24}
        if lark_chat_id:
            _payload["lark_chat_id"] = lark_chat_id
        payload = json.dumps(_payload, ensure_ascii=False)
        result_str = submit_background_task_sync(payload)
        result = json.loads(result_str) if result_str.startswith("{") else {}
        if result.get("status") not in ("ok", "queued"):
            logger.warning("[deferred-sched] 后台任务提交失败: %s", result_str[:200])
    except Exception as e:
        logger.exception("[deferred-sched] 触发执行异常: %s", e)
    finally:
        _remove_persisted_job(job_id)


async def _submit_via_new_loop(intent: str, *, lark_chat_id: str | None = None) -> str:
    """在 APScheduler 后台线程（无 loop）时，创建 engine 并直接 run_agent。"""
    try:
        from l3_node.__main__ import _create_engine_standalone
        from l3_node.agent_core import run_agent

        engine = _create_engine_standalone()
        implicit: dict[str, Any] = {"channel": "deferred_task_scheduler"}
        if lark_chat_id:
            implicit["lark_chat_id"] = lark_chat_id
        answer = await run_agent(
            intent,
            engine,
            max_iterations=24,
            implicit_attribution=implicit,
        )
        ans = answer or ""
        # asyncio.run 分支不走 background_task_service，必须由宿主在此处推送（与 implicit lark_chat_id 一致）
        if lark_chat_id and ans and not ans.startswith("error:"):
            await asyncio.to_thread(_send_deferred_result_to_lark, ans[:4000], lark_chat_id)
        return ans
    except Exception as e:
        logger.exception("[deferred-sched] _submit_via_new_loop 失败: %s", e)
        return f"error: {e}"


# ──────────────────────────────────────────────────────────────────────────────
# 核心注册
# ──────────────────────────────────────────────────────────────────────────────

def _register_job(
    fire_at: datetime,
    intent: str,
    *,
    persist: bool = True,
    lark_chat_id: str | None = None,
) -> dict[str, Any]:
    """注册一次性定时任务，返回结果 dict。"""
    tz = _shanghai_tz()
    if fire_at.tzinfo is None:
        fire_at = fire_at.replace(tzinfo=tz)
    else:
        fire_at = fire_at.astimezone(tz)

    now = _now_sh()
    if fire_at <= now + timedelta(seconds=2):
        return {
            "ok": False,
            "error": f"时刻须晚于当前时间（当前 {now:%Y-%m-%d %H:%M:%S}）",
        }

    sched = _get_scheduler()
    if sched is None:
        return {"ok": False, "error": "APScheduler 不可用，请确认已 pip install apscheduler"}

    job_id = f"{_JOB_PREFIX}{int(fire_at.timestamp())}_{hash(intent) & 0xFFFF:04x}"
    jid, the_intent, the_cid = job_id, intent, lark_chat_id

    def _run() -> None:
        _execute_deferred_job(the_intent, jid, lark_chat_id=the_cid)

    try:
        sched.add_job(
            _run,
            trigger="date",
            run_date=fire_at,
            id=job_id,
            replace_existing=True,
            misfire_grace_time=600,
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}

    iso = fire_at.isoformat()
    if persist:
        _persist_job(job_id, iso, intent, lark_chat_id=lark_chat_id)

    logger.info(
        "[deferred-sched] 已注册 job_id=%s fire_at=%s lark_chat_id=%s intent=%s",
        job_id, fire_at.strftime("%Y-%m-%d %H:%M:%S"), lark_chat_id or "-", intent[:80],
    )
    notify_hint = "飞书推送" if lark_chat_id else "（到点将通过桌面或飞书推送，取决于任务描述）"
    return {
        "ok": True,
        "job_id": job_id,
        "fire_at_iso": iso,
        "fire_at_display": fire_at.strftime("%Y-%m-%d %H:%M:%S"),
        "intent_preview": intent[:100],
        "notify_channel": f"Lark chat_id={lark_chat_id}" if lark_chat_id else "未指定（由任务内容决定）",
        "hint": (
            f"已注册 L3 进程内定时任务，到点将通过后台 Agent Worker 真实执行（无需桌面客户端）。"
            f"通知渠道：{notify_hint}。"
            "任务持久化于 ~/.jachin/deferred_scheduled_tasks.json，L3 重启可恢复未过期任务。"
        ),
    }


# ──────────────────────────────────────────────────────────────────────────────
# 时刻解析
# ──────────────────────────────────────────────────────────────────────────────

_RE_TIME_HM = re.compile(
    r"(?:"
    r"(?:今天|今日|明天|明日)?\s*"
    r"(?:上午|下午|晚上|凌晨|早上|早|午后)?\s*"
    r"(\d{1,2})\s*[:：时]\s*(\d{2})\s*(?:分|min)?"
    r"|"
    r"(\d{1,2})\s*[:：]\s*(\d{2})"
    r")",
    re.I,
)
_RE_DELAY_MINUTES = re.compile(
    r"(\d+)\s*(?:分钟?|min)(?:\s*(?:后|之后|以后))?",
    re.I,
)
_RE_DELAY_HOURS = re.compile(
    r"(\d+)\s*(?:小时|hours?)(?:\s*(?:后|之后|以后))?",
    re.I,
)


def _parse_fire_at_from_text(text: str) -> datetime | None:
    """
    从自然语言文本解析目标触发时刻（本地 Asia/Shanghai）。
    支持：HH:MM、上午/下午HH:MM、N分钟后、N小时后、明天HH:MM 等。
    """
    t = (text or "").strip()
    tz = _shanghai_tz()
    now = _now_sh()

    # ── 绝对时刻 HH:MM ──
    m = _RE_TIME_HM.search(t)
    if m:
        h_raw = int(m.group(1) or m.group(3) or 0)
        mi_raw = int(m.group(2) or m.group(4) or 0)
        if not (0 <= h_raw <= 23 and 0 <= mi_raw <= 59):
            return None

        # 下午/晚上：12小时制 → 24小时制
        before = t[: m.start()]
        if re.search(r"下午|晚上|午后|p\.?m\.?", before + t[m.start() : m.end()], re.I):
            if 1 <= h_raw <= 11:
                h_raw += 12
        if re.search(r"凌晨", before + t[m.start() : m.end()], re.I):
            if h_raw == 12:
                h_raw = 0

        fire_at = now.replace(hour=h_raw, minute=mi_raw, second=0, microsecond=0)

        # 明天
        if re.search(r"明天|明日", t):
            fire_at = fire_at + timedelta(days=1)
        elif fire_at <= now + timedelta(seconds=30):
            fire_at = fire_at + timedelta(days=1)

        return fire_at

    # ── 相对延迟：N小时后 ──
    m_h = _RE_DELAY_HOURS.search(t)
    if m_h:
        try:
            return now + timedelta(hours=int(m_h.group(1)))
        except (TypeError, ValueError):
            pass

    # ── 相对延迟：N分钟后 ──
    m_m = _RE_DELAY_MINUTES.search(t)
    if m_m:
        try:
            mins = int(m_m.group(1))
            if 1 <= mins <= 24 * 60:
                return now + timedelta(minutes=mins)
        except (TypeError, ValueError):
            pass

    return None


# ──────────────────────────────────────────────────────────────────────────────
# 公开 API（供 core_util_tools 注册的 util:schedule_task 调用）
# ──────────────────────────────────────────────────────────────────────────────

def schedule_task(
    intent: str,
    *,
    fire_at_iso: str | None = None,
    fire_at_unix_ms: int | None = None,
    delay_seconds: float | None = None,
    fire_at_natural: str | None = None,
    lark_chat_id: str | None = None,
) -> dict[str, Any]:
    """
    通用任务定时注册入口。

    ``intent``：到点需执行的完整任务描述（模型自由填写，等价于 run_agent 的 user_input）。
    ``lark_chat_id``：可选，用户所在的 Lark 会话 ID；到点时结果将通过飞书发回此会话。
    时刻四种来源（优先级顺序）：
      1. ``fire_at_iso``：ISO8601 字符串（无时区 → Asia/Shanghai）
      2. ``fire_at_unix_ms``：Unix 毫秒时间戳
      3. ``delay_seconds``：相对延迟秒数
      4. ``fire_at_natural``：自然语言（「上午11:23」「30分钟后」等），内部解析
    """
    if not (intent or "").strip():
        return {"ok": False, "error": "intent 不能为空"}

    tz = _shanghai_tz()
    fire_at: datetime | None = None

    if fire_at_iso:
        try:
            s = str(fire_at_iso).strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            fire_at = dt if dt.tzinfo else dt.replace(tzinfo=tz)
            fire_at = fire_at.astimezone(tz)
        except (ValueError, TypeError) as e:
            return {"ok": False, "error": f"fire_at_iso 解析失败: {e}"}

    elif fire_at_unix_ms is not None:
        try:
            fire_at = datetime.fromtimestamp(int(fire_at_unix_ms) / 1000.0, tz=tz)
        except (ValueError, TypeError, OSError) as e:
            return {"ok": False, "error": f"fire_at_unix_ms 解析失败: {e}"}

    elif delay_seconds is not None:
        try:
            fire_at = _now_sh() + timedelta(seconds=float(delay_seconds))
        except (ValueError, TypeError) as e:
            return {"ok": False, "error": f"delay_seconds 解析失败: {e}"}

    elif fire_at_natural:
        fire_at = _parse_fire_at_from_text(str(fire_at_natural))
        if fire_at is None:
            return {
                "ok": False,
                "error": (
                    f"无法从「{fire_at_natural}」解析时刻。"
                    "请提供更明确的时间（如 fire_at_iso 或 delay_seconds）。"
                ),
            }

    if fire_at is None:
        return {
            "ok": False,
            "error": "须提供 fire_at_iso、fire_at_unix_ms、delay_seconds 或 fire_at_natural 之一",
        }

    # 模型漏传时：从当前 RoleExecutionAgent 轮次绑定的 Lark 会话兜底（与接收消息时的 chat_id 一致）
    _lc = (lark_chat_id or "").strip() or None
    if not _lc:
        try:
            from l3_node.channels.lark.turn_chat_context import peek_lark_chat_id_for_tools

            _peek = peek_lark_chat_id_for_tools()
            if _peek:
                _lc = _peek
                logger.debug("[deferred-sched] schedule_task 从未显式参数回填 lark_chat_id …%s", _peek[-16:])
        except Exception:
            pass

    return _register_job(fire_at, intent.strip(), lark_chat_id=_lc)


def parse_and_schedule_from_user_message(
    text: str,
    intent_override: str | None = None,
    lark_chat_id: str | None = None,
) -> dict[str, Any] | None:
    """
    从对话文本中尝试提取时刻并注册定时任务。
    ``intent_override``：若不传则用 text 本身作为 intent。
    返回 None 表示未检测到时刻。
    """
    fire_at = _parse_fire_at_from_text(text)
    if fire_at is None:
        return None
    intent = (intent_override or text).strip()
    return _register_job(fire_at, intent, lark_chat_id=lark_chat_id)


def list_pending_tasks() -> list[dict[str, Any]]:
    """列出持久化的未过期定时任务（供查询/展示）。"""
    if not _PERSIST_PATH.is_file():
        return []
    try:
        raw = json.loads(_PERSIST_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return []
        now = _now_sh()
        result = []
        for row in raw:
            if not isinstance(row, dict):
                continue
            try:
                dt = datetime.fromisoformat(str(row.get("fire_at_iso") or ""))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=_shanghai_tz())
                if dt > now:
                    result.append({
                        "job_id": row.get("id", ""),
                        "fire_at_display": dt.strftime("%Y-%m-%d %H:%M:%S"),
                        "intent_preview": str(row.get("intent", ""))[:80],
                    })
            except (ValueError, TypeError):
                continue
        return result
    except Exception:
        return []


def cancel_task(job_id: str) -> dict[str, Any]:
    """取消一个已注册的定时任务。"""
    sched = _get_scheduler()
    removed_from_sched = False
    if sched is not None:
        try:
            sched.remove_job(job_id)
            removed_from_sched = True
        except Exception:
            pass
    _remove_persisted_job(job_id)
    if removed_from_sched:
        return {"ok": True, "message": f"已取消任务 {job_id}"}
    return {"ok": False, "error": f"调度器中未找到任务 {job_id}（可能已执行或 id 有误）"}


def ensure_deferred_scheduler_started() -> None:
    """L3 启动时调用，恢复未过期持久化任务。"""
    _get_scheduler()


# ──────────────────────────────────────────────────────────────────────────────
# Dispatcher 级确定性拦截（不依赖 LLM 判断）
# ──────────────────────────────────────────────────────────────────────────────

# 明确的任务意图动词/词组（中文）
_TASK_INTENT_RE = re.compile(
    r"帮(?:我|你|我们)|给(?:我|我们)|替(?:我|我们)|"
    r"新建|创建|创一个|建一个|建立|生成|写|发送|发一条|发消息|发飞书|"
    r"提醒我|查询|查一下|分析|整理|备份|执行|运行",
    re.I,
)

# 时刻锚定词（「的时候」「时」「分」等表达）
_TIME_ANCHOR_RE = re.compile(
    r"(?:上午|下午|晚上|凌晨|早上)?\d{1,2}\s*[:：时]\s*\d{2}"
    r"|(?:\d+)\s*(?:分钟?|小时|hour|min)(?:\s*(?:后|之后|以后))"
    r"|明天|后天",
    re.I,
)


# 时刻核心部分（HH:MM 或 HH点MM / HH点 格式）
_RE_CLOCK = (
    r"\d{1,2}\s*(?:[:：时点])\s*\d{0,2}\s*(?:分)?"
)
# 完整时间词（可选日期 + 时段 + 时刻 + 后缀）
_RE_TIME_CLAUSE = re.compile(
    r"(?:"
    r"(?:(?:今天|今日|明天|明日)\s*)?"
    r"(?:上午|下午|晚上|凌晨|早上|早)?\s*"
    + _RE_CLOCK +
    r"\s*(?:的时候|时)?"
    r"|"
    r"\d+\s*(?:分钟?|小时)(?:\s*(?:后|之后|以后))?"
    r"|"
    r"(?:明天|后天)"
    r")",
    re.I,
)

# 开头可选人称「(请)?你（可以）（到/在）」+ 时间
_RE_LEADING_PERSON_TIME = re.compile(
    r"^(?:请\s*)?你\s*(?:可以\s*)?(?:到|在)?\s*"
    r"(?:(?:今天|今日|明天|明日)\s*)?"
    r"(?:上午|下午|晚上|凌晨|早上|早)?\s*"
    + _RE_CLOCK +
    r"\s*(?:的时候|时)?\s*",
    re.I,
)

# 「在 + 时间 + 的时候/时」句中嵌入子句
_RE_EMBEDDED_TIME_CLAUSE = re.compile(
    r"在\s*(?:(?:今天|今日|明天|明日)\s*)?"
    r"(?:上午|下午|晚上|凌晨|早上|早)?\s*"
    + _RE_CLOCK +
    r"\s*(?:的时候|时)?\s*",
    re.I,
)


def _strip_time_prefix(text: str) -> str:
    """
    从用户输入中剔除调度时间表达，只保留实际任务描述（用作 deferred intent）。

    处理的典型形态（非穷举）：
      - 「你上午11:43的时候帮我...」
      - 「请你在下午14:25的时候帮我...」
      - 「30分钟后帮我...」
      - 「明天下午3点帮我...」
      - 句中嵌入：「帮我在14:25时建个文件」→ 去掉「在14:25时」
    """
    t = text.strip()

    # 优先：开头含「(请)你...时刻」
    stripped = _RE_LEADING_PERSON_TIME.sub("", t).strip()
    if stripped and stripped != t:
        return stripped

    # 开头是纯相对时间「N分钟后」「N小时后」
    stripped2 = re.sub(
        r"^\d+\s*(?:分钟?|小时)(?:\s*(?:后|之后|以后))?\s*",
        "",
        t,
    )
    if stripped2 and stripped2 != t:
        return stripped2.strip()

    # 句中嵌入「在下午14:25的时候」——直接删掉该子句
    stripped3 = _RE_EMBEDDED_TIME_CLAUSE.sub("", t).strip()
    if stripped3 and stripped3 != t:
        return stripped3

    # 其余：直接删掉所有匹配到的时刻表达（宽松兜底）
    stripped4 = _RE_TIME_CLAUSE.sub("", t).strip()
    if stripped4 and stripped4 != t:
        return stripped4

    return t


def try_generic_timed_task_intercept(
    user_message: str,
    *,
    min_future_seconds: float = 30.0,
    lark_chat_id: str | None = None,
) -> str | None:
    """
    确定性定时任务拦截器（在 dispatcher/ws_server 里于 run_agent 之前调用）。

    检测逻辑：
      1. 消息中含**未来时刻**表达（绝对或相对）
      2. 消息中含明确**任务意图**动词/词组
      3. 该时刻距当前 > ``min_future_seconds`` 秒

    满足以上条件时，直接注册定时任务并返回用户可见的确认文字；
    否则返回 None（让上游继续走 run_agent）。

    Args:
        user_message: 原始用户输入
        min_future_seconds: 最小未来时差（秒），避免几秒后的任务被误拦截
        lark_chat_id: 可选，用户所在 Lark 会话 ID；到点时将飞书回推结果

    Returns:
        str: 注册成功的回复文字（包含 fire_at_display 与 intent 预览）
        None: 未检测到定时任务意图，让上游继续处理
    """
    text = (user_message or "").strip()
    if not text:
        return None

    # ① 必须含任务意图关键词
    if not _TASK_INTENT_RE.search(text):
        return None

    # ② 必须含时刻表达
    if not _TIME_ANCHOR_RE.search(text):
        return None

    # ③ 解析出的时刻必须在未来
    fire_at = _parse_fire_at_from_text(text)
    if fire_at is None:
        return None

    now = _now_sh()
    delta_sec = (fire_at - now).total_seconds()
    if delta_sec < min_future_seconds:
        # 时刻太近或已过，不拦截——让 run_agent 即时处理
        return None

    # ④ 提取 intent：剔除时间前缀，保留实际任务描述
    intent = _strip_time_prefix(text)

    # ⑤ 注册任务（含 Lark 会话信息）
    result = _register_job(fire_at, intent, lark_chat_id=lark_chat_id)
    if not result.get("ok"):
        # 注册失败：让 run_agent 兜底
        logger.warning("[deferred-intercept] 注册失败（将走 run_agent）: %s", result.get("error"))
        return None

    fire_display = result.get("fire_at_display", fire_at.strftime("%Y-%m-%d %H:%M:%S"))
    intent_preview = intent[:80] + ("..." if len(intent) > 80 else "")

    # 根据是否有 Lark 渠道调整提示文字
    if lark_chat_id:
        notify_note = "到点时 L3 后台 Agent 将自动执行，并通过**飞书**把结果发回本会话。"
    else:
        notify_note = "到点时 L3 后台 Agent 将自动执行，无需桌面客户端。"

    reply = (
        f"✅ 已为您注册定时任务！\n\n"
        f"**执行时刻**：{fire_display}\n"
        f"**任务内容**：{intent_preview}\n\n"
        f"{notify_note}\n"
        f"任务 ID：`{result.get('job_id', '')}`"
    )
    logger.info(
        "[deferred-intercept] 拦截并注册 fire_at=%s lark_chat_id=%s intent=%s",
        fire_display, lark_chat_id or "-", intent[:60],
    )
    return reply
