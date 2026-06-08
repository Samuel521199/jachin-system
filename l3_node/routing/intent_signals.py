"""轻量意图信号（关键词 + 会话上下文），供招聘/PMO 路由与 prompt 后缀使用。"""
from __future__ import annotations

import re
from typing import Any, Literal

LarkSessionDomain = Literal["hr_recruitment", "pmo_bi", "general"]

# 注意：勿含「飞书」——Lark/PMO 几乎每轮都会带飞书链或群内@，会把最近 N 轮 tail 误判为招聘域。
_RECRUIT = re.compile(
    r"招聘|职位|JD|简历|透析|Boss|无人值守|收网|打招呼|候选人|面试|offer|猎头",
    re.I,
)

_PMO_IM_ROUTE_RE = re.compile(
    r"PMO|pmo[_-]?copilot|pmo\s*bmo|宏观看板|atom[_:]?bi|bi_project|atom_bi_project|"
    r"需求池|项目进度|设计专用|pmo_lark_pull|pmo_lark|拉表|战报|播报|notifier|"
    r"atom_lark_notifier|包体优化|\bEpic\b|冒烟|Executive\s*Summary|综合战报|"
    r"tblfK9|tblNdv7|DiSnwVB1|ZItbw4om|B19Iww8t",
    re.I,
)

_WORK_TASK_OR_TEAM_STATUS_RE = re.compile(
    r"手头.{0,20}任务|"
    r"有什么任务|哪些任务|在做什[么麽]|正在做|手上有.*任务|名下.*任务|"
    r"谁.{0,10}(忙|在做)|谁在.*(做|负责)|负责人|分工|负荷|排期|进度怎么|Blocked|阻塞",
    re.I,
)

# 用户**显式**声明当前在做 PMO / 不要招聘（本轮强信号，不单独用于路由）
_EXPLICIT_PMO_SWITCH_RE = re.compile(
    r"PMO\s*任务|执行\s*PMO|pmo\s*任务|"
    r"忘了招聘|只会招聘|不要招聘|别管招聘|你不能忘了招聘|不要管招聘",
    re.I,
)

# 会话 tail 中的 PMO/战报痕迹（Observation、工具名、报告结构）
_SESSION_PMO_MARKERS_RE = re.compile(
    r"PMO项目|pmo_macro_dashboard|pmo_personnel_report|core:pmo_|"
    r"人员任务矩阵|Executive\s*Summary|深度交叉分析|current_sprint|"
    r"\d{4}/\d{2}/\d{2}-Sprint|Sprint.·|需求进度全览|版本发布需求映射|"
    r"pmo_raw_records|pmo_views_meta",
    re.I,
)

# 会话 tail 中的招聘流程痕迹（预检注入、工具、参数收集）
_SESSION_HR_MARKERS_RE = re.compile(
    r"Boss\s*选岗|jd_select|无人值守|add_automated_recruitment|atom_post_job_boss|"
    r"累计收网目标|透析触发|推荐牛人|沟通收简历|"
    r"岗位名称是什么|招聘类型|薪资待遇大概|学历要求|经验要求|"
    r"配置无人值守招聘|JD\s*配置|抓取简历",
    re.I,
)

_LARK_HR_RECRUITMENT_KEYWORDS = (
    "招聘", "发布", "发职位", "职位", "JD", "岗位", "简历", "打招呼", "推荐牛人",
    "同意", "确认发布", "直接发布", "收网", "抓取简历", "抓简历",
    "清除岗位", "清除全部", "清空岗位", "删除岗位",
    "post", "greet", "harvest",
)

_RE_A_SHARE = re.compile(
    r"(?:"
    r"\b[03689]\d{5}\b|"
    r"A股|沪深|创业板|科创板|个股|股票代码|"
    r"股价|行情走势|走势分析|K线|日线|周线|月线|前复权|"
    r"基本面|利润表|财报|市盈率|市净率|PE\b|PB\b|估值|"
    r"茅台|白酒板块|上证|深证"
    r")",
    re.I,
)

_SESSION_TAIL_N = 8
_DOMAIN_SCORE_MARGIN = 2


def user_message_suggests_a_share_analysis(user_input: str) -> bool:
    """用户是否在问 A 股行情/基本面类问题（需优先 AKShare 原生工具）。"""
    return bool(_RE_A_SHARE.search(user_input or ""))


def user_message_explicit_recruitment_intent(user_input: str) -> bool:
    """用户本轮话里是否**显式**出现招聘域关键词。"""
    return bool(_RECRUIT.search(user_input or ""))


def user_message_suggests_pmo_or_bi_context(user_input: str) -> bool:
    """本轮用户话是否**显式**像 PMO/BI/产研（不含口语追问词，追问靠会话上下文判）。"""
    u = user_input or ""
    if _PMO_IM_ROUTE_RE.search(u):
        return True
    if _WORK_TASK_OR_TEAM_STATUS_RE.search(u):
        return True
    if _EXPLICIT_PMO_SWITCH_RE.search(u):
        return True
    return False


def _session_tail_blob(prior_messages: list[dict[str, Any]] | None, *, n: int = _SESSION_TAIL_N) -> str:
    if not prior_messages:
        return ""
    tail = prior_messages[-n:] if len(prior_messages) > n else prior_messages
    return "\n".join(str(m.get("content") or "") for m in tail if isinstance(m, dict))


def _score_pmo_context(blob: str) -> int:
    if not blob:
        return 0
    score = len(_SESSION_PMO_MARKERS_RE.findall(blob))
    if _PMO_IM_ROUTE_RE.search(blob):
        score += 2
    if _WORK_TASK_OR_TEAM_STATUS_RE.search(blob):
        score += 1
    return score


def _score_hr_context(blob: str) -> int:
    if not blob:
        return 0
    score = len(_SESSION_HR_MARKERS_RE.findall(blob))
    if _RECRUIT.search(blob):
        score += 2
    if "【系统】本条为 **Boss 选岗" in blob or "【系统】用户要发布职位" in blob:
        score += 3
    return score


def infer_lark_session_domain(
    user_input: str,
    prior_messages: list[dict[str, Any]] | None = None,
) -> LarkSessionDomain:
    """
    根据**本轮话术 + 近期会话**推断飞书对话主线（非写死领域表）。

    - 本轮显式招聘 / Boss 选岗行 → hr_recruitment
    - 本轮显式 PMO/产研 → pmo_bi
    - 口语追问（如「还有什么不合理」）→ 看 session tail 哪条主线分更高
    - 打平或空会话 → general（不默认塞进招聘包）
    """
    u = (user_input or "").strip()
    if not u:
        return "general"

    if _EXPLICIT_PMO_SWITCH_RE.search(u):
        return "pmo_bi"
    if user_message_suggests_pmo_or_bi_context(u):
        return "pmo_bi"
    if user_message_explicit_recruitment_intent(u) or _text_hits_hr_recruitment_keywords(u):
        return "hr_recruitment"

    tail = _session_tail_blob(prior_messages)
    if tail:
        pmo_s = _score_pmo_context(tail)
        hr_s = _score_hr_context(tail)
        if pmo_s >= hr_s + _DOMAIN_SCORE_MARGIN:
            return "pmo_bi"
        if hr_s >= pmo_s + _DOMAIN_SCORE_MARGIN:
            return "hr_recruitment"

    try:
        from l3_node.im_channels.dispatcher import _line_parses_as_boss_job_select

        if _line_parses_as_boss_job_select(u):
            return "hr_recruitment"
    except Exception:
        pass

    return "general"


def user_message_suggests_recruitment_domain(
    user_input: str,
    prior_messages: list[dict[str, Any]] | None = None,
) -> bool:
    if user_message_explicit_recruitment_intent(user_input or ""):
        return True
    if not prior_messages:
        return False
    return infer_lark_session_domain(user_input or "", prior_messages) == "hr_recruitment"


def _text_hits_hr_recruitment_keywords(text: str) -> bool:
    u = (text or "").strip()
    if not u:
        return False
    t = u.lower()
    for kw in _LARK_HR_RECRUITMENT_KEYWORDS:
        if kw.lower() in t or kw in u:
            return True
    return False


def lark_message_should_use_hr_recruitment(
    user_input: str,
    *,
    prior_messages: list[dict[str, Any]] | None = None,
) -> bool:
    """是否走 HR process_lark_message：仅当上下文推断主线为招聘。"""
    return infer_lark_session_domain(user_input, prior_messages) == "hr_recruitment"
