def test_memory_recall_precision_under_noise(tmp_path):
    from scripts.memory_recall_precision_stress import run

    result = run(tmp_path / "recall_precision", noise_count=300, seed=20260715, top_k=10)

    assert result["passed"] is True
    assert result["metrics"]["top1_rate"] >= 0.8
    assert result["metrics"]["top3_rate"] >= 0.95
    assert result["checks"]["expired_decoys_filtered"] is True


def test_memory_recall_handles_chinese_compact_query_and_evidence_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import MemoryWriteRequest
    from l3_node.cognitive_kernel.memory_lifecycle import recall_lifecycle_memories, write_lifecycle_memory

    target = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="recall-compact-target",
            source_event="recall_precision_test",
            memory_type="alias",
            content="Jachin 项目路径就是 D:/Projects/jachi/jachin-system-main，用于本机主项目开发。",
            confidence=0.92,
            ttl="permanent",
            evidence=[{"type": "unit", "governance_key": "project:jachin:path", "ok": True}],
        )
    )
    write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="recall-compact-confuser",
            source_event="recall_precision_test",
            memory_type="alias",
            content="Jachin 旧文档路径是 D:/Archive/jachin-docs，这不是当前主项目路径。",
            confidence=0.86,
            ttl="permanent",
            evidence=[{"type": "unit", "governance_key": "project:jachin:old-docs", "ok": True}],
        )
    )

    hits = recall_lifecycle_memories("Jachin项目路径在哪里", memory_types=["alias"], limit=5)
    assert hits
    assert hits[0].memory_id == target.memory_id

    evidence_hits = recall_lifecycle_memories("project jachin path", memory_types=["alias"], limit=5)
    assert evidence_hits
    assert evidence_hits[0].memory_id == target.memory_id
