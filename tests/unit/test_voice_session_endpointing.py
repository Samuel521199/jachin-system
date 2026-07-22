from l3_node.voice_session_endpointing import evaluate_voice_session_endpoint


def test_continuous_endpoint_waits_for_bare_action(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    decision = evaluate_voice_session_endpoint(
        "打开",
        voice_context={
            "voice_interaction_mode": "continuous_listen",
            "voice_stt_finalized": True,
            "voice_stt_confidence": 0.92,
        },
        run_id="endpoint-1",
        session_id="session-a",
    )

    assert decision.action == "wait"
    assert decision.reason_code == "bare_action_without_target"
    assert decision.should_continue_planning is False


def test_continuous_endpoint_merges_pending_fragment(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    first = evaluate_voice_session_endpoint(
        "打开",
        voice_context={"voice_interaction_mode": "continuous_listen", "voice_stt_finalized": True},
        run_id="endpoint-2a",
        session_id="session-b",
    )
    second = evaluate_voice_session_endpoint(
        "Lark",
        voice_context={"voice_interaction_mode": "continuous_listen", "voice_stt_finalized": True},
        run_id="endpoint-2b",
        session_id="session-b",
    )

    assert first.action == "wait"
    assert second.action == "merged"
    assert "打开" in second.effective_text
    assert "Lark" in second.effective_text
    assert second.should_continue_planning is True


def test_endpoint_bypasses_push_to_talk(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    decision = evaluate_voice_session_endpoint(
        "打开",
        voice_context={"voice_interaction_mode": "push_to_talk", "voice_stt_finalized": True},
        run_id="endpoint-3",
        session_id="session-c",
    )

    assert decision.action == "ready"
    assert decision.should_continue_planning is True
