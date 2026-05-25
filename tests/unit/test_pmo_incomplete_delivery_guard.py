"""PMO：未完成读表/推送时禁止 Final Answer（含 JSON 笔记早停）。"""
from __future__ import annotations

import json
from unittest.mock import patch

from l3_node.agent_core import (
    PMO_BRANCH_A_REQUIRED_VIEW_IDS,
    _pmo_delivery_read_checklist_met,
    _pmo_init_required_views_from_bi,
    _pmo_maybe_record_fs_read_view,
    _pmo_view_ids_from_bi_observation,
    _reject_pmo_branch_a_incomplete_delivery_guard,
)
from l3_node.engine.hooks_pipeline import PipelineContext


def _branch_a_ctx(**meta: object) -> PipelineContext:
    base = {
        "_implicit_channel": "pmo_copilot_cli",
        "_pmo_bi_project_context_ok": True,
        "_pmo_required_view_ids": list(PMO_BRANCH_A_REQUIRED_VIEW_IDS),
        "_pmo_files_read": [],
        "_pmo_vewpI8lyYw_fs_read_ok_count": 0,
        "_pmo_notifier_invoke_count": 0,
    }
    base.update(meta)
    return PipelineContext("", metadata=base)


def test_view_ids_from_bi_observation() -> None:
    obs = json.dumps(
        {
            "status": "success",
            "files": [
                "01_x_vew8TxMcSh.md",
                "03_x_vewpI8lyYw.md",
                "05_x_vewCz1FFJi.md",
            ],
        }
    )
    ids = _pmo_view_ids_from_bi_observation(obs)
    assert "vew8TxMcSh" in ids
    assert "vewpI8lyYw" in ids


def test_delivery_checklist_requires_all_views_and_03_x3() -> None:
    ctx = _branch_a_ctx(
        _pmo_files_read=list(PMO_BRANCH_A_REQUIRED_VIEW_IDS),
        _pmo_vewpI8lyYw_fs_read_ok_count=2,
    )
    assert _pmo_delivery_read_checklist_met(ctx) is False
    ctx.metadata["_pmo_vewpI8lyYw_fs_read_ok_count"] = 3
    assert _pmo_delivery_read_checklist_met(ctx) is True


def test_rejects_pmo_table_notes_json_final_answer() -> None:
    ctx = _branch_a_ctx()
    messages: list = []
    ans = json.dumps({"pmo_table_notes_refresh": True, "tables_read_count": 3})
    with patch("l3_node.agent_core._pmo_branch_a_requires_bi_pull", lambda c: True):
        blocked = _reject_pmo_branch_a_incomplete_delivery_guard(
            ctx, messages, f"Final Answer: {ans}", ans, via="test"
        )
    assert blocked is True
    assert len(messages) == 2
    assert "PMO_TABLE_NOTES_JSON" in messages[1]["content"]


def test_allows_after_notify_ok() -> None:
    ctx = _branch_a_ctx(_pmo_atom_lark_notify_ok=True)
    messages: list = []
    with patch("l3_node.agent_core._pmo_branch_a_requires_bi_pull", lambda c: True):
        blocked = _reject_pmo_branch_a_incomplete_delivery_guard(
            ctx, messages, "Final Answer: 已推送。", "已推送。", via="test"
        )
    assert blocked is False


def test_fs_read_records_view_in_metadata() -> None:
    ctx = _branch_a_ctx()
    _pmo_init_required_views_from_bi(
        ctx,
        json.dumps({"status": "success", "files": ["03_a_vewpI8lyYw.md"]}),
    )
    inp = json.dumps({"file_path": r"C:\x\03_a_vewpI8lyYw.md"})
    _pmo_maybe_record_fs_read_view(ctx, inp, "x" * 200)
    assert "vewpI8lyYw" in ctx.metadata.get("_pmo_files_read", [])
