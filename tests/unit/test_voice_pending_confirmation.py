from l3_node.cognitive_kernel.voice_pending_confirmation import (
    load_pending_voice_confirmation,
    resolve_pending_voice_confirmation,
    save_pending_voice_confirmation,
)


def test_voice_pending_confirmation_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    save_pending_voice_confirmation(
        original_user_input="打开浏览器",
        normalized_text="打开浏览器",
        guard={"action": "confirm", "reason_code": "low_confidence_action"},
        turn_id="turn-a",
        session_id="session-a",
        channel="voice",
    )

    pending = load_pending_voice_confirmation(session_id="session-a", channel="voice")
    assert pending is not None
    assert pending.normalized_text == "打开浏览器"

    resolved = resolve_pending_voice_confirmation(
        reply_text="确认执行",
        session_id="session-a",
        channel="voice",
        turn_id="turn-b",
    )

    assert resolved is not None
    assert resolved["action"] == "confirm"
    assert resolved["pending"]["normalized_text"] == "打开浏览器"
    assert load_pending_voice_confirmation(session_id="session-a", channel="voice") is None


def test_voice_pending_confirmation_cancel(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    save_pending_voice_confirmation(
        original_user_input="发送消息给 Neil",
        normalized_text="发送消息给 Neil",
        guard={"action": "confirm", "reason_code": "risky_action_requires_voice_confirmation"},
        turn_id="turn-a",
        session_id="session-b",
        channel="voice",
    )

    resolved = resolve_pending_voice_confirmation(
        reply_text="取消",
        session_id="session-b",
        channel="voice",
        turn_id="turn-b",
    )

    assert resolved is not None
    assert resolved["action"] == "cancel"
    assert load_pending_voice_confirmation(session_id="session-b", channel="voice") is None


def test_voice_pending_confirmation_updates_message_slots_before_confirm(tmp_path, monkeypatch):
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path))

    save_pending_voice_confirmation(
        original_user_input="发送消息，你好，给 New。",
        normalized_text="发送消息，你好，给 New。",
        guard={"action": "confirm", "reason_code": "risky_action_requires_voice_confirmation"},
        turn_id="turn-a",
        session_id="session-c",
        channel="voice",
    )

    updated = resolve_pending_voice_confirmation(
        reply_text="可以，你把你把这个消耗数据给老张。",
        session_id="session-c",
        channel="voice",
        turn_id="turn-b",
    )

    assert updated is not None
    assert updated["action"] == "update"
    assert "老张" in updated["pending"]["normalized_text"]
    assert "这个消耗数据" in updated["pending"]["normalized_text"]
    assert "New" not in updated["pending"]["normalized_text"]

    confirmed = resolve_pending_voice_confirmation(
        reply_text="确认执行",
        session_id="session-c",
        channel="voice",
        turn_id="turn-c",
    )

    assert confirmed is not None
    assert confirmed["action"] == "confirm"
    assert "老张" in confirmed["pending"]["normalized_text"]
    assert "这个消耗数据" in confirmed["pending"]["normalized_text"]
    assert load_pending_voice_confirmation(session_id="session-c", channel="voice") is None
