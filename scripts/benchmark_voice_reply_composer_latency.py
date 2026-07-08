#!/usr/bin/env python3
"""Benchmark the voice clarification ReplyPlan -> composer -> TTS path.

This is intentionally narrower than benchmark_voice_pipeline_latency.py. It is
for answering one question: when a voice command needs a follow-up, where does
the extra time go?

Examples:
  python scripts/benchmark_voice_reply_composer_latency.py --composer-mode fallback --skip-tts
  python scripts/benchmark_voice_reply_composer_latency.py --composer-mode real --runs 5
  python scripts/benchmark_voice_reply_composer_latency.py --wav .\\sample.wav --composer-mode both
  python scripts/benchmark_voice_reply_composer_latency.py --wav .\\sample.wav --composer-mode compare
  python scripts/benchmark_voice_reply_composer_latency.py --case-set smoke --composer-mode both --runs 1
  python scripts/benchmark_voice_reply_composer_latency.py --case-set followup_text --composer-mode both --runs 1
  python scripts/benchmark_voice_reply_composer_latency.py --case-set wav_followups --composer-mode both --runs 1
  python scripts/benchmark_voice_reply_composer_latency.py --case-set recorded_wavs --composer-mode both --runs 1
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import statistics
import sys
import time
import urllib.request
import uuid
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VOICE_SERVER = ROOT / "voice_server"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(VOICE_SERVER) not in sys.path:
    sys.path.insert(0, str(VOICE_SERVER))

from l3_node.voice_reply_plan import build_reply_composer_prompt
from services.voice_understanding import VoiceUnderstandingCorrector


if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


DEFAULT_TEXT = "在 LARK 给Neil发消息"
DEFAULT_JVS = os.getenv("JACHIN_VOICE_SERVER_URL", "http://127.0.0.1:18982").rstrip("/")
DEFAULT_L3 = os.getenv("JACHIN_L3_HTTP_BASE", "http://127.0.0.1:18991").rstrip("/")
DEFAULT_OUT_DIR = ROOT / "data" / "voice_reply_composer_bench"
DEFAULT_VOICE = "zm_053"
SENSEVOICE_TAG_RE = re.compile(r"<\|.*?\|>")


@dataclass
class RunMetric:
    run_id: int
    case_id: str
    case_input: str
    expected_focus: str
    case_verdict: str
    case_notes: str
    ok: bool
    error: str
    input_ms: float
    stt_ms: float
    reply_plan_ms: float
    fallback_compose_ms: float
    prompt_build_ms: float
    fast_composer_ms: float
    l3_composer_ms: float
    tts_ms: float
    total_ms: float
    tts_audio_ms: int
    tts_calls: int
    raw_text: str
    recognized_text: str
    corrected_text: str
    selected_type: str
    selected_intent: str
    reply_intent: str
    missing_slots: str
    fallback_reply: str
    fast_reply: str
    l3_reply: str
    tts_input: str
    active_reply_source: str
    fast_source: str
    fast_error: str



@dataclass(frozen=True)
class BenchCase:
    case_id: str
    text: str = ""
    wav_path: Path | None = None
    expected_focus: str = ""
def _perf_ms() -> float:
    return time.perf_counter() * 1000.0


def _now_stamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _sanitize_stt_text(text: str) -> str:
    value = SENSEVOICE_TAG_RE.sub("", str(text or "")).strip()
    return re.sub(r"\s+", " ", value).strip()


def _json_post(url: str, payload: dict[str, Any], timeout: float) -> bytes:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _http_post_multipart_stt(url: str, wav_bytes: bytes, timeout: float) -> dict[str, Any]:
    boundary = f"----jachin-reply-composer-{int(time.time() * 1000)}"
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(b'Content-Disposition: form-data; name="audio"; filename="speech.wav"\r\n')
    body.extend(b"Content-Type: audio/wav\r\n\r\n")
    body.extend(wav_bytes)
    body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    req = urllib.request.Request(
        url,
        data=bytes(body),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _wav_duration_ms(wav_bytes: bytes) -> int:
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate() or 24000
            return int(frames / float(rate) * 1000)
    except Exception:
        return 0


def _extract_l3_answer(raw: bytes) -> str:
    data = json.loads(raw.decode("utf-8"))
    if isinstance(data, dict):
        for key in ("answer", "content", "text", "message", "output"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        result = data.get("result")
        if isinstance(result, dict):
            for key in ("answer", "content", "text"):
                value = result.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return str(data).strip()


def _first_tts_sentence(text: str) -> str:
    value = re.sub(r"```[\s\S]*?```", " ", str(text or ""))
    value = re.sub(r"`[^`]*`", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    if not value:
        return ""
    for sep in ("。", "！", "？", "!", "?", ";", "；", "\n"):
        idx = value.find(sep)
        if idx >= 0:
            return value[: idx + 1].strip()
    return value[:120].strip()


def _l3_compose(
    *,
    l3_base: str,
    prompt: str,
    reply_plan: dict[str, Any],
    timeout: float,
    chat_id: str,
) -> str:
    payload = {
        "user_input": prompt,
        "chat_id": chat_id,
        "max_iterations": 2,
        "implicit_signals": {
            "desktop_companion": True,
            "source": "voice_reply_composer_latency_bench",
            "voice_reply_composer": True,
            "voice_reply_plan": reply_plan,
            "prefer_direct_llm": True,
            "skip_retrieval": True,
            "skip_vector_retrieval": True,
            "clarification_pending": True,
        },
        "implicit_attribution": {"channel": "voice_reply_composer_latency_bench"},
    }
    raw = _json_post(f"{l3_base}/api/v3/agent/run", payload, timeout)
    return _extract_l3_answer(raw)


def _fast_compose(
    *,
    l3_base: str,
    reply_plan: dict[str, Any],
    user_text: str,
    fallback_text: str,
    timeout: float,
) -> tuple[str, dict[str, Any]]:
    raw = _json_post(
        f"{l3_base}/api/v3/voice/reply-compose",
        {
            "reply_plan": reply_plan,
            "user_text": user_text,
            "fallback_text": fallback_text,
            "timeout_sec": min(max(timeout, 0.5), 12.0),
            "max_tokens": 80,
        },
        timeout,
    )
    data = json.loads(raw.decode("utf-8"))
    reply = str(data.get("reply") or "").strip() if isinstance(data, dict) else ""
    if not reply:
        raise RuntimeError(f"fast composer returned empty reply: {data}")
    return reply, data if isinstance(data, dict) else {}

def _tts_once(*, jvs_base: str, text: str, voice: str, timeout: float, chat_id: str) -> tuple[int, int]:
    wav = _json_post(
        f"{jvs_base}/v1/tts/synthesize",
        {"text": text, "voice": voice, "session_id": chat_id},
        timeout,
    )
    return len(wav), _wav_duration_ms(wav)


def _plan_summary(result: dict[str, Any]) -> tuple[str, str, str, str]:
    selected = (result.get("understanding") or {}).get("selected") or {}
    plan = result.get("reply_plan") or {}
    missing = plan.get("missing_slots") or selected.get("missing_slots") or []
    if not isinstance(missing, list):
        missing = [str(missing)]
    return (
        str(selected.get("type") or ""),
        str(selected.get("intent") or ""),
        str(plan.get("reply_intent") or ""),
        ",".join(str(x) for x in missing),
    )


def run_once(
    *,
    run_id: int,
    text: str,
    wav_bytes: bytes | None,
    corrector: VoiceUnderstandingCorrector,
    composer_mode: str,
    skip_tts: bool,
    jvs_base: str,
    l3_base: str,
    voice: str,
    timeout_stt: float,
    timeout_l3: float,
    timeout_tts: float,
    chat_prefix: str,
    case_id: str = "single",
    case_input: str = "",
    expected_focus: str = "",
) -> RunMetric:
    started = _perf_ms()
    input_ms = stt_ms = reply_plan_ms = fallback_ms = prompt_ms = fast_ms = l3_ms = tts_ms = 0.0
    tts_audio_ms = tts_calls = 0
    recognized = raw_text = corrected = ""
    selected_type = selected_intent = reply_intent = missing_slots = ""
    fallback_reply = fast_reply = l3_reply = tts_input = ""
    active_reply_source = fast_source = fast_error = ""
    chat_id = f"{chat_prefix}-{run_id}-{uuid.uuid4().hex[:8]}"

    try:
        st = _perf_ms()
        if wav_bytes is not None:
            raw_text = "<wav>"
            stt_started = _perf_ms()
            stt_json = _http_post_multipart_stt(f"{jvs_base}/v1/stt/transcribe", wav_bytes, timeout_stt)
            stt_ms = _perf_ms() - stt_started
            recognized = _sanitize_stt_text(stt_json.get("text") or "")
        else:
            raw_text = text
            recognized = text
        input_ms = _perf_ms() - st

        if not recognized:
            raise RuntimeError("No recognized text to benchmark.")

        st = _perf_ms()
        result = corrector.correct(recognized)
        reply_plan_ms = _perf_ms() - st
        corrected = str(result.get("corrected_text") or recognized)
        reply_plan = result.get("reply_plan") or {}
        selected_type, selected_intent, reply_intent, missing_slots = _plan_summary(result)
        if not reply_plan:
            reply_intent = reply_intent or "no_followup"
            active_reply_source = "none_no_followup"
            return RunMetric(
                run_id=run_id,
                case_id=case_id,
                case_input=case_input,
                expected_focus=expected_focus,
                case_verdict="",
                case_notes="",
                ok=True,
                error="",
                input_ms=input_ms,
                stt_ms=stt_ms,
                reply_plan_ms=reply_plan_ms,
                fallback_compose_ms=0.0,
                prompt_build_ms=0.0,
                fast_composer_ms=0.0,
                l3_composer_ms=0.0,
                tts_ms=0.0,
                total_ms=_perf_ms() - started,
                tts_audio_ms=0,
                tts_calls=0,
                raw_text=raw_text,
                recognized_text=recognized,
                corrected_text=corrected,
                selected_type=selected_type,
                selected_intent=selected_intent,
                reply_intent=reply_intent,
                missing_slots=missing_slots,
                fallback_reply="",
                fast_reply="",
                l3_reply="",
                tts_input="",
                active_reply_source=active_reply_source,
                fast_source="",
                fast_error="",
            )

        st = _perf_ms()
        fallback_reply = str(reply_plan.get("fallback_template") or result.get("user_message") or "").strip()
        fallback_ms = _perf_ms() - st

        prompt = ""
        should_run_l3 = composer_mode in {"l3", "compare"}
        if composer_mode in {"fast", "both", "real", "compare"}:
            st = _perf_ms()
            try:
                candidate_reply, _fast_meta = _fast_compose(
                    l3_base=l3_base,
                    reply_plan=reply_plan,
                    user_text=recognized,
                    fallback_text=fallback_reply,
                    timeout=min(timeout_l3, 12.0),
                )
                fast_ms = _perf_ms() - st
                fast_source = str((_fast_meta or {}).get("source") or "")
                if composer_mode in {"real", "both"}:
                    if fast_source == "qwen_flash":
                        fast_reply = candidate_reply
                        active_reply_source = "qwen_flash"
                    else:
                        should_run_l3 = True
                else:
                    fast_reply = candidate_reply
                    if composer_mode == "fast":
                        active_reply_source = fast_source or "fast"
            except Exception as exc:
                fast_ms = _perf_ms() - st
                fast_error = f"{type(exc).__name__}: {exc}"
                if composer_mode in {"real", "both"}:
                    should_run_l3 = True
                else:
                    raise

        if should_run_l3:
            st = _perf_ms()
            prompt = build_reply_composer_prompt(reply_plan, user_text=recognized)
            prompt_ms = _perf_ms() - st

            st = _perf_ms()
            l3_reply = _l3_compose(
                l3_base=l3_base,
                prompt=prompt,
                reply_plan=reply_plan,
                timeout=timeout_l3,
                chat_id=chat_id,
            )
            l3_ms = _perf_ms() - st
            if l3_reply and not active_reply_source:
                active_reply_source = "l3_reply_composer"

        if not active_reply_source and fallback_reply:
            active_reply_source = "fallback_template"

        if not skip_tts:
            spoken = fast_reply if fast_reply else (l3_reply if l3_reply else fallback_reply)
            tts_input = _first_tts_sentence(spoken)
            if not tts_input:
                raise RuntimeError("No TTS input generated.")
            st = _perf_ms()
            _, audio_ms = _tts_once(
                jvs_base=jvs_base,
                text=tts_input,
                voice=voice,
                timeout=timeout_tts,
                chat_id=chat_id,
            )
            tts_ms = _perf_ms() - st
            tts_calls = 1
            tts_audio_ms = audio_ms

        return RunMetric(
            run_id=run_id,
            case_id=case_id,
            case_input=case_input,
            expected_focus=expected_focus,
            case_verdict="",
            case_notes="",
            ok=True,
            error="",
            input_ms=input_ms,
            stt_ms=stt_ms,
            reply_plan_ms=reply_plan_ms,
            fallback_compose_ms=fallback_ms,
            prompt_build_ms=prompt_ms,
            fast_composer_ms=fast_ms,
            l3_composer_ms=l3_ms,
            tts_ms=tts_ms,
            total_ms=_perf_ms() - started,
            tts_audio_ms=tts_audio_ms,
            tts_calls=tts_calls,
            raw_text=raw_text,
            recognized_text=recognized,
            corrected_text=corrected,
            selected_type=selected_type,
            selected_intent=selected_intent,
            reply_intent=reply_intent,
            missing_slots=missing_slots,
            fallback_reply=fallback_reply[:240],
            fast_reply=fast_reply[:240],
            l3_reply=l3_reply[:240],
            tts_input=tts_input[:240],
            active_reply_source=active_reply_source,
            fast_source=fast_source,
            fast_error=fast_error[:240],
        )
    except Exception as exc:
        return RunMetric(
            run_id=run_id,
            case_id=case_id,
            case_input=case_input,
            expected_focus=expected_focus,
            case_verdict="FAIL",
            case_notes="runtime_error",
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            input_ms=input_ms,
            stt_ms=stt_ms,
            reply_plan_ms=reply_plan_ms,
            fallback_compose_ms=fallback_ms,
            prompt_build_ms=prompt_ms,
            fast_composer_ms=fast_ms,
            l3_composer_ms=l3_ms,
            tts_ms=tts_ms,
            total_ms=_perf_ms() - started,
            tts_audio_ms=tts_audio_ms,
            tts_calls=tts_calls,
            raw_text=raw_text,
            recognized_text=recognized,
            corrected_text=corrected,
            selected_type=selected_type,
            selected_intent=selected_intent,
            reply_intent=reply_intent,
            missing_slots=missing_slots,
            fallback_reply=fallback_reply[:240],
            fast_reply=fast_reply[:240],
            l3_reply=l3_reply[:240],
            tts_input=tts_input[:240],
            active_reply_source=active_reply_source,
            fast_source=fast_source,
            fast_error=fast_error[:240],
        )


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * p)))
    return ordered[idx]


def _reply_text(row: RunMetric) -> str:
    return " ".join(x for x in [row.fast_reply, row.l3_reply, row.fallback_reply] if x).strip()


def _apply_case_verdict(row: RunMetric) -> None:
    if not row.ok:
        row.case_verdict = "FAIL"
        row.case_notes = row.error[:240]
        return
    cid = (row.case_id or "").lower()
    focus = (row.expected_focus or "").lower()
    missing = (row.missing_slots or "").lower()
    intent = (row.reply_intent or "").lower()
    reply = _reply_text(row)
    notes: list[str] = []

    def pass_if(cond: bool, ok: str, bad: str) -> bool:
        notes.append(ok if cond else bad)
        return cond

    if "missing_message" in cid or ("requires_confirmation" in focus and '"content"' not in focus):
        good = pass_if(intent == "ask_missing_slot" and "message_content" in missing, "asks_missing_message", "expected_missing_message")
        good = pass_if(("内容" in reply or "发什么" in reply), "reply_mentions_content", "reply_does_not_ask_content") and good
        row.case_verdict = "PASS" if good else "REVIEW"
    elif "missing_contact" in cid:
        good = pass_if("contact" in missing or "recipient" in missing, "asks_missing_contact", "expected_missing_contact")
        good = pass_if("谁" in reply or "联系人" in reply, "reply_mentions_recipient", "reply_does_not_ask_recipient") and good
        row.case_verdict = "PASS" if good else "REVIEW"
    elif "missing_app" in cid:
        good = pass_if(intent in {"confirm_external_action", "ask_missing_slot"}, "safe_app_gap_handling", "unexpected_intent")
        row.case_verdict = "PASS" if good else "REVIEW"
    elif "ready_external_confirmation" in cid or ('"content"' in focus and "requires_confirmation" in focus):
        good = pass_if(intent == "confirm_external_action", "asks_external_confirmation", "expected_external_confirmation")
        good = pass_if("message_content" not in missing, "does_not_ask_message_again", "wrongly_missing_message") and good
        row.case_verdict = "PASS" if good else "REVIEW"
    elif "typo_contact" in cid:
        good = pass_if(intent == "ask_missing_slot" and "message_content" in missing, "typo_still_reaches_message_followup", "typo_not_recovered_to_message_followup")
        row.case_verdict = "PASS" if good else "REVIEW"
    else:
        row.case_verdict = "INFO"
        notes.append("no_auto_rule")
    row.case_notes = "; ".join(notes)[:500]

def _print_summary(rows: list[RunMetric]) -> None:
    ok_rows = [row for row in rows if row.ok]
    print("\nSummary")
    print(f"  ok={len(ok_rows)}/{len(rows)}")
    for name, attr in (
        ("STT", "stt_ms"),
        ("ReplyPlan", "reply_plan_ms"),
        ("Fallback", "fallback_compose_ms"),
        ("PromptBuild", "prompt_build_ms"),
        ("FastComposer", "fast_composer_ms"),
        ("L3Composer", "l3_composer_ms"),
        ("TTS", "tts_ms"),
        ("TOTAL", "total_ms"),
    ):
        values = [float(getattr(row, attr)) for row in ok_rows if float(getattr(row, attr)) > 0]
        if not values:
            print(f"  {name}: -")
            continue
        print(
            f"  {name}: avg={statistics.mean(values):.1f}ms "
            f"p50={_percentile(values, 0.5):.1f}ms p90={_percentile(values, 0.9):.1f}ms"
        )


def _write_outputs(rows: list[RunMetric], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"voice_reply_composer_latency-{_now_stamp()}"
    csv_path = out_dir / f"{stem}.csv"
    jsonl_path = out_dir / f"{stem}.jsonl"
    fieldnames = list(asdict(rows[0]).keys()) if rows else list(RunMetric.__dataclass_fields__.keys())
    with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")
    return csv_path, jsonl_path



def _default_followup_text_cases() -> list[BenchCase]:
    return [
        BenchCase(
            "text_missing_message_lark_vivian",
            text="在 LARK 给 Vivian 发消息",
            expected_focus="应追问消息正文，保留 Lark/Vivian，不执行发送",
        ),
        BenchCase(
            "text_missing_contact_lark_content",
            text="在 LARK 发消息内容是我今天要睡觉",
            expected_focus="应追问收件人/联系人，不编造联系人",
        ),
        BenchCase(
            "text_missing_app_vivian_content",
            text="给 Vivian 发消息内容是我今天要睡觉",
            expected_focus="应追问发送渠道/应用，或进入安全确认，不直接发送",
        ),
        BenchCase(
            "text_typo_contact_viian_missing_message",
            text="在 LARK 给 Viian 发消息",
            expected_focus="应识别/纠正 Vivian 候选，并追问消息正文",
        ),
        BenchCase(
            "text_ready_external_confirmation",
            text="在 LARK 给 Neil 发消息内容是明天同步一下",
            expected_focus="信息齐全时应走外部发送确认，不应再问正文",
        ),
        BenchCase(
            "text_open_then_message_missing_content",
            text="请打开 Lark 然后给 Vivian 发消息",
            expected_focus="复合口令中应追问消息正文，不声称已打开或已发送",
        ),
    ]


def _default_followup_wav_cases() -> list[BenchCase]:
    candidates = [
        ("wav_sample_lark_vivian_missing_message", ROOT / "sample.wav", "真实 sample：应追问 Vivian 的消息正文"),
        (
            "wav_hotword_lark_vivian_missing_message",
            ROOT / "data" / "eval_wav" / "hotword_match" / "hm_send_lark_vivian_001.wav",
            "热词录音：应追问 Vivian 的消息正文",
        ),
        (
            "wav_stt_entity_lark_vivian_missing_message",
            ROOT / "data" / "eval_wav" / "stt_entity" / "send_lark_vivian_001.wav",
            "STT entity 录音：应追问 Vivian 的消息正文",
        ),
        (
            "wav_hotword_lark_neil_with_content",
            ROOT / "data" / "eval_wav" / "hotword_match" / "hm_send_lark_neil_content_001.wav",
            "内容齐全录音：应确认外部发送，或暴露 STT/解析缺陷",
        ),
        (
            "wav_stt_entity_lark_vivian_with_content",
            ROOT / "data" / "eval_wav" / "stt_entity" / "send_lark_vivian_content_001.wav",
            "内容齐全录音：应确认外部发送，或暴露 STT/解析缺陷",
        ),
    ]
    return [BenchCase(case_id, wav_path=path, expected_focus=focus) for case_id, path, focus in candidates if path.is_file()]


def _resolve_wav_path(path: Path) -> Path:
    wav_path = path.expanduser()
    if not wav_path.is_absolute():
        wav_path = (Path.cwd() / wav_path).resolve()
    if not wav_path.is_file():
        raise FileNotFoundError(str(wav_path))
    return wav_path


def _manifest_cases(manifest_path: Path, *, limit: int = 0, group_filter: str = "") -> list[BenchCase]:
    path = manifest_path.expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    if not path.is_file():
        raise FileNotFoundError(str(path))
    out: list[BenchCase] = []
    group_filter_norm = str(group_filter or "").strip().lower()
    for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        raw = line.strip()
        if not raw:
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            continue
        group = str(item.get("group") or "").strip()
        if group_filter_norm and group.lower() != group_filter_norm:
            continue
        rel = item.get("path") or item.get("wav") or item.get("file")
        if not rel:
            continue
        wav_path = Path(str(rel))
        if not wav_path.is_absolute():
            wav_path = (ROOT / wav_path).resolve()
        if not wav_path.is_file():
            continue
        expected = item.get("expected") if isinstance(item.get("expected"), dict) else {}
        spoken = str(item.get("spoken") or "").strip()
        focus_bits = []
        if group:
            focus_bits.append(f"group={group}")
        if expected:
            focus_bits.append("expected=" + json.dumps(expected, ensure_ascii=False, sort_keys=True))
        if spoken:
            focus_bits.append(f"spoken={spoken}")
        out.append(
            BenchCase(
                case_id=str(item.get("id") or f"manifest_{line_no}"),
                wav_path=wav_path,
                expected_focus="; ".join(focus_bits),
            )
        )
        if limit > 0 and len(out) >= limit:
            break
    if not out:
        raise RuntimeError(f"No usable wav cases in manifest: {path}")
    return out



def _recorded_manifest_cases(*, limit: int = 0) -> list[BenchCase]:
    manifests = [
        ROOT / "data" / "eval_wav" / "hotword_match" / "manifest.jsonl",
        ROOT / "data" / "eval_wav" / "stt_entity" / "manifest.jsonl",
    ]
    out: list[BenchCase] = []
    for manifest in manifests:
        if not manifest.is_file():
            continue
        remaining = max(0, int(limit or 0)) - len(out) if limit else 0
        if limit and remaining <= 0:
            break
        try:
            out.extend(_manifest_cases(manifest, limit=remaining, group_filter=""))
        except Exception:
            continue
    return out



def _all_recorded_wav_cases(*, limit: int = 0) -> list[BenchCase]:
    out = _recorded_manifest_cases(limit=limit)
    seen = {case.wav_path.resolve() for case in out if case.wav_path}
    if not limit or len(out) < limit:
        for wav_path in sorted((ROOT / "data" / "eval_wav").glob("**/*.wav")):
            resolved = wav_path.resolve()
            if resolved in seen:
                continue
            out.append(
                BenchCase(
                    case_id=f"recorded_{wav_path.parent.name}_{wav_path.stem}",
                    wav_path=resolved,
                    expected_focus="recorded wav without manifest metadata",
                )
            )
            seen.add(resolved)
            if limit and len(out) >= limit:
                break
    if not out:
        raise FileNotFoundError("No recorded wav cases found under data/eval_wav")
    return out



def _smoke_recorded_wav_cases() -> list[BenchCase]:
    preferred_stems = {
        "hm_open_lark_001",
        "hm_open_chrome_001",
        "hm_open_vscode_001",
        "hm_find_vivian_001",
        "hm_find_neil_001",
        "hm_find_jachin_001",
        "hm_send_lark_vivian_001",
        "hm_send_lark_neil_content_001",
        "hm_send_feishu_ethan_content_001",
        "open_lark_001",
        "find_vivian_001",
        "send_lark_vivian_001",
        "send_lark_vivian_content_001",
    }
    cases = _all_recorded_wav_cases()
    selected = [case for case in cases if case.wav_path and case.wav_path.stem in preferred_stems]
    return selected or cases[:12]

def _build_cases(args: argparse.Namespace) -> list[BenchCase]:
    case_set = str(args.case_set or "single").strip().lower()
    if case_set == "smoke":
        return _smoke_recorded_wav_cases()
    if case_set == "followup_text":
        return _default_followup_text_cases()
    if case_set == "wav_followups":
        cases = _default_followup_wav_cases()
        if not cases:
            raise FileNotFoundError("No built-in wav follow-up cases found under data/eval_wav")
        return cases
    if case_set == "recorded_wavs":
        return _all_recorded_wav_cases(limit=max(0, int(args.manifest_limit or 0)))
    if case_set == "manifest":
        return _manifest_cases(args.manifest, limit=max(0, int(args.manifest_limit or 0)), group_filter=str(args.manifest_group or ""))
    if case_set == "all":
        cases = [*_default_followup_text_cases(), *_default_followup_wav_cases()]
        if not cases:
            raise RuntimeError("No benchmark cases found")
        return cases
    if args.wav:
        wav_path = _resolve_wav_path(args.wav)
        return [BenchCase("single_wav", wav_path=wav_path, expected_focus="单条 WAV：按真实 STT -> ReplyPlan -> 追问链路验证")]
    return [BenchCase("single_text", text=str(args.text or DEFAULT_TEXT), expected_focus="单条文本：跳过 STT，验证 ReplyPlan -> 追问链路")]
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark voice ReplyPlan/L3 composer/TTS latency.")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="Text input for --case-set single when --wav is omitted.")
    parser.add_argument("--wav", type=Path, help="Optional WAV input for --case-set single. When set, STT is measured first.")
    parser.add_argument("--runs", type=int, default=3, help="Repeat count per case. With --case-set, every case runs N times.")
    parser.add_argument("--interval-sec", type=float, default=0.3)
    parser.add_argument(
        "--case-set",
        choices=("single", "smoke", "followup_text", "wav_followups", "recorded_wavs", "manifest", "all"),
        default="single",
        help="single keeps the old command shape; smoke runs representative recorded wavs; followup_text runs built-in text follow-up cases; wav_followups uses curated recorded eval wavs; recorded_wavs runs every wav under data/eval_wav; manifest reads a manifest.jsonl; all runs built-in text+wav follow-up cases.",
    )
    parser.add_argument(
        "--composer-mode",
        choices=("real", "both", "fallback", "fast", "l3", "compare"),
        default="real",
        help="real/both mirror desktop voice flow; compare runs fast plus legacy L3 for side-by-side diagnostics.",
    )
    parser.add_argument("--skip-tts", action="store_true", help="Do not call JVS TTS.")
    parser.add_argument("--jvs-base", default=DEFAULT_JVS)
    parser.add_argument("--l3-base", default=DEFAULT_L3)
    parser.add_argument("--voice", default=DEFAULT_VOICE)
    parser.add_argument("--timeout-stt", type=float, default=90.0)
    parser.add_argument("--timeout-l3", type=float, default=180.0)
    parser.add_argument("--timeout-tts", type=float, default=120.0)
    parser.add_argument("--chat-prefix", default="voice-reply-composer-bench")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "data" / "eval_wav" / "hotword_match" / "manifest.jsonl",
        help="Manifest JSONL for --case-set manifest.",
    )
    parser.add_argument("--manifest-limit", type=int, default=0, help="Max manifest cases to run; 0 means all.")
    parser.add_argument("--manifest-group", default="", help="Optional manifest group filter, e.g. message_confirmation.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        cases = _build_cases(args)
    except Exception as exc:
        print(f"[ERROR] failed to build cases: {exc}", file=sys.stderr)
        print(
            "        Example: python scripts\\benchmark_voice_reply_composer_latency.py "
            "--case-set smoke --composer-mode both --runs 1",
            file=sys.stderr,
        )
        return 2

    corrector = VoiceUnderstandingCorrector()
    rows: list[RunMetric] = []
    per_case_runs = max(1, int(args.runs))
    total_runs = len(cases) * per_case_runs
    print(
        f"[INFO] mode={args.composer_mode} case_set={args.case_set} cases={len(cases)} "
        f"runs_per_case={per_case_runs} total_runs={total_runs} skip_tts={args.skip_tts} "
        f"JVS={args.jvs_base} L3={args.l3_base}"
    )

    seq = 0
    for case in cases:
        wav_bytes = case.wav_path.read_bytes() if case.wav_path else None
        case_input = str(case.wav_path) if case.wav_path else case.text
        print(f"\n[CASE] {case.case_id}")
        print(f"  input: {case_input}")
        if case.expected_focus:
            print(f"  expect: {case.expected_focus}")
        for repeat in range(1, per_case_runs + 1):
            seq += 1
            row = run_once(
                run_id=seq,
                case_id=case.case_id,
                case_input=case_input,
                expected_focus=case.expected_focus,
                text=case.text or str(args.text or DEFAULT_TEXT),
                wav_bytes=wav_bytes,
                corrector=corrector,
                composer_mode=args.composer_mode,
                skip_tts=bool(args.skip_tts),
                jvs_base=str(args.jvs_base).rstrip("/"),
                l3_base=str(args.l3_base).rstrip("/"),
                voice=str(args.voice),
                timeout_stt=float(args.timeout_stt),
                timeout_l3=float(args.timeout_l3),
                timeout_tts=float(args.timeout_tts),
                chat_prefix=str(args.chat_prefix),
            )
            _apply_case_verdict(row)
            rows.append(row)
            status = "OK" if row.ok else "FAIL"
            print(
                f"[{seq}/{total_runs} case_run={repeat}/{per_case_runs}] {status} "
                f"case={row.case_id} STT={row.stt_ms:.1f}ms ReplyPlan={row.reply_plan_ms:.1f}ms "
                f"Fallback={row.fallback_compose_ms:.1f}ms Prompt={row.prompt_build_ms:.1f}ms "
                f"Fast={row.fast_composer_ms:.1f}ms L3={row.l3_composer_ms:.1f}ms "
                f"TTS={row.tts_ms:.1f}ms Total={row.total_ms:.1f}ms "
                f"src={row.active_reply_source or '-'} intent={row.reply_intent or '-'} missing={row.missing_slots or '-'} verdict={row.case_verdict or '-'}"
            )
            if row.case_notes:
                print(f"  verdict: {row.case_verdict} - {row.case_notes}")
            if row.error:
                print(f"  error: {row.error}")
            if row.recognized_text:
                print(f"  recognized: {row.recognized_text}")
            if row.fallback_reply:
                print(f"  fallback: {row.fallback_reply}")
            if row.fast_reply:
                print(f"  fast: {row.fast_reply} (source={row.fast_source or '-'})")
            if row.fast_error:
                print(f"  fast_error: {row.fast_error}")
            if row.l3_reply:
                print(f"  l3: {row.l3_reply}")
            if seq < total_runs and args.interval_sec > 0:
                time.sleep(float(args.interval_sec))

    _print_summary(rows)
    csv_path, jsonl_path = _write_outputs(rows, args.out_dir)
    print(f"\nWrote:\n  {csv_path}\n  {jsonl_path}")
    return 0 if any(row.ok for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())