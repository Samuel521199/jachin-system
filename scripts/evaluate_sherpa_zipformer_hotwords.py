#!/usr/bin/env python3
"""Batch-evaluate hotword/entity matching on the active Jachin STT backend.

This file keeps its historical name for command compatibility. It no longer
builds a standalone Sherpa/Zipformer recognizer by default. Instead, it creates
the same configured Jachin voice STT service used by production, which means it
now tests DashScope Fun-ASR native vocabulary/context hotwords when cloud STT
is active.

Examples:
  python scripts/evaluate_sherpa_zipformer_hotwords.py
  python scripts/evaluate_sherpa_zipformer_hotwords.py --manifest data/eval_wav/hotword_match/manifest.jsonl
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "data" / "eval_wav" / "hotword_match" / "manifest.jsonl"
DEFAULT_JSON_OUT = ROOT / "reports" / "jachin_stt_hotword_eval.json"
DEFAULT_CSV_OUT = ROOT / "reports" / "jachin_stt_hotword_eval.csv"


@dataclass
class EvalRow:
    id: str
    spoken: str
    expected_terms: str
    expected_intent: str
    wav: str
    audio_sec: float
    raw_text: str
    text: str
    user_message: str
    raw_terms_hit: bool
    final_terms_hit: bool
    terms_hit: bool
    selected_type: str
    selected_intent: str
    selected_slots: str
    needs_confirmation: bool
    missing_slots: str
    confidence: float
    latency_ms: int
    rtf: float
    hotword_count: int
    hotword_status: str
    backend: str
    error: str = ""


def _ensure_utf8_console() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _make_jachin_stt_service() -> tuple[Any, Any]:
    voice_server_dir = ROOT / "voice_server"
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if str(voice_server_dir) not in sys.path:
        sys.path.insert(0, str(voice_server_dir))

    from config import load_config

    cfg = load_config()
    if cfg.stt_backend == "cloud":
        from services.cloud_stt_service import CloudSttService

        service = CloudSttService(
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
    else:
        from services.stt_service import SttService

        service = SttService(cfg.stt_dir)
    return cfg, service


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


def selected_summary(understanding: dict[str, Any]) -> tuple[str, str]:
    selected = understanding.get("selected") or {}
    if not isinstance(selected, dict):
        return "", "", "{}", False, "[]"
    selected_type = str(selected.get("type") or "")
    intent = str(selected.get("intent") or selected.get("type") or "")
    slots = selected.get("slots") or {}
    missing = selected.get("missing_slots") or []
    return (
        selected_type,
        intent,
        json.dumps(slots, ensure_ascii=False, sort_keys=True),
        bool(selected.get("needs_confirmation")),
        json.dumps(missing, ensure_ascii=False),
    )


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
    fieldnames = list(EvalRow.__dataclass_fields__.keys())
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def print_summary(rows: list[EvalRow], *, backend: str, hotword_status: str) -> None:
    total = len(rows)
    hits = sum(row.terms_hit for row in rows)
    raw_hits = sum(row.raw_terms_hit for row in rows)
    final_hits = sum(row.final_terms_hit for row in rows)
    clarification = sum(row.selected_type == "clarification_required" for row in rows)
    confirmations = sum(row.needs_confirmation for row in rows)
    errors = sum(bool(row.error) for row in rows)
    avg_latency = round(sum(row.latency_ms for row in rows) / max(total, 1), 1)
    avg_rtf = round(sum(row.rtf for row in rows) / max(total, 1), 3)

    print()
    print("-- Jachin STT Hotword Batch Eval --")
    print(f"samples       : {total}")
    print(f"terms_hit     : {hits}/{total}")
    print(f"raw_terms_hit : {raw_hits}/{total}")
    print(f"final_terms_hit: {final_hits}/{total}")
    print(f"clarifications: {clarification}/{total}")
    print(f"confirmations : {confirmations}/{total}")
    print(f"errors        : {errors}/{total}")
    print(f"avg_latency_ms: {avg_latency}")
    print(f"avg_rtf       : {avg_rtf}")
    print(f"backend       : {backend}")
    print(f"hotword_status: {hotword_status}")
    print()

    for row in rows:
        marker = "hit" if row.terms_hit else "miss"
        if row.error:
            marker = "error"
        print(f"[{row.id}] expected={row.expected_terms or '(none)'} result={marker}")
        print(f"  spoken : {row.spoken}")
        print(f"  raw    : {row.raw_text or '(empty)'}")
        print(f"  text   : {row.text or '(empty)'}")
        if row.selected_intent:
            print(f"  task   : {row.selected_type}:{row.selected_intent} {row.selected_slots}")
        if row.missing_slots and row.missing_slots != "[]":
            print(f"  missing: {row.missing_slots}")
        if row.error:
            print(f"  error  : {row.error}")


def main() -> int:
    _ensure_utf8_console()
    parser = argparse.ArgumentParser(description="Batch-test active Jachin STT hotword/entity matching")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_JSON_OUT)
    parser.add_argument("--csv-out", type=Path, default=DEFAULT_CSV_OUT)
    parser.add_argument("--limit", type=int, default=0, help="Run only first N manifest rows")
    args = parser.parse_args()

    manifest_rows = read_manifest(args.manifest)
    if args.limit > 0:
        manifest_rows = manifest_rows[: args.limit]
    if not manifest_rows:
        raise SystemExit(f"No samples found in manifest: {args.manifest}")

    cfg, service = _make_jachin_stt_service()
    if not service.ready:
        raise SystemExit(f"Jachin STT is not ready: {getattr(service, 'model_path', 'unknown')}")

    print(f"[manifest] {args.manifest}")
    print(f"[stt_backend] {cfg.stt_backend}")
    print(f"[stt_model] {getattr(service, 'model_name', 'unknown')}")
    print(f"[model_ref] {getattr(service, 'model_path', '')}")

    rows: list[EvalRow] = []
    for item in manifest_rows:
        wav_text = str(item.get("wav") or item.get("path") or "").strip()
        if not wav_text:
            raise SystemExit(f"Manifest row {item.get('id', '?')} has no wav/path field")
        wav = resolve_wav(wav_text)
        expected = item.get("expected") or {}
        terms = expected_terms(expected)
        expected_intent = str(expected.get("intent") or "")
        row_id = str(item.get("id", wav.stem))
        if not wav.is_file():
            rows.append(
                EvalRow(
                    id=row_id,
                    spoken=str(item.get("spoken", "")),
                    expected_terms=", ".join(terms),
                    expected_intent=expected_intent,
                    wav=str(wav),
                    audio_sec=0.0,
                    raw_text="",
                    text="",
                    user_message="",
                    raw_terms_hit=False,
                    final_terms_hit=False,
                    terms_hit=False,
                    selected_type="",
                    selected_intent="",
                    selected_slots="{}",
                    needs_confirmation=False,
                    missing_slots="[]",
                    confidence=0.0,
                    latency_ms=0,
                    rtf=0.0,
                    hotword_count=0,
                    hotword_status="",
                    backend=getattr(service, "model_name", "unknown"),
                    error=f"WAV not found: {wav}",
                )
            )
            continue
        audio_bytes = wav.read_bytes()
        started = time.perf_counter()
        result = service.transcribe(audio_bytes)
        latency_ms = int((time.perf_counter() - started) * 1000)
        audio_sec = round(result.duration_ms / 1000.0, 3)
        rtf = round((latency_ms / 1000.0) / max(audio_sec, 0.001), 3)
        selected_type, selected_intent, selected_slots, needs_confirmation, missing_slots = selected_summary(result.understanding or {})
        raw_hit = all_terms_hit(result.raw_text or "", terms)
        final_hit = all_terms_hit(" ".join([result.text or "", selected_slots]), terms)
        hit_text = " ".join([result.raw_text or "", result.text or "", selected_slots])
        rows.append(
            EvalRow(
                id=row_id,
                spoken=str(item.get("spoken", "")),
                expected_terms=", ".join(terms),
                expected_intent=expected_intent,
                wav=str(wav),
                audio_sec=audio_sec,
                raw_text=result.raw_text,
                text=result.text,
                user_message=result.user_message,
                raw_terms_hit=raw_hit,
                final_terms_hit=final_hit,
                terms_hit=all_terms_hit(hit_text, terms),
                selected_type=selected_type,
                selected_intent=selected_intent,
                selected_slots=selected_slots,
                needs_confirmation=needs_confirmation,
                missing_slots=missing_slots,
                confidence=float(result.confidence or 0.0),
                latency_ms=latency_ms,
                rtf=rtf,
                hotword_count=int(result.hotword_count or 0),
                hotword_status=str(result.hotword_status or ""),
                backend=str(result.backend or ""),
                error=result.text if str(result.text).startswith("[STT error]") else "",
            )
        )

    report = {
        "manifest": str(args.manifest),
        "stt_backend": cfg.stt_backend,
        "stt_model": getattr(service, "model_name", "unknown"),
        "model_ref": getattr(service, "model_path", ""),
        "vocabulary_id_configured": bool(getattr(service, "vocabulary_id", "")),
        "auto_sync_vocabulary": bool(getattr(service, "auto_sync_vocabulary", False)),
        "rows": [asdict(row) for row in rows],
    }
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(args.csv_out, rows)
    print_summary(
        rows,
        backend=str(report["stt_model"]),
        hotword_status=", ".join(sorted({row.hotword_status for row in rows if row.hotword_status})),
    )
    print(f"[json] {args.json_out}")
    print(f"[csv] {args.csv_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
