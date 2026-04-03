"""
§12.2 L1 上架白名单：注册 skill_id 形态校验（可测、可挂 CI）。
"""
from __future__ import annotations

import re

# 与现有工具 id 风格对齐：core:* / mcp:* / skill.*
# bootstrap 使用 core.xxx；工具多为 mcp:xxx 与 skill.xxx
_SKILL_ID_RE = re.compile(
    r"^(?:core[.:][\w.\-]+|mcp:[\w.\-]+|skill\.[\w.\-]+)$",
    re.IGNORECASE,
)


def is_first_party_preflight_skill_id(skill_id: str) -> bool:
    sid = (skill_id or "").strip()
    if not sid or len(sid) > 128:
        return False
    return bool(_SKILL_ID_RE.match(sid))


def assert_preflight_skill_id_eligible(skill_id: str) -> None:
    """第三方/实验插件应显式 tier=third_party 并仅在沙箱开启时匹配。"""
    if not is_first_party_preflight_skill_id(skill_id):
        raise ValueError(
            f"intent_gateway preflight skill_id 不符合 first_party 形态: {skill_id!r} "
            "(期望 core:* / mcp:* / skill.*)"
        )
