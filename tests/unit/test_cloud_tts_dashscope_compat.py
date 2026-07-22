from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VOICE_SERVER = ROOT / "voice_server"
if str(VOICE_SERVER) not in sys.path:
    sys.path.insert(0, str(VOICE_SERVER))

import services.cloud_tts_service as cloud_tts_service
from services.cloud_tts_service import _patch_dashscope_tts_close_callback


def test_dashscope_tts_close_callback_accepts_old_websocket_signature() -> None:
    cloud_tts_service._DASHSCOPE_TTS_CLOSE_PATCHED = False
    seen: dict[str, object] = {}

    class FakeSpeechSynthesizer:
        def on_close(self, ws, close_status_code, close_msg) -> None:  # noqa: ANN001
            seen["ws"] = ws
            seen["code"] = close_status_code
            seen["msg"] = close_msg

    _patch_dashscope_tts_close_callback(FakeSpeechSynthesizer)

    instance = FakeSpeechSynthesizer()
    instance.on_close("ws-object")

    assert seen == {"ws": "ws-object", "code": None, "msg": None}
