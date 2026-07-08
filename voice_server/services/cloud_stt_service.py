from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import time
import wave
from dataclasses import dataclass, field
from typing import Any

import requests

try:
    from services.voice_understanding import VoiceUnderstandingCorrector
except ModuleNotFoundError:
    from voice_server.services.voice_understanding import VoiceUnderstandingCorrector

logger = logging.getLogger("jachin.voice_server.cloud_stt")
_MEANINGFUL_CHAR_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")


@dataclass
class CloudSttResult:
    text: str
    confidence: float
    duration_ms: int
    language: str
    hotword_count: int = 0
    hotword_status: str = "cloud_not_supported"
    hotword_sources: tuple[str, ...] = ()
    raw_text: str = ""
    user_message: str = ""
    user_message_source: str = ""
    reply_plan: dict[str, Any] = field(default_factory=dict)
    backend: str = "dashscope-qwen-asr"
    understanding: dict[str, Any] = field(default_factory=dict)


class CloudSttService:
    """DashScope Qwen-ASR short-audio service with the same interface as local STT."""

    def __init__(
        self,
        api_key: str,
        api_base: str,
        model: str = "qwen3-asr-flash",
        realtime_model: str = "qwen3-asr-flash-realtime",
        hotword_model: str = "fun-asr-realtime",
        file_model: str = "fun-asr",
        language: str = "",
    ) -> None:
        self.api_key = api_key.strip()
        self.api_base = api_base.rstrip("/")
        self.model_name = model.strip() or "qwen3-asr-flash"
        self.realtime_model = realtime_model.strip() or "qwen3-asr-flash-realtime"
        self.hotword_model = hotword_model.strip() or "fun-asr-realtime"
        self.file_model = file_model.strip() or "fun-asr"
        self.language = language.strip()
        self.model_path = f"cloud:{self.model_name}"
        self._load_error: str | None = None
        self._understanding = VoiceUnderstandingCorrector()
        self._client = requests.Session()

    @property
    def ready(self) -> bool:
        return bool(self.api_key and self.api_base)

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def _load_engine(self) -> bool:
        if not self.ready:
            self._load_error = "DASHSCOPE_API_KEY is missing" if not self.api_key else "DashScope API base is missing"
            return False
        return True

    def transcribe(self, audio_bytes: bytes) -> CloudSttResult:
        duration_ms = self._estimate_duration_ms(audio_bytes)
        if not self.ready:
            return CloudSttResult(
                text="",
                confidence=0.0,
                duration_ms=duration_ms,
                language=self.language or "zh",
                backend=f"dashscope:{self.model_name}",
            )

        mime = self._guess_mime(audio_bytes)
        data_uri = "data:{};base64,{}".format(mime, base64.b64encode(audio_bytes).decode("ascii"))
        body: dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {"data": data_uri},
                        }
                    ],
                }
            ],
            "stream": False,
            "asr_options": {"enable_itn": False},
        }
        if self.language:
            body["asr_options"]["language"] = self.language

        started = time.perf_counter()
        try:
            resp = self._client.post(
                f"{self.api_base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=float(self._timeout_seconds()),
            )
            if not resp.ok:
                self._load_error = f"DashScope ASR HTTP {resp.status_code}: {resp.text[:500]}"
                logger.warning("DashScope ASR failed status=%s body=%s", resp.status_code, resp.text[:500])
                return self._error_result(self._load_error, duration_ms)
            payload = resp.json()
            raw_text = self._extract_text(payload)
            text = self._apply_domain_terms(self._sanitize_transcript_text(raw_text))
            correction: dict[str, Any] = self._understanding.correct(text) if text else {}
            corrected = self._choose_transcript(text, str(correction.get("corrected_text") or "").strip())
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            logger.info("DashScope ASR ok model=%s elapsed_ms=%s text_len=%s", self.model_name, elapsed_ms, len(corrected))
            return CloudSttResult(
                text=corrected,
                raw_text=raw_text,
                user_message=str(correction.get("user_message") or "").strip(),
                user_message_source=str(correction.get("user_message_source") or "").strip(),
                reply_plan=correction.get("reply_plan", {}) if isinstance(correction.get("reply_plan"), dict) else {},
                confidence=0.92 if corrected else 0.0,
                duration_ms=duration_ms,
                language=self.language or "auto",
                backend=f"dashscope:{self.model_name}",
                understanding=correction.get("understanding", {}),
            )
        except Exception as e:
            self._load_error = str(e)
            logger.exception("DashScope ASR exception")
            return self._error_result(f"DashScope ASR error: {e}", duration_ms)

    def _error_result(self, message: str, duration_ms: int) -> CloudSttResult:
        return CloudSttResult(
            text=f"[STT error] {message}",
            confidence=0.0,
            duration_ms=duration_ms,
            language=self.language or "zh",
            backend=f"dashscope:{self.model_name}",
        )

    @staticmethod
    def _extract_text(payload: dict[str, Any]) -> str:
        try:
            choices = payload.get("choices") or []
            if choices:
                msg = choices[0].get("message") or {}
                content = msg.get("content")
                if isinstance(content, str):
                    return content.strip()
                if isinstance(content, list):
                    parts = []
                    for item in content:
                        if isinstance(item, dict):
                            parts.append(str(item.get("text") or item.get("content") or ""))
                    return "".join(parts).strip()
        except Exception:
            pass
        return ""

    @staticmethod
    def _sanitize_transcript_text(text: str) -> str:
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
        if not cleaned or not _MEANINGFUL_CHAR_RE.search(cleaned):
            return ""
        return cleaned

    @staticmethod
    def _guess_mime(audio_bytes: bytes) -> str:
        head = audio_bytes[:16]
        if head.startswith(b"RIFF") and b"WAVE" in head:
            return "audio/wav"
        if head.startswith(b"ID3") or head[:2] == b"\xff\xfb":
            return "audio/mpeg"
        if head.startswith(b"OggS"):
            return "audio/ogg"
        if head.startswith(b"fLaC"):
            return "audio/flac"
        return "audio/wav"

    @staticmethod
    def _estimate_duration_ms(audio_bytes: bytes) -> int:
        try:
            with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate() or 16000
                duration = int((frames / rate) * 1000)
                if 0 < duration < 60 * 60 * 1000:
                    return duration
        except Exception:
            pass
        try:
            if audio_bytes.startswith(b"RIFF") and len(audio_bytes) > 44:
                channels = int.from_bytes(audio_bytes[22:24], "little") or 1
                sample_rate = int.from_bytes(audio_bytes[24:28], "little") or 16000
                bits_per_sample = int.from_bytes(audio_bytes[34:36], "little") or 16
                bytes_per_second = sample_rate * channels * max(1, bits_per_sample // 8)
                if bytes_per_second > 0:
                    return int(max(0, len(audio_bytes) - 44) * 1000 / bytes_per_second)
        except Exception:
            pass
        return 0

    @staticmethod
    def _apply_domain_terms(text: str) -> str:
        if not text:
            return ""
        raw = os.getenv(
            "JACHIN_STT_DOMAIN_TERMS",
            "Jochen=Jachin,Jachi=Jachin,Jaqin=Jachin,Cortex=Codex,Lock=Lark",
        )
        out = text
        for item in raw.split(","):
            if "=" not in item:
                continue
            src, dst = item.split("=", 1)
            src = src.strip()
            dst = dst.strip()
            if src and dst:
                out = re.sub(rf"\b{re.escape(src)}\b", dst, out, flags=re.IGNORECASE)
        return out

    @staticmethod
    def _choose_transcript(raw_text: str, corrected_text: str) -> str:
        raw = (raw_text or "").strip()
        corrected = (corrected_text or "").strip()
        if not corrected:
            return raw
        raw_meaningful = re.sub(r"\s+", "", raw)
        corrected_meaningful = re.sub(r"\s+", "", corrected)
        if len(raw_meaningful) >= 8 and len(corrected_meaningful) < max(4, int(len(raw_meaningful) * 0.55)):
            return raw
        return corrected

    @staticmethod
    def _timeout_seconds() -> float:
        try:
            import os

            return max(3.0, min(60.0, float(os.getenv("JACHIN_STT_TIMEOUT_SEC", "20"))))
        except Exception:
            return 20.0
