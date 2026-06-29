#!/usr/bin/env python3
"""
JVS（voice_server）交互测试：STT 语音识别 + TTS 文字朗读。

前置：先启动 voice_server（默认 http://127.0.0.1:18982）

  python voice_server/main.py

用法（仓库根目录）::

  python scripts/test_jvs_voice.py              # 交互菜单
  python scripts/test_jvs_voice.py health
  python scripts/test_jvs_voice.py stt --record 5
  python scripts/test_jvs_voice.py stt --file path/to.wav
  python scripts/test_jvs_voice.py tts "你好，测试朗读" --play

麦克风录音需额外安装（与 voice_server 同环境）::

  pip install sounddevice soundfile

环境变量（可选）::

  JACHIN_VOICE_SERVER_URL=http://127.0.0.1:18982
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE = os.getenv("JACHIN_VOICE_SERVER_URL", "http://127.0.0.1:18982").rstrip("/")
STT_SAMPLE_RATE = 16000


def _http_json(url: str, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_post_json(url: str, payload: dict, timeout: float = 120.0) -> bytes:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _http_post_multipart_stt(
    url: str,
    wav_bytes: bytes,
    filename: str = "capture.wav",
    timeout: float = 120.0,
) -> dict:
    boundary = f"----jachin-jvs-{int(time.time() * 1000)}"
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        f"Content-Disposition: form-data; name=\"audio\"; filename=\"{filename}\"\r\n".encode()
    )
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


def play_wav(path: Path) -> None:
    path = path.resolve()
    if _play_wav_inline(path):
        return
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606
        print(f"[播放] 已用系统默认播放器打开: {path}")
        return
    import subprocess

    if sys.platform == "darwin":
        subprocess.run(["afplay", str(path)], check=False)
    else:
        subprocess.run(["ffplay", "-nodisp", "-autoexit", str(path)], check=False)
    print(f"[播放] {path}")


def _play_wav_inline(path: Path) -> bool:
    """优先用 sounddevice 在当前进程播放，避免弹出外部播放器。"""
    try:
        import sounddevice as sd
        import soundfile as sf

        data, fs = sf.read(str(path), dtype="float32")
        print("[播放] 正在朗读…")
        sd.play(data, fs)
        sd.wait()
        print("[播放] 完成")
        return True
    except ImportError:
        return False
    except Exception as e:
        print(f"[播放] 内置播放失败 ({e})，尝试外部播放器…")
        return False


def cmd_health(base: str) -> int:
    print(f"[健康检查] GET {base}/health")
    try:
        data = _http_json(f"{base}/health")
    except urllib.error.URLError as e:
        print(f"[错误] 无法连接 JVS: {e}")
        print("请先运行: python voice_server/main.py")
        return 1
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if not data.get("ok"):
        print("[警告] ok=false")
        return 1
    if not data.get("stt_ready"):
        print("[警告] stt_ready=false，STT 可能 503")
    if not data.get("tts_ready"):
        print("[警告] tts_ready=false，TTS 可能 503")
    print("[OK] JVS 在线")
    return 0


def record_wav_bytes(duration_sec: float, quiet: bool = False) -> bytes | None:
    """麦克风录音 → WAV 字节（仅内存，不落盘）。"""
    try:
        import sounddevice as sd
        import soundfile as sf
    except ImportError:
        if not quiet:
            print(
                "[错误] 麦克风录音需要: pip install sounddevice soundfile\n"
                "或改用: python scripts/test_jvs_voice.py stt --file 你的.wav"
            )
        else:
            print(
                "[错误] 麦克风录音需要: pip install sounddevice soundfile",
                file=sys.stderr,
            )
        return None

    duration_sec = max(0.5, min(duration_sec, 120.0))
    if not quiet:
        print(f"[录音] {duration_sec:.1f}s，采样率 {STT_SAMPLE_RATE} Hz — 请说话…")
    frames = int(duration_sec * STT_SAMPLE_RATE)
    try:
        audio = sd.rec(frames, samplerate=STT_SAMPLE_RATE, channels=1, dtype="int16")
        sd.wait()
        buf = io.BytesIO()
        sf.write(buf, audio, STT_SAMPLE_RATE, subtype="PCM_16", format="WAV")
    except Exception as e:
        msg = f"[错误] 录音失败: {e}"
        print(msg, file=sys.stderr if quiet else None)
        if not quiet:
            print("[提示] 检查麦克风是否被占用、系统是否允许录音权限")
        return None
    if not quiet:
        print("[录音] 完成，正在识别（不保存文件）…")
    return buf.getvalue()


def cmd_stt(
    base: str,
    file: Path | None,
    record_sec: float | None,
    save_wav: Path | None,
    print_text: bool = False,
) -> int:
    quiet = print_text
    if file is not None:
        if not file.is_file():
            msg = f"[错误] 文件不存在: {file}"
            print(msg, file=sys.stderr if quiet else None)
            return 1
        wav_bytes = file.read_bytes()
        upload_name = file.name
    else:
        wav_bytes = record_wav_bytes(record_sec or 5.0, quiet=quiet)
        if wav_bytes is None:
            return 1
        upload_name = "capture.wav"

    if save_wav is not None:
        save_wav.write_bytes(wav_bytes)
        if not quiet:
            print(f"[另存] {save_wav.resolve()}")

    url = f"{base}/v1/stt/transcribe"
    if not quiet:
        print(f"[STT] POST {url}")
    try:
        result = _http_post_multipart_stt(url, wav_bytes, filename=upload_name)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        msg = f"[错误] HTTP {e.code}: {body}"
        print(msg, file=sys.stderr if quiet else None)
        return 1
    except urllib.error.URLError as e:
        msg = f"[错误] 无法连接 JVS: {e}"
        print(msg, file=sys.stderr if quiet else None)
        return 1

    text = (result.get("text") or "").strip()
    if print_text:
        if not text:
            print("[错误] STT 未识别到文本", file=sys.stderr)
            return 1
        print(text)
        return 0

    print("[STT 结果]")
    print(f"  文本: {text}")
    print(f"  置信度: {result.get('confidence')}")
    print(f"  时长(ms): {result.get('duration_ms')}")
    print(f"  语言: {result.get('language')}")
    return 0


def cmd_tts(base: str, text: str, out: Path | None, play: bool, voice: str | None) -> int:
    if not text.strip():
        print("[错误] 文本为空")
        return 1
    payload: dict = {"text": text}
    if voice:
        payload["voice"] = voice

    url = f"{base}/v1/tts/synthesize"
    print(f"[TTS] POST {url}")
    print(f"  文本: {text[:120]}{'…' if len(text) > 120 else ''}")
    try:
        wav_bytes = _http_post_json(url, payload)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"[错误] HTTP {e.code}: {body}")
        return 1
    except urllib.error.URLError as e:
        print(f"[错误] 无法连接 JVS: {e}")
        return 1

    out_path = out or Path(tempfile.gettempdir()) / f"jvs_tts_{int(time.time())}.wav"
    out_path.write_bytes(wav_bytes)
    duration_ms = 0
    try:
        with wave.open(str(out_path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate() or 24000
            duration_ms = int(frames / rate * 1000)
    except Exception:
        pass
    if out:
        print(f"[TTS] 已保存 WAV (~{duration_ms}ms): {out_path}")
    else:
        print(f"[TTS] 合成完成 (~{duration_ms}ms)")
    if play:
        play_wav(out_path)
    return 0


def interactive_loop(base: str) -> int:
    print("=" * 56)
    print("  Jachin JVS 语音模块测试")
    print(f"  服务地址: {base}")
    print("=" * 56)
    if cmd_health(base) != 0:
        return 1

    while True:
        print()
        print("请选择:")
        print("  1) 麦克风录音 → STT（语音识别）")
        print("  2) 指定 WAV 文件 → STT")
        print("  3) 输入文字 → TTS（朗读并播放）")
        print("  4) 健康检查")
        print("  0) 退出")
        try:
            choice = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            return 0

        if choice in ("0", "q", "quit", "exit"):
            print("再见。")
            return 0
        if choice == "1":
            try:
                raw = input("录音秒数（默认 5）: ").strip()
                sec = float(raw) if raw else 5.0
            except ValueError:
                print("[错误] 请输入有效数字，例如 5")
                continue
            except (EOFError, KeyboardInterrupt):
                print("\n已取消。")
                continue
            cmd_stt(base, None, sec, save_wav=None)
        elif choice == "2":
            try:
                p = input("WAV 文件路径: ").strip().strip('"')
            except (EOFError, KeyboardInterrupt):
                print("\n已取消。")
                continue
            if not p:
                continue
            cmd_stt(base, Path(p), None, save_wav=None)
        elif choice == "3":
            try:
                text = input("要朗读的文字: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n已取消。")
                continue
            if not text:
                continue
            cmd_tts(base, text, None, play=True, voice=None)
        elif choice == "4":
            cmd_health(base)
        else:
            print("无效选项")


def build_parser() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--base-url",
        default=DEFAULT_BASE,
        help=f"JVS 根 URL（默认 {DEFAULT_BASE}）",
    )

    p = argparse.ArgumentParser(description="JVS voice_server STT/TTS 测试")
    p.add_argument(
        "--base-url",
        default=DEFAULT_BASE,
        help=f"JVS 根 URL（默认 {DEFAULT_BASE}）",
    )
    sub = p.add_subparsers(dest="command")

    sub.add_parser("health", help="健康检查", parents=[parent])

    stt_p = sub.add_parser("stt", help="语音 → 文字", parents=[parent])
    stt_p.add_argument("--file", type=Path, help="WAV 文件路径")
    stt_p.add_argument("--record", type=float, metavar="SEC", help="麦克风录音秒数")
    stt_p.add_argument(
        "--save-wav",
        type=Path,
        metavar="PATH",
        help="可选：将本次音频另存为 WAV（默认麦克风录音不落盘）",
    )
    stt_p.add_argument(
        "--print-text",
        action="store_true",
        help="仅向 stdout 打印识别文本（供 simulate_voice_companion_chat.ps1 等脚本调用）",
    )

    tts_p = sub.add_parser("tts", help="文字 → 语音", parents=[parent])
    tts_p.add_argument("text", help="要合成的文字")
    tts_p.add_argument("-o", "--out", type=Path, help="输出 WAV 路径")
    tts_p.add_argument("--play", action="store_true", help="合成后播放")
    tts_p.add_argument("--voice", help="音色 ID（可选）")

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    if args.command is None:
        return interactive_loop(base)
    if args.command == "health":
        return cmd_health(base)
    if args.command == "stt":
        if not args.file and args.record is None:
            print("[错误] stt 需要 --file 或 --record")
            return 1
        return cmd_stt(base, args.file, args.record, args.save_wav, args.print_text)
    if args.command == "tts":
        return cmd_tts(base, args.text, args.out, args.play, args.voice)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
