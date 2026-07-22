from l3_node.voice_interruption_agent import classify_voice_interruption
from l3_node.cognitive_kernel.input_adapter import adapt_input_for_cognitive_kernel
from l3_node.voice_task_replan import (
    apply_voice_task_replan_to_input,
    build_voice_task_replan_patch,
)


def _ctx():
    return {
        "voice_interaction_mode": "continuous_listen",
        "voice_raw_stt_text": "placeholder",
        "voice_stt_confidence": 0.92,
        "voice_active_task_context": {
            "focused_task_id": "run-123",
            "active_tasks": [{"id": "run-123", "title": "发送 Lark 消息给 Vivian"}],
        },
    }


def test_replan_patch_replaces_and_removes_recipient():
    text = "改成发给 Neil，不要发给 Vivian"
    decision = classify_voice_interruption(text, voice_context=_ctx(), run_id="turn-1")

    patch = build_voice_task_replan_patch(
        text,
        voice_context=_ctx(),
        interruption_decision=decision.to_dict(),
        run_id="turn-1",
    )

    assert patch.is_replan is True
    assert patch.patch_type in {"recipient_change", "mixed_change"}
    assert patch.recipient_replace == ["Neil"]
    assert patch.recipient_remove == ["Vivian"]
    assert patch.requires_confirmation is False
    assert "收件人改为：Neil" in patch.replanned_instruction
    assert "不要发送给：Vivian" in patch.replanned_instruction


def test_replan_patch_changes_message_content():
    text = "内容换成你好，今晚不用开会了"
    decision = classify_voice_interruption(text, voice_context=_ctx(), run_id="turn-2")

    patch = build_voice_task_replan_patch(
        text,
        voice_context=_ctx(),
        interruption_decision=decision.to_dict(),
        run_id="turn-2",
    )

    assert patch.is_replan is True
    assert patch.patch_type == "content_change"
    assert patch.message_content == "你好，今晚不用开会了"
    assert "消息内容改为：你好，今晚不用开会了" in patch.replanned_instruction


def test_replan_apply_returns_effective_instruction():
    text = "改成发给 Neil"
    decision = classify_voice_interruption(text, voice_context=_ctx(), run_id="turn-3")
    patch = build_voice_task_replan_patch(
        text,
        voice_context=_ctx(),
        interruption_decision=decision.to_dict(),
        run_id="turn-3",
    )

    effective = apply_voice_task_replan_to_input(text, patch)

    assert effective != text
    assert "修正当前正在执行的任务" in effective
    assert "收件人改为：Neil" in effective


def test_replan_without_active_task_is_not_applied():
    ctx = {
        "voice_interaction_mode": "continuous_listen",
        "voice_raw_stt_text": "placeholder",
        "voice_active_task_context": {"active_tasks": []},
    }
    text = "改成发给 Neil"
    decision = classify_voice_interruption(text, voice_context=ctx, run_id="turn-4")

    patch = build_voice_task_replan_patch(
        text,
        voice_context=ctx,
        interruption_decision=decision.to_dict(),
        run_id="turn-4",
    )

    assert patch.is_replan is False
    assert apply_voice_task_replan_to_input(text, patch) == text


def test_input_adapter_attaches_voice_replan_patch():
    companion = _ctx()
    companion["voice_raw_stt_text"] = "改成发给 Neil，不要发给 Vivian"

    adaptation = adapt_input_for_cognitive_kernel(
        turn_id="turn-5",
        user_input="改成发给 Neil，不要发给 Vivian",
        channel="voice",
        session_id="session-1",
        desktop_companion_context=companion,
    )

    patch = companion.get("voice_task_replan_patch") or {}
    assert patch.get("is_replan") is True
    assert patch.get("recipient_replace") == ["Neil"]
    assert patch.get("recipient_remove") == ["Vivian"]
    assert (adaptation.modality_evidence.get("voice_task_replan") or {}).get("patch_type") in {
        "recipient_change",
        "mixed_change",
    }


def test_replan_patch_understands_normal_chinese_recipient_change(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))
    text = "不是发给 Neil，改成 Vivian"
    decision = classify_voice_interruption(text, voice_context=_ctx(), run_id="turn-cn-1")

    patch = build_voice_task_replan_patch(
        text,
        voice_context=_ctx(),
        interruption_decision=decision.to_dict(),
        run_id="turn-cn-1",
    )

    assert patch.is_replan is True
    assert patch.recipient_replace == ["Vivian"]


def test_replan_uses_recent_active_task_context_when_current_context_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))
    seed_text = "改成发给 Neil"
    seed_decision = classify_voice_interruption(seed_text, voice_context=_ctx(), run_id="turn-cn-2a")
    seed_patch = build_voice_task_replan_patch(
        seed_text,
        voice_context=_ctx(),
        interruption_decision=seed_decision.to_dict(),
        run_id="turn-cn-2a",
    )
    assert seed_patch.is_replan is True

    text = "内容改成今天测试通过"
    sparse_ctx = {
        "voice_interaction_mode": "continuous_listen",
        "voice_raw_stt_text": text,
        "voice_stt_confidence": 0.94,
    }
    decision = classify_voice_interruption(text, voice_context=sparse_ctx, run_id="turn-cn-2b")
    patch = build_voice_task_replan_patch(
        text,
        voice_context=sparse_ctx,
        interruption_decision=decision.to_dict(),
        run_id="turn-cn-2b",
    )

    assert patch.is_replan is True
    assert patch.message_content == "今天测试通过"
