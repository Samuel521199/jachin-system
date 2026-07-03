#!/usr/bin/env python3
"""
原生 SenseVoice STT 测试 — 不经过 JVS / Rust VAD / 声纹过滤 / L3 意图路由。

链路（极简）::

  麦克风 (16kHz mono) → funasr_onnx.SenseVoiceSmall → 打印识别文本

用于对比「产品里识别差」是否由前置链路（VAD 截断、重采样、声纹裁剪、HTTP 等）引起。

依赖（与 voice_server 同环境）::

  pip install funasr-onnx onnxruntime numpy sounddevice soundfile

模型目录（默认）::

  data/models/voice/stt/SenseVoiceSmall-onnx/
  需含 model_quant.onnx（可从 HuggingFace haixuantao/SenseVoiceSmall-onnx 下载）

用法（仓库根目录）::

  python scripts/test_sensevoice_pure.py                 # 交互：回车开始/结束录音
  python scripts/test_sensevoice_pure.py --record 5      # 固定录 5 秒
  python scripts/test_sensevoice_pure.py --file foo.wav   # 识别已有 WAV
  python scripts/test_sensevoice_pure.py --language en    # 强制英文（默认 auto）
  python scripts/test_sensevoice_pure.py --list-devices  # 列出麦克风
  python scripts/test_sensevoice_pure.py --compare-jvs   # 同一段音频再跑 JVS 对比

环境变量（可选）::

  JACHIN_VOICE_MODEL_ROOT   模型根目录（默认 data/models/voice）
  JACHIN_VOICE_STT_DIR      STT 子目录（默认 stt/SenseVoiceSmall-onnx）
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL_ROOT = Path(
    os.getenv("JACHIN_VOICE_MODEL_ROOT", str(ROOT / "data" / "models" / "voice"))
)
DEFAULT_STT_REL = os.getenv("JACHIN_VOICE_STT_DIR", r"stt\SenseVoiceSmall-onnx")
DEFAULT_JVS = os.getenv("JACHIN_VOICE_SERVER_URL", "http://127.0.0.1:18982").rstrip("/")
SAMPLE_RATE = 16000
_SENSEVOICE_TAG_RE = re.compile(r"<\|.*?\|>")


@dataclass
class PureSttResult:
    raw: str
    cleaned: str
    postprocessed: str
    latency_ms: int
    language: str
    use_itn: bool


def resolve_stt_dir(model_root: Path, stt_rel: str) -> Path:
    return (model_root / stt_rel).resolve()


def check_model(stt_dir: Path) -> str | None:
    onnx = stt_dir / "model_quant.onnx"
    if not stt_dir.is_dir():
        return f"模型目录不存在: {stt_dir}"
    if not onnx.is_file():
        return (
            f"缺少 {onnx.name}，请先下载 SenseVoiceSmall-onnx 完整包。\n"
            f"  参考: data/models/voice/stt/SenseVoiceSmall-onnx/README.md"
        )
    return None


def load_engine(stt_dir: Path):
    from funasr_onnx import SenseVoiceSmall

    print(f"[加载] SenseVoice ONNX: {stt_dir}")
    t0 = time.perf_counter()
    engine = SenseVoiceSmall(str(stt_dir), batch_size=1, quantize=True)
    print(f"[加载] 完成 ({(time.perf_counter() - t0) * 1000:.0f} ms)")
    return engine


def sanitize_tags(text: str) -> str:
    if not text:
        return ""
    cleaned = _SENSEVOICE_TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", cleaned).strip()


def transcribe_pure(
    engine,
    audio_f32,
    *,
    language: str = "auto",
    use_itn: bool = True,
) -> PureSttResult:
    import numpy as np
    from funasr_onnx.utils.postprocess_utils import rich_transcription_postprocess

    audio = np.asarray(audio_f32, dtype=np.float32)
    t0 = time.perf_counter()
    raw_list = engine(audio, fs=SAMPLE_RATE, language=language, use_itn=use_itn)
    raw = raw_list[0] if raw_list else ""
    post = rich_transcription_postprocess(raw).strip() if raw else ""
    if not post:
        post = (raw or "").strip()
    cleaned = sanitize_tags(post or raw)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    return PureSttResult(
        raw=raw or "",
        cleaned=cleaned,
        postprocessed=post,
        latency_ms=latency_ms,
        language=language,
        use_itn=use_itn,
    )


def print_result(result: PureSttResult, *, label: str = "SenseVoice 原生") -> None:
    print()
    print(f"── {label} ──")
    print(f"  语言参数 : {result.language}")
    print(f"  use_itn  : {result.use_itn}")
    print(f"  推理耗时 : {result.latency_ms} ms")
    if result.raw and result.raw != result.cleaned:
        print(f"  原始输出 : {result.raw}")
    print(f"  识别文本 : {result.cleaned or '(空)'}")
    print()


def load_wav_f32(path: Path) -> tuple:
    import numpy as np
    import soundfile as sf

    data, fs = sf.read(str(path), dtype="float32", always_2d=False)
    if isinstance(data, np.ndarray) and data.ndim > 1:
        data = data.mean(axis=1)
    audio = np.asarray(data, dtype=np.float32)
    if fs != SAMPLE_RATE:
        target_len = max(1, int(len(audio) * SAMPLE_RATE / fs))
        x_old = np.linspace(0.0, 1.0, len(audio), dtype=np.float64)
        x_new = np.linspace(0.0, 1.0, target_len, dtype=np.float64)
        audio = np.interp(x_new, x_old, audio.astype(np.float64)).astype(np.float32)
        print(f"[重采样] {fs} Hz → {SAMPLE_RATE} Hz ({len(audio) / SAMPLE_RATE:.2f}s)")
    return audio, SAMPLE_RATE


def record_fixed(duration_sec: float, device: int | None) -> bytes:
    import sounddevice as sd
    import soundfile as sf

    duration_sec = max(0.5, min(duration_sec, 120.0))
    print(f"[录音] {duration_sec:.1f}s @ {SAMPLE_RATE} Hz — 请说话…")
    frames = int(duration_sec * SAMPLE_RATE)
    audio = sd.rec(
        frames,
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        device=device,
    )
    sd.wait()
    buf = io.BytesIO()
    sf.write(buf, audio, SAMPLE_RATE, subtype="PCM_16", format="WAV")
    print("[录音] 完成")
    return buf.getvalue()


def record_ptt(device: int | None) -> bytes:
    """回车开始、再回车结束（Push-to-Talk）。"""
    import numpy as np
    import sounddevice as sd
    import soundfile as sf

    chunks: list[np.ndarray] = []
    stop = threading.Event()

    def callback(indata, _frames, _time_info, status):
        if status:
            print(f"[录音] {status}", file=sys.stderr)
        chunks.append(indata.copy())

    print("[PTT] 按 Enter 开始录音…")
    try:
        input()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消")
        return b""

    print("[PTT] 录音中… 再按 Enter 结束")
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
    stop.set()

    if not chunks:
        print("[录音] 无音频")
        return b""

    audio = np.concatenate(chunks, axis=0)
    duration = len(audio) / SAMPLE_RATE
    print(f"[录音] 完成 ({duration:.2f}s)")
    buf = io.BytesIO()
    sf.write(buf, audio, SAMPLE_RATE, subtype="PCM_16", format="WAV")
    return buf.getvalue()


def wav_bytes_to_f32(wav_bytes: bytes):
    import numpy as np
    import soundfile as sf

    data, fs = sf.read(io.BytesIO(wav_bytes), dtype="float32", always_2d=False)
    if isinstance(data, np.ndarray) and data.ndim > 1:
        data = data.mean(axis=1)
    audio = np.asarray(data, dtype=np.float32)
    if fs != SAMPLE_RATE:
        target_len = max(1, int(len(audio) * SAMPLE_RATE / fs))
        x_old = np.linspace(0.0, 1.0, len(audio), dtype=np.float64)
        x_new = np.linspace(0.0, 1.0, target_len, dtype=np.float64)
        audio = np.interp(x_new, x_old, audio.astype(np.float64)).astype(np.float32)
    return audio


def transcribe_jvs(wav_bytes: bytes, base_url: str) -> dict | None:
    boundary = f"----sensevoice-compare-{int(time.time() * 1000)}"
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(b'Content-Disposition: form-data; name="audio"; filename="capture.wav"\r\n')
    body.extend(b"Content-Type: audio/wav\r\n\r\n")
    body.extend(wav_bytes)
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        f"{base_url}/v1/stt/transcribe",
        data=bytes(body),
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        print(f"[JVS 对比] 无法连接 {base_url}: {e}")
        return None


def list_input_devices() -> int:
    import sounddevice as sd

    print(sd.query_devices())
    default_in = sd.default.device[0]
    print(f"\n默认输入设备 index: {default_in}")
    return 0


def run_once(
    engine,
    *,
    wav_bytes: bytes | None = None,
    file_path: Path | None = None,
    record_sec: float | None = None,
    ptt: bool = False,
    device: int | None = None,
    language: str = "auto",
    use_itn: bool = True,
    compare_jvs: bool = False,
    jvs_url: str = DEFAULT_JVS,
    save_wav: Path | None = None,
) -> int:
    if file_path is not None:
        if not file_path.is_file():
            print(f"[错误] 文件不存在: {file_path}")
            return 1
        audio, _ = load_wav_f32(file_path)
        wav_bytes = file_path.read_bytes()
    elif wav_bytes is None:
        if ptt:
            wav_bytes = record_ptt(device)
        elif record_sec is not None:
            wav_bytes = record_fixed(record_sec, device)
        else:
            wav_bytes = record_ptt(device)
        if not wav_bytes:
            return 1
        audio = wav_bytes_to_f32(wav_bytes)
    else:
        audio = wav_bytes_to_f32(wav_bytes)

    if save_wav is not None:
        save_wav.write_bytes(wav_bytes)
        print(f"[另存] {save_wav.resolve()}")

    result = transcribe_pure(engine, audio, language=language, use_itn=use_itn)
    print_result(result)

    if compare_jvs and wav_bytes:
        print("── JVS 对比（同一段 WAV 走 voice_server）──")
        jvs = transcribe_jvs(wav_bytes, jvs_url)
        if jvs:
            print(f"  识别文本 : {(jvs.get('text') or '').strip() or '(空)'}")
            print(f"  置信度   : {jvs.get('confidence')}")
            print(f"  时长(ms) : {jvs.get('duration_ms')}")
            print()
            pure_text = result.cleaned
            jvs_text = (jvs.get("text") or "").strip()
            if pure_text == jvs_text:
                print("[对比] 原生与 JVS 结果一致 — 差异更可能来自产品前置链路（VAD/声纹/截断）")
            else:
                print("[对比] 原生与 JVS 结果不一致 — 检查 JVS 后处理或模型路径是否相同")
    return 0


def interactive_loop(
    engine,
    *,
    device: int | None,
    language: str,
    use_itn: bool,
    compare_jvs: bool,
    jvs_url: str,
) -> int:
    print("=" * 60)
    print("  SenseVoice 原生 STT 测试（无 JVS / 无 VAD / 无声纹）")
    print("=" * 60)
    print("  1) 回车开始/结束录音 (PTT)")
    print("  2) 固定秒数录音")
    print("  3) 识别 WAV 文件")
    print("  4) 切换语言 (当前: {})".format(language))
    print("  5) 切换 use_itn (当前: {})".format(use_itn))
    print("  0) 退出")
    print()

    while True:
        try:
            choice = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见。")
            return 0

        if choice in ("0", "q", "quit", "exit"):
            print("再见。")
            return 0
        if choice == "1":
            run_once(
                engine,
                ptt=True,
                device=device,
                language=language,
                use_itn=use_itn,
                compare_jvs=compare_jvs,
                jvs_url=jvs_url,
            )
        elif choice == "2":
            try:
                sec = float(input("录音秒数（默认 5）: ").strip() or "5")
            except ValueError:
                print("[错误] 请输入数字")
                continue
            run_once(
                engine,
                record_sec=sec,
                device=device,
                language=language,
                use_itn=use_itn,
                compare_jvs=compare_jvs,
                jvs_url=jvs_url,
            )
        elif choice == "3":
            p = input("WAV 路径: ").strip().strip('"')
            if p:
                run_once(
                    engine,
                    file_path=Path(p),
                    language=language,
                    use_itn=use_itn,
                    compare_jvs=compare_jvs,
                    jvs_url=jvs_url,
                )
        elif choice == "4":
            lang = input("语言 (auto/zh/en/ja/yue/ko，默认 auto): ").strip() or "auto"
            language = lang
            print(f"[设置] language={language}")
        elif choice == "5":
            use_itn = not use_itn
            print(f"[设置] use_itn={use_itn}")
        else:
            print("无效选项")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="原生 SenseVoice STT：麦克风 → funasr_onnx → 文本（绕过 JVS 全链路）",
    )
    p.add_argument(
        "--model-dir",
        type=Path,
        default=None,
        help="SenseVoiceSmall-onnx 目录（默认 JACHIN_VOICE_MODEL_ROOT + STT 子目录）",
    )
    p.add_argument("--file", type=Path, help="识别已有 WAV")
    p.add_argument("--record", type=float, metavar="SEC", help="固定录音秒数")
    p.add_argument("--ptt", action="store_true", help="回车开始/结束录音（默认交互模式）")
    p.add_argument("--device", type=int, default=None, help="sounddevice 输入设备编号")
    p.add_argument(
        "--language",
        default="auto",
        choices=["auto", "zh", "en", "ja", "yue", "ko"],
        help="SenseVoice language 参数（默认 auto）",
    )
    p.add_argument(
        "--no-itn",
        action="store_true",
        help="关闭逆文本归一化 use_itn（英文有时 ITN 会干扰）",
    )
    p.add_argument("--save-wav", type=Path, metavar="PATH", help="保存本次录音 WAV")
    p.add_argument(
        "--compare-jvs",
        action="store_true",
        help="同一段音频再 POST 到 JVS /v1/stt/transcribe 对比",
    )
    p.add_argument("--jvs-url", default=DEFAULT_JVS, help=f"JVS 地址（默认 {DEFAULT_JVS}）")
    p.add_argument("--list-devices", action="store_true", help="列出麦克风设备")
    return p


def main() -> int:
    args = build_parser().parse_args()

    if args.list_devices:
        try:
            return list_input_devices()
        except ImportError:
            print("[错误] pip install sounddevice")
            return 1

    stt_dir = args.model_dir or resolve_stt_dir(DEFAULT_MODEL_ROOT, DEFAULT_STT_REL)
    err = check_model(stt_dir)
    if err:
        print(f"[错误] {err}")
        return 1

    try:
        import funasr_onnx  # noqa: F401
        import numpy as np  # noqa: F401
    except ImportError:
        print(
            "[错误] 缺少依赖。请在 voice_server 环境中安装:\n"
            "  pip install funasr-onnx onnxruntime numpy sounddevice soundfile"
        )
        return 1

    engine = load_engine(stt_dir)
    use_itn = not args.no_itn

    if args.file or args.record is not None or args.ptt:
        return run_once(
            engine,
            file_path=args.file,
            record_sec=args.record,
            ptt=args.ptt or (args.record is None and args.file is None),
            device=args.device,
            language=args.language,
            use_itn=use_itn,
            compare_jvs=args.compare_jvs,
            jvs_url=args.jvs_url.rstrip("/"),
            save_wav=args.save_wav,
        )

    return interactive_loop(
        engine,
        device=args.device,
        language=args.language,
        use_itn=use_itn,
        compare_jvs=args.compare_jvs,
        jvs_url=args.jvs_url.rstrip("/"),
    )


if __name__ == "__main__":
    raise SystemExit(main())
