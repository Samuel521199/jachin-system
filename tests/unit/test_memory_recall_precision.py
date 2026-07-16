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


def test_memory_recall_matches_tool_names_with_underscore_and_hyphen(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import MemoryWriteRequest
    from l3_node.cognitive_kernel.memory_lifecycle import recall_lifecycle_memories, warm_lifecycle_memory_index, write_lifecycle_memory

    target = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="recall-tool-name-target",
            source_event="recall_precision_test",
            memory_type="failure_hint",
            content="Browser foreground recovery should try switch_existing_window before longer timeout.",
            confidence=0.91,
            ttl="permanent",
            evidence=[{"type": "unit", "governance_key": "browser:focus:recovery", "ok": True}],
        )
    )
    write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="recall-tool-name-confuser",
            source_event="recall_precision_test",
            memory_type="failure_hint",
            content="Browser focus timeout can be ignored only when the active-window title is already correct.",
            confidence=0.87,
            ttl="permanent",
            evidence=[{"type": "unit", "governance_key": "browser:focus:weak", "ok": True}],
        )
    )

    warm_lifecycle_memory_index()
    hits = recall_lifecycle_memories("browser focus failed switch existing window", memory_types=["failure_hint"], limit=5)
    assert hits
    assert hits[0].memory_id == target.memory_id


def test_memory_recall_uses_normalized_dot_rerank_helpers():
    from l3_node.cognitive_kernel.memory_lifecycle import _dot_product, _normalize_vector, _normalized_hash_vector

    vector = _normalize_vector([3.0, 4.0])
    assert round(vector[0], 3) == 0.6
    assert round(vector[1], 3) == 0.8
    assert round(_dot_product(vector, vector), 6) == 1.0

    query_vector = _normalized_hash_vector("browser focus switch existing window")
    same_vector = _normalized_hash_vector("browser focus switch existing window")
    other_vector = _normalized_hash_vector("lark message recipient neil")
    assert _dot_product(query_vector, same_vector) > _dot_product(query_vector, other_vector)


def test_memory_recall_marks_three_layer_pipeline_in_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import MemoryWriteRequest
    from l3_node.cognitive_kernel.memory_lifecycle import recall_lifecycle_memories, write_lifecycle_memory

    target = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="recall-three-layer-target",
            source_event="recall_precision_test",
            memory_type="tool_habit",
            content="When the user says lock in desktop control, confirm whether they meant Lark before execution.",
            confidence=0.9,
            ttl="permanent",
            evidence=[{"type": "unit", "governance_key": "app:lark:phonetic-correction", "ok": True}],
        )
    )
    write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="recall-three-layer-confuser",
            source_event="recall_precision_test",
            memory_type="tool_habit",
            content="When the user says browser, prefer Chrome if it is already installed.",
            confidence=0.88,
            ttl="permanent",
            evidence=[{"type": "unit", "governance_key": "app:browser:chrome", "ok": True}],
        )
    )

    hits = recall_lifecycle_memories("lock lark correction", memory_types=["tool_habit"], limit=3)
    assert hits
    assert hits[0].memory_id == target.memory_id
    assert "inverted-index" in hits[0].relevance_reason
    assert "normalized-dot-rerank" in hits[0].relevance_reason
