"""PMO 拉表 freshness：今日已入库则跳过阶段零。"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

from l3_node.pmo_init_runner import (
    pmo_expected_view_ids,
    pmo_resolve_refresh_pull_markdown,
)


def test_expected_view_ids_count() -> None:
    assert len(pmo_expected_view_ids()) == 12


def test_skip_pull_when_today_synced() -> None:
    today = date.today()
    with (
        patch("l3_node.pmo_init_runner.pmo_skip_pull_markdown_refresh", return_value=False),
        patch("l3_node.tools.pmo_db_tools.pmo_mirror_db_ready", return_value=True),
        patch("l3_node.pmo_init_runner._pmo_views_meta_coverage", return_value=(True, 12, 12)),
        patch("l3_node.pmo_init_runner._pmo_manifest_local_date", return_value=None),
        patch("l3_node.pmo_init_runner._pmo_latest_mirror_sync_local_date", return_value=today),
    ):
        need, reason = pmo_resolve_refresh_pull_markdown()
    assert need is False
    assert "今日" in reason or str(today) in reason


def test_pull_when_db_empty() -> None:
    with (
        patch("l3_node.pmo_init_runner.pmo_skip_pull_markdown_refresh", return_value=False),
        patch("l3_node.tools.pmo_db_tools.pmo_mirror_db_ready", return_value=False),
    ):
        need, _ = pmo_resolve_refresh_pull_markdown()
    assert need is True
