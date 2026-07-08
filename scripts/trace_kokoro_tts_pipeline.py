#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
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

TTS_SCENARIO_SUITES: dict[str, list[dict[str, Any]]] = {
    "assistant_cues": [
        {"id": "wake_im_here", "category": "wake_ack", "text": "\u6211\u5728", "expected_kind": "cue", "max_duration_ms": 1500, "note": "short wake acknowledgment"},
        {"id": "wake_natural_welcome", "category": "wake_ack", "text": "\u4f60\u597d\uff0c\u6211\u5728", "expected_kind": "cue", "max_duration_ms": 1800, "note": "recommended natural welcome"},
        {"id": "thinking", "category": "latency_masking", "text": "\u6211\u60f3\u60f3", "expected_kind": "cue", "max_duration_ms": 1700, "note": "foreground thinking cue"},
        {"id": "background_ack", "category": "latency_masking", "text": "\u6536\u5230\uff0c\u6211\u6765\u5904\u7406", "expected_kind": "cue", "max_duration_ms": 2400, "note": "background task acknowledgment"},
        {"id": "done_short", "category": "task_done", "text": "\u5b8c\u6210\u4e86", "expected_kind": "cue", "max_duration_ms": 1500, "note": "short completion cue"},
        {"id": "lark_done_viian", "category": "task_done", "text": "\u5df2\u7ecf\u5e2e\u4f60\u53d1\u7ed9 viian \u4e86", "expected_kind": "content", "max_duration_ms": 2600, "note": "Lark send completion with recipient"},
        {"id": "lark_done_owner_sample", "category": "task_done", "text": "\u4f60\u597d\u4e3b\u4eba\uff0c\u6211\u5df2\u7ecf\u5e2e\u4f60\u5b8c\u6210\u4e86 Lark \u53d1\u9001", "expected_kind": "content", "max_duration_ms": 3600, "note": "owner-style Lark completion sample"},
        {"id": "lark_done_generic", "category": "task_done", "text": "\u5df2\u7ecf\u5e2e\u4f60\u5b8c\u6210 Lark \u53d1\u9001", "expected_kind": "content", "max_duration_ms": 2800, "note": "generic Lark completion"},
        {"id": "reminder_done", "category": "task_done", "text": "\u597d\uff0c\u6211\u4f1a\u63d0\u9192\u4f60", "expected_kind": "content", "max_duration_ms": 2300, "note": "reminder confirmation"},
        {"id": "meeting_reminder_done", "category": "task_done", "text": "\u597d\uff0c\u4e0b\u5348\u5f00\u4f1a\u524d\u6211\u63d0\u9192\u4f60", "expected_kind": "content", "max_duration_ms": 3000, "note": "meeting reminder confirmation"},
        {"id": "ask_recipient", "category": "clarify", "text": "\u4f60\u60f3\u53d1\u7ed9\u8c01", "expected_kind": "content", "max_duration_ms": 2200, "note": "missing recipient clarification"},
        {"id": "ask_content", "category": "clarify", "text": "\u4f60\u60f3\u53d1\u9001\u4ec0\u4e48\u5185\u5bb9", "expected_kind": "content", "max_duration_ms": 2600, "note": "missing message clarification"},
        {"id": "stt_unclear", "category": "repair", "text": "\u6211\u53ef\u80fd\u6ca1\u542c\u6e05\uff0c\u4f60\u518d\u8bf4\u4e00\u904d", "expected_kind": "content", "max_duration_ms": 3200, "note": "speech recognition repair"},
        {"id": "app_not_found_luck", "category": "error", "text": "\u6211\u6ca1\u627e\u5230 luck\uff0c\u8981\u4e0d\u8981\u6362\u4e2a\u540d\u5b57\u518d\u8bd5\u4e00\u6b21", "expected_kind": "content", "max_duration_ms": 4200, "note": "app not found recovery"},
        {"id": "permission_needed", "category": "safety", "text": "\u8fd9\u4e2a\u64cd\u4f5c\u9700\u8981\u4f60\u786e\u8ba4\u4e00\u4e0b", "expected_kind": "content", "max_duration_ms": 3000, "note": "confirmation required"},
        {"id": "retry_short", "category": "error", "text": "\u521a\u624d\u6ca1\u6210\u529f\uff0c\u6211\u518d\u8bd5\u4e00\u6b21", "expected_kind": "content", "max_duration_ms": 3000, "note": "short retry recovery"},
    ]
}
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
        default=1.4,
        help="TTS speed override (default: 1.4, matching system voice output).",
    )
    p.add_argument("--model-dir", type=Path, default=None, help="Override model dir.")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Output directory for reports.")
    p.add_argument(
        "--suite",
        choices=sorted(TTS_SCENARIO_SUITES),
        default="",
        help="Run a preset scenario suite and generate an aggregate listening report.",
    )
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




def _suite_row_status(row: dict[str, Any]) -> tuple[str, str]:
    reasons: list[str] = []
    expected_kind = str(row.get("expected_kind") or "")
    actual_kind = str(row.get("actual_kind") or "")
    max_duration_ms = int(row.get("max_duration_ms") or 0)
    duration_ms = int(row.get("duration_ms") or 0)
    quality = str(row.get("quality") or "")
    tone_loss_risk = str(row.get("tone_loss_risk") or "")

    if not row.get("synthesized"):
        reasons.append("synthesis skipped")
    if expected_kind and actual_kind and actual_kind != expected_kind:
        reasons.append(f"kind {actual_kind} != expected {expected_kind}")
    if expected_kind and not actual_kind:
        reasons.append(f"missing actual kind, expected {expected_kind}")
    if max_duration_ms and duration_ms and duration_ms > max_duration_ms:
        reasons.append(f"duration {duration_ms}ms > max {max_duration_ms}ms")
    if quality and quality != "ok":
        reasons.append(f"quality={quality}")
    if tone_loss_risk in {"medium", "high"}:
        reasons.append(f"tone_loss_risk={tone_loss_risk}")
    return ("warn" if reasons else "pass", "; ".join(reasons))


def build_suite_html_report(summary: dict[str, Any]) -> str:
    rows = summary["rows"]
    table_rows: list[str] = []
    for row in rows:
        wav_name = Path(str(row.get("wav_path") or "")).name
        report_name = Path(str(row.get("report_path") or "")).name
        html_name = Path(str(row.get("html_path") or "")).name
        audio_html = f'<audio controls preload="none" src="{html_escape(wav_name)}"></audio>' if wav_name else ""
        table_rows.append(
            "<tr class='{status}'>"
            "<td>{status}</td>"
            "<td>{idx}</td>"
            "<td>{case_id}<br><small>{category}</small></td>"
            "<td class='text'>{text}</td>"
            "<td>{audio}</td>"
            "<td>{actual_kind}<br><small>expected {expected_kind}</small></td>"
            "<td>{duration} / {max_duration}</td>"
            "<td>{style_mode}<br><small>idx {style_index}</small></td>"
            "<td>{raw_duration}<br><small>{lead}/{trail}</small></td>"
            "<td>{tokens}<br><small>drop {total_drop}</small></td>"
            "<td>{reasons}</td>"
            "<td><a href='{html_name}'>html</a> / <a href='{report_name}'>json</a></td>"
            "</tr>".format(
                status=html_escape(row.get("status", "")),
                idx=html_escape(row.get("idx", "")),
                case_id=html_escape(row.get("id", "")),
                category=html_escape(row.get("category", "")),
                text=html_escape(row.get("text", "")),
                audio=audio_html,
                actual_kind=html_escape(row.get("actual_kind", "")),
                expected_kind=html_escape(row.get("expected_kind", "")),
                duration=html_escape(row.get("duration_ms", "")),
                max_duration=html_escape(row.get("max_duration_ms", "")),
                style_mode=html_escape(row.get("style_mode", "")),
                style_index=html_escape(row.get("style_index", "")),
                raw_duration=html_escape(row.get("raw_duration_ms", "")),
                lead=html_escape(row.get("leading_trim_ms", "")),
                trail=html_escape(row.get("trailing_trim_ms", "")),
                tokens=html_escape(row.get("token_count", "")),
                total_drop=html_escape(row.get("total_drop", "")),
                reasons=html_escape(row.get("reasons", "")),
                html_name=html_escape(html_name),
                report_name=html_escape(report_name),
            )
        )

    meta = summary["meta"]
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <title>Kokoro TTS Scenario Suite</title>
  <style>
    body {{ font-family: system-ui, -apple-system, Segoe UI, sans-serif; margin: 24px; color: #172026; background: #f6f7f9; }}
    h1 {{ margin: 0 0 8px; font-size: 26px; }}
    .meta {{ margin: 0 0 18px; color: #52616b; }}
    table {{ width: 100%; border-collapse: collapse; background: white; box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
    th, td {{ border-bottom: 1px solid #e5e8ec; padding: 10px; text-align: left; vertical-align: top; font-size: 13px; }}
    th {{ position: sticky; top: 0; background: #eef2f5; z-index: 1; }}
    tr.warn {{ background: #fff8e6; }}
    tr.pass td:first-child {{ color: #167044; font-weight: 700; }}
    tr.warn td:first-child {{ color: #9a5b00; font-weight: 700; }}
    td.text {{ min-width: 210px; font-size: 15px; line-height: 1.45; }}
    audio {{ width: 210px; }}
    small {{ color: #697782; }}
    a {{ color: #1f5aa6; }}
  </style>
</head>
<body>
  <h1>Kokoro TTS Scenario Suite: {html_escape(meta['suite'])}</h1>
  <p class="meta">voice={html_escape(meta['voice'])} speed={html_escape(meta['speed'])} cases={html_escape(meta['case_count'])} pass={html_escape(meta['pass_count'])} warn={html_escape(meta['warn_count'])} time={html_escape(meta['time'])}</p>
  <table>
    <thead>
      <tr><th>Status</th><th>#</th><th>Case</th><th>Text</th><th>Audio</th><th>Kind</th><th>Duration ms</th><th>Style</th><th>Trim raw<br><small>lead/trail</small></th><th>Tokens</th><th>Reasons</th><th>Trace</th></tr>
    </thead>
    <tbody>
      {''.join(table_rows)}
    </tbody>
  </table>
</body>
</html>
"""


def run_suite(
    *,
    suite_name: str,
    tts: Any,
    voice: str,
    speed: float,
    out_dir: Path,
    no_synthesize: bool,
    no_play: bool,
) -> None:
    cases = TTS_SCENARIO_SUITES[suite_name]
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    suite_dir = out_dir / f"tts_suite_{suite_name}_{ts}"
    suite_dir.mkdir(parents=True, exist_ok=True)

    print("\n=== Kokoro TTS Scenario Suite ===")
    print(f"suite    : {suite_name}")
    print(f"voice    : {voice}")
    print(f"speed    : {speed}")
    print(f"cases    : {len(cases)}")
    print(f"out_dir  : {suite_dir}")

    rows: list[dict[str, Any]] = []
    for idx, case in enumerate(cases, start=1):
        case_id = str(case["id"])
        text = str(case["text"])
        print(f"\n[{idx}/{len(cases)}] {case_id}: {safe_console_text(text)}")
        report_path = run_trace(
            raw_text=text,
            tts=tts,
            voice=voice,
            speed=speed,
            out_dir=suite_dir,
            no_synthesize=no_synthesize,
            no_play=no_play,
        )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        synthesis = report.get("synthesis", {}) or {}
        actual = (report.get("g2p", {}) or {}).get("actual", {}) or synthesis.get("actual_trace", {}) or {}
        voice_style = report.get("voice_style", {}) or {}
        analysis = report.get("analysis", {}) or {}
        normalize = report.get("normalize", {}) or {}
        trim = actual.get("audio_trim", {}) or {}

        row: dict[str, Any] = {
            "idx": idx,
            "id": case_id,
            "category": case.get("category", ""),
            "text": text,
            "normalized": normalize.get("normalized", ""),
            "note": case.get("note", ""),
            "expected_kind": case.get("expected_kind", ""),
            "actual_kind": actual.get("tts_kind", ""),
            "max_duration_ms": case.get("max_duration_ms", ""),
            "duration_ms": synthesis.get("duration_ms", trim.get("duration_ms", "")),
            "raw_duration_ms": trim.get("original_duration_ms", ""),
            "leading_trim_ms": trim.get("leading_trim_ms", ""),
            "trailing_trim_ms": trim.get("trailing_trim_ms", ""),
            "quality": synthesis.get("quality", ""),
            "synth_ms": synthesis.get("synth_ms", ""),
            "synthesized": bool(synthesis.get("ran")),
            "style_mode": actual.get("style_mode", voice_style.get("style_mode", "")),
            "style_index": actual.get("style_index", voice_style.get("chosen_style_index", "")),
            "actual_path": actual.get("actual_path", ""),
            "token_count": actual.get("token_count", (report.get("g2p", {}) or {}).get("token_count", "")),
            "tone_drop_count": actual.get("tone_drop_count", ""),
            "total_drop": actual.get("total_drop", ""),
            "tone_loss_risk": analysis.get("tone_loss_risk", ""),
            "wav_path": synthesis.get("wav_path", ""),
            "report_path": str(report_path),
            "html_path": str(report_path.with_suffix(".html")),
        }
        row["status"], row["reasons"] = _suite_row_status(row)
        rows.append(row)

    pass_count = sum(1 for row in rows if row["status"] == "pass")
    warn_count = len(rows) - pass_count
    summary = {
        "meta": {
            "time": dt.datetime.now().isoformat(timespec="seconds"),
            "suite": suite_name,
            "voice": voice,
            "speed": speed,
            "case_count": len(rows),
            "pass_count": pass_count,
            "warn_count": warn_count,
            "out_dir": str(suite_dir),
        },
        "rows": rows,
    }

    summary_base = suite_dir / f"suite_summary_{suite_name}_{ts}"
    json_path = summary_base.with_suffix(".json")
    csv_path = summary_base.with_suffix(".csv")
    html_path = summary_base.with_suffix(".html")
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        if rows:
            writer.writeheader()
            writer.writerows(rows)
    html_path.write_text(build_suite_html_report(summary), encoding="utf-8")

    print("\n=== Kokoro TTS Suite Summary ===")
    print(f"pass/warn : {pass_count}/{warn_count}")
    for row in rows:
        suffix = f" - {row['reasons']}" if row.get("reasons") else ""
        print(f"{row['status'].upper():4} {row['id']} duration={row.get('duration_ms', '')}ms kind={row.get('actual_kind', '')}{suffix}")
    print(f"summary   : {json_path}")
    print(f"csv       : {csv_path}")
    print(f"listen    : {html_path}")


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

    if args.suite:
        run_suite(
            suite_name=args.suite,
            tts=tts,
            voice=voice,
            speed=speed,
            out_dir=out_dir,
            no_synthesize=args.no_synthesize,
            no_play=args.no_play,
        )
        return

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

