from dataclasses import dataclass

from l3_node.voice_evidence_agent import (
    attach_voice_runtime_ui_protocol,
    build_voice_runtime_ui_protocol,
    record_voice_evidence_snapshot,
)


@dataclass
class _Source:
    value: str


@dataclass
class _Adaptation:
    source: _Source
    raw_text: str
    normalized_text: str
    confidence: float
    changed: bool


@dataclass
class _Contract:
    task_type: str = "message_delivery"
    goal: str = "send a message"
    selected_workflow: str = "lark_send"
    execution_allowed: bool = True
    risk_level: str = "high"


@dataclass
class _WorkOrder:
    work_order_id: str = "wo-1"
    role_agent: str = "MessageExecutorAgent"


@dataclass
class _Plan:
    decision_contract: _Contract
    work_orders: list[_WorkOrder]


def _voice_ctx():
    return {
        "voice_interaction_mode": "continuous_listen",
        "voice_raw_stt_text": "改成发给 Neil",
        "voice_stt_confidence": 0.91,
        "input_adapter_steps": [{"name": "voice_language_normalizer"}],
        "voice_false_trigger_guard": {"action": "allow", "reason_code": "accepted"},
        "voice_interruption_decision": {"action": "modify_current_task", "confidence": 0.78},
        "voice_task_replan_patch": {"is_replan": True, "patch_type": "recipient_change"},
    }


def test_voice_evidence_snapshot_collects_voice_path():
    payload = record_voice_evidence_snapshot(
        turn_id="turn-voice",
        stage="planning_finished",
        companion=_voice_ctx(),
        adaptation=_Adaptation(
            source=_Source("voice"),
            raw_text="改成发给 Neil",
            normalized_text="改成发给 Neil",
            confidence=0.91,
            changed=False,
        ),
        plan=_Plan(decision_contract=_Contract(), work_orders=[_WorkOrder()]),
    )

    assert payload is not None
    assert payload["type"] == "voice_evidence"
    assert payload["stage"] == "planning_finished"
    assert payload["voice_interaction_mode"] == "continuous_listen"
    assert payload["false_trigger_guard"]["reason_code"] == "accepted"
    assert payload["interruption"]["action"] == "modify_current_task"
    assert payload["replan"]["patch_type"] == "recipient_change"
    assert payload["planning"]["task_type"] == "message_delivery"
    assert payload["planning"]["work_order_count"] == 1


def test_voice_evidence_snapshot_skips_text_turn():
    payload = record_voice_evidence_snapshot(
        turn_id="turn-text",
        stage="input_adapted",
        companion={},
        adaptation=_Adaptation(
            source=_Source("text"),
            raw_text="hello",
            normalized_text="hello",
            confidence=0.9,
            changed=False,
        ),
    )

    assert payload is None


def test_voice_runtime_ui_protocol_attaches_compact_chat_state():
    ctx = _voice_ctx()
    ctx["input_adapter_normalized_text"] = "打开 Lark"
    ctx["voice_language_normalization"] = {
        "correction": {
            "corrections": [
                {
                    "original": "lock",
                    "canonical": "Lark",
                    "kind": "app",
                    "reason": "builtin_alias",
                }
            ]
        }
    }
    rendered = attach_voice_runtime_ui_protocol(
        "已打开 Lark。",
        turn_id="turn-voice-ui",
        stage="direct_mainline_finished",
        companion=ctx,
        extra={"status": "done", "current_task": "open_app / Lark"},
    )

    assert "jachin-ui:voice-runtime" in rendered
    assert '"status":"done"' in rendered
    assert '"from":"lock"' in rendered
    assert '"to":"Lark"' in rendered


def test_voice_runtime_ui_protocol_marks_noise_drop():
    protocol = build_voice_runtime_ui_protocol(
        {
            "stage": "noise_guard_blocked",
            "turn_id": "turn-noise",
            "raw_text": "嗯对",
            "normalized_text": "嗯对",
            "stt_confidence": 0.2,
            "adapter_steps": [],
            "false_trigger_guard": {
                "action": "drop",
                "reason_code": "background_noise_fragment",
            },
            "normalization": {},
            "planning": {},
            "closure": {},
            "extra": {},
        }
    )

    assert protocol["status"] == "drop"
    assert protocol["reason_code"] == "background_noise_fragment"
    assert protocol["stages"][0]["label"] == "噪声/主人判断"
