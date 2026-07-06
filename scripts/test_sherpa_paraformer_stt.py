#!/usr/bin/env python3
"""Standalone Sherpa-ONNX STT tester for Paraformer and Zipformer.

This is an experiment-only script. It does not modify or call the production
Jachin voice server. It can test:

- Paraformer baseline transcription
- Zipformer Transducer transcription
- Zipformer hotwords A/B via from_transducer(hotwords_file=...)

Default model directories:
  D:\project\model\sherpa-onnx-paraformer-zh-2024-03-09
  D:\project\model\sherpa-onnx-zipformer-zh-en-2023-11-22

Examples:
  python scripts/test_sherpa_paraformer_stt.py --model-kind zipformer --download --proxy http://127.0.0.1:8800
  python scripts/test_sherpa_paraformer_stt.py --model-kind zipformer --file data/eval_wav/t1_clean/foo.wav --ab-hotwords
  python scripts/test_sherpa_paraformer_stt.py --model-kind zipformer --record 5 --save-wav data/eval_wav/t1_clean/manual.wav --ab-hotwords
  python scripts/test_sherpa_paraformer_stt.py --model-kind paraformer --file data/eval_wav/t1_clean/foo.wav
  python scripts/test_sherpa_paraformer_stt.py --list-devices
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from math import gcd
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL_ROOT = Path(os.getenv("JACHIN_SHERPA_MODEL_ROOT", r"D:\project\model"))
PARAFORMER_REPO_ID = "csukuangfj/sherpa-onnx-paraformer-zh-2024-03-09"
ZIPFORMER_REPO_ID = "csukuangfj/sherpa-onnx-zipformer-zh-en-2023-11-22"
DEFAULT_HOTWORDS = ROOT / "data" / "voice" / "sherpa_hotwords.txt"
SAMPLE_RATE = 16000


@dataclass
class SherpaRun:
    label: str
    text: str
    latency_ms: int
    audio_sec: float
    rtf: float
    hotwords_count: int
    error: str = ""


@dataclass
class SherpaReport:
    model_kind: str
    model_dir: str
    model_files: dict[str, str]
    provider: str
    num_threads: int
    source: str
    audio_sample_rate: int
    audio_sec: float
    hotwords_file: str
    hotwords_score: float
    runs: list[SherpaRun] = field(default_factory=list)


def default_repo_id(model_kind: str) -> str:
    return ZIPFORMER_REPO_ID if model_kind == "zipformer" else PARAFORMER_REPO_ID


def default_model_dir(model_kind: str) -> Path:
    return DEFAULT_MODEL_ROOT / default_repo_id(model_kind).split("/")[-1]


def _require_module(name: str, install_hint: str) -> Any:
    try:
        return __import__(name)
    except ImportError as exc:
        raise SystemExit(f"Missing dependency {name}. Install with: {install_hint}") from exc


def _apply_proxy_env(proxy: str) -> None:
    if not proxy:
        return
    if "://" not in proxy:
        proxy = "http://" + proxy
    os.environ["HTTP_PROXY"] = proxy
    os.environ["HTTPS_PROXY"] = proxy
    os.environ["ALL_PROXY"] = proxy
    print(f"[proxy] {proxy}")


def download_model(repo_id: str, model_dir: Path, *, hf_endpoint: str = "", proxy: str = "", force_download: bool = False) -> Path:
    _apply_proxy_env(proxy)
    _require_module("huggingface_hub", "python -m pip install huggingface_hub")
    from huggingface_hub import snapshot_download

    if hf_endpoint:
        os.environ["HF_ENDPOINT"] = hf_endpoint.rstrip("/")
        print(f"[hf-endpoint] {os.environ['HF_ENDPOINT']}")
    model_dir.parent.mkdir(parents=True, exist_ok=True)
    print(f"[download] repo={repo_id}")
    print(f"[download] target={model_dir}")
    path = snapshot_download(repo_id=repo_id, local_dir=str(model_dir), resume_download=True, force_download=force_download)
    print(f"[download] done: {path}")
    return Path(path)


def _find_first(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.is_file():
            return path
    return None


def find_paraformer_files(model_dir: Path) -> dict[str, Path]:
    if not model_dir.exists():
        raise FileNotFoundError(f"Model directory not found: {model_dir}")
    tokens = _find_first([model_dir / "tokens.txt", *sorted(model_dir.rglob("tokens.txt"))])
    model = _find_first([
        model_dir / "model.int8.onnx",
        model_dir / "model.onnx",
        model_dir / "model_quant.onnx",
        *sorted(model_dir.rglob("model.int8.onnx")),
        *sorted(model_dir.rglob("model.onnx")),
        *sorted(model_dir.rglob("*.onnx")),
    ])
    if tokens is None:
        raise FileNotFoundError(f"tokens.txt not found under: {model_dir}")
    if model is None:
        raise FileNotFoundError(f"Paraformer ONNX model not found under: {model_dir}")
    return {"model": model, "tokens": tokens}


def _pick_zipformer_component(model_dir: Path, name: str) -> Path | None:
    patterns = [
        f"{name}*.int8.onnx",
        f"{name}*.onnx",
        f"*{name}*.int8.onnx",
        f"*{name}*.onnx",
    ]
    for pattern in patterns:
        matches = sorted(model_dir.rglob(pattern))
        if matches:
            return matches[0]
    return None


def find_zipformer_files(model_dir: Path) -> dict[str, Path]:
    if not model_dir.exists():
        raise FileNotFoundError(f"Model directory not found: {model_dir}")
    tokens = _find_first([model_dir / "tokens.txt", *sorted(model_dir.rglob("tokens.txt"))])
    encoder = _pick_zipformer_component(model_dir, "encoder")
    decoder = _pick_zipformer_component(model_dir, "decoder")
    joiner = _pick_zipformer_component(model_dir, "joiner")
    missing = [name for name, value in {"tokens": tokens, "encoder": encoder, "decoder": decoder, "joiner": joiner}.items() if value is None]
    if missing:
        raise FileNotFoundError(f"Missing Zipformer files under {model_dir}: {', '.join(missing)}")
    out = {"encoder": encoder, "decoder": decoder, "joiner": joiner, "tokens": tokens}  # type: ignore[dict-item]
    bpe_vocab = _find_first([model_dir / "bpe.vocab", *sorted(model_dir.rglob("bpe.vocab"))])
    if bpe_vocab is not None:
        out["bpe_vocab"] = bpe_vocab
    return out


def ensure_default_hotwords(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    words = [
        "LARK :8.0",
        "Lark :8.0",
        "飞书 :6.0",
        "VIVIAN :8.0",
        "Vivian :8.0",
        "VIVI :5.0",
        "vivi :5.0",
        "薇薇安 :8.0",
        "微微安 :8.0",
        "JACHIN :5.0",
        "Jachin :5.0",
        "FEISHU :5.0",
        "Feishu :5.0",
        "VS CODE :4.0",
        "VS Code :4.0",
        "CHROME :4.0",
        "Chrome :4.0",
        "CODEX :4.0",
        "Codex :4.0",
    ]
    if path.exists():
        existing = [line.strip() for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines()]
        if all("?" not in line for line in existing if line):
            return
    path.write_text("\n".join(words) + "\n", encoding="utf-8")
    print(f"[hotwords] wrote default hotwords: {path}")


def read_hotwords(path: Path | None) -> tuple[str, int]:
    if path is None or not path.is_file():
        return "", 0
    words = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()]
    words = [w for w in words if w and not w.startswith("#")]
    return "\n".join(words), len(words)


def load_audio(path: Path) -> tuple[Any, int]:
    np = _require_module("numpy", "python -m pip install numpy soundfile scipy")
    sf = _require_module("soundfile", "python -m pip install soundfile")
    data, sample_rate = sf.read(str(path), dtype="float32", always_2d=False)
    audio = np.asarray(data, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sample_rate != SAMPLE_RATE:
        try:
            from scipy.signal import resample_poly

            g = gcd(int(sample_rate), SAMPLE_RATE)
            audio = resample_poly(audio, SAMPLE_RATE // g, int(sample_rate) // g).astype(np.float32)
        except Exception:
            target_len = max(1, int(len(audio) * SAMPLE_RATE / sample_rate))
            x_old = np.linspace(0.0, 1.0, len(audio), dtype=np.float64)
            x_new = np.linspace(0.0, 1.0, target_len, dtype=np.float64)
            audio = np.interp(x_new, x_old, audio.astype(np.float64)).astype(np.float32)
        print(f"[audio] resampled {sample_rate} Hz -> {SAMPLE_RATE} Hz")
        sample_rate = SAMPLE_RATE
    return audio, int(sample_rate)


def record_wav_bytes(duration_sec: float, device: int | None) -> bytes:
    sd = _require_module("sounddevice", "python -m pip install sounddevice")
    sf = _require_module("soundfile", "python -m pip install soundfile")
    duration_sec = max(0.5, min(float(duration_sec), 120.0))
    frames = int(duration_sec * SAMPLE_RATE)
    print(f"[record] recording {duration_sec:.1f}s @ {SAMPLE_RATE} Hz")
    audio = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1, dtype="float32", device=device)
    sd.wait()
    buf = io.BytesIO()
    sf.write(buf, audio, SAMPLE_RATE, subtype="PCM_16", format="WAV")
    return buf.getvalue()


def record_ptt_bytes(device: int | None) -> bytes:
    np = _require_module("numpy", "python -m pip install numpy")
    sd = _require_module("sounddevice", "python -m pip install sounddevice")
    sf = _require_module("soundfile", "python -m pip install soundfile")
    chunks: list[Any] = []

    def callback(indata, _frames, _time_info, status) -> None:
        if status:
            print(f"[record] {status}", file=sys.stderr)
        chunks.append(indata.copy())

    print("[ptt] Press Enter to start recording")
    input()
    print("[ptt] Recording. Press Enter to stop")
    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        device=device,
        blocksize=1024,
        callback=callback,
    ):
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass
    if not chunks:
        return b""
    audio = np.concatenate(chunks, axis=0)
    buf = io.BytesIO()
    sf.write(buf, audio, SAMPLE_RATE, subtype="PCM_16", format="WAV")
    return buf.getvalue()


def wav_bytes_to_audio(wav_bytes: bytes) -> tuple[Any, int]:
    sf = _require_module("soundfile", "python -m pip install soundfile")
    import numpy as np

    data, sample_rate = sf.read(io.BytesIO(wav_bytes), dtype="float32", always_2d=False)
    audio = np.asarray(data, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio, int(sample_rate)


def build_paraformer(model_dir: Path, *, num_threads: int, provider: str, debug: bool):
    sherpa_onnx = _require_module("sherpa_onnx", "python -m pip install sherpa-onnx")
    files = find_paraformer_files(model_dir)
    print(f"[model] paraformer={files['model']}")
    print(f"[model] tokens={files['tokens']}")
    t0 = time.perf_counter()
    recognizer = sherpa_onnx.OfflineRecognizer.from_paraformer(
        paraformer=str(files["model"]),
        tokens=str(files["tokens"]),
        num_threads=num_threads,
        sample_rate=SAMPLE_RATE,
        feature_dim=80,
        decoding_method="greedy_search",
        provider=provider,
        debug=debug,
    )
    print(f"[model] loaded in {(time.perf_counter() - t0) * 1000:.0f} ms")
    return recognizer, files


def build_zipformer(model_dir: Path, *, num_threads: int, provider: str, debug: bool, hotwords_file: Path | None, hotwords_score: float):
    sherpa_onnx = _require_module("sherpa_onnx", "python -m pip install sherpa-onnx")
    files = find_zipformer_files(model_dir)
    print(f"[model] encoder={files['encoder']}")
    print(f"[model] decoder={files['decoder']}")
    print(f"[model] joiner={files['joiner']}")
    print(f"[model] tokens={files['tokens']}")
    t0 = time.perf_counter()
    recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
        encoder=str(files["encoder"]),
        decoder=str(files["decoder"]),
        joiner=str(files["joiner"]),
        tokens=str(files["tokens"]),
        num_threads=num_threads,
        sample_rate=SAMPLE_RATE,
        feature_dim=80,
        decoding_method="modified_beam_search" if hotwords_file else "greedy_search",
        max_active_paths=4,
        hotwords_file=str(hotwords_file or ""),
        hotwords_score=hotwords_score,
        modeling_unit="bpe" if hotwords_file and files.get("bpe_vocab") else "cjkchar",
        bpe_vocab=str(files.get("bpe_vocab") or ""),
        provider=provider,
        debug=debug,
    )
    print(f"[model] loaded in {(time.perf_counter() - t0) * 1000:.0f} ms")
    return recognizer, files


def transcribe_once(recognizer: Any, audio: Any, sample_rate: int, *, label: str, hotwords_count: int) -> SherpaRun:
    audio_sec = len(audio) / max(sample_rate, 1)
    try:
        stream = recognizer.create_stream()
        stream.accept_waveform(sample_rate, audio)
        t0 = time.perf_counter()
        recognizer.decode_stream(stream)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        text = str(stream.result.text or "").strip()
        rtf = (latency_ms / 1000.0) / max(audio_sec, 0.001)
        return SherpaRun(
            label=label,
            text=text,
            latency_ms=latency_ms,
            audio_sec=round(audio_sec, 3),
            rtf=round(rtf, 3),
            hotwords_count=hotwords_count,
        )
    except Exception as exc:
        return SherpaRun(
            label=label,
            text="",
            latency_ms=0,
            audio_sec=round(audio_sec, 3),
            rtf=0.0,
            hotwords_count=hotwords_count,
            error=str(exc),
        )


def list_devices() -> int:
    sd = _require_module("sounddevice", "python -m pip install sounddevice")
    print(sd.query_devices())
    print(f"\nDefault input device: {sd.default.device[0]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Test Sherpa-ONNX STT models without touching Jachin runtime")
    parser.add_argument("--model-kind", choices=["paraformer", "zipformer"], default="paraformer")
    parser.add_argument("--model-dir", type=Path, default=None)
    parser.add_argument("--repo-id", default="")
    parser.add_argument("--download", action="store_true", help="Download model to --model-dir with huggingface_hub")
    parser.add_argument("--force-download", action="store_true", help="Force re-download model files")
    parser.add_argument("--proxy", default=os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or "", help="Proxy, e.g. http://127.0.0.1:8800")
    parser.add_argument("--hf-endpoint", default=os.getenv("HF_ENDPOINT", ""), help="Optional Hugging Face mirror endpoint")
    parser.add_argument("--file", type=Path, help="WAV file to transcribe")
    parser.add_argument("--record", type=float, help="Record N seconds from microphone")
    parser.add_argument("--ptt", action="store_true", help="Press Enter to start/stop recording")
    parser.add_argument("--device", type=int, default=None)
    parser.add_argument("--save-wav", type=Path)
    parser.add_argument("--hotwords-file", type=Path, default=DEFAULT_HOTWORDS)
    parser.add_argument("--hotwords-score", type=float, default=4.0)
    parser.add_argument("--no-hotwords", action="store_true")
    parser.add_argument("--ab-hotwords", action="store_true", help="For zipformer: run no-hotwords and with-hotwords on same audio")
    parser.add_argument("--num-threads", type=int, default=max(1, min(4, os.cpu_count() or 1)))
    parser.add_argument("--provider", default="cpu", choices=["cpu", "cuda", "coreml"])
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--list-devices", action="store_true")
    args = parser.parse_args()

    if args.list_devices:
        return list_devices()

    model_dir = args.model_dir or default_model_dir(args.model_kind)
    repo_id = args.repo_id or default_repo_id(args.model_kind)

    if args.download:
        try:
            download_model(repo_id, model_dir, hf_endpoint=args.hf_endpoint, proxy=args.proxy, force_download=args.force_download)
        except Exception as exc:
            print(f"[download failed] {exc}", file=sys.stderr)
            print(f"Put the model files under: {model_dir}", file=sys.stderr)
            return 2

    hotwords_text = ""
    hotwords_count = 0
    hotwords_file: Path | None = None
    if not args.no_hotwords:
        ensure_default_hotwords(args.hotwords_file)
        hotwords_text, hotwords_count = read_hotwords(args.hotwords_file)
        hotwords_file = args.hotwords_file if hotwords_text else None

    source = ""
    if args.file:
        source = str(args.file)
        audio, sample_rate = load_audio(args.file)
    else:
        if args.ptt or args.record is None:
            wav_bytes = record_ptt_bytes(args.device)
            source = "microphone-ptt"
        else:
            wav_bytes = record_wav_bytes(args.record, args.device)
            source = f"microphone-{args.record:.1f}s"
        if not wav_bytes:
            print("[error] no audio captured", file=sys.stderr)
            return 1
        if args.save_wav:
            args.save_wav.parent.mkdir(parents=True, exist_ok=True)
            args.save_wav.write_bytes(wav_bytes)
            print(f"[save] {args.save_wav}")
        audio, sample_rate = wav_bytes_to_audio(wav_bytes)

    runs: list[SherpaRun] = []
    files: dict[str, Path]
    if args.model_kind == "paraformer":
        recognizer, files = build_paraformer(model_dir, num_threads=args.num_threads, provider=args.provider, debug=args.debug)
        if hotwords_file and args.ab_hotwords:
            print("[hotwords] paraformer note: Sherpa-ONNX Paraformer does not support contextual biasing; decoding baseline only.")
        runs.append(transcribe_once(recognizer, audio, sample_rate, label="paraformer_without_hotwords", hotwords_count=0))
    else:
        recognizer_base, files = build_zipformer(
            model_dir,
            num_threads=args.num_threads,
            provider=args.provider,
            debug=args.debug,
            hotwords_file=None,
            hotwords_score=args.hotwords_score,
        )
        runs.append(transcribe_once(recognizer_base, audio, sample_rate, label="zipformer_without_hotwords", hotwords_count=0))
        if args.ab_hotwords and hotwords_file:
            recognizer_hot, _ = build_zipformer(
                model_dir,
                num_threads=args.num_threads,
                provider=args.provider,
                debug=args.debug,
                hotwords_file=hotwords_file,
                hotwords_score=args.hotwords_score,
            )
            runs.append(transcribe_once(recognizer_hot, audio, sample_rate, label="zipformer_with_hotwords", hotwords_count=hotwords_count))

    report = SherpaReport(
        model_kind=args.model_kind,
        model_dir=str(model_dir),
        model_files={k: str(v) for k, v in files.items()},
        provider=args.provider,
        num_threads=args.num_threads,
        source=source,
        audio_sample_rate=sample_rate,
        audio_sec=round(len(audio) / max(sample_rate, 1), 3),
        hotwords_file=str(hotwords_file or ""),
        hotwords_score=args.hotwords_score,
        runs=runs,
    )

    print()
    print(f"-- Sherpa-ONNX {args.model_kind} STT --")
    for run in runs:
        print(f"[{run.label}]")
        print(f"  text          : {run.text or '(empty)'}")
        print(f"  latency_ms    : {run.latency_ms}")
        print(f"  audio_sec     : {run.audio_sec}")
        print(f"  rtf           : {run.rtf}")
        print(f"  hotwords_count: {run.hotwords_count}")
        if run.error:
            print(f"  error         : {run.error}")
    if args.ab_hotwords and len(runs) == 2:
        changed = runs[0].text != runs[1].text
        print(f"[A/B] hotwords_changed_text: {changed}")
        print(f"[A/B] hotwords_supported: {args.model_kind == 'zipformer'}")

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[report] {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
