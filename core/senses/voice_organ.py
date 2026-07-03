"""
Jachin Voice 仿生器官 — 听觉与喉咙 (Animus Protocol v8.0)

唤醒词 (pvporcupine) -> STT (SpeechRecognition + Whisper) -> OmniSensoryBus -> TTS (edge-tts + pygame)
极低功耗唤醒词阻塞监听，仅检测到 "Hey Jachin" (jarvis) 后才触发录音与识别。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

logger = logging.getLogger(__name__)
console = Console()
# TTS voice baseline: same Kokoro voice used by scripts/trace_kokoro_tts_pipeline.py.
DEFAULT_TTS_VOICE = "zm_053"
DEFAULT_TTS_SPEED = 1.25
DEFAULT_JVS_TTS_URL = "http://127.0.0.1:18982/v1/tts/synthesize"

# 唤醒词：内置 "jarvis" 最接近 "Hey Jachin"；可配置自定义 .ppn 路径
DEFAULT_WAKE_KEYWORD = "jarvis"
_NEXUS_CONFIG = Path.home() / ".jachin" / "nexus_config.json"


def _load_voice_config() -> dict:
    """读取语音配置（唤醒词、自定义 .ppn 路径等）"""
    if not _NEXUS_CONFIG.exists():
        return {}
    try:
        cfg = json.loads(_NEXUS_CONFIG.read_text(encoding="utf-8"))
        return cfg.get("voice") or cfg.get("animus") or {}
    except Exception:
        return {}


def _get_picovoice_access_key() -> str | None:
    """获取 Picovoice AccessKey，用于 pvporcupine 唤醒词"""
    key = (
        os.environ.get("PICOVOICE_ACCESS_KEY")
        or os.environ.get("PVPORCUPINE_ACCESS_KEY")
    )
    if key and key.strip():
        return key.strip()
    cfg = _load_voice_config()
    key = cfg.get("picovoice_access_key") or cfg.get("access_key")
    if key and str(key).strip():
        return str(key).strip()
    return None


def _listen_for_wake_word() -> bool:
    """
    极低功耗唤醒词阻塞监听。检测到唤醒词返回 True，异常或不可用时返回 False。
    使用 pvporcupine + pyaudio，内置 "jarvis" 作为 Hey Jachin 替代。
    """
    access_key = _get_picovoice_access_key()
    if not access_key:
        logger.warning("[Animus] PICOVOICE_ACCESS_KEY 未配置，跳过唤醒词，直接进入录音")
        return True  # 降级：无唤醒词时直接通过

    try:
        import pvporcupine
        import pyaudio
    except ImportError as e:
        logger.warning("[Animus] pvporcupine/pyaudio 未安装: %s，跳过唤醒词", e)
        return True

    cfg = _load_voice_config()
    keyword_paths = cfg.get("wake_keyword_paths")  # 自定义 .ppn 路径列表
    keywords = cfg.get("wake_keywords") or [DEFAULT_WAKE_KEYWORD]

    porcupine = None
    stream = None
    pa = None
    try:
        if keyword_paths and isinstance(keyword_paths, list):
            porcupine = pvporcupine.create(
                access_key=access_key,
                keyword_paths=[str(Path(p).expanduser()) for p in keyword_paths],
                sensitivities=[cfg.get("wake_sensitivity", 0.5)] * len(keyword_paths),
            )
        else:
            # 内置关键词，jarvis 最接近 Hey Jachin
            valid = [k for k in keywords if k in pvporcupine.KEYWORDS]
            if not valid:
                valid = [DEFAULT_WAKE_KEYWORD]
            porcupine = pvporcupine.create(
                access_key=access_key,
                keywords=valid,
                sensitivities=[cfg.get("wake_sensitivity", 0.5)] * len(valid),
            )

        frame_length = porcupine.frame_length
        pa = pyaudio.PyAudio()
        stream = pa.open(
            rate=porcupine.sample_rate,
            channels=1,
            format=pyaudio.paInt16,
            input=True,
            frames_per_buffer=frame_length,
        )

        console.print("[dim magenta][Animus] 唤醒词监听中... 说 \"Hey Jarvis\" 激活[/dim magenta]")
        while True:
            try:
                buf = stream.read(frame_length, exception_on_overflow=False)
                pcm = [int.from_bytes(buf[i : i + 2], "little", signed=True) for i in range(0, len(buf), 2)]
                if len(pcm) >= frame_length:
                    pcm = pcm[:frame_length]
                else:
                    continue
                idx = porcupine.process(pcm)
                if idx >= 0:
                    console.print("[bold green][Animus] ⚡ 唤醒词检测！[/bold green]")
                    return True
            except OSError as e:
                logger.warning("[Animus] 麦克风流异常: %s", e)
                return False
    except Exception as e:
        if "Porcupine" in type(e).__name__ or "porcupine" in str(e).lower():
            logger.warning("[Animus] Porcupine 初始化失败: %s，降级为直接录音", e)
        else:
            logger.warning("[Animus] 唤醒词引擎异常: %s，降级为直接录音", e)
        return True
    except Exception as e:
        logger.exception("[Animus] 唤醒词监听异常: %s", e)
        return False
    finally:
        if porcupine:
            try:
                porcupine.delete()
            except Exception:
                pass
        if stream:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        if pa:
            try:
                pa.terminate()
            except Exception:
                pass


async def speak_text(text: str, voice: str = DEFAULT_TTS_VOICE) -> None:
    """Speak through local JVS Kokoro so legacy voice output matches the desktop baseline."""
    if not text or not text.strip():
        return

    import urllib.request

    def _synthesize_wav() -> bytes:
        payload = json.dumps({"text": text.strip(), "voice": voice, "speed": DEFAULT_TTS_SPEED}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            DEFAULT_JVS_TTS_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()

    tmp_path: str | None = None
    try:
        wav_bytes = await asyncio.to_thread(_synthesize_wav)
        fd, tmp_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        Path(tmp_path).write_bytes(wav_bytes)
        console.print("[cyan][Jachin voice][/cyan]")

        if os.name == "nt":
            import winsound

            await asyncio.to_thread(winsound.PlaySound, tmp_path, winsound.SND_FILENAME)
        else:
            console.print(f"[dim]Audio saved: {tmp_path}[/dim]")
    except Exception as e:
        logger.exception("JVS Kokoro TTS error: %s", e)
        console.print(f"[red][TTS error] {e}[/red]")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

def listen_and_transcribe() -> str | None:
    """
    STT 耳朵：SpeechRecognition 监听麦克风，Whisper API 转文本。
    返回识别到的文本，失败或静音返回 None。
    """
    if not os.environ.get("OPENAI_API_KEY"):
        logger.warning("OPENAI_API_KEY 未设置，Whisper STT 不可用")
        return None

    import io

    import speech_recognition as sr
    from openai import OpenAI

    recognizer = sr.Recognizer()
    try:
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            audio = recognizer.listen(source, timeout=10, phrase_time_limit=15)
    except sr.WaitTimeoutError:
        return None
    except OSError as e:
        logger.warning("麦克风监听异常: %s", e)
        return None
    except Exception as e:
        logger.warning("麦克风监听异常: %s", e)
        return None

    wav_data = audio.get_wav_data()
    client = OpenAI()
    try:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=io.BytesIO(wav_data),
        )
        return (result.text or "").strip() or None
    except Exception as e:
        logger.warning("Whisper 识别异常: %s", e)
        return None


class JachinVoiceInterface:
    """
    语音感官接口：唤醒词 -> 录音 -> 注入总线 -> 等待大脑回复 (TTS)。
    完全通过 OmniSensoryBus 交互。
    """

    def __init__(self, bus=None) -> None:
        from core.event_bus import get_bus

        self._bus = bus or get_bus()
        self._reply_event: asyncio.Event | None = None

    async def _on_brain_reply(self, ev) -> None:
        """总线输出回调：提取 result 文本，调用 TTS 朗读"""
        text = getattr(ev, "result", None) or getattr(ev, "content", "")
        if text:
            await speak_text(text)
        if self._reply_event:
            self._reply_event.set()

    async def run_voice_loop(self) -> None:
        """
        麦克风循环：唤醒词阻塞 -> 录音 -> 注入总线 -> 等待大脑回复 -> 下一轮。
        异常时自动重试，保持赛博朋克风格日志。
        """
        from core.event_bus import emit_omni_input, subscribe_omni_output

        subscribe_omni_output("voice", self._on_brain_reply)

        console.print("[dim]  (说完后静默 1 秒，或按 Ctrl+C 退出)[/dim]")
        console.print()

        loop = asyncio.get_event_loop()
        while True:
            try:
                self._reply_event = asyncio.Event()
                self._reply_event.clear()

                # 1. 唤醒词阻塞（极低功耗）
                woke = await loop.run_in_executor(None, _listen_for_wake_word)
                if not woke:
                    console.print("[yellow][Animus] 麦克风异常，3 秒后重试...[/yellow]")
                    await asyncio.sleep(3.0)
                    continue

                # 2. 录音与识别
                console.print("[yellow]🎤 请说话...[/yellow]")
                transcribed = await loop.run_in_executor(None, listen_and_transcribe)

                if not transcribed:
                    console.print("[dim]  (未检测到有效语音，请重试)[/dim]")
                    continue

                console.print(f"[cyan]  → 识别: {transcribed}[/cyan]")
                console.print("[dim]  → 注入全息感官总线，大脑处理中...[/dim]")

                emit_omni_input("voice", transcribed, {})

                try:
                    await asyncio.wait_for(self._reply_event.wait(), timeout=120.0)
                except asyncio.TimeoutError:
                    console.print("[red]  ⚠ 超时：大脑未在 120 秒内回复[/red]")

                console.print()
            except asyncio.CancelledError:
                console.print("[dim][Animus] 语音循环已终止[/dim]")
                break
            except Exception as e:
                logger.exception("[Voice] 麦克风循环异常: %s", e)
                console.print(f"[red][Voice] 异常: {e}，5 秒后重试[/red]")
                await asyncio.sleep(5.0)
