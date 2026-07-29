from __future__ import annotations

import json
from pathlib import Path


def _manifest() -> dict:
    return {
        "id": "test.codex.recovery",
        "recovery_playbook": {
            "targets": [
                {
                    "id": "codex-staged",
                    "role_agent": "AppControlExecutorAgent",
                    "tools": ["mcp:windows_codex_work_plan_query"],
                    "max_attempts": 5,
                    "steps": [
                        {
                            "strategy": "expand_project_then_conversation",
                            "tool": "$same",
                            "when": {
                                "failure_any": [
                                    "navigate",
                                    "conversation_not_found",
                                ]
                            },
                            "action_patch": {
                                "recovery_stage": "navigate",
                                "navigation_strategy": (
                                    "expand_project_then_conversation"
                                ),
                            },
                            "rationale": "expand the project",
                            "priority": 10,
                        },
                        {
                            "strategy": "sidebar_search_conversation",
                            "tool": "$same",
                            "when": {
                                "failure_any": [
                                    "navigate",
                                    "conversation_not_found",
                                ]
                            },
                            "action_patch": {
                                "recovery_stage": "navigate",
                                "navigation_strategy": (
                                    "sidebar_search_conversation"
                                ),
                            },
                            "rationale": "search after project expansion failed",
                            "priority": 20,
                        },
                        {
                            "strategy": "extend_wait_window",
                            "tool": "$same",
                            "when": {"failure_any": ["wait", "timeout"]},
                            "action_patch": {
                                "recovery_stage": "wait",
                                "extend_seconds": 30,
                            },
                            "rationale": "extend without resubmitting",
                            "priority": 10,
                        },
                    ],
                }
            ]
        },
    }


def test_recovery_selects_one_new_path_after_each_failure():
    from l3_client.local_mcps.windows_uia_mcp.codex_stage_recovery import (
        CodexStageRecoveryPlanner,
    )

    planner = CodexStageRecoveryPlanner(manifests=[_manifest()])
    second_path = planner.observe_failure(
        stage="navigate_conversation",
        failure_reason="navigate:conversation_not_found",
        attempted_strategy="direct_conversation",
        evidence={"screenshot": "a.png"},
    )
    assert second_path is not None
    assert second_path.strategy == "expand_project_then_conversation"
    assert second_path.history_reasons == ["navigate:conversation_not_found"]

    third_path = planner.observe_failure(
        stage="navigate",
        failure_reason="navigate:conversation_not_found_after_project_expand",
        attempted_strategy=second_path.strategy,
        evidence={"screenshot": "b.png"},
    )
    assert third_path is not None
    assert third_path.strategy == "sidebar_search_conversation"
    assert third_path.history_reasons == [
        "navigate:conversation_not_found",
        "navigate:conversation_not_found_after_project_expand",
    ]

    exhausted = planner.observe_failure(
        stage="navigate",
        failure_reason="navigate:search_result_not_found",
        attempted_strategy=third_path.strategy,
    )
    assert exhausted is None
    assert [row.strategy for row in planner.decisions] == [
        "expand_project_then_conversation",
        "sidebar_search_conversation",
    ]


def test_recovery_does_not_cross_stage_or_retry_permission_failure():
    from l3_client.local_mcps.windows_uia_mcp.codex_stage_recovery import (
        CodexStageRecoveryPlanner,
    )

    planner = CodexStageRecoveryPlanner(manifests=[_manifest()])
    wait_path = planner.observe_failure(
        stage="wait_reply.timeout",
        failure_reason="wait:timeout:still_generating",
        attempted_strategy="bounded_wait",
    )
    assert wait_path is not None
    assert wait_path.strategy == "extend_wait_window"

    permission = planner.observe_failure(
        stage="wait",
        failure_reason="wait:permission_required",
        attempted_strategy="wait_for_reply",
    )
    assert permission is None
    assert all(
        decision.stage != "navigate"
        for decision in planner.decisions
    )


def test_recovery_writes_success_and_terminal_journal(tmp_path):
    from l3_client.local_mcps.windows_uia_mcp.codex_stage_recovery import (
        CodexStageRecoveryPlanner,
    )

    journal = tmp_path / "learned.jsonl"
    planner = CodexStageRecoveryPlanner(
        manifests=[_manifest()],
        journal_path=journal,
    )
    decision = planner.observe_failure(
        stage="navigate",
        failure_reason="navigate:conversation_not_found",
        attempted_strategy="direct_conversation",
    )
    assert decision is not None
    success = planner.record_success(
        stage="navigate",
        strategy=decision.strategy,
        evidence={"selected_path": "project_expand"},
    )
    terminal = planner.record_terminal_failure(
        final_reason="extract:reply_unverified"
    )

    rows = [
        json.loads(line)
        for line in journal.read_text(encoding="utf-8").splitlines()
    ]
    assert rows == [success, terminal]
    assert rows[0]["failure_history"][0]["failure_reason"].startswith(
        "navigate:"
    )
    assert rows[1]["recommended_next_steps"]


def test_windows_uia_manifest_declares_valid_codex_recovery_playbook():
    from l3_node.cognitive_kernel.recovery_playbook_schema import (
        validate_recovery_playbook_manifest,
    )

    path = (
        Path(__file__).resolve().parents[2]
        / "skills_repo"
        / "l1_upload_stubs"
        / "com.jachin.mcp.stub.windows.uia"
        / "plugin.json"
    )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert validate_recovery_playbook_manifest(manifest) == []
    targets = manifest["recovery_playbook"]["targets"]
    codex_target = next(
        target
        for target in targets
        if target["id"] == "windows_codex_work_plan_staged_recovery"
    )
    assert codex_target["max_attempts"] == 5
    assert {
        step["action_patch"]["recovery_stage"]
        for step in codex_target["steps"]
    } >= {"open", "navigate", "verify", "input", "submit", "wait", "extract", "fuse"}
