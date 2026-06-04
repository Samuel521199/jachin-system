"""Unit tests for PMO sprint epic report (case study SSOT)."""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from l3_node.tools import pmo_sprint_query as sq


def test_parent_text_string_and_array():
    assert sq.parent_text({"父记录": "开发"}) == "开发"
    assert sq.parent_text({"父记录": [{"text": "游戏加载"}]}) == "游戏加载"
    assert sq.parent_text({"父记录": None}) is None
    assert sq.parent_text({}) is None


def test_is_big_epic_rules():
    assert sq._is_big_epic(
        {"Requirement": "游戏加载", "父记录": None, "任务编号": "K11-1"}
    )
    assert not sq._is_big_epic({"Requirement": "开发", "父记录": "Epic", "任务编号": "x"})
    assert not sq._is_big_epic({"Requirement": "游戏加载", "父记录": "开发", "任务编号": "K11-1"})


def test_run_sprint_epic_report_empty_db(monkeypatch):
    monkeypatch.setattr(sq, "pmo_mirror_db_ready", lambda: False)
    out = sq.run_sprint_epic_report(sprint="2026/05/11-Sprint")
    assert out["status"] == "error"
    assert out.get("error_class") == "config"


def test_sprint_report_fixture_counts(monkeypatch, tmp_path):
    db = tmp_path / "pmo_test.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE pmo_raw_records (
            id INTEGER PRIMARY KEY,
            source_view TEXT,
            source_file TEXT,
            row_index INTEGER,
            raw_text TEXT,
            fields TEXT,
            synced_at TEXT
        )
        """
    )
    sprint = "2026/05/11-Sprint"

    def row(ri, req, parent, task_no=None, extra=None):
        f = {"Requirement": req, "Sprint": sprint, "priority": "P0"}
        if parent is not None:
            f["父记录"] = parent
        if task_no:
            f["任务编号"] = task_no
        if extra:
            f.update(extra)
        conn.execute(
            "INSERT INTO pmo_raw_records (source_view, source_file, row_index, fields, synced_at) "
            "VALUES (?,?,?,?,?)",
            ("vewpI8lyYw", "t.md", ri, json.dumps(f, ensure_ascii=False), "2026-01-01"),
        )

    row(0, "EpicA", None, "E1")
    row(1, "开发", "EpicA")
    row(2, "子任务1", "开发", "T1")
    row(3, "产品", "EpicA")
    row(4, "产品子1", "产品", "P1")
    row(5, "美术", "EpicA")
    row(6, "美术子1", "美术", "A1")
    row(7, "EpicB", None, "E2")
    conn.commit()
    conn.close()

    monkeypatch.setattr(sq, "get_pmo_db_path", lambda: db)
    monkeypatch.setattr(sq, "pmo_mirror_db_ready", lambda: True)
    monkeypatch.setattr(sq, "_connect", lambda: sqlite3.connect(db))

    out = sq.run_sprint_epic_report(sprint=sprint)
    assert out["status"] == "ok"
    assert out["summary"]["epic_count"] == 2
    assert out["summary"]["dev_task_count"] == 1
    assert out["summary"]["product_task_count"] == 1
    assert out["summary"]["art_task_count"] == 1
    assert out["dev_tasks"][0]["parent_epic"] == "EpicA"
    assert out["dev_tasks"][0]["department"] == "开发"
    assert out["product_tasks"][0]["parent_epic"] == "EpicA"
    assert out["art_tasks"][0]["department"] == "美术"
    assert len(out["epic_children"]) == 3

    out_dev_only = sq.run_sprint_epic_report(sprint=sprint, department="development")
    assert out_dev_only["summary"]["dev_task_count"] == 1
    assert out_dev_only["summary"]["product_task_count"] == 0


def test_epic_chain_parent_collects_participants(monkeypatch, tmp_path):
    """父记录=Epic 名链（非 开发 占位）的子任务须归并到大需求。"""
    db = tmp_path / "pmo_chain.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE pmo_raw_records (
            id INTEGER PRIMARY KEY,
            source_view TEXT,
            source_file TEXT,
            row_index INTEGER,
            raw_text TEXT,
            fields TEXT,
            synced_at TEXT
        )
        """
    )
    sprint = "2026/06/01-Sprint"

    def row(ri, req, parent, task_no=None, person=None):
        f = {"Requirement": req, "Sprint": sprint, "priority": "P2"}
        if parent is not None:
            f["父记录"] = parent
        if task_no:
            f["任务编号"] = task_no
        if person:
            f["Person in charge/Participant"] = person
        conn.execute(
            "INSERT INTO pmo_raw_records (source_view, source_file, row_index, fields, synced_at) "
            "VALUES (?,?,?,?,?)",
            ("vewpI8lyYw", "t.md", ri, json.dumps(f, ensure_ascii=False), "2026-01-01"),
        )

    row(100, "技术优化", None, "E-TECH")
    row(101, "中台技术优化", "技术优化", "G-MID")
    row(102, "中台技术优化-BI导出", "中台技术优化", "T1", "Jade")
    row(103, "中台技术优化-创建房间", "中台技术优化", "T2", "Kelden")
    row(104, "中台技术优化-grpc", "中台技术优化", "T3", "hex")
    conn.commit()
    conn.close()

    monkeypatch.setattr(sq, "get_pmo_db_path", lambda: db)
    monkeypatch.setattr(sq, "pmo_mirror_db_ready", lambda: True)
    monkeypatch.setattr(sq, "_connect", lambda: sqlite3.connect(db))

    out = sq.run_sprint_epic_report(sprint=sprint)
    assert out["status"] == "ok"
    tech_kids = [t for t in out["dev_tasks"] if t.get("parent_epic") == "技术优化"]
    with_person = [t for t in tech_kids if t.get("person")]
    assert len(with_person) == 3
    assert {t.get("person") for t in with_person} == {"Jade", "Kelden", "hex"}

    from l3_node.pmo_epic_aggregate import epic_participants

    epic = next(e for e in out["epics"] if e.get("epic_name") == "技术优化")
    label = epic_participants(epic, tech_kids)
    assert "Jade" in label and "Kelden" in label and "hex" in label


def test_resolve_sprint_label(monkeypatch, tmp_path):
    db = tmp_path / "pmo_resolve.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE pmo_raw_records (id INTEGER PRIMARY KEY, source_view TEXT, fields TEXT)"
    )
    f = json.dumps({"Sprint": "2026/05/11-Sprint"}, ensure_ascii=False)
    conn.execute(
        "INSERT INTO pmo_raw_records (source_view, fields) VALUES (?,?)",
        ("vewpI8lyYw", f),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(sq, "get_pmo_db_path", lambda: db)
    monkeypatch.setattr(sq, "pmo_mirror_db_ready", lambda: True)
    monkeypatch.setattr(sq, "_connect", lambda: sqlite3.connect(db))

    out = sq.run_resolve_sprint(label="5月11", year=2026)
    assert out.get("resolved_sprint") == "2026/05/11-Sprint"
