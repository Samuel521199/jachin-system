from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
VOICE_SERVER = ROOT / "voice_server"
if str(VOICE_SERVER) not in sys.path:
    sys.path.insert(0, str(VOICE_SERVER))

from services.stt_hotwords import SttHotwordProvider
from services.stt_service import SttService


def test_stt_hotword_provider_merges_router_json_and_env(tmp_path, monkeypatch) -> None:
    lexicon = tmp_path / "domain_lexicon.json"
    lexicon.write_text(
        json.dumps(
            {
                "apps": {"Notion": ["notion", "肉身"]},
                "contacts": {"Neil": {"aliases": ["neil", "你哦"], "weight": 25}},
                "hotwords": [{"word": "Jachin", "weight": 30}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("JACHIN_STT_HOTWORDS", "Linear:17; Vivian:31")

    snapshot = SttHotwordProvider(extra_paths=[lexicon]).snapshot()

    assert snapshot.words["Lark"] >= 20
    assert snapshot.words["Notion"] == 20
    assert snapshot.words["肉身"] == 10
    assert snapshot.words["Neil"] == 25
    assert snapshot.words["你哦"] == 15
    assert snapshot.words["Jachin"] == 30
    assert snapshot.words["Linear"] == 17
    assert snapshot.words["Vivian"] == 31
    assert "apps" not in snapshot.words
    assert str(lexicon) in snapshot.sources
    assert "env:JACHIN_STT_HOTWORDS" in snapshot.sources


class NoHotwordEngine:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, wav_content, **kwargs):
        self.calls.append(kwargs)
        return ["hello"]


class HotwordEngine:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, wav_content, hotword=None, **kwargs):
        self.calls.append({**kwargs, "hotword": hotword})
        return ["hello"]


class HotwordsOnlyEngine:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, wav_content, fs=None, language=None, use_itn=None, hotwords=None):
        self.calls.append({"fs": fs, "language": language, "use_itn": use_itn, "hotwords": hotwords})
        return ["hello"]


def test_stt_service_marks_current_engine_shape_as_hotword_unsupported(tmp_path) -> None:
    svc = SttService(tmp_path)
    engine = NoHotwordEngine()

    out, status = svc._call_engine(engine, np.zeros(160, dtype=np.float32), hotwords={"Lark": 20})

    assert out == ["hello"]
    assert status == "unsupported"
    assert "hotword" not in engine.calls[0]
    assert "hotwords" not in engine.calls[0]


def test_stt_service_passes_hotword_when_engine_supports_it(tmp_path) -> None:
    svc = SttService(tmp_path)
    engine = HotwordEngine()

    _, status = svc._call_engine(engine, np.zeros(160, dtype=np.float32), hotwords={"Lark": 20})

    assert status == "applied"
    assert engine.calls[0]["hotword"] == {"Lark": 20}


def test_stt_service_can_fall_back_to_hotwords_keyword(tmp_path) -> None:
    svc = SttService(tmp_path)
    engine = HotwordsOnlyEngine()

    _, status = svc._call_engine(engine, np.zeros(160, dtype=np.float32), hotwords={"Lark": 20})

    assert status == "applied"
    assert engine.calls[0]["hotwords"] == {"Lark": 20}


def test_stt_resample_to_16k_uses_quality_resampler_shape() -> None:
    audio = np.sin(np.linspace(0, np.pi * 2, 4800, dtype=np.float32))

    out = SttService._resample_to_16k(audio, 48000)

    assert out.dtype == np.float32
    assert 1550 <= len(out) <= 1650
    assert np.isfinite(out).all()
