#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox


LOG = logging.getLogger("jachin.voice_audition")
MOSS_FRAME_MS = 80
DEFAULT_TEXT = "你好，我在。"


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parent.parent
    default_threads = min(8, max(4, os.cpu_count() or 4))
    parser = argparse.ArgumentParser(description="MOSS 音色点选试听 GUI（点哪个播哪个）")
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=project_root / "data" / "models" / "voice" / "tts",
        help="MOSS 模型目录（默认 data/models/voice/tts）",
    )
    parser.add_argument(
        "--text",
        default=DEFAULT_TEXT,
        help="试听文案。试听越短，生成越快。",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=project_root / "data" / "voice_audition_out",
        help="保存试听 wav 的目录",
    )
    parser.add_argument(
        "--max-new-frames",
        type=int,
        default=0,
        help="试听生成帧上限；默认 0 表示按文本长度自动选择。",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=default_threads,
        help="MOSS ONNX CPU 线程数。",
    )
    parser.add_argument(
        "--no-warmup",
        action="store_true",
        help="启动后不做短句预热。",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="不复用已生成的试听 wav，每次点击都重新生成。",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="启动时清理输出目录里的 preview_*.wav。",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="控制台日志级别。",
    )
    return parser.parse_args()


def configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )


def likely_hit_frame_cap(duration_ms: int, max_new_frames: int) -> bool:
    expected_cap_ms = max(0, int(max_new_frames) * MOSS_FRAME_MS)
    return expected_cap_ms > 0 and duration_ms >= int(expected_cap_ms * 0.92)


def play_wav(path: Path, *, async_play: bool = True) -> None:
    errs: list[str] = []
    try:
        import winsound

        LOG.debug("停止上一段播放，准备播放 path=%s async=%s", path, async_play)
        winsound.PlaySound(None, winsound.SND_PURGE)
        flags = winsound.SND_FILENAME
        if async_play:
            flags |= winsound.SND_ASYNC
        winsound.PlaySound(str(path), flags)
        LOG.info("播放已提交 method=winsound async=%s path=%s", async_play, path)
        return
    except Exception as exc:  # noqa: BLE001
        errs.append(f"winsound: {exc}")

    try:
        safe = str(path).replace("'", "''")
        method = "Play" if async_play else "PlaySync"
        script = f"$p = New-Object System.Media.SoundPlayer '{safe}'; $p.{method}()"
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            check=True,
            capture_output=True,
            text=True,
        )
        LOG.info("播放已提交 method=powershell async=%s path=%s", async_play, path)
        return
    except Exception as exc:  # noqa: BLE001
        errs.append(f"powershell_soundplayer: {exc}")

    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
        LOG.info("播放已提交 method=os.startfile path=%s", path)
        return
    except Exception as exc:  # noqa: BLE001
        errs.append(f"os.startfile: {exc}")

    raise RuntimeError(" | ".join(errs))


def stop_wav() -> None:
    try:
        import winsound

        winsound.PlaySound(None, winsound.SND_PURGE)
    except Exception:
        pass


def safe_voice_name(voice: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in voice).strip("._-") or "voice"


def clear_preview_cache(out_dir: Path) -> int:
    if not out_dir.is_dir():
        return 0
    count = 0
    for path in out_dir.glob("preview_*.wav"):
        try:
            path.unlink()
            count += 1
        except Exception as exc:  # noqa: BLE001
            LOG.warning("清理缓存失败 path=%s err=%s", path, exc)
    return count


class MossVoiceClickAuditionApp:
    def __init__(
        self,
        root: tk.Tk,
        model_dir: Path,
        text: str,
        out_dir: Path,
        warmup: bool,
        max_new_frames: int,
        use_cache: bool,
    ) -> None:
        self.root = root
        self.model_dir = model_dir
        self.text = text
        self.out_dir = out_dir
        self.max_new_frames = max(0, int(max_new_frames))
        self.use_cache = bool(use_cache)
        self.busy = False
        self.warming = False
        self.cache: dict[tuple[str, str, int], Path] = {}
        self.synth_lock = threading.Lock()

        project_root = Path(__file__).resolve().parent.parent
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))

        from voice_server.services.tts_service import TtsService  # pylint: disable=import-outside-toplevel

        LOG.info(
            "启动试听 GUI model_dir=%s out_dir=%s max_new_frames=%s cache=%s warmup=%s text=%r",
            model_dir,
            out_dir,
            self.max_new_frames,
            self.use_cache,
            warmup,
            text,
        )
        self.tts = TtsService(model_dir)
        load_started = time.perf_counter()
        if not self.tts.ready or not self.tts._load_engine():
            raise RuntimeError(f"TTS 引擎未就绪: dir={model_dir}, err={self.tts._load_error}")
        self.voices = self.tts.list_voices()
        LOG.info("TTS 引擎已加载 load_ms=%s voices=%s", int((time.perf_counter() - load_started) * 1000), len(self.voices))
        LOG.debug("音色列表: %s", ", ".join(self.voices))

        self.root.title("Jachin MOSS 音色点选试听")
        self.root.geometry("520x680")
        self.root.minsize(420, 500)

        wrap = ttk.Frame(root, padding=12)
        wrap.pack(fill=tk.BOTH, expand=True)

        ttk.Label(wrap, text="试听文案").pack(anchor=tk.W)
        self.text_var = tk.StringVar(value=self.text)
        ttk.Entry(wrap, textvariable=self.text_var).pack(fill=tk.X, pady=(4, 10))

        ttk.Label(wrap, text=f"模型目录: {self.model_dir}").pack(anchor=tk.W)
        ttk.Label(wrap, text=f"输出目录: {self.out_dir}").pack(anchor=tk.W, pady=(0, 8))

        self.status_var = tk.StringVar(value="点左侧任意音色即可试听")
        ttk.Label(wrap, textvariable=self.status_var).pack(anchor=tk.W, pady=(0, 8))

        list_wrap = ttk.Frame(wrap)
        list_wrap.pack(fill=tk.BOTH, expand=True)
        self.listbox = tk.Listbox(list_wrap, activestyle="dotbox")
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(list_wrap, orient=tk.VERTICAL, command=self.listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.configure(yscrollcommand=scrollbar.set)

        for voice in self.voices:
            self.listbox.insert(tk.END, voice)

        self.listbox.bind("<<ListboxSelect>>", self._on_click_voice)
        self.listbox.bind("<Double-Button-1>", self._on_click_voice)

        btn_row = ttk.Frame(wrap)
        btn_row.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(btn_row, text="停止播放", command=self._stop_playback).pack(side=tk.LEFT)

        if warmup and self.voices:
            self.root.after(250, self._start_warmup)

    def _stop_playback(self) -> None:
        LOG.info("用户停止播放")
        stop_wav()
        self.status_var.set("已停止播放")

    def _start_warmup(self) -> None:
        if self.busy or self.warming or not self.voices:
            return
        voice = self.voices[0]
        warmup_text = "你好。"
        warmup_started = time.perf_counter()
        self.warming = True
        self.status_var.set(f"正在预热 MOSS: {voice}")
        LOG.info("预热开始 voice=%s text=%r frame_budget=%s", voice, warmup_text, self._frame_budget(warmup_text))

        def worker() -> None:
            try:
                with self.synth_lock:
                    result = self.tts.synthesize(text=warmup_text, voice=voice, session_id="audition-warmup")
                LOG.info(
                    "预热完成 voice=%s synth_ms=%s audio_ms=%s bytes=%s",
                    voice,
                    int((time.perf_counter() - warmup_started) * 1000),
                    result.duration_ms,
                    len(result.wav_bytes),
                )
                self.root.after(0, self._on_warmup_ok)
            except Exception as exc:  # noqa: BLE001
                err = str(exc)
                LOG.exception("预热失败")
                self.root.after(0, lambda e=err: self._on_warmup_fail(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_warmup_ok(self) -> None:
        self.warming = False
        self.status_var.set("预热完成，点左侧任意音色即可试听")

    def _on_warmup_fail(self, err: str) -> None:
        self.warming = False
        self.status_var.set(f"预热失败，仍可直接试听: {err[:80]}")

    def _on_click_voice(self, _event: object) -> None:
        if self.busy:
            LOG.info("忽略点击：当前仍在生成")
            return
        if self.warming:
            LOG.info("忽略点击：MOSS 仍在预热")
            self.status_var.set("MOSS 正在预热，马上可以试听")
            return
        sel = self.listbox.curselection()
        if not sel:
            return
        voice = self.listbox.get(sel[0]).strip()
        if not voice:
            return
        self._audition_voice(voice)

    def _frame_budget(self, text: str) -> int:
        if self.max_new_frames > 0:
            return self.max_new_frames
        if hasattr(self.tts, "_resolve_max_new_frames"):
            return int(self.tts._resolve_max_new_frames(text))  # pylint: disable=protected-access
        return 0

    def _cache_path(self, voice: str, text: str) -> Path:
        raw = f"v5\0mode=fixed\0frames={self._frame_budget(text)}\0{voice}\0{text}".encode("utf-8", errors="ignore")
        digest = hashlib.sha1(raw).hexdigest()[:12]
        return self.out_dir / f"preview_{safe_voice_name(voice)}_{digest}.wav"

    def _audition_voice(self, voice: str) -> None:
        text = self.text_var.get().strip() or DEFAULT_TEXT
        frame_budget = self._frame_budget(text)
        cache_key = (voice, text, frame_budget)
        cached = self.cache.get(cache_key) or self._cache_path(voice, text)
        if any(ch.isascii() and ch.isalpha() for ch in text):
            LOG.warning("试听文本包含英文字符，MOSS ONNX 可能生成更慢或发音不稳定；如果持续只读前半句，可试中文读法，比如把 jachin 写成 加秦。text=%r", text)
        LOG.info(
            "点击试听 voice=%s text=%r text_len=%s max_new_frames=%s cache=%s cache_path=%s",
            voice,
            text,
            len(text),
            frame_budget,
            self.use_cache,
            cached,
        )
        if self.use_cache and cached.is_file():
            self.cache[cache_key] = cached
            try:
                LOG.info("缓存命中 voice=%s path=%s size=%s", voice, cached, cached.stat().st_size)
                play_wav(cached, async_play=True)
                self.status_var.set(f"已播放缓存: {voice}（{cached.name}）")
                return
            except Exception as exc:  # noqa: BLE001
                LOG.exception("缓存播放失败，转为重新生成 voice=%s path=%s", voice, cached)
                self.status_var.set(f"缓存播放失败，重新生成: {exc}")

        self.busy = True
        self.status_var.set(f"正在生成: {voice}")
        LOG.info("开始生成 voice=%s text=%r", voice, text)

        def worker() -> None:
            started = time.perf_counter()
            try:
                with self.synth_lock:
                    result = self.tts.synthesize(text=text, voice=voice, session_id=f"click-{voice}")
                elapsed_ms = int((time.perf_counter() - started) * 1000)
                if likely_hit_frame_cap(result.duration_ms, frame_budget):
                    suggested = max(frame_budget + 32, int(frame_budget * 1.5))
                    LOG.warning(
                        "疑似撞到 max_new_frames 上限，可能被截断 voice=%s audio_ms=%s frame_cap_ms~%s max_new_frames=%s 建议尝试 --max-new-frames %s",
                        voice,
                        result.duration_ms,
                        frame_budget * MOSS_FRAME_MS,
                        frame_budget,
                        suggested,
                    )
                self.out_dir.mkdir(parents=True, exist_ok=True)
                wav_path = self._cache_path(voice, text)
                if wav_path.exists():
                    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    wav_path = self.out_dir / f"preview_{safe_voice_name(voice)}_{ts}.wav"
                wav_path.write_bytes(result.wav_bytes)
                self.cache[cache_key] = wav_path
                LOG.info(
                    "生成完成 voice=%s synth_ms=%s audio_ms=%s sample_rate=%s bytes=%s path=%s",
                    voice,
                    elapsed_ms,
                    result.duration_ms,
                    result.sample_rate,
                    len(result.wav_bytes),
                    wav_path,
                )
                self.root.after(0, lambda: self.status_var.set(f"正在播放: {voice}（生成 {elapsed_ms} ms）"))
                play_wav(wav_path, async_play=True)
                self.root.after(0, lambda: self._on_ok(voice, wav_path, elapsed_ms))
            except Exception as exc:  # noqa: BLE001
                err = str(exc)
                LOG.exception("试听生成失败 voice=%s text=%r", voice, text)
                self.root.after(0, lambda e=err: self._on_fail(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_ok(self, voice: str, wav_path: Path, elapsed_ms: int) -> None:
        self.busy = False
        self.status_var.set(f"已开始播放: {voice}（生成 {elapsed_ms} ms，已保存 {wav_path.name}）")

    def _on_fail(self, err: str) -> None:
        self.busy = False
        self.status_var.set("试听失败")
        messagebox.showerror("试听失败", err)


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level)
    args.max_new_frames = max(0, int(args.max_new_frames))
    args.threads = max(1, int(args.threads))

    if args.max_new_frames > 0:
        os.environ["JACHIN_VOICE_TTS_MAX_NEW_FRAMES"] = str(args.max_new_frames)
    else:
        os.environ.pop("JACHIN_VOICE_TTS_MAX_NEW_FRAMES", None)
    os.environ["JACHIN_VOICE_TTS_THREADS"] = str(args.threads)
    os.environ["JACHIN_VOICE_TTS_SAMPLE_MODE"] = "fixed"
    os.environ["JACHIN_VOICE_TTS_VOICE_CLONE_MAX_TEXT_TOKENS"] = "75"

    if args.clear_cache:
        cleared = clear_preview_cache(args.out_dir)
        LOG.info("已清理试听缓存 out_dir=%s count=%s", args.out_dir, cleared)

    LOG.info(
        "启动参数 text=%r max_new_frames=%s threads=%s no_warmup=%s no_cache=%s clear_cache=%s log_level=%s",
        args.text,
        args.max_new_frames,
        args.threads,
        args.no_warmup,
        args.no_cache,
        args.clear_cache,
        args.log_level,
    )
    LOG.info(
        "环境参数 JACHIN_VOICE_TTS_MAX_NEW_FRAMES=%s JACHIN_VOICE_TTS_THREADS=%s JACHIN_VOICE_TTS_SAMPLE_MODE=%s JACHIN_VOICE_TTS_VOICE_CLONE_MAX_TEXT_TOKENS=%s",
        os.getenv("JACHIN_VOICE_TTS_MAX_NEW_FRAMES"),
        os.getenv("JACHIN_VOICE_TTS_THREADS"),
        os.getenv("JACHIN_VOICE_TTS_SAMPLE_MODE"),
        os.getenv("JACHIN_VOICE_TTS_VOICE_CLONE_MAX_TEXT_TOKENS"),
    )

    root = tk.Tk()
    try:
        _ = MossVoiceClickAuditionApp(
            root=root,
            model_dir=args.model_dir,
            text=args.text,
            out_dir=args.out_dir,
            warmup=not args.no_warmup,
            max_new_frames=args.max_new_frames,
            use_cache=not args.no_cache,
        )
    except Exception as exc:  # noqa: BLE001
        LOG.exception("启动失败")
        messagebox.showerror("启动失败", str(exc))
        return 1
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
