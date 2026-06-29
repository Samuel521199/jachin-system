#!/usr/bin/env python3
"""
常驻麦克风监听：仅当识别到唤醒句时才采集后续语音、请求 L3 回复并 TTS 朗读。

默认唤醒句：「快来听我说话」（可用 --wake-word 修改）。

前置：
  1. JVS: python voice_server/main.py  或 start-layer3.ps1 已拉起 18982
  2. L3:  python -m l3_node --gateway   （HTTP 18991，用于 agent/run 回复）
  3. pip install sounddevice soundfile numpy

用法（仓库根目录）::

  python scripts/test_voice_wake_listen.py -v
  python scripts/test_voice_wake_listen.py --wake-word "快来听我说话" --window-sec 4
  python scripts/test_voice_wake_listen.py --mic-test

环境变量（可选）::
  JACHIN_VOICE_SERVER_URL   JVS 根地址
  JACHIN_L3_HTTP_BASE       L3 HTTP 根地址（默认 http://127.0.0.1:18991）
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import queue
import subprocess
import sys
import threading
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
DEFAULT_JVS = os.getenv("JACHIN_VOICE_SERVER_URL", "http://127.0.0.1:18982").rstrip("/")
DEFAULT_L3 = os.getenv("JACHIN_L3_HTTP_BASE", "http://127.0.0.1:18991").rstrip("/")
DEFAULT_WAKE = "快来听我说话"

SAMPLE_RATE = 16000
CHUNK = 512
# 长中文唤醒句需更长窗口（默认 4s；「快来听我说话」正常语速约 2.5～3.5s）
DEFAULT_WINDOW_SEC = 4.0
MIN_POLL_INTERVAL = 1.5
MIN_RMS_KWS = 0.004
COOLDOWN_SEC = 1.5
LISTENING_TIMEOUT_SEC = 8.0
DEFAULT_CONVERSATION_SEC = 60.0

SPEECH_RMS = 0.006
# 出声期间（TTS/答应）打断阈值抬高，减少喇叭漏音误触
BARGE_RMS_PLAYBACK = 0.012
BARGE_MIN_FRAMES = 7  # ~224ms @ 32ms/chunk
BARGE_COOLDOWN_SEC = 0.45  # 一次开口只急停一轮
BARGE_REARM_SEC = 8.0  # 打断后等待新指令的最长时间
RING_BUFFER_SEC = 2.0  # 打断前保留的音频（加长防句首被切）
RING_PREROLL_CHUNKS = 10  # 检测到说话起点后再往前多留 ~320ms
SPEECH_RMS_REARM = 0.004  # 接话阶段略降低起句门限
SILENCE_FRAMES_END = 25
MAX_FRAMES = 468
MIN_SPEECH_FRAMES = 10


def normalize_wake_text(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalnum() or ord(c) > 0x4e00)


def transcript_matches_wake(transcript: str, wake_word: str) -> bool:
    t = normalize_wake_text(transcript)
    w = normalize_wake_text(wake_word)
    if not t or not w:
        return False
    if w in t or t in w:
        return True
    # 长句：连续子串命中（STT 常漏字/错字）
    min_sub = min(4, max(3, len(w) // 2))
    for i in range(len(w) - min_sub + 1):
        sub = w[i : i + min_sub]
        if sub in t:
            return True
    return False


def strip_wake_prefix(text: str, wake_word: str) -> str:
    t = text.strip()
    w = wake_word.strip()
    if not t:
        return ""
    if t.lower() == w.lower():
        return ""
    tn, wn = normalize_wake_text(t), normalize_wake_text(w)
    if wn and tn.startswith(wn) and len(t) > len(w):
        return t[len(w) :].strip()
    return t


def pcm_f32_to_wav(samples: list[float], sr: int = SAMPLE_RATE) -> bytes:
    buf = io.BytesIO()
    import numpy as np

    arr = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    pcm16 = (arr * 32767.0).astype(np.int16)
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm16.tobytes())
    return buf.getvalue()


def rms_f32(chunk: list[float]) -> float:
    if not chunk:
        return 0.0
    s = sum(x * x for x in chunk)
    return math.sqrt(s / len(chunk))


def _http_post_multipart_stt(url: str, wav_bytes: bytes, timeout: float = 90.0) -> dict:
    boundary = f"----jachin-wake-{int(time.time() * 1000)}"
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        f"Content-Disposition: form-data; name=\"audio\"; filename=\"speech.wav\"\r\n".encode()
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


def _http_post_json(url: str, payload: dict, timeout: float = 180.0) -> bytes:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def jvs_stt(base: str, wav_bytes: bytes) -> str:
    url = f"{base}/v1/stt/transcribe"
    result = _http_post_multipart_stt(url, wav_bytes)
    return (result.get("text") or "").strip()


def jvs_tts_play(base: str, text: str, playback: InterruptiblePlayback) -> bool:
    if not text.strip():
        return False
    url = f"{base}/v1/tts/synthesize"
    try:
        wav_bytes = _http_post_json(url, {"text": text})
        return playback.play_wav_bytes(wav_bytes, "tts")
    except Exception as e:
        print(f"[TTS] 失败: {e}")
        return False


def l3_agent_run(base: str, user_input: str, chat_id: str, timeout: float) -> str:
    url = f"{base}/api/v3/agent/run"
    body = {
        "user_input": user_input,
        "chat_id": chat_id,
        "max_iterations": 8,
    }
    raw = _http_post_json(url, body, timeout=timeout)
    data = json.loads(raw.decode("utf-8"))
    if data.get("error"):
        raise RuntimeError(data["error"])
    return (data.get("answer") or "").strip()


def inject_desktop_user(exe: Path, text: str) -> None:
    subprocess.Popen(
        [str(exe), "--jachin-voice-sim", "user", text, "listening"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def play_earcon(freq: float = 880.0, ms: int = 120) -> None:
    """滴一声。Windows 上用 winsound，避免与麦克风 InputStream 抢 sounddevice。"""
    if sys.platform == "win32":
        try:
            import winsound

            winsound.Beep(int(freq), ms)
            return
        except Exception:
            pass
    try:
        import numpy as np
        import sounddevice as sd

        n = int(SAMPLE_RATE * ms / 1000)
        t = np.arange(n, dtype=np.float32) / SAMPLE_RATE
        tone = 0.25 * np.sin(2 * math.pi * freq * t)
        with sd.OutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32") as stream:
            stream.write(tone)
    except Exception as e:
        print(f"[Earcon] 播放失败: {e}")


def wav_duration_sec(wav_bytes: bytes) -> float:
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            return wf.getnframes() / float(wf.getframerate())
    except Exception:
        return 1.0


class InterruptiblePlayback:
    """可打断播放。禁止 sd.stop() 全局调用——会掐死麦克风 InputStream。"""

    def __init__(self) -> None:
        self._playing = False
        self._stop_flag = False
        self._lock = threading.Lock()
        self._play_thread: threading.Thread | None = None

    @property
    def is_playing(self) -> bool:
        return self._playing

    def stop(self) -> None:
        with self._lock:
            self._stop_flag = True
            self._playing = False
        if sys.platform == "win32":
            try:
                import winsound

                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass

    def play_wav_bytes(self, wav_bytes: bytes, label: str = "audio") -> bool:
        self.stop()
        with self._lock:
            self._stop_flag = False
            self._playing = True

        result_ok = True

        def _run_win() -> None:
            nonlocal result_ok
            tmp_path: str | None = None
            try:
                import winsound
                import tempfile

                # SND_MEMORY | SND_ASYNC 在 Windows 上会 RuntimeError，改用临时文件 + 异步
                fd, tmp_path = tempfile.mkstemp(suffix=".wav", prefix="jachin_voice_")
                os.write(fd, wav_bytes)
                os.close(fd)
                flags = winsound.SND_FILENAME | winsound.SND_ASYNC
                # PlaySound 异步模式成功时常返回 None，不能用 if not 判断失败
                winsound.PlaySound(tmp_path, flags)
                duration = wav_duration_sec(wav_bytes)
                deadline = time.monotonic() + duration + 0.35
                while time.monotonic() < deadline:
                    with self._lock:
                        if self._stop_flag:
                            winsound.PlaySound(None, winsound.SND_PURGE)
                            result_ok = False
                            if verbose_playback:
                                print(f"[打断] 已停止播放 ({label})")
                            return
                    time.sleep(0.02)
            except Exception as e:
                print(f"[播放/{label}] 失败: {e}")
                result_ok = False
            finally:
                if tmp_path:
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass

        def _run_sd() -> None:
            nonlocal result_ok
            try:
                import sounddevice as sd
                import soundfile as sf

                data, fs = sf.read(io.BytesIO(wav_bytes), dtype="float32")
                if data.ndim > 1:
                    data = data[:, 0]
                chunk_size = 2048
                idx = 0
                with sd.OutputStream(
                    samplerate=int(fs),
                    channels=1,
                    dtype="float32",
                ) as stream:
                    while idx < len(data):
                        with self._lock:
                            if self._stop_flag:
                                result_ok = False
                                stream.stop()
                                return
                        end = min(idx + chunk_size, len(data))
                        stream.write(data[idx:end])
                        idx = end
            except Exception as e:
                print(f"[播放/{label}] 失败: {e}")
                result_ok = False

        runner = _run_win if sys.platform == "win32" else _run_sd
        self._play_thread = threading.Thread(target=runner, daemon=True)
        self._play_thread.start()
        while self._play_thread.is_alive():
            self._play_thread.join(timeout=0.05)
        with self._lock:
            self._playing = False
            self._stop_flag = False
        if result_ok and verbose_playback:
            print(f"[播放/{label}] 完成 ({wav_duration_sec(wav_bytes):.1f}s)")
        elif result_ok and label == "tts":
            print(f"[TTS] 朗读完成 ({wav_duration_sec(wav_bytes):.1f}s)")
        return result_ok


# 模块级 verbose 开关，供 InterruptiblePlayback 打印
verbose_playback = False


class BargeInDetector:
    """连续人声检测 → 触发打断（对齐桌面 VAD 主路径）。"""

    def __init__(self) -> None:
        self.speech_frames = 0
        self.cooldown_until = 0.0

    def reset(self) -> None:
        self.speech_frames = 0
        self.cooldown_until = 0.0

    def feed(self, chunk: list[float], elevated: bool) -> bool:
        now = time.monotonic()
        if now < self.cooldown_until:
            self.speech_frames = 0
            return False
        thresh = BARGE_RMS_PLAYBACK if elevated else SPEECH_RMS
        if rms_f32(chunk) >= thresh:
            self.speech_frames += 1
            if self.speech_frames >= BARGE_MIN_FRAMES:
                self.speech_frames = 0
                self.cooldown_until = now + BARGE_COOLDOWN_SEC
                return True
        else:
            self.speech_frames = 0
        return False


def load_wake_ack_wav(pool_dir: Path | None, pool_ids: list[str]) -> tuple[bytes | None, Path | None]:
    if not pool_dir or not pool_dir.is_dir():
        return None, None
    existing = [pid for pid in pool_ids if (pool_dir / f"{pid}.wav").is_file()]
    if not existing:
        return None, None
    idx = int(time.time() * 1000) % len(existing)
    pid = existing[idx]
    path = pool_dir / f"{pid}.wav"
    return path.read_bytes(), path


def play_verbal_ack(pool_dir: Path | None, playback: InterruptiblePlayback) -> None:
    wav, path = load_wake_ack_wav(
        pool_dir, ["im_here", "yes", "how_can_i_help", "please_say"]
    )
    if not wav or not path:
        print("[答应] 未找到 wake_ack WAV，跳过（运行: python scripts/gen_wake_ack_wavs.py）")
        return
    print(f"[答应] 播放 {path.name} …")
    ok = playback.play_wav_bytes(wav, "verbal_ack")
    if ok:
        print("[答应] 完成 — 请直接说你的指令")
    else:
        print("[答应] 播放失败，见上方错误；你仍可继续说指令")


class SttAssistedKws:
    def __init__(self, wake_word: str, window_samples: int) -> None:
        self.wake_word = wake_word.strip()
        self.window_samples = window_samples
        self.window: list[float] = []
        self.last_poll = time.monotonic() - MIN_POLL_INTERVAL

    def feed(self, chunk: list[float]) -> tuple[list[float] | None, float]:
        for s in chunk:
            if len(self.window) >= self.window_samples:
                self.window.pop(0)
            self.window.append(s)
        rms = rms_f32(chunk)
        if len(self.window) < self.window_samples:
            return None, rms
        if time.monotonic() - self.last_poll < MIN_POLL_INTERVAL:
            return None, rms
        win_rms = rms_f32(self.window)
        if win_rms < MIN_RMS_KWS:
            return None, rms
        self.last_poll = time.monotonic()
        return list(self.window), rms


class EnergyEndpointing:
    """唤醒后指令截断：能量门限 + 尾音静音。"""

    def __init__(self) -> None:
        self.speaking = False
        self.buffer: list[float] = []
        self.silence_frames = 0
        self.total_frames = 0

    def reset(self) -> None:
        self.speaking = False
        self.buffer.clear()
        self.silence_frames = 0
        self.total_frames = 0

    def seed_from_ring(self, ring: list[list[float]], *, rearm: bool = False) -> None:
        """打断后把环形缓冲拼进当前句；rearm 时用更低门限 + 前滚，避免「我」等起首被吃。"""
        self.reset()
        if not ring:
            return

        thresh = SPEECH_RMS_REARM if rearm else SPEECH_RMS
        preroll = RING_PREROLL_CHUNKS if rearm else 4

        onset = 0
        for i, ch in enumerate(ring):
            if rms_f32(ch) >= thresh:
                onset = max(0, i - preroll)
                break

        self.speaking = True
        self.silence_frames = 0
        for ch in ring[onset:]:
            self.buffer.extend(ch)
        self.total_frames = max(1, len(self.buffer) // CHUNK)

    def feed(self, chunk: list[float], *, rms_floor: float | None = None) -> list[float] | None:
        loud_thresh = rms_floor if rms_floor is not None else SPEECH_RMS
        loud = rms_f32(chunk) >= loud_thresh
        if not self.speaking:
            if loud:
                self.speaking = True
                self.buffer = list(chunk)
                self.silence_frames = 0
                self.total_frames = 1
            return None

        self.buffer.extend(chunk)
        self.total_frames += 1
        if loud:
            self.silence_frames = 0
        else:
            self.silence_frames += 1

        should_end = (
            self.silence_frames >= SILENCE_FRAMES_END
            or self.total_frames >= MAX_FRAMES
        )
        if not should_end:
            return None

        self.speaking = False
        frames = self.total_frames
        self.silence_frames = 0
        self.total_frames = 0
        if frames < MIN_SPEECH_FRAMES:
            self.buffer.clear()
            return None
        out = list(self.buffer)
        self.buffer.clear()
        return out


def drain_audio_queue(q: queue.Queue, keep_wake_hits: bool = False) -> int:
    """清空积压音频，避免 L3/TTS 阻塞时队列满导致后续听不到。"""
    n = 0
    pending_hits: list[str] = []
    while True:
        try:
            item = q.get_nowait()
            n += 1
            if keep_wake_hits and item == "__WAKE_HIT__":
                pending_hits.append(item)
        except queue.Empty:
            break
    for hit in pending_hits:
        try:
            q.put_nowait(hit)
        except queue.Full:
            break
    return n


def check_jvs(base: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        ok = bool(data.get("ok"))
        if ok:
            stt = data.get("stt_ready", True)
            if not stt:
                print("[警告] JVS ok 但 stt_ready=false，STT 可能 503")
        return ok
    except Exception:
        return False


def check_l3(base: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base}/api/v3/skills", timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def print_input_devices() -> None:
    import sounddevice as sd

    print("[设备] 可用输入麦克风:")
    for i, dev in enumerate(sd.query_devices()):
        if dev.get("max_input_channels", 0) > 0:
            mark = " (默认)" if i == sd.default.device[0] else ""
            print(f"  [{i}] {dev['name']}{mark}")


def run_mic_test(device: int | None, seconds: float = 3.0) -> int:
    import sounddevice as sd

    print_input_devices()
    print(f"[麦克测试] 请对着麦克风说话 {seconds:.0f}s …")
    frames = int(seconds * SAMPLE_RATE)
    audio = sd.rec(
        frames,
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        device=device,
    )
    sd.wait()
    peak = float(abs(audio).max())
    rms = float((audio ** 2).mean() ** 0.5)
    print(f"[麦克测试] peak={peak:.4f} rms={rms:.4f}")
    if peak < 0.01:
        print(
            "[错误] 几乎无信号：检查是否选对麦克风、是否被其它程序占用、系统录音权限"
        )
        print("  可指定设备: --device N  （先看上表编号）")
        return 1
    if rms < MIN_RMS_KWS:
        print(
            f"[警告] 信号偏弱 (rms={rms:.4f} < {MIN_RMS_KWS})，请靠近麦克风或提高音量"
        )
    else:
        print("[OK] 麦克风有有效信号")
    return 0


def run_loop(
    wake_word: str,
    jvs_base: str,
    l3_base: str,
    desktop_exe: Path | None,
    no_tts: bool,
    l3_timeout: float,
    verbose: bool,
    window_sec: float,
    device: int | None,
    conversation_sec: float,
    wake_ack_dir: Path | None,
    stdin_barge: bool,
) -> int:
    global verbose_playback
    verbose_playback = verbose
    try:
        import sounddevice as sd
    except ImportError:
        print("[错误] 需要: pip install sounddevice soundfile numpy")
        return 1

    if not check_jvs(jvs_base):
        print(f"[错误] JVS 未就绪 ({jvs_base})，请先启动 voice_server")
        return 1

    window_samples = max(int(window_sec * SAMPLE_RATE), CHUNK * 4)

    use_desktop = desktop_exe is not None
    if use_desktop:
        if not desktop_exe.is_file():
            print(f"[错误] 桌面 exe 不存在: {desktop_exe}")
            return 1
        print(f"[模式] 桌面陪伴注入: {desktop_exe}")
    else:
        if not check_l3(l3_base):
            print(f"[警告] L3 HTTP 未响应 ({l3_base})，agent/run 可能失败")
        print(f"[模式] L3 HTTP 回复: {l3_base}/api/v3/agent/run")

    in_dev = device if device is not None else sd.default.device[0]
    try:
        dev_info = sd.query_devices(in_dev)
        dev_name = dev_info["name"]
    except Exception:
        dev_name = str(in_dev)

    print("")
    print("=" * 56)
    print(f"  唤醒句: 「{wake_word}」")
    print(f"  KWS 窗口: {window_sec:.1f}s（长句请保持 --window-sec 4 或更大）")
    print(f"  唤醒后对话窗口: {conversation_sec:.0f}s（期间可连续提问，无需重复唤醒句）")
    print("  未听到唤醒句时不会请求 L3 / 不会朗读")
    print("  步骤: 唤醒句 → 提示音 → 连续说指令（对话窗口内）")
    print("  朗读中可直接说话打断（或 stdin 输入 b 模拟 Ctrl+Space）")
    print("  -v 会显示麦克风电平与 STT 轮询结果")
    print("  Ctrl+C 退出")
    print("=" * 56)
    print(f"[麦克] 输入设备 [{in_dev}] {dev_name}")
    print("")

    phase = "kws_idle"
    kws = SttAssistedKws(wake_word, window_samples)
    endpointing = EnergyEndpointing()
    barge_detector = BargeInDetector()
    playback = InterruptiblePlayback()
    conversation_until = 0.0
    audio_q: queue.Queue[list[float] | str] = queue.Queue(maxsize=2048)
    stt_lock = threading.Lock()
    kws_poll_lock = threading.Lock()
    state_lock = threading.Lock()
    l3_busy = False
    l3_abort = False
    barge_latched = False  # 已打断，正在接新指令，禁止重复急停
    barge_latched_until = 0.0
    ring_buffer: list[list[float]] = []
    ring_max_chunks = max(8, int(RING_BUFFER_SEC * SAMPLE_RATE / CHUNK))
    peak_rms = 0.0
    last_meter = time.monotonic()
    chunks_seen = 0

    def append_ring(chunk: list[float]) -> None:
        ring_buffer.append(chunk)
        while len(ring_buffer) > ring_max_chunks:
            ring_buffer.pop(0)

    def trigger_barge_in(source: str) -> bool:
        """返回 True 表示本轮已触发打断（调用方勿重复 feed 当前块）。"""
        nonlocal l3_abort, barge_latched, barge_latched_until
        with state_lock:
            if barge_latched:
                return False
            if not (playback.is_playing or l3_busy):
                return False
            l3_abort = True
            playback.stop()
            barge_latched = True
            barge_latched_until = time.monotonic() + BARGE_REARM_SEC
            barge_detector.reset()
            barge_detector.cooldown_until = time.monotonic() + BARGE_COOLDOWN_SEC
            snap = list(ring_buffer)
        endpointing.seed_from_ring(snap, rearm=True)
        print(f"[打断] {source} → 已停播，请继续说新指令（接话中）…")
        return True

    def stdin_barge_worker() -> None:
        while True:
            try:
                line = sys.stdin.readline()
                if line.strip().lower() in ("b", "interrupt", "stop"):
                    trigger_barge_in("stdin(b)")
            except Exception:
                break

    if stdin_barge:
        threading.Thread(target=stdin_barge_worker, daemon=True).start()
        print("[提示] 打断兜底：在终端输入 b 并回车")

    def stt_worker(wav: bytes, label: str) -> str:
        with stt_lock:
            try:
                return jvs_stt(jvs_base, wav)
            except Exception as e:
                print(f"[STT/{label}] 失败: {e}")
                return ""

    def kws_poll(window: list[float], win_rms: float) -> None:
        if not kws_poll_lock.acquire(blocking=False):
            return
        try:
            print(
                f"[KWS] 发起 STT 轮询 (窗口 rms={win_rms:.4f}, "
                f"{len(window) / SAMPLE_RATE:.1f}s)"
            )
            wav = pcm_f32_to_wav(window)
            text = stt_worker(wav, "kws")
            hit = transcript_matches_wake(text, wake_word)
            print(f"[KWS] STT: 「{text or '(空)'}」 → 命中={hit}")
            if hit:
                audio_q.put("__WAKE_HIT__")
        finally:
            kws_poll_lock.release()

    def on_audio(indata, frames, time_info, status) -> None:
        if status:
            print(f"[麦克] stream status: {status}")
        chunk = indata[:, 0].tolist()
        try:
            audio_q.put_nowait(chunk)
        except queue.Full:
            try:
                audio_q.get_nowait()
                audio_q.put_nowait(chunk)
            except queue.Empty:
                pass

    chat_id = f"voice-wake-test-{int(time.time())}"

    def deliver_command(cmd: str) -> None:
        nonlocal l3_busy, l3_abort, conversation_until, barge_latched
        with state_lock:
            l3_busy = True
            l3_abort = False
            barge_latched = False
        drained = drain_audio_queue(audio_q)
        if drained and verbose:
            print(f"[麦克] L3/TTS 前丢弃积压块 {drained}")

        print(f"[用户] {cmd}")
        answer = ""
        if use_desktop:
            inject_desktop_user(desktop_exe, cmd)
            print("[已注入桌面陪伴链路，等待 L3 + JVS 播报…]")
        else:
            try:
                print("[L3] 思考中…（此时说话可打断）")
                answer = l3_agent_run(l3_base, cmd, chat_id, l3_timeout)
            except Exception as e:
                print(f"[L3 错误] {e}")
                answer = ""

        with state_lock:
            aborted = l3_abort
            l3_busy = False

        if aborted:
            print("[L3] 已打断，丢弃本轮回复")
            conversation_until = max(conversation_until, time.monotonic() + conversation_sec)
            print("[接话] 继续说新指令即可（已在听）")
            return

        if answer:
            preview = answer[:200] + ("…" if len(answer) > 200 else "")
            print(f"[助手] {preview}")
            if not no_tts:
                drain_audio_queue(audio_q)
                jvs_tts_play(jvs_base, answer, playback)

        drain_audio_queue(audio_q)
        endpointing.reset()
        conversation_until = max(conversation_until, time.monotonic() + conversation_sec)
        print(
            f"[对话] 继续监听（{conversation_sec:.0f}s 内可直接说，朗读中开口可打断）"
        )

    def on_utterance_ready(utterance: list[float]) -> None:
        wav = pcm_f32_to_wav(utterance)
        text = stt_worker(wav, "utterance")
        cmd = strip_wake_prefix(text, wake_word)
        print(f"[指令 STT] {text}")
        if not cmd:
            print("[跳过] 未识别到有效指令，请再说一遍")
            endpointing.reset()
            return
        threading.Thread(target=deliver_command, args=(cmd,), daemon=True).start()

    def clear_barge_latch_if_expired(now: float) -> None:
        nonlocal barge_latched, barge_latched_until
        if barge_latched and now > barge_latched_until:
            barge_latched = False
            endpointing.reset()
            print("[接话] 超时未听到新指令，继续等待…")

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=CHUNK,
        device=device,
        callback=on_audio,
    ):
        print("[麦克] 采集已启动，请说话…\n")
        while True:
            now = time.monotonic()

            if phase == "conversation" and now > conversation_until:
                with state_lock:
                    busy = l3_busy or playback.is_playing
                if not busy and not barge_latched:
                    print("[对话] 会话结束，等待唤醒句…")
                    phase = "kws_idle"
                    kws = SttAssistedKws(wake_word, window_samples)
                    drain_audio_queue(audio_q)

            clear_barge_latch_if_expired(now)

            try:
                item = audio_q.get(timeout=0.1)
            except queue.Empty:
                if verbose and phase == "kws_idle" and now - last_meter >= 2.0:
                    print(
                        f"[麦克] 电平 peak_rms≈{peak_rms:.4f} "
                        f"(需>{MIN_RMS_KWS:.4f} 才轮询 STT) chunks={chunks_seen}"
                    )
                    peak_rms = 0.0
                    last_meter = now
                continue

            if item == "__WAKE_HIT__":
                if phase == "kws_idle":
                    print(f"[唤醒] 命中「{wake_word}」")
                    play_earcon()
                    play_verbal_ack(wake_ack_dir, playback)
                    phase = "conversation"
                    conversation_until = now + conversation_sec
                    endpointing.reset()
                    barge_detector.reset()
                    drain_audio_queue(audio_q)
                    print(
                        f"[对话] 已进入对话模式 {conversation_sec:.0f}s，"
                        "可直接连续提问"
                    )
                elif playback.is_playing or l3_busy:
                    trigger_barge_in("唤醒句(辅路)")
                    play_earcon()
                    play_verbal_ack(wake_ack_dir, playback)
                    endpointing.reset()
                    conversation_until = now + conversation_sec
                continue

            chunk = item
            chunks_seen += 1
            cr = rms_f32(chunk)
            if cr > peak_rms:
                peak_rms = cr
            append_ring(chunk)

            with state_lock:
                masking = playback.is_playing
                thinking = l3_busy
                latched = barge_latched

            barged_this_chunk = False
            if phase == "conversation" and not latched and (masking or thinking):
                if barge_detector.feed(chunk, elevated=masking):
                    barged_this_chunk = trigger_barge_in("人声(VAD)")
                    if barged_this_chunk:
                        latched = True

            if phase == "kws_idle":
                with state_lock:
                    if l3_busy:
                        continue
                window, _ = kws.feed(chunk)
                if window is not None:
                    win_rms = rms_f32(window)
                    threading.Thread(
                        target=kws_poll,
                        args=(window, win_rms),
                        daemon=True,
                    ).start()
            elif phase == "conversation":
                with state_lock:
                    masking = playback.is_playing
                    thinking = l3_busy
                    latched = barge_latched
                if masking and not latched:
                    continue
                if thinking and not latched:
                    continue
                if barged_this_chunk:
                    continue  # 已在 seed_from_ring 中计入当前块
                rms_floor = SPEECH_RMS_REARM if latched else None
                utterance = endpointing.feed(chunk, rms_floor=rms_floor)
                if utterance is not None:
                    with state_lock:
                        barge_latched = False
                    on_utterance_ready(utterance)


def main() -> int:
    parser = argparse.ArgumentParser(description="唤醒句监听联调（STT 辅助 KWS）")
    parser.add_argument(
        "--wake-word",
        default=DEFAULT_WAKE,
        help=f"唤醒句（默认: {DEFAULT_WAKE}）",
    )
    parser.add_argument("--jvs-base", default=DEFAULT_JVS, help="JVS 根 URL")
    parser.add_argument("--l3-base", default=DEFAULT_L3, help="L3 HTTP 根 URL")
    parser.add_argument(
        "--desktop-exe",
        type=Path,
        default=None,
        help="若指定，用户指令注入 jachin-desktop --jachin-voice-sim",
    )
    parser.add_argument("--no-tts", action="store_true", help="L3 模式不朗读 TTS")
    parser.add_argument(
        "--l3-timeout",
        type=float,
        default=180.0,
        help="L3 agent/run 超时秒数",
    )
    parser.add_argument(
        "--window-sec",
        type=float,
        default=DEFAULT_WINDOW_SEC,
        help=f"KWS 分析窗口秒数（默认 {DEFAULT_WINDOW_SEC}，长唤醒句建议 4～5）",
    )
    parser.add_argument(
        "--device",
        type=int,
        default=None,
        help="sounddevice 输入设备编号（--list-devices 查看）",
    )
    parser.add_argument("--list-devices", action="store_true", help="列出麦克风后退出")
    parser.add_argument(
        "--mic-test",
        action="store_true",
        help="测试麦克风 3 秒后退出",
    )
    parser.add_argument(
        "--conversation-sec",
        type=float,
        default=DEFAULT_CONVERSATION_SEC,
        help=f"唤醒后连续对话秒数（默认 {DEFAULT_CONVERSATION_SEC}）",
    )
    parser.add_argument(
        "--wake-ack-dir",
        type=Path,
        default=None,
        help="口头答应 WAV 目录（默认尝试 public/audio/wake_ack 与 LOCALAPPDATA）",
    )
    parser.add_argument(
        "--stdin-barge",
        action="store_true",
        help="终端输入 b 回车模拟打断（桌面端用 Ctrl+Space）",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="打印麦克风电平")
    args = parser.parse_args()

    if args.list_devices:
        print_input_devices()
        return 0

    if args.mic_test:
        return run_mic_test(args.device)

    wake = (args.wake_word or "").strip()
    if len(wake) < 2:
        print("[错误] 唤醒句太短")
        return 1

    # 长唤醒句自动加长窗口
    window_sec = max(args.window_sec, min(5.0, len(wake) * 0.45 + 1.5))

    wake_ack_dir = args.wake_ack_dir
    if wake_ack_dir is None:
        candidates = [
            ROOT / "clients" / "desktop" / "public" / "audio" / "wake_ack",
            Path(os.environ.get("LOCALAPPDATA", "")) / "jachin" / "desktop" / "audio" / "wake_ack",
        ]
        for c in candidates:
            if c.is_dir() and any(c.glob("*.wav")):
                wake_ack_dir = c
                break

    return run_loop(
        wake_word=wake,
        jvs_base=args.jvs_base.rstrip("/"),
        l3_base=args.l3_base.rstrip("/"),
        desktop_exe=args.desktop_exe,
        no_tts=args.no_tts,
        l3_timeout=args.l3_timeout,
        verbose=args.verbose,
        window_sec=window_sec,
        device=args.device,
        conversation_sec=max(10.0, args.conversation_sec),
        wake_ack_dir=wake_ack_dir,
        stdin_barge=args.stdin_barge,
    )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[退出]")
        sys.exit(0)
