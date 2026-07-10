"""飞书域路由：依据会话上下文，而非单句关键词写死。"""
from __future__ import annotations

from l3_node.routing.intent_signals import (
    infer_lark_session_domain,
    lark_message_should_use_hr_recruitment,
)


_PMO_TAIL = [
    {"role": "user", "content": "对 PMO 项目做深度交叉分析"},
    {
        "role": "assistant",
        "content": "WorkOrder: core:pmo_macro_dashboard_preview\nVerification evidence: Executive Summary 当前 Sprint 2026/06/01-Sprint 人员任务矩阵",
    },
]

_HR_TAIL = [
    {"role": "user", "content": "@_user_1 你好"},
    {
        "role": "assistant",
        "content": "请确认收网目标、打招呼人数、透析触发份数",
    },
    {"role": "user", "content": "还没定参数"},
]


def test_ambiguous_followup_follows_pmo_session():
    q = "@_user_1 还有其他什么不合理的地方吗"
    assert infer_lark_session_domain(q, _PMO_TAIL) == "pmo_bi"
    assert not lark_message_should_use_hr_recruitment(q, prior_messages=_PMO_TAIL)


def test_ambiguous_followup_follows_hr_session():
    q = "还有其他什么不合理的地方吗"
    assert infer_lark_session_domain(q, _HR_TAIL) == "hr_recruitment"
    assert lark_message_should_use_hr_recruitment(q, prior_messages=_HR_TAIL)


def test_empty_session_not_forced_pmo_or_hr():
    q = "还有其他什么不合理的地方吗"
    assert infer_lark_session_domain(q, []) == "general"
    assert not lark_message_should_use_hr_recruitment(q, prior_messages=[])


def test_explicit_recruitment_still_hr():
    assert lark_message_should_use_hr_recruitment("帮我发布 Java 岗位 20-30K")


def test_user_explicit_pmo_switch():
    assert infer_lark_session_domain("@_user_1 你记得你在执行PMO任务吗", _HR_TAIL) == "pmo_bi"
