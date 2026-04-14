"""轻量意图信号（关键词），供招聘域动态后缀（recruitment_longform / hr_hint）等使用。"""
from __future__ import annotations

import re
from typing import Any


_RECRUIT = re.compile(
    r"招聘|职位|JD|简历|透析|Boss|飞书|无人值守|收网|打招呼|候选人|面试|offer|猎头",
    re.I,
)

# A 股 / 行情 / 基本面：与 core:akshare_* 强制路由配合（避免模型只用 mcp:fetch 臆造外链）
_RE_A_SHARE = re.compile(
    r"(?:"
    r"\b[03689]\d{5}\b|"  # 沪深 / 创业板 / 科创板 常见 6 位代码形态
    r"A股|沪深|创业板|科创板|个股|股票代码|"
    r"股价|行情走势|走势分析|K线|日线|周线|月线|前复权|"
    r"基本面|利润表|财报|市盈率|市净率|PE\b|PB\b|估值|"
    r"茅台|白酒板块|上证|深证"
    r")",
    re.I,
)


def user_message_suggests_a_share_analysis(user_input: str) -> bool:
    """用户是否在问 A 股行情/基本面类问题（需优先 AKShare 原生工具）。"""
    return bool(_RE_A_SHARE.search(user_input or ""))


def user_message_suggests_recruitment_domain(user_input: str, prior_messages: list[dict[str, Any]] | None = None) -> bool:
    if _RECRUIT.search(user_input or ""):
        return True
    if not prior_messages:
        return False
    tail = prior_messages[-6:] if len(prior_messages) > 6 else prior_messages
    blob = "\n".join(str(m.get("content") or "") for m in tail if isinstance(m, dict))
    return bool(_RECRUIT.search(blob))
