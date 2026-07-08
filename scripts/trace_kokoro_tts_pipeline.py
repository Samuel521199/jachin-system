#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import html
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = PROJECT_ROOT / "data" / "tts_trace_out"

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Trace Kokoro TTS pipeline: normalize -> sentence split -> g2p -> tokens -> synthesize."
    )
    p.add_argument("--text", default="", help="One-shot input text. If empty, enters interactive mode.")
    p.add_argument("--voice", default=None, help="Voice id override (default from voice_server config).")
    p.add_argument(
        "--speed",
        type=float,
        default=1.25,
        help="TTS speed override (default: 1.25, matching system voice output).",
    )
    p.add_argument("--model-dir", type=Path, default=None, help="Override model dir.")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Output directory for reports.")
    p.add_argument("--no-synthesize", action="store_true", help="Skip final wav synthesis.")
    p.add_argument("--no-play", action="store_true", help="Do not auto-play generated wav.")
    return p.parse_args()


def split_sentences(text: str) -> list[str]:
    if not text.strip():
        return []
    parts = re.split(r"([。！？!?；;])", text)
    out: list[str] = []
    cur = ""
    for part in parts:
        if part is None or part == "":
            continue
        cur += part
        if re.fullmatch(r"[。！？!?；;]", part):
            s = cur.strip()
            if s:
                out.append(s)
            cur = ""
    tail = cur.strip()
    if tail:
        out.append(tail)
    return out


def render_diff_ops(src: str, dst: str) -> list[dict[str, str]]:
    ops: list[dict[str, str]] = []
    for row in difflib.ndiff(list(src), list(dst)):
        tag = row[:2]
        ch = row[2:]
        if tag == "  ":
            ops.append({"op": "keep", "ch": ch})
        elif tag == "- ":
            ops.append({"op": "drop", "ch": ch})
        elif tag == "+ ":
            ops.append({"op": "add", "ch": ch})
    return ops


def classify_char(ch: str) -> str:
    if re.match(r"[\u4e00-\u9fff]", ch):
        return "han"
    if re.match(r"[A-Za-z]", ch):
        return "latin"
    if re.match(r"\d", ch):
        return "digit"
    if re.match(r"\s", ch):
        return "space"
    return "symbol"


def html_escape(s: Any) -> str:
    return html.escape(str(s), quote=True)


def safe_console_text(s: Any) -> str:
    txt = str(s)
    try:
        txt.encode(sys.stdout.encoding or "utf-8", errors="strict")
        return txt
    except Exception:
        return txt.encode("utf-8", errors="backslashreplace").decode("utf-8", errors="ignore")


def play_wav(path: Path) -> None:
    if os.name == "nt":
        try:
            import winsound

            winsound.PlaySound(str(path), winsound.SND_FILENAME)
            return
        except Exception as e:
            print(f"[WARN] play_wav failed: {e}")
            return
    print(f"[INFO] Audio saved: {path}")


def build_html_report(report: dict[str, Any]) -> str:
    diff_rows = "".join(
        f"<tr><td>{i+1}</td><td>{html_escape(op['op'])}</td><td>{html_escape(op['ch'])}</td></tr>"
        for i, op in enumerate(report["normalize"]["diff_ops"])
    )
    char_rows = "".join(
        f"<tr><td>{i+1}</td><td>{html_escape(row['char'])}</td><td>{html_escape(row['type'])}</td></tr>"
        for i, row in enumerate(report["normalize"]["normalized_chars"])
    )
    sent_rows = "".join(
        f"<tr><td>{i+1}</td><td>{html_escape(s['text'])}</td><td>{s['len']}</td></tr>"
        for i, s in enumerate(report["sentences"])
    )
    tok_rows = "".join(
        "<tr>"
        f"<td>{row['idx']}</td>"
        f"<td>{html_escape(row['phoneme'])}</td>"
        f"<td>{row['token_id']}</td>"
        f"<td>{row['source']}</td>"
        "</tr>"
        for row in report["g2p"]["token_trace"]
    )
    dropped_rows = "".join(
        f"<tr><td>{html_escape(row['char'])}</td><td>{row['count']}</td><td>{html_escape(row['kind'])}</td></tr>"
        for row in report["analysis"]["dropped_chars"]
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>Kokoro TTS Trace</title>
  <style>
    body {{ font-family: Segoe UI, system-ui, Arial; background:#0b1020; color:#d8e8ff; margin:0; padding:18px; }}
    h1,h2 {{ margin:14px 0 8px; }}
    .card {{ border:1px solid #2a3c66; border-radius:10px; padding:12px; margin-bottom:12px; background:#111a30; }}
    table {{ border-collapse:collapse; width:100%; font-size:12px; }}
    th,td {{ border:1px solid #24365a; padding:6px 8px; vertical-align:top; }}
    th {{ background:#162449; }}
    code, pre {{ background:#0f1830; color:#d5f0ff; border:1px solid #2a3c66; border-radius:8px; padding:8px; display:block; white-space:pre-wrap; }}
    .meta span {{ display:inline-block; margin-right:16px; }}
  </style>
</head>
<body>
  <h1>Kokoro TTS Trace Report</h1>
  <div class="card meta">
    <span><b>time:</b> {html_escape(report["meta"]["time"])}</span>
    <span><b>voice:</b> {html_escape(report["meta"]["voice"])}</span>
    <span><b>speed:</b> {html_escape(report["meta"]["speed"])}</span>
    <span><b>language_lock:</b> {html_escape(report["meta"]["language_lock"])}</span>
  </div>

  <div class="card">
    <h2>1) 原文与归一化</h2>
    <p><b>raw:</b> {html_escape(report["normalize"]["raw"])}</p>
    <p><b>normalized:</b> {html_escape(report["normalize"]["normalized"])}</p>
    <table><thead><tr><th>#</th><th>op</th><th>char</th></tr></thead><tbody>{diff_rows}</tbody></table>
    <h3>归一化后字符类型</h3>
    <table><thead><tr><th>#</th><th>char</th><th>type</th></tr></thead><tbody>{char_rows}</tbody></table>
  </div>

  <div class="card">
    <h2>2) 分句结果</h2>
    <table><thead><tr><th>#</th><th>sentence</th><th>len</th></tr></thead><tbody>{sent_rows}</tbody></table>
  </div>

  <div class="card">
    <h2>3) G2P / Token</h2>
    <p><b>backend:</b> {html_escape(report["g2p"]["backend"])}</p>
    <p><b>phonemes:</b></p>
    <pre>{html_escape(report["g2p"]["phonemes"])}</pre>
    <p><b>token_count:</b> {report["g2p"]["token_count"]} | <b>input_ids_count:</b> {report["g2p"]["input_ids_count"]}</p>
    <table><thead><tr><th>idx</th><th>phoneme char</th><th>token_id</th><th>source</th></tr></thead><tbody>{tok_rows}</tbody></table>
  </div>

  <div class="card">
    <h2>4) 口音风险分析（修正版口径）</h2>
    <p><b>结论口径：</b>{html_escape(report["analysis"]["summary"])}</p>
    <p><b>说明：</b>{html_escape(report["analysis"]["explain_no_minus1_to_onnx"])}</p>
    <p><b>display-only missing chars:</b> {report["analysis"]["display_missing_count"]}</p>
    <p><b>tone/prosody dropped:</b> {report["analysis"]["tone_like_missing_count"]}</p>
    <p><b>tone/prosody risk:</b> {html_escape(report["analysis"]["tone_loss_risk"])}</p>
    <table><thead><tr><th>char</th><th>count</th><th>kind</th></tr></thead><tbody>{dropped_rows}</tbody></table>
  </div>

  <div class="card">
    <h2>5) 音色向量与合成</h2>
    <pre>{html_escape(json.dumps(report["voice_style"], ensure_ascii=False, indent=2))}</pre>
    <pre>{html_escape(json.dumps(report["synthesis"], ensure_ascii=False, indent=2))}</pre>
  </div>
</body>
</html>
"""


def run_trace(
    *,
    raw_text: str,
    tts: Any,
    voice: str,
    speed: float,
    out_dir: Path,
    no_synthesize: bool,
    no_play: bool,
) -> Path:
    normalized = tts._normalize_text_for_zh_tts(raw_text)
    sentences = split_sentences(normalized)
    from voice_server.services.tts_service import PHONEME_CHAR_MAP, PHONEME_SEQUENCE_MAP  # pylint: disable=import-outside-toplevel

    vocab = tts._load_tokenizer_vocab()
    tone_like_chars = {
        "↓", "↑", "↘", "↗", "→",
        "˥", "˦", "˧", "˨", "˩",
    }
    tone_vocab_support = {ch: (ch in vocab) for ch in sorted(tone_like_chars)}

    def _trace_tokens_with_mapping(phoneme_str: str, source: str) -> tuple[list[int], list[dict[str, Any]], dict[str, Any]]:
        sequence_mapped = 0
        normalized_phoneme_str = phoneme_str
        for src, dst in sorted(PHONEME_SEQUENCE_MAP.items(), key=lambda x: len(x[0]), reverse=True):
            count = normalized_phoneme_str.count(src)
            if count:
                normalized_phoneme_str = normalized_phoneme_str.replace(src, dst)
                sequence_mapped += count
        token_ids_local: list[int] = []
        trace_rows: list[dict[str, Any]] = []
        map_drop_counts: dict[str, int] = {}
        oov_after_map_counts: dict[str, int] = {}
        mapped_changes = 0
        kept = 0
        for idx, ch in enumerate(normalized_phoneme_str, start=1):
            mapped = PHONEME_CHAR_MAP.get(ch, ch)
            if mapped != ch:
                mapped_changes += 1
            if mapped == "":
                map_drop_counts[ch] = map_drop_counts.get(ch, 0) + 1
                trace_rows.append(
                    {
                        "idx": idx,
                        "phoneme": ch,
                        "mapped": mapped,
                        "token_id": -1,
                        "kept_index": -1,
                        "source": source,
                        "drop_reason": "mapped_to_empty",
                    }
                )
                continue
            token_id = vocab.get(mapped)
            if token_id is None:
                oov_after_map_counts[mapped] = oov_after_map_counts.get(mapped, 0) + 1
                trace_rows.append(
                    {
                        "idx": idx,
                        "phoneme": ch,
                        "mapped": mapped,
                        "token_id": -1,
                        "kept_index": -1,
                        "source": source,
                        "drop_reason": "oov_after_mapping",
                    }
                )
                continue
            token_ids_local.append(int(token_id))
            kept += 1
            trace_rows.append(
                {
                    "idx": idx,
                    "phoneme": ch,
                    "mapped": mapped,
                    "token_id": int(token_id),
                    "kept_index": kept,
                    "source": source,
                    "drop_reason": "",
                }
            )
        normalized = "".join(PHONEME_CHAR_MAP.get(ch, ch) for ch in normalized_phoneme_str if PHONEME_CHAR_MAP.get(ch, ch))
        summary = {
            "normalized_phonemes": normalized,
            "pause_score": normalized.count(" ") + (normalized.count(",") + normalized.count("，")) * 2,
            "space_count": normalized.count(" "),
            "comma_count": normalized.count(",") + normalized.count("，"),
            "sequence_mapped": sequence_mapped,
            "mapped_changes": mapped_changes,
            "map_drop_counts": map_drop_counts,
            "oov_after_map_counts": oov_after_map_counts,
        }
        return token_ids_local, trace_rows, summary

    g2p = tts._ensure_g2p()
    g2p_backend = f"{g2p.__class__.__module__}.{g2p.__class__.__name__}"
    phonemes, tokens = g2p(normalized)
    token_ids = tts._coerce_g2p_tokens(tokens)
    token_source = "g2p_tokens" if token_ids else ""
    if not token_ids and isinstance(phonemes, str):
        token_ids, token_source = tts._phonemes_to_tokens_with_source(phonemes)

    # Reconstruct service-side route selection (baseline vs zh_frontend).
    chosen_source = token_source or "unknown"
    chosen_path = "baseline_g2p"
    zh_frontend_phonemes = ""
    zh_frontend_source = ""
    zh_frontend_tokens: list[int] = []
    zh_frontend_trace: list[dict[str, Any]] = []
    zh_frontend_summary: dict[str, Any] = {"mapped_changes": 0, "map_drop_counts": {}, "oov_after_map_counts": {}}

    baseline_tokens, baseline_trace, baseline_summary = _trace_tokens_with_mapping(
        phonemes if isinstance(phonemes, str) else "",
        "baseline_g2p",
    )
    baseline_summary["token_len"] = len(baseline_tokens)
    baseline_summary["tone_drop_count"] = sum(
        cnt for ch, cnt in baseline_summary.get("map_drop_counts", {}).items() if ch in tone_like_chars
    ) + sum(
        cnt for ch, cnt in baseline_summary.get("oov_after_map_counts", {}).items() if ch in tone_like_chars
    )
    baseline_summary["total_drop"] = sum(baseline_summary.get("map_drop_counts", {}).values()) + sum(
        baseline_summary.get("oov_after_map_counts", {}).values()
    )

    candidates: list[dict[str, Any]] = [
        {
            "name": "baseline_g2p",
            "token_ids": baseline_tokens,
            "trace": baseline_trace,
            "summary": baseline_summary,
            "source": "tokenizer_json_vocab",
        }
    ]

    if hasattr(tts, "_build_phonemes_with_zh_frontend"):
        try:
            zh_frontend_phonemes, zh_frontend_source = tts._build_phonemes_with_zh_frontend(normalized)
            if zh_frontend_phonemes:
                zh_frontend_tokens, zh_frontend_trace, zh_frontend_summary = _trace_tokens_with_mapping(
                    zh_frontend_phonemes,
                    "zh_frontend",
                )
                zh_frontend_summary["token_len"] = len(zh_frontend_tokens)
                zh_frontend_summary["tone_drop_count"] = sum(
                    cnt for ch, cnt in zh_frontend_summary.get("map_drop_counts", {}).items() if ch in tone_like_chars
                ) + sum(
                    cnt for ch, cnt in zh_frontend_summary.get("oov_after_map_counts", {}).items() if ch in tone_like_chars
                )
                zh_frontend_summary["total_drop"] = sum(zh_frontend_summary.get("map_drop_counts", {}).values()) + sum(
                    zh_frontend_summary.get("oov_after_map_counts", {}).values()
                )
                if zh_frontend_tokens:
                    candidates.append(
                        {
                            "name": "zh_frontend",
                            "token_ids": zh_frontend_tokens,
                            "trace": zh_frontend_trace,
                            "summary": zh_frontend_summary,
                            "source": "tokenizer_json_vocab+jieba+pypinyin+misaki_ipa",
                        }
                    )
                    zh_frontend_source = f"{zh_frontend_source} -> tokenizer_json_vocab"
        except Exception as e:
            zh_frontend_source = f"error: {e}"

    mode = str(getattr(tts, "_zh_frontend_mode", "auto")).lower()
    if mode not in {"auto", "on", "off"}:
        mode = "auto"

    def _score(summary: dict[str, Any]) -> tuple[int, int, int, int]:
        token_len = int(summary.get("token_len", 0))
        tone_drop = int(summary.get("tone_drop_count", 0))
        pause_score = int(summary.get("pause_score", 9999))
        total_drop = int(summary.get("total_drop", 0))
        return (-tone_drop, -pause_score, -total_drop, token_len)

    if mode == "on":
        chosen = next((c for c in candidates if c["name"] == "zh_frontend"), candidates[0])
    elif mode == "off":
        chosen = next((c for c in candidates if c["name"] == "baseline_g2p"), candidates[0])
    else:
        chosen = sorted(candidates, key=lambda c: _score(c["summary"]), reverse=True)[0]

    chosen_path = chosen["name"]
    token_ids = list(chosen["token_ids"])
    token_trace = list(chosen["trace"])
    mapping_summary = dict(chosen["summary"])
    chosen_source = str(chosen["source"])
    input_ids = [0, *token_ids, 0]

    dropped_counts: dict[str, int] = {}
    for row in token_trace:
        if row["token_id"] == -1:
            ch = str(row["mapped"] if row.get("mapped") else row["phoneme"])
            dropped_counts[ch] = dropped_counts.get(ch, 0) + 1
    dropped_chars = [
        {
            "char": ch,
            "count": cnt,
            "kind": "tone_or_prosody" if ch in tone_like_chars else "other_phoneme",
        }
        for ch, cnt in sorted(dropped_counts.items(), key=lambda x: (-x[1], x[0]))
    ]
    tone_like_missing_count = sum(item["count"] for item in dropped_chars if item["kind"] == "tone_or_prosody")
    display_missing_count = sum(item["count"] for item in dropped_chars)
    if tone_like_missing_count >= 8:
        tone_loss_risk = "high"
    elif tone_like_missing_count >= 3:
        tone_loss_risk = "medium"
    else:
        tone_loss_risk = "low"

    style_path = tts.voices_dir / f"{voice}.bin"
    _style_vec, style_idx, style_vec_count = tts._select_style_vector(
        voice,
        len(token_ids),
        len(phonemes) if isinstance(phonemes, str) else 0,
    )
    model_sha = tts._file_sha256_short(tts.model_path, limit_bytes=8 * 1024 * 1024)
    voice_sha = tts._file_sha256_short(style_path, limit_bytes=1024 * 1024)

    synth_info: dict[str, Any] = {"ran": False}
    actual_trace: dict[str, Any] = {}
    wav_path = None
    if not no_synthesize:
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        wav_path = out_dir / f"tts_trace_{ts}.wav"
        result = tts.synthesize(normalized, voice=voice, session_id=f"tts-trace-{ts}")
        wav_path.write_bytes(result.wav_bytes)
        actual_trace = result.trace or {}
        synth_info = {
            "ran": True,
            "duration_ms": result.duration_ms,
            "synth_ms": result.synth_ms,
            "sample_rate": result.sample_rate,
            "quality": result.quality_status,
            "wav_path": str(wav_path),
            "wav_bytes": len(result.wav_bytes),
            "actual_trace": actual_trace,
        }
        if not no_play:
            play_wav(wav_path)

    report = {
        "meta": {
            "time": dt.datetime.now().isoformat(timespec="seconds"),
            "voice": voice,
            "speed": speed,
            "language_lock": "z (Chinese)",
            "model_dir": str(tts.kokoro_dir),
            "model_sha": model_sha,
            "voice_sha": voice_sha,
            "sample_rate": 24000,
        },
        "normalize": {
            "raw": raw_text,
            "normalized": normalized,
            "diff_ops": render_diff_ops(raw_text, normalized),
            "normalized_chars": [{"char": ch, "type": classify_char(ch)} for ch in normalized],
        },
        "sentences": [{"text": s, "len": len(s)} for s in sentences],
        "g2p": {
            "backend": g2p_backend,
            "phonemes": phonemes if isinstance(phonemes, str) else str(phonemes),
            "token_source": chosen_source,
            "chosen_path": chosen_path,
            "token_count": len(token_ids),
            "input_ids_count": len(input_ids),
            "input_ids": input_ids,
            "token_trace": token_trace,
            "zh_frontend": {
                "source": zh_frontend_source,
                "phonemes": zh_frontend_phonemes,
                "token_count": len(zh_frontend_tokens),
            },
            "mapping_summary": mapping_summary,
            "tone_vocab_support": tone_vocab_support,
            "actual": actual_trace,
        },
        "analysis": {
            "summary": "不是 -1 被喂给模型，而是部分 G2P 音素/声调符号没有进入 token，可能导致声调或韵律信息丢失。",
            "explain_no_minus1_to_onnx": "-1 仅用于报告显示；实际送进 ONNX 的是 input_ids，不包含 -1。",
            "display_missing_count": display_missing_count,
            "tone_like_missing_count": tone_like_missing_count,
            "tone_loss_risk": tone_loss_risk,
            "dropped_chars": dropped_chars,
        },
        "voice_style": {
            "voice_id": voice,
            "style_bin": str(style_path),
            "style_vector_count": style_vec_count,
            "chosen_style_index": int(actual_trace.get("style_index", style_idx)) if actual_trace else style_idx,
            "style_mode": str(actual_trace.get("style_mode", getattr(tts, "_style_mode", "token_len"))) if actual_trace else getattr(tts, "_style_mode", "token_len"),
            "style_index_override": getattr(tts, "_style_index_override", None),
        },
        "synthesis": synth_info,
    }

    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    json_path = out_dir / f"tts_trace_{ts}.json"
    html_path = out_dir / f"tts_trace_{ts}.html"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(build_html_report(report), encoding="utf-8")

    print("\n=== Kokoro TTS Trace ===")
    print(f"raw       : {safe_console_text(raw_text)}")
    print(f"normalized: {safe_console_text(normalized)}")
    print(f"sentences : {len(sentences)} -> {safe_console_text([s['text'] for s in report['sentences']])}")
    print(f"g2p       : {g2p_backend}")
    print(f"path      : {chosen_path}")
    print(f"tokens    : {len(token_ids)} ({chosen_source})")
    if zh_frontend_phonemes:
        print(f"frontend  : {zh_frontend_source} tokens={len(zh_frontend_tokens)}")
    print(
        "mapping   : changed={} map_drop={} oov_after_map={}".format(
            mapping_summary.get("mapped_changes", 0),
            sum(mapping_summary.get("map_drop_counts", {}).values()),
            sum(mapping_summary.get("oov_after_map_counts", {}).values()),
        )
    )
    print("analysis  : -1 is display-only; input_ids never feed -1 to ONNX")
    print(f"analysis  : dropped_display={display_missing_count} tone_like_dropped={tone_like_missing_count} risk={tone_loss_risk}")
    shown_style_idx = int(actual_trace.get("style_index", style_idx)) if actual_trace else style_idx
    shown_style_mode = str(actual_trace.get("style_mode", getattr(tts, "_style_mode", "token_len"))) if actual_trace else getattr(tts, "_style_mode", "token_len")
    print(f"style     : {voice} idx={shown_style_idx}/{max(0, style_vec_count - 1)} mode={shown_style_mode}")
    pause_stats = actual_trace.get("pause_stats", {}) if actual_trace else {}
    if pause_stats:
        print(
            "pause     : space={} comma={} period={} original_punc={} inserted_comma={}".format(
                pause_stats.get("space_count", ""),
                pause_stats.get("comma_count", ""),
                pause_stats.get("period_count", ""),
                pause_stats.get("original_punctuation_count", ""),
                pause_stats.get("inserted_comma_count", ""),
            )
        )
    print(f"fingerprint: model={model_sha} voice={voice_sha} sample_rate=24000")
    if synth_info["ran"]:
        if actual_trace:
            print(
                "actual    : path={} tokens={} tone_drop={} total_drop={} sequence_mapped={}".format(
                    actual_trace.get("actual_path", ""),
                    actual_trace.get("token_count", ""),
                    actual_trace.get("tone_drop_count", ""),
                    actual_trace.get("total_drop", ""),
                    actual_trace.get("sequence_mapped", ""),
                )
            )
        trim_stats = actual_trace.get("audio_trim", {}) if actual_trace else {}
        if trim_stats:
            print(
                "trim      : raw={}ms out={}ms lead={}ms trail={}ms".format(
                    trim_stats.get("original_duration_ms", ""),
                    trim_stats.get("duration_ms", ""),
                    trim_stats.get("leading_trim_ms", ""),
                    trim_stats.get("trailing_trim_ms", ""),
                )
            )
        print(
            f"synth     : duration={synth_info['duration_ms']}ms synth={synth_info['synth_ms']}ms quality={synth_info['quality']}"
        )
        print(f"wav       : {synth_info['wav_path']}")
    print(f"report    : {json_path}")
    print(f"visualize : {html_path}")
    print("========================\n")

    return json_path


def main() -> None:
    args = parse_args()
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    from voice_server.config import load_config  # pylint: disable=import-outside-toplevel
    from voice_server.services.tts_service import TtsService  # pylint: disable=import-outside-toplevel

    cfg = load_config()
    model_dir = Path(args.model_dir) if args.model_dir else Path(cfg.tts_dir)
    voice = (args.voice or cfg.tts_voice or "zm_053").strip() or "zm_053"
    speed = float(args.speed)

    tts = TtsService(model_dir, default_voice=voice, default_speed=speed)
    if not tts.ready:
        raise RuntimeError(f"TTS model dir not ready: {model_dir}")
    if not tts._load_engine():
        raise RuntimeError(f"TTS engine load failed: {tts._load_error}")
    if not tts.has_voice(voice):
        raise RuntimeError(f"Voice '{voice}' not found under {tts.voices_dir}")

    if args.text.strip():
        run_trace(
            raw_text=args.text.strip(),
            tts=tts,
            voice=voice,
            speed=speed,
            out_dir=out_dir,
            no_synthesize=args.no_synthesize,
            no_play=args.no_play,
        )
        return

    print("Interactive Kokoro TTS trace mode is ready.")
    print(f"model_dir={model_dir}")
    print(f"voice={voice} speed={speed}")
    print("Type text and press Enter. Type 'q' to quit.")
    while True:
        raw = input("\nInput text> ").strip()
        if raw.lower() in {"q", "quit", "exit"}:
            print("Bye.")
            return
        if not raw:
            continue
        run_trace(
            raw_text=raw,
            tts=tts,
            voice=voice,
            speed=speed,
            out_dir=out_dir,
            no_synthesize=args.no_synthesize,
            no_play=args.no_play,
        )


if __name__ == "__main__":
    main()

