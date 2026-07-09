#!/usr/bin/env python3
"""Record repeated pronunciations for Jachin STT hotwords.

The output is a WAV corpus plus a JSONL manifest. It is meant for discovering
how the current ASR hears each hotword, so we can later add safe
phonetic_aliases and sync them into the cloud/native hotword path.

Examples:
  python scripts/record_hotword_pronunciation_wavs.py
  python scripts/record_hotword_pronunciation_wavs.py --repeats 3
  python scripts/record_hotword_pronunciation_wavs.py --mode canonical --repeats 5
  python scripts/record_hotword_pronunciation_wavs.py --kind contacts --limit 20
  python scripts/record_hotword_pronunciation_wavs.py --list-devices
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SAMPLE_RATE = 16000
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LEXICON = ROOT / "data" / "voice" / "domain_lexicon.json"
DEFAULT_STT_HOTWORDS = ROOT / "data" / "voice" / "stt_hotwords.json"
DEFAULT_OUT_DIR = ROOT / "data" / "eval_wav" / "hotword_pronunciation"
DEFAULT_MANIFEST = DEFAULT_OUT_DIR / "manifest.jsonl"


@dataclass(frozen=True)
class PromptTerm:
    id_key: str
    kind: str
    canonical: str
    spoken: str
    role: str
    source: str


def configure_console() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _require_module(name: str, install_hint: str) -> Any:
    try:
        return __import__(name)
    except ImportError as exc:
        raise SystemExit(f"Missing dependency {name}. Install with: {install_hint}") from exc


def list_devices() -> int:
    sd = _require_module("sounddevice", "python -m pip install sounddevice")
    print(sd.query_devices())
    print(f"\nDefault input device: {sd.default.device[0]}")
    return 0


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def clean_spoken(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", text):
        return ""
    return text[:80]


def slugify(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        text = "term"
    return text[:48]


def _add_term(out: list[PromptTerm], seen: set[tuple[str, str, str]], *, kind: str, canonical: str, spoken: str, role: str, source: str) -> None:
    spoken = clean_spoken(spoken)
    canonical = clean_spoken(canonical)
    if not spoken or not canonical:
        return
    key = (kind, canonical.lower(), spoken.lower())
    if key in seen:
        return
    seen.add(key)
    out.append(
        PromptTerm(
            id_key=f"{kind}_{slugify(canonical)}_{slugify(role)}_{slugify(spoken)}",
            kind=kind,
            canonical=canonical,
            spoken=spoken,
            role=role,
            source=source,
        )
    )


def build_terms(args: argparse.Namespace) -> list[PromptTerm]:
    data = load_json(args.lexicon)
    stt_hotwords = load_json(args.stt_hotwords)
    terms: list[PromptTerm] = []
    seen: set[tuple[str, str, str]] = set()

    selected_kinds = {"apps", "contacts", "projects"} if args.kind == "all" else {args.kind}
    for kind in ("apps", "contacts", "projects"):
        if kind not in selected_kinds:
            continue
        for canonical, meta in (data.get(kind) or {}).items():
            if isinstance(meta, dict) and meta.get("active", True) is False:
                continue
            _add_term(terms, seen, kind=kind, canonical=canonical, spoken=canonical, role="canonical", source=str(args.lexicon))
            if args.mode in {"all", "aliases"} and isinstance(meta, dict):
                for alias in meta.get("aliases") or []:
                    _add_term(terms, seen, kind=kind, canonical=canonical, spoken=alias, role="alias", source=str(args.lexicon))
            if args.mode in {"all", "phonetic"} and isinstance(meta, dict):
                for alias in meta.get("phonetic_aliases") or []:
                    _add_term(terms, seen, kind=kind, canonical=canonical, spoken=alias, role="phonetic_alias", source=str(args.lexicon))

    if args.include_stt_hotwords:
        for item in stt_hotwords.get("hotwords") or []:
            if isinstance(item, dict):
                word = clean_spoken(item.get("word") or item.get("name") or item.get("canonical"))
            else:
                word = clean_spoken(item)
            if word:
                _add_term(terms, seen, kind="hotwords", canonical=word, spoken=word, role="stt_hotword", source=str(args.stt_hotwords))

    if args.only:
        needles = [x.strip().lower() for x in re.split(r"[,;]+", args.only) if x.strip()]
        terms = [
            term
            for term in terms
            if any(n in term.canonical.lower() or n in term.spoken.lower() or n in term.kind.lower() for n in needles)
        ]
    if args.limit > 0:
        terms = terms[: args.limit]
    return terms


def audio_stats(audio: Any) -> dict[str, float]:
    np = _require_module("numpy", "python -m pip install numpy")
    flat = np.asarray(audio, dtype=np.float32).reshape(-1)
    if flat.size == 0:
        return {"duration_sec": 0.0, "peak": 0.0, "rms": 0.0}
    peak = float(np.max(np.abs(flat)))
    rms = float(np.sqrt(np.mean(np.square(flat))))
    return {
        "duration_sec": round(float(flat.size / SAMPLE_RATE), 3),
        "peak": round(peak, 4),
        "rms": round(rms, 4),
    }


def record_ptt(device: int | None) -> Any:
    np = _require_module("numpy", "python -m pip install numpy")
    sd = _require_module("sounddevice", "python -m pip install sounddevice")
    chunks: list[Any] = []

    def callback(indata, _frames, _time_info, status) -> None:
        if status:
            print(f"[record] {status}", file=sys.stderr)
        chunks.append(indata.copy())

    print("[ptt] 按 Enter 开始录音")
    input()
    print("[ptt] 正在录音。读完后按 Enter 停止")
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
        return np.zeros((0, 1), dtype=np.float32)
    return np.concatenate(chunks, axis=0)


def record_fixed(duration_sec: float, device: int | None) -> Any:
    sd = _require_module("sounddevice", "python -m pip install sounddevice")
    duration_sec = max(0.4, min(float(duration_sec), 30.0))
    frames = int(duration_sec * SAMPLE_RATE)
    print(f"[record] 录音 {duration_sec:.1f}s @ {SAMPLE_RATE} Hz")
    audio = sd.rec(frames, samplerate=SAMPLE_RATE, channels=1, dtype="float32", device=device)
    sd.wait()
    return audio


def write_wav(path: Path, audio: Any) -> None:
    sf = _require_module("soundfile", "python -m pip install soundfile")
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), audio, SAMPLE_RATE, subtype="PCM_16", format="WAV")


def append_manifest(manifest: Path, record: dict[str, Any]) -> None:
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def next_sample_id(out_dir: Path, term: PromptTerm, repeat_index: int) -> str:
    base = f"hp_{term.id_key}_{repeat_index:03d}"
    sample_id = base
    suffix = 2
    while (out_dir / f"{sample_id}.wav").exists():
        sample_id = f"{base}_{suffix}"
        suffix += 1
    return sample_id


def main() -> int:
    configure_console()
    parser = argparse.ArgumentParser(description="Record repeated pronunciations for Jachin STT hotwords")
    parser.add_argument("--lexicon", type=Path, default=DEFAULT_LEXICON)
    parser.add_argument("--stt-hotwords", type=Path, default=DEFAULT_STT_HOTWORDS)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--repeats", type=int, default=3, help="每个热词录几遍")
    parser.add_argument("--mode", choices=["canonical", "aliases", "phonetic", "all"], default="all", help="录哪些词：canonical 只录标准名；all 录标准名+别名+近音别名")
    parser.add_argument("--kind", choices=["all", "apps", "contacts", "projects"], default="all")
    parser.add_argument("--include-stt-hotwords", action="store_true", help="同时录 data/voice/stt_hotwords.json 中的独立热词")
    parser.add_argument("--only", default="", help="只录匹配这些关键字的词，逗号分隔，例如 Vivian,Neil,Lark")
    parser.add_argument("--limit", type=int, default=0, help="最多录多少个词，0 表示不限制")
    parser.add_argument("--duration", type=float, default=0.0, help="固定录音秒数；默认按 Enter 开始/停止")
    parser.add_argument("--device", type=int, default=None)
    parser.add_argument("--list-devices", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="只展示录制计划，不录音")
    parser.add_argument("--yes", action="store_true", help="不逐条询问是否跳过")
    args = parser.parse_args()

    if args.list_devices:
        return list_devices()

    repeats = max(1, int(args.repeats))
    terms = build_terms(args)
    if not terms:
        raise SystemExit("没有找到可录制的热词。请检查词库路径或 --only/--kind 参数。")

    total = len(terms) * repeats
    print(f"[lexicon] {args.lexicon}")
    print(f"[out] {args.out_dir}")
    print(f"[manifest] {args.manifest}")
    print(f"[plan] terms={len(terms)} repeats={repeats} total_wavs={total}")
    print("[tip] 每条只读屏幕上的热词本身，不要加“打开/找到/帮我”等命令词。")
    print("[tip] 建议每次读法稍微自然变化一点：正常读、快一点、慢一点。")
    if args.dry_run:
        print()
        print("[dry-run] 前 30 条录制项：")
        for index, term in enumerate(terms[:30], 1):
            print(f"{index:02d}. {term.kind} | {term.canonical} | {term.role} | {term.spoken}")
        if len(terms) > 30:
            print(f"... 还有 {len(terms) - 30} 条")
        return 0

    recorded = 0
    try:
        for index, term in enumerate(terms, 1):
            for repeat in range(1, repeats + 1):
                print()
                print(f"[{index}/{len(terms)} repeat {repeat}/{repeats}]")
                print(f"类型      : {term.kind}")
                print(f"标准名    : {term.canonical}")
                print(f"请朗读    : {term.spoken}")
                print(f"来源角色  : {term.role}")
                if not args.yes:
                    answer = input("[Enter=录这条, s=跳过, q=退出] ").strip().lower()
                    if answer == "q":
                        raise KeyboardInterrupt
                    if answer == "s":
                        continue

                audio = record_fixed(args.duration, args.device) if args.duration > 0 else record_ptt(args.device)
                stats = audio_stats(audio)
                if stats["duration_sec"] <= 0:
                    print("[skip] 没有录到音频")
                    continue
                sample_id = next_sample_id(args.out_dir, term, repeat)
                wav_path = args.out_dir / f"{sample_id}.wav"
                write_wav(wav_path, audio)
                record = {
                    "id": sample_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "path": str(wav_path.relative_to(ROOT)),
                    "sample_rate": SAMPLE_RATE,
                    "audio": stats,
                    "kind": term.kind,
                    "canonical": term.canonical,
                    "spoken": term.spoken,
                    "role": term.role,
                    "source": term.source,
                    "repeat": repeat,
                    "expected": {
                        "entity": term.canonical,
                        "kind": term.kind,
                        "spoken": term.spoken,
                        "role": term.role,
                    },
                }
                append_manifest(args.manifest, record)
                recorded += 1
                print(f"[saved] {wav_path} duration={stats['duration_sec']}s peak={stats['peak']} rms={stats['rms']}")
    except KeyboardInterrupt:
        print("\n[stop] 已停止录制，已录内容保留。")

    print()
    print(f"[done] recorded={recorded}/{total}")
    print(f"[manifest] {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
