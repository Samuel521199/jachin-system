import asyncio
import sys
import time
from pathlib import Path


def test_stt_http_transcribe_waits_grace_for_local_fallback(monkeypatch):
    voice_server_dir = Path(__file__).resolve().parents[2] / "voice_server"
    monkeypatch.syspath_prepend(str(voice_server_dir))
    sys.modules.pop("config", None)
    import voice_server.main as main
    from voice_server.services.stt_service import SttResult

    class SlowCloudStt:
        ready = True
        model_path = "cloud"

        def transcribe(self, _audio_bytes: bytes):
            time.sleep(1.0)
            return SttResult(text="cloud late", confidence=0.9, duration_ms=10, language="zh", backend="cloud")

    class SlowButUsefulFallback:
        ready = True
        model_name = "fake-local"
        model_path = "fake-local-path"

        def transcribe(self, _audio_bytes: bytes):
            time.sleep(0.15)
            return SttResult(text="本地兜底结果", confidence=0.8, duration_ms=10, language="zh", backend="local")

    monkeypatch.setattr(main, "stt_service", SlowCloudStt())
    monkeypatch.setattr(main, "local_stt_fallback_service", SlowButUsefulFallback())
    monkeypatch.setattr(main, "_stt_cloud_soft_timeout_seconds", lambda _hard: 0.05)
    monkeypatch.setattr(main, "_stt_fallback_grace_seconds", lambda: 0.3)

    result = asyncio.run(
        main._transcribe_stt_with_local_fallback(
            b"fake-wav-bytes",
            stage="unit_http_transcribe",
            timeout_sec=0.1,
        )
    )

    assert result.text == "本地兜底结果"
    assert "fallback_from_cloud" in result.backend
    assert result.understanding["stt_fallback"]["reason"] == "cloud_soft_timeout"
    stages = [event["stage"] for event in result.understanding["stt_orchestration"]]
    assert "fallback_grace_wait_start" in stages
    assert "fallback_result_after_grace" in stages
