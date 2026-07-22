from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VOICE_SERVER_DIR = ROOT / "voice_server"
if str(VOICE_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(VOICE_SERVER_DIR))


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _example_audio(sv_dir: Path) -> dict[str, Path]:
    model_examples = sv_dir / "examples"
    base = model_examples if model_examples.is_dir() else VOICE_SERVER_DIR / "examples" / "speaker_verification"
    return {
        "speaker1_a": base / "speaker1_a_cn_16k.wav",
        "speaker1_b": base / "speaker1_b_cn_16k.wav",
        "speaker2_a": base / "speaker2_a_cn_16k.wav",
    }


def _read_audio(path: Path) -> bytes:
    if not path.is_file():
        raise FileNotFoundError(f"missing example audio: {path}")
    return path.read_bytes()


def _write_report(report: dict[str, Any]) -> Path:
    out_dir = ROOT / "output" / "voice_sv_smoke"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"voice_sv_smoke_{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_smoke(repeat: int, threshold: float) -> dict[str, Any]:
    from config import load_config
    from services.sv_service import SvService

    cfg = load_config()
    files = _example_audio(cfg.sv_dir)
    required_model_files = {
        "configuration": cfg.sv_dir / "configuration.json",
        "weights": cfg.sv_dir / "campplus_cn_common.bin",
    }
    model_file_status = {
        name: {
            "path": str(path),
            "exists": path.is_file(),
            "size": path.stat().st_size if path.is_file() else 0,
        }
        for name, path in required_model_files.items()
    }
    examples_status = {
        name: {"path": str(path), "exists": path.is_file(), "size": path.stat().st_size if path.is_file() else 0}
        for name, path in files.items()
    }

    service = SvService(cfg.sv_dir, device=cfg.torch_device, require_gpu=cfg.require_gpu)
    started = time.perf_counter()
    service.warmup()
    warmup_ms = _ms(started)

    report: dict[str, Any] = {
        "ok": False,
        "model_root": str(cfg.model_root),
        "sv_dir": str(cfg.sv_dir),
        "backend": service.backend,
        "ready": service.ready,
        "load_error": service.load_error,
        "device_request": cfg.torch_device,
        "effective_device": service.effective_device,
        "require_gpu": cfg.require_gpu,
        "threshold": threshold,
        "warmup_ms": warmup_ms,
        "model_files": model_file_status,
        "examples": examples_status,
        "runs": [],
    }
    if not service.ready:
        report["reason"] = "sv_model_not_ready"
        return report

    speaker1_a = _read_audio(files["speaker1_a"])
    speaker1_b = _read_audio(files["speaker1_b"])
    speaker2_a = _read_audio(files["speaker2_a"])

    started = time.perf_counter()
    centroid = service.extract_embedding(speaker1_a).tolist()
    report["embedding"] = {"dim": len(centroid), "extract_ms": _ms(started)}

    pass_count = 0
    for i in range(max(1, repeat)):
        same_start = time.perf_counter()
        same = service.verify(speaker1_b, centroid, threshold)
        same_ms = _ms(same_start)
        diff_start = time.perf_counter()
        diff = service.verify(speaker2_a, centroid, threshold)
        diff_ms = _ms(diff_start)
        run_ok = bool(same.is_match and not diff.is_match and same.score > diff.score)
        if run_ok:
            pass_count += 1
        report["runs"].append(
            {
                "index": i + 1,
                "ok": run_ok,
                "same_speaker": {"score": round(same.score, 6), "is_match": same.is_match, "reason": same.reason, "ms": same_ms},
                "different_speaker": {"score": round(diff.score, 6), "is_match": diff.is_match, "reason": diff.reason, "ms": diff_ms},
            }
        )

    report["ok"] = pass_count == max(1, repeat)
    report["pass_count"] = pass_count
    report["repeat"] = max(1, repeat)
    if not report["ok"]:
        report["reason"] = "speaker_verification_unstable"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test Jachin CAM++ speaker verification model.")
    parser.add_argument("--repeat", type=int, default=3, help="repeat same/different verification rounds")
    parser.add_argument("--threshold", type=float, default=0.31, help="speaker verification threshold")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda", "cuda:0"), default=None, help="override JACHIN_VOICE_TORCH_DEVICE")
    parser.add_argument("--require-gpu", action="store_true", help="fail if CUDA is unavailable")
    args = parser.parse_args()
    if args.device:
        import os

        os.environ["JACHIN_VOICE_TORCH_DEVICE"] = args.device
    if args.require_gpu:
        import os

        os.environ["JACHIN_VOICE_REQUIRE_GPU"] = "1"

    report = run_smoke(args.repeat, args.threshold)
    report_path = _write_report(report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nReport: {report_path}")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
