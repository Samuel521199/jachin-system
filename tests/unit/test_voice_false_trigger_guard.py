import time

from l3_node.voice_false_trigger_guard import evaluate_voice_false_trigger


def test_continuous_voice_drops_filler():
    decision = evaluate_voice_false_trigger(
        "嗯",
        voice_context={"voice_interaction_mode": "continuous_listen", "voice_stt_confidence": 0.92},
        run_id="turn-filler",
    )

    assert decision.action == "drop"
    assert decision.reason_code == "filler_or_backchannel"
    assert decision.should_continue_planning is False


def test_continuous_voice_confirms_low_confidence_action():
    decision = evaluate_voice_false_trigger(
        "打开浏览器",
        voice_context={"voice_interaction_mode": "continuous_listen", "voice_stt_confidence": 0.31},
        run_id="turn-low",
    )

    assert decision.action == "confirm"
    assert decision.reason_code == "low_confidence_action"
    assert "确认执行" in decision.user_visible_reply


def test_push_to_talk_allows_reasonable_lowish_confidence_command():
    decision = evaluate_voice_false_trigger(
        "打开 Lark",
        voice_context={"voice_interaction_mode": "push_to_talk", "voice_stt_confidence": 0.43},
        run_id="turn-ptt",
    )

    assert decision.action == "allow"
    assert decision.should_continue_planning is True


def test_duplicate_fragment_is_dropped():
    decision = evaluate_voice_false_trigger(
        "打开浏览器",
        voice_context={
            "voice_interaction_mode": "continuous_listen",
            "voice_stt_confidence": 0.9,
            "voice_last_text": "打开浏览器",
            "voice_last_text_at_ms": int(time.time() * 1000),
        },
        run_id="turn-dupe",
    )

    assert decision.action == "drop"
    assert decision.reason_code == "duplicate_fragment"


def test_confirmed_pending_voice_skips_guard_once():
    decision = evaluate_voice_false_trigger(
        "确认执行",
        voice_context={
            "voice_interaction_mode": "continuous_listen",
            "voice_stt_confidence": 0.2,
            "voice_false_trigger_skip_once": True,
        },
        run_id="turn-confirmed",
    )

    assert decision.action == "allow"
    assert decision.reason_code == "confirmed_pending_voice"


def test_provisional_streaming_fragment_is_dropped():
    decision = evaluate_voice_false_trigger(
        "打开浏览",
        voice_context={
            "voice_interaction_mode": "continuous_listen",
            "voice_stt_confidence": 0.88,
            "voice_stt_provisional": True,
        },
        run_id="turn-provisional",
    )

    assert decision.action == "drop"
    assert decision.reason_code == "stt_not_finalized"


def test_incomplete_action_fragment_is_dropped():
    decision = evaluate_voice_false_trigger(
        "打开",
        voice_context={"voice_interaction_mode": "continuous_listen", "voice_stt_confidence": 0.93},
        run_id="turn-incomplete",
    )

    assert decision.action == "drop"
    assert decision.reason_code == "incomplete_action_fragment"


def test_non_owner_speaker_is_dropped():
    decision = evaluate_voice_false_trigger(
        "打开浏览器",
        voice_context={
            "voice_interaction_mode": "continuous_listen",
            "voice_stt_confidence": 0.94,
            "voice_speaker_verified": False,
        },
        run_id="turn-non-owner",
    )

    assert decision.action == "drop"
    assert decision.reason_code == "non_owner_speaker"


def test_ambiguous_speaker_requires_confirmation_for_action():
    decision = evaluate_voice_false_trigger(
        "打开浏览器",
        voice_context={
            "voice_interaction_mode": "continuous_listen",
            "voice_stt_confidence": 0.94,
            "voice_speaker_verification_status": "ambiguous",
        },
        run_id="turn-ambiguous-speaker",
    )

    assert decision.action == "confirm"
    assert decision.reason_code == "speaker_verification_ambiguous"


def test_continuous_action_without_speaker_evidence_requires_confirmation():
    decision = evaluate_voice_false_trigger(
        "open wechat",
        voice_context={"voice_interaction_mode": "continuous_listen", "voice_stt_confidence": 0.94},
        run_id="turn-missing-speaker",
    )

    assert decision.action == "confirm"
    assert decision.reason_code == "speaker_verification_ambiguous"


def test_continuous_action_with_owner_evidence_is_allowed():
    decision = evaluate_voice_false_trigger(
        "open wechat",
        voice_context={
            "voice_interaction_mode": "continuous_listen",
            "voice_stt_confidence": 0.94,
            "voice_owner_track_accepted": True,
            "voice_owner_track_reason": "sv_owner_track_ok",
            "voice_owner_duration_ms": 1400,
            "voice_total_duration_ms": 1800,
            "voice_owner_skipped_segments_count": 1,
        },
        run_id="turn-owner-speaker",
    )

    assert decision.action == "allow"
    assert decision.should_continue_planning is True


def test_message_send_without_recipient_uses_slot_fill_not_generic_confirmation():
    decision = evaluate_voice_false_trigger(
        "send message hello",
        voice_context={
            "voice_interaction_mode": "continuous_listen",
            "voice_stt_confidence": 0.62,
            "voice_owner_track_accepted": True,
            "voice_owner_track_reason": "sv_owner_track_ok",
            "voice_owner_duration_ms": 1200,
            "voice_total_duration_ms": 1500,
            "voice_owner_skipped_segments_count": 0,
        },
        run_id="turn-message-slot-fill",
    )

    assert decision.action == "allow"
    assert decision.reason_code == "accepted"
    assert decision.should_continue_planning is True


def test_continuous_action_with_accepted_but_missing_owner_metrics_is_allowed():
    decision = evaluate_voice_false_trigger(
        "open wechat",
        voice_context={
            "voice_interaction_mode": "continuous_listen",
            "voice_stt_confidence": 0.96,
            "voice_owner_track_accepted": True,
            "voice_owner_track_reason": "rust_owner_track_ok",
        },
        run_id="turn-owner-metrics-missing",
    )

    assert decision.action == "allow"
    assert decision.reason_code == "accepted"
    assert decision.should_continue_planning is True


def test_continuous_noise_with_weak_owner_ratio_is_dropped():
    decision = evaluate_voice_false_trigger(
        "they are talking near the desk",
        voice_context={
            "voice_interaction_mode": "continuous_listen",
            "voice_stt_confidence": 0.91,
            "voice_owner_track_accepted": True,
            "voice_owner_track_reason": "sv_owner_track_ok",
            "voice_owner_duration_ms": 300,
            "voice_total_duration_ms": 2200,
            "voice_owner_skipped_segments_count": 8,
        },
        run_id="turn-weak-owner-noise",
    )

    assert decision.action == "drop"
    assert decision.reason_code == "weak_owner_evidence_noise"


def test_low_confidence_non_action_is_dropped_in_continuous_mode():
    decision = evaluate_voice_false_trigger(
        "旁边有人在聊天",
        voice_context={"voice_interaction_mode": "continuous_listen", "voice_stt_confidence": 0.24},
        run_id="turn-low-noise",
    )

    assert decision.action == "drop"
    assert decision.reason_code == "low_confidence_non_action"
