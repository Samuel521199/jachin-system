import json


def test_memory_quality_governance_marks_low_confidence_stale_and_conflicts(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import MemoryWriteRequest
    from l3_node.cognitive_kernel.memory_lifecycle import (
        govern_lifecycle_memories,
        memory_quality_snapshot,
        pending_lifecycle_review_items,
        write_lifecycle_memory,
    )

    low = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="quality-low",
            source_event="quality_test",
            memory_type="tool_habit",
            content="Tool habit with weak evidence should be reviewed.",
            confidence=0.2,
            ttl="permanent",
            evidence=[{"type": "quality", "ok": False}],
        )
    )
    stale = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="quality-stale",
            source_event="quality_test",
            memory_type="project_fact",
            content="Old project fact should be revalidated.",
            confidence=0.8,
            ttl="permanent",
            evidence=[{"type": "quality", "governance_key": "project:jachin"}],
        )
    )
    stale.created_at_ms = 1
    stale.updated_at_ms = 1
    stale.last_verified_at_ms = 1

    a = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="quality-conflict-a",
            source_event="quality_test",
            memory_type="correction",
            content="When the user says lock, resolve it as Lark.",
            confidence=0.82,
            ttl="permanent",
            evidence=[{"type": "quality", "governance_key": "speech:lock"}],
        )
    )
    b = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="quality-conflict-b",
            source_event="quality_test",
            memory_type="correction",
            content="When the user says lock, resolve it as local lock screen.",
            confidence=0.82,
            ttl="permanent",
            evidence=[{"type": "quality", "governance_key": "speech:lock"}],
        )
    )

    # Persist the manually aged stale record without relying on private helpers.
    store = tmp_path / "kernel" / "memory" / "memory_lifecycle.jsonl"
    records = []
    for line in store.read_text(encoding="utf-8").splitlines():
        obj = json.loads(line)
        if obj["memory_id"] == stale.memory_id:
            obj["created_at_ms"] = stale.created_at_ms
            obj["updated_at_ms"] = stale.updated_at_ms
            obj["last_verified_at_ms"] = stale.last_verified_at_ms
        records.append(obj)
    store.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in records) + "\n", encoding="utf-8")

    summary = govern_lifecycle_memories(stale_after_days=1)
    assert summary["low_confidence_count"] >= 1
    assert summary["stale_unverified_count"] >= 1
    assert summary["conflict_count"] >= 2
    assert summary["review_required_count"] >= 4

    pending = pending_lifecycle_review_items(limit=10)
    reasons = {item["review_reason"] for item in pending}
    assert any(str(reason).startswith("low_confidence") for reason in reasons)
    assert "stale_unverified" in reasons
    assert "memory_conflict" in reasons
    pending_ids = {item["memory_id"] for item in pending}
    assert {low.memory_id, stale.memory_id, a.memory_id, b.memory_id}.issubset(pending_ids)

    snapshot = memory_quality_snapshot()
    assert snapshot["review_required_count"] >= 4
    assert snapshot["governance"]["review_required_count"] >= 4


def test_memory_quality_governance_survives_corrupt_lines_and_duplicate_storm(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import MemoryWriteRequest
    from l3_node.cognitive_kernel.memory_lifecycle import (
        govern_lifecycle_memories,
        memory_quality_snapshot,
        recall_lifecycle_memories,
        write_lifecycle_memory,
    )

    request = MemoryWriteRequest(
        turn_id="quality-duplicate",
        source_event="quality_test",
        memory_type="alias",
        content="Jachin project path is D:/Projects/jachi/jachin-system-main.",
        confidence=0.76,
        ttl="permanent",
        evidence=[{"type": "quality", "ok": True, "governance_key": "project:jachin:path"}],
    )
    first = write_lifecycle_memory(request)
    for _ in range(199):
        write_lifecycle_memory(request)

    store = tmp_path / "kernel" / "memory" / "memory_lifecycle.jsonl"
    store.write_text(store.read_text(encoding="utf-8") + "{ broken json\n", encoding="utf-8")

    summary = govern_lifecycle_memories()
    assert summary["invalid_raw_line_count"] == 1
    assert summary["total_count"] == 1
    assert summary["active_count"] == 1

    hits = recall_lifecycle_memories("Jachin project path", memory_types=["alias"])
    assert len(hits) == 1
    assert hits[0].memory_id == first.memory_id

    snapshot = memory_quality_snapshot()
    assert snapshot["invalid_raw_line_count"] == 1
