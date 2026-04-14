"""zombie_tasks.json 与 core:check_interrupted_tasks。"""
from __future__ import annotations

import json
from l3_node.primitives.agent_tasks import background_task_service as bts


def test_check_interrupted_tasks_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(bts, "_jachin_dir", lambda: tmp_path)
    raw = bts.check_interrupted_tasks_sync("")
    o = json.loads(raw)
    assert o["ok"] is True
    assert o["tasks"] == []
    assert o["count"] == 0


def test_append_and_read_consume(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(bts, "_jachin_dir", lambda: tmp_path)
    bts._append_zombie_task_record(
        {
            "task_id": "T-test111",
            "task_prompt": "hello intent",
            "interrupted_at": 1.0,
            "previous_status": "running",
        }
    )
    raw = bts.check_interrupted_tasks_sync("{}")
    o = json.loads(raw)
    assert o["ok"] is True
    assert o["count"] == 1
    assert o["tasks"][0]["task_id"] == "T-test111"
    assert o["tasks"][0]["task_prompt"] == "hello intent"

    raw2 = bts.check_interrupted_tasks_sync('{"consume": true}')
    o2 = json.loads(raw2)
    assert o2["consumed"] is True
    assert o2["count"] == 1

    raw3 = bts.check_interrupted_tasks_sync("")
    o3 = json.loads(raw3)
    assert o3["count"] == 0


def test_reconcile_skips_zombie_file_and_appends(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(bts, "_jachin_dir", lambda: tmp_path)
    td = bts._task_dir()
    fake = {
        "task_id": "T-olddead",
        "status": "running",
        "intent": "do work",
        "require_skills": [],
        "max_iterations": 8,
        "created_at": 0.0,
    }
    (td / "T-olddead.json").write_text(json.dumps(fake, ensure_ascii=False), encoding="utf-8")
    n = bts.reconcile_stale_background_tasks_on_startup()
    assert n == 1
    rec = json.loads((td / "T-olddead.json").read_text(encoding="utf-8"))
    assert rec["status"] == "interrupted"
    zpath = td / "zombie_tasks.json"
    assert zpath.is_file()
    z = json.loads(zpath.read_text(encoding="utf-8"))
    assert len(z) == 1
    assert z[0]["task_id"] == "T-olddead"
    assert z[0]["task_prompt"] == "do work"
