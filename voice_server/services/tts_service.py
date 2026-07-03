from __future__ import annotations

import importlib.util
import io
import json
import logging
import os
import sys
import hashlib
import tempfile
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger("jachin.voice_server.tts")

DEFAULT_SAMPLE_RATE = 48000
DEFAULT_RUNTIME_DIR = Path(os.getenv("JACHIN_MOSS_TTS_RUNTIME_DIR", r"D:\model\MOSS-TTS-Nano"))
DEFAULT_RUNTIME_FILE = "onnx_tts_runtime.py"
DEFAULT_CPU_THREADS = 4
DEFAULT_VOICE_CLONE_MAX_TEXT_TOKENS = 75
DEFAULT_MAX_NEW_FRAMES = 375
DEFAULT_TTS_MAX_RETRIES = 2
DEFAULT_MAX_ALLOWED_AUDIO_MS = 12000
KOKORO_DIRNAME = "Kokoro-82M-v1.1-zh-ONNX"
KOKORO_SAMPLE_RATE = 24000


def _env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid integer env %s=%r, using %s", name, raw, default)
        return default


def _env_optional_int(name: str) -> int | None:
    raw = str(os.getenv(name, "")).strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid integer env %s=%r, ignoring", name, raw)
        return None


@dataclass
class TtsResult:
    wav_bytes: bytes
    duration_ms: int
    sample_rate: int
    synth_ms: int = 0
    attempts: int = 1
    max_new_frames: int = 0
    quality_status: str = "ok"


class TtsCancelledError(RuntimeError):
    pass


class TtsService:
    """Local ONNX TTS service. Kokoro is the current preferred bundled model."""

    def __init__(
        self,
        tts_dir: Path,
        default_voice: str = "zm_053",
        default_speed: float = 1.0,
    ) -> None:
        self.tts_dir = tts_dir
        self.default_voice = default_voice.strip() or "zm_053"
        self.default_speed = float(np.clip(default_speed, 0.8, 1.5))
        raw_sample_mode = str(os.getenv("JACHIN_VOICE_TTS_SAMPLE_MODE", "fixed")).strip().lower() or "fixed"
        if raw_sample_mode not in {"greedy", "fixed", "full"}:
            logger.warning("Invalid MOSS sample mode %r, using fixed", raw_sample_mode)
            raw_sample_mode = "fixed"
        self.sample_mode = raw_sample_mode
        self.do_sample = self.sample_mode != "greedy"
        self.base_seed = _env_optional_int("JACHIN_VOICE_TTS_SEED")
        self.max_retry_attempts = max(0, _env_int("JACHIN_VOICE_TTS_MAX_RETRIES", DEFAULT_TTS_MAX_RETRIES))
        self.max_allowed_audio_ms = max(1200, _env_int("JACHIN_VOICE_TTS_MAX_ALLOWED_AUDIO_MS", DEFAULT_MAX_ALLOWED_AUDIO_MS))
        configured_max_new_frames = _env_optional_int("JACHIN_VOICE_TTS_MAX_NEW_FRAMES")
        self.max_new_frames = configured_max_new_frames if configured_max_new_frames and configured_max_new_frames > 0 else None
        self.voice_clone_max_text_tokens = max(
            8,
            _env_int("JACHIN_VOICE_TTS_VOICE_CLONE_MAX_TEXT_TOKENS", DEFAULT_VOICE_CLONE_MAX_TEXT_TOKENS),
        )
        self.runtime_dir = Path(os.getenv("JACHIN_MOSS_TTS_RUNTIME_DIR", str(DEFAULT_RUNTIME_DIR)))
        self.runtime_file = self.runtime_dir / DEFAULT_RUNTIME_FILE
        self.tts_model_dir = self.tts_dir / "MOSS-TTS-Nano-100M-ONNX"
        self.codec_model_dir = self.tts_dir / "MOSS-Audio-Tokenizer-Nano-ONNX"
        self.model_path = self.tts_model_dir / "browser_poc_manifest.json"
        self.kokoro_dir = self.tts_dir / KOKORO_DIRNAME
        self.kokoro_model_path = self.kokoro_dir / "onnx" / "model.onnx"
        self.kokoro_tokenizer_path = self.kokoro_dir / "tokenizer.json"
        self.kokoro_voices_dir = self.kokoro_dir / "voices"
        self._runtime: Any = None
        self._backend: str | None = None
        self._kokoro_vocab: dict[str, int] | None = None
        self._load_error: str | None = None
        self._runtime_lock = threading.Lock()
        self._synthesize_lock = threading.Lock()
        self._cancel_lock = threading.Lock()
        self._global_cancel_seq = 0
        self._session_cancel_seq: dict[str, int] = {}
        self._voices_cache: list[str] | None = None

    @property
    def ready(self) -> bool:
        if self._has_kokoro_files():
            return self._kokoro_dependencies_ready()
        return (
            self.tts_model_dir.is_dir()
            and self.codec_model_dir.is_dir()
            and self.model_path.is_file()
            and self.runtime_file.is_file()
        )

    @property
    def backend(self) -> str:
        if self._backend:
            return self._backend
        if self._has_kokoro_files():
            return "kokoro"
        return "moss"

    def _has_kokoro_files(self) -> bool:
        return (
            self.kokoro_model_path.is_file()
            and self.kokoro_tokenizer_path.is_file()
            and self.kokoro_voices_dir.is_dir()
            and any(self.kokoro_voices_dir.glob("*.bin"))
        )

    def _kokoro_dependencies_ready(self) -> bool:
        return importlib.util.find_spec("onnxruntime") is not None and importlib.util.find_spec("pypinyin") is not None

    def diagnostics(self) -> dict[str, Any]:
        missing: list[str] = []
        if self._has_kokoro_files() or self.kokoro_dir.exists():
            checks = {
                "kokoro_model_file": self.kokoro_model_path,
                "kokoro_tokenizer_file": self.kokoro_tokenizer_path,
                "kokoro_voices_dir": self.kokoro_voices_dir,
            }
            for dep in ("onnxruntime", "pypinyin"):
                if importlib.util.find_spec(dep) is None:
                    missing.append(f"python_dependency:{dep}")
        else:
            checks = {
                "moss_tts_model_dir": self.tts_model_dir,
                "moss_codec_model_dir": self.codec_model_dir,
                "moss_manifest_file": self.model_path,
                "moss_runtime_file": self.runtime_file,
            }
        for name, path in checks.items():
            if not path.exists():
                missing.append(name)
        return {
            "ready": self.ready,
            "backend": self.backend,
            "tts_dir": str(self.tts_dir),
            "kokoro_dir": str(self.kokoro_dir),
            "kokoro_model_file": str(self.kokoro_model_path),
            "kokoro_tokenizer_file": str(self.kokoro_tokenizer_path),
            "kokoro_voices_dir": str(self.kokoro_voices_dir),
            "tts_model_dir": str(self.tts_model_dir),
            "codec_model_dir": str(self.codec_model_dir),
            "manifest_file": str(self.model_path),
            "runtime_file": str(self.runtime_file),
            "missing": missing,
            "load_error": self._load_error,
        }

    def _import_runtime_module(self):
        if not self.runtime_file.is_file():
            raise FileNotFoundError(f"MOSS runtime file missing: {self.runtime_file}")
        runtime_parent = str(self.runtime_dir.resolve())
        if runtime_parent not in sys.path:
            sys.path.insert(0, runtime_parent)
        spec = importlib.util.spec_from_file_location("jachin_moss_onnx_runtime", str(self.runtime_file))
        if spec is None or spec.loader is None:
            raise RuntimeError(f"failed to load runtime spec from {self.runtime_file}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _load_engine(self) -> bool:
        if self._runtime is not None:
            return True
        if self._load_error is not None:
            return False
        if not self.ready:
            self._load_error = f"{self.backend} model/runtime files missing under: {self.tts_dir}"
            return False
        if self._has_kokoro_files():
            return self._load_kokoro_engine()
        return self._load_moss_engine()

    def _load_kokoro_engine(self) -> bool:
        try:
            with self._runtime_lock:
                if self._runtime is not None:
                    return True
                import onnxruntime as ort

                logger.info("Loading Kokoro ONNX runtime from %s", self.kokoro_model_path)
                self._runtime = ort.InferenceSession(
                    str(self.kokoro_model_path),
                    providers=["CPUExecutionProvider"],
                )
                self._backend = "kokoro"
            return True
        except Exception as e:
            self._load_error = str(e)
            logger.exception("Kokoro ONNX load failed: %s", e)
            return False

    def _load_moss_engine(self) -> bool:
        try:
            with self._runtime_lock:
                if self._runtime is not None:
                    return True
                module = self._import_runtime_module()
                OnnxTtsRuntime = getattr(module, "OnnxTtsRuntime")
                execution_provider = str(os.getenv("JACHIN_VOICE_TTS_EP", "cpu")).strip().lower() or "cpu"
                thread_count = int(os.getenv("JACHIN_VOICE_TTS_THREADS", str(DEFAULT_CPU_THREADS)) or DEFAULT_CPU_THREADS)
                logger.info(
                    "Loading MOSS ONNX runtime from %s (model_dir=%s, ep=%s, threads=%s)",
                    self.runtime_file,
                    self.tts_dir,
                    execution_provider,
                    thread_count,
                )
                self._runtime = OnnxTtsRuntime(
                    model_dir=str(self.tts_dir),
                    thread_count=max(1, thread_count),
                    execution_provider=execution_provider if execution_provider in {"cpu", "cuda"} else "cpu",
                    sample_mode=self.sample_mode,
                    do_sample=self.do_sample,
                )
                self._backend = "moss"
            return True
        except Exception as e:
            self._load_error = str(e)
            logger.exception("MOSS ONNX load failed: %s", e)
            return False

    def list_voices(self) -> list[str]:
        if self._voices_cache is not None:
            return list(self._voices_cache)
        if self._has_kokoro_files():
            voices = sorted(p.stem for p in self.kokoro_voices_dir.glob("*.bin"))
            self._voices_cache = voices or [self.default_voice]
            return list(self._voices_cache)
        if not self._load_engine() or self._runtime is None:
            return [self.default_voice]
        try:
            rows = self._runtime.list_builtin_voices()
            out = [str(item.get("voice", "")).strip() for item in rows if str(item.get("voice", "")).strip()]
            voices = sorted(set(out)) if out else [self.default_voice]
            self._voices_cache = voices
            return list(voices)
        except Exception:
            return [self.default_voice]

    def has_voice(self, voice: str | None) -> bool:
        voice_id = (voice or "").strip()
        if not voice_id:
            return False
        voices = self.list_voices()
        return voice_id in voices

    def cancel_session(self, session_id: str | None) -> bool:
        sid = (session_id or "").strip()
        if not sid:
            return False
        with self._cancel_lock:
            self._session_cancel_seq[sid] = self._session_cancel_seq.get(sid, 0) + 1
        return True

    def cancel_all(self) -> None:
        with self._cancel_lock:
            self._global_cancel_seq += 1

    def _snapshot_cancel_state(self, session_id: str | None) -> tuple[int, int]:
        sid = (session_id or "").strip()
        with self._cancel_lock:
            return (
                self._global_cancel_seq,
                self._session_cancel_seq.get(sid, 0) if sid else 0,
            )

    def _is_cancelled(self, session_id: str | None, snapshot: tuple[int, int]) -> bool:
        sid = (session_id or "").strip()
        with self._cancel_lock:
            if self._global_cancel_seq != snapshot[0]:
                return True
            if sid and self._session_cancel_seq.get(sid, 0) != snapshot[1]:
                return True
        return False

    def _ensure_not_cancelled(self, session_id: str | None, snapshot: tuple[int, int], stage: str) -> None:
        if self._is_cancelled(session_id, snapshot):
            raise TtsCancelledError(f"tts_cancelled_at:{stage}")

    def synthesize(self, text: str, voice: str | None = None, session_id: str | None = None) -> TtsResult:
        normalized = (text or "").strip()
        if not normalized:
            normalized = " "
        cancel_snapshot = self._snapshot_cancel_state(session_id)

        if not self.ready:
            return self._fallback_sine(normalized, freq_hz=330.0, sample_rate=DEFAULT_SAMPLE_RATE)

        if not self._load_engine():
            return self._error_wav(f"[TTS ERROR] {self.backend} model not loaded: {self._load_error or 'unknown'}")

        voice_id = (voice or self.default_voice).strip() or self.default_voice
        if not self.has_voice(voice_id):
            fallback_voice = self.default_voice if self.has_voice(self.default_voice) else (self.list_voices()[0] if self.list_voices() else self.default_voice)
            logger.warning(
                "%s voice '%s' missing, fallback to default '%s'",
                self.backend,
                voice_id,
                fallback_voice,
            )
            voice_id = fallback_voice

        if self.backend == "kokoro":
            return self._synthesize_kokoro(
                text=normalized,
                voice_id=voice_id,
                session_id=session_id,
                cancel_snapshot=cancel_snapshot,
            )

        base_max_new_frames = self._resolve_max_new_frames(normalized)
        max_attempts = max(1, self.max_retry_attempts + 1)
        last_result: TtsResult | None = None
        last_reason = "unknown"
        synth_started_at = time.perf_counter()

        for attempt_index in range(max_attempts):
            self._ensure_not_cancelled(session_id, cancel_snapshot, f"before_synthesize_attempt_{attempt_index + 1}")
            attempt_max_new_frames = self._attempt_max_new_frames(base_max_new_frames, attempt_index)
            try:
                attempt_result = self._synthesize_once(
                    text=normalized,
                    voice_id=voice_id,
                    session_id=session_id,
                    attempt_index=attempt_index,
                    max_new_frames=attempt_max_new_frames,
                )
                self._ensure_not_cancelled(session_id, cancel_snapshot, f"after_synthesize_attempt_{attempt_index + 1}")
            except TtsCancelledError:
                logger.info("MOSS synthesize cancelled (session_id=%s)", (session_id or "").strip() or "none")
                raise
            except Exception as e:
                logger.exception("MOSS synthesize attempt failed (attempt=%s/%s)", attempt_index + 1, max_attempts)
                last_reason = str(e)
                continue

            attempt_result.synth_ms = int((time.perf_counter() - synth_started_at) * 1000)
            attempt_result.attempts = attempt_index + 1
            attempt_result.max_new_frames = attempt_max_new_frames
            last_result = attempt_result
            is_bad, reason = self._is_abnormal_tts_result(normalized, attempt_result)
            attempt_result.quality_status = reason
            if not is_bad:
                if attempt_index > 0:
                    logger.info(
                        "MOSS synthesize recovered after retry (attempt=%s, audio_ms=%s)",
                        attempt_index + 1,
                        attempt_result.duration_ms,
                    )
                return attempt_result

            last_reason = reason
            if attempt_index < max_attempts - 1:
                logger.warning(
                    "MOSS synthesize abnormal output, retrying (attempt=%s/%s, reason=%s, audio_ms=%s, max_new_frames=%s)",
                    attempt_index + 1,
                    max_attempts,
                    reason,
                    attempt_result.duration_ms,
                    attempt_max_new_frames,
                )
            else:
                logger.warning(
                    "MOSS synthesize abnormal output after retries; returning last result (reason=%s, audio_ms=%s)",
                    reason,
                    attempt_result.duration_ms,
                )

        if last_result is not None:
            last_result.synth_ms = int((time.perf_counter() - synth_started_at) * 1000)
            last_result.quality_status = last_reason
            return self._trim_result_to_allowed_duration(normalized, last_result)
        return self._error_wav(f"[TTS ERROR] MOSS synthesize failed: {last_reason}")

    def _synthesize_kokoro(
        self,
        *,
        text: str,
        voice_id: str,
        session_id: str | None,
        cancel_snapshot: tuple[int, int],
    ) -> TtsResult:
        started_at = time.perf_counter()
        self._ensure_not_cancelled(session_id, cancel_snapshot, "before_kokoro_tokenize")
        tokens = self._kokoro_text_to_tokens(text)
        if not tokens:
            return self._fallback_sine(text, freq_hz=420.0, sample_rate=KOKORO_SAMPLE_RATE)
        if len(tokens) > 510:
            tokens = tokens[:510]
        input_ids = np.array([[0, *tokens, 0]], dtype=np.int64)
        style_vec = self._kokoro_style_vector(voice_id, token_len=len(tokens))
        speed = np.array([self.default_speed], dtype=np.float32)
        self._ensure_not_cancelled(session_id, cancel_snapshot, "before_kokoro_infer")
        with self._synthesize_lock:
            outputs = self._runtime.run(
                None,
                {
                    "input_ids": input_ids,
                    "style": style_vec,
                    "speed": speed,
                },
            )
        self._ensure_not_cancelled(session_id, cancel_snapshot, "after_kokoro_infer")
        samples = np.asarray(outputs[0], dtype=np.float32).reshape(-1)
        wav_bytes = self._pcm_f32_to_wav(samples, KOKORO_SAMPLE_RATE)
        duration_ms = self._wav_duration_ms(wav_bytes, sample_rate=KOKORO_SAMPLE_RATE)
        return TtsResult(
            wav_bytes=wav_bytes,
            duration_ms=duration_ms,
            sample_rate=KOKORO_SAMPLE_RATE,
            synth_ms=int((time.perf_counter() - started_at) * 1000),
            quality_status="ok",
        )

    def _kokoro_vocab_map(self) -> dict[str, int]:
        if self._kokoro_vocab is not None:
            return self._kokoro_vocab
        raw = json.loads(self.kokoro_tokenizer_path.read_text(encoding="utf-8"))
        vocab = raw.get("model", {}).get("vocab", {})
        self._kokoro_vocab = {str(k): int(v) for k, v in vocab.items()}
        return self._kokoro_vocab

    def _kokoro_text_to_tokens(self, text: str) -> list[int]:
        vocab = self._kokoro_vocab_map()
        sequence = self._kokoro_phonemize_text(text)
        tokens: list[int] = []
        for ch in sequence:
            token = vocab.get(ch)
            if token is not None:
                tokens.append(token)
        return tokens

    def _kokoro_phonemize_text(self, text: str) -> str:
        try:
            from pypinyin import Style, pinyin
        except Exception:
            return text

        tone_marks = {
            "ˉ": "1",
            "ˊ": "2",
            "ˇ": "3",
            "ˋ": "4",
            "˙": "5",
        }
        punctuation = {
            "，": ",",
            "。": ".",
            "！": "!",
            "？": "?",
            "；": ";",
            "：": ":",
            "、": ",",
            "（": "(",
            "）": ")",
            "“": '"',
            "”": '"',
        }
        parts: list[str] = []
        for ch in text:
            mapped = punctuation.get(ch)
            if mapped is not None:
                parts.append(mapped)
                continue
            if "\u4e00" <= ch <= "\u9fff":
                bopomofo = pinyin(ch, style=Style.BOPOMOFO, neutral_tone_with_five=True, errors="default")
                raw = bopomofo[0][0] if bopomofo and bopomofo[0] else ch
                tone = "5"
                cleaned: list[str] = []
                for item in raw:
                    if item in tone_marks:
                        tone = tone_marks[item]
                    else:
                        cleaned.append(item)
                parts.append("".join(cleaned) + tone)
                continue
            parts.append(ch.lower() if ch.isascii() else ch)
        return " ".join(p for p in parts if p)

    def _kokoro_style_vector(self, voice_id: str, token_len: int) -> np.ndarray:
        voice_path = self.kokoro_voices_dir / f"{voice_id}.bin"
        if not voice_path.is_file():
            voice_path = self.kokoro_voices_dir / f"{self.default_voice}.bin"
        if not voice_path.is_file():
            first = next(self.kokoro_voices_dir.glob("*.bin"))
            voice_path = first
        raw = np.fromfile(voice_path, dtype=np.float32)
        if raw.size % 256 != 0:
            raise RuntimeError(f"invalid Kokoro voice style vector: {voice_path}")
        styles = raw.reshape((-1, 256))
        idx = min(max(token_len, 0), styles.shape[0] - 1)
        return styles[idx : idx + 1].astype(np.float32, copy=False)

    @staticmethod
    def _pcm_f32_to_wav(samples: np.ndarray, sample_rate: int) -> bytes:
        clipped = np.clip(samples, -1.0, 1.0)
        pcm = (clipped * 32767.0).astype("<i2")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm.tobytes())
        return buf.getvalue()

    def _synthesize_once(
        self,
        *,
        text: str,
        voice_id: str,
        session_id: str | None,
        attempt_index: int,
        max_new_frames: int,
    ) -> TtsResult:
        tmp_dir = Path(tempfile.gettempdir()) / "jachin_moss_tts"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix="moss_", suffix=".wav", dir=tmp_dir, delete=False) as tmp:
            output_path = Path(tmp.name)
        seed = self._session_seed(session_id=session_id, voice=voice_id, attempt_index=attempt_index)
        try:
            with self._synthesize_lock:
                result = self._runtime.synthesize(
                    text=text,
                    voice=voice_id,
                    output_audio_path=str(output_path),
                    do_sample=self.do_sample,
                    sample_mode=self.sample_mode,
                    streaming=True,
                    max_new_frames=max_new_frames,
                    voice_clone_max_text_tokens=self.voice_clone_max_text_tokens,
                    enable_wetext=False,
                    enable_normalize_tts_text=True,
                    seed=seed,
                )
            wav_path = Path(str(result.get("audio_path", output_path)))
            wav_bytes = wav_path.read_bytes()
        finally:
            try:
                output_path.unlink(missing_ok=True)
            except Exception:
                pass

        # MOSS runtime does not expose speed directly; apply a light time-domain adjustment here.
        wav_bytes = self._apply_speed_to_wav_bytes(wav_bytes, self.default_speed)
        sample_rate = int(result.get("sample_rate", DEFAULT_SAMPLE_RATE))
        duration_ms = self._wav_duration_ms(wav_bytes, sample_rate=sample_rate)
        return TtsResult(wav_bytes=wav_bytes, duration_ms=duration_ms, sample_rate=sample_rate)
    @staticmethod
    def _wav_duration_ms(wav_bytes: bytes, sample_rate: int) -> int:
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                frames = wf.getnframes()
                sr = wf.getframerate() or sample_rate
                return int(frames / max(1, sr) * 1000)
        except Exception:
            return 0

    def _session_seed(self, session_id: str | None, voice: str, attempt_index: int = 0) -> int | None:
        if self.base_seed is None:
            return None
        sid = (session_id or "").strip() or "default"
        raw = f"{self.base_seed}:{voice}:{sid}:attempt={attempt_index}".encode("utf-8", errors="ignore")
        digest = hashlib.sha256(raw).digest()
        return int.from_bytes(digest[:4], byteorder="little", signed=False)

    def _resolve_max_new_frames(self, text: str) -> int:
        if self.max_new_frames is not None:
            return self.max_new_frames
        text_len = self._speakable_text_len(text)
        if text_len <= 12:
            return 96
        if text_len <= 25:
            return 128
        if text_len <= 50:
            return 192
        return DEFAULT_MAX_NEW_FRAMES

    @staticmethod
    def _speakable_text_len(text: str) -> int:
        ignored = set(" \t\r\n，。！？、,.!?;；:：\"'“”‘’（）()[]【】")
        return sum(1 for ch in text if ch not in ignored)

    def _expected_audio_ms(self, text: str) -> int:
        text_len = self._speakable_text_len(text)
        return max(1600, text_len * 180)

    def _max_allowed_duration_ms(self, text: str) -> int:
        return min(self.max_allowed_audio_ms, int(self._expected_audio_ms(text) * 2.8))

    def _is_abnormal_tts_result(self, text: str, result: TtsResult) -> tuple[bool, str]:
        max_allowed = self._max_allowed_duration_ms(text)
        if result.duration_ms > max_allowed:
            return True, f"audio_too_long:{result.duration_ms}>{max_allowed}"
        return False, "ok"

    @staticmethod
    def _attempt_max_new_frames(base_max_new_frames: int, attempt_index: int) -> int:
        if attempt_index <= 0:
            return base_max_new_frames
        return max(48, int(round(base_max_new_frames * (0.8 ** attempt_index))))

    def _trim_result_to_allowed_duration(self, text: str, result: TtsResult) -> TtsResult:
        max_allowed = self._max_allowed_duration_ms(text)
        if result.duration_ms <= max_allowed:
            return result
        trimmed = self._trim_wav_to_duration(result.wav_bytes, max_allowed)
        return TtsResult(
            wav_bytes=trimmed,
            duration_ms=self._wav_duration_ms(trimmed, sample_rate=result.sample_rate),
            sample_rate=result.sample_rate,
            synth_ms=result.synth_ms,
            attempts=result.attempts,
            max_new_frames=result.max_new_frames,
            quality_status=f"trimmed:{result.quality_status}",
        )
    @staticmethod
    def _apply_speed_to_wav_bytes(wav_bytes: bytes, speed: float) -> bytes:
        speed = float(np.clip(speed, 0.8, 1.6))
        if abs(speed - 1.0) < 1e-3:
            return wav_bytes
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
                sample_rate = wf.getframerate()
                frame_count = wf.getnframes()
                pcm = wf.readframes(frame_count)

            if sample_width != 2 or frame_count <= 1:
                return wav_bytes

            audio = np.frombuffer(pcm, dtype=np.int16)
            if channels > 1:
                audio = audio.reshape(-1, channels)
            else:
                audio = audio.reshape(-1, 1)

            old_len = audio.shape[0]
            new_len = max(1, int(round(old_len / speed)))
            src_idx = np.arange(old_len, dtype=np.float32)
            dst_idx = np.linspace(0.0, max(0.0, old_len - 1), num=new_len, dtype=np.float32)
            out = np.empty((new_len, channels), dtype=np.int16)
            for ch in range(channels):
                out[:, ch] = np.interp(dst_idx, src_idx, audio[:, ch]).astype(np.int16)

            out_pcm = out.reshape(-1).tobytes()
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(channels)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(out_pcm)
            return buf.getvalue()
        except Exception:
            return wav_bytes

    @staticmethod
    def _trim_wav_to_duration(wav_bytes: bytes, max_duration_ms: int) -> bytes:
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
                sample_rate = wf.getframerate()
                max_frames = max(1, int(sample_rate * max_duration_ms / 1000.0))
                pcm = wf.readframes(max_frames)
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(channels)
                wf.setsampwidth(sample_width)
                wf.setframerate(sample_rate)
                wf.writeframes(pcm)
            return buf.getvalue()
        except Exception:
            return wav_bytes
    @staticmethod
    def _trim_wav_silence(wav_bytes: bytes) -> bytes:
        try:
            with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
                sample_rate = wf.getframerate()
                frame_count = wf.getnframes()
                pcm = wf.readframes(frame_count)

            if sample_width != 2 or frame_count <= 1:
                return wav_bytes

            audio = np.frombuffer(pcm, dtype=np.int16)
            if channels > 1:
                audio = audio.reshape(-1, channels)
                energy = np.max(np.abs(audio), axis=1)
            else:
                audio = audio.reshape(-1, 1)
                energy = np.abs(audio[:, 0])

            max_energy = int(np.max(energy)) if energy.size else 0
            if max_energy <= 0:
                return wav_bytes

            threshold = max(80, int(max_energy * 0.015))
            active = np.flatnonzero(energy > threshold)
            if active.size == 0:
                return wav_bytes

            pad_start = int(sample_rate * 0.04)
            pad_end = int(sample_rate * 0.16)
            start = max(0, int(active[0]) - pad_start)
            end = min(audio.shape[0], int(active[-1]) + pad_end)
            if end <= start:
                return wav_bytes

            if start == 0 and audio.shape[0] - end < int(sample_rate * 0.25):
                return wav_bytes

            trimmed = audio[start:end].reshape(-1).astype(np.int16).tobytes()
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(channels)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(trimmed)
            return buf.getvalue()
        except Exception:
            return wav_bytes
    def _fallback_sine(self, text: str, freq_hz: float, sample_rate: int) -> TtsResult:
        duration_ms = min(8000, max(300, len(text) * 85))
        wav = self._sine_wav(duration_ms=duration_ms, freq_hz=freq_hz, sample_rate=sample_rate)
        return TtsResult(wav_bytes=wav, duration_ms=duration_ms, sample_rate=sample_rate)

    def _error_wav(self, message: str) -> TtsResult:
        wav = self._sine_wav(duration_ms=800, freq_hz=220.0, sample_rate=DEFAULT_SAMPLE_RATE)
        logger.error("%s", message)
        return TtsResult(wav_bytes=wav, duration_ms=800, sample_rate=DEFAULT_SAMPLE_RATE)

    @staticmethod
    def _sine_wav(duration_ms: int, freq_hz: float, sample_rate: int = DEFAULT_SAMPLE_RATE) -> bytes:
        samples = int(sample_rate * (duration_ms / 1000.0))
        t = np.arange(samples, dtype=np.float64) / sample_rate
        audio = (0.35 * np.sin(2 * np.pi * freq_hz * t)).astype(np.float32)
        buf = io.BytesIO()
        pcm = (np.clip(audio, -1, 1) * 32767.0).astype(np.int16)
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm.tobytes())
        return buf.getvalue()





