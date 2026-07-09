#!/usr/bin/env python3
"""Generate pre-rendered companion cue WAV files from the desktop cue manifest.

Usage:
  python scripts/gen_companion_cue_wavs.py --jvs http://127.0.0.1:18982
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "clients" / "desktop" / "public" / "audio" / "companion_cues" / "manifest.json"
DEFAULT_SPEED = 1.0


def synthesize_wav(jvs_base: str, text: str, *, voice: str, speed: float, timeout: float) -> bytes:
    url = f"{jvs_base.rstrip('/')}/v1/tts/synthesize"
    body = json.dumps(
        {
            "text": text,
            "voice": voice,
            "speed": speed,
            "kind": "cue",
            "session_id": "companion-cue-prewarm",
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate companion cue WAV files via JVS")
    parser.add_argument("--jvs", default="http://127.0.0.1:18982", help="JVS base URL")
    parser.add_argument("--voice", default="zm_053", help="TTS voice id")
    parser.add_argument("--speed", type=float, default=DEFAULT_SPEED, help="TTS speed")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="Cue manifest path")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--skip-existing", action="store_true", help="Do not overwrite existing wav files")
    args = parser.parse_args()

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
            sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass

    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items: list[dict[str, Any]] = list(manifest.get("items") or [])
    out_dir = manifest_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    ok = 0
    skipped = 0
    for item in items:
        text = str(item.get("text") or "").strip()
        filename = str(item.get("file") or "").strip()
        cue_id = str(item.get("id") or filename).strip()
        if not text or not filename:
            continue
        dest = out_dir / filename
        if args.skip_existing and dest.is_file() and dest.stat().st_size > 44:
            print(f"[skip] {cue_id}: {dest.name}")
            skipped += 1
            continue
        try:
            wav = synthesize_wav(args.jvs, text, voice=args.voice, speed=args.speed, timeout=args.timeout)
        except Exception as exc:  # noqa: BLE001
            print(f"[fail] {cue_id}: {exc}")
            continue
        dest.write_bytes(wav)
        print(f"[ok] {cue_id}: {dest} ({len(wav)} bytes) <- {text}")
        ok += 1

    print(f"Done. generated={ok} skipped={skipped} manifest={manifest_path}")
    return 0 if ok or skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
