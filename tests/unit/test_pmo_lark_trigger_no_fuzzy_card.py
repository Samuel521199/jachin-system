"""PMO Lark：已移除模糊意图 1/2/3 确认卡片。"""
from __future__ import annotations

from unittest.mock import MagicMock

from l3_node.pmo_lark_trigger import try_pmo_lark_intercept


def test_fuzzy_project_question_does_not_intercept() -> None:
    """项目相关自然语言不再弹卡片，应交 run_agent。"""
    text = "你觉得现在项目表里有哪些人的信息填写还不够完整，我得去催他们填一下呢"
    out = try_pmo_lark_intercept(
        text,
        "oc_test_chat",
        "u1",
        MagicMock(return_value=True),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        [],
    )
    assert out is None


def test_exact_pmo_prefix_not_treated_as_fuzzy_card() -> None:
    from l3_node.pmo_lark_trigger import _PMO_EXACT_RE

    assert _PMO_EXACT_RE.search("/pmo 关注发版")
    assert _PMO_EXACT_RE.search("全量看板") is None or _PMO_EXACT_RE.search("全量看板")
