#!/usr/bin/env python3
"""批量生成唤醒口头确认（Verbal ACK）预渲染 WAV，供 Rust wake_pipeline 播放。

用法:
  python scripts/gen_wake_ack_wavs.py --jvs http://127.0.0.1:18982

默认输出: clients/desktop/public/audio/wake_ack/
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "clients" / "desktop" / "public" / "audio" / "wake_ack"
DEFAULT_TTS_SPEED = 1.4

def localappdata_out() -> Path | None:
    base = os.environ.get("LOCALAPPDATA", "").strip()
    if not base:
        return None
    return Path(base) / "jachin" / "desktop" / "audio" / "wake_ack"

PRESETS: dict[str, str] = {
    "im_here": "我在",
    "yes": "嗯",
    "how_can_i_help": "有什么可以帮你",
    "please_say": "请说",
}


def synthesize_wav(jvs_base: str, text: str) -> bytes:
    import urllib.request

    url = f"{jvs_base.rstrip('/')}/v1/tts/synthesize"
    body = json.dumps({"text": text, "speed": DEFAULT_TTS_SPEED, "kind": "cue"}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate wake verbal ACK WAV files via JVS")
    parser.add_argument("--jvs", default="http://127.0.0.1:18982", help="JVS base URL")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output directory")
    parser.add_argument(
        "--phrases",
        default="",
        help="Optional comma-separated id:text pairs, e.g. im_here:我在,yes:嗯",
    )
    args = parser.parse_args()

    out_dirs = [args.out]
    la = localappdata_out()
    if la and la != args.out:
        out_dirs.append(la)

    items = dict(PRESETS)
    if args.phrases.strip():
        for part in args.phrases.split(","):
            if ":" in part:
                k, v = part.split(":", 1)
                items[k.strip()] = v.strip()

    ok = 0
    for id_, text in items.items():
        if not text:
            continue
        wav = None
        try:
            wav = synthesize_wav(args.jvs, text)
        except Exception as e:
            print(f"[fail] {id_}: {e}")
            continue
        for out_dir in out_dirs:
            out_dir.mkdir(parents=True, exist_ok=True)
            dest = out_dir / f"{id_}.wav"
            dest.write_bytes(wav)
            print(f"[ok] {dest} ({len(wav)} bytes) «{text}»")
        ok += 1

    if ok == 0:
        print("No WAV generated. Is JVS running on", args.jvs, "?")
        return 1
    print(f"Done. {ok} preset(s) → {len(out_dirs)} dir(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
