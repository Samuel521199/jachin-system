import json

import pytest


def test_os_live_stress_learning_generalizes_and_blocks_missing_slots(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from scripts.os_live_stress_matrix import (
        correction_negative_feedback_reopens_review,
        learning_generalizes_after_guidance,
        missing_message_slots_blocks_execution,
    )

    learned = learning_generalizes_after_guidance(tmp_path / "kernel")
    assert learned["ok"], json.dumps(learned, ensure_ascii=False)
    assert learned["direct"]["name"] == "Lark"
    assert learned["fuzzy"]["name"] == "Lark"
    assert learned["plan"]["review_summary"]["target"]["source"] == "learned_entity_correction"
    assert learned["plan"]["decision_contract"]["execution_allowed"] is True

    negative = correction_negative_feedback_reopens_review(tmp_path / "kernel_negative")
    assert negative["ok"], json.dumps(negative, ensure_ascii=False)
    assert negative["direct"]["requires_confirmation"] is True
    assert negative["fuzzy"] == {}
    assert negative["plan"]["decision_contract"]["execution_allowed"] is False

    blocked = missing_message_slots_blocks_execution(tmp_path / "kernel")
    assert blocked["ok"], json.dumps(blocked, ensure_ascii=False)
    assert blocked["review"]["needs_clarification"] is True
    assert blocked["decision"]["execution_allowed"] is False


def test_os_live_stress_plans_common_workflows(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    from scripts.os_live_stress_matrix import (
        calculator_task_splits_to_open_and_calculate,
        close_uses_latest_under_long_recent_history,
        close_uses_latest_recent_app,
        file_read_open_reveal_planning,
        lifecycle_store_corruption_is_ignored,
        lark_message_has_slots_and_two_steps,
        planning_common_apps,
        recovery_attempt_limit_summary,
    )

    kernel_home = tmp_path / "kernel"
    assert planning_common_apps(kernel_home)["ok"]
    assert close_uses_latest_recent_app(kernel_home)["ok"]
    assert close_uses_latest_under_long_recent_history(kernel_home)["ok"]
    assert calculator_task_splits_to_open_and_calculate(kernel_home)["ok"]
    assert lark_message_has_slots_and_two_steps(kernel_home)["ok"]
    assert file_read_open_reveal_planning(kernel_home, tmp_path / "run")["ok"]
    assert recovery_attempt_limit_summary(kernel_home)["ok"]
    assert lifecycle_store_corruption_is_ignored(kernel_home)["ok"]


def test_live_confirmed_lark_recipient_allowlist_blocks_unknown_recipient():
    from scripts.os_live_stress_matrix import _validate_live_lark_recipients

    assert _validate_live_lark_recipients(["Neil", "测试备注冒烟草稿"]) == ["Neil", "测试备注冒烟草稿"]
    with pytest.raises(ValueError, match="live-confirmed Lark recipients"):
        _validate_live_lark_recipients(["Neil", "Vivian"])


def test_live_confirmed_lark_sender_validates_allowlist_before_tool_call(tmp_path, monkeypatch):
    import scripts.os_live_stress_matrix as matrix

    called = {"value": False}

    async def should_not_call(_work_order):
        called["value"] = True
        return "{}"

    monkeypatch.setattr(matrix, "_live_lark_executor", should_not_call)
    with pytest.raises(ValueError, match="live-confirmed Lark recipients"):
        matrix.live_confirmed_lark_send(tmp_path / "kernel", tmp_path / "run", ["Vivian"], "hello")
    assert called["value"] is False
