from __future__ import annotations

import io
import logging
import os
import queue
import re
import struct
import threading
import time
import wave
from dataclasses import dataclass
from typing import Any, Iterator

import requests

logger = logging.getLogger("jachin.voice_server.cloud_tts")


@dataclass
class CloudTtsResult:
    wav_bytes: bytes
    duration_ms: int
    sample_rate: int
    synth_ms: int = 0
    attempts: int = 1
    max_new_frames: int = 0
    quality_status: str = "ok"
    trace: dict[str, Any] | None = None


class TtsCancelledError(RuntimeError):
    pass


class CloudTtsService:
    """DashScope CosyVoice HTTP TTS service with the same interface as local TTS."""

    def __init__(
        self,
        api_key: str,
        http_api_base: str,
        model: str = "cosyvoice-v3-plus",
        fast_model: str = "cosyvoice-v3-flash",
        default_voice: str = "longanhuan",
        default_speed: float = 1.0,
        audio_format: str = "wav",
        sample_rate: int = 24000,
    ) -> None:
        self.api_key = api_key.strip()
        self.http_api_base = http_api_base.rstrip("/")
        self.model_name = model.strip() or "cosyvoice-v3-plus"
        self.fast_model = fast_model.strip() or "cosyvoice-v3-flash"
        self.default_voice = default_voice.strip() or "longanhuan"
        self.default_speed = float(default_speed or 1.0)
        self.audio_format = (audio_format or "wav").strip().lower()
        self.sample_rate = int(sample_rate or 24000)
        self.model_path = f"cloud:{self.model_name}"
        self._load_error: str | None = None

    @property
    def ready(self) -> bool:
        return bool(self.api_key and self.http_api_base)

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def _load_engine(self) -> bool:
        if not self.ready:
            self._load_error = "DASHSCOPE_API_KEY is missing" if not self.api_key else "DashScope HTTP API base is missing"
            return False
        try:
            import dashscope  # type: ignore

            dashscope.api_key = self.api_key
            dashscope.base_http_api_url = self.http_api_base
            return True
        except Exception as e:
            self._load_error = f"dashscope SDK unavailable: {e}"
            logger.exception("DashScope SDK load failed")
            return False

    def list_voices(self) -> list[str]:
        return [self.default_voice]

    def has_voice(self, voice: str | None) -> bool:
        if not voice:
            return True
        v = voice.strip()
        return bool(v) and not v.startswith("zm_")

    def cancel_session(self, _session_id: str) -> bool:
        return False

    def cancel_all(self) -> None:
        return None

    def synthesize(
        self,
        text: str,
        voice: str | None = None,
        session_id: str | None = None,
        speed: float | None = None,
        kind: str | None = None,
    ) -> CloudTtsResult:
        if not text.strip():
            raise ValueError("text is empty")
        if not self._load_engine():
            raise RuntimeError(self._load_error or "DashScope TTS not ready")

        synthesis_text = self._normalize_text_for_stable_style(text)
        selected_model = self._select_model(kind)
        selected_voice = self._select_voice(voice)
        selected_rate = self._normalize_rate(speed)
        started = time.perf_counter()
        actual_model = selected_model
        try:
            audio = b""
            last_error: Exception | None = None
            for candidate_model in self._model_candidates(selected_model):
                actual_model = candidate_model
                try:
                    audio = self._synthesize_with_dashscope(synthesis_text, candidate_model, selected_voice, selected_rate)
                    break
                except Exception as e:
                    last_error = e
                    logger.warning("DashScope TTS model failed model=%s voice=%s err=%s", candidate_model, selected_voice, e)
            if not audio:
                raise last_error or RuntimeError("DashScope TTS returned empty audio")
            audio = self._normalize_wav_header(audio)
            synth_ms = int((time.perf_counter() - started) * 1000)
            duration_ms = self._estimate_wav_duration_ms(audio)
            logger.info(
                "DashScope TTS ok model=%s voice=%s kind=%s synth_ms=%s bytes=%s",
                actual_model,
                selected_voice,
                kind or "",
                synth_ms,
                len(audio),
            )
            return CloudTtsResult(
                wav_bytes=audio,
                duration_ms=duration_ms,
                sample_rate=self.sample_rate,
                synth_ms=synth_ms,
                attempts=1,
                quality_status="cloud",
                trace={
                    "tts_kind": kind or "",
                    "model": actual_model,
                    "requested_model": selected_model,
                    "voice": selected_voice,
                    "backend": "dashscope-cosyvoice",
                    "session_id": session_id or "",
                    "input_text": text,
                    "synthesis_text": synthesis_text,
                    "text_normalized": synthesis_text != text,
                },
            )
        except Exception as e:
            self._load_error = str(e)
            logger.exception("DashScope TTS synthesize failed")
            raise

    def _synthesize_with_dashscope(self, text: str, model: str, voice: str, rate: float) -> bytes:
        try:
            return self._synthesize_with_http_tts(text, model, voice, rate)
        except ModuleNotFoundError as e:
            if "http_tts" not in str(e):
                raise
            logger.info("DashScope http_tts SDK unavailable; fallback to tts_v2")
        except Exception as e:
            msg = str(e)
            if "does not support http call" not in msg and "Model not exist" not in msg:
                raise
            logger.info("DashScope http_tts unavailable for model=%s; fallback to tts_v2: %s", model, e)
        return self._synthesize_with_tts_v2(text, model, voice, rate)

    def _synthesize_with_http_tts(self, text: str, model: str, voice: str, rate: float) -> bytes:
        import dashscope  # type: ignore
        from dashscope.audio.http_tts.http_speech_synthesizer import HttpSpeechSynthesizer  # type: ignore

        dashscope.api_key = self.api_key
        dashscope.base_http_api_url = self.http_api_base
        result = HttpSpeechSynthesizer.call(
            model=model,
            text=text,
            voice=voice,
            format=self.audio_format,
            sample_rate=self.sample_rate,
            rate=rate,
            stream=False,
            api_key=self.api_key,
            language_hints=self._language_hints(text),
        )
        audio_url = getattr(result, "audio_url", None)
        if not audio_url:
            raise RuntimeError(f"DashScope TTS returned no audio_url: {result!r}")
        return self._download_audio(str(audio_url))

    def _synthesize_with_tts_v2(self, text: str, model: str, voice: str, rate: float) -> bytes:
        import dashscope  # type: ignore
        from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer  # type: ignore

        dashscope.api_key = self.api_key
        fmt = self._tts_v2_format(AudioFormat)
        synthesizer = SpeechSynthesizer(
            model=model,
            voice=voice,
            format=fmt,
            speech_rate=rate,
            language_hints=self._language_hints(text),
            url=self._tts_v2_ws_url(),
        )
        audio = synthesizer.call(text, timeout_millis=int(self._timeout_seconds() * 1000))
        if not audio:
            response = None
            try:
                response = synthesizer.get_response()
            except Exception:
                pass
            raise RuntimeError(f"DashScope tts_v2 returned empty audio: {response!r}")
        return bytes(audio)

    def stream_synthesize(
        self,
        text: str,
        voice: str | None = None,
        session_id: str | None = None,
        speed: float | None = None,
        kind: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield PCM chunks from DashScope CosyVoice streaming TTS."""
        if not text.strip():
            raise ValueError("text is empty")
        if not self._load_engine():
            raise RuntimeError(self._load_error or "DashScope TTS not ready")

        synthesis_text = self._normalize_text_for_stable_style(text)
        import dashscope  # type: ignore
        from dashscope.audio.tts_v2 import AudioFormat, ResultCallback, SpeechSynthesizer  # type: ignore

        dashscope.api_key = self.api_key
        ws_url = self._tts_v2_ws_url()
        if ws_url:
            try:
                dashscope.base_websocket_api_url = ws_url
            except Exception:
                pass

        selected_model = self._select_model(kind)
        selected_voice = self._select_voice(voice)
        selected_rate = self._normalize_rate(speed)
        fmt = self._tts_v2_pcm_format(AudioFormat)
        events: "queue.Queue[dict[str, Any] | None]" = queue.Queue()
        started = time.perf_counter()
        first_packet_ms = 0

        class _Callback(ResultCallback):  # type: ignore[misc, valid-type]
            def on_open(self) -> None:
                events.put({"type": "open"})

            def on_complete(self) -> None:
                events.put(
                    {
                        "type": "complete",
                        "first_packet_ms": first_packet_ms,
                        "request_id": _safe_request_id(),
                    }
                )

            def on_error(self, message: str) -> None:
                events.put({"type": "error", "message": str(message)})
                events.put(None)

            def on_close(self) -> None:
                events.put({"type": "close"})
                events.put(None)

            def on_event(self, message: Any) -> None:
                events.put({"type": "event", "message": message})

            def on_data(self, data: bytes) -> None:
                nonlocal first_packet_ms
                if not data:
                    return
                if first_packet_ms <= 0:
                    first_packet_ms = int((time.perf_counter() - started) * 1000)
                events.put({"type": "audio", "data": bytes(data), "elapsed_ms": int((time.perf_counter() - started) * 1000)})

        callback = _Callback()
        synthesizer = SpeechSynthesizer(
            model=selected_model,
            voice=selected_voice,
            format=fmt,
            speech_rate=selected_rate,
            language_hints=self._language_hints(text),
            callback=callback,
            url=ws_url,
        )

        def _safe_request_id() -> str:
            try:
                return str(synthesizer.get_last_request_id() or "")
            except Exception:
                return ""

        def _run() -> None:
            try:
                synthesizer.call(synthesis_text, timeout_millis=int(self._timeout_seconds() * 1000))
            except Exception as exc:  # noqa: BLE001
                events.put({"type": "error", "message": str(exc)})
                events.put(None)

        threading.Thread(target=_run, name="dashscope-tts-stream", daemon=True).start()
        yield {
            "type": "meta",
            "backend": "dashscope-cosyvoice-stream",
            "model": selected_model,
            "voice": selected_voice,
            "format": "pcm_s16le",
            "sample_rate": self.sample_rate,
            "channels": 1,
            "session_id": session_id or "",
            "input_text": text,
            "synthesis_text": synthesis_text,
            "text_normalized": synthesis_text != text,
        }
        while True:
            try:
                event = events.get(timeout=self._timeout_seconds())
            except queue.Empty:
                yield {"type": "error", "message": "DashScope TTS stream timeout"}
                break
            if event is None:
                break
            yield event

    def _tts_v2_ws_url(self) -> str | None:
        explicit = os.getenv("JACHIN_TTS_WS_API_BASE", "").strip()
        if explicit:
            return explicit
        base = self.http_api_base.rstrip("/")
        if base.endswith("/api/v1"):
            host = base[: -len("/api/v1")]
            if host.startswith("https://"):
                return "wss://" + host[len("https://") :] + "/api-ws/v1/inference"
            if host.startswith("http://"):
                return "ws://" + host[len("http://") :] + "/api-ws/v1/inference"
        return None

    def _tts_v2_format(self, audio_format_enum: Any) -> Any:
        if self.audio_format == "mp3":
            return getattr(audio_format_enum, f"MP3_{self.sample_rate}HZ_MONO_256KBPS", audio_format_enum.MP3_24000HZ_MONO_256KBPS)
        if self.audio_format == "pcm":
            return getattr(audio_format_enum, f"PCM_{self.sample_rate}HZ_MONO_16BIT", audio_format_enum.PCM_24000HZ_MONO_16BIT)
        return getattr(audio_format_enum, f"WAV_{self.sample_rate}HZ_MONO_16BIT", audio_format_enum.WAV_24000HZ_MONO_16BIT)

    def _tts_v2_pcm_format(self, audio_format_enum: Any) -> Any:
        return getattr(audio_format_enum, f"PCM_{self.sample_rate}HZ_MONO_16BIT", audio_format_enum.PCM_24000HZ_MONO_16BIT)

    def _download_audio(self, url: str) -> bytes:
        resp = requests.get(url, timeout=self._timeout_seconds())
        if not resp.ok:
            raise RuntimeError(f"download synthesized audio failed {resp.status_code}: {resp.text[:300]}")
        return resp.content

    def _select_model(self, kind: str | None) -> str:
        if str(kind or "").strip().lower() == "cue" and self.fast_model:
            return self.fast_model
        if os.getenv("JACHIN_TTS_FORCE_FAST", "").strip().lower() in {"1", "true", "yes", "on"}:
            return self.fast_model or self.model_name
        return self.model_name

    @staticmethod
    def _model_candidates(model: str) -> list[str]:
        model = (model or "").strip()
        candidates = [model] if model else []
        fallback_map = {
            "cosyvoice-v3.5-plus": ["cosyvoice-v3-plus", "cosyvoice-v3-flash"],
            "cosyvoice-v3.5-flash": ["cosyvoice-v3-flash"],
        }
        for item in fallback_map.get(model, []):
            if item not in candidates:
                candidates.append(item)
        return candidates

    def _select_voice(self, voice: str | None) -> str:
        v = str(voice or "").strip()
        if not v or v.startswith("zm_") or v.lower().startswith("kokoro"):
            return self.default_voice
        return v

    @staticmethod
    def _normalize_text_for_stable_style(text: str) -> str:
        """Normalize assistant-facing TTS text to reduce CosyVoice style jumps."""
        s = str(text or "")
        s = re.sub(r"\s+", " ", s).strip()
        if not s:
            return s
        s = re.sub(r"^\s*(你好|您好)\s*主人\s*[，,、]?\s*", r"\1，", s)
        s = re.sub(r"^\s*主人\s*[，,、]?\s*", "", s)
        s = re.sub(r"\s*主人\s*", "", s)
        s = re.sub(r"\bLark\b", "飞书", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*飞书\s*", "飞书", s)
        s = re.sub(r"\s+", " ", s).strip()
        s = re.sub(r"([，。！？；：、])\s+", r"\1", s)
        body_without_final_period = s[:-1] if s.endswith("。") else s
        if len(s) <= 60 and body_without_final_period.count("。") == 1 and "？" not in s and "！" not in s:
            s = s.replace("。", "，", 1)
        return s

    @staticmethod
    def _normalize_rate(speed: float | None) -> float:
        try:
            return max(0.5, min(2.0, float(speed if speed is not None else 1.0)))
        except Exception:
            return 1.0

    @staticmethod
    def _language_hints(text: str) -> list[str] | None:
        has_zh = any("\u4e00" <= ch <= "\u9fff" for ch in text)
        has_en = any(("a" <= ch.lower() <= "z") for ch in text)
        if has_zh and not has_en:
            return ["zh"]
        if has_en and not has_zh:
            return ["en"]
        return None

    @staticmethod
    def _normalize_wav_header(audio: bytes) -> bytes:
        if not audio.startswith(b"RIFF") or audio[8:12] != b"WAVE":
            return audio
        try:
            offset = 12
            fmt_payload = b""
            data_payload = b""
            while offset + 8 <= len(audio):
                chunk_id = audio[offset : offset + 4]
                chunk_size = struct.unpack_from("<I", audio, offset + 4)[0]
                payload_start = offset + 8
                if chunk_id == b"fmt ":
                    fmt_payload = audio[payload_start : min(payload_start + chunk_size, len(audio))]
                if chunk_id == b"data":
                    data_payload = audio[payload_start:]
                    break
                offset = payload_start + chunk_size + (chunk_size % 2)
            if len(fmt_payload) < 16 or not data_payload:
                return audio
            audio_format, channels, sample_rate, byte_rate, block_align, bits_per_sample = struct.unpack(
                "<HHIIHH", fmt_payload[:16]
            )
            if audio_format != 1 or channels <= 0 or sample_rate <= 0 or block_align <= 0:
                return audio
            data_len = len(data_payload)
            header = (
                b"RIFF"
                + struct.pack("<I", 36 + data_len)
                + b"WAVEfmt "
                + struct.pack("<IHHIIHH", 16, audio_format, channels, sample_rate, byte_rate, block_align, bits_per_sample)
                + b"data"
                + struct.pack("<I", data_len)
            )
            return header + data_payload
        except Exception:
            return audio

    @staticmethod
    def _estimate_wav_duration_ms(audio: bytes) -> int:
        try:
            with wave.open(io.BytesIO(audio), "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate() or 24000
                duration = int((frames / rate) * 1000)
                # Some DashScope streaming WAV responses use a placeholder RIFF
                # chunk size, which makes wave report an absurd frame count.
                if 0 < duration < 60 * 60 * 1000:
                    return duration
        except Exception:
            pass
        try:
            if audio.startswith(b"RIFF") and len(audio) > 44:
                channels = int.from_bytes(audio[22:24], "little") or 1
                sample_rate = int.from_bytes(audio[24:28], "little") or 24000
                bits_per_sample = int.from_bytes(audio[34:36], "little") or 16
                bytes_per_second = sample_rate * channels * max(1, bits_per_sample // 8)
                if bytes_per_second > 0:
                    return int(max(0, len(audio) - 44) * 1000 / bytes_per_second)
        except Exception:
            pass
        return 0

    @staticmethod
    def _timeout_seconds() -> float:
        try:
            return max(5.0, min(120.0, float(os.getenv("JACHIN_TTS_TIMEOUT_SEC", "45"))))
        except Exception:
            return 45.0
