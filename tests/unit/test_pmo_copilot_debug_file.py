"""PMO 人类可读调试日志格式。"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from l3_node import pmo_copilot_debug_file as dbg


@pytest.fixture(autouse=True)
def _reset_debug_module():
    dbg._pending_action.clear()
    dbg._session.clear()
    old = os.environ.pop("JACHIN_PMO_COPILOT_DEBUG_LOG", None)
    yield
    dbg._pending_action.clear()
    dbg._session.clear()
    if old is not None:
        os.environ["JACHIN_PMO_COPILOT_DEBUG_LOG"] = old
    else:
        os.environ.pop("JACHIN_PMO_COPILOT_DEBUG_LOG", None)


def test_format_bi_round_lists_tables_and_output_dir(tmp_path: Path):
    log = tmp_path / "pmo.txt"
    dbg.init_pmo_debug_session(
        log_path=log,
        user_message="跑宏观看板",
        correlation_id="abc",
        max_iterations=26,
    )
    obs = json.dumps(
        {
            "status": "success",
            "output_dir": "C:/Users/x/.jachin/data/pmo_tables/run1",
            "files": [
                "需求进度_vewpI8lyYw.md",
                "MANIFEST.json",
                "人员矩阵_vewCz1FFJi.md",
            ],
            "nodes": [
                {"view_id_hint": "vewpI8lyYw", "title": "需求进度"},
                {"view_id_hint": "vewCz1FFJi", "title": "人员矩阵"},
            ],
        },
        ensure_ascii=False,
    )
    dbg.append_pmo_debug_action(
        tool="mcp:atom_bi_project_context",
        inp='{"wiki_urls":["https://example.com/wiki"]}',
        iteration=0,
        run_id="run1",
    )
    dbg.append_pmo_debug_observation(
        tool="mcp:atom_bi_project_context",
        observation_full=obs,
        iteration=0,
        run_id="run1",
    )
    text = log.read_text(encoding="utf-8")
    assert "【第 1 / 26 轮】" in text
    assert "从飞书拉取多维表并落盘" in text
    assert "落盘目录: C:/Users/x/.jachin/data/pmo_tables/run1" in text
    assert "需求进度" in text
    assert "vewpI8lyYw" in text
    assert "人员矩阵" in text
    assert "MANIFEST.json" not in text or "需求进度_vewpI8lyYw.md" in text


def test_format_fs_read_success_and_error(tmp_path: Path):
    log = tmp_path / "pmo.txt"
    dbg.init_pmo_debug_session(log_path=log, user_message="x", max_iterations=10)

    ok_obs = "| col |\n| --- |\n| a |\n| b |\n" + "x" * 200
    dbg.append_pmo_debug_action(
        tool="core:fs_read",
        inp='{"file_path":"data/pmo/需求进度.md"}',
        iteration=1,
        run_id="r",
    )
    dbg.append_pmo_debug_observation(
        tool="core:fs_read",
        observation_full=ok_obs,
        iteration=1,
        run_id="r",
    )
    dbg.append_pmo_debug_action(
        tool="core:fs_read",
        inp='{"file_path":"missing.md"}',
        iteration=2,
        run_id="r",
    )
    dbg.append_pmo_debug_observation(
        tool="core:fs_read",
        observation_full="File not found: missing.md",
        iteration=2,
        run_id="r",
    )
    text = log.read_text(encoding="utf-8")
    assert "【第 2 / 10 轮】" in text
    assert "读到约" in text
    assert "【第 3 / 10 轮】" in text
    assert "读文件失败" in text
    assert "⚠️ 报错" in text


def test_fs_read_success_does_not_flag_settimeout_in_table(tmp_path: Path):
    log = tmp_path / "pmo.txt"
    dbg.init_pmo_debug_session(log_path=log, user_message="x", max_iterations=10)
    obs = (
        "| req |\n| --- |\n| row |\n"
        "Requirement: 清理未清除的 setTimeout · Sprint: 2026/03/30\n"
    ) + "x" * 300
    dbg.append_pmo_debug_action(
        tool="core:fs_read",
        inp='{"file_path":"03_vewpI8lyYw.md"}',
        iteration=0,
        run_id="r",
    )
    dbg.append_pmo_debug_observation(
        tool="core:fs_read",
        observation_full=obs,
        iteration=0,
        run_id="r",
    )
    text = log.read_text(encoding="utf-8")
    assert "读到约" in text
    assert "报错: 无" in text
    assert "⚠️ 报错" not in text


def test_finalize_appends_task_end(tmp_path: Path):
    log = tmp_path / "pmo.txt"
    dbg.init_pmo_debug_session(log_path=log, user_message="done", max_iterations=5)
    dbg.finalize_pmo_debug_log("战报已生成")
    text = log.read_text(encoding="utf-8")
    assert "【任务结束】" in text
    assert "战报已生成" in text
