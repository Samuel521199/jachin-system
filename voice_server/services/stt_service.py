from __future__ import annotations

import io
import logging
import math
import os
import re
import tempfile
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from services.stt_hotwords import HotwordSnapshot, SttHotwordProvider

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

    The voice layer is STT-only: native hotwords may bias decoding, but this
    service does not rewrite entities, select intents, or create reply plans.
    """

    model_name = "sherpa-onnx-zipformer-zh-en-2023-11-22"

    def __init__(self, stt_dir: Path) -> None:
        self.stt_dir = stt_dir
        self.model_path = stt_dir / "encoder-epoch-34-avg-19.int8.onnx"
        self._files = self._find_zipformer_files(stt_dir)
        self._engine: Any = None
        self._load_error: str | None = None
        self._hotwords = SttHotwordProvider()
        self._hotword_file: Path | None = None
        self._hotword_signature: tuple[tuple[str, int], ...] = ()

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

    def _load_engine(self, hotword_snapshot: HotwordSnapshot | None = None) -> Any | None:
        hotword_snapshot = hotword_snapshot or self._hotwords.snapshot()
        hotword_signature = self._snapshot_signature(hotword_snapshot)
        if self._engine is not None and hotword_signature != self._hotword_signature:
            logger.info("Sherpa hotwords changed; reloading STT recognizer")
            self._engine = None
        if self._engine is not None:
            return self._engine
        if self._load_error is not None:
            return None
        if not self.ready or self._files is None:
            self._load_error = f"model missing: {self.stt_dir}"
            return None
        try:
            import sherpa_onnx

            hotwords_file = self._prepare_hotword_file(hotword_snapshot)
            logger.info("Loading Sherpa-ONNX Zipformer from %s", self.stt_dir)
            self._engine = sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=str(self._files.encoder),
                decoder=str(self._files.decoder),
                joiner=str(self._files.joiner),
                tokens=str(self._files.tokens),
                num_threads=int(os.getenv("JACHIN_STT_THREADS", "2")),
                sample_rate=16000,
                feature_dim=80,
                decoding_method="modified_beam_search" if hotwords_file else "greedy_search",
                max_active_paths=int(os.getenv("JACHIN_STT_MAX_ACTIVE_PATHS", "4")),
                hotwords_file=str(hotwords_file) if hotwords_file else "",
                hotwords_score=float(os.getenv("JACHIN_STT_HOTWORDS_SCORE", "4.0")),
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

        hotword_snapshot = self._hotwords.snapshot()
        engine = self._load_engine(hotword_snapshot)
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
            hotword_count=hotword_snapshot.count,
            hotword_status="applied" if hotword_snapshot.words else "not_configured",
            hotword_sources=tuple(hotword_snapshot.sources),
            understanding={},
        )

    def _prepare_hotword_file(self, snapshot: HotwordSnapshot) -> Path | None:
        signature = self._snapshot_signature(snapshot)
        if not signature:
            self._hotword_file = None
            self._hotword_signature = ()
            return None
        if self._hotword_file and self._hotword_file.is_file() and signature == self._hotword_signature:
            return self._hotword_file
        path = Path(tempfile.gettempdir()) / "jachin_sherpa_hotwords.txt"
        lines = [f"{word} :{self._format_hotword_weight(weight)}" for word, weight in sorted(snapshot.words.items())]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self._hotword_file = path
        self._hotword_signature = signature
        return path

    @staticmethod
    def _snapshot_signature(snapshot: HotwordSnapshot) -> tuple[tuple[str, int], ...]:
        return tuple(sorted(snapshot.words.items()))

    @staticmethod
    def _format_hotword_weight(weight: int) -> str:
        return str(max(1, min(100, int(weight or 1))))

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
