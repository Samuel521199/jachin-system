from __future__ import annotations

import argparse
import io
import json
import math
import multiprocessing as mp
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
JVS_DEFAULT_BASE = "http://127.0.0.1:18982"


def perf_ms() -> float:
    return time.perf_counter() * 1000.0


def make_probe_wav(duration_sec: float = 1.2, sample_rate: int = 16000) -> bytes:
    """Generate a small valid WAV. It is for latency/timeout diagnosis, not ASR quality."""

    frames = bytearray()
    total = max(1, int(duration_sec * sample_rate))
    for i in range(total):
        # Low-volume two-tone signal to avoid an all-silence fast path.
        sample = int(900 * math.sin(2 * math.pi * 440 * i / sample_rate))
        sample += int(500 * math.sin(2 * math.pi * 880 * i / sample_rate))
        frames += int(sample).to_bytes(2, "little", signed=True)

    out = io.BytesIO()
    with wave.open(out, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(frames))
    return out.getvalue()


def read_audio(path: Path | None) -> tuple[bytes, str]:
    if path is None:
        return make_probe_wav(), "probe.wav"
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return path.read_bytes(), path.name


def http_get_json(url: str, timeout: float) -> tuple[int, Any, float]:
    started = perf_ms()
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        body = resp.read()
        status = int(resp.status)
    elapsed = perf_ms() - started
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        data = body.decode("utf-8", errors="replace")
    return status, data, elapsed


def multipart_body(field_name: str, filename: str, data: bytes) -> tuple[bytes, str]:
    boundary = f"----JachinSttDiag{int(time.time() * 1000)}"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
        "Content-Type: audio/wav\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")
    return head + data + tail, f"multipart/form-data; boundary={boundary}"


def http_post_stt(base: str, wav: bytes, filename: str, timeout: float) -> dict[str, Any]:
    body, content_type = multipart_body("audio", filename, wav)
    req = urllib.request.Request(
        f"{base.rstrip('/')}/v1/stt/transcribe",
        data=body,
        method="POST",
        headers={"Content-Type": content_type, "Content-Length": str(len(body))},
    )
    started = perf_ms()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        status = int(resp.status)
    elapsed = perf_ms() - started
    parsed = json.loads(raw.decode("utf-8", errors="replace"))
    return {"ok": True, "status": status, "elapsed_ms": round(elapsed, 1), "json": parsed}


def run_jvs_http_probe(args: argparse.Namespace, wav: bytes, filename: str) -> int:
    base = args.base.rstrip("/")
    print(f"[JVS] base={base}")
    print(f"[Audio] file={filename} bytes={len(wav)}")

    try:
        status, data, elapsed = http_get_json(f"{base}/health", timeout=args.health_timeout)
        print(f"[Health before] ok status={status} elapsed_ms={elapsed:.1f}")
        print(json.dumps(data, ensure_ascii=False, indent=2)[:3000])
    except Exception as exc:
        print(f"[Health before] FAILED {type(exc).__name__}: {exc}")
        return 2

    result_box: dict[str, Any] = {}

    def _stt_worker() -> None:
        try:
            result_box["result"] = http_post_stt(base, wav, filename, timeout=args.stt_timeout)
        except Exception as exc:
            result_box["error"] = {
                "type": type(exc).__name__,
                "message": str(exc),
            }

    print(f"[STT] POST /v1/stt/transcribe timeout={args.stt_timeout}s")
    started = perf_ms()
    thread = threading.Thread(target=_stt_worker, daemon=True)
    thread.start()

    if args.health_during:
        time.sleep(args.health_during_delay)
        try:
            status, data, elapsed = http_get_json(f"{base}/health", timeout=args.health_during_timeout)
            print(f"[Health during STT] ok status={status} elapsed_ms={elapsed:.1f}")
            print(
                "  verdict=JVS event loop is still responsive while STT is running."
            )
        except Exception as exc:
            print(
                f"[Health during STT] FAILED after {args.health_during_timeout}s "
                f"{type(exc).__name__}: {exc}"
            )
            print(
                "  verdict=JVS is likely blocked by the synchronous STT call; "
                "this matches frontend stuck at stt.jvs_health_check."
            )

    thread.join(timeout=args.stt_timeout + 1.0)
    total = perf_ms() - started
    if thread.is_alive():
        print(f"[STT] HARD HANG total_ms={total:.1f}; client thread still waiting")
        return 3

    if "error" in result_box:
        err = result_box["error"]
        print(f"[STT] FAILED total_ms={total:.1f} {err['type']}: {err['message']}")
        return 4

    result = result_box.get("result") or {}
    print(f"[STT] ok elapsed_ms={result.get('elapsed_ms')} total_ms={total:.1f}")
    payload = result.get("json") or {}
    print(json.dumps(payload, ensure_ascii=False, indent=2)[:5000])
    text = str(payload.get("text") or "")
    if text.startswith("[STT error]") or text.startswith("【STT错误】"):
        print("[Verdict] JVS returned an STT error payload.")
        return 5
    print("[Verdict] JVS HTTP STT returned normally.")
    return 0


def _direct_cloud_child(audio: bytes, q: "mp.Queue[dict[str, Any]]") -> None:
    try:
        sys.path.insert(0, str(ROOT / "voice_server"))
        from config import load_config
        from services.cloud_stt_service import CloudSttService

        cfg = load_config()
        svc = CloudSttService(
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
        started = perf_ms()
        result = svc.transcribe(audio)
        q.put(
            {
                "ok": True,
                "elapsed_ms": round(perf_ms() - started, 1),
                "text": result.text,
                "raw_text": result.raw_text,
                "backend": result.backend,
                "confidence": result.confidence,
                "hotword_status": result.hotword_status,
                "hotword_count": result.hotword_count,
            }
        )
    except Exception as exc:
        q.put({"ok": False, "error_type": type(exc).__name__, "error": str(exc)})


def run_direct_cloud_probe(args: argparse.Namespace, wav: bytes) -> int:
    print(f"[Direct Cloud] child timeout={args.direct_timeout}s")
    os.environ["JACHIN_STT_TIMEOUT_SEC"] = str(args.direct_timeout)
    q: mp.Queue[dict[str, Any]] = mp.Queue()
    proc = mp.Process(target=_direct_cloud_child, args=(wav, q), daemon=True)
    started = perf_ms()
    proc.start()
    proc.join(timeout=args.direct_timeout + 1.0)
    elapsed = perf_ms() - started
    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=2.0)
        print(f"[Direct Cloud] HARD TIMEOUT elapsed_ms={elapsed:.1f}")
        print("  verdict=CloudSttService/DashScope SDK call did not return in time.")
        return 6
    if q.empty():
        print(f"[Direct Cloud] child exited without result elapsed_ms={elapsed:.1f} exitcode={proc.exitcode}")
        return 7
    result = q.get()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("ok"):
        return 8
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diagnose whether JVS cloud STT/DashScope is timing out or blocking the voice entry."
    )
    parser.add_argument("--base", default=os.environ.get("JACHIN_JVS_BASE", JVS_DEFAULT_BASE))
    parser.add_argument("--audio", type=Path, help="WAV/MP3/OGG/FLAC file to upload. Defaults to a generated probe WAV.")
    parser.add_argument("--stt-timeout", type=float, default=25.0)
    parser.add_argument("--health-timeout", type=float, default=3.0)
    parser.add_argument("--health-during", action="store_true", default=True)
    parser.add_argument("--no-health-during", dest="health_during", action="store_false")
    parser.add_argument("--health-during-delay", type=float, default=1.0)
    parser.add_argument("--health-during-timeout", type=float, default=3.0)
    parser.add_argument("--direct-cloud", action="store_true", help="Also call CloudSttService directly in a child process.")
    parser.add_argument("--direct-timeout", type=float, default=25.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    wav, filename = read_audio(args.audio)
    code = run_jvs_http_probe(args, wav, filename)
    if args.direct_cloud:
        direct_code = run_direct_cloud_probe(args, wav)
        if code == 0:
            code = direct_code
    return code


if __name__ == "__main__":
    raise SystemExit(main())
