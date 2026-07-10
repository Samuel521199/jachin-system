"""Worker B 字段对齐注入与 SQL 提示。"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from pathlib import Path

from l3_node.pmo_worker_b_field_align import (
    augment_worker_b_task,
    build_worker_b_field_alignment_block,
    extract_source_view_from_sql,
    field_align_hint_for_sql,
    load_worker_b_field_alignment,
)
from l3_node.pmo_multi_agent_queries import WORKER_B_TASK
from l3_node.tools.pmo_db_tools import ensure_pmo_schema, init_pmo_database


def test_extract_source_view():
    sql = "SELECT 1 FROM pmo_raw_records WHERE source_view = 'vew8TxMcSh' LIMIT 1"
    assert extract_source_view_from_sql(sql) == "vew8TxMcSh"


def test_augment_worker_b_task_inserts_alignment_block():
    aug = augment_worker_b_task(WORKER_B_TASK)
    assert "【字段对齐 · 启动时已核对 pmo_views_meta】" in aug
    assert "**字段对齐 · B-4 · `vewCz1FFJi`**" in aug
    assert aug.index("【字段对齐") < aug.index("**B-1 · 产品")


def test_field_align_from_sqlite_meta(tmp_path=None):
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "pmo_align.sqlite"
        os.environ["JACHIN_PMO_DB_PATH"] = str(db)
        try:
            init_pmo_database()
            conn = sqlite3.connect(str(db))
            ensure_pmo_schema(conn)
            cols = json.dumps(
                ["需求简述", "优先级", "Sprint", "责任人", "需求状态", "开发状态"],
                ensure_ascii=False,
            )
            conn.execute(
                "INSERT INTO pmo_views_meta (view_id, view_name, record_count, columns_json, synced_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("vew8TxMcSh", "K11 需求池", 50, cols, "2026-06-02"),
            )
            conn.commit()
            conn.close()

            load_worker_b_field_alignment(force=True)
            block = build_worker_b_field_alignment_block()
            assert "vew8TxMcSh" in block
            assert "需求简述" in block

            hint = field_align_hint_for_sql(
                "SELECT * FROM pmo_raw_records WHERE source_view = 'vew8TxMcSh' LIMIT 1"
            )
            assert hint is not None
            assert "字段对齐" in hint
            assert "需求简述" in hint
        finally:
            os.environ.pop("JACHIN_PMO_DB_PATH", None)
            load_worker_b_field_alignment(force=True)
