#!/usr/bin/env python3
"""
语音问答链路延迟基准脚本（STT -> L3 -> TTS）。

目标：
1) 连续压测并实时输出每轮耗时
2) 落盘 CSV/JSONL，便于后续定位瓶颈
3) 统计 p50/p90，快速评估提速效果

默认链路（陪伴态对齐）：
  路由     : clients/desktop/src/voice/voiceIntentRouter.ts（via tsx CLI，与 chat.tsx 同源）
  JVS STT  : POST /v1/stt/transcribe     (18982)
  L3 回答  : POST /api/v3/agent/run      (18991)
  JVS TTS  : POST /v1/tts/synthesize     (18982)
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
import urllib.error
import urllib.request
import uuid
import wave
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from voice_intent_router import (
    build_companion_implicit_signals,
    dispatch_voice_intent,
)


if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


DEFAULT_JVS = os.getenv("JACHIN_VOICE_SERVER_URL", "http://127.0.0.1:18982").rstrip("/")
DEFAULT_L3 = os.getenv("JACHIN_L3_HTTP_BASE", "http://127.0.0.1:18991").rstrip("/")
DEFAULT_OUT_DIR = Path("data/voice_latency_bench")
DEFAULT_VOICE = "zm_053"

SENSEVOICE_TAG_RE = re.compile(r"<\|.*?\|>")

RESULT_HINT_RE = re.compile(r"(已|已经|完成|成功|失败|结果|最终|搞定|好了|完成了|已为你|我已|无法|没法|失败了|报错|可以了|请重试)")
PROCESS_HINT_RE = re.compile(r"(首先|接下来|然后|步骤|第[一二三四五六七八九十]|先|再|最后|正在|我会|我将|分析|思路|计划|流程|执行中|处理中|如下)")
LIST_PREFIX_RE = re.compile(r"^(\d+[\.\)]|[-*•]|第[一二三四五六七八九十]+步|步骤[:：]?)")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _perf_ms() -> float:
    return time.perf_counter() * 1000.0


def _http_post_json(url: str, payload: dict[str, Any], timeout: float) -> bytes:
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
    boundary = f"----jachin-latency-{int(time.time() * 1000)}"
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(b'Content-Disposition: form-data; name="audio"; filename="speech.wav"\r\n')
    body.extend(b"Content-Type: audio/wav\r\n\r\n")
    body.extend(wav_bytes)
    body.extend(f"\r\n--{boundary}--\r\n".encode())
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


def _first_sentence(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    seps = "。！？!?；;\n"
    idx = min([t.find(s) for s in seps if s in t] or [-1])
    if idx >= 0:
        return t[: idx + 1].strip()
    return t[:80].strip()


def _sanitize_stt_text(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    t = SENSEVOICE_TAG_RE.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return ""
    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", t):
        return ""
    return t


def _load_route_context(path: Path | None) -> dict[str, Any]:
    """VoiceDispatcherContext JSON（activeTasks / awaitingConfirmation 等）。"""
    if path is None:
        return {"activeTasks": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"route context must be object: {path}")
    if "activeTasks" not in data:
        data["activeTasks"] = []
    return data


def _pick_result_clause(s: str) -> str | None:
    clauses = [x.strip() for x in re.split(r"[，。；！？]", s or "") if x.strip()]
    if not clauses:
        return None
    for c in reversed(clauses):
        if RESULT_HINT_RE.search(c):
            return f"{c}。"
    return None


def _prepare_sentence_for_tts(raw: str) -> str | None:
    s = (raw or "")
    s = re.sub(r"```[\s\S]*?```", " ", s)
    s = re.sub(r"`[^`]*`", " ", s)
    s = re.sub(r"[#*_~]", "", s)
    s = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) < 2:
        return None
    letters = len(re.findall(r"[\w\u4e00-\u9fff]", s, flags=re.UNICODE))
    if letters < 2:
        return None
    noisy = len(re.findall(r"[^\w\u4e00-\u9fff\s，。！？、；：]", s, flags=re.UNICODE))
    if len(s) > 0 and (noisy / len(s)) > 0.45:
        return None
    has_result = bool(RESULT_HINT_RE.search(s))
    has_process = bool(PROCESS_HINT_RE.search(s))
    if LIST_PREFIX_RE.search(s) and not has_result:
        return None
    if has_process and not has_result:
        return None
    if (has_result and has_process) or (has_result and len(s) > 36):
        concise = _pick_result_clause(s)
        if concise:
            s = concise
    return s


def _split_for_companion_tts(text: str) -> list[str]:
    t = (text or "").strip()
    if not t:
        return []
    out: list[str] = []
    buf: list[str] = []
    enders = set("。！？!?；;")
    for ch in t:
        buf.append(ch)
        if ch in enders:
            s = "".join(buf).strip()
            if s:
                out.append(s)
            buf = []
    if buf:
        s = "".join(buf).strip()
        if s:
            out.append(s)
    if not out:
        out = [t]
    return out


def _pick_tts_inputs(answer: str, mode: str, max_sentences: int) -> list[str]:
    if mode == "legacy":
        one = _first_sentence(answer) or answer[:80]
        return [one] if one else []
    # companion mode: 使用接近前端 speakableText 的过滤，默认只播报结果类句子。
    rows = _split_for_companion_tts(answer)
    kept: list[str] = []
    for r in rows:
        s = _prepare_sentence_for_tts(r)
        if s:
            kept.append(s)
        if len(kept) >= max(1, max_sentences):
            break
    if kept:
        return kept
    one = _first_sentence(answer) or answer[:80]
    return [one] if one else []


def _record_wav_bytes(duration_sec: float, sample_rate: int = 16000) -> bytes:
    try:
        import sounddevice as sd
        import soundfile as sf
    except ImportError as e:
        raise RuntimeError(
            "录音模式需要安装 sounddevice + soundfile，请执行: pip install sounddevice soundfile"
        ) from e
    frames = int(max(0.5, duration_sec) * sample_rate)
    audio = sd.rec(frames, samplerate=sample_rate, channels=1, dtype="int16")
    sd.wait()
    buf = io.BytesIO()
    sf.write(buf, audio, sample_rate, subtype="PCM_16", format="WAV")
    return buf.getvalue()


def _play_wav_bytes(wav_bytes: bytes) -> None:
    if sys.platform != "win32":
        return
    import tempfile
    import winsound

    p = Path(tempfile.gettempdir()) / "jachin_latency_preview.wav"
    p.write_bytes(wav_bytes)
    winsound.PlaySound(None, winsound.SND_PURGE)
    winsound.PlaySound(str(p), winsound.SND_FILENAME | winsound.SND_ASYNC)


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


@dataclass
class RunMetric:
    run_id: int
    timestamp_ms: int
    ok: bool
    error: str
    stt_ms: float
    l3_ms: float
    tts_ms: float
    total_ms: float
    recognized_text: str
    answer_preview: str
    tts_input: str
    tts_calls: int
    tts_audio_ms: int
    routed_text: str
    voice_dispatch_tier: str
    voice_intent_class: str
    voice_fast_lane: bool
    voice_interrupt_verdict: str
    voice_route_notes: str


def run_once(
    i: int,
    *,
    jvs_base: str,
    l3_base: str,
    wav_bytes: bytes | None,
    text_input: str | None,
    voice: str,
    t_stt: float,
    t_l3: float,
    t_tts: float,
    chat_prefix: str,
    play_audio: bool,
    tts_mode: str,
    max_speak_sentences: int,
    companion_real_route: bool,
    route_context: dict[str, Any],
    progress: Callable[[str], None] | None = None,
) -> RunMetric:
    started = _perf_ms()
    stamp = _now_ms()
    stt_ms = l3_ms = tts_ms = 0.0
    recognized = ""
    answer = ""
    tts_input = ""
    tts_calls = 0
    tts_audio_ms = 0
    routed_text = ""
    voice_dispatch_tier = ""
    voice_intent_class = ""
    voice_fast_lane = False
    voice_interrupt_verdict = ""
    voice_route_notes = ""
    try:
        if text_input is not None:
            recognized = text_input.strip()
        else:
            if not wav_bytes:
                raise RuntimeError("无输入音频，且未提供 --text")
            if progress:
                progress("STT 请求中...")
            st = _perf_ms()
            stt_json = _http_post_multipart_stt(f"{jvs_base}/v1/stt/transcribe", wav_bytes, timeout=t_stt)
            stt_ms = _perf_ms() - st
            recognized = _sanitize_stt_text(stt_json.get("text") or "")
        if not recognized:
            raise RuntimeError("STT 未识别到文本")

        if progress:
            progress("L3 推理中...")
        st = _perf_ms()
        chat_id = f"{chat_prefix}-{uuid.uuid4().hex[:10]}"
        if companion_real_route:
            decision = dispatch_voice_intent(recognized, route_context)
            routed_text = (decision.get("normalized_text") or recognized).strip() or recognized
            implicit_signals = build_companion_implicit_signals(
                raw_stt_text=recognized,
                decision=decision,
                source="voice_latency_bench",
                active_tasks=route_context.get("activeTasks"),
            )
        else:
            routed_text = recognized
            implicit_signals = {
                "desktop_companion": True,
                "source": "desktop_voice_companion",
                "voice_raw_stt_text": recognized,
                "voice_routed_text": routed_text,
            }
            decision = {}
        voice_dispatch_tier = str(decision.get("tier") or "")
        voice_intent_class = str(decision.get("intent_class") or "")
        hints = decision.get("router_hints") or {}
        voice_fast_lane = bool(hints.get("fast_lane"))
        voice_interrupt_verdict = str(decision.get("interrupt_verdict") or "")
        notes = decision.get("route_notes") or []
        voice_route_notes = "|".join(notes) if isinstance(notes, list) else str(notes)
        l3_raw = _http_post_json(
            f"{l3_base}/api/v3/agent/run",
            {
                "user_input": routed_text,
                "chat_id": chat_id,
                "max_iterations": 8,
                "implicit_signals": implicit_signals,
                "implicit_attribution": {"channel": "websocket_terminal"},
            },
            timeout=t_l3,
        )
        l3_ms = _perf_ms() - st
        l3_json = json.loads(l3_raw.decode("utf-8"))
        if l3_json.get("error"):
            raise RuntimeError(str(l3_json["error"]))
        answer = (l3_json.get("answer") or "").strip()
        if not answer:
            raise RuntimeError("L3 返回空 answer")

        tts_inputs = _pick_tts_inputs(answer, mode=tts_mode, max_sentences=max_speak_sentences)
        if not tts_inputs:
            raise RuntimeError("TTS 无可用输入句子")
        tts_input = " | ".join(tts_inputs)
        for idx, one in enumerate(tts_inputs, start=1):
            if progress:
                progress(f"TTS 合成中... ({idx}/{len(tts_inputs)})")
            st = _perf_ms()
            tts_wav = _http_post_json(
                f"{jvs_base}/v1/tts/synthesize",
                {"text": one, "voice": voice, "session_id": chat_id},
                timeout=t_tts,
            )
            tts_ms += _perf_ms() - st
            tts_calls += 1
            tts_audio_ms += _wav_duration_ms(tts_wav)
            if play_audio:
                _play_wav_bytes(tts_wav)

        return RunMetric(
            run_id=i,
            timestamp_ms=stamp,
            ok=True,
            error="",
            stt_ms=stt_ms,
            l3_ms=l3_ms,
            tts_ms=tts_ms,
            total_ms=_perf_ms() - started,
            recognized_text=recognized,
            answer_preview=answer[:220],
            tts_input=tts_input,
            tts_calls=tts_calls,
            tts_audio_ms=tts_audio_ms,
            routed_text=routed_text,
            voice_dispatch_tier=voice_dispatch_tier,
            voice_intent_class=voice_intent_class,
            voice_fast_lane=voice_fast_lane,
            voice_interrupt_verdict=voice_interrupt_verdict,
            voice_route_notes=voice_route_notes,
        )
    except Exception as e:  # noqa: BLE001
        return RunMetric(
            run_id=i,
            timestamp_ms=stamp,
            ok=False,
            error=str(e),
            stt_ms=stt_ms,
            l3_ms=l3_ms,
            tts_ms=tts_ms,
            total_ms=_perf_ms() - started,
            recognized_text=recognized,
            answer_preview=answer[:220],
            tts_input=tts_input,
            tts_calls=tts_calls,
            tts_audio_ms=tts_audio_ms,
            routed_text=routed_text,
            voice_dispatch_tier=voice_dispatch_tier,
            voice_intent_class=voice_intent_class,
            voice_fast_lane=voice_fast_lane,
            voice_interrupt_verdict=voice_interrupt_verdict,
            voice_route_notes=voice_route_notes,
        )


def write_rows(csv_path: Path, jsonl_path: Path, rows: list[RunMetric]) -> None:
    if not rows:
        return
    is_new = not csv_path.exists()
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
        if is_new:
            w.writeheader()
        for r in rows:
            w.writerow(asdict(r))
    with jsonl_path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")


def print_live_summary(rows: list[RunMetric]) -> None:
    ok_rows = [r for r in rows if r.ok]
    if not ok_rows:
        print("  当前无成功样本。")
        return
    stt = [r.stt_ms for r in ok_rows if r.stt_ms > 0]
    l3 = [r.l3_ms for r in ok_rows if r.l3_ms > 0]
    tts = [r.tts_ms for r in ok_rows if r.tts_ms > 0]
    total = [r.total_ms for r in ok_rows]
    def _s(name: str, arr: list[float]) -> str:
        if not arr:
            return f"{name}: -"
        return f"{name}: p50={_percentile(arr,0.5):.0f}ms p90={_percentile(arr,0.9):.0f}ms avg={statistics.mean(arr):.0f}ms"
    print("  " + " | ".join([_s("STT", stt), _s("L3", l3), _s("TTS", tts), _s("TOTAL", total)]))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="语音链路延迟基准（STT->L3->TTS）")
    p.add_argument("--jvs-base", default=DEFAULT_JVS, help="JVS 地址，默认 http://127.0.0.1:18982")
    p.add_argument("--l3-base", default=DEFAULT_L3, help="L3 HTTP 地址，默认 http://127.0.0.1:18991")
    p.add_argument("--runs", type=int, default=20, help="压测轮数")
    p.add_argument("--interval-sec", type=float, default=0.5, help="每轮间隔秒")
    p.add_argument("--voice", default=DEFAULT_VOICE, help="TTS 音色 ID（陪伴态默认 zm_053 / Kokoro）")
    p.add_argument(
        "--tts-mode",
        choices=("companion", "legacy"),
        default="companion",
        help="TTS 输入模式：companion=按陪伴态句级（最多 N 句），legacy=仅首句",
    )
    p.add_argument(
        "--max-speak-sentences",
        type=int,
        default=3,
        help="companion 模式下最多送 TTS 的句子数（默认 3）",
    )
    p.add_argument("--audio-file", type=Path, help="固定 WAV 文件作为输入（推荐可重复）")
    p.add_argument("--record-sec", type=float, default=3.0, help="每轮现场录音秒数（未提供 --audio-file/--text 时生效）")
    p.add_argument("--reuse-audio", action="store_true", help="录音模式下，仅第1轮录音，后续复用")
    p.add_argument("--text", help="跳过 STT，直接用文本作为输入（仅压 L3+TTS）")
    p.add_argument("--play", action="store_true", help="每轮 TTS 合成后本地播放")
    p.add_argument("--timeout-stt", type=float, default=90.0)
    p.add_argument("--timeout-l3", type=float, default=180.0)
    p.add_argument("--timeout-tts", type=float, default=120.0)
    p.add_argument("--chat-prefix", default="voice-latency-bench")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    p.add_argument(
        "--companion-real-route",
        action="store_true",
        default=True,
        help="调用 voiceIntentRouter.ts（与 chat.tsx 同源）并注入完整 implicit_signals（默认开启）",
    )
    p.add_argument(
        "--no-companion-real-route",
        action="store_true",
        help="关闭陪伴态路由，仅传 STT 原文与最小 companion 信号",
    )
    p.add_argument(
        "--route-context-json",
        type=Path,
        help="VoiceDispatcherContext JSON（含 activeTasks 等），模拟长任务运行中插嘴",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    csv_path = args.out_dir / f"voice_latency_{stamp}.csv"
    jsonl_path = args.out_dir / f"voice_latency_{stamp}.jsonl"
    print(f"[INFO] 输出 CSV: {csv_path}")
    print(f"[INFO] 输出 JSONL: {jsonl_path}")
    print(
        f"[INFO] JVS={args.jvs_base}  L3={args.l3_base}  "
        f"voice={args.voice}  tts_mode={args.tts_mode}  max_speak_sentences={args.max_speak_sentences}"
    )
    if args.no_companion_real_route:
        args.companion_real_route = False
    route_context = _load_route_context(args.route_context_json)
    print(f"[INFO] companion_real_route={bool(args.companion_real_route)} router=voiceIntentRouter.ts")
    if route_context.get("activeTasks"):
        print(f"[INFO] route_context activeTasks={len(route_context['activeTasks'])}")

    fixed_wav: bytes | None = None
    if args.audio_file:
        if not args.audio_file.is_file():
            print(f"[ERROR] audio-file 不存在: {args.audio_file}")
            return 1
        fixed_wav = args.audio_file.read_bytes()
        print(f"[INFO] 固定音频输入: {args.audio_file} ({len(fixed_wav)} bytes)")

    rows: list[RunMetric] = []
    for i in range(1, max(1, args.runs) + 1):
        wav = fixed_wav
        if args.text is None and wav is None:
            if i == 1 or not args.reuse_audio:
                print(f"\n[{i}/{args.runs}] 请开始说话（录音 {args.record_sec:.1f}s）...")
                try:
                    wav = _record_wav_bytes(args.record_sec)
                except Exception as e:  # noqa: BLE001
                    print(f"[{i}/{args.runs}] 录音失败: {e}")
                    rows.append(
                        RunMetric(
                            i, _now_ms(), False, str(e), 0, 0, 0, 0, "", "", "", 0, 0, "", "", "", False, "", ""
                        )
                    )
                    write_rows(csv_path, jsonl_path, [rows[-1]])
                    continue
                if args.reuse_audio:
                    fixed_wav = wav
                print(f"[{i}/{args.runs}] 录音完成，开始跑 STT/L3/TTS ...")
            else:
                wav = fixed_wav

        metric = run_once(
            i,
            jvs_base=args.jvs_base.rstrip("/"),
            l3_base=args.l3_base.rstrip("/"),
            wav_bytes=wav,
            text_input=args.text,
            voice=args.voice,
            t_stt=args.timeout_stt,
            t_l3=args.timeout_l3,
            t_tts=args.timeout_tts,
            chat_prefix=args.chat_prefix,
            play_audio=args.play,
            tts_mode=args.tts_mode,
            max_speak_sentences=max(1, int(args.max_speak_sentences)),
            companion_real_route=bool(args.companion_real_route),
            route_context=route_context,
            progress=lambda msg, idx=i, total=args.runs: print(f"[{idx}/{total}] {msg}"),
        )
        rows.append(metric)
        write_rows(csv_path, jsonl_path, [metric])

        if metric.ok:
            print(
                f"[{i}/{args.runs}] OK  STT={metric.stt_ms:.0f}ms  L3={metric.l3_ms:.0f}ms  "
                f"TTS={metric.tts_ms:.0f}ms  TOTAL={metric.total_ms:.0f}ms  "
                f"AUDIO={metric.tts_audio_ms}ms  CALLS={metric.tts_calls}  "
                f"TIER={metric.voice_dispatch_tier or '-'} FAST={int(metric.voice_fast_lane)} "
                f"INT={metric.voice_interrupt_verdict or '-'}"
            )
            if metric.voice_route_notes:
                print(f"         route_notes={metric.voice_route_notes[:120]}")
        else:
            print(f"[{i}/{args.runs}] FAIL {metric.error}")
        print_live_summary(rows)

        if i < args.runs and args.interval_sec > 0:
            time.sleep(args.interval_sec)

    ok_count = len([r for r in rows if r.ok])
    print("\n=== 完成 ===")
    print(f"成功 {ok_count}/{len(rows)}，结果文件：")
    print(f"- {csv_path}")
    print(f"- {jsonl_path}")
    return 0 if ok_count > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

