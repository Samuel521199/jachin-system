import json


def test_memory_growth_scaffold_and_append_raw_event(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import (
        append_raw_event,
        ensure_memory_growth_scaffold,
        memory_growth_dir,
    )

    root = ensure_memory_growth_scaffold()
    assert root == memory_growth_dir()
    assert (root / "raw" / "evidence").is_dir()
    assert (root / "concepts" / "README.md").is_file()
    assert (root / "playbooks" / "README.md").is_file()

    path = append_raw_event(
        category="evidence",
        source="unit_test",
        stream="probe",
        payload={"hello": "memory growth"},
        source_refs=[{"type": "unit_test", "id": "raw-1"}],
    )
    assert path.exists()
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["schema_version"] == 1
    assert rows[0]["category"] == "evidence"
    assert rows[0]["source"] == "unit_test"
    assert rows[0]["payload"]["hello"] == "memory growth"
    assert rows[0]["review"]["review_candidate"] is True


def test_close_turn_records_memory_growth_raw_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.runtime import (
        build_decision_contract,
        build_work_order,
        close_turn,
        verify_work_order,
    )

    contract = build_decision_contract(
        turn_id="ck-memory-growth-close-1",
        goal="read file",
        tool="core:fs_read",
        work_order_input='{"path":"README.md"}',
    )
    work = build_work_order(
        contract=contract,
        tool="core:fs_read",
        work_order_input='{"path":"README.md"}',
    )
    report = verify_work_order(
        turn_id=contract.turn_id,
        work_order=work,
        observation='{"ok":true,"content":"ok"}',
        elapsed_ms=1.0,
    )
    close_turn(
        turn_id=contract.turn_id,
        final_text="done",
        executed_work_orders=[work.work_order_id],
        verification_reports=[report],
    )

    raw_root = tmp_path / "kernel" / "memory_growth" / "raw" / "evidence"
    paths = list(raw_root.glob("*.turn_closure.jsonl"))
    assert len(paths) == 1
    rows = [json.loads(line) for line in paths[0].read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "turn_closure_agent"
    assert row["payload"]["turn_id"] == contract.turn_id
    assert row["payload"]["closure"]["verification_status"] == "passed"
    assert row["payload"]["promotion_hints"]["has_executed_work_orders"] is True
    assert row["review"]["promotion_targets"] == ["concepts", "playbooks", "outputs"]
    assert any(ref.get("work_order_id") == work.work_order_id for ref in row["source_refs"])


def test_close_turn_waiting_user_records_pending_raw_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.runtime import close_turn_waiting_user

    close_turn_waiting_user(
        turn_id="ck-memory-growth-wait-1",
        final_text="需要确认",
        pending_decision={"decision_id": "d1", "requires_confirmation": True},
    )

    paths = list((tmp_path / "kernel" / "memory_growth" / "raw" / "evidence").glob("*.turn_closure.jsonl"))
    assert len(paths) == 1
    row = json.loads(paths[0].read_text(encoding="utf-8").splitlines()[0])
    assert row["payload"]["closure"]["pending_decision"]["decision_id"] == "d1"
    assert row["payload"]["promotion_hints"]["has_pending_decision"] is True
    assert row["review"]["priority"] == "high"


def test_daily_review_generates_review_and_patch_from_raw_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.daily_review import run_daily_review
    from l3_node.cognitive_kernel.runtime import (
        build_decision_contract,
        build_work_order,
        close_turn,
        verify_work_order,
    )

    contract = build_decision_contract(
        turn_id="ck-memory-growth-daily-1",
        goal="open calculator",
        tool="windows_app_open",
        work_order_input='{"app":"calculator"}',
    )
    work = build_work_order(
        contract=contract,
        tool="windows_app_open",
        work_order_input='{"app":"calculator"}',
    )
    report = verify_work_order(
        turn_id=contract.turn_id,
        work_order=work,
        observation='{"ok":true,"window":"Calculator"}',
        elapsed_ms=1.0,
    )
    close_turn(
        turn_id=contract.turn_id,
        final_text="calculator opened",
        executed_work_orders=[work.work_order_id],
        verification_reports=[report],
    )

    result = run_daily_review()
    assert result.raw_event_count == 1
    assert result.task_count == 1
    assert result.passed_count == 1
    assert result.failed_count == 0
    assert result.concept_candidate_count >= 1
    assert result.playbook_candidate_count >= 1
    assert result.output_candidate_count >= 1
    assert result.review_path.exists()
    assert result.patch_path.exists()

    patch = json.loads(result.patch_path.read_text(encoding="utf-8"))
    assert patch["schema_version"] == 1
    assert patch["source"] == "daily_review_agent"
    assert patch["task_summaries"][0]["turn_id"] == contract.turn_id
    assert patch["concept_candidates"][0]["source_refs"]
    assert "Daily Review" in result.review_path.read_text(encoding="utf-8")


def test_concept_curator_promotes_stable_candidates_and_quarantines_weak_ones(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.concept_curator import apply_concept_patch
    from l3_node.cognitive_kernel.memory_growth import append_raw_event, ensure_memory_growth_scaffold, memory_growth_dir

    ensure_memory_growth_scaffold()
    patch_path = memory_growth_dir() / "reviews" / "patches" / "2026-07-10.daily_review.patch.json"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "review_id": "daily_2026-07-10_unit",
                "date": "2026-07-10",
                "source": "daily_review_agent",
                "summary": {},
                "task_summaries": [],
                "concept_candidates": [
                    {
                        "candidate_id": "concept:turn-1:project_fact:abc",
                        "target_type": "project_fact",
                        "summary": "Jachin uses Memory Growth raw evidence before concept promotion.",
                        "confidence": 0.86,
                        "requires_user_confirmation": False,
                        "merge_policy": "dedupe_and_merge",
                        "source_refs": [{"type": "raw_event", "event_id": "raw-1"}],
                    },
                    {
                        "candidate_id": "concept:turn-2:project_fact:def",
                        "target_type": "project_fact",
                        "summary": "This weak fact should wait for more evidence.",
                        "confidence": 0.4,
                        "requires_user_confirmation": False,
                        "merge_policy": "dedupe_and_merge",
                        "source_refs": [{"type": "raw_event", "event_id": "raw-2"}],
                    },
                    {
                        "candidate_id": "concept:turn-3:preference:ghi",
                        "target_type": "preference",
                        "summary": "User confirmation is required before storing this preference.",
                        "confidence": 0.9,
                        "requires_user_confirmation": True,
                        "merge_policy": "dedupe_and_merge",
                        "source_refs": [{"type": "raw_event", "event_id": "raw-3"}],
                    },
                ],
                "playbook_candidates": [],
                "output_candidates": [],
                "warnings": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = apply_concept_patch(patch_path)
    assert result.promoted_count == 1
    assert result.quarantined_count == 2
    assert result.skipped_count == 0
    assert len(result.concept_paths) == 1
    assert len(result.conflict_paths) == 2
    assert result.report_path and result.report_path.exists()

    concept_text = result.concept_paths[0].read_text(encoding="utf-8")
    assert "Memory Growth raw evidence" in concept_text
    assert "raw-1" in concept_text
    assert (memory_growth_dir() / "indexes" / "concepts.json").exists()
    conflict_reasons = [json.loads(path.read_text(encoding="utf-8"))["reason"] for path in result.conflict_paths]
    assert any(reason.startswith("low_confidence") for reason in conflict_reasons)
    assert "requires_user_confirmation" in conflict_reasons


def test_concept_curator_appends_non_conflicting_updates(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.concept_curator import apply_concept_patch
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir

    ensure_memory_growth_scaffold()
    patch_path = memory_growth_dir() / "reviews" / "patches" / "2026-07-10.daily_review.patch.json"
    patch_path.parent.mkdir(parents=True, exist_ok=True)

    base = {
        "schema_version": 1,
        "review_id": "daily_2026-07-10_unit",
        "date": "2026-07-10",
        "source": "daily_review_agent",
        "summary": {},
        "task_summaries": [],
        "playbook_candidates": [],
        "output_candidates": [],
        "warnings": [],
    }
    base["concept_candidates"] = [
        {
            "candidate_id": "concept:turn-1:project_fact:abc",
            "target_type": "project_fact",
            "summary": "Jachin Memory Growth records raw evidence for later review.",
            "confidence": 0.86,
            "source_refs": [{"type": "raw_event", "event_id": "raw-1"}],
        }
    ]
    patch_path.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    first = apply_concept_patch(patch_path)

    base["review_id"] = "daily_2026-07-11_unit"
    base["concept_candidates"] = [
        {
            "candidate_id": "concept:turn-1:project_fact:abc",
            "target_type": "project_fact",
            "summary": "Jachin Memory Growth records raw evidence and source refs for later review.",
            "confidence": 0.88,
            "source_refs": [{"type": "raw_event", "event_id": "raw-4"}],
        }
    ]
    patch_path.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    second = apply_concept_patch(patch_path)

    assert first.concept_paths == second.concept_paths
    assert second.promoted_count == 1
    text = second.concept_paths[0].read_text(encoding="utf-8")
    assert "raw-1" in text
    assert "raw-4" in text
    assert "daily_2026-07-11_unit" in text


def test_playbook_builder_promotes_usable_candidates_and_quarantines_weak_ones(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.cognitive_kernel.playbook_builder import apply_playbook_patch

    ensure_memory_growth_scaffold()
    patch_path = memory_growth_dir() / "reviews" / "patches" / "2026-07-10.daily_review.patch.json"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "review_id": "daily_2026-07-10_unit",
                "date": "2026-07-10",
                "source": "daily_review_agent",
                "summary": {},
                "task_summaries": [],
                "concept_candidates": [],
                "playbook_candidates": [
                    {
                        "candidate_id": "playbook:turn-1:success:abc",
                        "title": "Repeatable app control flow",
                        "trigger": {
                            "sources": ["turn_closure_agent"],
                            "categories": ["evidence"],
                            "memory_types": ["short_term_action"],
                        },
                        "recommended_flow": [
                            {
                                "step": "reuse_work_order_chain",
                                "work_order_ids": ["work-1"],
                                "verification_status": "passed",
                            }
                        ],
                        "evidence_required": ["work_order", "verification_report", "turn_closure"],
                        "source_refs": [{"type": "raw_event", "event_id": "raw-1"}],
                        "confidence": 0.72,
                    },
                    {
                        "candidate_id": "playbook:turn-2:success:def",
                        "title": "Weak flow",
                        "trigger": {},
                        "recommended_flow": [{"step": "maybe"}],
                        "evidence_required": [],
                        "source_refs": [{"type": "raw_event", "event_id": "raw-2"}],
                        "confidence": 0.2,
                    },
                ],
                "output_candidates": [],
                "warnings": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = apply_playbook_patch(patch_path)
    assert result.promoted_count == 1
    assert result.quarantined_count == 1
    assert result.skipped_count == 0
    assert len(result.playbook_paths) == 1
    assert len(result.quarantine_paths) == 1
    assert result.report_path and result.report_path.exists()

    text = result.playbook_paths[0].read_text(encoding="utf-8")
    assert "Repeatable app control flow" in text
    assert "reuse_work_order_chain" in text
    assert "work-1" in text
    assert "raw-1" in text
    assert (memory_growth_dir() / "indexes" / "playbooks.json").exists()
    reason = json.loads(result.quarantine_paths[0].read_text(encoding="utf-8"))["reason"]
    assert reason.startswith("low_confidence")


def test_playbook_builder_appends_existing_playbook(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.cognitive_kernel.playbook_builder import apply_playbook_patch

    ensure_memory_growth_scaffold()
    patch_path = memory_growth_dir() / "reviews" / "patches" / "2026-07-10.daily_review.patch.json"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch = {
        "schema_version": 1,
        "review_id": "daily_2026-07-10_unit",
        "date": "2026-07-10",
        "source": "daily_review_agent",
        "summary": {},
        "task_summaries": [],
        "concept_candidates": [],
        "playbook_candidates": [
            {
                "candidate_id": "playbook:turn-1:success:abc",
                "title": "Repeatable message flow",
                "trigger": {"sources": ["turn_closure_agent"]},
                "recommended_flow": [{"step": "send_message", "work_order_ids": ["work-1"]}],
                "evidence_required": ["message_id"],
                "source_refs": [{"type": "raw_event", "event_id": "raw-1"}],
                "confidence": 0.7,
            }
        ],
        "output_candidates": [],
        "warnings": [],
    }
    patch_path.write_text(json.dumps(patch, ensure_ascii=False), encoding="utf-8")
    first = apply_playbook_patch(patch_path)

    patch["review_id"] = "daily_2026-07-11_unit"
    patch["playbook_candidates"][0]["recommended_flow"] = [{"step": "verify_send", "work_order_ids": ["work-2"]}]
    patch["playbook_candidates"][0]["source_refs"] = [{"type": "raw_event", "event_id": "raw-3"}]
    patch_path.write_text(json.dumps(patch, ensure_ascii=False), encoding="utf-8")
    second = apply_playbook_patch(patch_path)

    assert first.playbook_paths == second.playbook_paths
    assert second.promoted_count == 1
    text = second.playbook_paths[0].read_text(encoding="utf-8")
    assert "work-1" in text
    assert "work-2" in text
    assert "raw-1" in text
    assert "raw-3" in text
    assert "daily_2026-07-11_unit" in text


def test_output_review_promotes_user_facing_outputs(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.cognitive_kernel.output_review import apply_output_patch

    ensure_memory_growth_scaffold()
    patch_path = memory_growth_dir() / "reviews" / "patches" / "2026-07-10.daily_review.patch.json"
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "review_id": "daily_2026-07-10_unit",
                "date": "2026-07-10",
                "source": "daily_review_agent",
                "summary": {},
                "task_summaries": [],
                "concept_candidates": [],
                "playbook_candidates": [],
                "output_candidates": [
                    {
                        "candidate_id": "output:turn-1:raw-1",
                        "target_type": "lark_messages",
                        "summary": "Task turn-1 sent a user-facing Lark summary.",
                        "content": "Neil，Jachin 今日已完成 Memory Growth 输出回流。",
                        "verification_status": "passed",
                        "closure_type": "completed",
                        "source_refs": [{"type": "raw_event", "event_id": "raw-1"}],
                        "confidence": 0.45,
                    },
                    {
                        "candidate_id": "output:turn-2:raw-2",
                        "target_type": "work_records",
                        "summary": "",
                        "content": "",
                        "source_refs": [{"type": "raw_event", "event_id": "raw-2"}],
                        "confidence": 0.9,
                    },
                ],
                "warnings": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = apply_output_patch(patch_path)
    assert result.promoted_count == 1
    assert result.skipped_count == 1
    assert result.quarantined_count == 0
    assert len(result.output_paths) == 1
    assert result.report_path and result.report_path.exists()

    text = result.output_paths[0].read_text(encoding="utf-8")
    assert "Memory Growth 输出回流" in text
    assert "raw-1" in text
    assert "Verification status" in text
    assert (memory_growth_dir() / "indexes" / "outputs.json").exists()


def test_growth_scheduler_runs_full_pipeline(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.growth_scheduler import run_growth_pipeline
    from l3_node.cognitive_kernel.runtime import (
        build_decision_contract,
        build_work_order,
        close_turn,
        verify_work_order,
    )

    contract = build_decision_contract(
        turn_id="ck-memory-growth-pipeline-1",
        goal="read project summary source",
        tool="core:fs_read",
        work_order_input='{"path":"README.md"}',
    )
    work = build_work_order(
        contract=contract,
        tool="core:fs_read",
        work_order_input='{"path":"README.md"}',
    )
    report = verify_work_order(
        turn_id=contract.turn_id,
        work_order=work,
        observation='{"ok":true,"content":"Jachin memory growth source"}',
        elapsed_ms=1.0,
    )
    close_turn(
        turn_id=contract.turn_id,
        final_text="Neil，Jachin 已完成一条自生长知识系统简报。",
        executed_work_orders=[work.work_order_id],
        verification_reports=[report],
    )

    result = run_growth_pipeline()
    assert result.daily_review.raw_event_count == 1
    assert result.concept_result is not None
    assert result.playbook_result is not None
    assert result.output_result is not None
    assert result.concept_result.promoted_count >= 1
    assert result.playbook_result.promoted_count >= 1
    assert result.output_result.promoted_count >= 1
    assert result.report_path.exists()

    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["summary"]["output_promoted"] >= 1
    assert payload["stages"]["daily_review"]["raw_event_count"] == 1


def test_weekly_review_detects_lifecycle_issues(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.cognitive_kernel.weekly_review import run_weekly_review

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    concept_dir = root / "concepts" / "project_fact"
    concept_dir.mkdir(parents=True, exist_ok=True)
    concept_text = (
        "---\n"
        'summary: "Jachin Memory Growth keeps source-backed concepts."\n'
        'source_refs: [{"type":"raw_event","event_id":"raw-1"}]\n'
        "confidence: 0.86\n"
        'last_verified: "2026-05-01T00:00:00+0800"\n'
        'valid_until: ""\n'
        "---\n\n"
        "# Jachin Memory Growth keeps source-backed concepts.\n\n"
        "## Source Evidence\n\n"
        '- `{"type":"raw_event","event_id":"raw-1"}`\n'
    )
    (concept_dir / "memory-growth-source-backed-a.md").write_text(concept_text, encoding="utf-8")
    (concept_dir / "memory-growth-source-backed-b.md").write_text(concept_text.replace("raw-1", "raw-2"), encoding="utf-8")

    output_dir = root / "outputs" / "lark_messages"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "weak-output.md").write_text(
        "---\n"
        'summary: "Weak output"\n'
        "source_refs: []\n"
        "confidence: 0.1\n"
        'verification_status: "failed"\n'
        "---\n\n"
        "# Weak output\n\n"
        "short\n",
        encoding="utf-8",
    )
    conflict_dir = root / "conflicts" / "project_fact"
    conflict_dir.mkdir(parents=True, exist_ok=True)
    (conflict_dir / "conflict.json").write_text(
        json.dumps({"reason": "low_confidence:0.20", "candidate": {"candidate_id": "c1"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = run_weekly_review(week_start="2026-07-06", stale_after_days=30)
    assert result.concept_count == 2
    assert result.output_count == 1
    assert result.conflict_count == 1
    assert result.duplicate_cluster_count == 1
    assert result.stale_concept_count == 2
    assert result.weak_output_count == 1
    assert result.report_path.exists()
    assert result.markdown_path.exists()

    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["summary"]["duplicate_cluster_count"] == 1
    assert any(item["pattern"] == "low_confidence:0.20" for item in report["failure_patterns"])
    assert (root / "indexes" / "weekly_lifecycle.json").exists()
    assert "Weekly Memory Growth Review" in result.markdown_path.read_text(encoding="utf-8")


def test_growth_scheduler_can_include_weekly_lifecycle_review(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.growth_scheduler import run_growth_pipeline
    from l3_node.cognitive_kernel.runtime import (
        build_decision_contract,
        build_work_order,
        close_turn,
        verify_work_order,
    )

    contract = build_decision_contract(
        turn_id="ck-memory-growth-weekly-pipeline-1",
        goal="write a weekly-ready summary",
        tool="core:reply",
        work_order_input='{"message":"weekly summary"}',
    )
    work = build_work_order(
        contract=contract,
        tool="core:reply",
        work_order_input='{"message":"weekly summary"}',
    )
    report = verify_work_order(
        turn_id=contract.turn_id,
        work_order=work,
        observation='{"ok":true}',
        elapsed_ms=1.0,
    )
    close_turn(
        turn_id=contract.turn_id,
        final_text="本周 Memory Growth 管线已完成初步闭环。",
        executed_work_orders=[work.work_order_id],
        verification_reports=[report],
    )

    result = run_growth_pipeline(weekly_lifecycle_review=True)
    assert result.weekly_result is not None
    assert result.weekly_result.report_path.exists()

    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["stages"]["weekly_review"] is not None
    assert "weekly_lifecycle_issues" in payload["summary"]


def test_graph_sync_adapter_derives_nodes_and_edges(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.graph_sync_adapter import sync_memory_growth_graph
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    concept_dir = root / "concepts" / "project_fact"
    concept_dir.mkdir(parents=True, exist_ok=True)
    (concept_dir / "jachin-memory-growth.md").write_text(
        "---\n"
        'summary: "Jachin Memory Growth stores source-backed concepts."\n'
        'source_refs: [{"type":"raw_event","event_id":"raw-graph-1"}]\n'
        "confidence: 0.9\n"
        "---\n\n"
        "# Jachin Memory Growth stores source-backed concepts.\n\n"
        "Memory Growth keeps evidence and concepts connected.\n",
        encoding="utf-8",
    )
    output_dir = root / "outputs" / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "memory-growth-report.md").write_text(
        "---\n"
        'summary: "Memory Growth report explains source-backed concepts."\n'
        'source_refs: [{"type":"raw_event","event_id":"raw-graph-2"}]\n'
        "confidence: 0.8\n"
        'verification_status: "passed"\n'
        "---\n\n"
        "# Memory Growth report explains source-backed concepts.\n\n"
        "The report explains how Memory Growth connects evidence and concepts.\n",
        encoding="utf-8",
    )

    result = sync_memory_growth_graph()
    assert result.node_count >= 4
    assert result.edge_count >= 3
    assert result.event_path.exists()
    assert result.node_index_path.exists()
    assert result.edge_index_path.exists()

    nodes = json.loads(result.node_index_path.read_text(encoding="utf-8"))["nodes"]
    edges = json.loads(result.edge_index_path.read_text(encoding="utf-8"))["edges"]
    assert any(node["kind"] == "concept" for node in nodes)
    assert any(node["kind"] == "output" for node in nodes)
    assert any(edge["relation"] == "DERIVED_FROM" for edge in edges)
    assert any(edge["relation"] == "RELATED_BY_KEYWORDS" for edge in edges)


def test_growth_scheduler_can_sync_graph(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.growth_scheduler import run_growth_pipeline
    from l3_node.cognitive_kernel.runtime import (
        build_decision_contract,
        build_work_order,
        close_turn,
        verify_work_order,
    )

    contract = build_decision_contract(
        turn_id="ck-memory-growth-graph-pipeline-1",
        goal="create graph ready output",
        tool="core:reply",
        work_order_input='{"message":"graph output"}',
    )
    work = build_work_order(
        contract=contract,
        tool="core:reply",
        work_order_input='{"message":"graph output"}',
    )
    report = verify_work_order(
        turn_id=contract.turn_id,
        work_order=work,
        observation='{"ok":true}',
        elapsed_ms=1.0,
    )
    close_turn(
        turn_id=contract.turn_id,
        final_text="Memory Growth Graph Sync 已生成本地实体关系事件。",
        executed_work_orders=[work.work_order_id],
        verification_reports=[report],
    )

    result = run_growth_pipeline(sync_graph=True)
    assert result.graph_sync_result is not None
    assert result.graph_sync_result.node_count > 0
    assert result.graph_sync_result.edge_count > 0

    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["stages"]["graph_sync"] is not None
    assert payload["summary"]["graph_nodes"] > 0


def test_graph_connectors_sync_local_and_report_unconfigured_external_connectors(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))
    monkeypatch.delenv("JACHIN_COGNEE_ENDPOINT", raising=False)
    monkeypatch.delenv("JACHIN_GRAPHITI_ENDPOINT", raising=False)

    from l3_node.cognitive_kernel.graph_connectors import sync_graph_engine_connectors
    from l3_node.cognitive_kernel.graph_sync_adapter import sync_memory_growth_graph
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    concept_dir = root / "concepts" / "project_fact"
    concept_dir.mkdir(parents=True, exist_ok=True)
    (concept_dir / "connector-source.md").write_text(
        "---\n"
        'summary: "Connector source concept for graph sync."\n'
        'source_refs: [{"type":"raw_event","event_id":"raw-connector-1"}]\n'
        "confidence: 0.9\n"
        "---\n\n"
        "# Connector source concept for graph sync.\n",
        encoding="utf-8",
    )
    sync_memory_growth_graph()

    results = sync_graph_engine_connectors(["local_json_graph", "cognee", "graphiti"])
    by_id = {item.connector_id: item for item in results}
    assert by_id["local_json_graph"].ok is True
    assert by_id["local_json_graph"].status == "synced"
    assert by_id["cognee"].ok is False
    assert by_id["cognee"].status == "not_configured"
    assert by_id["graphiti"].ok is False
    assert by_id["graphiti"].status == "not_configured"
    assert (root / "graph" / "connectors" / "local_json_graph" / "latest_snapshot.json").exists()
    assert (root / "indexes" / "graph_connectors.json").exists()


def test_growth_scheduler_can_run_graph_connectors(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.growth_scheduler import run_growth_pipeline
    from l3_node.cognitive_kernel.runtime import (
        build_decision_contract,
        build_work_order,
        close_turn,
        verify_work_order,
    )

    contract = build_decision_contract(
        turn_id="ck-memory-growth-connector-pipeline-1",
        goal="create connector ready output",
        tool="core:reply",
        work_order_input='{"message":"connector output"}',
    )
    work = build_work_order(
        contract=contract,
        tool="core:reply",
        work_order_input='{"message":"connector output"}',
    )
    report = verify_work_order(
        turn_id=contract.turn_id,
        work_order=work,
        observation='{"ok":true}',
        elapsed_ms=1.0,
    )
    close_turn(
        turn_id=contract.turn_id,
        final_text="Memory Growth Connector 已准备好同步本地图谱。",
        executed_work_orders=[work.work_order_id],
        verification_reports=[report],
    )

    result = run_growth_pipeline(sync_graph=True, graph_connector_ids=["local_json_graph"])
    assert result.graph_connector_results
    assert result.graph_connector_results[0].connector_id == "local_json_graph"
    assert result.graph_connector_results[0].ok is True

    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["stages"]["graph_connectors"][0]["connector_id"] == "local_json_graph"
    assert payload["summary"]["graph_connectors_ok"] == 1


def test_memory_growth_http_registers_routes():
    from l3_node.memory_growth_http import register_memory_growth_routes

    class Router:
        def __init__(self):
            self.routes = []

        def add_get(self, path, handler):
            self.routes.append(("GET", path, handler.__name__))

        def add_post(self, path, handler):
            self.routes.append(("POST", path, handler.__name__))

    class App:
        def __init__(self):
            self.router = Router()

    app = App()
    register_memory_growth_routes(app)
    routes = {(method, path) for method, path, _ in app.router.routes}

    assert ("GET", "/api/v1/memory-growth/status") in routes
    assert ("POST", "/api/v1/memory-growth/pipeline") in routes
    assert ("POST", "/api/v1/memory-growth/weekly-review") in routes
    assert ("POST", "/api/v1/memory-growth/graph-sync") in routes
    assert ("POST", "/api/v1/memory-growth/connector-sync") in routes
    assert ("POST", "/api/v1/memory-growth/governance") in routes
    assert ("POST", "/api/v1/memory-growth/batch-governance") in routes


def test_memory_growth_http_pipeline_endpoint(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.runtime import (
        build_decision_contract,
        build_work_order,
        close_turn,
        verify_work_order,
    )
    from l3_node.memory_growth_http import handle_memory_growth_pipeline

    contract = build_decision_contract(
        turn_id="ck-memory-growth-http-pipeline-1",
        goal="create http pipeline evidence",
        tool="core:reply",
        work_order_input='{"message":"http pipeline"}',
    )
    work = build_work_order(
        contract=contract,
        tool="core:reply",
        work_order_input='{"message":"http pipeline"}',
    )
    report = verify_work_order(
        turn_id=contract.turn_id,
        work_order=work,
        observation='{"ok":true}',
        elapsed_ms=1.0,
    )
    close_turn(
        turn_id=contract.turn_id,
        final_text="Memory Growth HTTP 入口可以触发自生长管线。",
        executed_work_orders=[work.work_order_id],
        verification_reports=[report],
    )

    class Request:
        body_exists = True

        async def json(self):
            return {"sync_graph": True, "graph_connector_ids": ["local_json_graph"]}

    async def run_endpoint():
        response = await handle_memory_growth_pipeline(Request())
        assert response.status == 200
        return json.loads(response.text)

    payload = asyncio.run(run_endpoint())
    assert payload["ok"] is True
    assert payload["result"]["graph_sync_result"]["node_count"] > 0
    assert payload["result"]["graph_connector_results"][0]["connector_id"] == "local_json_graph"
    assert payload["status"]["counts"]["raw_events"] >= 1
    assert payload["status"]["counts"]["graph_nodes"] > 0


def test_memory_growth_http_status_includes_quality_monitoring(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import MemoryWriteRequest
    from l3_node.cognitive_kernel.memory_lifecycle import record_lifecycle_memory_feedback, write_lifecycle_memory
    from l3_node.cognitive_kernel.memory_growth import append_raw_event, ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.memory_growth_http import memory_growth_status

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    concept_dir = root / "concepts" / "project_fact"
    concept_dir.mkdir(parents=True, exist_ok=True)
    (concept_dir / "old-fact.md").write_text(
        "---\n"
        'summary: "Old project fact that needs review."\n'
        "last_verified: 2020-01-01\n"
        "confidence: 0.8\n"
        "---\n\n"
        "# Old project fact that needs review.\n",
        encoding="utf-8",
    )
    conflict_dir = root / "conflicts"
    conflict_dir.mkdir(parents=True, exist_ok=True)
    (conflict_dir / "needs-confirmation.json").write_text(
        json.dumps(
            {
                "reason": "requires_user_confirmation",
                "date": "2026-07-13",
                "candidate": {
                    "candidate_id": "concept:confirm:1",
                    "summary": "User prefers memory growth changes to be source backed.",
                    "requires_user_confirmation": True,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (conflict_dir / "tool-failed.json").write_text(
        json.dumps(
            {
                "reason": "tool_result_conflict",
                "date": "2026-07-13",
                "candidate": {"candidate_id": "concept:failed:1", "summary": "Tool result conflicted."},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    append_raw_event(
        category="evidence",
        source="unit_test",
        stream="turn_closure",
        payload={
            "turn_id": "failed-turn-1",
            "closure": {
                "verification_status": "failed",
                "failure_reason": "ocr_mismatch",
            },
        },
    )
    correction_content = json.dumps(
        {"type": "app_entity_correction", "surface_norm": "lock", "target_app": "Lark"},
        ensure_ascii=False,
        sort_keys=True,
    )
    write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="ck-memory-growth-correction-1",
            source_event="entity_correction_confirmed",
            memory_type="correction",
            content=correction_content,
            confidence=0.86,
            ttl="permanent",
            evidence=[{"ok": True}],
        )
    )
    record_lifecycle_memory_feedback(
        memory_type="correction",
        content=correction_content,
        ok=False,
        turn_id="ck-memory-growth-correction-2",
        failure_reason="app_focus_failed",
    )
    record_lifecycle_memory_feedback(
        memory_type="correction",
        content=correction_content,
        ok=False,
        turn_id="ck-memory-growth-correction-3",
        failure_reason="app_focus_failed",
    )

    status = memory_growth_status()
    monitoring = status["monitoring"]

    assert len(monitoring["trends"]["days_7"]) == 7
    assert len(monitoring["trends"]["days_14"]) == 14
    assert len(monitoring["trends"]["days_30"]) == 30
    assert any(row["raw_events"] >= 1 for row in monitoring["trends"]["days_7"])
    assert any(row["reason"] == "requires_user_confirmation" for row in monitoring["conflict_types"])
    assert any(row["reason"] == "tool_result_conflict" for row in monitoring["conflict_types"])
    assert any(row["reason"] == "last_verified_stale" for row in monitoring["stale_concepts"])
    assert any(row["reason"] == "requires_user_confirmation" for row in monitoring["pending_confirmation_queue"])
    assert any(row["kind"] == "memory_lifecycle_review" and row["reason"] == "app_focus_failed" for row in monitoring["pending_confirmation_queue"])
    assert any(str(row["pattern"]).startswith("conflict:tool_result_conflict") for row in monitoring["failure_patterns"])
    assert any(str(row["pattern"]).startswith("failed_turn:ocr_mismatch") for row in monitoring["failure_patterns"])
    assert monitoring["health"]["pending_confirmation_count"] >= 1


def test_memory_growth_governance_confirms_pending_and_writes_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import append_raw_event, ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.memory_growth_http import apply_memory_growth_governance, memory_growth_status

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    conflict_dir = root / "conflicts"
    conflict_dir.mkdir(parents=True, exist_ok=True)
    conflict_path = conflict_dir / "needs-confirmation.json"
    conflict_path.write_text(
        json.dumps(
            {
                "reason": "requires_user_confirmation",
                "date": "2026-07-13",
                "candidate": {
                    "candidate_id": "concept:confirm:governance",
                    "summary": "Confirmed governance knowledge should become a concept.",
                    "confidence": 0.91,
                    "requires_user_confirmation": True,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = apply_memory_growth_governance(
        action="confirm_pending",
        item={"path": "conflicts/needs-confirmation.json", "summary": "Confirmed governance knowledge"},
        note="unit test confirm",
    )

    assert result["action"] == "confirm_pending"
    assert result["report_path"]
    assert result["raw_event_path"]
    side_effect_types = {item["type"] for item in result["side_effects"]}
    assert "conflict_governance_status" in side_effect_types
    assert "confirmed_concept_written" in side_effect_types

    updated_conflict = json.loads(conflict_path.read_text(encoding="utf-8"))
    assert updated_conflict["governance"]["status"] == "confirmed"
    assert (root / "reviews" / "governance").exists()
    assert list((root / "concepts" / "confirmed").glob("*.md"))
    assert list((root / "raw" / "evidence").glob("*.governance.jsonl"))

    status = memory_growth_status()
    assert not status["monitoring"]["pending_confirmation_queue"]


def test_memory_growth_governance_generates_failure_playbook(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.memory_growth_http import apply_memory_growth_governance

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    result = apply_memory_growth_governance(
        action="generate_failure_playbook",
        item={"pattern": "failed_turn:ocr_mismatch", "count": 3, "examples": ["raw/evidence/example.jsonl"]},
        note="unit test playbook",
    )

    assert result["action"] == "generate_failure_playbook"
    assert any(item["type"] == "failure_playbook_written" for item in result["side_effects"])
    playbooks = list((root / "playbooks" / "recovery").glob("*.md"))
    assert playbooks
    text = playbooks[0].read_text(encoding="utf-8")
    assert "ocr_mismatch" in text
    assert "Recommended Recovery" in text


def test_memory_growth_status_includes_governance_history_and_recommendations(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import append_raw_event, ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.memory_growth_http import apply_memory_growth_governance, memory_growth_status

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    conflict_dir = root / "conflicts"
    conflict_dir.mkdir(parents=True, exist_ok=True)
    (conflict_dir / "needs-confirmation.json").write_text(
        json.dumps(
            {
                "reason": "requires_user_confirmation",
                "date": "2026-07-13",
                "candidate": {
                    "candidate_id": "concept:governance:queue",
                    "summary": "A governance queue item needs user confirmation.",
                    "requires_user_confirmation": True,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (conflict_dir / "tool-conflict.json").write_text(
        json.dumps({"reason": "tool_result_conflict", "date": "2026-07-13"}, ensure_ascii=False),
        encoding="utf-8",
    )
    stale_dir = root / "concepts" / "project"
    stale_dir.mkdir(parents=True, exist_ok=True)
    (stale_dir / "old.md").write_text(
        "---\nsummary: \"Old project concept\"\nlast_verified: \"2026-01-01\"\n---\n\n# Old project concept\n",
        encoding="utf-8",
    )

    apply_memory_growth_governance(
        action="generate_failure_playbook",
        item={"pattern": "failed_turn:ocr_mismatch", "count": 3, "examples": ["raw/evidence/example.jsonl"]},
        note="history seed",
    )
    append_raw_event(
        category="evidence",
        source="unit_test",
        stream="turn_closure",
        payload={
            "turn_id": "failed-after-governance",
            "closure": {
                "verification_status": "failed",
                "failure_reason": "ocr_mismatch",
            },
        },
    )
    status = memory_growth_status()
    monitoring = status["monitoring"]

    assert monitoring["governance_history"]
    assert monitoring["governance_history"][0]["action"] == "generate_failure_playbook"
    assert monitoring["health"]["governance_history_count"] >= 1

    effectiveness = monitoring["governance_effectiveness"]
    assert effectiveness["action_count"] >= 1
    assert effectiveness["generated_playbook_count"] >= 1
    assert effectiveness["post_governance_failure_count"] >= 1
    assert monitoring["health"]["governance_effectiveness_score"] == effectiveness["score"]

    recommendations = monitoring["governance_recommendations"]
    actions = {row["action"] for row in recommendations}
    assert "confirm_pending" in actions
    assert "revalidate_stale" in actions
    assert "generate_failure_playbook" in actions
    assert monitoring["health"]["recommendation_count"] >= 3


def test_memory_growth_batch_governance_executes_multiple_operations(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.memory_growth_http import apply_memory_growth_batch_governance, memory_growth_status

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    conflict_dir = root / "conflicts"
    conflict_dir.mkdir(parents=True, exist_ok=True)
    (conflict_dir / "needs-confirmation.json").write_text(
        json.dumps(
            {
                "reason": "requires_user_confirmation",
                "date": "2026-07-13",
                "candidate": {
                    "candidate_id": "concept:batch:confirm",
                    "summary": "Batch confirmed knowledge should become a concept.",
                    "requires_user_confirmation": True,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    stale_dir = root / "concepts" / "project"
    stale_dir.mkdir(parents=True, exist_ok=True)
    stale_path = stale_dir / "old.md"
    stale_path.write_text(
        "---\nsummary: \"Old batch concept\"\nlast_verified: \"2026-01-01\"\n---\n\n# Old batch concept\n",
        encoding="utf-8",
    )

    result = apply_memory_growth_batch_governance(
        operations=[
            {"action": "confirm_pending", "item": {"path": "conflicts/needs-confirmation.json"}, "note": "batch confirm"},
            {"action": "revalidate_stale", "item": {"path": "concepts/project/old.md"}, "note": "batch revalidate"},
        ],
        note="unit test batch",
    )

    assert result["executed_count"] == 2
    assert result["failed_count"] == 0
    assert result["report_path"].endswith(".batch.json")
    assert list((root / "reviews" / "governance").glob("*.batch.json"))
    assert list((root / "raw" / "evidence").glob("*.batch_governance.jsonl"))
    updated_conflict = json.loads((conflict_dir / "needs-confirmation.json").read_text(encoding="utf-8"))
    assert updated_conflict["governance"]["status"] == "confirmed"
    assert "revalidated_by_governance" in stale_path.read_text(encoding="utf-8")

    status = memory_growth_status()
    history = status["monitoring"]["governance_history"]
    assert any(row["action"] == "batch_governance" for row in history)


def test_weekly_review_includes_governance_effect_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold
    from l3_node.cognitive_kernel.weekly_review import run_weekly_review
    from l3_node.memory_growth_http import apply_memory_growth_batch_governance

    ensure_memory_growth_scaffold()
    apply_memory_growth_batch_governance(
            operations=[
                {"action": "generate_failure_playbook", "item": {"pattern": "failed_turn:ocr_mismatch", "count": 3}},
                {"action": "confirm_pending", "item": {"summary": "missing path should fail"}},
            ],
        note="weekly governance batch",
    )

    result = run_weekly_review(week_start="2026-07-13", stale_after_days=30)
    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["summary"]["governance_action_count"] >= 2
    assert payload["summary"]["governance_batch_count"] >= 1
    assert payload["summary"]["governance_failed_count"] >= 1
    assert payload["summary"]["governance_effectiveness_score"] >= 0
    assert payload["governance_effectiveness"]["action_count"] >= 2
    assert payload["governance_effectiveness"]["failure_count"] >= 1
    assert payload["governance_actions"]
    markdown = result.markdown_path.read_text(encoding="utf-8")
    assert "Governance actions" in markdown
    assert "Governance effectiveness score" in markdown


def test_governance_effectiveness_index_and_status_trends(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold
    from l3_node.cognitive_kernel.weekly_review import run_weekly_review
    from l3_node.memory_growth_http import apply_memory_growth_batch_governance, memory_growth_status

    root = ensure_memory_growth_scaffold()
    apply_memory_growth_batch_governance(
        operations=[
            {"action": "generate_failure_playbook", "item": {"pattern": "failed_turn:ocr_mismatch", "count": 3}},
            {"action": "confirm_pending", "item": {"summary": "missing path should fail"}},
        ],
        note="trend seed",
    )

    result = run_weekly_review(week_start="2026-07-13", stale_after_days=30)
    index_path = root / "indexes" / "governance_effectiveness.json"
    assert index_path.exists()
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["latest"]["week_id"] == result.week_id
    assert index["history"]
    assert "effective_actions" in index["attribution"]
    assert "ineffective_actions" in index["attribution"]

    status = memory_growth_status()
    monitoring = status["monitoring"]
    trends = monitoring["governance_effectiveness_trends"]
    assert trends["days_30"]
    assert trends["days_30"][-1]["week_id"] == result.week_id
    attribution = monitoring["governance_effectiveness_attribution"]
    assert attribution["latest"]["week_id"] == result.week_id
    assert attribution["ineffective_actions"]
    assert index["strategy_policy"]["action_policy"]
    assert monitoring["governance_strategy_policy"]["action_policy"]


def test_memory_growth_strategy_policy_adjusts_recommendation_priority(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold
    from l3_node.memory_growth_http import memory_growth_status

    root = ensure_memory_growth_scaffold()
    (root / "indexes").mkdir(parents=True, exist_ok=True)
    (root / "indexes" / "governance_effectiveness.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "latest": {"week_id": "2026-W29", "score": 50},
                "history": [
                    {"week_id": "2026-W28", "date": "2026-07-06", "score": 75},
                    {"week_id": "2026-W29", "date": "2026-07-13", "score": 50},
                ],
                "attribution": {
                    "effective_actions": [{"action": "confirm_pending", "success_count": 3}],
                    "ineffective_actions": [{"action": "generate_failure_playbook", "failure_count": 4}],
                    "repeated_failures": [],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    conflict_dir = root / "conflicts"
    conflict_dir.mkdir(parents=True, exist_ok=True)
    (conflict_dir / "needs-confirmation.json").write_text(
        json.dumps(
            {
                "reason": "requires_user_confirmation",
                "date": "2026-07-13",
                "candidate": {
                    "candidate_id": "concept:strategy:confirm",
                    "summary": "Strategy policy should allow this confirmation to be batched.",
                    "requires_user_confirmation": True,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (conflict_dir / "tool-conflict.json").write_text(
        json.dumps({"reason": "tool_result_conflict", "date": "2026-07-13"}, ensure_ascii=False),
        encoding="utf-8",
    )

    status = memory_growth_status()
    monitoring = status["monitoring"]
    policy = monitoring["governance_strategy_policy"]
    assert policy["global_mode"] == "cautious"
    assert policy["action_policy"]["confirm_pending"]["execution_mode"] == "batch_ok"
    assert policy["action_policy"]["generate_failure_playbook"]["execution_mode"] == "manual_review"

    recommendations = monitoring["governance_recommendations"]
    confirm = next(row for row in recommendations if row["action"] == "confirm_pending")
    playbook = next(row for row in recommendations if row["action"] == "generate_failure_playbook")
    assert confirm["strategy"]["execution_mode"] == "batch_ok"
    assert playbook["strategy"]["execution_mode"] == "manual_review"
    assert playbook["strategy"]["requires_more_evidence"] is True
    assert confirm["priority_score"] > playbook["priority_score"]


def test_weekly_review_writes_strategy_policy_to_artifact_frontmatter(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.cognitive_kernel.weekly_review import run_weekly_review
    from l3_node.memory_growth_http import apply_memory_growth_governance

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    conflict_dir = root / "conflicts"
    conflict_dir.mkdir(parents=True, exist_ok=True)
    (conflict_dir / "needs-confirmation.json").write_text(
        json.dumps(
            {
                "reason": "requires_user_confirmation",
                "date": "2026-07-13",
                "candidate": {
                    "candidate_id": "concept:strategy:persist",
                    "summary": "Strategy persistence should be written into confirmed concepts.",
                    "requires_user_confirmation": True,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    apply_memory_growth_governance(
        action="confirm_pending",
        item={"path": "conflicts/needs-confirmation.json"},
        note="confirm strategy seed",
    )
    apply_memory_growth_governance(
        action="generate_failure_playbook",
        item={"pattern": "failed_turn:window_focus_timeout", "count": 3},
        note="playbook strategy seed",
    )

    run_weekly_review(week_start="2026-07-13", stale_after_days=30)

    concept_path = next((root / "concepts" / "confirmed").glob("*.md"))
    playbook_path = next((root / "playbooks" / "recovery").glob("*.md"))
    concept_text = concept_path.read_text(encoding="utf-8")
    playbook_text = playbook_path.read_text(encoding="utf-8")
    assert 'governance_strategy_action: "confirm_pending"' in concept_text
    assert 'governance_execution_mode: "batch_ok"' in concept_text
    assert 'governance_strategy_action: "generate_failure_playbook"' in playbook_text
    assert 'governance_execution_mode: "batch_ok"' in playbook_text


def test_turn_closure_updates_memory_growth_artifact_usage(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import VerificationReport
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.cognitive_kernel.runtime import close_turn
    from l3_node.memory_growth_http import memory_growth_status

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    playbook_path = root / "playbooks" / "browser-open-focus.md"
    playbook_path.write_text(
        """---
id: "playbook:browser-open-focus"
type: "playbook"
summary: "Open browser and verify focus"
confidence: 0.82
last_verified: "2026-07-10T00:00:00Z"
---

# Open browser and verify focus
""",
        encoding="utf-8",
    )
    refs = [
        {
            "bucket": "failure_hints",
            "memory_id": "memory_growth:playbook:browser-open-focus",
            "source": "Memory Growth Playbooks",
            "artifact_path": str(playbook_path),
            "preview": "playbook path=" + str(playbook_path) + "; summary=Open browser and verify focus",
        }
    ]

    close_turn(
        turn_id="turn-artifact-success",
        final_text="ok",
        executed_work_orders=["work-1"],
        verification_reports=[VerificationReport(verification_id="verify-1", work_order_id="work-1", ok=True)],
        memory_context_refs=refs,
    )
    close_turn(
        turn_id="turn-artifact-failure",
        final_text="failed",
        executed_work_orders=["work-2"],
        verification_reports=[
            VerificationReport(
                verification_id="verify-2",
                work_order_id="work-2",
                ok=False,
                failure_reason="window_focus_timeout",
            )
        ],
        aborted=True,
        memory_context_refs=refs,
    )

    text = playbook_path.read_text(encoding="utf-8")
    assert "memory_use_count: 2" in text
    assert "memory_success_count: 1" in text
    assert "memory_failure_count: 1" in text
    assert "memory_success_rate: 0.5" in text
    assert 'memory_last_failure_reason: "window_focus_timeout"' in text

    usage_index = json.loads((root / "indexes" / "artifact_usage.json").read_text(encoding="utf-8"))
    row = next(item for item in usage_index["artifacts"] if item["path"] == "playbooks/browser-open-focus.md")
    assert row["memory_use_count"] == 2
    assert row["memory_success_count"] == 1
    assert row["memory_failure_count"] == 1
    assert row["memory_last_failure_reason"] == "window_focus_timeout"
    status_row = next(item for item in memory_growth_status()["monitoring"]["artifact_usage"] if item["path"] == "playbooks/browser-open-focus.md")
    assert status_row["memory_use_count"] == 2


def test_weekly_review_indexes_artifact_usage_trends_and_recommendations(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.contracts import VerificationReport
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.cognitive_kernel.runtime import close_turn
    from l3_node.cognitive_kernel.weekly_review import run_weekly_review
    from l3_node.memory_growth_http import memory_growth_status

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    good_path = root / "playbooks" / "browser-open-focus.md"
    bad_path = root / "playbooks" / "lark-send-unstable.md"
    for path, summary in (
        (good_path, "Open browser and verify focus"),
        (bad_path, "Send Lark message with unstable selector"),
    ):
        path.write_text(
            f"""---
id: "playbook:{path.stem}"
type: "playbook"
summary: "{summary}"
confidence: 0.82
last_verified: "2026-07-10T00:00:00Z"
---

# {summary}
""",
            encoding="utf-8",
        )

    good_refs = [{"bucket": "failure_hints", "memory_id": "memory_growth:playbook:browser-open-focus", "source": "Memory Growth Playbooks", "artifact_path": str(good_path)}]
    bad_refs = [{"bucket": "failure_hints", "memory_id": "memory_growth:playbook:lark-send-unstable", "source": "Memory Growth Playbooks", "artifact_path": str(bad_path)}]
    close_turn(
        turn_id="artifact-good-1",
        final_text="ok",
        executed_work_orders=["work-good-1"],
        verification_reports=[VerificationReport(verification_id="verify-good-1", work_order_id="work-good-1", ok=True)],
        memory_context_refs=good_refs,
    )
    close_turn(
        turn_id="artifact-good-2",
        final_text="ok",
        executed_work_orders=["work-good-2"],
        verification_reports=[VerificationReport(verification_id="verify-good-2", work_order_id="work-good-2", ok=True)],
        memory_context_refs=good_refs,
    )
    for index in range(2):
        close_turn(
            turn_id=f"artifact-bad-{index}",
            final_text="failed",
            executed_work_orders=[f"work-bad-{index}"],
            verification_reports=[
                VerificationReport(
                    verification_id=f"verify-bad-{index}",
                    work_order_id=f"work-bad-{index}",
                    ok=False,
                    failure_reason="ocr_send_button_missing",
                )
            ],
            aborted=True,
            memory_context_refs=bad_refs,
        )

    result = run_weekly_review(week_start="2026-07-13", stale_after_days=30)
    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["summary"]["artifact_usage_count"] >= 2
    assert payload["summary"]["artifact_total_use_count"] == 4
    assert payload["artifact_usage"]["top_successful_assets"]
    assert payload["artifact_usage"]["low_success_assets"]

    trend_path = root / "indexes" / "artifact_usage_trends.json"
    assert trend_path.exists()
    trend = json.loads(trend_path.read_text(encoding="utf-8"))
    assert trend["latest"]["week_id"] == result.week_id
    assert trend["latest"]["total_use_count"] == 4
    assert trend["attribution"]["best_playbooks"]
    assert trend["attribution"]["low_success_assets"]
    assert any(row["action"] == "rewrite_or_downrank" for row in trend["recommendations"])

    monitoring = memory_growth_status()["monitoring"]
    assert monitoring["artifact_usage_trends"]["days_30"]
    assert monitoring["artifact_usage_trends"]["days_30"][-1]["week_id"] == result.week_id
    assert monitoring["artifact_usage_attribution"]["low_success_assets"]
    assert monitoring["artifact_usage_recommendations"]


def test_artifact_governance_actions_update_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.memory_growth_http import apply_memory_growth_governance, memory_growth_status

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    low_path = root / "playbooks" / "low-success.md"
    good_path = root / "playbooks" / "good-playbook.md"
    stale_path = root / "concepts" / "project_fact" / "unused-concept.md"
    stale_path.parent.mkdir(parents=True, exist_ok=True)
    low_path.write_text(
        """---
id: "playbook:low-success"
type: "playbook"
summary: "Low success playbook"
memory_use_count: 3
memory_success_count: 0
memory_failure_count: 3
memory_success_rate: 0.0
memory_last_failure_reason: "ocr_send_button_missing"
---

# Low success playbook
""",
        encoding="utf-8",
    )
    good_path.write_text(
        """---
id: "playbook:good-playbook"
type: "playbook"
summary: "Good playbook"
memory_use_count: 4
memory_success_count: 4
memory_failure_count: 0
memory_success_rate: 1.0
---

# Good playbook
""",
        encoding="utf-8",
    )
    stale_path.write_text(
        """---
id: "concept:unused-concept"
type: "project_fact"
summary: "Unused concept"
memory_use_count: 0
memory_success_count: 0
memory_failure_count: 0
memory_success_rate: 0.0
---

# Unused concept
""",
        encoding="utf-8",
    )

    downrank = apply_memory_growth_governance(
        action="rewrite_or_downrank",
        item={"target": "playbooks/low-success.md", "reason": "low_success_rate"},
        note="unit downrank",
    )
    assert any(effect["type"] == "artifact_downranked" for effect in downrank["side_effects"])
    low_text = low_path.read_text(encoding="utf-8")
    assert 'governance_strategy_weight: "0.45"' in low_text
    assert 'artifact_review_status: "needs_rewrite"' in low_text

    recovery = apply_memory_growth_governance(
        action="create_or_update_recovery_playbook",
        item={"target": "playbooks/low-success.md", "reason": "ocr_send_button_missing"},
        note="unit recovery",
    )
    assert any(effect["type"] == "artifact_recovery_playbook_written" for effect in recovery["side_effects"])
    recovery_effect = next(effect for effect in recovery["side_effects"] if effect["type"] == "artifact_recovery_playbook_written")
    assert (root / recovery_effect["path"]).exists()

    promoted = apply_memory_growth_governance(
        action="promote_preferred_guidance",
        item={"target": "playbooks/good-playbook.md"},
        note="unit promote",
    )
    assert any(effect["type"] == "artifact_promoted_preferred_guidance" for effect in promoted["side_effects"])
    good_text = good_path.read_text(encoding="utf-8")
    assert 'preferred_guidance: "true"' in good_text
    assert 'governance_strategy_weight: "1.50"' in good_text

    archived = apply_memory_growth_governance(
        action="archive_or_revalidate",
        item={"target": "concepts/project_fact/unused-concept.md"},
        note="unit archive",
    )
    assert any(effect["type"] == "artifact_archived" for effect in archived["side_effects"])
    assert not stale_path.exists()
    assert (root / "archive" / "artifacts" / "concepts" / "project_fact" / "unused-concept.md").exists()

    status = memory_growth_status()
    actions = status["available_actions"]
    assert "artifact-governance" in actions


def test_artifact_curator_turns_rewrite_requests_into_drafts(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.artifact_curator import run_artifact_curator
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.memory_growth_http import apply_memory_growth_governance, memory_growth_status

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    artifact_path = root / "playbooks" / "weak-lark-send.md"
    artifact_path.write_text(
        """---
id: "playbook:weak-lark-send"
type: "playbook"
summary: "Weak Lark send playbook"
memory_use_count: 4
memory_success_count: 1
memory_failure_count: 3
memory_success_rate: 0.25
memory_last_failure_reason: "ocr_send_button_missing"
---

# Weak Lark send playbook

## Recommended Flow

1. Click send directly.
""",
        encoding="utf-8",
    )
    apply_memory_growth_governance(
        action="rewrite_or_downrank",
        item={"target": "playbooks/weak-lark-send.md", "reason": "low_success_rate"},
        note="unit curator seed",
    )

    result = run_artifact_curator(max_items=5)
    assert result.processed_count == 1
    assert result.skipped_count == 0
    assert result.draft_paths
    assert result.confirmation_paths
    draft = json.loads(result.draft_paths[0].read_text(encoding="utf-8"))
    assert draft["artifact_path"] == "playbooks/weak-lark-send.md"
    assert "ocr_send_button_missing" in draft["draft_markdown"]
    assert result.draft_paths[0].with_suffix(".md").exists()
    confirmation = json.loads(result.confirmation_paths[0].read_text(encoding="utf-8"))
    assert confirmation["reason"] == "artifact_rewrite_requires_user_confirmation"
    assert confirmation["candidate"]["requires_user_confirmation"] is True
    assert confirmation["candidate"]["draft_path"] == str(result.draft_paths[0].relative_to(root))

    request_path = next((root / "reviews" / "artifact_rewrites").glob("*.json"))
    request = json.loads(request_path.read_text(encoding="utf-8"))
    assert request["curation_status"] == "drafted"
    status = memory_growth_status()
    assert "artifact-curator" in status["available_actions"]
    assert status["latest"]["artifact_curator_report"]


def test_artifact_draft_merge_updates_source_with_backup(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.artifact_curator import run_artifact_curator
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.memory_growth_http import apply_memory_growth_governance

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    artifact_path = root / "playbooks" / "weak-browser-open.md"
    artifact_path.write_text(
        """---
id: "playbook:weak-browser-open"
type: "playbook"
summary: "Weak browser open playbook"
memory_use_count: 3
memory_success_count: 1
memory_failure_count: 2
memory_success_rate: 0.33
memory_last_failure_reason: "app_focus_failed"
---

# Weak browser open playbook

## Recommended Flow

1. Open browser once.
""",
        encoding="utf-8",
    )
    apply_memory_growth_governance(
        action="rewrite_or_downrank",
        item={"target": "playbooks/weak-browser-open.md", "reason": "low_success_rate"},
        note="unit merge seed",
    )
    curator = run_artifact_curator(max_items=5)
    draft_rel = str(curator.draft_paths[0].relative_to(root))
    confirmation_rel = str(curator.confirmation_paths[0].relative_to(root))

    merged = apply_memory_growth_governance(
        action="merge_artifact_draft",
        item={"draft_path": draft_rel, "confirmation_path": confirmation_rel},
        note="unit merge",
    )
    assert any(effect["type"] == "artifact_rewrite_merged" for effect in merged["side_effects"])
    assert any(effect["type"] == "artifact_backup_written" for effect in merged["side_effects"])
    updated = artifact_path.read_text(encoding="utf-8")
    assert "Rewrite Draft: Weak browser open playbook" in updated
    assert 'artifact_review_status: "rewritten"' in updated
    assert 'governance_strategy_action: "artifact_rewrite_merged"' in updated
    draft_payload = json.loads(curator.draft_paths[0].read_text(encoding="utf-8"))
    assert draft_payload["merge_status"] == "merged"
    assert (root / draft_payload["backup_path"]).exists()
    confirmation = json.loads(curator.confirmation_paths[0].read_text(encoding="utf-8"))
    assert confirmation["governance"]["status"] == "confirmed"


def test_confirm_pending_artifact_rewrite_merges_draft(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.artifact_curator import run_artifact_curator
    from l3_node.cognitive_kernel.memory_growth import ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.memory_growth_http import apply_memory_growth_governance

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    artifact_path = root / "concepts" / "project_fact" / "weak-fact.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        """---
id: "concept:weak-fact"
type: "project_fact"
summary: "Weak fact"
memory_use_count: 2
memory_success_count: 0
memory_failure_count: 2
memory_success_rate: 0.0
memory_last_failure_reason: "state_conflict"
---

# Weak fact
""",
        encoding="utf-8",
    )
    apply_memory_growth_governance(
        action="rewrite_or_downrank",
        item={"target": "concepts/project_fact/weak-fact.md", "reason": "low_success_rate"},
        note="unit pending merge seed",
    )
    curator = run_artifact_curator(max_items=5)
    confirmation_rel = str(curator.confirmation_paths[0].relative_to(root))
    result = apply_memory_growth_governance(
        action="confirm_pending",
        item={"path": confirmation_rel},
        note="confirm artifact rewrite",
    )
    assert any(effect["type"] == "artifact_rewrite_confirmed_via_pending" for effect in result["side_effects"])
    updated = artifact_path.read_text(encoding="utf-8")
    assert "Rewrite Draft: Weak fact" in updated
    confirmation = json.loads(curator.confirmation_paths[0].read_text(encoding="utf-8"))
    assert confirmation["governance"]["status"] == "confirmed"


def test_memory_growth_e2e_governance_curator_weekly_recall_loop(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from l3_node.cognitive_kernel.artifact_curator import run_artifact_curator
    from l3_node.cognitive_kernel.contracts import AgentInputEnvelope, InputSource, MemoryRecallRequest
    from l3_node.cognitive_kernel.memory_growth import append_raw_event, ensure_memory_growth_scaffold, memory_growth_dir
    from l3_node.cognitive_kernel.memory_growth_recall import recall_memory_growth
    from l3_node.cognitive_kernel.weekly_review import run_weekly_review
    from l3_node.memory_growth_http import apply_memory_growth_governance, memory_growth_status

    ensure_memory_growth_scaffold()
    root = memory_growth_dir()
    artifact_path = root / "playbooks" / "browser-open-focus.md"
    artifact_path.write_text(
        """---
id: "playbook:browser-open-focus"
type: "playbook"
summary: "Browser open focus recovery"
memory_use_count: 6
memory_success_count: 1
memory_failure_count: 5
memory_success_rate: 0.16
memory_last_failure_reason: "app_focus_failed"
---

# Browser open focus recovery

## Recommended Flow

1. Open browser and assume it is focused.
""",
        encoding="utf-8",
    )
    append_raw_event(
        category="evidence",
        source="unit_e2e",
        stream="verification",
        payload={
            "task_type": "app_control",
            "tool": "windows_app_open",
            "ok": False,
            "failure_reason": "app_focus_failed",
            "artifact_refs": ["playbooks/browser-open-focus.md"],
        },
        review={
            "review_candidate": True,
            "promotion_targets": ["playbooks"],
            "priority": "high",
            "reason": "e2e weak artifact",
        },
    )

    governance = apply_memory_growth_governance(
        action="rewrite_or_downrank",
        item={"target": "playbooks/browser-open-focus.md", "reason": "low_success_rate"},
        note="e2e governance seed",
    )
    assert any(effect["type"] == "artifact_downranked" for effect in governance["side_effects"])
    assert any(effect["type"] == "artifact_rewrite_request_written" for effect in governance["side_effects"])

    curator = run_artifact_curator(max_items=5)
    assert curator.processed_count == 1
    assert curator.confirmation_paths
    confirmation_rel = str(curator.confirmation_paths[0].relative_to(root))
    confirm = apply_memory_growth_governance(
        action="confirm_pending",
        item={"path": confirmation_rel},
        note="e2e approve rewrite",
    )
    assert any(effect["type"] == "artifact_rewrite_confirmed_via_pending" for effect in confirm["side_effects"])
    backup_effect = next(effect for effect in confirm["side_effects"] if effect["type"] == "artifact_backup_written")
    assert (root / backup_effect["path"]).exists()

    weekly = run_weekly_review(week_start="2026-07-13", stale_after_days=30)
    assert weekly.report_path.exists()
    status = memory_growth_status()
    assert status["latest"]["weekly_report"]
    assert status["latest"]["artifact_curator_report"]
    assert status["monitoring"]["artifact_usage"]

    envelope = AgentInputEnvelope(
        turn_id="memory-growth-e2e",
        source=InputSource.TEXT,
        raw_text="打开浏览器后焦点失败应该如何恢复",
        normalized_text="打开浏览器后焦点失败应该如何恢复",
    )
    request = MemoryRecallRequest(
        turn_id="memory-growth-e2e",
        input_envelope=envelope,
        candidate_intents=["app_control"],
        candidate_task_domains=["browser", "window_focus"],
        candidate_entities=["browser", "focus"],
        multi_queries={"task": "browser open app_focus_failed recovery"},
        retrieval_channels=["memory_growth_playbook_memory"],
    )
    recalled, gaps = recall_memory_growth(request, limit=5)
    recalled_ids = {item.memory_id for item in recalled}
    assert "memory_growth:playbook:browser-open-focus" in recalled_ids
    recalled_text = "\n".join(item.content for item in recalled)
    assert "app_focus_failed" in recalled_text
    assert "memory_growth_no_relevant_concepts_or_playbooks" not in gaps
