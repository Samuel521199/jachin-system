"""
HR / 飞书 Lark 侧：精确遥控词表与谓词（分析 / 继续 / 进度 / 停止收网 inject 等）。

供 ``lark_workflow_command_interceptor`` 与 ``intent_clarification`` 的 HR 插件共用，避免循环依赖。
"""

from __future__ import annotations

import re

# 明显是「停掉招聘自动化/无人值守」，且无收网抓取语境 → 不抢 inject，走调度器/Agent
_RECRUITMENT_AUTOMATION_STOP = re.compile(
    r"(停止|关闭|取消|结束|不要)(?:了)?(?:所有|全部|整个)?(?:的)?(?:无人值守|自动化)?(?:招聘|无人值守|自动化招聘)"
    r"|(?:招聘|无人值守)(?:任务)?(?:停止|关闭|取消|结束)"
    r"|stop\s*(?:automation|recruitment|hiring|unattended)",
    re.I,
)
_HARVEST_OR_FETCH_CONTEXT = re.compile(
    r"收网|抓取|抓简历|抓人|别抓|停抓|harvest|pending|下载简历|打招呼|Playwright|爬虫|收简历|入库简历|简历抓取",
    re.I,
)
_HARVEST_STOP_PHRASES = re.compile(
    r"停止收网|暂停收网|结束收网|别抓了|先别抓|别抓先|停抓|停止抓取|停止抓简历|先停抓|"
    r"别跑了|停手|先停下|立刻停收网|马上停收网|强制停收网|"
    r"stop\s*harvest|halt\s*harvest",
    re.I,
)
_URGENT_STOP = re.compile(r"立刻停止|马上停止|立即停止|强制停止|立即停|马上停|立刻停", re.I)

_ANALYZE_EXACT = frozenset(
    {
        "开始分析",
        "分析简历",
        "分析",
        "再分析",
        "重新分析",
        "再去分析",
        "再跑分析",
        "立即分析",
        "马上分析",
        "跑透析镜",
        "执行透析镜",
        "启动透析镜",
        "开透析镜",
        "透析镜",
        "HR透析镜",
        "hr透析镜",
    }
)
_ANALYZE_EXTRA = re.compile(
    r"^(再|重新)(来)?(一遍|一下)?分析(简历)?$"
    r"|^(立即|马上)(开始)?分析(简历)?$"
    r"|再跑一次透析镜|重新跑透析镜|再跑透析镜|透析镜再跑",
    re.I,
)
# 非 HR 简历分析歧义（与 BI / 报表等区分）
ANALYZE_AMBIGUOUS = re.compile(
    r"BI分析|数据分析|报表分析|需求分析|商业智能|漏斗分析|转化分析|归因分析",
    re.I,
)

_CONTINUE_EXACT = frozenset(
    {
        "继续",
        "继续收网",
        "继续抓取",
        "继续抓简历",
        "恢复",
        "恢复收网",
        "恢复抓取",
    }
)
_CONTINUE_RE = re.compile(
    r"^(继续|恢复)(吧|一下)?$|^(继续|恢复)(收网|抓取|抓简历)(吧|一下)?$",
    re.I,
)

_STATUS_EXACT = frozenset(
    {
        "进度",
        "状态",
        "招聘进度",
        "当前进度",
        "什么进度",
        "进度如何",
        "查进度",
        "看一下进度",
        "看下进度",
    }
)
_STATUS_RE = re.compile(
    r"^(现在)?(什么|啥)进度[？?]?$"
    r"|^汇报(一下)?进度[？?]?$"
    r"|^当前(什么)?状态[？?]?$"
    r"|^招聘(什么)?状态[？?]?$",
    re.I,
)


def recruitment_stop_without_harvest_cue(text: str) -> bool:
    """True：主要是停招聘/无人值守，且句子里没有收网抓取侧线索 → 不 inject，交给 Agent。"""
    if not _RECRUITMENT_AUTOMATION_STOP.search(text):
        return False
    if _HARVEST_OR_FETCH_CONTEXT.search(text) or _HARVEST_STOP_PHRASES.search(text):
        return False
    return True


def matches_stop_harvest_inject(text: str) -> bool:
    """是否应对当前消息 inject STOP_HARVEST（秒停 Playwright 侧）。"""
    t = text.strip()
    if not t:
        return False
    t_lower = t.lower()
    if t in ("停止", "暂停") or t_lower == "stop":
        return True
    if _HARVEST_STOP_PHRASES.search(t):
        return True
    if _URGENT_STOP.search(t):
        return len(t) <= 24 or bool(_HARVEST_OR_FETCH_CONTEXT.search(t))
    return False


def matches_continue_command(text: str) -> bool:
    """飞书「继续」：恢复调度（与「继续分析」等区分）。"""
    t = text.strip()
    if not t:
        return False
    if "分析" in t and "收网" not in t and "抓取" not in t and "抓简历" not in t:
        return False
    if t in _CONTINUE_EXACT:
        return True
    if _CONTINUE_RE.match(t):
        return True
    return False


def matches_hr_analyze_command(text: str) -> bool:
    """是否走停收网 + 透析镜后台任务（精确命中）。"""
    t = text.strip()
    if not t:
        return False
    if t in _ANALYZE_EXACT:
        return True
    if _ANALYZE_EXTRA.search(t):
        return True
    if ANALYZE_AMBIGUOUS.search(t):
        return False
    return False


def matches_hr_status_briefing_command(text: str) -> bool:
    """HR 主动查进度（精确命中）。"""
    t = text.strip()
    if not t:
        return False
    if t in _STATUS_EXACT:
        return True
    if _STATUS_RE.match(t):
        return True
    return False
