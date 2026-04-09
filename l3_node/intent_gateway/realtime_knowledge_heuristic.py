"""
实时外部知识需求：启发式快速判定（避免小模型超时导致无法 Tavily 预取）。
"""
from __future__ import annotations

import re

# 中英：时事/检索/行情/天气/版本文档等（偏保守，避免闲聊误触）
_REALTIME_HINT_RE = re.compile(
    r"(最近|最新|今天|昨日|本周|本月|今年|近期|时下|当前|实时|即时|刚刚|"
    r"时事|新闻|资讯|热点|大事|动态|发布|更新|股价|股票|大盘|指数|汇率|"
    r"天气|气温|降雨|台风|"
    r"赛程|比分|"
    r"发生了什么|怎么回事|有何动向|"
    r"联网|网上|搜索|检索|查一下|搜一下|"
    r"api\s*文档|版本说明|release\s*notes|changelog|"
    r"breaking\s*news|latest\s+news|what'?s\s+new|today'?s|stock\s+price|weather\s+now)",
    re.IGNORECASE,
)


def heuristic_requires_realtime_knowledge(user_input: str, classification_text: str) -> bool:
    """
    若用户/分类面命中「明显需要外部时效信息」的关键词，返回 True（不调用小模型）。
    """
    s = f"{user_input or ''}\n{classification_text or ''}".strip()
    if len(s) < 3:
        return False
    return bool(_REALTIME_HINT_RE.search(s))
