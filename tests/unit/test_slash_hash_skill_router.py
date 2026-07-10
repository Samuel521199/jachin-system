"""#*# / /#/ 显式 Skill 路由：前缀识别、PMO 语义匹配、战报回执缩短。"""

from __future__ import annotations

from l3_node.pmo_lark_trigger import _shorten_pmo_lark_dispatcher_reply
from l3_node.slash_hash_skill_router import (
    PMO_SKILL_ID,
    build_pmo_user_message_for_tail,
    extract_skill_route_tail,
    is_slash_hash_skill_invocation,
    resolve_pmo_action_key,
    resolve_skill_id_from_tail,
)


def test_hash_star_prefix_detected() -> None:
    assert is_slash_hash_skill_invocation("#*#告诉我项目进度")
    assert extract_skill_route_tail("#*#告诉我项目进度") == "告诉我项目进度"


def test_slash_hash_alias_still_works() -> None:
    assert is_slash_hash_skill_invocation("/#/跑一下 pmo")
    assert extract_skill_route_tail("/#/跑一下 pmo") == "跑一下 pmo"


def test_pmo_skill_matched_from_project_management_question() -> None:
    tail = "告诉我我们现在项目进行地怎么样了"
    assert resolve_skill_id_from_tail(tail) == PMO_SKILL_ID
    assert resolve_pmo_action_key(tail) == "full_board"


def test_pmo_anomaly_branch_from_tail() -> None:
    tail = "巡检异常人员阻塞情况"
    assert resolve_skill_id_from_tail(tail) == PMO_SKILL_ID
    assert resolve_pmo_action_key(tail) == "anomaly"


def test_build_pmo_message_insists_on_lark_card() -> None:
    msg = build_pmo_user_message_for_tail("项目情况怎么样", "full_board")
    assert "atom_lark_notifier" in msg
    assert "禁止" in msg and "User-facing result" in msg


def test_shorten_war_report_markdown_in_final_answer() -> None:
    md = """## Executive Summary
### 📊 需求进度全览
| 需求 | 状态 |
| --- | --- |
| Epic | 进行中 |
"""
    out = _shorten_pmo_lark_dispatcher_reply(md)
    assert "飞书消息卡片" in out
    assert "| Epic |" not in out
