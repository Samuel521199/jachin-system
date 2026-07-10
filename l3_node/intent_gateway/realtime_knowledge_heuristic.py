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

# 本轮明显是「本地看图 / OCR」，与 Tavily 时事预取无关；须先于时效关键词判断
_VISION_LOCAL_PRIORITY_RE = re.compile(
    r"(读图|读图片|看图|识图|辨图|图片里|图像里|截图|截屏|\bocr\b|"
    r"辨认.*字|识别.*文字|提取.*文字|图中文字|图上写了|"
    r"附件.*图|上传.*图|多模态|这张?图|该图|如图所示)",
    re.IGNORECASE,
)


def user_input_should_skip_realtime_prefetch_for_vision(user_input: str) -> bool:
    """本轮用户句是否以本地视觉理解为主（不应触发全网实时预取）。"""
    u = (user_input or "").strip()
    if len(u) < 2:
        return False
    return bool(_VISION_LOCAL_PRIORITY_RE.search(u))


def heuristic_requires_realtime_knowledge(user_input: str, classification_text: str) -> bool:
    """
    若命中「明显需要外部时效信息」的关键词，返回 True（不调用小模型）。

    仅以**本轮 user_input** 为主匹配；不再把 short_memory / classification 全文拼进来，
    否则历史摘要里的「新闻/检索」会污染「读图」等无关轮次。
    user_input 极短且几乎为空时，才回退用 classification_text（极少见）。
    """
    ui = (user_input or "").strip()
    if user_input_should_skip_realtime_prefetch_for_vision(ui):
        return False
    if len(ui) >= 3:
        return bool(_REALTIME_HINT_RE.search(ui))
    ct = (classification_text or "").strip()
    if len(ct) >= 8:
        return bool(_REALTIME_HINT_RE.search(ct))
    return False
