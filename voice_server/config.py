from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from defaults import DEFAULT_KOKORO_TTS_SPEED, DEFAULT_KOKORO_TTS_VOICE
except ModuleNotFoundError:
    from voice_server.defaults import DEFAULT_KOKORO_TTS_SPEED, DEFAULT_KOKORO_TTS_VOICE


@dataclass
class VoiceServerConfig:
    host: str
    port: int
    stt_tcp_port: int
    voice_backend: str
    stt_backend: str
    tts_backend: str
    dashscope_api_key: str
    dashscope_tts_api_key: str
    dashscope_api_base: str
    dashscope_http_api_base: str
    dashscope_ws_api_base: str
    dashscope_workspace_id: str
    stt_model: str
    stt_realtime_model: str
    stt_hotword_model: str
    stt_hotword_mode: str
    stt_file_model: str
    stt_vocabulary_id: str
    stt_vocabulary_prefix: str
    stt_auto_sync_vocabulary: bool
    stt_language: str
    tts_model: str
    tts_fast_model: str
    tts_cloud_voice: str
    tts_format: str
    tts_sample_rate: int
    model_root: Path
    stt_dir: Path
    tts_dir: Path
    sv_dir: Path
    torch_device: str
    require_gpu: bool
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


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "y", "on"}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_dotenv_file(path: Path, *, override: bool = False) -> None:
    if not path.is_file():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            value = value.strip().strip('"').strip("'")
            if override or not os.getenv(key):
                os.environ[key] = value
    except Exception:
        # Keep voice_server bootable even when a dotenv file is malformed.
        return


def _merge_dotenv_into_environ() -> None:
    root = _project_root()
    # Packaged / repo .env first, user ~/.jachin/.env last so personal profile wins.
    _load_dotenv_file(root / ".env", override=False)
    _load_dotenv_file(Path.home() / ".jachin" / ".env", override=True)


def _first_env(names: tuple[str, ...], default: str = "") -> str:
    for name in names:
        val = os.getenv(name, "").strip()
        if val:
            return val
    return default


def _active_region() -> str:
    return os.getenv("JACHIN_ACTIVE_REGION", "CN").strip().upper() or "CN"


def _dashscope_api_key() -> str:
    if _active_region() == "SEA":
        return _first_env(("DASHSCOPE_API_KEY_SEA", "DASHSCOPE_API_KEY", "QWEN_API_KEY", "QWEN_AI_API_KEY"))
    return _first_env(("DASHSCOPE_API_KEY_CN", "DASHSCOPE_API_KEY", "QWEN_API_KEY", "QWEN_AI_API_KEY"))


def _tts_region() -> str:
    return os.getenv("JACHIN_TTS_REGION", "CN").strip().upper() or "CN"


def _dashscope_tts_api_key() -> str:
    if _tts_region() == "SEA":
        return _first_env(("DASHSCOPE_API_KEY_SEA", "DASHSCOPE_API_KEY", "QWEN_API_KEY", "QWEN_AI_API_KEY"))
    return _first_env(("DASHSCOPE_API_KEY_CN", "DASHSCOPE_API_KEY", "QWEN_API_KEY", "QWEN_AI_API_KEY"))


def _dashscope_api_base() -> str:
    if _active_region() == "SEA":
        return _first_env(
            ("JACHIN_ASR_API_BASE", "DASHSCOPE_API_BASE_SEA", "DASHSCOPE_API_BASE"),
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        )
    return _first_env(
        ("JACHIN_ASR_API_BASE", "DASHSCOPE_API_BASE_CN", "DASHSCOPE_API_BASE"),
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )


def _dashscope_tts_compatible_base() -> str:
    explicit = _first_env(("JACHIN_TTS_API_BASE", "JACHIN_TTS_COMPATIBLE_API_BASE"))
    if explicit:
        return explicit.rstrip("/")
    if _tts_region() == "SEA":
        return _first_env(
            ("DASHSCOPE_API_BASE_SEA", "DASHSCOPE_API_BASE"),
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        )
    return _first_env(
        ("DASHSCOPE_API_BASE_CN",),
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    )


def _dashscope_http_api_base(compatible_base: str, *, tts: bool = False) -> str:
    explicit = _first_env(("JACHIN_TTS_HTTP_API_BASE", "DASHSCOPE_HTTP_API_BASE"))
    if explicit:
        return explicit.rstrip("/")
    if tts:
        compatible_base = _dashscope_tts_compatible_base()
    base = compatible_base.rstrip("/")
    if base.endswith("/compatible-mode/v1"):
        return base[: -len("/compatible-mode/v1")] + "/api/v1"
    return base.rstrip("/") + "/api/v1"


def _dashscope_ws_api_base(compatible_base: str) -> str:
    explicit = _first_env(("JACHIN_ASR_WS_API_BASE", "DASHSCOPE_WS_API_BASE", "DASHSCOPE_WEBSOCKET_API_BASE"))
    if explicit:
        return explicit.rstrip("/")
    base = compatible_base.rstrip("/")
    if base.endswith("/compatible-mode/v1"):
        host = base[: -len("/compatible-mode/v1")]
        if host.startswith("https://"):
            host = "wss://" + host[len("https://") :]
        elif host.startswith("http://"):
            host = "ws://" + host[len("http://") :]
        return host + "/api-ws/v1/inference"
    if base.startswith("https://"):
        base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://") :]
    return base.rstrip("/") + "/api-ws/v1/inference"


def _has_camplus_sv_model(model_root: Path, sv_rel: str) -> bool:
    sv_dir = model_root / sv_rel
    return (
        (sv_dir / "configuration.json").is_file()
        and (sv_dir / "campplus_cn_common.bin").is_file()
        and (sv_dir / "campplus_cn_common.bin").stat().st_size > 1024 * 1024
    )


def _resolve_voice_model_root(sv_rel: str) -> Path:
    explicit = os.getenv("JACHIN_VOICE_MODEL_ROOT", "").strip()
    if explicit:
        return Path(explicit)

    root = _project_root()
    candidates = [
        root / "data" / "models" / "voice",
        root.parent / "data" / "models" / "voice",
        Path.home() / ".jachin" / "models" / "voice",
        Path.home() / "Desktop" / "voice",
    ]
    for candidate in candidates:
        if _has_camplus_sv_model(candidate, sv_rel):
            return candidate
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return root / "data" / "models" / "voice"


def load_config() -> VoiceServerConfig:
    _merge_dotenv_into_environ()
    stt_rel = os.getenv("JACHIN_VOICE_STT_DIR", r"stt\sherpa-onnx-zipformer-zh-en-2023-11-22")
    tts_rel = os.getenv("JACHIN_VOICE_TTS_DIR", r"tts")
    sv_rel = os.getenv(
        "JACHIN_VOICE_SV_DIR",
        r"sv\speech_campplus_sv_zh-cn_16k-common",
    )
    model_root = _resolve_voice_model_root(sv_rel)
    voice_backend = os.getenv("JACHIN_VOICE_BACKEND", "cloud").strip().lower() or "cloud"
    stt_backend = os.getenv("JACHIN_STT_BACKEND", voice_backend).strip().lower() or voice_backend
    tts_backend = os.getenv("JACHIN_TTS_BACKEND", voice_backend).strip().lower() or voice_backend
    dashscope_api_base = _dashscope_api_base()

    return VoiceServerConfig(
        host=os.getenv("JACHIN_VOICE_SERVER_HOST", "127.0.0.1"),
        port=_env_int("JACHIN_VOICE_SERVER_PORT", 18982),
        stt_tcp_port=_env_int("JACHIN_VOICE_STT_TCP_PORT", 18983),
        voice_backend=voice_backend,
        stt_backend=stt_backend,
        tts_backend=tts_backend,
        dashscope_api_key=_dashscope_api_key(),
        dashscope_tts_api_key=_dashscope_tts_api_key(),
        dashscope_api_base=dashscope_api_base,
        dashscope_http_api_base=_dashscope_http_api_base(dashscope_api_base, tts=True),
        dashscope_ws_api_base=_dashscope_ws_api_base(dashscope_api_base),
        dashscope_workspace_id=_first_env(("JACHIN_DASHSCOPE_WORKSPACE_ID", "DASHSCOPE_WORKSPACE_ID", "DASHSCOPE_WORKSPACE")),
        stt_model=os.getenv("JACHIN_STT_MODEL", "fun-asr-realtime").strip() or "fun-asr-realtime",
        stt_realtime_model=os.getenv("JACHIN_STT_REALTIME_MODEL", "fun-asr-realtime").strip() or "fun-asr-realtime",
        stt_hotword_model=os.getenv("JACHIN_STT_HOTWORD_MODEL", "fun-asr-realtime").strip() or "fun-asr-realtime",
        stt_hotword_mode=os.getenv("JACHIN_STT_HOTWORD_MODE", "adaptive").strip().lower() or "adaptive",
        stt_file_model=os.getenv("JACHIN_STT_FILE_MODEL", "fun-asr").strip() or "fun-asr",
        stt_vocabulary_id=_first_env(("JACHIN_STT_VOCABULARY_ID", "JACHIN_ASR_VOCABULARY_ID", "DASHSCOPE_ASR_VOCABULARY_ID")),
        stt_vocabulary_prefix=os.getenv("JACHIN_STT_VOCABULARY_PREFIX", "jachin").strip() or "jachin",
        stt_auto_sync_vocabulary=_env_bool("JACHIN_STT_AUTO_SYNC_VOCABULARY", True),
        stt_language=os.getenv("JACHIN_STT_LANGUAGE", "").strip(),
        tts_model=os.getenv("JACHIN_TTS_MODEL", "cosyvoice-v3-plus").strip() or "cosyvoice-v3-plus",
        tts_fast_model=os.getenv("JACHIN_TTS_FAST_MODEL", "cosyvoice-v3-flash").strip() or "cosyvoice-v3-flash",
        tts_cloud_voice=os.getenv("JACHIN_CLOUD_TTS_VOICE", "longanhuan").strip() or "longanhuan",
        tts_format=os.getenv("JACHIN_TTS_FORMAT", "pcm").strip().lower() or "pcm",
        tts_sample_rate=_env_int("JACHIN_TTS_SAMPLE_RATE", 24000),
        model_root=model_root,
        stt_dir=model_root / stt_rel,
        tts_dir=model_root / tts_rel,
        sv_dir=model_root / sv_rel,
        torch_device=os.getenv("JACHIN_VOICE_TORCH_DEVICE", "auto").strip().lower() or "auto",
        require_gpu=_env_bool("JACHIN_VOICE_REQUIRE_GPU", False),
        tts_voice=os.getenv("JACHIN_VOICE_TTS_VOICE", DEFAULT_KOKORO_TTS_VOICE),
        tts_speed=_env_float("JACHIN_VOICE_TTS_SPEED", DEFAULT_KOKORO_TTS_SPEED),
        log_level=os.getenv("JACHIN_VOICE_LOG_LEVEL", "info"),
    )
