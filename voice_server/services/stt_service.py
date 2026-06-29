from __future__ import annotations

import io
import logging
import re
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger("jachin.voice_server.stt")
_SENSEVOICE_INTERNAL_TAG_RE = re.compile(r"<\|.*?\|>")
_MEANINGFUL_CHAR_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")


@dataclass
class SttResult:
    text: str
    confidence: float
    duration_ms: int
    language: str


class SttService:
    """SenseVoiceSmall ONNX（funasr-onnx）本地转写。"""

    def __init__(self, stt_dir: Path) -> None:
        self.stt_dir = stt_dir
        self.model_path = stt_dir / "model_quant.onnx"
        self._engine: Any = None
        self._load_error: str | None = None

    @property
    def ready(self) -> bool:
        return self.model_path.is_file()

    def _load_engine(self) -> Any | None:
        if self._engine is not None:
            return self._engine
        if self._load_error is not None:
            return None
        if not self.ready:
            self._load_error = f"model missing: {self.model_path}"
            return None
        try:
            from funasr_onnx import SenseVoiceSmall

            logger.info("Loading SenseVoice ONNX from %s", self.stt_dir)
            self._engine = SenseVoiceSmall(
                str(self.stt_dir),
                batch_size=1,
                quantize=True,
            )
            logger.info("SenseVoice ONNX loaded")
            return self._engine
        except Exception as e:
            self._load_error = str(e)
            logger.exception("SenseVoice load failed: %s", e)
            return None

    def transcribe(self, audio_bytes: bytes) -> SttResult:
        duration_ms = self._estimate_duration_ms(audio_bytes)
        if not self.ready:
            return SttResult(
                text="",
                confidence=0.0,
                duration_ms=duration_ms,
                language="zh",
            )

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
            return SttResult(
                text="【STT错误】无法解析音频或音频为空",
                confidence=0.0,
                duration_ms=duration_ms,
                language="zh",
            )

        audio = self._resample_to_16k(audio, sample_rate)

        try:
            from funasr_onnx.utils.postprocess_utils import rich_transcription_postprocess

            raw_list = engine(audio, fs=16000, language="auto", use_itn=True)
            raw = raw_list[0] if raw_list else ""
            text = rich_transcription_postprocess(raw).strip()
            if not text:
                text = (raw or "").strip()
            text = self._sanitize_transcript_text(text)
        except Exception as e:
            logger.exception("SenseVoice transcribe failed")
            text = f"【STT错误】{e}"

        confidence = 0.9 if text and not text.startswith("【STT错误】") else 0.0
        return SttResult(
            text=text,
            confidence=confidence,
            duration_ms=duration_ms,
            language="zh",
        )

    @staticmethod
    def _sanitize_transcript_text(text: str) -> str:
        """
        清理 SenseVoice 可能返回的内部标签（如 <|en|><|EMO_UNKNOWN|>）。
        若清理后仅剩噪声标点/空白，返回空字符串，交由上游判定为无效识别。
        """
        if not text:
            return ""
        cleaned = _SENSEVOICE_INTERNAL_TAG_RE.sub("", text)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
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
            return audio
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
