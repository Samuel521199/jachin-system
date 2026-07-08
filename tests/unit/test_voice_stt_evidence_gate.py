from l3_node.ws_server import _voice_evidence_gate_reply


def test_voice_evidence_gate_blocks_stream_task_result() -> None:
    reply, reason = _voice_evidence_gate_reply(
        "找到Chrome",
        {
            "voice_stt_source": "jvs_stream_ws",
            "voice_stt_finalized": False,
            "voice_dispatch_lane": "foreground",
            "voice_intent_class": "TASK_SYNC",
        },
    )

    assert reason == "non_final_voice_stt"
    assert reply is not None
    assert "临时识别结果" in reply
    assert "找到Chrome" in reply


def test_voice_evidence_gate_blocks_hotword_dominated_final() -> None:
    reply, reason = _voice_evidence_gate_reply(
        "找到Chrome",
        {
            "voice_stt_source": "jvs_http_transcribe",
            "voice_stt_finalized": True,
            "voice_stt_hotword_dominated": True,
        },
    )

    assert reason == "hotword_dominated"
    assert reply is not None
    assert "热词影响" in reply


def test_voice_evidence_gate_allows_normal_final_voice_text() -> None:
    reply, reason = _voice_evidence_gate_reply(
        "打开计算器算一下40乘90",
        {
            "voice_stt_source": "jvs_http_transcribe",
            "voice_stt_finalized": True,
            "voice_stt_hotword_dominated": False,
            "voice_dispatch_lane": "foreground",
        },
    )

    assert reply is None
    assert reason == ""
