"""轻量意图信号（关键词），供招聘域动态后缀（recruitment_longform / hr_hint）等使用。"""
from __future__ import annotations

import re
from typing import Any

# 注意：勿含「飞书」——Lark/PMO 几乎每轮都会带飞书链或群内@，会把最近 N 轮 tail 误判为招聘域，
# 从而整段注入 HR 招聘 SKILL，通用机器人只剩招聘话术。
_RECRUIT = re.compile(
    r"招聘|职位|JD|简历|透析|Boss|无人值守|收网|打招呼|候选人|面试|offer|猎头",
    re.I,
)

# 飞书「通用机器人」入站：含 PMO / 拉表 / K11 表 token 等则**不要**走 HR 专用 process_lark_message，
# 也不要靠「多维表/同步」类弱关键词误拐进招聘。
_PMO_IM_ROUTE_RE = re.compile(
    r"PMO|pmo[_-]?copilot|pmo\s*bmo|宏观看板|atom[_:]?bi|bi_project|atom_bi_project|"
    r"需求池|项目进度|设计专用|pmo_lark_pull|pmo_lark|拉表|战报|播报|notifier|"
    r"atom_lark_notifier|包体优化|\bEpic\b|冒烟|Executive\s*Summary|综合战报|"
    r"tblfK9|tblNdv7|DiSnwVB1|ZItbw4om|B19Iww8t",
    re.I,
)

# 产研/看板式追问（含口头问某人「手头任务」），**不应**走招聘包或注入 HR 招聘总监长 SOP。
_WORK_TASK_OR_TEAM_STATUS_RE = re.compile(
    r"手头.{0,20}任务|"
    r"有什么任务|哪些任务|在做什[么麽]|正在做|手上有.*任务|名下.*任务|"
    r"谁.{0,10}(忙|在做)|谁在.*(做|负责)|负责人|分工|负荷|排期|进度怎么|Blocked|阻塞",
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


def user_message_suggests_pmo_or_bi_context(user_input: str) -> bool:
    """Lark IM 等场景：本轮用户话是否更像 PMO/BI/产研任务追问（应优先通用 Agent，而非 HR 包）。"""
    u = user_input or ""
    if _PMO_IM_ROUTE_RE.search(u):
        return True
    if _WORK_TASK_OR_TEAM_STATUS_RE.search(u):
        return True
    return False


def user_message_explicit_recruitment_intent(user_input: str) -> bool:
    """用户本轮话里是否**显式**出现招聘域关键词（与 PMO/任务追问并存时仍以招聘为准）。"""
    return bool(_RECRUIT.search(user_input or ""))
