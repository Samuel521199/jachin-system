from __future__ import annotations

import io
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VOICE_SERVER = ROOT / "voice_server"
if str(VOICE_SERVER) not in sys.path:
    sys.path.insert(0, str(VOICE_SERVER))

from services.cloud_stt_service import CloudSttService
from services.stt_hotwords import HotwordSnapshot


def _wav_bytes() -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 1600)
    return out.getvalue()


class _FakeHotwords:
    def snapshot(self) -> HotwordSnapshot:
        return HotwordSnapshot(
            words={"Lark": 40, "Vivian": 35, "Chrome": 20},
            sources=["unit:lexicon"],
        )


def test_fun_asr_passes_native_vocabulary_id_and_context_hotwords(monkeypatch) -> None:
    seen: dict = {}

    class FakeResult:
        status_code = 200
        output = {"sentence": [{"text": "鎵撳紑 Lark"}]}

        def get_sentence(self):
            return self.output["sentence"]

    class FakeRecognition:
        def __init__(self, **kwargs):
            seen["init"] = kwargs

        def call(self, file: str, phrase_id: str | None = None, **kwargs):
            seen["call"] = {"file": file, "phrase_id": phrase_id, "kwargs": kwargs}
            assert Path(file).is_file()
            return FakeResult()

    class FakeCallback:
        pass

    svc = CloudSttService(
        api_key="test-key",
        api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        ws_api_base="wss://dashscope.aliyuncs.com/api-ws/v1/inference",
        model="fun-asr-realtime",
        vocabulary_id="vocab-123",
        auto_sync_vocabulary=False,
        language="zh",
    )
    svc._hotwords = _FakeHotwords()
    monkeypatch.setattr(svc, "_configure_dashscope_sdk", lambda: None)
    monkeypatch.setattr(svc, "_recognition_class", lambda: FakeRecognition)
    monkeypatch.setattr(svc, "_recognition_callback_class", lambda: FakeCallback)

    result = svc.transcribe(_wav_bytes())

    assert result.text == "鎵撳紑 Lark"
    assert result.raw_text == "鎵撳紑 Lark"
    assert result.backend == "dashscope:fun-asr-realtime"
    assert result.hotword_count == 3
    assert result.hotword_status == "native_vocabulary_id+context_terms"
    assert "dashscope:vocabulary_id" in result.hotword_sources
    assert "dashscope:raw_input.context" in result.hotword_sources

    assert seen["init"]["model"] == "fun-asr-realtime"
    assert seen["init"]["format"] == "wav"
    assert seen["init"]["sample_rate"] == 16000
    assert seen["init"]["vocabulary_id"] == "vocab-123"
    assert seen["init"]["language_hints"] == ["zh"]
    assert seen["call"]["phrase_id"] == "vocab-123"
    assert seen["call"]["kwargs"]["vocabulary_id"] == "vocab-123"
    context_text = seen["call"]["kwargs"]["raw_input"]["context"][0]["content"][0]["text"]
    assert "Lark" in context_text
    assert "Vivian" in context_text


def test_fun_asr_auto_syncs_existing_hotwords_to_dashscope_vocabulary(monkeypatch) -> None:
    seen: dict = {}

    class FakeResult:
        status_code = 200

        def get_sentence(self):
            return {"text": "鎵惧埌 Vivian"}

    class FakeRecognition:
        def __init__(self, **kwargs):
            seen["recognition"] = kwargs

        def call(self, file: str, phrase_id: str | None = None, **kwargs):
            seen["call"] = {"phrase_id": phrase_id, "kwargs": kwargs}
            return FakeResult()

    class FakeVocabularyService:
        def __init__(self, **kwargs):
            seen["vocab_service"] = kwargs

        def list_vocabularies(self, **kwargs):
            seen["list"] = kwargs
            return []

        def create_vocabulary(self, **kwargs):
            seen["create"] = kwargs
            return "created-vocab"

    class FakeCallback:
        pass

    svc = CloudSttService(
        api_key="test-key",
        api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="fun-asr-realtime",
        vocabulary_prefix="Jachin!",
        auto_sync_vocabulary=True,
    )
    svc._hotwords = _FakeHotwords()
    monkeypatch.setattr(svc, "_configure_dashscope_sdk", lambda: None)
    monkeypatch.setattr(svc, "_recognition_class", lambda: FakeRecognition)
    monkeypatch.setattr(svc, "_recognition_callback_class", lambda: FakeCallback)
    monkeypatch.setattr(svc, "_vocabulary_service_class", lambda: FakeVocabularyService)

    result = svc.transcribe(_wav_bytes())

    assert result.text == "鎵惧埌 Vivian"
    assert result.hotword_status == "native_vocabulary_id+context_terms"
    assert seen["list"]["prefix"] == "jachin"
    assert seen["create"]["target_model"] == "fun-asr-realtime"
    assert {"text": "Lark", "weight": 3} in seen["create"]["vocabulary"]
    assert seen["recognition"]["vocabulary_id"] == "created-vocab"
    assert seen["call"]["phrase_id"] == "created-vocab"


def test_fun_asr_does_not_apply_local_domain_term_rewrites(monkeypatch) -> None:
    class FakeResult:
        status_code = 200

        def get_sentence(self):
            return {"text": "open Lock"}

    class FakeRecognition:
        def __init__(self, **kwargs):
            pass

        def call(self, file: str, phrase_id: str | None = None, **kwargs):
            return FakeResult()

    class FakeCallback:
        pass

    svc = CloudSttService(
        api_key="test-key",
        api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        model="fun-asr-realtime",
        auto_sync_vocabulary=False,
    )
    svc._hotwords = _FakeHotwords()
    monkeypatch.setenv("JACHIN_STT_DOMAIN_TERMS", "Lock=Lark")
    monkeypatch.setattr(svc, "_configure_dashscope_sdk", lambda: None)
    monkeypatch.setattr(svc, "_recognition_class", lambda: FakeRecognition)
    monkeypatch.setattr(svc, "_recognition_callback_class", lambda: FakeCallback)

    result = svc.transcribe(_wav_bytes())

    assert result.text == "open Lock"
    assert result.raw_text == "open Lock"

def test_fun_asr_extracts_sentence_list_and_dict() -> None:
    class ListResult:
        def get_sentence(self):
            return [{"text": "鎵撳紑"}, {"text": "Lark"}]

    class DictResult:
        def get_sentence(self):
            return {"text": "鎵惧埌 Vivian"}

    assert CloudSttService._extract_fun_asr_text(ListResult()) == "鎵撳紑Lark"
    assert CloudSttService._extract_fun_asr_text(DictResult()) == "鎵惧埌 Vivian"
