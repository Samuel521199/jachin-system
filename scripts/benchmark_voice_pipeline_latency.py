#!/usr/bin/env python3
"""
Voice companion latency benchmark: STT -> L3 cognitive main loop -> TTS.

The desktop voice intent router has been retired. This script sends the
recognized text and STT evidence directly to L3, matching the current desktop
voice path.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import io
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable

_SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))


if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


DEFAULT_JVS = os.getenv("JACHIN_VOICE_SERVER_URL", "http://127.0.0.1:18982").rstrip("/")
DEFAULT_L3 = os.getenv("JACHIN_L3_HTTP_BASE", "http://127.0.0.1:18991").rstrip("/")
DEFAULT_L3_WS = os.getenv("JACHIN_L3_WS_URL", "ws://127.0.0.1:18981/sensory").rstrip("/")
DEFAULT_OUT_DIR = Path("data/voice_latency_bench")
DEFAULT_VOICE = "zm_053"
DEFAULT_COMPANION_CUE_MANIFEST = (
    PROJECT_ROOT / "clients" / "desktop" / "public" / "audio" / "companion_cues" / "manifest.json"
)

SENSEVOICE_TAG_RE = re.compile(r"<\|.*?\|>")

RESULT_HINT_RE = re.compile(r"(宸瞸宸茬粡|瀹屾垚|鎴愬姛|澶辫触|缁撴灉|鏈€缁坾鎼炲畾|濂戒簡|瀹屾垚浜唡宸蹭负浣爘鎴戝凡|鏃犳硶|娌℃硶|澶辫触浜唡鎶ラ敊|鍙互浜唡璇烽噸璇?")
PROCESS_HINT_RE = re.compile(r"(棣栧厛|鎺ヤ笅鏉鐒跺悗|姝ラ|绗琜涓€浜屼笁鍥涗簲鍏竷鍏節鍗乚|鍏坾鍐峾鏈€鍚巪姝ｅ湪|鎴戜細|鎴戝皢|鍒嗘瀽|鎬濊矾|璁″垝|娴佺▼|鎵ц涓瓅澶勭悊涓瓅濡備笅)")
LIST_PREFIX_RE = re.compile(r"^(\d+[\.\)]|[-*鈥|绗琜涓€浜屼笁鍥涗簲鍏竷鍏節鍗乚+姝姝ラ[:锛歖?)")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _perf_ms() -> float:
    return time.perf_counter() * 1000.0


def _http_post_json(url: str, payload: dict[str, Any], timeout: float) -> bytes:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _jvs_ws_base(jvs_base: str) -> str:
    base = (jvs_base or DEFAULT_JVS).rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base[len("https://") :]
    if base.startswith("http://"):
        return "ws://" + base[len("http://") :]
    if base.startswith("ws://") or base.startswith("wss://"):
        return base
    return "ws://127.0.0.1:18982"


def _pcm_duration_ms(byte_count: int, sample_rate: int, channels: int) -> int:
    denom = max(1, int(sample_rate or 24000) * max(1, int(channels or 1)) * 2)
    return int(round(max(0, int(byte_count)) * 1000 / denom))


def _stream_tts_by_jvs(
    *,
    jvs_base: str,
    text: str,
    voice: str,
    session_id: str,
    timeout: float,
) -> dict[str, Any]:
    try:
        from websockets.sync.client import connect
    except ImportError as e:
        raise RuntimeError("websockets.sync client is required for JVS TTS stream") from e

    url = f"{_jvs_ws_base(jvs_base)}/v1/tts/stream"
    started = _perf_ms()
    first_audio_ms = 0.0
    chunks = 0
    audio_bytes = 0
    sample_rate = 24000
    channels = 1
    request_id = ""
    with connect(
        url,
        open_timeout=max(0.5, min(5.0, timeout)),
        close_timeout=1,
        ping_interval=None,
        max_size=16 * 1024 * 1024,
    ) as ws:
        ws.send(
            json.dumps(
                {
                    "text": text,
                    "voice": voice,
                    "session_id": session_id,
                    "speed": 1.0,
                    "kind": "content",
                },
                ensure_ascii=False,
            )
        )
        deadline = time.monotonic() + max(0.5, timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("JVS TTS stream timed out")
            raw = ws.recv(timeout=remaining)
            try:
                msg = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
            except Exception:
                continue
            typ = str(msg.get("type") or "")
            if typ == "meta":
                try:
                    sample_rate = int(msg.get("sample_rate") or sample_rate)
                except Exception:
                    pass
                try:
                    channels = int(msg.get("channels") or channels)
                except Exception:
                    pass
                continue
            if typ == "audio":
                b64 = str(msg.get("audio_b64") or "")
                if not b64:
                    continue
                data = base64.b64decode(b64)
                if data:
                    chunks += 1
                    audio_bytes += len(data)
                    if first_audio_ms <= 0:
                        first_audio_ms = _perf_ms() - started
                continue
            if typ == "done":
                request_id = str(msg.get("request_id") or "")
                total_ms = _perf_ms() - started
                return {
                    "ok": True,
                    "unsupported": False,
                    "first_audio_ms": first_audio_ms,
                    "total_ms": total_ms,
                    "chunks": chunks,
                    "bytes": audio_bytes,
                    "audio_ms": _pcm_duration_ms(audio_bytes, sample_rate, channels),
                    "sample_rate": sample_rate,
                    "channels": channels,
                    "request_id": request_id,
                }
            if typ == "error":
                code = str(msg.get("code") or "")
                message = str(msg.get("message") or "")
                if code in {"stream_unsupported", "tts_not_ready"}:
                    return {
                        "ok": False,
                        "unsupported": True,
                        "first_audio_ms": first_audio_ms,
                        "total_ms": _perf_ms() - started,
                        "chunks": chunks,
                        "bytes": audio_bytes,
                        "audio_ms": _pcm_duration_ms(audio_bytes, sample_rate, channels),
                        "sample_rate": sample_rate,
                        "channels": channels,
                        "request_id": request_id,
                        "error": f"{code}: {message}",
                    }
                raise RuntimeError(message or code or "JVS TTS stream error")


async def _l3_ws_run_async(
    ws_url: str,
    payload: dict[str, Any],
    timeout: float,
) -> tuple[str, float, float, dict[str, Any]]:
    """Run one L3 turn through the same /sensory WebSocket path used by desktop companion."""
    try:
        import websockets
    except ImportError as e:
        raise RuntimeError("websockets package is required for --l3-transport ws/auto") from e

    open_timeout = max(0.5, min(5.0, timeout))
    sent_at = 0.0
    first_chunk_ms = 0.0
    answer_ms = 0.0
    chunks: list[str] = []
    async with websockets.connect(
        ws_url,
        open_timeout=open_timeout,
        close_timeout=1,
        ping_interval=None,
        max_size=16 * 1024 * 1024,
    ) as ws:
        session_id = str(payload.get("session_id") or payload.get("chat_id") or "").strip()
        lark_chat_id = str(payload.get("lark_chat_id") or "").strip()
        await ws.send(json.dumps({"type": "manifest", "caps": ["voice_latency_bench"]}, ensure_ascii=False))
        if session_id:
            prep = {
                "type": "prepare_context",
                "trigger": "companion_voice_start",
                "source": "desktop_voice_companion",
                "origin": "desktop_voice_companion",
                "session_id": session_id,
                "implicit_signals": {
                    "desktop_companion": True,
                    "source": "desktop_voice_companion",
                    "local_voice_session": not bool(lark_chat_id),
                    "voice_benchmark": True,
                },
            }
            prep["chat_id"] = lark_chat_id or session_id
            await ws.send(json.dumps(prep, ensure_ascii=False))
        sent_at = _perf_ms()
        await ws.send(json.dumps(payload, ensure_ascii=False))
        deadline = time.monotonic() + max(0.5, timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("L3 WebSocket timed out waiting for answer")
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            try:
                msg = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
            except Exception:
                continue
            step = str(msg.get("step_type") or msg.get("type") or "")
            if step == "chunk":
                content = str(msg.get("content") or "")
                if content:
                    chunks.append(content)
                    if first_chunk_ms <= 0:
                        first_chunk_ms = _perf_ms() - sent_at
                continue
            if step == "answer":
                answer_ms = _perf_ms() - sent_at
                trace = msg.get("latency_trace") if isinstance(msg.get("latency_trace"), dict) else {}
                answer = str(msg.get("content") or "").strip()
                if answer:
                    return answer, first_chunk_ms, answer_ms, trace
                joined = "".join(chunks).strip()
                if joined:
                    return joined, first_chunk_ms, answer_ms, trace
                raise RuntimeError("L3 WebSocket returned empty answer")
            if step == "voice_template_post_answer_metrics":
                continue
            if step == "error":
                raise RuntimeError(str(msg.get("content") or msg.get("error") or "L3 WebSocket error"))


def _l3_ws_run(ws_url: str, payload: dict[str, Any], timeout: float) -> tuple[str, float, float, dict[str, Any]]:
    return asyncio.run(_l3_ws_run_async(ws_url, payload, timeout))


class L3WsClient:
    """Persistent /sensory WebSocket client, matching the desktop companion connection shape."""

    def __init__(self, ws_url: str):
        self.ws_url = ws_url
        self._ws = None

    def close(self) -> None:
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def _connect(self, timeout: float):
        if self._ws is not None:
            return self._ws
        try:
            from websockets.sync.client import connect
        except ImportError as e:
            raise RuntimeError("websockets.sync client is required for persistent L3 WS") from e
        self._ws = connect(
            self.ws_url,
            open_timeout=max(0.5, min(5.0, timeout)),
            close_timeout=1,
            ping_interval=None,
            max_size=16 * 1024 * 1024,
        )
        self._ws.send(json.dumps({"type": "manifest", "caps": ["voice_latency_bench"]}, ensure_ascii=False))
        return self._ws

    def prepare_context(self, session_id: str, *, lark_chat_id: str = "", timeout: float = 3.0) -> None:
        sid = (session_id or "").strip()
        if not sid:
            return
        lark_cid = (lark_chat_id or "").strip()
        ws = self._connect(timeout)
        prep = {
            "type": "prepare_context",
            "trigger": "companion_voice_start",
            "source": "desktop_voice_companion",
            "origin": "desktop_voice_companion",
            "session_id": sid,
            "implicit_signals": {
                "desktop_companion": True,
                "source": "desktop_voice_companion",
                "local_voice_session": not bool(lark_cid),
                "voice_benchmark": True,
            },
        }
        prep["chat_id"] = lark_cid or sid
        ws.send(json.dumps(prep, ensure_ascii=False))

    def run(self, payload: dict[str, Any], timeout: float) -> tuple[str, float, float, dict[str, Any]]:
        ws = self._connect(timeout)
        sent_at = _perf_ms()
        first_chunk_ms = 0.0
        answer_ms = 0.0
        chunks: list[str] = []
        try:
            ws.send(json.dumps(payload, ensure_ascii=False))
            deadline = time.monotonic() + max(0.5, timeout)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("L3 WebSocket timed out waiting for answer")
                raw = ws.recv(timeout=remaining)
                try:
                    msg = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
                except Exception:
                    continue
                step = str(msg.get("step_type") or msg.get("type") or "")
                if step == "chunk":
                    content = str(msg.get("content") or "")
                    if content:
                        chunks.append(content)
                        if first_chunk_ms <= 0:
                            first_chunk_ms = _perf_ms() - sent_at
                    continue
                if step == "answer":
                    answer_ms = _perf_ms() - sent_at
                    trace = msg.get("latency_trace") if isinstance(msg.get("latency_trace"), dict) else {}
                    answer = str(msg.get("content") or "").strip()
                    if answer:
                        return answer, first_chunk_ms, answer_ms, trace
                    joined = "".join(chunks).strip()
                    if joined:
                        return joined, first_chunk_ms, answer_ms, trace
                    raise RuntimeError("L3 WebSocket returned empty answer")
                if step == "voice_template_post_answer_metrics":
                    continue
                if step == "error":
                    raise RuntimeError(str(msg.get("content") or msg.get("error") or "L3 WebSocket error"))
        except Exception:
            self.close()
            raise


def _http_post_multipart_stt(url: str, wav_bytes: bytes, timeout: float) -> dict[str, Any]:
    boundary = f"----jachin-latency-{int(time.time() * 1000)}"
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(b'Content-Disposition: form-data; name="audio"; filename="speech.wav"\r\n')
    body.extend(b"Content-Type: audio/wav\r\n\r\n")
    body.extend(wav_bytes)
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        url,
        data=bytes(body),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _wav_duration_ms(wav_bytes: bytes) -> int:
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate() or 24000
            return int(frames / float(rate) * 1000)
    except Exception:
        return 0


def _normalize_companion_cue_text(text: str) -> str:
    s = re.sub(r"[\s\u3000]+", "", text or "")
    s = re.sub(r"[銆傦紒锛??锛?銆侊紱;锛?\"鈥溾€濃€樷€?锛堬級()\[\]銆愩€慮+$", "", s)
    return s.strip()


_COMPANION_CUE_CACHE: dict[str, Path] | None = None


def _load_companion_cue_cache() -> dict[str, Path]:
    global _COMPANION_CUE_CACHE
    if _COMPANION_CUE_CACHE is not None:
        return _COMPANION_CUE_CACHE
    out: dict[str, Path] = {}
    manifest_path = DEFAULT_COMPANION_CUE_MANIFEST
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest.get("items") or []:
            key = _normalize_companion_cue_text(str(item.get("text") or ""))
            filename = str(item.get("file") or "").strip()
            if not key or not filename:
                continue
            wav_path = manifest_path.parent / filename
            if wav_path.is_file() and wav_path.stat().st_size > 44:
                out[key] = wav_path
    except Exception:
        out = {}
    _COMPANION_CUE_CACHE = out
    return out


def _find_companion_cue_wav(text: str) -> Path | None:
    key = _normalize_companion_cue_text(text)
    if not key:
        return None
    return _load_companion_cue_cache().get(key)


def _first_sentence(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    seps = "銆傦紒锛??锛?\n"
    idx = min([t.find(s) for s in seps if s in t] or [-1])
    if idx >= 0:
        return t[: idx + 1].strip()
    return t[:80].strip()


def _sanitize_stt_text(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    t = SENSEVOICE_TAG_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"(?<=[\u4e00-\u9fff])[A-Za-z]$", "", t).strip()
    if not t:
        return ""
    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", t):
        return ""
    return t



def _pick_result_clause(s: str) -> str | None:
    clauses = [x.strip() for x in re.split(r"[锛屻€傦紱锛侊紵]", s or "") if x.strip()]
    if not clauses:
        return None
    for c in reversed(clauses):
        if RESULT_HINT_RE.search(c):
            return f"{c}."
    return None


def _prepare_sentence_for_tts(raw: str) -> str | None:
    s = (raw or "")
    s = re.sub(r"```[\s\S]*?```", " ", s)
    s = re.sub(r"`[^`]*`", " ", s)
    s = re.sub(r"[#*_~]", "", s)
    s = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) < 2:
        return None
    letters = len(re.findall(r"[\w\u4e00-\u9fff]", s, flags=re.UNICODE))
    if letters < 2:
        return None
    noisy = len(re.findall(r"[^\w\u4e00-\u9fff\s锛屻€傦紒锛熴€侊紱锛歖", s, flags=re.UNICODE))
    if len(s) > 0 and (noisy / len(s)) > 0.45:
        return None
    has_result = bool(RESULT_HINT_RE.search(s))
    has_process = bool(PROCESS_HINT_RE.search(s))
    if LIST_PREFIX_RE.search(s) and not has_result:
        return None
    if has_process and not has_result:
        return None
    if (has_result and has_process) or (has_result and len(s) > 36):
        concise = _pick_result_clause(s)
        if concise:
            s = concise
    return s


def _split_for_companion_tts(text: str) -> list[str]:
    t = (text or "").strip()
    if not t:
        return []
    out: list[str] = []
    buf: list[str] = []
    enders = set("銆傦紒锛??锛?")
    for ch in t:
        buf.append(ch)
        if ch in enders:
            s = "".join(buf).strip()
            if s:
                out.append(s)
            buf = []
    if buf:
        s = "".join(buf).strip()
        if s:
            out.append(s)
    if not out:
        out = [t]
    return out


def _pick_tts_inputs(answer: str, mode: str, max_sentences: int) -> list[str]:
    if mode == "legacy":
        one = _first_sentence(answer) or answer[:80]
        return [one] if one else []
    # companion mode: 浣跨敤鎺ヨ繎鍓嶇 speakableText 鐨勮繃婊わ紝榛樿鍙挱鎶ョ粨鏋滅被鍙ュ瓙銆?    rows = _split_for_companion_tts(answer)
    kept: list[str] = []
    for r in rows:
        s = _prepare_sentence_for_tts(r)
        if s:
            kept.append(s)
        if len(kept) >= max(1, max_sentences):
            break
    if kept:
        return kept
    one = _first_sentence(answer) or answer[:80]
    return [one] if one else []


def _record_wav_bytes(duration_sec: float, sample_rate: int = 16000) -> bytes:
    try:
        import sounddevice as sd
        import soundfile as sf
    except ImportError as e:
        raise RuntimeError(
            "褰曢煶妯″紡闇€瑕佸畨瑁?sounddevice + soundfile锛岃鎵ц: pip install sounddevice soundfile"
        ) from e
    frames = int(max(0.5, duration_sec) * sample_rate)
    audio = sd.rec(frames, samplerate=sample_rate, channels=1, dtype="int16")
    sd.wait()
    buf = io.BytesIO()
    sf.write(buf, audio, sample_rate, subtype="PCM_16", format="WAV")
    return buf.getvalue()


def _play_wav_bytes(wav_bytes: bytes) -> None:
    if sys.platform != "win32":
        return
    import tempfile
    import winsound

    p = Path(tempfile.gettempdir()) / "jachin_latency_preview.wav"
    p.write_bytes(wav_bytes)
    winsound.PlaySound(None, winsound.SND_PURGE)
    winsound.PlaySound(str(p), winsound.SND_FILENAME | winsound.SND_ASYNC)


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


@dataclass
class RunMetric:
    run_id: int
    timestamp_ms: int
    ok: bool
    error: str
    stt_ms: float
    l3_ms: float
    l3_first_chunk_ms: float
    l3_answer_ms: float
    l3_server_session_save_ms: float
    l3_server_broadcast_ms: float
    l3_server_append_final_ms: float
    l3_latency_trace: str
    tts_ms: float
    tts_first_audio_ms: float
    total_ms: float
    recognized_text: str
    answer_preview: str
    tts_input: str
    tts_calls: int
    tts_stream_chunks: int
    tts_stream_bytes: int
    tts_audio_ms: int
    tts_transport: str
    tts_fallback_reason: str
    routed_text: str
    l3_transport: str
    l3_fallback_reason: str


def run_once(
    i: int,
    *,
    jvs_base: str,
    l3_base: str,
    l3_ws: str,
    l3_transport: str,
    l3_ws_client: L3WsClient | None,
    chat_id: str,
    lark_chat_id: str,
    wav_bytes: bytes | None,
    text_input: str | None,
    voice: str,
    t_stt: float,
    t_l3: float,
    t_tts: float,
    chat_prefix: str,
    play_audio: bool,
    tts_stream: bool,
    tts_mode: str,
    max_speak_sentences: int,
    progress: Callable[[str], None] | None = None,
) -> RunMetric:
    started = _perf_ms()
    stamp = _now_ms()
    stt_ms = l3_ms = tts_ms = 0.0
    tts_first_audio_ms = 0.0
    l3_first_chunk_ms = 0.0
    l3_answer_ms = 0.0
    l3_server_session_save_ms = 0.0
    l3_server_broadcast_ms = 0.0
    l3_server_append_final_ms = 0.0
    l3_latency_trace = ""
    l3_trace: dict[str, Any] = {}
    recognized = ""
    answer = ""
    tts_input = ""
    tts_calls = 0
    tts_stream_chunks = 0
    tts_stream_bytes = 0
    tts_audio_ms = 0
    tts_transport = ""
    tts_fallback_reason = ""
    routed_text = ""
    l3_transport_used = ""
    l3_fallback_reason = ""
    try:
        if text_input is not None:
            recognized = text_input.strip()
        else:
            if not wav_bytes:
                raise RuntimeError("鏃犺緭鍏ラ煶棰戯紝涓旀湭鎻愪緵 --text")
            if progress:
                progress("STT 璇锋眰涓?..")
            st = _perf_ms()
            stt_json = _http_post_multipart_stt(f"{jvs_base}/v1/stt/transcribe", wav_bytes, timeout=t_stt)
            stt_ms = _perf_ms() - st
            recognized = _sanitize_stt_text(stt_json.get("text") or "")
        if not recognized:
            raise RuntimeError("STT 鏈瘑鍒埌鏂囨湰")

        if progress:
            progress("L3 鎺ㄧ悊涓?..")
        st = _perf_ms()
        routed_text = recognized
        implicit_signals = {
            "desktop_companion": True,
            "source": "desktop_voice_companion",
            "voice_raw_stt_text": recognized,
            "voice_asr_raw_text": recognized,
            "voice_corrected_text": recognized,
            "voice_final_text": recognized,
        }
        implicit_signals["desktop_companion"] = True
        implicit_signals["source"] = "desktop_voice_companion"
        implicit_signals["benchmark_source"] = "voice_latency_bench"
        implicit_signals["voice_benchmark"] = True
        implicit_signals["voice_channel"] = "desktop_companion"
        implicit_signals["local_voice_session"] = not bool(lark_chat_id)
        l3_payload = {
            "intent": routed_text,
            "chat_id": lark_chat_id or chat_id,
            "session_id": lark_chat_id or chat_id,
            "origin": "terminal" if lark_chat_id else "desktop_voice_companion",
            "implicit_signals": implicit_signals,
        }
        if lark_chat_id:
            l3_payload["lark_chat_id"] = lark_chat_id
        http_payload = {
            "user_input": routed_text,
            "chat_id": lark_chat_id or chat_id,
            "session_id": chat_id,
            "max_iterations": 8,
            "implicit_signals": implicit_signals,
            "implicit_attribution": {
                "channel": "websocket_terminal" if lark_chat_id else "desktop_voice_companion",
                "session_id": chat_id,
                "local_voice_session": not bool(lark_chat_id),
            },
        }
        transport = (l3_transport or "auto").strip().lower()
        if transport not in ("auto", "ws", "http"):
            transport = "auto"
        if transport in ("auto", "ws"):
            try:
                if l3_ws_client is not None:
                    answer, l3_first_chunk_ms, l3_answer_ms, l3_trace = l3_ws_client.run(l3_payload, timeout=t_l3)
                    l3_transport_used = "ws_reuse"
                else:
                    answer, l3_first_chunk_ms, l3_answer_ms, l3_trace = _l3_ws_run(l3_ws, l3_payload, timeout=t_l3)
                    l3_transport_used = "ws"
            except Exception as ws_error:  # noqa: BLE001
                l3_fallback_reason = str(ws_error)
                if transport == "ws":
                    raise RuntimeError(f"L3 WebSocket failed: {ws_error}") from ws_error
        if not answer:
            l3_raw = _http_post_json(
                f"{l3_base}/api/v3/agent/run",
                http_payload,
                timeout=t_l3,
            )
            l3_transport_used = "http" if transport == "http" else "http_fallback"
            l3_json = json.loads(l3_raw.decode("utf-8"))
            if l3_json.get("error"):
                raise RuntimeError(str(l3_json["error"]))
            answer = (l3_json.get("answer") or "").strip()
            l3_answer_ms = _perf_ms() - st
        l3_ms = _perf_ms() - st
        if isinstance(l3_trace, dict) and l3_trace:
            def _trace_ms(name: str) -> float:
                try:
                    return float(l3_trace.get(name) or 0)
                except Exception:
                    return 0.0
            l3_server_session_save_ms = _trace_ms("session_save_ms")
            l3_server_broadcast_ms = _trace_ms("broadcast_ms")
            l3_server_append_final_ms = _trace_ms("append_final_ms")
            l3_latency_trace = json.dumps(l3_trace, ensure_ascii=False)[:800]
        if not answer:
            raise RuntimeError("L3 returned empty answer")

        effective_max_sentences = max(1, int(max_speak_sentences))
        tts_inputs = _pick_tts_inputs(answer, mode=tts_mode, max_sentences=effective_max_sentences)
        if not tts_inputs:
            raise RuntimeError("TTS has no speakable input")
        tts_input = " | ".join(tts_inputs)
        for idx, one in enumerate(tts_inputs, start=1):
            if progress:
                progress(f"TTS 鍚堟垚涓?.. ({idx}/{len(tts_inputs)})")
            st = _perf_ms()
            cue_wav_path = _find_companion_cue_wav(one)
            if cue_wav_path is not None:
                tts_wav = cue_wav_path.read_bytes()
                tts_transport = "local_cue_cache" if not tts_transport else f"{tts_transport}|local_cue_cache"
                tts_audio_ms += _wav_duration_ms(tts_wav)
                if play_audio:
                    _play_wav_bytes(tts_wav)
            else:
                stream_ok = False
                if tts_stream:
                    try:
                        stream_result = _stream_tts_by_jvs(
                            jvs_base=jvs_base,
                            text=one,
                            voice=voice,
                            session_id=chat_id,
                            timeout=t_tts,
                        )
                        if stream_result.get("ok") and int(stream_result.get("chunks") or 0) > 0:
                            stream_ok = True
                            tts_transport = "jvs_cloud_stream" if not tts_transport else f"{tts_transport}|jvs_cloud_stream"
                            tts_stream_chunks += int(stream_result.get("chunks") or 0)
                            tts_stream_bytes += int(stream_result.get("bytes") or 0)
                            tts_audio_ms += int(stream_result.get("audio_ms") or 0)
                            fa = float(stream_result.get("first_audio_ms") or 0.0)
                            if fa > 0 and (tts_first_audio_ms <= 0 or fa < tts_first_audio_ms):
                                tts_first_audio_ms = fa
                        elif stream_result.get("unsupported"):
                            tts_fallback_reason = str(stream_result.get("error") or "stream_unsupported")[:300]
                    except Exception as stream_error:  # noqa: BLE001
                        tts_fallback_reason = str(stream_error)[:300]
                if not stream_ok:
                    tts_wav = _http_post_json(
                        f"{jvs_base}/v1/tts/synthesize",
                        {"text": one, "voice": voice, "session_id": chat_id},
                        timeout=t_tts,
                    )
                    tts_transport = "jvs_http_wav" if not tts_transport else f"{tts_transport}|jvs_http_wav"
                    tts_calls += 1
                    tts_audio_ms += _wav_duration_ms(tts_wav)
                    if play_audio:
                        _play_wav_bytes(tts_wav)
            tts_ms += _perf_ms() - st

        return RunMetric(
            run_id=i,
            timestamp_ms=stamp,
            ok=True,
            error="",
            stt_ms=stt_ms,
            l3_ms=l3_ms,
            l3_first_chunk_ms=l3_first_chunk_ms,
            l3_answer_ms=l3_answer_ms,
            l3_server_session_save_ms=l3_server_session_save_ms,
            l3_server_broadcast_ms=l3_server_broadcast_ms,
            l3_server_append_final_ms=l3_server_append_final_ms,
            l3_latency_trace=l3_latency_trace,
            tts_ms=tts_ms,
            tts_first_audio_ms=tts_first_audio_ms,
            total_ms=_perf_ms() - started,
            recognized_text=recognized,
            answer_preview=answer[:220],
            tts_input=tts_input,
            tts_calls=tts_calls,
            tts_stream_chunks=tts_stream_chunks,
            tts_stream_bytes=tts_stream_bytes,
            tts_audio_ms=tts_audio_ms,
            tts_transport=tts_transport,
            tts_fallback_reason=tts_fallback_reason,
            routed_text=routed_text,
            l3_transport=l3_transport_used,
            l3_fallback_reason=l3_fallback_reason[:300],
        )
    except Exception as e:  # noqa: BLE001
        return RunMetric(
            run_id=i,
            timestamp_ms=stamp,
            ok=False,
            error=str(e),
            stt_ms=stt_ms,
            l3_ms=l3_ms,
            l3_first_chunk_ms=l3_first_chunk_ms,
            l3_answer_ms=l3_answer_ms,
            l3_server_session_save_ms=l3_server_session_save_ms,
            l3_server_broadcast_ms=l3_server_broadcast_ms,
            l3_server_append_final_ms=l3_server_append_final_ms,
            l3_latency_trace=l3_latency_trace,
            tts_ms=tts_ms,
            tts_first_audio_ms=tts_first_audio_ms,
            total_ms=_perf_ms() - started,
            recognized_text=recognized,
            answer_preview=answer[:220],
            tts_input=tts_input,
            tts_calls=tts_calls,
            tts_stream_chunks=tts_stream_chunks,
            tts_stream_bytes=tts_stream_bytes,
            tts_audio_ms=tts_audio_ms,
            tts_transport=tts_transport,
            tts_fallback_reason=tts_fallback_reason,
            routed_text=routed_text,
            l3_transport=l3_transport_used,
            l3_fallback_reason=l3_fallback_reason[:300],
        )


def write_rows(csv_path: Path, jsonl_path: Path, rows: list[RunMetric]) -> None:
    if not rows:
        return
    is_new = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        if is_new:
            w.writeheader()
        for r in rows:
            w.writerow(asdict(r))
    with jsonl_path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")


def print_live_summary(rows: list[RunMetric]) -> None:
    ok_rows = [r for r in rows if r.ok]
    if not ok_rows:
        print("  no successful samples yet")
        return
    stt = [r.stt_ms for r in ok_rows if r.stt_ms > 0]
    l3 = [r.l3_ms for r in ok_rows if r.l3_ms > 0]
    ans = [r.l3_answer_ms for r in ok_rows if r.l3_answer_ms > 0]
    tfa = [r.tts_first_audio_ms for r in ok_rows if r.tts_first_audio_ms > 0]
    tts = [r.tts_ms for r in ok_rows if r.tts_ms > 0]
    total = [r.total_ms for r in ok_rows]
    def _s(name: str, arr: list[float]) -> str:
        if not arr:
            return f"{name}: -"
        return f"{name}: p50={_percentile(arr,0.5):.0f}ms p90={_percentile(arr,0.9):.0f}ms avg={statistics.mean(arr):.0f}ms"
    print("  " + " | ".join([_s("STT", stt), _s("L3", l3), _s("ANS", ans), _s("TFA", tfa), _s("TTS", tts), _s("TOTAL", total)]))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Voice latency benchmark: STT -> L3 -> TTS")
    p.add_argument("--jvs-base", default=DEFAULT_JVS, help="JVS base URL")
    p.add_argument("--l3-base", default=DEFAULT_L3, help="L3 HTTP base URL")
    p.add_argument("--runs", type=int, default=20, help="Number of benchmark runs")
    p.add_argument("--interval-sec", type=float, default=0.5, help="Delay between runs")
    p.add_argument("--voice", default=DEFAULT_VOICE, help="TTS voice id")
    p.add_argument(
        "--tts-mode",
        choices=("companion", "legacy"),
        default="companion",
        help="TTS text selection mode",
    )
    p.add_argument(
        "--max-speak-sentences",
        type=int,
        default=3,
        help="Maximum sentences to send to TTS",
    )
    p.add_argument("--audio-file", type=Path, help="Use a fixed WAV file as input")
    p.add_argument("--record-sec", type=float, default=3.0, help="Recording seconds when no audio/text is provided")
    p.add_argument("--reuse-audio", action="store_true", help="Record once and reuse the audio for subsequent runs")
    p.add_argument("--text", help="Skip STT and use text directly as L3 input")
    p.add_argument("--play", action="store_true", help="Play synthesized TTS audio after each run")
    p.add_argument("--timeout-stt", type=float, default=90.0)
    p.add_argument("--timeout-l3", type=float, default=180.0)
    p.add_argument("--l3-ws", default=DEFAULT_L3_WS, help="L3 sensory WebSocket URL")
    p.add_argument(
        "--l3-transport",
        choices=("auto", "ws", "http"),
        default="ws",
        help="L3 transport: ws matches desktop companion; auto falls back to HTTP",
    )
    p.add_argument("--timeout-tts", type=float, default=120.0)
    p.add_argument("--no-tts-stream", action="store_true", help="Disable JVS streaming TTS")
    p.add_argument("--chat-prefix", default="voice-latency-bench")
    p.add_argument(
        "--lark-chat-id",
        default="",
        help="Optional real Lark chat_id (oc_...). Omit for local desktop companion mode.",
    )
    p.add_argument(
        "--chat-session-mode",
        choices=("stable", "per-run"),
        default="stable",
        help="stable matches desktop companion; per-run isolates every benchmark turn",
    )
    p.add_argument("--no-ws-reuse", action="store_true", help="Do not keep a persistent L3 WebSocket connection")
    p.add_argument("--no-ws-preflight", action="store_true", help="Do not send prepare_context before STT/turn")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    csv_path = args.out_dir / f"voice_latency_{stamp}.csv"
    jsonl_path = args.out_dir / f"voice_latency_{stamp}.jsonl"
    print(f"[INFO] CSV: {csv_path}")
    print(f"[INFO] JSONL: {jsonl_path}")
    print(
        f"[INFO] JVS={args.jvs_base}  L3_HTTP={args.l3_base}  L3_WS={args.l3_ws}  "
        f"transport={args.l3_transport}  voice={args.voice}  tts_mode={args.tts_mode}  "
        f"max_speak_sentences={args.max_speak_sentences}  "
        f"tts_stream={not args.no_tts_stream}  "
        f"chat_session_mode={args.chat_session_mode}  ws_reuse={not args.no_ws_reuse}  "
        f"ws_preflight={not args.no_ws_preflight}  "
        f"local_voice_session={not bool((args.lark_chat_id or '').strip())}"
    )

    fixed_wav: bytes | None = None
    if args.audio_file:
        if not args.audio_file.is_file():
            print(f"[ERROR] audio-file not found: {args.audio_file}")
            return 1
        fixed_wav = args.audio_file.read_bytes()
        print(f"[INFO] fixed audio input: {args.audio_file} ({len(fixed_wav)} bytes)")

    stable_chat_id = f"{args.chat_prefix}-{uuid.uuid4().hex[:10]}"
    ws_client: L3WsClient | None = None
    if args.l3_transport in ("auto", "ws") and not args.no_ws_reuse:
        ws_client = L3WsClient(args.l3_ws.rstrip("/"))

    rows: list[RunMetric] = []
    try:
        for i in range(1, max(1, args.runs) + 1):
            chat_id = (
                stable_chat_id
                if args.chat_session_mode == "stable"
                else f"{args.chat_prefix}-{i}-{uuid.uuid4().hex[:8]}"
            )
            if ws_client is not None and not args.no_ws_preflight:
                try:
                    ws_client.prepare_context(chat_id, lark_chat_id=args.lark_chat_id)
                except Exception:
                    # run_once will reconnect/fallback/report the concrete WS error.
                    pass

            wav = fixed_wav
            if args.text is None and wav is None:
                if i == 1 or not args.reuse_audio:
                    print(f"\n[{i}/{args.runs}] Please speak now (record {args.record_sec:.1f}s)...")
                    try:
                        wav = _record_wav_bytes(args.record_sec)
                    except Exception as e:  # noqa: BLE001
                        print(f"[{i}/{args.runs}] record failed: {e}")
                        rows.append(
                            RunMetric(
                                run_id=i,
                                timestamp_ms=_now_ms(),
                                ok=False,
                                error=str(e),
                                stt_ms=0,
                                l3_ms=0,
                                l3_first_chunk_ms=0,
                                l3_answer_ms=0,
                                l3_server_session_save_ms=0,
                                l3_server_broadcast_ms=0,
                                l3_server_append_final_ms=0,
                                l3_latency_trace="",
                                tts_ms=0,
                                tts_first_audio_ms=0,
                                total_ms=0,
                                recognized_text="",
                                answer_preview="",
                                tts_input="",
                                tts_calls=0,
                                tts_stream_chunks=0,
                                tts_stream_bytes=0,
                                tts_audio_ms=0,
                                tts_transport="",
                                tts_fallback_reason="",
                                routed_text="",
                                l3_transport="",
                                l3_fallback_reason="",
                            )
                        )
                        write_rows(csv_path, jsonl_path, [rows[-1]])
                        continue
                    if args.reuse_audio:
                        fixed_wav = wav
                    print(f"[{i}/{args.runs}] recording done; running STT/L3/TTS...")
                else:
                    wav = fixed_wav

            metric = run_once(
                i,
                jvs_base=args.jvs_base.rstrip("/"),
                l3_base=args.l3_base.rstrip("/"),
                l3_ws=args.l3_ws.rstrip("/"),
                l3_transport=args.l3_transport,
                l3_ws_client=ws_client,
                chat_id=chat_id,
                lark_chat_id=args.lark_chat_id.strip(),
                wav_bytes=wav,
                text_input=args.text,
                voice=args.voice,
                t_stt=args.timeout_stt,
                t_l3=args.timeout_l3,
                t_tts=args.timeout_tts,
                chat_prefix=args.chat_prefix,
                play_audio=args.play,
                tts_stream=not args.no_tts_stream,
                tts_mode=args.tts_mode,
                max_speak_sentences=max(1, int(args.max_speak_sentences)),
                progress=lambda msg, idx=i, total=args.runs: print(f"[{idx}/{total}] {msg}"),
            )
            rows.append(metric)
            write_rows(csv_path, jsonl_path, [metric])

            if metric.ok:
                print(
                    f"[{i}/{args.runs}] OK  STT={metric.stt_ms:.0f}ms  L3={metric.l3_ms:.0f}ms  "
                    f"TTS={metric.tts_ms:.0f}ms  TOTAL={metric.total_ms:.0f}ms  "
                    f"TFA={metric.tts_first_audio_ms:.0f}ms  AUDIO={metric.tts_audio_ms}ms  "
                    f"CALLS={metric.tts_calls} STREAM_CHUNKS={metric.tts_stream_chunks}  "
                    f"VIA={metric.l3_transport or '-'} FC={metric.l3_first_chunk_ms:.0f}ms  "
                    f"ANS={metric.l3_answer_ms:.0f}ms"
                )
                if metric.l3_latency_trace:
                    print(f"         l3_trace={metric.l3_latency_trace[:160]}")
                if metric.l3_fallback_reason:
                    print(f"         l3_fallback={metric.l3_fallback_reason[:160]}")
                if metric.tts_transport:
                    print(f"         tts_transport={metric.tts_transport[:160]}")
                if metric.tts_fallback_reason:
                    print(f"         tts_fallback={metric.tts_fallback_reason[:160]}")
            else:
                print(f"[{i}/{args.runs}] FAIL {metric.error}")
            print_live_summary(rows)

            if i < args.runs and args.interval_sec > 0:
                time.sleep(args.interval_sec)
    finally:
        if ws_client is not None:
            ws_client.close()

    ok_count = len([r for r in rows if r.ok])
    print("\n=== Done ===")
    print(f"Success {ok_count}/{len(rows)}. Result files:")
    print(f"- {csv_path}")
    print(f"- {jsonl_path}")
    return 0 if ok_count > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())



