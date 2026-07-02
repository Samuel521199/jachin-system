from __future__ import annotations

import logging
import json
import base64
import asyncio
import io
import struct
import time
import wave
import socket
import socketserver
import threading
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

from config import load_config
from services.stt_service import SttService
from services.tts_service import TtsCancelledError, TtsService
from services.sv_service import SvService


class TtsRequest(BaseModel):
    text: str
    voice: Optional[str] = None
    session_id: Optional[str] = None
    speed: Optional[float] = None


class CancelRequest(BaseModel):
    session_id: Optional[str] = None


class WarmAudioRequest(BaseModel):
    stt: bool = False
    tts: bool = False
    sv: bool = False


cfg = load_config()
logging.basicConfig(level=getattr(logging, cfg.log_level.upper(), logging.INFO))
logger = logging.getLogger("jachin.voice_server")

stt_service = SttService(cfg.stt_dir)
tts_service = TtsService(cfg.tts_dir, default_voice=cfg.tts_voice, default_speed=cfg.tts_speed)
sv_service = SvService(cfg.sv_dir)

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
        logger.info("Preloading SenseVoice STT engine…")
        stt_service._load_engine()


def _warmup_tts_engine() -> None:
    if tts_service.ready:
        logger.info("Preloading Kokoro ONNX TTS engine…")
        if tts_service._load_engine():
            # Run one tiny synthesis so the first user-facing sentence does not pay
            # the ONNX/G2P cold path. The bytes are intentionally discarded.
            result = tts_service.synthesize("你好。", voice=cfg.tts_voice)
            logger.info(
                "Kokoro ONNX TTS warmed (voice=%s, sample_rate=%s, duration_ms=%s)",
                cfg.tts_voice,
                result.sample_rate,
                result.duration_ms,
            )


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

# Tauri/Vite 开发页（如 http://localhost:31421）跨域访问 127.0.0.1:18982 时浏览器会先发 OPTIONS 预检
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
    return {
        "ok": True,
        "stt_ready": stt_service.ready,
        "tts_ready": tts_service.ready,
        "sv_ready": sv_service.ready,
        "stt_model": "SenseVoiceSmall-onnx",
        "tts_model": "Kokoro-82M-v1.1-zh-ONNX",
        "sv_model": sv_service.backend,
        "sv_load_error": sv_service.load_error,
        "tts_voice": cfg.tts_voice,
        "tts_speed": cfg.tts_speed,
        "tts_voice_exists": tts_service.has_voice(cfg.tts_voice),
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
    return await asyncio.to_thread(stt_service.transcribe, wav_bytes)


def _stt_transcribe_pcm_sync(pcm_bytes: bytes, sample_rate: int = 16000) -> "SttResult":
    wav_bytes = _pcm16le_to_wav_bytes(pcm_bytes, sample_rate=sample_rate, channels=1)
    return stt_service.transcribe(wav_bytes)


def _pack_tcp_frame(msg_type: int, payload: bytes = b"") -> bytes:
    return struct.pack("<BI", int(msg_type) & 0xFF, len(payload)) + payload


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
        min_new_bytes_for_partial = 3200 * 2
        min_infer_interval_sec = 0.30
        self._send_json(_STT_TCP_READY, {"type": "ready", "sample_rate": sample_rate})

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
                    {
                        "type": "partial",
                        "session_id": session_id,
                        "text": result.text,
                        "confidence": result.confidence,
                        "duration_ms": result.duration_ms,
                        "language": result.language,
                    },
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
                    self._send_json(_STT_TCP_ACK, {"type": "ack_start", "session_id": session_id, "sample_rate": sample_rate})
                    continue
                if msg_type == _STT_TCP_RESET:
                    pcm_buffer.clear()
                    partial_text = ""
                    last_infer_at = 0.0
                    last_infer_len = 0
                    self._send_json(_STT_TCP_ACK, {"type": "ack_reset", "session_id": session_id})
                    continue
                if msg_type == _STT_TCP_PING:
                    self._send_json(_STT_TCP_ACK, {"type": "pong", "session_id": session_id})
                    continue
                if msg_type == _STT_TCP_CHUNK:
                    if payload:
                        pcm_buffer.extend(payload)
                        maybe_partial(force=False)
                    continue
                if msg_type == _STT_TCP_FINALIZE:
                    maybe_partial(force=True)
                    final = _stt_transcribe_pcm_sync(bytes(pcm_buffer), sample_rate=sample_rate)
                    self._send_json(
                        _STT_TCP_FINAL,
                        {
                            "type": "final",
                            "session_id": session_id,
                            "text": final.text,
                            "confidence": final.confidence,
                            "duration_ms": final.duration_ms,
                            "language": final.language,
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
    raw = await audio.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty audio payload")
    result = stt_service.transcribe(raw)
    if not stt_service.ready:
        raise HTTPException(
            status_code=503,
            detail=f"STT model not ready: {stt_service.model_path}",
        )
    return {
        "text": result.text,
        "confidence": result.confidence,
        "duration_ms": result.duration_ms,
        "language": result.language,
        "session_id": session_id,
    }


@app.websocket("/v1/stt/stream")
async def stt_stream(websocket: WebSocket, session_id: Optional[str] = None):
    await websocket.accept()
    if not stt_service.ready:
        await websocket.send_json(
            {
                "type": "error",
                "session_id": session_id,
                "message": f"STT model not ready: {stt_service.model_path}",
            }
        )
        await websocket.close(code=1011)
        return

    sample_rate = 16000
    pcm_buffer = bytearray()
    partial_text = ""
    last_infer_at = 0.0
    last_infer_len = 0
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
                {
                    "type": "partial",
                    "session_id": session_id,
                    "text": result.text,
                    "confidence": result.confidence,
                    "duration_ms": result.duration_ms,
                    "language": result.language,
                }
            )

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
                    await websocket.send_json({"type": "ack_start", "session_id": session_id, "sample_rate": sample_rate})
                    continue
                if msg_type == "reset":
                    pcm_buffer.clear()
                    partial_text = ""
                    last_infer_at = 0.0
                    last_infer_len = 0
                    await websocket.send_json({"type": "ack_reset", "session_id": session_id})
                    continue
                if msg_type == "finalize":
                    await maybe_infer_partial(force=True)
                    final = await _stt_transcribe_pcm_async(bytes(pcm_buffer), sample_rate=sample_rate)
                    await websocket.send_json(
                        {
                            "type": "final",
                            "session_id": session_id,
                            "text": final.text,
                            "confidence": final.confidence,
                            "duration_ms": final.duration_ms,
                            "language": final.language,
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
    try:
        result = tts_service.synthesize(req.text, voice=req.voice, session_id=req.session_id, speed=req.speed)
    except TtsCancelledError:
        raise HTTPException(status_code=409, detail="tts cancelled")
    if not tts_service.ready:
        raise HTTPException(
            status_code=503,
            detail=f"TTS model not ready: {tts_service.model_path}",
        )
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
        },
    )


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
    if req.stt:
        _warmup_stt_engine()
        warmed["stt"] = True
    if req.tts:
        _warmup_tts_engine()
        warmed["tts"] = True
    if req.sv:
        _warmup_sv_engine()
        warmed["sv"] = True
    return {"ok": True, "warmed": warmed}


if __name__ == "__main__":
    import uvicorn

    logger.info("Starting voice_server at http://%s:%s", cfg.host, cfg.port)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level=cfg.log_level)

