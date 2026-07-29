from __future__ import annotations

from copy import deepcopy

from l3_node.codex_release_gate import evaluate_release_gate


def _evidence(
    scenario: str,
    *,
    status: str = "succeeded",
    stage: str = "reply_validated",
    detail: str = "scenario_complete",
) -> dict:
    return {
        "scenario": scenario,
        "evidence": {
            "scenario": scenario,
            "invocation_id": f"jcx-{scenario}",
            "detail": detail,
            "invocation_manager_final": {
                "status": status,
                "stage": stage,
                "detail": detail,
                "history": [
                    {
                        "status": status,
                        "stage": stage,
                        "detail": detail,
                    }
                ],
            },
            "timeline": [
                {"stage": stage, "status": status, "detail": detail}
            ],
        },
    }


def _ready_matrix() -> list[dict]:
    baseline = _evidence("baseline")
    baseline["evidence"].update(
        {
            "context_verification": {"ok": True},
            "reply_selection": {
                "ok": True,
                "answer": "A complete correlated answer.",
                "validation": {"ok": True, "issues": []},
            },
        }
    )
    baseline["evidence"]["timeline"].extend(
        [
            {
                "stage": "verify_codex_work_plan_context",
                "status": "done",
            },
            {"stage": "submit_prompt", "status": "done"},
        ]
    )

    wrong_context = _evidence(
        "wrong_context",
        status="failed",
        stage="verify_codex_work_plan_context",
        detail="project_context_mismatch",
    )

    collapsed = _evidence("collapsed_project")
    collapsed["evidence"]["recovery"] = {
        "attempts": [
            {
                "stage": "navigate",
                "strategy": "direct_conversation",
                "failure_reason": "conversation_not_found",
            }
        ],
        "decisions": [
            {"strategy": "expand_project_then_conversation"}
        ],
        "max_attempts": 5,
    }

    busy = _evidence("busy_queue")
    busy["queue_assertions"] = {
        "unique_invocations": True,
        "serialized_lease": True,
        "no_prompt_overlap": True,
        "detail": "second invocation waited for the first lease",
    }

    permission = _evidence(
        "permission_required",
        status="failed",
        stage="permission_required",
        detail="permission approval required",
    )
    permission["evidence"]["recovery_pending_user_confirmation"] = {
        "reason": "approval_required"
    }

    timeout = _evidence(
        "network_timeout",
        status="failed",
        stage="wait_reply_timeout",
        detail="network_timeout",
    )
    timeout["evidence"]["recovery"] = {
        "attempts": [{"failure_reason": "network_timeout"}],
        "decisions": [],
        "max_attempts": 5,
    }
    timeout["evidence"]["recovery_terminal"] = {
        "recommended_next_steps": ["Check network and resume wait stage."]
    }

    conflict = _evidence(
        "fact_conflict",
        status="failed",
        stage="claim_fusion_conflict",
        detail="Codex claim conflicts with test evidence",
    )
    conflict["evidence"]["claim_fusion"] = {
        "conflicts": [{"claim": "tests passed", "evidence": "tests failed"}],
        "delivery_blocked": True,
        "raw_answer_used_as_final": False,
    }
    return [
        baseline,
        wrong_context,
        collapsed,
        busy,
        permission,
        timeout,
        conflict,
    ]


def test_complete_release_matrix_is_ready():
    result = evaluate_release_gate(_ready_matrix())

    assert result["release_ready"]
    assert result["passed_scenarios"] == 7
    assert result["invariants_ok"]
    assert result["missing_scenarios"] == []


def test_release_gate_accepts_generator_without_losing_invariants():
    result = evaluate_release_gate(row for row in _ready_matrix())

    assert result["release_ready"]
    assert result["scenario_count"] == 7
    assert result["invariants"]["wrong_context_submits"] == 0


def test_wrong_context_submission_blocks_release():
    matrix = _ready_matrix()
    wrong = next(row for row in matrix if row["scenario"] == "wrong_context")
    wrong["evidence"]["timeline"].append(
        {"stage": "submit_prompt", "status": "done"}
    )

    result = evaluate_release_gate(matrix)

    assert not result["release_ready"]
    assert "wrong_context" in result["failed_scenarios"]
    assert result["invariants"]["wrong_context_submits"] == 1


def test_stale_reply_and_raw_codex_use_block_release():
    matrix = deepcopy(_ready_matrix())
    baseline = next(row for row in matrix if row["scenario"] == "baseline")
    baseline["evidence"]["reply_selection"]["validation"]["issues"] = [
        "invocation_marker_mismatch"
    ]
    conflict = next(row for row in matrix if row["scenario"] == "fact_conflict")
    conflict["evidence"]["claim_fusion"]["raw_answer_used_as_final"] = True

    result = evaluate_release_gate(matrix)

    assert not result["release_ready"]
    assert result["invariants"]["stale_reply_accepts"] == 1
    assert result["invariants"]["raw_codex_direct_use"] == 1
    assert "fact_conflict" in result["failed_scenarios"]


def test_failure_without_terminal_evidence_blocks_release():
    matrix = _ready_matrix()
    timeout = next(row for row in matrix if row["scenario"] == "network_timeout")
    timeout["evidence"]["invocation_manager_final"].pop("history")

    result = evaluate_release_gate(matrix)

    assert not result["release_ready"]
    assert result["invariants"]["failures_without_complete_evidence"] == 1
    assert "network_timeout" in result["failed_scenarios"]


def test_codex_profile_recognizes_store_desktop_process():
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import APP_PROFILES

    profile = APP_PROFILES["codex"]
    assert "chatgpt" in profile["keywords"]
    assert "ChatGPT.exe" in profile["exe_names"]
    assert "\\openai.codex_" in profile["process_path_markers"]


class _ForegroundWindowStub:
    def __init__(self, process_path: str) -> None:
        self.process_path = process_path

    def active_snapshot(self) -> dict:
        return {
            "title": "ChatGPT",
            "process": "ChatGPT.exe",
            "process_path": self.process_path,
        }


def test_codex_environment_rejects_unrelated_chatgpt_process():
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import (
        EnvironmentVerifier,
        _app_contract,
    )

    verifier = EnvironmentVerifier(
        _ForegroundWindowStub(
            r"C:\Program Files\ChatGPT\ChatGPT.exe"
        )
    )

    result = verifier.verify(_app_contract("codex"))

    assert not result.ok
    assert result.detail == "foreground_process_identity_mismatch"
    assert not result.checks["process_path_ok"]


def test_codex_environment_accepts_store_codex_package_process():
    from l3_client.local_mcps.windows_uia_mcp.os_tasks import (
        EnvironmentVerifier,
        _app_contract,
    )

    verifier = EnvironmentVerifier(
        _ForegroundWindowStub(
            r"C:\Program Files\WindowsApps"
            r"\OpenAI.Codex_26.721.3404.0_x64__2p2nqsd0c76g0"
            r"\app\ChatGPT.exe"
        )
    )

    result = verifier.verify(_app_contract("codex"))

    assert result.ok
    assert result.detail == "environment_verified"
    assert result.checks["process_path_ok"]
