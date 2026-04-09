"""安全锁：按需注入、pending 审批、删除。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def tmp_jachin_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("JACHIN_HOME", str(tmp_path))
    monkeypatch.delenv("JACHIN_SAFETY_LOCK_LEARN", raising=False)
    monkeypatch.delenv("JACHIN_SAFETY_LOCK_FULL_INJECT", raising=False)
    return tmp_path


def test_learn_off_rejects_append(tmp_jachin_home: Path) -> None:
    from l3_node.jachin_safety_lock import append_verified_fact

    r = append_verified_fact("x", source="t")
    assert r.get("ok") is False
    assert r.get("error") == "learn_disabled"


def test_pending_flow_and_approve(tmp_jachin_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JACHIN_SAFETY_LOCK_LEARN", "1")
    monkeypatch.setenv("JACHIN_SAFETY_LOCK_ADMIN_TOKEN", "adm-sec")
    (tmp_jachin_home / "nexus_config.json").write_text("{}", encoding="utf-8")

    from l3_node.jachin_safety_lock import (
        append_verified_fact,
        approve_pending,
        global_lock_path,
        list_pending_entries,
    )

    r1 = append_verified_fact("line one", source="agent")
    assert r1.get("status") == "pending_approval"
    pid = r1.get("pending_id")
    assert pid
    assert list_pending_entries().get("count") == 1

    bad = approve_pending(pid, "wrong")
    assert bad.get("error") == "forbidden"

    ok = approve_pending(pid, "adm-sec")
    assert ok.get("ok") is True
    assert list_pending_entries().get("count") == 0
    text = global_lock_path().read_text(encoding="utf-8")
    assert "line one" in text
    assert "id=" in text


def test_direct_append_when_configured(tmp_jachin_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JACHIN_SAFETY_LOCK_LEARN", "1")
    cfg = {"safety_lock": {"direct_append_to_md": True}}
    (tmp_jachin_home / "nexus_config.json").write_text(json.dumps(cfg), encoding="utf-8")

    from l3_node.jachin_safety_lock import append_verified_fact, global_lock_path

    r = append_verified_fact("direct", source="t")
    assert r.get("status") == "appended"
    assert global_lock_path().read_text(encoding="utf-8").count("direct") >= 1


def test_remove_entry(tmp_jachin_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JACHIN_SAFETY_LOCK_LEARN", "1")
    cfg = {"safety_lock": {"direct_append_to_md": True}}
    (tmp_jachin_home / "nexus_config.json").write_text(json.dumps(cfg), encoding="utf-8")

    from l3_node.jachin_safety_lock import append_verified_fact, global_lock_path, remove_entry_by_id

    ar = append_verified_fact("to remove", source="t")
    eid = ar.get("entry_id")
    assert eid
    assert "to remove" in global_lock_path().read_text(encoding="utf-8")
    rr = remove_entry_by_id(str(eid))
    assert rr.get("ok") is True
    assert "to remove" not in global_lock_path().read_text(encoding="utf-8")


def test_domain_inject_css_vs_db(tmp_jachin_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """未命中 db/shell 时不应灌入大块全局（仅 pin / 头段）。"""
    ddir = tmp_jachin_home / "safety_lock"
    ddir.mkdir(parents=True, exist_ok=True)
    (ddir / "db_safety_lock.md").write_text("DB_RULE_ONLY", encoding="utf-8")
    (tmp_jachin_home / "JACHIN_SAFETY_LOCK.md").write_text("X" * 5000, encoding="utf-8")

    from l3_node.jachin_safety_lock import get_safety_lock_snippet

    slim = get_safety_lock_snippet(user_text="帮我改一下 CSS 颜色")
    assert "DB_RULE_ONLY" not in slim

    fat = get_safety_lock_snippet(user_text="查询数据库 users 表结构")
    assert "DB_RULE_ONLY" in fat


def test_tofu_auto_second_append_same_category(tmp_jachin_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """首条带 category 人工批准后，同 category 再次 append 应直接写入并覆盖旧块。"""
    monkeypatch.setenv("JACHIN_SAFETY_LOCK_LEARN", "1")
    monkeypatch.setenv("JACHIN_SAFETY_LOCK_ADMIN_TOKEN", "adm-sec")
    (tmp_jachin_home / "nexus_config.json").write_text("{}", encoding="utf-8")

    from l3_node.jachin_safety_lock import (
        append_verified_fact,
        approve_pending,
        global_lock_path,
    )

    r1 = append_verified_fact("rule v1", source="agent", category="backend_framework")
    assert r1.get("status") == "pending_approval"
    pid = r1.get("pending_id")
    assert approve_pending(str(pid), "adm-sec").get("ok") is True
    text1 = global_lock_path().read_text(encoding="utf-8")
    assert "rule v1" in text1
    assert "category=`backend_framework`" in text1

    r2 = append_verified_fact("rule v2 override", source="agent", category="backend_framework")
    assert r2.get("status") == "auto_approved_tofu"
    assert r2.get("category") == "backend_framework"
    text2 = global_lock_path().read_text(encoding="utf-8")
    assert "rule v2 override" in text2
    assert "rule v1" not in text2
    assert text2.count("category=`backend_framework`") == 1


def test_core_safety_lock_append_dispatch_unit() -> None:
    from l3_node.tools.core_safety_lock_append import decide_safety_lock_append_path

    assert (
        decide_safety_lock_append_path(
            append_requires_approval=True,
            category_norm="x",
            approved_categories={"x"},
        )
        == "tofu_auto"
    )
    assert (
        decide_safety_lock_append_path(
            append_requires_approval=True,
            category_norm="x",
            approved_categories=set(),
        )
        == "pending"
    )
    assert (
        decide_safety_lock_append_path(
            append_requires_approval=False,
            category_norm="x",
            approved_categories=set(),
        )
        == "direct_md"
    )


def test_full_inject_respects_cap(tmp_jachin_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JACHIN_SAFETY_LOCK_FULL_INJECT", "1")
    (tmp_jachin_home / "JACHIN_SAFETY_LOCK.md").write_text("H" * 100_000, encoding="utf-8")

    from l3_node.jachin_safety_lock import get_safety_lock_snippet

    s = get_safety_lock_snippet(user_text="css")
    assert len(s) < 50_000
    assert "截断" in s or len(s) <= 33000
