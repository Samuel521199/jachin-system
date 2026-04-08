"""
L5 记忆整理定时提示与聊天侧配置（间隔天数、倒计时秒）。

状态文件：~/.jachin/memory/compact_schedule.json
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STATE_DIR = Path.home() / ".jachin" / "memory"
_STATE_PATH = _STATE_DIR / "compact_schedule.json"

_DEFAULT_INTERVAL_DAYS = 3
_DEFAULT_COUNTDOWN_SEC = 10
_PROMPT_COOLDOWN_SEC = 300.0


def _env_float(key: str, default: float) -> float:
    try:
        v = (os.environ.get(key) or "").strip()
        return float(v) if v else default
    except (TypeError, ValueError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        v = (os.environ.get(key) or "").strip()
        return int(v) if v else default
    except (TypeError, ValueError):
        return default


def default_interval_days() -> int:
    return max(1, _env_int("JACHIN_MEMORY_SCHEDULE_INTERVAL_DAYS", _DEFAULT_INTERVAL_DAYS))


def default_countdown_sec() -> int:
    return max(3, min(120, _env_int("JACHIN_MEMORY_SCHEDULE_COUNTDOWN_SEC", _DEFAULT_COUNTDOWN_SEC)))


def load_state() -> dict[str, Any]:
    if not _STATE_PATH.exists():
        now = time.time()
        st = {
            "interval_days": default_interval_days(),
            "countdown_sec": default_countdown_sec(),
            "last_compact_completed_ts": 0.0,
            "last_prompt_sent_ts": 0.0,
            "defer_until_ts": 0.0,
            "first_cycle_anchor_ts": now,
        }
        save_state(st)
        return st
    try:
        data = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not dict")
        now = time.time()
        anchor = float(data.get("first_cycle_anchor_ts") or 0)
        if anchor <= 0:
            anchor = now
        out = {
            "interval_days": max(1, int(data.get("interval_days", default_interval_days()))),
            "countdown_sec": max(3, min(120, int(data.get("countdown_sec", default_countdown_sec())))),
            "last_compact_completed_ts": float(data.get("last_compact_completed_ts") or 0),
            "last_prompt_sent_ts": float(data.get("last_prompt_sent_ts") or 0),
            "defer_until_ts": float(data.get("defer_until_ts") or 0),
            "first_cycle_anchor_ts": anchor,
        }
        if "first_cycle_anchor_ts" not in data:
            out["first_cycle_anchor_ts"] = anchor
            save_state(out)
        return out
    except Exception as e:
        logger.debug("[MemSchedule] 读取状态失败，使用默认: %s", e)
        now = time.time()
        return {
            "interval_days": default_interval_days(),
            "countdown_sec": default_countdown_sec(),
            "last_compact_completed_ts": 0.0,
            "last_prompt_sent_ts": 0.0,
            "defer_until_ts": 0.0,
            "first_cycle_anchor_ts": now,
        }


def save_state(data: dict[str, Any]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


_RE_INTERVAL_DAYS = re.compile(
    r"(?:记忆|本地记忆)?\s*(?:整理|坍缩|合并|压缩)?\s*间隔\s*(?:改|设置|设为|调整|改成|改为)?\s*(\d+)\s*天",
    re.I,
)
_RE_COUNTDOWN = re.compile(
    r"(?:记忆整理|记忆坍缩|本地记忆)?\s*(?:倒计时|自动开始|秒后开始)\s*(?:改|设置|设为)?\s*(\d+)\s*秒",
    re.I,
)
# 须含「整理/坍缩/合并」之一，避免误匹配「推迟发货」等；(?<!不) 降低「不要推迟…」误触
_RE_DEFER = re.compile(
    r"(?<!不)(?<!别)(?<!莫)推迟(?:记忆|本地记忆)?(?:整理|坍缩|合并)(?:\s*(\d+)\s*小时)?",
    re.I,
)


def try_parse_defer_command(text: str) -> bool:
    """用户聊天中推迟整理：命中则写入 defer_until_ts 并返回 True。"""
    t = (text or "").strip()
    if not t:
        return False
    m = _RE_DEFER.search(t)
    if not m:
        return False
    h_raw = m.group(1)
    try:
        h = float(h_raw) if h_raw else 24.0
    except (TypeError, ValueError):
        h = 24.0
    defer_hours(max(1.0, h))
    logger.info("[MemSchedule] 用户聊天推迟记忆整理 %.1f 小时", h)
    return True


def try_apply_chat_command(text: str) -> str | None:
    """
    从用户聊天中解析间隔/倒计时并落盘。命中则返回可展示的确认短句。
    """
    t = (text or "").strip()
    if not t:
        return None
    st = load_state()
    changed = False
    m1 = _RE_INTERVAL_DAYS.search(t)
    if m1:
        st["interval_days"] = max(1, int(m1.group(1)))
        changed = True
    m2 = _RE_COUNTDOWN.search(t)
    if m2:
        st["countdown_sec"] = max(3, min(120, int(m2.group(1))))
        changed = True
    if not changed:
        return None
    save_state(st)
    logger.info(
        "[MemSchedule] 已由聊天更新 interval_days=%s countdown_sec=%s",
        st["interval_days"],
        st["countdown_sec"],
    )
    return (
        f"已记录：记忆整理间隔 **{st['interval_days']}** 天；"
        f"到期提示后无人操作时 **{st['countdown_sec']}** 秒自动开始整理。"
    )


def is_due_for_scheduled_prompt() -> bool:
    if str(os.environ.get("JACHIN_MEMORY_SCHEDULE_ENABLED", "1")).strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return False
    st = load_state()
    now = time.time()
    if now < float(st.get("defer_until_ts") or 0):
        return False
    last = float(st.get("last_compact_completed_ts") or 0)
    interval_sec = float(st["interval_days"]) * 86400.0
    anchor = float(st.get("first_cycle_anchor_ts") or 0)
    if last > 0:
        return (now - last) >= interval_sec
    return (now - anchor) >= interval_sec


def should_send_prompt_now() -> bool:
    """是否应向客户端推送一次「是否开始整理」提示（含冷却）。"""
    if not is_due_for_scheduled_prompt():
        return False
    st = load_state()
    now = time.time()
    if now - float(st.get("last_prompt_sent_ts") or 0) < _PROMPT_COOLDOWN_SEC:
        return False
    return True


def record_prompt_sent() -> None:
    st = load_state()
    st["last_prompt_sent_ts"] = time.time()
    save_state(st)


def record_compact_completed() -> None:
    st = load_state()
    st["last_compact_completed_ts"] = time.time()
    st["defer_until_ts"] = 0.0
    save_state(st)


def defer_hours(hours: float = 24.0) -> None:
    st = load_state()
    st["defer_until_ts"] = time.time() + max(3600.0, float(hours) * 3600.0)
    save_state(st)


def build_ws_prompt_payload() -> dict[str, Any]:
    st = load_state()
    body = (
        "【记忆整理】本地记忆已超过设定的整理周期。"
        f"可在 **{st['countdown_sec']}** 秒后自动开始「梦境合并」（后台执行，不打断当前聊天）；"
        "也可在聊天中发送 **立即整理记忆** 立刻执行，或 **推迟记忆整理** 推迟 24 小时。"
    )
    return {
        "step_type": "memory_compact_suggest",
        "content": body,
        "run_id": "",
        "metadata": {
            "countdown_sec": int(st["countdown_sec"]),
            "interval_days": int(st["interval_days"]),
            "kind": "scheduled_memory_compact",
        },
    }
