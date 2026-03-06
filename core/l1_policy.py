"""
Jachin Nexus V2 - L1 策略同步状态

L1 心跳响应中的 global_banned_skills、subscription_status 等，供 L2 鉴权与权限合并使用。
"""
from __future__ import annotations

import threading
from typing import Any

_lock = threading.Lock()
_subscription_status: str = "active"
_global_banned_skills: list[str] = []
_raw_response: dict[str, Any] = {}


def apply_heartbeat_response(data: dict[str, Any]) -> None:
    """
    应用 L1 心跳响应中的策略。
    - subscription_status: "active" | "expired" | "trial" | ...
    - global_banned_skills: ["core:shell_exec", ...]
    """
    global _subscription_status, _global_banned_skills, _raw_response
    with _lock:
        _raw_response = dict(data or {})
        status = data.get("subscription_status")
        if status is not None and isinstance(status, str):
            _subscription_status = status.strip().lower()
        banned = data.get("global_banned_skills")
        if isinstance(banned, list):
            _global_banned_skills = [str(s).strip().lower() for s in banned if isinstance(s, str) and s.strip()]
        elif banned is not None:
            _global_banned_skills = []


def get_subscription_status() -> str:
    """当前订阅状态。expired 表示欠费/过期，L2 应返回 402"""
    with _lock:
        return _subscription_status


def is_subscription_expired() -> bool:
    """是否欠费/过期"""
    return get_subscription_status() in ("expired", "suspended", "overdue")


def get_global_banned_skills() -> list[str]:
    """L1 下发的全局封禁技能列表"""
    with _lock:
        return list(_global_banned_skills)


def is_skill_banned(skill_id: str) -> bool:
    """技能是否在全局封禁列表中（支持 core:xxx 与 xxx 互匹配）"""
    if not skill_id or not skill_id.strip():
        return False
    skill = skill_id.strip().lower()
    skill_norm = skill if ":" in skill else f"core:{skill}"
    banned = get_global_banned_skills()
    for b in banned:
        b_norm = b if ":" in b else f"core:{b}"
        if skill_norm == b_norm:
            return True
        if b_norm.startswith("core:") and skill_norm == b_norm[5:]:
            return True
        if skill_norm.startswith("core:") and b_norm == skill_norm[5:]:
            return True
    return False
