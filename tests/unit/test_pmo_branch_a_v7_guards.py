"""PMO 分支 A v7 守卫：单次推送、防 INIT 漂移、大颗粒度探针、DB 就绪跳过拉表。"""
from __future__ import annotations

import json

from l3_node.agent_core import (
    PipelineContext,
    PMO_BRANCH_A_PERSONNEL_SSOT_VIEW,
    _pmo_append_draft_gfm_hint_after_db_query,
    _pmo_append_react_budget_warning,
    _pmo_branch_a_blocked_force_assembly_round,
    _pmo_branch_a_blocked_init_tools_during_analysis,
    _pmo_branch_a_blocked_invalid_field_sql,
    _pmo_branch_a_blocked_premature_lark_observation,
    _pmo_branch_a_blocked_duplicate_step1_map,
    _pmo_branch_a_blocked_rerun_db_after_markdown_block,
    _pmo_branch_a_delivery_complete,
    _pmo_branch_a_missing_cross_analysis,
    _pmo_branch_a_notifier_markdown_is_complete,
    _pmo_branch_a_push_prerequisites_met,
    _pmo_branch_a_requires_bi_pull,
    _pmo_assistant_thought_has_all_three_tables,
    _pmo_extract_gfm_draft_fingerprint,
    _pmo_final_answer_falsely_claims_lark_sent,
    _pmo_markdown_incomplete_system_nudge,
    _pmo_notifier_markdown_section_format_examples,
    _pmo_track_db_query_sql,
    _pmo_track_notifier_chat_success,
    _reject_pmo_branch_a_analysis_incomplete_delivery_guard,
    _reject_pmo_branch_a_force_push_exit_guard,
    _reject_pmo_branch_a_init_completion_guard,
)
from unittest.mock import patch


def _ctx(**meta) -> PipelineContext:
    base = {"_implicit_channel": "pmo_copilot_cli"}
    base.update(meta)
    return PipelineContext("", metadata=base)


def _full_mc() -> str:
    return """**📊 需求进度全览**
| 需求名称 | 时间跨度 | 参与人 | 完成度 | 状态 |
| --- | --- | --- | --- | --- |
| Epic A | 05/01→05/25 | Ethan | [▓▓░░] 20% | 🔵 进行中 |

**👥 人员任务矩阵**
| p | t | s |
| --- | --- | --- |
| Ethan | task | ✅ |

**📦 版本发布需求映射**
| 项目 | 状态 |
| --- | --- |
| ⚠️ 版本目标字段未填写 | 161 条 Version Goal 为空 |

📋 本次数据质量：需求进度 ✅ | 人员矩阵 ✅ | 版本映射 ⚠️
"""


def _probes_complete_meta() -> dict:
    return {
        "pmo_db_ready": True,
        "pmo_analysis_only": True,
        "_pmo_db_query_count": 10,
        "_pmo_views_queried": {
            "vewpI8lyYw",
            "vewCz1FFJi",
            "vew8TxMcSh",
        },
        "_pmo_analysis_probes": {
            "sprint": True,
            "status": True,
            "personnel": True,
            "personnel_kanban": True,
            "version": True,
            "epic": True,
            "cross_view_6a": True,
            "cross_view_6b": True,
        },
    }


def test_bi_pull_skipped_when_db_ready() -> None:
    ctx = _ctx(pmo_db_ready=True)
    assert _pmo_branch_a_requires_bi_pull(ctx) is False


def test_bi_pull_required_when_db_not_ready() -> None:
    ctx = _ctx(pmo_db_ready=False, pmo_analysis_only=False)
    assert _pmo_branch_a_requires_bi_pull(ctx) is True


def test_notifier_complete_with_degraded_version_section() -> None:
    mc = _full_mc()
    inp = json.dumps({"title": "t", "markdown_content": mc}, ensure_ascii=False)
    assert _pmo_branch_a_notifier_markdown_is_complete(inp) is True


def test_premature_block_when_analysis_incomplete() -> None:
    ctx = _ctx(pmo_db_ready=True, pmo_analysis_only=True, _pmo_db_query_count=3)
    inp = json.dumps({"title": "t", "markdown_content": _full_mc()}, ensure_ascii=False)
    obs = _pmo_branch_a_blocked_premature_lark_observation(inp, ctx)
    assert obs is not None
    d = json.loads(obs)
    assert d.get("error") == "pmo_premature_notifier_blocked"
    assert "交叉分析" in d.get("msg", "") or "探针" in d.get("msg", "")


def test_premature_block_applies_in_analysis_only_mode() -> None:
    ctx = _ctx(**_probes_complete_meta())
    inp = json.dumps(
        {
            "title": "t",
            "markdown_content": "only partial",
            "chat_id": "oc_437c98d11106295fb10751a5481ee465",
        },
        ensure_ascii=False,
    )
    obs = _pmo_branch_a_blocked_premature_lark_observation(inp, ctx)
    assert obs is not None
    assert json.loads(obs).get("error") == "pmo_premature_notifier_blocked"


def test_push_prerequisites_require_epic_probe() -> None:
    ctx = _ctx(
        pmo_db_ready=True,
        _pmo_db_query_count=10,
        _pmo_analysis_probes={
            "sprint": True,
            "status": True,
            "personnel": True,
            "version": True,
            "epic": False,
        },
    )
    assert _pmo_branch_a_push_prerequisites_met(ctx) is False


def test_track_epic_probe_from_sql() -> None:
    ctx = _ctx()
    sql = (
        "SELECT Requirement FROM pmo_raw_records WHERE source_view = 'vewpI8lyYw' "
        "AND json_extract(fields, '$.\"父记录\"[0].text') IS NULL "
        "AND json_extract(fields, '$.Requirement') NOT IN ('开发', '美术', '产品')"
    )
    inp = json.dumps({"sql": sql}, ensure_ascii=False)
    _pmo_track_db_query_sql(ctx, "core:db_query", inp)
    assert (ctx.metadata.get("_pmo_analysis_probes") or {}).get("epic") is True


def test_track_personnel_probe_from_person_in_charge() -> None:
    ctx = _ctx()
    sql = (
        "SELECT json_extract(fields, '$.\"Person in charge/Participant\"') AS owner, COUNT(*) "
        "FROM pmo_raw_records WHERE source_view = 'vewpI8lyYw' GROUP BY owner"
    )
    inp = json.dumps({"sql": sql}, ensure_ascii=False)
    _pmo_track_db_query_sql(ctx, "core:db_query", inp)
    probes = ctx.metadata.get("_pmo_analysis_probes") or {}
    assert probes.get("personnel") is not True
    assert probes.get("personnel_vewp_only") is True


def test_track_personnel_kanban_from_vewCz1FFJi() -> None:
    ctx = _ctx()
    sql = (
        "SELECT j.value AS person, COUNT(*) FROM pmo_raw_records r, "
        "json_each(json_extract(r.fields, '$.\"Person in charge/Participant\"'), '$.name') AS j "
        f"WHERE r.source_view = '{PMO_BRANCH_A_PERSONNEL_SSOT_VIEW}' GROUP BY person"
    )
    inp = json.dumps({"sql": sql}, ensure_ascii=False)
    _pmo_track_db_query_sql(ctx, "core:db_query", inp)
    probes = ctx.metadata.get("_pmo_analysis_probes") or {}
    assert probes.get("personnel_kanban") is True
    assert probes.get("personnel") is True
    assert PMO_BRANCH_A_PERSONNEL_SSOT_VIEW in (ctx.metadata.get("_pmo_views_queried") or set())


def test_push_blocked_without_cross_views() -> None:
    ctx = _ctx(
        pmo_db_ready=True,
        _pmo_db_query_count=12,
        _pmo_views_queried={"vewpI8lyYw"},
        _pmo_analysis_probes={
            "sprint": True,
            "status": True,
            "personnel": True,
            "personnel_kanban": True,
            "version": True,
            "epic": True,
        },
    )
    assert _pmo_branch_a_push_prerequisites_met(ctx) is False
    missing = _pmo_branch_a_missing_cross_analysis(ctx)
    assert any("vewCz1FFJi" in m for m in missing)


def test_epic_probe_requires_parent_filter() -> None:
    ctx = _ctx()
    sql = (
        "SELECT json_extract(fields, '$.Requirement') AS epic, COUNT(*) "
        "FROM pmo_raw_records WHERE source_view = 'vewpI8lyYw' GROUP BY epic"
    )
    inp = json.dumps({"sql": sql}, ensure_ascii=False)
    _pmo_track_db_query_sql(ctx, "core:db_query", inp)
    assert (ctx.metadata.get("_pmo_analysis_probes") or {}).get("epic") is not True


def test_track_status_probe_from_chinese_status_field() -> None:
    ctx = _ctx()
    sql = (
        "SELECT json_extract(fields, '$.\"状态\"') AS status, COUNT(*) "
        "FROM pmo_raw_records WHERE source_view = 'vewpI8lyYw' GROUP BY status"
    )
    inp = json.dumps({"sql": sql}, ensure_ascii=False)
    _pmo_track_db_query_sql(ctx, "core:db_query", inp)
    assert (ctx.metadata.get("_pmo_analysis_probes") or {}).get("status") is True


def test_premature_block_shows_missing_markdown_sections() -> None:
    ctx = _ctx(**_probes_complete_meta())
    inp = json.dumps(
        {
            "title": "📊 PMO",
            "markdown_content": "**📊 需求进度全览**\n| a | b |\n| --- | --- |\n| x | y |",
            "chat_id": "oc_437c98d11106295fb10751a5481ee465",
        },
        ensure_ascii=False,
    )
    obs = _pmo_branch_a_blocked_premature_lark_observation(inp, ctx)
    d = json.loads(obs or "{}")
    assert d.get("reason") == "markdown_incomplete"
    assert "👥" in str(d.get("missing_sections"))


def test_blocks_init_tools_during_analysis() -> None:
    ctx = _ctx(pmo_db_ready=True, pmo_analysis_only=True, _pmo_db_query_count=3)
    obs = _pmo_branch_a_blocked_init_tools_during_analysis("mcp:atom_bi_project_context", ctx)
    assert obs is not None
    d = json.loads(obs)
    assert d.get("error") == "pmo_branch_a_init_switch_blocked"


def test_blocks_db_query_after_partial_push() -> None:
    ctx = _ctx(
        pmo_db_ready=True,
        pmo_analysis_only=True,
        _pmo_db_query_count=12,
        _pmo_notifier_chats_success=["oc_437c98d11106295fb10751a5481ee465"],
    )
    obs = _pmo_branch_a_blocked_init_tools_during_analysis("core:db_query", ctx)
    assert obs is not None
    assert json.loads(obs).get("error") == "pmo_post_push_analysis_blocked"


def test_blocks_all_tools_after_dual_delivery() -> None:
    ctx = _ctx(
        pmo_db_ready=True,
        pmo_analysis_only=True,
        _pmo_notifier_chats_success=[
            "oc_437c98d11106295fb10751a5481ee465",
            "oc_0e321f92d758ecb44aea5b499c90510b",
        ],
    )
    assert _pmo_branch_a_delivery_complete(ctx) is True
    obs = _pmo_branch_a_blocked_init_tools_during_analysis("core:db_query", ctx)
    assert json.loads(obs).get("error") == "pmo_post_delivery_tool_blocked"


def test_duplicate_delivery_blocked() -> None:
    ctx = _ctx(
        **_probes_complete_meta(),
        _pmo_notifier_chats_success=[
            "oc_437c98d11106295fb10751a5481ee465",
            "oc_0e321f92d758ecb44aea5b499c90510b",
        ],
    )
    inp = json.dumps(
        {
            "title": "t",
            "markdown_content": _full_mc(),
            "chat_id": "oc_437c98d11106295fb10751a5481ee465",
        },
        ensure_ascii=False,
    )
    obs = _pmo_branch_a_blocked_premature_lark_observation(inp, ctx)
    assert json.loads(obs).get("error") == "pmo_duplicate_delivery_blocked"


def test_track_notifier_chat_success() -> None:
    ctx = _ctx()
    inp = json.dumps(
        {"chat_id": "oc_437c98d11106295fb10751a5481ee465", "markdown_content": "x"},
        ensure_ascii=False,
    )
    _pmo_track_notifier_chat_success(ctx, inp, '{"status":"success"}')
    assert ctx.metadata.get("_pmo_notifier_chats_success") == [
        "oc_437c98d11106295fb10751a5481ee465"
    ]


def test_reject_init_completion_final_answer() -> None:
    ctx = _ctx(_pmo_db_query_count=5)
    messages: list = []
    blocked = _reject_pmo_branch_a_init_completion_guard(
        ctx,
        messages,
        "response",
        "已成功完成 INIT 镜像入库，pmo_mirror_import 返回 ok。",
        via="test",
    )
    assert blocked is True
    assert len(messages) == 2


def test_reject_analysis_premature_delivery_final_answer() -> None:
    ctx = _ctx(pmo_db_ready=True, pmo_analysis_only=True, _pmo_db_query_count=14)
    messages: list = []
    ans = (
        "已成功完成PMO-Copilot v7 SKILL中的分支A·宏观看板分析，并将战报推送到主群和监控群。"
        "战报主要内容包括：需求进度全览、人员任务矩阵。"
    )
    assert _pmo_final_answer_falsely_claims_lark_sent(ans) is True
    blocked = _reject_pmo_branch_a_analysis_incomplete_delivery_guard(
        ctx, messages, f"Final Answer: {ans}", ans, via="test"
    )
    assert blocked is True
    assert len(messages) == 2
    assert "双群" in messages[1]["content"]


def test_version_goal_empty_placeholder_table_passes_markdown_guard() -> None:
    mc = """**📊 需求进度全览**
| 需求名称 | 时间跨度 | 参与人 | 完成度 | 状态 |
| --- | --- | --- | --- | --- |
| Epic A | 05/01→05/25 | Ethan | [▓▓░░] 20% | 🔵 进行中 |

**👥 人员任务矩阵**
| p | t | s |
| --- | --- | --- |
| Celine | task | ✅ |

**📦 版本发布需求映射**
| 视图 | 记录总数 | Version Goal 填写数 | 填写率 | 说明 |
| --- | --- | --- | --- | --- |
| vew8TxMcSh / vewL9Mofgd | 100 | 0 | 0% | ⚠️ 原表字段全空，建议 PMO 补充版本目标 |
"""
    inp = json.dumps({"title": "t", "markdown_content": mc}, ensure_ascii=False)
    assert _pmo_branch_a_notifier_markdown_is_complete(inp) is True


def test_version_goal_text_only_fails_markdown_guard() -> None:
    mc = """**📊 需求进度全览**
| a | b |
| --- | --- |
| x | y |

**👥 人员任务矩阵**
| p | t |
| --- | --- |
| a | b |

Version Goal 填写率为 0%，建议补充。
"""
    inp = json.dumps({"title": "t", "markdown_content": mc}, ensure_ascii=False)
    assert _pmo_branch_a_notifier_markdown_is_complete(inp) is False


def test_premature_block_includes_format_examples_and_no_rerun_hint() -> None:
    ctx = _ctx(**_probes_complete_meta())
    inp = json.dumps(
        {
            "title": "📊 PMO",
            "markdown_content": "Version Goal 填写率为 0%，建议补充。",
            "chat_id": "oc_437c98d11106295fb10751a5481ee465",
        },
        ensure_ascii=False,
    )
    obs = _pmo_branch_a_blocked_premature_lark_observation(inp, ctx)
    d = json.loads(obs or "{}")
    assert d.get("reason") == "markdown_incomplete"
    msg = d.get("msg") or ""
    assert "❌ **禁止重跑 Step 1–7" in msg
    assert "markdown_content" in msg and "Thought" in msg
    assert ctx.metadata.get("_pmo_markdown_fix_phase") == "supplemental"
    assert d.get("markdown_fix_phase") == "supplemental"
    examples = _pmo_notifier_markdown_section_format_examples(d.get("missing_sections") or [])
    assert "Version Goal 全空时仍须建表" in examples or "vew8TxMcSh" in examples


def test_markdown_fix_only_blocks_rerun_db_query() -> None:
    ctx = _ctx(**_probes_complete_meta())
    ctx.metadata["_pmo_markdown_fix_phase"] = "final"
    obs = _pmo_branch_a_blocked_rerun_db_after_markdown_block("core:db_query", ctx)
    d = json.loads(obs or "{}")
    assert d.get("error") == "pmo_markdown_fix_only_db_blocked"
    assert "禁止" in (d.get("msg") or "")


def test_markdown_supplemental_allows_group_by_sql() -> None:
    ctx = _ctx(**_probes_complete_meta())
    ctx.metadata["_pmo_markdown_fix_phase"] = "supplemental"
    sql = (
        "SELECT json_extract(json_extract(fields, '$.\"状态\"'), '$[0].text') AS status_text, "
        "COUNT(*) AS cnt FROM pmo_raw_records WHERE source_view='vewpI8lyYw' GROUP BY status_text"
    )
    obs = _pmo_branch_a_blocked_rerun_db_after_markdown_block(
        "core:db_query", ctx, json.dumps({"sql": sql}, ensure_ascii=False)
    )
    assert obs is None
    assert int(ctx.metadata.get("_pmo_markdown_fix_supplemental_count") or 0) == 1


def test_markdown_supplemental_blocks_step1_map() -> None:
    ctx = _ctx(**_probes_complete_meta())
    ctx.metadata["_pmo_markdown_fix_phase"] = "supplemental"
    sql = "SELECT view_id, columns_json FROM pmo_views_meta ORDER BY view_id"
    obs = _pmo_branch_a_blocked_rerun_db_after_markdown_block(
        "core:db_query", ctx, json.dumps({"sql": sql}, ensure_ascii=False)
    )
    d = json.loads(obs or "{}")
    assert d.get("error") == "pmo_markdown_fix_supplemental_db_blocked"


def test_force_assembly_round_blocks_db_query_when_probes_complete() -> None:
    ctx = _ctx(**_probes_complete_meta())
    obs = _pmo_branch_a_blocked_force_assembly_round("core:db_query", ctx)
    d = json.loads(obs or "{}")
    assert d.get("error") == "pmo_force_assembly_round_blocked"
    assert ctx.metadata.get("_pmo_assembly_phase") == "writing"


def test_force_assembly_round_blocks_notifier_before_thought_preview() -> None:
    ctx = _ctx(**_probes_complete_meta())
    ctx.metadata["_pmo_assembly_phase"] = "writing"
    obs = _pmo_branch_a_blocked_force_assembly_round(
        "mcp:atom_lark_notifier",
        ctx,
        "Thought: 只有摘要\nAction: atom_lark_notifier",
    )
    d = json.loads(obs or "{}")
    assert d.get("error") == "pmo_assembly_round_notifier_blocked"


def test_force_assembly_round_marks_ready_when_thought_has_three_tables() -> None:
    ctx = _ctx(**_probes_complete_meta())
    ctx.metadata["_pmo_assembly_phase"] = "writing"
    thought = (
        "Thought:\n"
        "**📊 需求进度全览**\n| Epic | 状态 |\n| --- | --- |\n| A | 🔵 |\n"
        "**👥 人员任务矩阵**\n| p | t |\n| --- | --- |\n| Ethan | task |\n"
        "**📦 版本发布需求映射**\n| v | n |\n| --- | --- |\n| vew8 | 0 |"
    )
    assert _pmo_assistant_thought_has_all_three_tables(thought) is True
    obs = _pmo_branch_a_blocked_force_assembly_round(
        "mcp:atom_lark_notifier", ctx, thought
    )
    d = json.loads(obs or "{}")
    assert d.get("error") == "pmo_assembly_round_notifier_blocked"
    assert ctx.metadata.get("_pmo_assembly_phase") == "ready"
    assert "下一轮" in (d.get("msg") or "")


def test_markdown_incomplete_system_nudge_text() -> None:
    ctx = _ctx(pmo_db_ready=True, pmo_analysis_only=True, _implicit_channel="pmo_copilot_cli")
    obs = json.dumps(
        {
            "status": "error",
            "error": "pmo_premature_notifier_blocked",
            "reason": "markdown_incomplete",
            "missing_sections": ["📊 需求进度全览"],
        },
        ensure_ascii=False,
    )
    nudge = _pmo_markdown_incomplete_system_nudge(ctx, obs, "mcp:atom_lark_notifier")
    assert "系统校验" in nudge
    assert "不会" in nudge and "自动" in nudge
    assert "Thought 历史" in nudge


def test_budget_warning_when_remaining_rounds_low() -> None:
    meta = _probes_complete_meta()
    meta["_pmo_db_query_count"] = 8
    meta["_pmo_analysis_probes"] = dict(meta["_pmo_analysis_probes"])
    meta["_pmo_analysis_probes"]["epic"] = False
    ctx = _ctx(**meta)
    out = _pmo_append_react_budget_warning(
        ctx,
        "Observation ok",
        iteration=28,
        max_iterations=32,
    )
    assert "预算警告" in out
    assert "剩余 3 轮" in out
    assert "JOIN" in out


def test_track_db_query_count_with_core_prefix() -> None:
    """core:db_query 须正确累积 _pmo_db_query_count（修复 canonical tool id 匹配）。"""
    ctx = _ctx()
    sql = (
        "SELECT view_id, view_name, record_count, columns_json "
        "FROM pmo_views_meta ORDER BY view_id"
    )
    inp = json.dumps({"sql": sql}, ensure_ascii=False)
    _pmo_track_db_query_sql(ctx, "core:db_query", inp)
    assert ctx.metadata.get("_pmo_db_query_count") == 1
    assert ctx.metadata.get("_pmo_step1_map_done") is True


def test_analysis_incomplete_shows_recovery_hint_when_queries_done() -> None:
    ctx = _ctx(
        pmo_db_ready=True,
        pmo_analysis_only=True,
        _pmo_db_query_count=10,
        _pmo_views_queried={"vewpI8lyYw", "vewCz1FFJi"},
        _pmo_analysis_probes={
            "sprint": True,
            "status": True,
            "personnel": True,
            "personnel_kanban": True,
            "version": True,
            "epic": True,
        },
    )
    inp = json.dumps({"title": "t", "markdown_content": _full_mc()}, ensure_ascii=False)
    obs = _pmo_branch_a_blocked_premature_lark_observation(inp, ctx)
    d = json.loads(obs or "{}")
    assert d.get("reason") == "analysis_incomplete"
    assert "禁止从 Step1 重跑" in (d.get("msg") or "")


def test_markdown_fix_only_set_when_query_count_sufficient() -> None:
    meta = _probes_complete_meta()
    meta["_pmo_db_query_count"] = 10
    meta["_pmo_views_queried"] = {"vewpI8lyYw", "vewCz1FFJi"}
    ctx = _ctx(**meta)
    inp = json.dumps(
        {
            "title": "t",
            "markdown_content": "只有摘要，没有三表",
            "chat_id": "oc_437c98d11106295fb10751a5481ee465",
        },
        ensure_ascii=False,
    )
    obs = _pmo_branch_a_blocked_premature_lark_observation(inp, ctx)
    d = json.loads(obs or "{}")
    assert d.get("reason") == "markdown_incomplete"
    assert ctx.metadata.get("_pmo_markdown_fix_phase") == "supplemental"
    assert "补缺模式" in (d.get("msg") or "")
    assert "补缺 SQL" in (d.get("msg") or "")


def test_duplicate_step1_map_blocked_after_first_map() -> None:
    ctx = _ctx(
        pmo_db_ready=True,
        pmo_analysis_only=True,
        _pmo_step1_map_done=True,
        _pmo_db_query_count=8,
    )
    sql = "SELECT view_id, record_count, columns_json FROM pmo_views_meta"
    inp = json.dumps({"sql": sql}, ensure_ascii=False)
    obs = _pmo_branch_a_blocked_duplicate_step1_map("core:db_query", inp, ctx)
    assert obs is not None
    d = json.loads(obs)
    assert d.get("error") in ("pmo_step1_rerun_warn_blocked", "pmo_step1_rerun_blocked")
    assert "Step1" in (d.get("msg") or "")


def test_draft_gfm_hint_when_thought_missing_table() -> None:
    ctx = _ctx(pmo_db_ready=True, pmo_analysis_only=True)
    obs = _pmo_append_draft_gfm_hint_after_db_query(
        ctx,
        "Thought: Step3 完成，人员分布正常。",
        "Observation ok",
    )
    assert "草稿提醒" in obs
    assert "GFM" in obs


def test_draft_gfm_hint_skipped_when_table_present() -> None:
    ctx = _ctx(pmo_db_ready=True, pmo_analysis_only=True)
    obs = _pmo_append_draft_gfm_hint_after_db_query(
        ctx,
        "Thought: Step3\n| 姓名 | 任务 |\n| --- | --- |\n| Ethan | task |",
        "Observation ok",
    )
    assert obs == "Observation ok"


def test_budget_warning_skipped_when_prerequisites_met() -> None:
    ctx = _ctx(**_probes_complete_meta())
    out = _pmo_append_react_budget_warning(
        ctx,
        "Observation ok",
        iteration=28,
        max_iterations=32,
    )
    assert out == "Observation ok"


def test_personnel_kanban_requires_json_each_on_vewCz1FFJi() -> None:
    ctx = _ctx()
    sql = (
        'SELECT json_extract(fields, \'$."Person in charge/Participant"\') AS person, '
        "json_extract(fields, '$.Requirement') AS task "
        "FROM pmo_raw_records WHERE source_view = 'vewCz1FFJi'"
    )
    inp = json.dumps({"sql": sql}, ensure_ascii=False)
    _pmo_track_db_query_sql(ctx, "core:db_query", inp)
    probes = ctx.metadata.get("_pmo_analysis_probes") or {}
    assert probes.get("personnel_kanban") is not True
    assert probes.get("personnel_kanban_partial") is True


def test_status_probe_requires_group_by_aggregate() -> None:
    ctx = _ctx()
    sql = (
        "SELECT json_extract(fields, '$.状态') AS status, "
        "json_extract(fields, '$.Sprint') AS sprint "
        "FROM pmo_raw_records WHERE source_view = 'vewCz1FFJi'"
    )
    inp = json.dumps({"sql": sql}, ensure_ascii=False)
    _pmo_track_db_query_sql(ctx, "core:db_query", inp)
    probes = ctx.metadata.get("_pmo_analysis_probes") or {}
    assert probes.get("status") is not True
    assert probes.get("status_detail_only") is True


def test_version_probe_requires_count_not_limit_one() -> None:
    ctx = _ctx()
    sql = (
        'SELECT json_extract(fields, \'$."Version Goal"\') AS version_goal '
        "FROM pmo_raw_records WHERE source_view = 'vewpI8lyYw' LIMIT 1"
    )
    inp = json.dumps({"sql": sql}, ensure_ascii=False)
    _pmo_track_db_query_sql(ctx, "core:db_query", inp)
    probes = ctx.metadata.get("_pmo_analysis_probes") or {}
    assert probes.get("version") is not True
    assert probes.get("version_sample_only") is True


def test_cross_view_probes_tracked_from_step6_sql() -> None:
    ctx = _ctx()
    sql_6a = (
        "SELECT json_extract(fields, '$.Requirement') AS req FROM pmo_raw_records "
        "WHERE source_view = 'vewpI8lyYw' "
        "AND json_extract(json_extract(fields, '$.\"状态\"'), '$[0].text') = '🔴 延期' LIMIT 5"
    )
    sql_6b = (
        "SELECT COUNT(*) FROM pmo_raw_records WHERE source_view = 'vewCz1FFJi' "
        "AND fields LIKE '%Tongits%'"
    )
    _pmo_track_db_query_sql(ctx, "core:db_query", json.dumps({"sql": sql_6a}, ensure_ascii=False))
    _pmo_track_db_query_sql(ctx, "core:db_query", json.dumps({"sql": sql_6b}, ensure_ascii=False))
    probes = ctx.metadata.get("_pmo_analysis_probes") or {}
    assert probes.get("cross_view_6a") is True
    assert probes.get("cross_view_6b") is True


def test_invalid_field_sql_blocked_on_vewpI8lyYw() -> None:
    ctx = _ctx(pmo_db_ready=True, pmo_analysis_only=True)
    sql = (
        "SELECT json_extract(fields, '$.负责人') AS person, "
        "json_extract(fields, '$.需求名称') AS requirement "
        "FROM pmo_raw_records WHERE source_view = 'vewpI8lyYw'"
    )
    obs = _pmo_branch_a_blocked_invalid_field_sql(
        "core:db_query", json.dumps({"sql": sql}, ensure_ascii=False), ctx
    )
    assert obs is not None
    d = json.loads(obs)
    assert d.get("error") == "pmo_invalid_field_name_blocked"


def test_draft_duplicate_hint_when_gfm_unchanged() -> None:
    ctx = _ctx(pmo_db_ready=True, pmo_analysis_only=True)
    gfm = "Thought: Step3\n| alvintan | task | sprint |\n| --- | --- | --- |"
    ctx.metadata["_pmo_last_gfm_draft_fingerprint"] = _pmo_extract_gfm_draft_fingerprint(gfm)
    obs = _pmo_append_draft_gfm_hint_after_db_query(ctx, gfm, "Observation ok")
    assert "草稿重复" in obs


def test_force_push_exit_guard_blocks_data_quality_early_exit() -> None:
    ctx = _ctx(pmo_db_ready=True, pmo_analysis_only=True, _pmo_db_query_count=8)
    messages: list = []
    ans = (
        "已按 PMO-Copilot SKILL v7 分支 A 完成七步探针，三表草稿已生成。"
        "因数据质量问题（多数关键字段为 null），无法形成有效业务洞察。建议修复数据源后重试。"
    )
    blocked = _reject_pmo_branch_a_force_push_exit_guard(
        ctx, messages, f"Final Answer: {ans}", ans, via="test"
    )
    assert blocked is True
    assert len(messages) == 2
    assert "必须先尝试" in messages[1]["content"]
    assert "atom_lark_notifier" in messages[1]["content"]
