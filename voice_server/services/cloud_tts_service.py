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

_DASHSCOPE_TTS_CLOSE_PATCHED = False


def _patch_dashscope_tts_close_callback(speech_synthesizer_cls: Any) -> None:
    """Make DashScope TTS close callbacks tolerant of websocket-client versions."""
    global _DASHSCOPE_TTS_CLOSE_PATCHED
    if _DASHSCOPE_TTS_CLOSE_PATCHED:
        return
    original = getattr(speech_synthesizer_cls, "on_close", None)
    if original is None or getattr(original, "_jachin_close_compat", False):
        _DASHSCOPE_TTS_CLOSE_PATCHED = True
        return

    def _on_close_compat(self: Any, *args: Any, **kwargs: Any) -> Any:
        ws = args[0] if len(args) >= 1 else None
        close_status_code = args[1] if len(args) >= 2 else None
        close_msg = args[2] if len(args) >= 3 else None
        try:
            return original(self, ws, close_status_code, close_msg, **kwargs)
        except TypeError as exc:
            if "close_status_code" not in str(exc) and "close_msg" not in str(exc):
                raise
            logger.debug("DashScope TTS close callback compatibility swallowed TypeError: %s", exc)
            return None

    _on_close_compat._jachin_close_compat = True  # type: ignore[attr-defined]
    setattr(speech_synthesizer_cls, "on_close", _on_close_compat)
    _DASHSCOPE_TTS_CLOSE_PATCHED = True


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
        audio_format: str = "pcm",
        sample_rate: int = 24000,
    ) -> None:
        self.api_key = api_key.strip()
        self.http_api_base = http_api_base.rstrip("/")
        self.model_name = model.strip() or "cosyvoice-v3-plus"
        self.fast_model = fast_model.strip() or "cosyvoice-v3-flash"
        self.default_voice = default_voice.strip() or "longanhuan"
        self.default_speed = float(default_speed or 1.0)
        self.audio_format = (audio_format or "pcm").strip().lower()
        self.sample_rate = int(sample_rate or 24000)
        self.model_path = f"cloud:{self.model_name}"
        self._load_error: str | None = None
        self._prewarm_lock = threading.Lock()
        self._last_prewarm_at = 0.0
        self._last_prewarm_trace: dict[str, Any] = {}
        self._pool_lock = threading.Lock()
        self._pool: Any | None = None
        self._pool_signature = ""
        self._pool_ready_trace: dict[str, Any] = {}

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
            from dashscope.audio.tts_v2 import SpeechSynthesizer  # type: ignore

            _patch_dashscope_tts_close_callback(SpeechSynthesizer)
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

    def prewarm_stream(self, reason: str = "manual", min_interval_sec: float | None = None) -> dict[str, Any]:
        """Warm the cloud streaming path and keep SDK WebSocket objects hot."""
        ttl = self._prewarm_min_interval_seconds() if min_interval_sec is None else max(0.0, float(min_interval_sec))
        now = time.time()
        age = now - self._last_prewarm_at if self._last_prewarm_at > 0 else None
        if age is not None and age < ttl:
            return {
                "ok": True,
                "status": "skipped_recent",
                "age_ms": int(age * 1000),
                "trace": self._last_prewarm_trace,
                "connection_reuse_supported": self._pool_enabled(),
            }
        if not self._prewarm_lock.acquire(blocking=False):
            return {
                "ok": True,
                "status": "already_running",
                "trace": self._last_prewarm_trace,
                "connection_reuse_supported": self._pool_enabled(),
            }
        try:
            text = os.getenv("JACHIN_TTS_PREWARM_TEXT", "\u6211\u5728\u3002").strip() or "\u6211\u5728\u3002"
            trace: dict[str, Any] = {
                "reason": reason,
                "text_len": len(text),
                "format": "pcm_s16le",
                "sample_rate": self.sample_rate,
                "connection_reuse_supported": self._pool_enabled(),
                "checks": {},
            }
            checks = {
                "cue": self._prewarm_stream_kind(text=text, kind="cue", reason=reason, now=now),
                "content": self._prewarm_stream_kind(text=text, kind="content", reason=reason, now=now),
            }
            trace["checks"] = checks
            trace["cue"] = checks["cue"]
            trace["content"] = checks["content"]
            ok = bool(checks["cue"].get("ok") and checks["content"].get("ok"))
            trace["ok"] = ok
            self._last_prewarm_at = time.time()
            self._last_prewarm_trace = trace
            if ok:
                logger.info("DashScope TTS stream prewarm ok reason=%s trace=%s", reason, trace)
            else:
                logger.warning("DashScope TTS stream prewarm failed reason=%s trace=%s", reason, trace)
            return {"ok": ok, "status": "warmed" if ok else "failed", "trace": trace, "connection_reuse_supported": self._pool_enabled()}
        except Exception as e:
            trace = {
                "reason": reason,
                "error": str(e),
                "connection_reuse_supported": self._pool_enabled(),
            }
            self._last_prewarm_at = time.time()
            self._last_prewarm_trace = trace
            logger.warning("DashScope TTS stream prewarm exception reason=%s err=%s", reason, e)
            return {"ok": False, "status": "failed", "trace": trace, "connection_reuse_supported": self._pool_enabled()}
        finally:
            self._prewarm_lock.release()

    def _prewarm_stream_kind(self, *, text: str, kind: str, reason: str, now: float) -> dict[str, Any]:
        started = time.perf_counter()
        trace: dict[str, Any] = {
            "reason": reason,
            "kind": kind,
            "text_len": len(text),
            "requested_model": self._select_model(kind),
            "format": "pcm_s16le",
            "sample_rate": self.sample_rate,
            "connection_reuse_supported": self._pool_enabled(),
            "fallbacks": [],
        }
        chunks = 0
        byte_count = 0
        last_error = ""
        session_id = f"prewarm-{kind}-{int(now)}"
        for event in self.stream_synthesize(text, voice=self.default_voice, session_id=session_id, kind=kind):
            event_type = str(event.get("type") or "")
            if event_type == "open":
                trace["tts_ws_open_ms"] = int(event.get("elapsed_ms") or 0)
                trace["model"] = event.get("model") or trace.get("model") or trace.get("requested_model")
            elif event_type == "meta":
                trace["backend"] = event.get("backend")
                trace["model"] = event.get("model")
                trace["requested_model"] = event.get("requested_model") or trace.get("requested_model")
                trace["voice"] = event.get("voice")
                trace["format"] = event.get("format")
                trace["sample_rate"] = event.get("sample_rate")
            elif event_type == "event":
                msg = event.get("message")
                if isinstance(msg, dict) and msg.get("event") == "model_fallback":
                    trace["fallbacks"].append(msg)
            elif event_type == "audio":
                chunks += 1
                data = event.get("data") or b""
                byte_count += len(data) if isinstance(data, bytes) else 0
                trace.setdefault("tts_first_audio_ms", int(event.get("elapsed_ms") or 0))
                trace["model"] = event.get("model") or trace.get("model")
            elif event_type == "complete":
                trace["tts_total_ms"] = int(event.get("total_ms") or int((time.perf_counter() - started) * 1000))
                trace["request_id"] = event.get("request_id") or ""
                trace["model"] = event.get("model") or trace.get("model")
                break
            elif event_type == "error":
                last_error = str(event.get("message") or "")
                trace["last_error"] = last_error[:500]
        trace["chunks"] = chunks
        trace["bytes"] = byte_count
        trace.setdefault("tts_total_ms", int((time.perf_counter() - started) * 1000))
        trace["ok"] = chunks > 0
        if not trace["ok"] and last_error:
            trace["error"] = last_error[:500]
        return trace

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
            if self.audio_format == "pcm":
                audio = self._pcm16_to_wav(audio, self.sample_rate, channels=1)
            else:
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
                    "format": "wav_compat_from_pcm" if self.audio_format == "pcm" else self.audio_format,
                    "requested_format": self.audio_format,
                    "sample_rate": self.sample_rate,
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

        _patch_dashscope_tts_close_callback(SpeechSynthesizer)
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
        selected_model = self._select_model(kind)
        selected_voice = self._select_voice(voice)
        selected_rate = self._normalize_rate(speed)
        candidates = self._model_candidates(selected_model)
        last_error = ""
        for idx, candidate_model in enumerate(candidates):
            saw_audio = False
            saw_complete = False
            attempt_error = ""
            if idx > 0:
                yield {
                    "type": "event",
                    "message": {
                        "event": "model_fallback",
                        "from_model": candidates[idx - 1],
                        "to_model": candidate_model,
                        "reason": last_error[:300],
                    },
                }
            for event in self._stream_synthesize_once(
                text=text,
                synthesis_text=synthesis_text,
                requested_model=selected_model,
                model=candidate_model,
                voice=selected_voice,
                rate=selected_rate,
                session_id=session_id,
                kind=kind,
            ):
                event_type = str(event.get("type") or "")
                if event_type == "audio":
                    saw_audio = True
                elif event_type == "complete":
                    saw_complete = True
                elif event_type == "error":
                    attempt_error = str(event.get("message") or "")
                    last_error = attempt_error
                    if (
                        not saw_audio
                        and idx < len(candidates) - 1
                        and self._should_retry_stream_model_error(attempt_error)
                    ):
                        continue
                yield event
            if saw_complete or saw_audio:
                return
            if not self._should_retry_stream_model_error(attempt_error) or idx >= len(candidates) - 1:
                return

    def _stream_synthesize_once(
        self,
        *,
        text: str,
        synthesis_text: str,
        requested_model: str,
        model: str,
        voice: str,
        rate: float,
        session_id: str | None,
        kind: str | None,
    ) -> Iterator[dict[str, Any]]:
        import dashscope  # type: ignore
        from dashscope.audio.tts_v2 import AudioFormat, ResultCallback, SpeechSynthesizer  # type: ignore

        dashscope.api_key = self.api_key
        ws_url = self._tts_v2_ws_url()
        if ws_url:
            try:
                dashscope.base_websocket_api_url = ws_url
            except Exception:
                pass

        fmt = self._tts_v2_pcm_format(AudioFormat)
        events: "queue.Queue[dict[str, Any] | None]" = queue.Queue()
        started = time.perf_counter()
        first_packet_ms = 0
        opened_ms = 0
        pool_borrow_ms = 0
        pool_reused = False
        pool_enabled = self._pool_enabled()
        pool_trace: dict[str, Any] = {}
        stream_failed = False
        stream_completed = False

        class _Callback(ResultCallback):  # type: ignore[misc, valid-type]
            def on_open(self) -> None:
                nonlocal opened_ms
                opened_ms = int((time.perf_counter() - started) * 1000)
                if opened_ms > self_outer._ws_open_warn_ms():
                    logger.warning(
                        "DashScope TTS ws open slow opened_ms=%s threshold_ms=%s model=%s kind=%s session_id=%s",
                        opened_ms,
                        self_outer._ws_open_warn_ms(),
                        model,
                        kind or "",
                        session_id or "",
                    )
                events.put({"type": "open", "elapsed_ms": opened_ms, "model": model, "requested_model": requested_model})

            def on_complete(self) -> None:
                nonlocal stream_completed
                stream_completed = True
                events.put(
                    {
                        "type": "complete",
                        "first_packet_ms": first_packet_ms,
                        "opened_ms": opened_ms,
                        "total_ms": int((time.perf_counter() - started) * 1000),
                        "request_id": _safe_request_id(),
                        "model": model,
                        "requested_model": requested_model,
                    }
                )

            def on_error(self, message: str) -> None:
                nonlocal stream_failed
                stream_failed = True
                events.put({"type": "error", "message": str(message), "model": model, "requested_model": requested_model})
                events.put(None)

            def on_close(self, *_args: Any, **_kwargs: Any) -> None:
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
                    self_outer._log_first_audio_latency(
                        first_packet_ms,
                        model=model,
                        kind=kind,
                        session_id=session_id,
                        text_len=len(synthesis_text),
                    )
                events.put({"type": "audio", "data": bytes(data), "elapsed_ms": int((time.perf_counter() - started) * 1000), "model": model, "requested_model": requested_model})

        self_outer = self
        callback = _Callback()
        synthesizer: Any | None = None

        def _safe_request_id() -> str:
            try:
                return str(synthesizer.get_last_request_id() or "") if synthesizer is not None else ""
            except Exception:
                return ""

        def _run() -> None:
            nonlocal synthesizer, pool_borrow_ms, pool_reused, pool_trace
            try:
                borrow_started = time.perf_counter()
                if pool_enabled:
                    pool = self._ensure_synthesizer_pool(ws_url)
                    pool_trace = dict(self._pool_ready_trace)
                    synthesizer = pool.borrow_synthesizer(
                        model=model,
                        voice=voice,
                        format=fmt,
                        speech_rate=rate,
                        language_hints=self._language_hints(text),
                        callback=callback,
                        additional_params={"enable_ssml": False},
                    )
                    pool_reused = self._synthesizer_is_connected(synthesizer)
                else:
                    synthesizer = SpeechSynthesizer(
                        model=model,
                        voice=voice,
                        format=fmt,
                        speech_rate=rate,
                        language_hints=self._language_hints(text),
                        callback=callback,
                        url=ws_url,
                        additional_params={"enable_ssml": False},
                    )
                pool_borrow_ms = int((time.perf_counter() - borrow_started) * 1000)
                if pool_enabled:
                    logger.info(
                        "DashScope TTS pool borrow model=%s voice=%s borrow_ms=%s reused=%s session_id=%s",
                        model,
                        voice,
                        pool_borrow_ms,
                        pool_reused,
                        session_id or "",
                    )
                synthesizer.streaming_call(synthesis_text)
                synthesizer.streaming_complete(int(self._timeout_seconds() * 1000))
            except Exception as exc:  # noqa: BLE001
                nonlocal_stream_failed["value"] = True
                events.put({"type": "error", "message": str(exc), "model": model, "requested_model": requested_model})
                events.put(None)

        nonlocal_stream_failed = {"value": False}
        threading.Thread(target=_run, name="dashscope-tts-stream", daemon=True).start()
        yield {
            "type": "meta",
            "backend": "dashscope-cosyvoice-stream",
            "model": model,
            "requested_model": requested_model,
            "voice": voice,
            "format": "pcm_s16le",
            "requested_format": "pcm",
            "sample_width": 2,
            "sample_rate": self.sample_rate,
            "channels": 1,
            "connection_reuse_supported": pool_enabled,
            "pool_reused": pool_reused,
            "pool_borrow_ms": pool_borrow_ms,
            "pool_ready_trace": pool_trace,
            "session_id": session_id or "",
            "input_text": text,
            "synthesis_text": synthesis_text,
            "text_normalized": synthesis_text != text,
        }
        while True:
            try:
                event = events.get(timeout=self._timeout_seconds())
            except queue.Empty:
                stream_failed = True
                yield {"type": "error", "message": "DashScope TTS stream timeout", "model": model, "requested_model": requested_model}
                break
            if event is None:
                break
            if str(event.get("type") or "") == "error":
                stream_failed = True
            yield event
        if pool_enabled and synthesizer is not None:
            if stream_completed and not stream_failed and not nonlocal_stream_failed["value"]:
                try:
                    self._ensure_synthesizer_pool(ws_url).return_synthesizer(synthesizer)
                except Exception as exc:
                    logger.warning("DashScope TTS return synthesizer failed: %s", exc)
            else:
                logger.warning(
                    "DashScope TTS stream object discarded model=%s requested_model=%s session_id=%s failed=%s completed=%s",
                    model,
                    requested_model,
                    session_id or "",
                    stream_failed or nonlocal_stream_failed["value"],
                    stream_completed,
                )

    def _ensure_synthesizer_pool(self, ws_url: str | None) -> Any:
        import dashscope  # type: ignore
        from dashscope.audio.tts_v2 import SpeechSynthesizer  # type: ignore
        from dashscope.audio.tts_v2.speech_synthesizer import SpeechSynthesizerObjectPool  # type: ignore

        _patch_dashscope_tts_close_callback(SpeechSynthesizer)
        dashscope.api_key = self.api_key
        if ws_url:
            try:
                dashscope.base_websocket_api_url = ws_url
            except Exception:
                pass
        size = self._pool_size()
        signature = f"{self.api_key[:6]}:{ws_url or ''}:{size}"
        with self._pool_lock:
            if self._pool is not None and self._pool_signature == signature:
                return self._pool
            started = time.perf_counter()
            self._pool = SpeechSynthesizerObjectPool(max_size=size, url=ws_url)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            self._pool_signature = signature
            self._pool_ready_trace = {
                "pool_size": size,
                "pool_init_ms": elapsed_ms,
                "ws_url": ws_url or "",
                "connection_reuse_supported": True,
            }
            logger.info("DashScope TTS object pool ready size=%s init_ms=%s ws_url=%s", size, elapsed_ms, ws_url or "")
            return self._pool

    @staticmethod
    def _synthesizer_is_connected(synthesizer: Any) -> bool:
        try:
            return bool(synthesizer._SpeechSynthesizer__is_connected())  # pylint: disable=protected-access
        except Exception:
            return False

    @staticmethod
    def _pool_enabled() -> bool:
        raw = os.getenv("JACHIN_TTS_POOL_ENABLED", "1").strip().lower()
        return raw not in {"0", "false", "no", "off"}

    @staticmethod
    def _pool_size() -> int:
        try:
            return max(1, min(20, int(os.getenv("JACHIN_TTS_POOL_SIZE", "2"))))
        except Exception:
            return 2

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
            "cosyvoice-v3": ["cosyvoice-v3-plus", "cosyvoice-v3-flash"],
            "cosyvoice-v3.5-plus": ["cosyvoice-v3-plus", "cosyvoice-v3-flash"],
            "cosyvoice-v3.5-flash": ["cosyvoice-v3-flash"],
        }
        for item in fallback_map.get(model, []):
            if item not in candidates:
                candidates.append(item)
        return candidates

    @staticmethod
    def _should_retry_stream_model_error(message: str) -> bool:
        msg = str(message or "")
        if not msg:
            return False
        retry_markers = (
            "AccessDenied",
            "Model not exist",
            "model_not_exist",
            "task-failed",
            "does not support",
            "InvalidParameter",
            "DashScope TTS stream timeout",
        )
        return any(marker.lower() in msg.lower() for marker in retry_markers)

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
    def _pcm16_to_wav(pcm: bytes, sample_rate: int, channels: int = 1) -> bytes:
        if pcm.startswith(b"RIFF") and pcm[8:12] == b"WAVE":
            return CloudTtsService._normalize_wav_header(pcm)
        sample_rate = int(sample_rate or 24000)
        channels = max(1, int(channels or 1))
        bits_per_sample = 16
        block_align = channels * bits_per_sample // 8
        byte_rate = sample_rate * block_align
        data_len = len(pcm)
        header = (
            b"RIFF"
            + struct.pack("<I", 36 + data_len)
            + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits_per_sample)
            + b"data"
            + struct.pack("<I", data_len)
        )
        return header + pcm

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

    @staticmethod
    def _prewarm_min_interval_seconds() -> float:
        try:
            return max(0.0, min(600.0, float(os.getenv("JACHIN_TTS_PREWARM_MIN_INTERVAL_SEC", "120"))))
        except Exception:
            return 120.0

    @staticmethod
    def _ws_open_warn_ms() -> int:
        try:
            return max(100, int(os.getenv("JACHIN_TTS_WS_OPEN_WARN_MS", "500")))
        except Exception:
            return 500

    @staticmethod
    def _first_audio_warn_ms() -> int:
        try:
            return max(300, int(os.getenv("JACHIN_TTS_FIRST_AUDIO_WARN_MS", "1500")))
        except Exception:
            return 1500

    @staticmethod
    def _first_audio_severe_ms() -> int:
        try:
            return max(1000, int(os.getenv("JACHIN_TTS_FIRST_AUDIO_SEVERE_MS", "3000")))
        except Exception:
            return 3000

    def _log_first_audio_latency(
        self,
        first_packet_ms: int,
        *,
        model: str,
        kind: str | None,
        session_id: str | None,
        text_len: int,
    ) -> None:
        warn_ms = self._first_audio_warn_ms()
        severe_ms = self._first_audio_severe_ms()
        if first_packet_ms > severe_ms:
            logger.warning(
                "DashScope TTS first audio severely slow first_audio_ms=%s severe_ms=%s model=%s kind=%s session_id=%s text_len=%s",
                first_packet_ms,
                severe_ms,
                model,
                kind or "",
                session_id or "",
                text_len,
            )
        elif first_packet_ms > warn_ms:
            logger.warning(
                "DashScope TTS first audio slow first_audio_ms=%s warn_ms=%s model=%s kind=%s session_id=%s text_len=%s",
                first_packet_ms,
                warn_ms,
                model,
                kind or "",
                session_id or "",
                text_len,
            )
