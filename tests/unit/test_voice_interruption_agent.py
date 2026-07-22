from l3_node.voice_interruption_agent import classify_voice_interruption


def _ctx(confidence=0.9):
    return {
        "voice_interaction_mode": "continuous_listen",
        "voice_raw_stt_text": "placeholder",
        "voice_stt_confidence": confidence,
        "voice_active_task_context": {
            "focused_task_id": "run-123",
            "active_tasks": [{"id": "run-123", "title": "发送 Lark 简报"}],
        },
    }


def test_voice_cancel_intercepts_active_task():
    decision = classify_voice_interruption("停一下，先别执行了", voice_context=_ctx(), run_id="turn-1")

    assert decision.action == "cancel"
    assert decision.should_intercept is True
    assert decision.should_cancel_run is True
    assert decision.target_task_id == "run-123"
    assert "explicit_cancel_or_stop" in decision.reasons


def test_low_confidence_control_requires_confirmation():
    decision = classify_voice_interruption("停一下", voice_context=_ctx(confidence=0.2), run_id="turn-2")

    assert decision.action == "confirm_required"
    assert decision.should_intercept is True
    assert decision.should_cancel_run is False


def test_side_chat_during_active_task_does_not_cancel():
    decision = classify_voice_interruption("你觉得这个方案怎么样？", voice_context=_ctx(), run_id="turn-3")

    assert decision.action == "side_chat"
    assert decision.should_intercept is True
    assert decision.should_cancel_run is False


def test_modify_current_task_continues_to_planner():
    decision = classify_voice_interruption("改成发给 Neil，不要发给 Vivian", voice_context=_ctx(), run_id="turn-4")

    assert decision.action == "modify_current_task"
    assert decision.should_intercept is False
    assert decision.should_cancel_run is False


def test_no_active_task_does_not_intercept():
    ctx = {
        "voice_interaction_mode": "continuous_listen",
        "voice_raw_stt_text": "取消",
        "voice_active_task_context": {"active_tasks": []},
    }
    decision = classify_voice_interruption("取消", voice_context=ctx, run_id="turn-5")

    assert decision.action == "none"
    assert decision.should_intercept is False
    assert "no_active_task" in decision.reasons
