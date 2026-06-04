"""SubAgent role 规格解析（字符串或 inline dict）。"""
from __future__ import annotations

from typing import Any


def sub_agent_role_label(role: Any) -> str:
    """从 role 字符串或 inline dict 提取日志/RunReport 用标签。"""
    if isinstance(role, dict):
        raw = role.get("id") or role.get("name") or "dynamic_role"
        return str(raw).lower()
    return str(role or "default").lower()
