import json

from l3_node.voice_false_trigger_learning import (
    latest_voice_learning_summary,
    record_voice_false_trigger_learning,
    record_voice_owner_validation_result,
    voice_false_trigger_threshold_overrides,
)


def test_voice_learning_raises_noise_drop_threshold(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    for i in range(8):
        record_voice_false_trigger_learning(
            {
                "action": "drop",
                "reason_code": "low_confidence_non_action",
                "confidence": 0.24,
                "mode": "continuous_listen",
                "evidence": {"run_id": f"noise-{i}", "input_preview": "background"},
            },
            turn_id=f"noise-{i}",
        )

    thresholds = voice_false_trigger_threshold_overrides()

    assert thresholds["adaptive"] is True
    assert thresholds["continuous_non_action_drop_threshold"] > 0.38
    assert thresholds["continuous_non_action_drop_threshold"] <= 0.45


def test_voice_learning_lowers_confirm_threshold_after_user_confirms(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    for i in range(4):
        record_voice_false_trigger_learning(
            {
                "action": "confirm",
                "reason_code": "low_confidence_action",
                "confidence": 0.51,
                "mode": "continuous_listen",
                "evidence": {"run_id": f"confirm-{i}", "input_preview": "open lark"},
            },
            turn_id=f"confirm-{i}",
            source="voice_pending_confirmation",
            accepted_override=True,
        )

    thresholds = voice_false_trigger_threshold_overrides()

    assert thresholds["continuous_action_confirm_threshold"] < 0.55
    assert thresholds["continuous_action_confirm_threshold"] >= 0.48


def test_owner_voiceprint_validation_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    record_voice_owner_validation_result(
        result_type="pass_owner_only",
        accepted=True,
        score=0.72,
        threshold=0.62,
        reason="owner_accept",
        evidence={"source": "unit"},
    )

    summary = latest_voice_learning_summary()
    path = tmp_path / "state" / "voice_false_trigger_learning.jsonl"
    payload = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])

    assert summary["owner_validation_count"] == 1
    assert payload["reason_code"] == "owner_validation_pass_owner_only"
    assert payload["speaker"]["accepted"] is True
