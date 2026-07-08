#!/usr/bin/env python3
"""Batch-evaluate Zipformer hotwords on a recorded STT entity manifest.

This is experiment-only. It loads the Zipformer Transducer once without
hotwords and once with hotwords, then runs both recognizers over every WAV in a
JSONL manifest. It does not call or modify the Jachin runtime.

Example:
  python scripts/evaluate_sherpa_zipformer_hotwords.py
  python scripts/evaluate_sherpa_zipformer_hotwords.py --hotwords-score 8.0
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from test_sherpa_paraformer_stt import (
    DEFAULT_HOTWORDS,
    ROOT,
    build_zipformer,
    default_model_dir,
    ensure_default_hotwords,
    load_audio,
    read_hotwords,
    transcribe_once,
)


DEFAULT_MANIFEST = ROOT / "data" / "eval_wav" / "stt_entity" / "manifest.jsonl"
DEFAULT_JSON_OUT = ROOT / "reports" / "sherpa_zipformer_hotwords_eval.json"
DEFAULT_CSV_OUT = ROOT / "reports" / "sherpa_zipformer_hotwords_eval.csv"


@dataclass
class EvalRow:
    id: str
    spoken: str
    expected_terms: str
    expected_intent: str
    wav: str
    audio_sec: float
    baseline_text: str
    hotwords_text: str
    baseline_terms_hit: bool
    hotwords_terms_hit: bool
    hotwords_changed_text: bool
    hotwords_improved_terms: bool
    hotwords_regressed_terms: bool


def normalize_text(value: str) -> str:
    return "".join(str(value or "").lower().split())


def entity_hit(text: str, expected: str) -> bool:
    if not expected:
        return False
    return normalize_text(expected) in normalize_text(text)


def expected_terms(expected: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for key in ("app", "entity"):
        value = str(expected.get(key) or "").strip()
        if value and value not in terms:
            terms.append(value)
    return terms


def all_terms_hit(text: str, terms: list[str]) -> bool:
    return bool(terms) and all(entity_hit(text, term) for term in terms)


def read_manifest(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
    return rows


def resolve_wav(path_text: str) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = ROOT / path
    return path


def write_csv(path: Path, rows: list[EvalRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()) if rows else list(EvalRow.__dataclass_fields__.keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def print_summary(rows: list[EvalRow], *, hotwords_count: int, hotwords_score: float) -> None:
    total = len(rows)
    baseline_hits = sum(row.baseline_terms_hit for row in rows)
    hot_hits = sum(row.hotwords_terms_hit for row in rows)
    changed = sum(row.hotwords_changed_text for row in rows)
    improved = sum(row.hotwords_improved_terms for row in rows)
    regressed = sum(row.hotwords_regressed_terms for row in rows)

    print()
    print("-- Zipformer Hotwords Batch Eval --")
    print(f"samples              : {total}")
    print(f"hotwords_count       : {hotwords_count}")
    print(f"hotwords_score       : {hotwords_score}")
    print(f"baseline_terms_hit   : {baseline_hits}/{total}")
    print(f"hotwords_terms_hit   : {hot_hits}/{total}")
    print(f"hotwords_changed_text: {changed}/{total}")
    print(f"hotwords_improved    : {improved}/{total}")
    print(f"hotwords_regressed   : {regressed}/{total}")
    print()

    for row in rows:
        marker = "same"
        if row.hotwords_improved_terms:
            marker = "improved"
        elif row.hotwords_regressed_terms:
            marker = "regressed"
        elif row.hotwords_changed_text:
            marker = "changed"
        print(f"[{row.id}] expected={row.expected_terms} result={marker}")
        print(f"  spoken  : {row.spoken}")
        print(f"  base    : {row.baseline_text or '(empty)'}")
        print(f"  hot     : {row.hotwords_text or '(empty)'}")


def main() -> int:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="Batch-test Zipformer hotwords on recorded entity WAVs")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--model-dir", type=Path, default=default_model_dir("zipformer"))
    parser.add_argument("--hotwords-file", type=Path, default=DEFAULT_HOTWORDS)
    parser.add_argument("--hotwords-score", type=float, default=4.0)
    parser.add_argument("--provider", default="cpu", choices=["cpu", "cuda", "coreml"])
    parser.add_argument("--num-threads", type=int, default=4)
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON_OUT)
    parser.add_argument("--csv-out", type=Path, default=DEFAULT_CSV_OUT)
    args = parser.parse_args()

    manifest_rows = read_manifest(args.manifest)
    if not manifest_rows:
        raise SystemExit(f"No samples found in manifest: {args.manifest}")

    ensure_default_hotwords(args.hotwords_file)
    _hotwords_text, hotwords_count = read_hotwords(args.hotwords_file)

    print(f"[manifest] {args.manifest}")
    print(f"[model] {args.model_dir}")
    print(f"[hotwords] {args.hotwords_file}")

    recognizer_base, files = build_zipformer(
        args.model_dir,
        num_threads=args.num_threads,
        provider=args.provider,
        debug=args.debug,
        hotwords_file=None,
        hotwords_score=args.hotwords_score,
    )
    recognizer_hot, _ = build_zipformer(
        args.model_dir,
        num_threads=args.num_threads,
        provider=args.provider,
        debug=args.debug,
        hotwords_file=args.hotwords_file,
        hotwords_score=args.hotwords_score,
    )

    rows: list[EvalRow] = []
    for item in manifest_rows:
        wav = resolve_wav(item["wav"])
        if not wav.is_file():
            raise SystemExit(f"WAV not found for {item.get('id', '?')}: {wav}")
        audio, sample_rate = load_audio(wav)
        expected = item.get("expected") or {}
        terms = expected_terms(expected)
        expected_intent = str(expected.get("intent") or "")
        baseline = transcribe_once(recognizer_base, audio, sample_rate, label="baseline", hotwords_count=0)
        hot = transcribe_once(recognizer_hot, audio, sample_rate, label="hotwords", hotwords_count=hotwords_count)
        baseline_hit = all_terms_hit(baseline.text, terms)
        hot_hit = all_terms_hit(hot.text, terms)
        rows.append(
            EvalRow(
                id=str(item.get("id", wav.stem)),
                spoken=str(item.get("spoken", "")),
                expected_terms=", ".join(terms),
                expected_intent=expected_intent,
                wav=str(wav),
                audio_sec=baseline.audio_sec,
                baseline_text=baseline.text,
                hotwords_text=hot.text,
                baseline_terms_hit=baseline_hit,
                hotwords_terms_hit=hot_hit,
                hotwords_changed_text=baseline.text != hot.text,
                hotwords_improved_terms=(not baseline_hit and hot_hit),
                hotwords_regressed_terms=(baseline_hit and not hot_hit),
            )
        )

    report = {
        "manifest": str(args.manifest),
        "model_dir": str(args.model_dir),
        "model_files": {k: str(v) for k, v in files.items()},
        "hotwords_file": str(args.hotwords_file),
        "hotwords_count": hotwords_count,
        "hotwords_score": args.hotwords_score,
        "rows": [asdict(row) for row in rows],
    }
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(args.csv_out, rows)
    print_summary(rows, hotwords_count=hotwords_count, hotwords_score=args.hotwords_score)
    print(f"[json] {args.json_out}")
    print(f"[csv] {args.csv_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
