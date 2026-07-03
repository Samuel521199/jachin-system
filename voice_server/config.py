from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class VoiceServerConfig:
    host: str
    port: int
    stt_tcp_port: int
    model_root: Path
    stt_dir: Path
    tts_dir: Path
    sv_dir: Path
    tts_voice: str
    tts_speed: float
    log_level: str


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def load_config() -> VoiceServerConfig:
    model_root = Path(
        os.getenv(
            "JACHIN_VOICE_MODEL_ROOT",
            r"D:\project\jachin-system-main\data\models\voice",
        )
    )
    stt_rel = os.getenv("JACHIN_VOICE_STT_DIR", r"stt\SenseVoiceSmall-onnx")
    tts_rel = os.getenv("JACHIN_VOICE_TTS_DIR", r"tts")
    sv_rel = os.getenv(
        "JACHIN_VOICE_SV_DIR",
        r"sv\speech_campplus_sv_zh-cn_16k-common",
    )

    return VoiceServerConfig(
        host=os.getenv("JACHIN_VOICE_SERVER_HOST", "127.0.0.1"),
        port=_env_int("JACHIN_VOICE_SERVER_PORT", 18982),
        stt_tcp_port=_env_int("JACHIN_VOICE_STT_TCP_PORT", 18983),
        model_root=model_root,
        stt_dir=model_root / stt_rel,
        tts_dir=model_root / tts_rel,
        sv_dir=model_root / sv_rel,
        tts_voice=os.getenv("JACHIN_VOICE_TTS_VOICE", "zm_053"),
        tts_speed=_env_float("JACHIN_VOICE_TTS_SPEED", 1.3),
        log_level=os.getenv("JACHIN_VOICE_LOG_LEVEL", "info"),
    )
