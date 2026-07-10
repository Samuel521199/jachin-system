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


def test_stt_hotword_provider_loads_sherpa_text_hotwords(tmp_path) -> None:
    hotwords = tmp_path / "sherpa_hotwords.txt"
    hotwords.write_text("Lark :8.0\nNeil :7\n# comment\nEthan\n", encoding="utf-8")

    snapshot = SttHotwordProvider(extra_paths=[hotwords]).snapshot()

    assert snapshot.words["Lark"] >= 8
    assert snapshot.words["Neil"] >= 7
    assert snapshot.words["Ethan"] == 20
    assert str(hotwords) in snapshot.sources


def test_stt_service_detects_zipformer_model_files(tmp_path) -> None:
    for name in (
        "encoder-epoch-34-avg-19.int8.onnx",
        "decoder-epoch-34-avg-19.onnx",
        "joiner-epoch-34-avg-19.int8.onnx",
        "tokens.txt",
        "bpe.vocab",
    ):
        (tmp_path / name).write_text("x", encoding="utf-8")

    svc = SttService(tmp_path)

    assert svc.ready is True
    assert svc._files is not None
    assert svc._files.encoder.name == "encoder-epoch-34-avg-19.int8.onnx"
    assert svc._files.decoder.name == "decoder-epoch-34-avg-19.onnx"
    assert svc._files.joiner.name == "joiner-epoch-34-avg-19.int8.onnx"
    assert svc._files.tokens.name == "tokens.txt"
    assert svc._files.bpe_vocab is not None


def test_stt_service_writes_sherpa_hotword_file(tmp_path) -> None:
    svc = SttService(tmp_path)
    snapshot = type("Snapshot", (), {"words": {"Lark": 8, "Vivian": 12}})()

    path = svc._prepare_hotword_file(snapshot)

    assert path is not None
    text = path.read_text(encoding="utf-8")
    assert "Lark :8" in text
    assert "Vivian :12" in text


def test_stt_resample_to_16k_uses_quality_resampler_shape() -> None:
    audio = np.sin(np.linspace(0, np.pi * 2, 4800, dtype=np.float32))

    out = SttService._resample_to_16k(audio, 48000)

    assert out.dtype == np.float32
    assert 1550 <= len(out) <= 1650
    assert np.isfinite(out).all()
