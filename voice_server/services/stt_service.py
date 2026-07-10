from __future__ import annotations

import io
import logging
import math
import os
import re
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger("jachin.voice_server.stt")
_MEANINGFUL_CHAR_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")


@dataclass
class ZipformerFiles:
    encoder: Path
    decoder: Path
    joiner: Path
    tokens: Path
    bpe_vocab: Path | None = None


@dataclass
class SttResult:
    text: str
    confidence: float
    duration_ms: int
    language: str
    hotword_count: int = 0
    hotword_status: str = "not_configured"
    hotword_sources: tuple[str, ...] = ()
    raw_text: str = ""
    user_message: str = ""
    user_message_source: str = ""
    reply_plan: dict[str, Any] = field(default_factory=dict)
    backend: str = "sherpa-onnx-zipformer"
    understanding: dict[str, Any] = field(default_factory=dict)


class SttService:
    """Sherpa-ONNX Zipformer Transducer local STT.

    The local fallback is STT-only. It intentionally does not apply Jachin
    hotwords because local phrase biasing has proven too brittle for fallback
    recognition. Cloud STT may still use provider-native hotwords.
    """

    model_name = "sherpa-onnx-zipformer-zh-en-2023-11-22"

    def __init__(self, stt_dir: Path) -> None:
        self.stt_dir = stt_dir
        self.model_path = stt_dir / "encoder-epoch-34-avg-19.int8.onnx"
        self._files = self._find_zipformer_files(stt_dir)
        self._engine: Any = None
        self._load_error: str | None = None

    @property
    def ready(self) -> bool:
        return self._files is not None

    @property
    def load_error(self) -> str | None:
        return self._load_error

    @staticmethod
    def _find_zipformer_files(stt_dir: Path) -> ZipformerFiles | None:
        encoder = stt_dir / "encoder-epoch-34-avg-19.int8.onnx"
        if not encoder.is_file():
            candidates = sorted(stt_dir.glob("encoder-*.int8.onnx")) or sorted(stt_dir.glob("encoder-*.onnx"))
            encoder = candidates[0] if candidates else encoder
        decoder_candidates = sorted(stt_dir.glob("decoder-*.onnx"))
        joiner_candidates = sorted(stt_dir.glob("joiner-*.int8.onnx")) or sorted(stt_dir.glob("joiner-*.onnx"))
        tokens = stt_dir / "tokens.txt"
        bpe_vocab = stt_dir / "bpe.vocab"
        if not (encoder.is_file() and decoder_candidates and joiner_candidates and tokens.is_file()):
            return None
        return ZipformerFiles(
            encoder=encoder,
            decoder=decoder_candidates[0],
            joiner=joiner_candidates[0],
            tokens=tokens,
            bpe_vocab=bpe_vocab if bpe_vocab.is_file() else None,
        )

    def _load_engine(self) -> Any | None:
        if self._engine is not None:
            return self._engine
        if self._load_error is not None:
            return None
        if not self.ready or self._files is None:
            self._load_error = f"model missing: {self.stt_dir}"
            return None
        try:
            import sherpa_onnx

            logger.info("Loading Sherpa-ONNX Zipformer from %s", self.stt_dir)
            self._engine = sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=str(self._files.encoder),
                decoder=str(self._files.decoder),
                joiner=str(self._files.joiner),
                tokens=str(self._files.tokens),
                num_threads=int(os.getenv("JACHIN_STT_THREADS", "2")),
                sample_rate=16000,
                feature_dim=80,
                decoding_method="greedy_search",
                max_active_paths=int(os.getenv("JACHIN_STT_MAX_ACTIVE_PATHS", "4")),
                modeling_unit="bpe" if self._files.bpe_vocab else "cjkchar",
                bpe_vocab=str(self._files.bpe_vocab or ""),
                provider=os.getenv("JACHIN_STT_PROVIDER", "cpu"),
            )
            logger.info("Sherpa-ONNX Zipformer loaded")
            return self._engine
        except Exception as e:
            self._load_error = str(e)
            logger.exception("Sherpa-ONNX Zipformer load failed: %s", e)
            return None

    def transcribe(self, audio_bytes: bytes) -> SttResult:
        duration_ms = self._estimate_duration_ms(audio_bytes)
        if not self.ready:
            return SttResult(text="", confidence=0.0, duration_ms=duration_ms, language="zh")

        engine = self._load_engine()
        if engine is None:
            return SttResult(
                text=f"【STT错误】模型未加载: {self._load_error or 'unknown'}",
                confidence=0.0,
                duration_ms=duration_ms,
                language="zh",
            )

        audio, sample_rate = self._decode_audio_bytes(audio_bytes)
        if audio is None or len(audio) == 0:
            return SttResult(text="【STT错误】无法解析音频或音频为空", confidence=0.0, duration_ms=duration_ms, language="zh")

        audio = self._resample_to_16k(audio, sample_rate)
        raw_text = ""
        text = ""
        try:
            stream = engine.create_stream()
            stream.accept_waveform(16000, audio.astype(np.float32))
            engine.decode_stream(stream)
            raw_text = self._sanitize_transcript_text(stream.result.text)
            text = raw_text
        except Exception as e:
            logger.exception("Sherpa-ONNX transcribe failed")
            text = f"【STT错误】{e}"

        confidence = self._result_confidence(text)
        return SttResult(
            text=text,
            raw_text=raw_text,
            user_message="",
            user_message_source="",
            reply_plan={},
            confidence=confidence,
            duration_ms=duration_ms,
            language="zh",
            hotword_count=0,
            hotword_status="disabled_local_sherpa",
            hotword_sources=(),
            understanding={"local_hotwords": "disabled"},
        )

    @staticmethod
    def _result_confidence(text: str) -> float:
        if not text or text.startswith("【STT错误】"):
            return 0.0
        return 0.9

    @staticmethod
    def _sanitize_transcript_text(text: str) -> str:
        if not text:
            return ""
        cleaned = re.sub(r"\s+", " ", str(text)).strip()
        if not cleaned:
            return ""
        if not _MEANINGFUL_CHAR_RE.search(cleaned):
            return ""
        return cleaned

    @staticmethod
    def _decode_audio_bytes(audio_bytes: bytes) -> tuple[np.ndarray | None, int]:
        try:
            import soundfile as sf

            data, fs = sf.read(io.BytesIO(audio_bytes), dtype="float32", always_2d=False)
            if isinstance(data, np.ndarray) and data.ndim > 1:
                data = data.mean(axis=1)
            return np.asarray(data, dtype=np.float32), int(fs)
        except Exception:
            pass

        try:
            with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
                channels = wf.getnchannels()
                rate = wf.getframerate() or 16000
                frames = wf.readframes(wf.getnframes())
                audio = np.frombuffer(frames, dtype=np.int16)
                if channels > 1:
                    audio = audio.reshape(-1, channels).mean(axis=1)
                return (audio.astype(np.float32) / 32768.0), rate
        except Exception:
            return None, 0

    @staticmethod
    def _resample_to_16k(audio: np.ndarray, sample_rate: int) -> np.ndarray:
        if sample_rate == 16000 or sample_rate <= 0:
            return audio.astype(np.float32)
        try:
            from scipy.signal import resample_poly

            divisor = math.gcd(int(sample_rate), 16000)
            up = 16000 // divisor
            down = int(sample_rate) // divisor
            resampled = resample_poly(audio.astype(np.float32), up, down)
            return np.asarray(resampled, dtype=np.float32)
        except Exception:
            target_len = max(1, int(len(audio) * 16000 / sample_rate))
            x_old = np.linspace(0.0, 1.0, len(audio), dtype=np.float64)
            x_new = np.linspace(0.0, 1.0, target_len, dtype=np.float64)
            return np.interp(x_new, x_old, audio.astype(np.float64)).astype(np.float32)

    @staticmethod
    def _estimate_duration_ms(audio_bytes: bytes) -> int:
        try:
            with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate() or 16000
                return int((frames / rate) * 1000)
        except Exception:
            try:
                import soundfile as sf

                info = sf.info(io.BytesIO(audio_bytes))
                return int((info.frames / info.samplerate) * 1000) if info.samplerate else 0
            except Exception:
                return 0
