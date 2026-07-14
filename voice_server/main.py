from __future__ import annotations

import logging
import json
import base64
import asyncio
import io
import os
import struct
import time
import wave
import socket
import socketserver
import threading
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from config import load_config
from services.sv_service import SvService


class TtsRequest(BaseModel):
    text: str
    voice: Optional[str] = None
    session_id: Optional[str] = None
    speed: Optional[float] = None
    kind: Optional[str] = None


class CancelRequest(BaseModel):
    session_id: Optional[str] = None


class WarmAudioRequest(BaseModel):
    stt: bool = False
    tts: bool = False
    sv: bool = False
    reason: Optional[str] = None


cfg = load_config()
logging.basicConfig(level=getattr(logging, cfg.log_level.upper(), logging.INFO))
logger = logging.getLogger("jachin.voice_server")


def _make_stt_service():
    if cfg.stt_backend == "cloud":
        from services.cloud_stt_service import CloudSttService

        logger.info(
            "JVS STT backend=cloud model=%s realtime=%s hotword=%s file=%s vocab=%s base=%s",
            cfg.stt_model,
            cfg.stt_realtime_model,
            cfg.stt_hotword_model,
            cfg.stt_file_model,
            "set" if cfg.stt_vocabulary_id else "unset",
            cfg.dashscope_api_base,
        )
        return CloudSttService(
            api_key=cfg.dashscope_api_key,
            api_base=cfg.dashscope_api_base,
            ws_api_base=cfg.dashscope_ws_api_base,
            model=cfg.stt_model,
            realtime_model=cfg.stt_realtime_model,
            hotword_model=cfg.stt_hotword_model,
            file_model=cfg.stt_file_model,
            vocabulary_id=cfg.stt_vocabulary_id,
            vocabulary_prefix=cfg.stt_vocabulary_prefix,
            auto_sync_vocabulary=cfg.stt_auto_sync_vocabulary,
            workspace=cfg.dashscope_workspace_id,
            language=cfg.stt_language,
        )
    from services.stt_service import SttService

    logger.info("JVS STT backend=local model_dir=%s", cfg.stt_dir)
    return SttService(cfg.stt_dir)


def _make_local_stt_service():
    from services.stt_service import SttService

    logger.info("JVS local STT fallback model_dir=%s", cfg.stt_dir)
    return SttService(cfg.stt_dir)


def _stt_fallback_enabled() -> bool:
    raw = os.getenv("JACHIN_STT_LOCAL_FALLBACK", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _make_tts_service():
    if cfg.tts_backend == "cloud":
        from services.cloud_tts_service import CloudTtsService

        logger.info(
            "JVS TTS backend=cloud model=%s fast=%s voice=%s base=%s",
            cfg.tts_model,
            cfg.tts_fast_model,
            cfg.tts_cloud_voice,
            cfg.dashscope_http_api_base,
        )
        return CloudTtsService(
            api_key=cfg.dashscope_tts_api_key,
            http_api_base=cfg.dashscope_http_api_base,
            model=cfg.tts_model,
            fast_model=cfg.tts_fast_model,
            default_voice=cfg.tts_cloud_voice,
            default_speed=cfg.tts_speed,
            audio_format=cfg.tts_format,
            sample_rate=cfg.tts_sample_rate,
        )
    from services.tts_service import TtsService

    logger.info("JVS TTS backend=local model_dir=%s", cfg.tts_dir)
    return TtsService(cfg.tts_dir, default_voice=cfg.tts_voice, default_speed=cfg.tts_speed)


stt_service = _make_stt_service()
local_stt_fallback_service = (
    _make_local_stt_service()
    if cfg.stt_backend == "cloud" and _stt_fallback_enabled()
    else None
)
tts_service = _make_tts_service()
sv_service = SvService(cfg.sv_dir)


def _stt_http_timeout_seconds() -> float:
    raw = os.getenv("JACHIN_JVS_STT_HTTP_TIMEOUT_SEC", "").strip()
    try:
        return max(3.0, float(raw or "18"))
    except ValueError:
        return 18.0


def _stt_cloud_soft_timeout_seconds(hard_timeout_sec: float) -> float:
    raw = os.getenv("JACHIN_STT_CLOUD_SOFT_TIMEOUT_SEC", "").strip()
    try:
        value = float(raw or "7")
    except ValueError:
        value = 7.0
    # Leave room for the local fallback before the HTTP/client hard timeout.
    return max(1.0, min(value, max(1.0, hard_timeout_sec - 2.0)))


def _stt_fallback_grace_seconds() -> float:
    raw = os.getenv("JACHIN_STT_FALLBACK_GRACE_SEC", "").strip()
    try:
        value = float(raw or "8")
    except ValueError:
        value = 8.0
    return max(0.0, min(value, 12.0))


def _stt_stream_final_timeout_seconds() -> float:
    raw = os.getenv("JACHIN_STT_STREAM_FINAL_TIMEOUT_SEC", "").strip()
    try:
        value = float(raw or "1.2")
    except ValueError:
        value = 1.2
    return max(0.8, min(value, 1.5))

# Raw TCP STT IPC frame types (lower framing overhead vs HTTP/WebSocket).
_STT_TCP_START = 0x01
_STT_TCP_CHUNK = 0x02
_STT_TCP_FINALIZE = 0x03
_STT_TCP_RESET = 0x04
_STT_TCP_PING = 0x05
_STT_TCP_READY = 0x65
_STT_TCP_PARTIAL = 0x66
_STT_TCP_FINAL = 0x67
_STT_TCP_ACK = 0x68
_STT_TCP_ERROR = 0x7F

_stt_tcp_server: socketserver.ThreadingTCPServer | None = None
_stt_tcp_thread: threading.Thread | None = None


def _warmup_stt_engine() -> None:
    if stt_service.ready:
        logger.info("Preloading %s STT engine...", getattr(stt_service, "model_name", "unknown"))
        stt_service._load_engine()
    else:
        logger.warning("STT backend not ready: %s", getattr(stt_service, "model_path", "unknown"))
    if local_stt_fallback_service is not None:
        if local_stt_fallback_service.ready:
            logger.info(
                "Preloading %s local fallback STT engine...",
                getattr(local_stt_fallback_service, "model_name", "unknown"),
            )
            local_stt_fallback_service._load_engine()
        else:
            logger.warning(
                "Local fallback STT not ready: %s",
                getattr(local_stt_fallback_service, "model_path", "unknown"),
            )


def _warmup_tts_engine(reason: str = "startup") -> dict[str, Any]:
    if tts_service.ready:
        logger.info("Preloading %s TTS engine...", getattr(tts_service, "model_name", "unknown"))
        loaded = tts_service._load_engine()
        if loaded and cfg.tts_backend == "cloud" and hasattr(tts_service, "prewarm_stream"):
            enabled = os.getenv("JACHIN_TTS_PREWARM_STREAM", "1").strip().lower() not in {"0", "false", "no", "off"}
            if not enabled:
                return {"ok": True, "status": "disabled"}
            return tts_service.prewarm_stream(reason=reason)
        if loaded and cfg.tts_backend != "cloud":
            # Local fallback only: avoid spending cloud TTS quota during warmup.
            result = tts_service.synthesize("\u4f60\u597d\u3002", voice=cfg.tts_voice)
            logger.info(
                "Local TTS warmed (voice=%s, sample_rate=%s, duration_ms=%s)",
                cfg.tts_voice,
                result.sample_rate,
                result.duration_ms,
            )
            return {"ok": True, "status": "local_warmed", "sample_rate": result.sample_rate, "duration_ms": result.duration_ms}
        return {"ok": bool(loaded), "status": "loaded" if loaded else "load_failed"}
    else:
        logger.warning("TTS backend not ready: %s", getattr(tts_service, "model_path", "unknown"))
        return {"ok": False, "status": "not_ready", "error": getattr(tts_service, "model_path", "unknown")}


def _warmup_sv_engine() -> None:
    sv_service.warmup()
    logger.info(
        "SV service ready=%s (dir=%s, backend=%s, load_error=%s)",
        sv_service.ready,
        cfg.sv_dir,
        sv_service.backend,
        sv_service.load_error,
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    threading.Thread(target=_warmup_stt_engine, name="jvs-warmup-stt", daemon=True).start()
    threading.Thread(target=_warmup_tts_engine, name="jvs-warmup-tts", daemon=True).start()
    threading.Thread(target=_warmup_sv_engine, name="jvs-warmup-sv", daemon=True).start()
    _start_stt_tcp_server()
    try:
        yield
    finally:
        _stop_stt_tcp_server()


app = FastAPI(title="Jachin Voice Server", version="0.1.0", lifespan=lifespan)

# Allow browser preflight from the Tauri/Vite dev page to the local voice server.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    available_voices = tts_service.list_voices()
    current_tts_voice = cfg.tts_cloud_voice if cfg.tts_backend == "cloud" else cfg.tts_voice
    tts_last_prewarm = getattr(tts_service, "_last_prewarm_trace", {})
    tts_prewarm_checks = tts_last_prewarm.get("checks", {}) if isinstance(tts_last_prewarm, dict) else {}
    return {
        "ok": True,
        "stt_ready": stt_service.ready,
        "tts_ready": tts_service.ready,
        "sv_ready": sv_service.ready,
        "stt_model": stt_service.model_name,
        "tts_model": getattr(tts_service, "model_name", "unknown"),
        "tts_format": getattr(tts_service, "audio_format", cfg.tts_format),
        "tts_sample_rate": getattr(tts_service, "sample_rate", cfg.tts_sample_rate),
        "tts_http_api_base": cfg.dashscope_http_api_base,
        "tts_connection_reuse_supported": bool(getattr(tts_service, "_pool_enabled", lambda: False)()),
        "tts_pool_ready": bool(getattr(tts_service, "_pool", None) is not None),
        "tts_pool_trace": getattr(tts_service, "_pool_ready_trace", {}),
        "tts_last_prewarm": tts_last_prewarm,
        "tts_stream_cue_ready": bool((tts_prewarm_checks.get("cue") or {}).get("ok")),
        "tts_stream_content_ready": bool((tts_prewarm_checks.get("content") or {}).get("ok")),
        "stt_backend": cfg.stt_backend,
        "stt_http_timeout_sec": _stt_http_timeout_seconds(),
        "stt_cloud_soft_timeout_sec": _stt_cloud_soft_timeout_seconds(_stt_http_timeout_seconds()),
        "stt_local_fallback_enabled": local_stt_fallback_service is not None,
        "stt_local_fallback_ready": bool(local_stt_fallback_service and local_stt_fallback_service.ready),
        "stt_local_fallback_model": getattr(local_stt_fallback_service, "model_name", ""),
        "stt_local_fallback_load_error": getattr(local_stt_fallback_service, "load_error", None),
        "stt_cloud_realtime_stream_enabled": _stt_realtime_stream_enabled(),
        "stt_cloud_realtime_stream_supported": hasattr(stt_service, "start_stream_session"),
        "stt_stream_mode": "cloud_realtime" if _stt_realtime_stream_enabled() and hasattr(stt_service, "start_stream_session") else "batch_incremental",
        "tts_backend": cfg.tts_backend,
        "stt_realtime_model": cfg.stt_realtime_model,
        "stt_hotword_model": cfg.stt_hotword_model,
        "stt_file_model": cfg.stt_file_model,
        "stt_vocabulary_id_configured": bool(cfg.stt_vocabulary_id),
        "stt_auto_sync_vocabulary": cfg.stt_auto_sync_vocabulary,
        "tts_fast_model": cfg.tts_fast_model,
        "sv_model": sv_service.backend,
        "sv_load_error": sv_service.load_error,
        "tts_voice": current_tts_voice,
        "tts_speed": cfg.tts_speed,
        "tts_cue_style_index": getattr(tts_service, "_cue_style_index", None),
        "tts_voice_exists": tts_service.has_voice(current_tts_voice),
        "tts_voice_count": len(available_voices),
        "model_root": str(cfg.model_root),
        "stt_tcp_port": cfg.stt_tcp_port,
        "version": "0.1.0",
    }


def _parse_centroid(centroid: str) -> list[float]:
    try:
        raw = json.loads(centroid)
        if not isinstance(raw, list):
            raise ValueError("centroid must be list")
        out = [float(v) for v in raw]
        if not out:
            raise ValueError("centroid empty")
        return out
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid centroid: {e}")


def _pcm16le_to_wav_bytes(pcm_bytes: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    if not pcm_bytes:
        return b""
    with io.BytesIO() as bio:
        with wave.open(bio, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)  # pcm16
            wf.setframerate(sample_rate)
            wf.writeframes(pcm_bytes)
        return bio.getvalue()


async def _stt_transcribe_pcm_async(pcm_bytes: bytes, sample_rate: int = 16000) -> "SttResult":
    wav_bytes = _pcm16le_to_wav_bytes(pcm_bytes, sample_rate=sample_rate, channels=1)
    return await _transcribe_stt_with_local_fallback(wav_bytes, stage="stream_partial")


def _stt_transcribe_pcm_sync(pcm_bytes: bytes, sample_rate: int = 16000) -> "SttResult":
    wav_bytes = _pcm16le_to_wav_bytes(pcm_bytes, sample_rate=sample_rate, channels=1)
    result = stt_service.transcribe(wav_bytes)
    if _stt_result_needs_local_fallback(result):
        fallback = _transcribe_with_local_fallback_sync(wav_bytes, reason="primary_error_sync")
        if fallback is not None:
            return fallback
    return result


def _stt_transcribe_pcm_local_fallback_sync(
    pcm_bytes: bytes,
    sample_rate: int = 16000,
    reason: str = "stream_final_timeout",
    cloud_elapsed_ms: int | None = None,
) -> Any | None:
    wav_bytes = _pcm16le_to_wav_bytes(pcm_bytes, sample_rate=sample_rate, channels=1)
    return _transcribe_with_local_fallback_sync(wav_bytes, reason=reason, cloud_elapsed_ms=cloud_elapsed_ms)


async def _stt_transcribe_pcm_local_fallback_async(
    pcm_bytes: bytes,
    sample_rate: int = 16000,
    reason: str = "stream_final_timeout",
    cloud_elapsed_ms: int | None = None,
) -> Any | None:
    return await asyncio.to_thread(
        _stt_transcribe_pcm_local_fallback_sync,
        pcm_bytes,
        sample_rate,
        reason,
        cloud_elapsed_ms,
    )


def _stt_result_needs_local_fallback(result: Any) -> bool:
    text = str(getattr(result, "text", "") or "").strip()
    if text.startswith("[STT error]") or text.startswith("\u3010STT\u9519\u8bef\u3011"):
        return True
    if not text and float(getattr(result, "confidence", 0.0) or 0.0) <= 0.0:
        return True
    return False


def _stt_stream_result_needs_local_fallback(result: Any) -> bool:
    if _stt_result_needs_local_fallback(result):
        return True
    understanding = getattr(result, "understanding", {}) or {}
    if not isinstance(understanding, dict):
        return False
    if understanding.get("streaming_mode") != "dashscope_recognition_start_send_audio_frame":
        return False
    return not bool(understanding.get("stream_finalized"))


def _mark_local_fallback_result(result: Any, reason: str, cloud_elapsed_ms: int | None = None) -> Any:
    try:
        result.backend = f"{result.backend}+fallback_from_cloud"
        result.understanding = {
            **(getattr(result, "understanding", {}) or {}),
            "stt_fallback": {
                "used": True,
                "reason": reason,
                "cloud_elapsed_ms": cloud_elapsed_ms,
                "fallback_model": getattr(local_stt_fallback_service, "model_name", ""),
            },
        }
    except Exception:
        pass
    return result


def _append_understanding_event(result: Any, key: str, event: dict[str, Any]) -> Any:
    try:
        understanding = dict(getattr(result, "understanding", {}) or {})
        events = list(understanding.get(key) or [])
        events.append(event)
        understanding[key] = events
        result.understanding = understanding
    except Exception:
        pass
    return result


def _attach_understanding_events(result: Any, key: str, events: list[dict[str, Any]]) -> Any:
    try:
        understanding = dict(getattr(result, "understanding", {}) or {})
        existing = list(understanding.get(key) or [])
        understanding[key] = existing + list(events)
        result.understanding = understanding
    except Exception:
        pass
    return result


def _orchestration_event(event_stage: str, started: float, **payload: Any) -> dict[str, Any]:
    if "stage" in payload:
        payload["request_stage"] = payload.pop("stage")
    event = {
        "stage": event_stage,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
    }
    event.update({k: v for k, v in payload.items() if v is not None})
    return event


def _stt_realtime_stream_enabled() -> bool:
    if cfg.stt_backend != "cloud":
        return False
    raw = os.getenv("JACHIN_STT_CLOUD_REALTIME_STREAM", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _start_realtime_stt_session(sample_rate: int, session_id: str | None) -> Any | None:
    if not _stt_realtime_stream_enabled():
        return None
    starter = getattr(stt_service, "start_stream_session", None)
    if starter is None:
        return None
    try:
        session = starter(sample_rate=sample_rate, session_id=session_id)
        logger.info(
            "STT cloud realtime stream started session_id=%s sample_rate=%s",
            session_id or "",
            sample_rate,
        )
        return session
    except Exception as e:
        logger.warning(
            "STT cloud realtime stream unavailable session_id=%s sample_rate=%s error=%s; falling back to batch stream",
            session_id or "",
            sample_rate,
            e,
        )
        return None


def _stt_stream_event_payload(event: dict[str, Any], session_id: str | None) -> dict[str, Any] | None:
    event_type = str(event.get("type") or "").strip().lower()
    text = str(event.get("text") or "").strip()
    if event_type not in {"partial", "final"} or not text:
        return None
    return {
        "type": event_type,
        "session_id": session_id,
        "text": text,
        "raw_text": str(event.get("raw_text") or text),
        "user_message": "",
        "user_message_source": "",
        "reply_plan": {},
        "confidence": 0.92,
        "duration_ms": 0,
        "language": getattr(stt_service, "language", "") or "auto",
        "backend": f"dashscope:{getattr(stt_service, 'hotword_model', getattr(stt_service, 'model_name', 'cloud'))}:stream",
        "hotword_count": 0,
        "hotword_status": "streaming",
        "hotword_sources": [],
        "understanding": {"streaming_mode": "dashscope_recognition_start_send_audio_frame"},
    }


def _observe_late_cloud_result(task: asyncio.Task[Any], started: float, stage: str, audio_bytes_len: int) -> None:
    async def _observe() -> None:
        try:
            result = await task
            logger.warning(
                "STT cloud_late_result stage=%s bytes=%s elapsed_ms=%s text_len=%s backend=%s",
                stage,
                audio_bytes_len,
                int((time.monotonic() - started) * 1000),
                len(str(getattr(result, "text", "") or "")),
                str(getattr(result, "backend", "") or ""),
            )
        except asyncio.CancelledError:
            logger.warning(
                "STT cloud_late_cancelled stage=%s bytes=%s elapsed_ms=%s",
                stage,
                audio_bytes_len,
                int((time.monotonic() - started) * 1000),
            )
        except Exception as e:
            logger.warning(
                "STT cloud_late_exception stage=%s bytes=%s elapsed_ms=%s error=%r",
                stage,
                audio_bytes_len,
                int((time.monotonic() - started) * 1000),
                e,
            )

    asyncio.create_task(_observe())


def _transcribe_with_local_fallback_sync(audio_bytes: bytes, reason: str, cloud_elapsed_ms: int | None = None) -> Any | None:
    if local_stt_fallback_service is None:
        return None
    if not local_stt_fallback_service.ready:
        logger.warning(
            "Local STT fallback unavailable reason=%s model_path=%s",
            reason,
            getattr(local_stt_fallback_service, "model_path", "unknown"),
        )
        return None
    started = time.monotonic()
    logger.warning(
        "Local STT fallback start reason=%s cloud_elapsed_ms=%s bytes=%s model=%s",
        reason,
        cloud_elapsed_ms,
        len(audio_bytes or b""),
        getattr(local_stt_fallback_service, "model_name", "unknown"),
    )
    try:
        fallback = local_stt_fallback_service.transcribe(audio_bytes)
    except Exception as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.exception(
            "Local STT fallback failed reason=%s cloud_elapsed_ms=%s fallback_elapsed_ms=%s error=%s",
            reason,
            cloud_elapsed_ms,
            elapsed_ms,
            e,
        )
        return None
    elapsed_ms = int((time.monotonic() - started) * 1000)
    logger.warning(
        "Local STT fallback used reason=%s cloud_elapsed_ms=%s fallback_elapsed_ms=%s text_len=%s",
        reason,
        cloud_elapsed_ms,
        elapsed_ms,
        len(str(getattr(fallback, "text", "") or "")),
    )
    return _mark_local_fallback_result(fallback, reason=reason, cloud_elapsed_ms=cloud_elapsed_ms)


async def _transcribe_stt_with_local_fallback(
    audio_bytes: bytes,
    *,
    stage: str,
    timeout_sec: float | None = None,
) -> Any:
    timeout_sec = timeout_sec or _stt_http_timeout_seconds()
    started = time.monotonic()
    hard_deadline = started + timeout_sec
    orchestration_events: list[dict[str, Any]] = [
        _orchestration_event(
            "cloud_start",
            started,
            request_stage=stage,
            bytes=len(audio_bytes),
            timeout_sec=timeout_sec,
            local_fallback_available=local_stt_fallback_service is not None,
        )
    ]
    cloud_task = asyncio.create_task(asyncio.to_thread(stt_service.transcribe, audio_bytes))

    async def _local(reason: str, cloud_elapsed_ms: int | None = None) -> Any | None:
        return await asyncio.to_thread(
            _transcribe_with_local_fallback_sync,
            audio_bytes,
            reason,
            cloud_elapsed_ms,
        )

    def _remaining() -> float:
        return max(0.0, hard_deadline - time.monotonic())

    try:
        soft_timeout = _stt_cloud_soft_timeout_seconds(timeout_sec)
        if local_stt_fallback_service is None:
            soft_timeout = timeout_sec
        orchestration_events.append(
            _orchestration_event(
                "cloud_wait_start",
                started,
                soft_timeout_sec=soft_timeout,
                hard_timeout_sec=timeout_sec,
            )
        )

        done, _pending = await asyncio.wait(
            {cloud_task},
            timeout=soft_timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if cloud_task in done:
            try:
                result = cloud_task.result()
            except Exception:
                cloud_elapsed_ms = int((time.monotonic() - started) * 1000)
                orchestration_events.append(
                    _orchestration_event("cloud_exception", started, elapsed_ms=cloud_elapsed_ms)
                )
                logger.exception(
                    "Primary STT failed stage=%s bytes=%s elapsed_ms=%s; trying local fallback",
                    stage,
                    len(audio_bytes),
                    cloud_elapsed_ms,
                )
                fallback = await _local("cloud_exception", cloud_elapsed_ms)
                if fallback is not None:
                    orchestration_events.append(
                        _orchestration_event(
                            "fallback_result",
                            started,
                            reason="cloud_exception",
                            text_len=len(str(getattr(fallback, "text", "") or "")),
                            backend=str(getattr(fallback, "backend", "") or ""),
                        )
                    )
                    _attach_understanding_events(fallback, "stt_orchestration", orchestration_events)
                    return fallback
                raise

            cloud_elapsed_ms = int((time.monotonic() - started) * 1000)
            orchestration_events.append(
                _orchestration_event(
                    "cloud_result",
                    started,
                    elapsed_ms=cloud_elapsed_ms,
                    text_len=len(str(getattr(result, "text", "") or "")),
                    backend=str(getattr(result, "backend", "") or ""),
                )
            )
            if _stt_result_needs_local_fallback(result):
                logger.warning(
                    "Primary STT returned unusable result stage=%s bytes=%s elapsed_ms=%s text=%r; trying local fallback",
                    stage,
                    len(audio_bytes),
                    cloud_elapsed_ms,
                    str(getattr(result, "text", "") or "")[:160],
                )
                fallback = await _local("cloud_error_result", cloud_elapsed_ms)
                if fallback is not None:
                    orchestration_events.append(
                        _orchestration_event(
                            "fallback_result",
                            started,
                            reason="cloud_error_result",
                            text_len=len(str(getattr(fallback, "text", "") or "")),
                            backend=str(getattr(fallback, "backend", "") or ""),
                        )
                    )
                    _attach_understanding_events(fallback, "stt_orchestration", orchestration_events)
                    return fallback
            _attach_understanding_events(result, "stt_orchestration", orchestration_events)
            return result

        cloud_elapsed_ms = int((time.monotonic() - started) * 1000)
        orchestration_events.append(
            _orchestration_event(
                "cloud_soft_timeout",
                started,
                elapsed_ms=cloud_elapsed_ms,
                soft_timeout_sec=soft_timeout,
                hard_timeout_sec=timeout_sec,
            )
        )
        logger.warning(
            "Primary STT soft timeout stage=%s bytes=%s soft_timeout_sec=%.1f hard_timeout_sec=%.1f elapsed_ms=%s; starting local fallback",
            stage,
            len(audio_bytes),
            soft_timeout,
            timeout_sec,
            cloud_elapsed_ms,
        )
        fallback_task = asyncio.create_task(_local("cloud_soft_timeout", cloud_elapsed_ms))
        orchestration_events.append(_orchestration_event("fallback_start", started, reason="cloud_soft_timeout"))
        pending: set[asyncio.Task[Any]] = {cloud_task, fallback_task}
        fallback_exhausted = False

        while pending and _remaining() > 0:
            done, pending = await asyncio.wait(
                pending,
                timeout=_remaining(),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                break
            for task in done:
                if task is fallback_task:
                    try:
                        fallback = task.result()
                    except Exception as e:
                        fallback = None
                        fallback_exhausted = True
                        orchestration_events.append(
                            _orchestration_event("fallback_exception", started, error=type(e).__name__)
                        )
                        logger.exception(
                            "Local STT fallback task failed stage=%s bytes=%s elapsed_ms=%s",
                            stage,
                            len(audio_bytes),
                            int((time.monotonic() - started) * 1000),
                        )
                    if fallback is not None:
                        orchestration_events.append(
                            _orchestration_event(
                                "fallback_result",
                                started,
                                reason="cloud_soft_timeout",
                                text_len=len(str(getattr(fallback, "text", "") or "")),
                                backend=str(getattr(fallback, "backend", "") or ""),
                                cloud_still_running=not cloud_task.done(),
                            )
                        )
                        if not cloud_task.done():
                            orchestration_events.append(
                                _orchestration_event(
                                    "cloud_late_observer_started",
                                    started,
                                    reason="fallback_returned_first",
                                )
                            )
                            _observe_late_cloud_result(cloud_task, started, stage, len(audio_bytes))
                        _attach_understanding_events(fallback, "stt_orchestration", orchestration_events)
                        return fallback
                    fallback_exhausted = True
                    orchestration_events.append(
                        _orchestration_event("fallback_unavailable", started, reason="local_returned_none")
                    )
                    continue
                if task is cloud_task:
                    try:
                        result = task.result()
                    except Exception:
                        orchestration_events.append(
                            _orchestration_event("cloud_exception_after_soft_timeout", started)
                        )
                        logger.exception(
                            "Primary STT failed after soft timeout stage=%s bytes=%s elapsed_ms=%s",
                            stage,
                            len(audio_bytes),
                            int((time.monotonic() - started) * 1000),
                        )
                        if not fallback_exhausted and fallback_task in pending:
                            continue
                        raise
                    if _stt_result_needs_local_fallback(result):
                        orchestration_events.append(
                            _orchestration_event(
                                "cloud_error_result_after_soft_timeout",
                                started,
                                text_len=len(str(getattr(result, "text", "") or "")),
                            )
                        )
                        logger.warning(
                            "Primary STT returned unusable result after soft timeout stage=%s bytes=%s elapsed_ms=%s text=%r",
                            stage,
                            len(audio_bytes),
                            int((time.monotonic() - started) * 1000),
                            str(getattr(result, "text", "") or "")[:160],
                        )
                        if not fallback_exhausted and fallback_task in pending:
                            continue
                    orchestration_events.append(
                        _orchestration_event(
                            "cloud_late_result",
                            started,
                            text_len=len(str(getattr(result, "text", "") or "")),
                            backend=str(getattr(result, "backend", "") or ""),
                        )
                    )
                    _attach_understanding_events(result, "stt_orchestration", orchestration_events)
                    return result

        fallback_grace_sec = _stt_fallback_grace_seconds()
        if fallback_task in pending and not fallback_exhausted and fallback_grace_sec > 0:
            orchestration_events.append(
                _orchestration_event(
                    "fallback_grace_wait_start",
                    started,
                    hard_timeout_sec=timeout_sec,
                    fallback_grace_sec=fallback_grace_sec,
                )
            )
            logger.warning(
                "STT hard timeout reached but local fallback still running stage=%s bytes=%s hard_timeout_sec=%.1f fallback_grace_sec=%.1f elapsed_ms=%s",
                stage,
                len(audio_bytes),
                timeout_sec,
                fallback_grace_sec,
                int((time.monotonic() - started) * 1000),
            )
            try:
                fallback = await asyncio.wait_for(fallback_task, timeout=fallback_grace_sec)
            except asyncio.TimeoutError:
                fallback = None
                orchestration_events.append(
                    _orchestration_event("fallback_grace_timeout", started, fallback_grace_sec=fallback_grace_sec)
                )
            except Exception as e:
                fallback = None
                orchestration_events.append(
                    _orchestration_event("fallback_exception", started, error=type(e).__name__)
                )
                logger.exception(
                    "Local STT fallback failed during grace wait stage=%s bytes=%s elapsed_ms=%s",
                    stage,
                    len(audio_bytes),
                    int((time.monotonic() - started) * 1000),
                )
            if fallback is not None:
                orchestration_events.append(
                    _orchestration_event(
                        "fallback_result_after_grace",
                        started,
                        reason="cloud_soft_timeout",
                        text_len=len(str(getattr(fallback, "text", "") or "")),
                        backend=str(getattr(fallback, "backend", "") or ""),
                        cloud_still_running=not cloud_task.done(),
                    )
                )
                if not cloud_task.done():
                    orchestration_events.append(
                        _orchestration_event("cloud_late_observer_started", started, reason="fallback_grace_returned")
                    )
                    _observe_late_cloud_result(cloud_task, started, stage, len(audio_bytes))
                _attach_understanding_events(fallback, "stt_orchestration", orchestration_events)
                return fallback

        for task in pending:
            if not task.done():
                task.cancel()
        orchestration_events.append(
            _orchestration_event(
                "stt_timeout",
                started,
                timeout_sec=timeout_sec,
                fallback_grace_sec=fallback_grace_sec,
            )
        )
        logger.warning(
            "STT timed out stage=%s bytes=%s timeout_sec=%.1f fallback_grace_sec=%.1f elapsed_ms=%s fallback_started=%s fallback_exhausted=%s",
            stage,
            len(audio_bytes),
            timeout_sec,
            fallback_grace_sec,
            int((time.monotonic() - started) * 1000),
            True,
            fallback_exhausted,
        )
        raise asyncio.TimeoutError()
    except asyncio.CancelledError:
        cloud_task.cancel()
        raise


def _pack_tcp_frame(msg_type: int, payload: bytes = b"") -> bytes:
    return struct.pack("<BI", int(msg_type) & 0xFF, len(payload)) + payload


def _stt_result_payload(result: "SttResult") -> dict:
    return {
        "text": result.text,
        "raw_text": result.raw_text,
        "user_message": result.user_message,
        "user_message_source": result.user_message_source,
        "reply_plan": result.reply_plan,
        "confidence": result.confidence,
        "duration_ms": result.duration_ms,
        "language": result.language,
        "backend": result.backend,
        "hotword_count": result.hotword_count,
        "hotword_status": result.hotword_status,
        "hotword_sources": list(result.hotword_sources),
        "understanding": result.understanding,
    }


class _SttTcpHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        sock = self.request
        sock.settimeout(1.0)
        if not stt_service.ready:
            self._send_json(_STT_TCP_ERROR, {"type": "error", "message": f"STT model not ready: {stt_service.model_path}"})
            return

        sample_rate = 16000
        session_id: str | None = None
        pcm_buffer = bytearray()
        partial_text = ""
        last_infer_at = 0.0
        last_infer_len = 0
        realtime_session: Any | None = None
        min_new_bytes_for_partial = 3200 * 2
        min_infer_interval_sec = 0.30
        self._send_json(_STT_TCP_READY, {"type": "ready", "sample_rate": sample_rate})

        def drain_realtime_events() -> None:
            if realtime_session is None:
                return
            try:
                for event in realtime_session.poll_events():
                    payload = _stt_stream_event_payload(event, session_id)
                    if not payload:
                        continue
                    if payload.get("type") == "final":
                        self._send_json(_STT_TCP_FINAL, payload)
                    else:
                        self._send_json(_STT_TCP_PARTIAL, payload)
            except Exception as e:
                logger.warning("STT TCP realtime event drain failed session_id=%s error=%s", session_id or "", e)

        def maybe_partial(force: bool = False) -> None:
            nonlocal partial_text, last_infer_at, last_infer_len
            if not pcm_buffer:
                return
            now = time.monotonic()
            new_bytes = len(pcm_buffer) - last_infer_len
            if not force:
                if new_bytes < min_new_bytes_for_partial:
                    return
                if (now - last_infer_at) < min_infer_interval_sec:
                    return
            result = _stt_transcribe_pcm_sync(bytes(pcm_buffer), sample_rate=sample_rate)
            last_infer_at = now
            last_infer_len = len(pcm_buffer)
            if result.text and result.text != partial_text:
                partial_text = result.text
                self._send_json(
                    _STT_TCP_PARTIAL,
                    {"type": "partial", "session_id": session_id, **_stt_result_payload(result)},
                )

        try:
            while True:
                header = self._recv_exact(5)
                if not header:
                    break
                msg_type, payload_len = struct.unpack("<BI", header)
                payload = self._recv_exact(payload_len) if payload_len > 0 else b""
                if payload_len > 0 and payload is None:
                    break
                if msg_type == _STT_TCP_START:
                    meta = self._safe_json(payload)
                    sample_rate = int(meta.get("sample_rate") or sample_rate)
                    sample_rate = sample_rate if sample_rate > 0 else 16000
                    session_id = str(meta.get("session_id") or "").strip() or None
                    pcm_buffer.clear()
                    partial_text = ""
                    last_infer_at = 0.0
                    last_infer_len = 0
                    realtime_session = _start_realtime_stt_session(sample_rate, session_id)
                    self._send_json(_STT_TCP_ACK, {"type": "ack_start", "session_id": session_id, "sample_rate": sample_rate})
                    continue
                if msg_type == _STT_TCP_RESET:
                    pcm_buffer.clear()
                    partial_text = ""
                    last_infer_at = 0.0
                    last_infer_len = 0
                    realtime_session = _start_realtime_stt_session(sample_rate, session_id)
                    self._send_json(_STT_TCP_ACK, {"type": "ack_reset", "session_id": session_id})
                    continue
                if msg_type == _STT_TCP_PING:
                    self._send_json(_STT_TCP_ACK, {"type": "pong", "session_id": session_id})
                    continue
                if msg_type == _STT_TCP_CHUNK:
                    if payload:
                        pcm_buffer.extend(payload)
                        if realtime_session is not None:
                            realtime_session.push_pcm(payload)
                            drain_realtime_events()
                        else:
                            maybe_partial(force=False)
                    continue
                if msg_type == _STT_TCP_FINALIZE:
                    if realtime_session is not None:
                        stream_started = time.monotonic()
                        final = realtime_session.finish(timeout_sec=_stt_stream_final_timeout_seconds())
                        drain_realtime_events()
                        if _stt_stream_result_needs_local_fallback(final):
                            fallback = _stt_transcribe_pcm_local_fallback_sync(
                                bytes(pcm_buffer),
                                sample_rate=sample_rate,
                                reason="stream_final_timeout_or_partial",
                                cloud_elapsed_ms=int((time.monotonic() - stream_started) * 1000),
                            )
                            if fallback is not None:
                                final = fallback
                    else:
                        maybe_partial(force=True)
                        final = _stt_transcribe_pcm_sync(bytes(pcm_buffer), sample_rate=sample_rate)
                    self._send_json(
                        _STT_TCP_FINAL,
                        {
                            "type": "final",
                            "session_id": session_id,
                            **_stt_result_payload(final),
                            "bytes": len(pcm_buffer),
                        },
                    )
                    break
                self._send_json(_STT_TCP_ERROR, {"type": "error", "message": f"unknown_frame_type:{msg_type}"})
        except Exception as e:
            logger.exception("STT TCP stream failed: %s", e)
            try:
                self._send_json(_STT_TCP_ERROR, {"type": "error", "message": str(e)})
            except Exception:
                pass

    def _send_json(self, msg_type: int, obj: dict) -> None:
        payload = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.request.sendall(_pack_tcp_frame(msg_type, payload))

    def _recv_exact(self, n: int) -> bytes | None:
        if n <= 0:
            return b""
        out = bytearray()
        while len(out) < n:
            try:
                chunk = self.request.recv(n - len(out))
            except socket.timeout:
                continue
            if not chunk:
                return None
            out.extend(chunk)
        return bytes(out)

    @staticmethod
    def _safe_json(raw: bytes) -> dict:
        try:
            val = json.loads(raw.decode("utf-8"))
            return val if isinstance(val, dict) else {}
        except Exception:
            return {}


def _start_stt_tcp_server() -> None:
    global _stt_tcp_server, _stt_tcp_thread
    if _stt_tcp_server is not None:
        return

    class _ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
        daemon_threads = True
        allow_reuse_address = True

    server = _ThreadedServer((cfg.host, cfg.stt_tcp_port), _SttTcpHandler)
    thread = threading.Thread(target=server.serve_forever, name="jvs-stt-tcp", daemon=True)
    thread.start()
    _stt_tcp_server = server
    _stt_tcp_thread = thread
    logger.info("STT raw TCP server listening at tcp://%s:%s", cfg.host, cfg.stt_tcp_port)


def _stop_stt_tcp_server() -> None:
    global _stt_tcp_server, _stt_tcp_thread
    if _stt_tcp_server is None:
        return
    try:
        _stt_tcp_server.shutdown()
        _stt_tcp_server.server_close()
    finally:
        _stt_tcp_server = None
    if _stt_tcp_thread is not None:
        _stt_tcp_thread.join(timeout=1.0)
        _stt_tcp_thread = None


@app.post("/v1/stt/transcribe")
async def stt_transcribe(audio: UploadFile = File(...), session_id: Optional[str] = None) -> dict:
    try:
        raw = await audio.read()
    finally:
        await audio.close()
    if not raw:
        raise HTTPException(status_code=400, detail="empty audio payload")
    if not stt_service.ready:
        raise HTTPException(
            status_code=503,
            detail=f"STT backend not ready: {stt_service.model_path}",
        )
    timeout_sec = _stt_http_timeout_seconds()
    started = time.monotonic()
    try:
        result = await _transcribe_stt_with_local_fallback(
            raw,
            stage="http_transcribe",
            timeout_sec=timeout_sec,
        )
    except asyncio.TimeoutError:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.warning(
            "STT HTTP transcribe timeout without fallback session_id=%s bytes=%s timeout_sec=%.1f elapsed_ms=%s",
            session_id,
            len(raw),
            timeout_sec,
            elapsed_ms,
        )
        raise HTTPException(status_code=504, detail="STT transcribe timeout")
    except Exception as e:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        logger.exception(
            "STT HTTP transcribe failed session_id=%s bytes=%s elapsed_ms=%s",
            session_id,
            len(raw),
            elapsed_ms,
        )
        raise HTTPException(status_code=500, detail=f"STT transcribe failed: {e}")
    return {**_stt_result_payload(result), "session_id": session_id}


@app.post("/v1/stt/transcribe_local")
async def stt_transcribe_local(audio: UploadFile = File(...), session_id: Optional[str] = None) -> dict:
    try:
        raw = await audio.read()
    finally:
        await audio.close()
    if not raw:
        raise HTTPException(status_code=400, detail="empty audio payload")
    local_service = local_stt_fallback_service or (stt_service if cfg.stt_backend != "cloud" else None)
    if local_service is None or not getattr(local_service, "ready", False):
        raise HTTPException(status_code=503, detail="local STT fallback not ready")
    started = time.monotonic()
    try:
        result = await asyncio.to_thread(local_service.transcribe, raw)
        result = _mark_local_fallback_result(
            result,
            reason="stream_miss_local_first",
            cloud_elapsed_ms=None,
        )
        _append_understanding_event(
            result,
            "stt_orchestration",
            _orchestration_event(
                "local_first_result",
                started,
                reason="stream_miss_local_first",
                text_len=len(str(getattr(result, "text", "") or "")),
                backend=str(getattr(result, "backend", "") or ""),
            ),
        )
    except Exception as e:
        logger.exception("STT local transcribe failed session_id=%s bytes=%s", session_id, len(raw))
        raise HTTPException(status_code=500, detail=f"local STT transcribe failed: {e}")
    return {**_stt_result_payload(result), "session_id": session_id}


@app.websocket("/v1/stt/stream")
async def stt_stream(websocket: WebSocket, session_id: Optional[str] = None):
    await websocket.accept()
    if not stt_service.ready:
        await websocket.send_json(
            {
                "type": "error",
                "session_id": session_id,
                "message": f"STT backend not ready: {stt_service.model_path}",
            }
        )
        await websocket.close(code=1011)
        return

    sample_rate = 16000
    pcm_buffer = bytearray()
    partial_text = ""
    last_infer_at = 0.0
    last_infer_len = 0
    realtime_session: Any | None = None
    # 100ms @ 16kHz mono pcm16 => 3200 bytes
    min_new_bytes_for_partial = 3200 * 2
    min_infer_interval_sec = 0.35

    async def maybe_infer_partial(force: bool = False) -> None:
        nonlocal partial_text, last_infer_at, last_infer_len
        if not pcm_buffer:
            return
        now = time.monotonic()
        new_bytes = len(pcm_buffer) - last_infer_len
        if not force:
            if new_bytes < min_new_bytes_for_partial:
                return
            if (now - last_infer_at) < min_infer_interval_sec:
                return
        result = await _stt_transcribe_pcm_async(bytes(pcm_buffer), sample_rate=sample_rate)
        last_infer_at = now
        last_infer_len = len(pcm_buffer)
        if result.text and result.text != partial_text:
            partial_text = result.text
            await websocket.send_json(
                {"type": "partial", "session_id": session_id, **_stt_result_payload(result)}
            )

    async def drain_realtime_events() -> None:
        if realtime_session is None:
            return
        try:
            for event in realtime_session.poll_events():
                payload = _stt_stream_event_payload(event, session_id)
                if payload:
                    await websocket.send_json(payload)
        except Exception as e:
            logger.warning("STT WS realtime event drain failed session_id=%s error=%s", session_id or "", e)

    try:
        await websocket.send_json({"type": "ready", "session_id": session_id, "sample_rate": sample_rate})
        while True:
            message = await websocket.receive()
            text_data = message.get("text")
            bytes_data = message.get("bytes")

            if text_data is not None:
                try:
                    payload = json.loads(text_data)
                except Exception:
                    payload = {}
                msg_type = str(payload.get("type") or "").strip().lower()
                if msg_type == "start":
                    sr = int(payload.get("sample_rate") or sample_rate)
                    sample_rate = sr if sr > 0 else 16000
                    pcm_buffer.clear()
                    partial_text = ""
                    last_infer_at = 0.0
                    last_infer_len = 0
                    realtime_session = _start_realtime_stt_session(sample_rate, session_id)
                    await websocket.send_json({"type": "ack_start", "session_id": session_id, "sample_rate": sample_rate})
                    continue
                if msg_type == "reset":
                    pcm_buffer.clear()
                    partial_text = ""
                    last_infer_at = 0.0
                    last_infer_len = 0
                    realtime_session = _start_realtime_stt_session(sample_rate, session_id)
                    await websocket.send_json({"type": "ack_reset", "session_id": session_id})
                    continue
                if msg_type == "finalize":
                    if realtime_session is not None:
                        stream_started = time.monotonic()
                        final = await asyncio.to_thread(
                            realtime_session.finish,
                            _stt_stream_final_timeout_seconds(),
                        )
                        await drain_realtime_events()
                        if _stt_stream_result_needs_local_fallback(final):
                            fallback = await _stt_transcribe_pcm_local_fallback_async(
                                bytes(pcm_buffer),
                                sample_rate=sample_rate,
                                reason="stream_final_timeout_or_partial",
                                cloud_elapsed_ms=int((time.monotonic() - stream_started) * 1000),
                            )
                            if fallback is not None:
                                final = fallback
                    else:
                        await maybe_infer_partial(force=True)
                        final = await _stt_transcribe_pcm_async(bytes(pcm_buffer), sample_rate=sample_rate)
                    await websocket.send_json(
                        {
                            "type": "final",
                            "session_id": session_id,
                            **_stt_result_payload(final),
                            "bytes": len(pcm_buffer),
                        }
                    )
                    break
                if msg_type == "ping":
                    await websocket.send_json({"type": "pong", "session_id": session_id})
                    continue
                await websocket.send_json({"type": "warn", "message": f"unknown_message_type: {msg_type}"})
                continue

            if bytes_data is not None:
                pcm_buffer.extend(bytes_data)
                if realtime_session is not None:
                    await asyncio.to_thread(realtime_session.push_pcm, bytes_data)
                    await drain_realtime_events()
                else:
                    await maybe_infer_partial(force=False)
                continue

            if message.get("type") == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        logger.info("STT stream client disconnected (session_id=%s)", session_id or "")
    except Exception as e:
        logger.exception("STT stream failed: %s", e)
        try:
            await websocket.send_json({"type": "error", "message": str(e), "session_id": session_id})
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@app.post("/v1/tts/synthesize")
def tts_synthesize(req: TtsRequest) -> Response:
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text is empty")
    if not tts_service.ready:
        raise HTTPException(
            status_code=503,
            detail=f"TTS backend not ready: {tts_service.model_path}",
        )
    try:
        result = tts_service.synthesize(req.text, voice=req.voice, session_id=req.session_id, speed=req.speed, kind=req.kind)
    except Exception as e:
        if e.__class__.__name__ == "TtsCancelledError":
            raise HTTPException(status_code=409, detail="tts cancelled")
        raise HTTPException(status_code=502, detail=f"tts synthesize failed: {e}")
    return Response(
        content=result.wav_bytes,
        media_type="audio/wav",
        headers={
            "X-Jachin-Duration-Ms": str(result.duration_ms),
            "X-Jachin-Sample-Rate": str(result.sample_rate),
            "X-Jachin-TTS-Synth-Ms": str(getattr(result, "synth_ms", 0)),
            "X-Jachin-TTS-Attempts": str(getattr(result, "attempts", 1)),
            "X-Jachin-TTS-Max-New-Frames": str(getattr(result, "max_new_frames", 0)),
            "X-Jachin-TTS-Quality": str(getattr(result, "quality_status", "ok")),
            "X-Jachin-TTS-Kind": str((result.trace or {}).get("tts_kind", "")),
            "X-Jachin-TTS-Style-Index": str((result.trace or {}).get("style_index", "")),
            "X-Jachin-TTS-Style-Mode": str((result.trace or {}).get("style_mode", "")),
            "X-Jachin-TTS-Raw-Duration-Ms": str(((result.trace or {}).get("audio_trim") or {}).get("original_duration_ms", result.duration_ms)),
            "X-Jachin-TTS-Trim-Leading-Ms": str(((result.trace or {}).get("audio_trim") or {}).get("leading_trim_ms", 0)),
            "X-Jachin-TTS-Trim-Trailing-Ms": str(((result.trace or {}).get("audio_trim") or {}).get("trailing_trim_ms", 0)),
        },
    )


@app.websocket("/v1/tts/stream")
async def tts_stream(websocket: WebSocket):
    await websocket.accept()
    try:
        raw = await websocket.receive_json()
        req = TtsRequest(**raw)
        if not req.text.strip():
            await websocket.send_json({"type": "error", "code": "bad_request", "message": "text is empty"})
            return
        if not tts_service.ready:
            await websocket.send_json({"type": "error", "code": "tts_not_ready", "message": f"TTS backend not ready: {tts_service.model_path}"})
            return
        if not hasattr(tts_service, "stream_synthesize"):
            await websocket.send_json({"type": "error", "code": "stream_unsupported", "message": "current TTS backend does not support streaming"})
            return

        stream_iter = tts_service.stream_synthesize(
            req.text,
            voice=req.voice,
            session_id=req.session_id,
            speed=req.speed if req.speed is not None else cfg.tts_speed,
            kind=req.kind,
        )

        def _next_event():
            try:
                return next(stream_iter)
            except StopIteration:
                return None

        while True:
            event = await asyncio.to_thread(_next_event)
            if event is None:
                break
            event_type = event.get("type")
            if event_type == "audio":
                data = event.get("data") or b""
                await websocket.send_json(
                    {
                        "type": "audio",
                        "audio_b64": base64.b64encode(data).decode("ascii"),
                        "elapsed_ms": event.get("elapsed_ms", 0),
                    }
                )
            elif event_type == "complete":
                await websocket.send_json(
                    {
                        "type": "done",
                        "first_packet_ms": event.get("first_packet_ms", 0),
                        "opened_ms": event.get("opened_ms", 0),
                        "total_ms": event.get("total_ms", 0),
                        "request_id": event.get("request_id", ""),
                    }
                )
            elif event_type == "error":
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "stream_error",
                        "message": str(event.get("message") or ""),
                    }
                )
                break
            elif event_type == "meta":
                await websocket.send_json({k: v for k, v in event.items() if k != "data"})
            elif event_type in {"open", "close"}:
                await websocket.send_json(
                    {
                        "type": event_type,
                        "elapsed_ms": event.get("elapsed_ms", 0),
                    }
                )
            else:
                await websocket.send_json({"type": "event", "message": str(event.get("message") or "")[:1000]})
    except WebSocketDisconnect:
        return
    except Exception as e:
        logger.exception("TTS stream failed")
        try:
            await websocket.send_json({"type": "error", "code": "server_error", "message": str(e)})
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


@app.get("/v1/tts/voices")
def tts_voices() -> dict:
    voices = tts_service.list_voices()
    return {
        "ok": True,
        "voices": voices,
        "default_voice": cfg.tts_voice,
        "default_voice_exists": tts_service.has_voice(cfg.tts_voice),
    }


@app.post("/v1/session/cancel")
def session_cancel(req: CancelRequest) -> dict:
    sid = (req.session_id or "").strip()
    if sid:
        cancelled = tts_service.cancel_session(sid)
    else:
        tts_service.cancel_all()
        cancelled = True
    return {
        "ok": True,
        "session_id": req.session_id,
        "cancelled": cancelled,
    }


@app.get("/v1/sv/status")
def sv_status() -> dict:
    return {
        "ok": True,
        "sv_ready": sv_service.ready,
        "sv_dir": str(cfg.sv_dir),
        "model": sv_service.backend,
        "load_error": sv_service.load_error,
    }


@app.post("/v1/sv/extract")
async def sv_extract(audio: UploadFile = File(...)) -> dict:
    raw = await audio.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty audio payload")
    try:
        emb = sv_service.extract_embedding(raw)
        return {
            "embedding": emb.astype(float).tolist(),
            "dim": int(emb.size),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"sv_extract_failed: {e}")
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"sv_model_unavailable: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"sv_extract_failed: {e}")


@app.post("/v1/sv/verify")
async def sv_verify(
    audio: UploadFile = File(...),
    centroid: str = Form(...),
    threshold: float = Form(0.31),
) -> dict:
    raw = await audio.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty audio payload")
    c = _parse_centroid(centroid)
    try:
        result = sv_service.verify(raw, c, threshold=float(threshold))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"sv_verify_failed: {e}")
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"sv_model_unavailable: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"sv_verify_failed: {e}")
    return {
        "score": result.score,
        "is_match": result.is_match,
        "reason": result.reason,
        "threshold": float(threshold),
    }


@app.post("/v1/sv/label_windows")
async def sv_label_windows(
    audio: UploadFile = File(...),
    centroid: str = Form(...),
    win_step_ms: int = Form(250),
    win_len_ms: int = Form(900),
    win_threshold_high: float = Form(0.38),
    win_threshold_low: float = Form(0.25),
    debounce_count: int = Form(1),
) -> dict:
    raw = await audio.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty audio payload")
    c = _parse_centroid(centroid)
    try:
        windows = sv_service.label_windows(
            raw,
            c,
            win_step_ms=int(win_step_ms),
            win_len_ms=int(win_len_ms),
            win_threshold_high=float(win_threshold_high),
            win_threshold_low=float(win_threshold_low),
            debounce_count=int(debounce_count),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"sv_label_windows_failed: {e}")
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"sv_model_unavailable: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"sv_label_windows_failed: {e}")
    return {"windows": windows}


@app.post("/v1/sv/filter_owner_track")
async def sv_filter_owner_track(
    audio: UploadFile = File(...),
    centroid: str = Form(...),
    win_step_ms: int = Form(250),
    win_len_ms: int = Form(900),
    win_threshold_high: float = Form(0.38),
    win_threshold_low: float = Form(0.25),
    min_owner_duration_ms: int = Form(300),
    debounce_count: int = Form(1),
) -> dict:
    raw = await audio.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty audio payload")
    c = _parse_centroid(centroid)
    try:
        owner_wav, skipped, owner_duration_ms = sv_service.filter_owner_track(
            raw,
            c,
            win_step_ms=int(win_step_ms),
            win_len_ms=int(win_len_ms),
            win_threshold_high=float(win_threshold_high),
            win_threshold_low=float(win_threshold_low),
            min_owner_duration_ms=int(min_owner_duration_ms),
            debounce_count=int(debounce_count),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"sv_filter_owner_track_failed: {e}")
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=f"sv_model_unavailable: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"sv_filter_owner_track_failed: {e}")
    return {
        "owner_wav_b64": base64.b64encode(owner_wav).decode("ascii") if owner_wav else "",
        "owner_duration_ms": owner_duration_ms,
        "skipped_segments": skipped,
    }


@app.post("/v1/models/audio/warm")
def warm_audio_models(req: WarmAudioRequest) -> dict:
    warmed: dict[str, bool] = {"stt": False, "tts": False, "sv": False}
    details: dict[str, Any] = {}
    if req.stt:
        _warmup_stt_engine()
        warmed["stt"] = True
    if req.tts:
        details["tts"] = _warmup_tts_engine(reason=req.reason or "warm_endpoint")
        warmed["tts"] = True
    if req.sv:
        _warmup_sv_engine()
        warmed["sv"] = True
    return {"ok": True, "warmed": warmed, "details": details}


if __name__ == "__main__":
    import uvicorn

    logger.info("Starting voice_server at http://%s:%s", cfg.host, cfg.port)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level=cfg.log_level)
