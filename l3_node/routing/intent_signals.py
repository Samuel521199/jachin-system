"""轻量意图信号（关键词），供招聘域动态后缀（recruitment_longform / hr_hint）等使用。"""
from __future__ import annotations

import re
from typing import Any


_RECRUIT = re.compile(
    r"招聘|职位|JD|简历|透析|Boss|飞书|无人值守|收网|打招呼|候选人|面试|offer|猎头",
    re.I,
)


def user_message_suggests_recruitment_domain(user_input: str, prior_messages: list[dict[str, Any]] | None = None) -> bool:
    if _RECRUIT.search(user_input or ""):
        return True
    if not prior_messages:
        return False
    tail = prior_messages[-6:] if len(prior_messages) > 6 else prior_messages
    blob = "\n".join(str(m.get("content") or "") for m in tail if isinstance(m, dict))
    return bool(_RECRUIT.search(blob))
