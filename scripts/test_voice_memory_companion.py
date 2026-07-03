#!/usr/bin/env python3
"""
陪伴态语音记忆测试脚本（STT -> L3 -> Kokoro TTS）。

用途：
1) 测「上一轮是否记得」
2) 测「前几轮核心信息是否记得」
3) 输出每轮延迟 + 记忆命中情况，便于做体验评估

默认使用内置多轮文本（稳定复现）；可切到录音模式测真实语音输入。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import statistics
import time
import urllib.request
import uuid
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DEFAULT_JVS = "http://127.0.0.1:18982"
DEFAULT_L3 = "http://127.0.0.1:18991"
DEFAULT_VOICE = "zm_053"
DEFAULT_OUT_DIR = Path("data/voice_memory_bench")

CHITCHAT_RE = re.compile(r"(你好|在吗|你在吗|早上好|中午好|晚上好|陪我聊聊|心情怎么样)")
SHORT_QUERY_RE = re.compile(r"(天气|气温|几点|时间|提醒|闹钟|打开|搜索|查一下|总结)")
LONG_TASK_RE = re.compile(r"(全部|批量|所有|每个|目录|文件夹|生成报告|汇总报告|导出)")
SENSEVOICE_TAG_RE = re.compile(r"<\|.*?\|>")


@dataclass
class TestTurn:
    prompt: str
    expect_last: list[str]
    expect_history: list[str]


@dataclass
class TurnResult:
    turn_id: int
    ok: bool
    l3_ok: bool
    tts_ok: bool
    error: str
    source: str
    recognized_text: str
    routed_text: str
    voice_dispatch_tier: str
    voice_fast_lane: bool
    l3_ms: float
    tts_ms: float
    total_ms: float
    tts_calls: int
    tts_audio_ms: int
    answer_preview: str
    last_turn_pass: bool
    history_pass: bool
    memory_pass: bool
    missing_keywords: str


def _perf_ms() -> float:
    return time.perf_counter() * 1000.0


def _http_post_json(url: str, payload: dict[str, Any], timeout: float) -> bytes:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _http_post_multipart_stt(url: str, wav_bytes: bytes, timeout: float) -> dict[str, Any]:
    boundary = f"----voice-memory-{int(time.time() * 1000)}"
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(b'Content-Disposition: form-data; name="audio"; filename="turn.wav"\r\n')
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


def _record_wav_bytes(duration_sec: float, sample_rate: int = 16000) -> bytes:
    import sounddevice as sd
    import soundfile as sf

    frames = int(max(0.6, duration_sec) * sample_rate)
    audio = sd.rec(frames, samplerate=sample_rate, channels=1, dtype="int16")
    sd.wait()
    buf = io.BytesIO()
    sf.write(buf, audio, sample_rate, subtype="PCM_16", format="WAV")
    return buf.getvalue()


def _wav_duration_ms(wav_bytes: bytes) -> int:
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate() or 24000
            return int(frames / float(rate) * 1000)
    except Exception:
        return 0


def _sanitize_stt_text(text: str) -> str:
    t = SENSEVOICE_TAG_RE.sub("", (text or "").strip())
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return ""
    if not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", t):
        return ""
    return t


def _route_for_companion(text: str) -> dict[str, Any]:
    t = (text or "").strip()
    tier = "CHIT_CHAT"
    fast_lane = bool(CHITCHAT_RE.search(t))
    if LONG_TASK_RE.search(t):
        tier = "LONG_TASK"
        fast_lane = False
    elif SHORT_QUERY_RE.search(t):
        tier = "SHORT_TASK"
        fast_lane = False
    hints = {
        "fast_lane": fast_lane,
        "skip_context_retrieval": fast_lane,
        "skip_context_sniffer": fast_lane,
        "skip_experience_rag": fast_lane,
        "skip_gateway_enrich": fast_lane,
        "prefer_direct_llm": fast_lane,
    }
    return {"tier": tier, "hints": hints, "routed_text": t}


def _pick_tts_inputs(answer: str, max_sentences: int) -> list[str]:
    t = (answer or "").strip()
    if not t:
        return []
    chunks = [x.strip() for x in re.split(r"(?<=[。！？!?；;])", t) if x.strip()]
    out = chunks[: max(1, max_sentences)]
    return out if out else [t[:80]]


def _check_keywords(text: str, expected: list[str]) -> tuple[bool, list[str]]:
    if not expected:
        return True, []
    missing = [k for k in expected if k not in text]
    return len(missing) == 0, missing


def run_turn(
    turn_id: int,
    turn: TestTurn,
    *,
    use_stt_record: bool,
    record_sec: float,
    chat_id: str,
    jvs_base: str,
    l3_base: str,
    voice: str,
    max_speak_sentences: int,
    timeout_stt: float,
    timeout_l3: float,
    timeout_tts: float,
) -> TurnResult:
    started = _perf_ms()
    recognized = ""
    routed_text = ""
    tier = ""
    fast_lane = False
    l3_ms = 0.0
    tts_ms = 0.0
    tts_calls = 0
    tts_audio_ms = 0
    answer = ""
    l3_ok = False
    tts_ok = False
    try:
        if use_stt_record:
            print(f"[{turn_id}] 录音中 {record_sec:.1f}s，请说：{turn.prompt}")
            wav = _record_wav_bytes(record_sec)
            stt_json = _http_post_multipart_stt(f"{jvs_base}/v1/stt/transcribe", wav, timeout=timeout_stt)
            recognized = _sanitize_stt_text(stt_json.get("text") or "")
            if not recognized:
                raise RuntimeError("STT 空结果（含标签清洗后为空）")
        else:
            recognized = turn.prompt.strip()

        route = _route_for_companion(recognized)
        routed_text = route["routed_text"]
        tier = route["tier"]
        fast_lane = bool(route["hints"]["fast_lane"])
        implicit_signals = {
            "desktop_companion": True,
            "source": "desktop_voice_companion",
            "voice_raw_stt_text": recognized,
            "voice_routed_text": routed_text,
            "voice_dispatch_tier": tier,
            "voice_fast_lane": fast_lane,
            **route["hints"],
        }

        t0 = _perf_ms()
        l3_raw = _http_post_json(
            f"{l3_base}/api/v3/agent/run",
            {
                "user_input": routed_text,
                "chat_id": chat_id,
                "max_iterations": 8,
                "implicit_signals": implicit_signals,
                "implicit_attribution": {"channel": "websocket_terminal"},
            },
            timeout=timeout_l3,
        )
        l3_ms = _perf_ms() - t0
        l3_json = json.loads(l3_raw.decode("utf-8"))
        if l3_json.get("error"):
            raise RuntimeError(str(l3_json["error"]))
        answer = (l3_json.get("answer") or "").strip()
        if not answer:
            raise RuntimeError("L3 返回空 answer")
        l3_ok = True

        last_ok, miss_last = _check_keywords(answer, turn.expect_last)
        hist_ok, miss_hist = _check_keywords(answer, turn.expect_history)
        missing = miss_last + miss_hist

        tts_error = ""
        try:
            for one in _pick_tts_inputs(answer, max_sentences=max_speak_sentences):
                t0 = _perf_ms()
                wav = _http_post_json(
                    f"{jvs_base}/v1/tts/synthesize",
                    {"text": one, "voice": voice, "session_id": chat_id},
                    timeout=timeout_tts,
                )
                tts_ms += _perf_ms() - t0
                tts_calls += 1
                tts_audio_ms += _wav_duration_ms(wav)
            tts_ok = tts_calls > 0
            if not tts_ok:
                tts_error = "TTS: no speakable input"
        except Exception as e:
            tts_error = f"TTS: {e}"

        return TurnResult(
            turn_id=turn_id,
            ok=l3_ok and tts_ok,
            l3_ok=l3_ok,
            tts_ok=tts_ok,
            error=tts_error,
            source="stt_record" if use_stt_record else "script_text",
            recognized_text=recognized,
            routed_text=routed_text,
            voice_dispatch_tier=tier,
            voice_fast_lane=fast_lane,
            l3_ms=l3_ms,
            tts_ms=tts_ms,
            total_ms=_perf_ms() - started,
            tts_calls=tts_calls,
            tts_audio_ms=tts_audio_ms,
            answer_preview=answer[:260],
            last_turn_pass=last_ok,
            history_pass=hist_ok,
            memory_pass=last_ok and hist_ok,
            missing_keywords="|".join(missing),
        )
    except Exception as e:
        return TurnResult(
            turn_id=turn_id,
            ok=False,
            l3_ok=l3_ok,
            tts_ok=tts_ok,
            error=str(e),
            source="stt_record" if use_stt_record else "script_text",
            recognized_text=recognized,
            routed_text=routed_text,
            voice_dispatch_tier=tier,
            voice_fast_lane=fast_lane,
            l3_ms=l3_ms,
            tts_ms=tts_ms,
            total_ms=_perf_ms() - started,
            tts_calls=tts_calls,
            tts_audio_ms=tts_audio_ms,
            answer_preview=answer[:260],
            last_turn_pass=False,
            history_pass=False,
            memory_pass=False,
            missing_keywords="",
        )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="陪伴态语音记忆测试（多轮记忆 + 延迟）")
    p.add_argument("--jvs-base", default=DEFAULT_JVS)
    p.add_argument("--l3-base", default=DEFAULT_L3)
    p.add_argument("--voice", default=DEFAULT_VOICE, help="Kokoro 音色，默认 zm_053")
    p.add_argument("--record-sec", type=float, default=3.0, help="录音模式下每轮录音时长")
    p.add_argument("--use-stt-record", action="store_true", help="启用录音+STT；默认用内置文本直接跑")
    p.add_argument("--max-speak-sentences", type=int, default=2, help="每轮最多送 TTS 的句子数")
    p.add_argument("--timeout-stt", type=float, default=90.0)
    p.add_argument("--timeout-l3", type=float, default=180.0)
    p.add_argument("--timeout-tts", type=float, default=120.0)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    csv_path = args.out_dir / f"voice_memory_{stamp}.csv"
    jsonl_path = args.out_dir / f"voice_memory_{stamp}.jsonl"
    chat_id = f"voice-memory-{uuid.uuid4().hex[:10]}"

    turns = [
        TestTurn("请记住：我叫林野。", expect_last=[], expect_history=[]),
        TestTurn("请记住：我早上喜欢喝桂花乌龙。", expect_last=[], expect_history=[]),
        TestTurn("请记住：我对花生过敏。", expect_last=[], expect_history=[]),
        TestTurn("请记住：我现在跟进的项目代号叫星桥。", expect_last=[], expect_history=[]),
        TestTurn("我刚刚告诉你我叫什么？", expect_last=[], expect_history=["林野"]),
        TestTurn("我早上喜欢喝什么？", expect_last=[], expect_history=["桂花", "乌龙"]),
        TestTurn("我需要避开什么食物？", expect_last=[], expect_history=["花生"]),
        TestTurn("我现在跟进的项目代号是什么？", expect_last=[], expect_history=["星桥"]),
        TestTurn("请用一句话总结你刚刚记住的四条信息。", expect_last=[], expect_history=["林野", "乌龙", "花生", "星桥"]),
    ]

    print(f"[INFO] 输出 CSV: {csv_path}")
    print(f"[INFO] 输出 JSONL: {jsonl_path}")
    print(f"[INFO] chat_id={chat_id}")
    print(f"[INFO] 模式={'录音+STT' if args.use_stt_record else '脚本文本（稳定复现）'} voice={args.voice}")

    rows: list[TurnResult] = []
    for idx, t in enumerate(turns, start=1):
        r = run_turn(
            idx,
            t,
            use_stt_record=bool(args.use_stt_record),
            record_sec=args.record_sec,
            chat_id=chat_id,
            jvs_base=args.jvs_base.rstrip("/"),
            l3_base=args.l3_base.rstrip("/"),
            voice=args.voice,
            max_speak_sentences=max(1, int(args.max_speak_sentences)),
            timeout_stt=args.timeout_stt,
            timeout_l3=args.timeout_l3,
            timeout_tts=args.timeout_tts,
        )
        rows.append(r)
        print(
            f"[{idx}/{len(turns)}] {'OK' if r.ok else 'FAIL'} "
            f"L3OK={int(r.l3_ok)} TTSOK={int(r.tts_ok)} "
            f"L3={r.l3_ms:.0f}ms TTS={r.tts_ms:.0f}ms TOTAL={r.total_ms:.0f}ms "
            f"MEM={int(r.memory_pass) if (t.expect_last or t.expect_history) else '-'} tier={r.voice_dispatch_tier or '-'} fast={int(r.voice_fast_lane)} "
            f"{('missing=' + r.missing_keywords) if r.missing_keywords else ''} "
            f"{('err=' + r.error) if r.error else ''}"
        )
        with csv_path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(asdict(r).keys()))
            if f.tell() == 0:
                w.writeheader()
            w.writerow(asdict(r))
        with jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")

    ok_rows = [x for x in rows if x.ok]
    l3_rows = [x for x in rows if x.l3_ok]
    tts_rows = [x for x in rows if x.tts_ok]
    memory_checks = [(r, t) for r, t in zip(rows, turns) if t.expect_last or t.expect_history]
    mem_pass = len([r for r, _t in memory_checks if r.memory_pass and r.l3_ok])
    memory_target_turns = len(memory_checks)
    l3_avg = statistics.mean([x.l3_ms for x in l3_rows]) if l3_rows else 0.0
    total_avg = statistics.mean([x.total_ms for x in l3_rows]) if l3_rows else 0.0

    print("\n=== 结果汇总 ===")
    print(f"端到端成功轮次: {len(ok_rows)}/{len(rows)}")
    print(f"L3 成功轮次: {len(l3_rows)}/{len(rows)}")
    print(f"TTS 成功轮次: {len(tts_rows)}/{len(rows)}")
    print(f"记忆通过轮次: {mem_pass}/{memory_target_turns}")
    print(f"L3 平均耗时: {l3_avg:.0f}ms")
    print(f"总平均耗时: {total_avg:.0f}ms")
    print(f"CSV: {csv_path}")
    print(f"JSONL: {jsonl_path}")
    return 0 if l3_rows else 1


if __name__ == "__main__":
    raise SystemExit(main())

