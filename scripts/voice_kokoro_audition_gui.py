#!/usr/bin/env python3
"""
Kokoro ONNX 音色试听 GUI（点选即播）。

用途：
- 扫描 voices 目录下所有 *.bin 音色
- 鼠标点击某个音色后，立即调用 JVS TTS 播放：
  “你好，我在。”

默认：
- voices 目录：
  D:\\project\\jachin-system-main\\data\\models\\voice\\tts\\Kokoro-82M-v1.1-zh-ONNX\\voices
- JVS 地址：
  http://127.0.0.1:18982
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

DEFAULT_VOICES_DIR = Path(
    r"D:\project\jachin-system-main\data\models\voice\tts\Kokoro-82M-v1.1-zh-ONNX\voices"
)
DEFAULT_JVS_BASE = "http://127.0.0.1:18982"
DEFAULT_TEXT = "你好，我在。"
DEFAULT_MANDARIN_VOICE = "zm_053"
DEFAULT_OUTPUT_DIR = Path(r"D:\project\jachin-system-main\data\voice_audition_out")
MANDARIN_VOICE_CANDIDATES = [
    "zm_053",
    "zf_001",
    "zm_033",
    "zm_009",
    "zm_001",
]

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def post_tts(base_url: str, text: str, voice: str, timeout: float = 45.0) -> bytes:
    payload = json.dumps({"text": text, "voice": voice}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/tts/synthesize",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def stop_playback() -> None:
    if sys.platform != "win32":
        return
    import winsound

    winsound.PlaySound(None, winsound.SND_PURGE)


def play_wav_bytes(wav_bytes: bytes) -> None:
    if sys.platform != "win32":
        raise RuntimeError("该脚本目前仅支持 Windows 播放（winsound）。")
    import winsound

    tmp_dir = Path(tempfile.gettempdir()) / "jachin_voice_audition"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    wav_path = tmp_dir / "preview.wav"
    wav_path.write_bytes(wav_bytes)

    # 先停掉前一个音频，再异步播放新音频
    winsound.PlaySound(None, winsound.SND_PURGE)
    winsound.PlaySound(str(wav_path), winsound.SND_FILENAME | winsound.SND_ASYNC)


class VoiceAuditionApp:
    def __init__(self, root: tk.Tk, voices_dir: Path, base_url: str, text: str, output_dir: Path) -> None:
        self.root = root
        self.voices_dir = voices_dir
        self.base_url = base_url.rstrip("/")
        self.text = text
        self.output_dir = output_dir
        self.voices = self._scan_voices()
        self.default_mandarin_voice = self._pick_mandarin_voice()
        self.last_voice = ""
        self.is_busy = False

        self.root.title("Jachin Kokoro ONNX 音色试听")
        self.root.geometry("520x620")
        self.root.minsize(420, 460)

        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="JVS 地址").pack(anchor=tk.W)
        self.base_var = tk.StringVar(value=self.base_url)
        ttk.Entry(frame, textvariable=self.base_var).pack(fill=tk.X, pady=(0, 8))

        ttk.Label(frame, text="试听模式").pack(anchor=tk.W)
        self.mode_var = tk.StringVar(value="普通话(本地JVS)")
        mode_box = ttk.Combobox(
            frame,
            textvariable=self.mode_var,
            state="readonly",
            values=["普通话(本地JVS)", "Kokoro ONNX"],
        )
        mode_box.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(frame, text="普通话模式音色 ID").pack(anchor=tk.W)
        self.mandarin_voice_var = tk.StringVar(value=self.default_mandarin_voice)
        ttk.Entry(frame, textvariable=self.mandarin_voice_var).pack(fill=tk.X, pady=(0, 8))

        ttk.Label(frame, text="试听文案").pack(anchor=tk.W)
        self.text_var = tk.StringVar(value=self.text)
        ttk.Entry(frame, textvariable=self.text_var).pack(fill=tk.X, pady=(0, 8))

        ttk.Label(frame, text="输出目录（每次试听都保存 WAV）").pack(anchor=tk.W)
        out_row = ttk.Frame(frame)
        out_row.pack(fill=tk.X, pady=(0, 8))
        self.output_var = tk.StringVar(value=str(self.output_dir))
        ttk.Entry(out_row, textvariable=self.output_var).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(out_row, text="选择", command=self._choose_output_dir).pack(side=tk.LEFT, padx=(6, 0))

        meta = f"音色目录：{self.voices_dir}"
        ttk.Label(frame, text=meta, foreground="#666666", wraplength=480).pack(anchor=tk.W, pady=(0, 8))

        self.status_var = tk.StringVar(
            value=(
                f"已加载 {len(self.voices)} 个音色，普通话默认音色: {self.default_mandarin_voice}。"
                "普通话模式下点击列表会直接使用该音色试听。"
            )
        )
        ttk.Label(frame, textvariable=self.status_var, foreground="#0a7f42").pack(anchor=tk.W, pady=(0, 6))

        list_wrap = ttk.Frame(frame)
        list_wrap.pack(fill=tk.BOTH, expand=True)

        self.listbox = tk.Listbox(list_wrap, activestyle="dotbox")
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(list_wrap, orient=tk.VERTICAL, command=self.listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.configure(yscrollcommand=scrollbar.set)

        for v in self.voices:
            self.listbox.insert(tk.END, v)

        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        self.listbox.bind("<Double-Button-1>", self._on_select)

        actions = ttk.Frame(frame)
        actions.pack(fill=tk.X, pady=(10, 0))
        ttk.Button(actions, text="停止播放", command=stop_playback).pack(side=tk.LEFT)
        ttk.Button(actions, text="刷新音色列表", command=self._refresh_voices).pack(side=tk.LEFT, padx=(8, 0))

    def _scan_voices(self) -> list[str]:
        if not self.voices_dir.exists():
            return []
        return sorted(p.stem for p in self.voices_dir.glob("*.bin") if p.is_file())

    def _pick_mandarin_voice(self) -> str:
        if not self.voices:
            return DEFAULT_MANDARIN_VOICE
        voice_set = set(self.voices)
        for v in MANDARIN_VOICE_CANDIDATES:
            if v in voice_set:
                return v
        # 没命中候选时退到第一个可用音色，避免指向不存在文件
        return self.voices[0]

    def _refresh_voices(self) -> None:
        self.voices = self._scan_voices()
        self.listbox.delete(0, tk.END)
        for v in self.voices:
            self.listbox.insert(tk.END, v)
        self.default_mandarin_voice = self._pick_mandarin_voice()
        self.mandarin_voice_var.set(self.default_mandarin_voice)
        self.status_var.set(
            f"已刷新，共 {len(self.voices)} 个音色，普通话默认音色: {self.default_mandarin_voice}"
        )

    def _choose_output_dir(self) -> None:
        selected = filedialog.askdirectory(
            title="选择试听音频输出目录",
            initialdir=self.output_var.get().strip() or str(DEFAULT_OUTPUT_DIR),
        )
        if selected:
            self.output_var.set(selected)

    @staticmethod
    def _safe_name(text: str) -> str:
        return "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in text)

    def _resolve_output_path(self, mode: str, voice: str) -> Path:
        base = Path(self.output_var.get().strip() or str(DEFAULT_OUTPUT_DIR))
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        mode_tag = "mandarin" if mode != "Kokoro ONNX" else "kokoro_onnx"
        voice_tag = self._safe_name(voice)[:48] or "voice"
        return base / f"{stamp}_{mode_tag}_{voice_tag}.wav"

    def _on_select(self, _event: object) -> None:
        if self.is_busy:
            return
        sel = self.listbox.curselection()
        if not sel:
            return
        voice = self.listbox.get(sel[0]).strip()
        if not voice:
            return
        self._preview_voice(voice)

    def _preview_voice(self, voice: str) -> None:
        text = self.text_var.get().strip() or DEFAULT_TEXT
        base = self.base_var.get().strip() or DEFAULT_JVS_BASE
        mode = self.mode_var.get().strip() or "普通话(本地JVS)"
        mandarin_voice = (self.mandarin_voice_var.get().strip() or self.default_mandarin_voice)
        voice_set = set(self.voices)
        if mode != "Kokoro ONNX" and mandarin_voice not in voice_set:
            mandarin_voice = self.default_mandarin_voice
            self.mandarin_voice_var.set(mandarin_voice)
        if mode != "Kokoro ONNX":
            # 普通话模式也跟随当前点击项，避免“点了列表却不生效”的误导。
            mandarin_voice = voice
            self.mandarin_voice_var.set(mandarin_voice)
        self.is_busy = True
        self.status_var.set(f"正在试听：{voice}（{mode}）")
        self.last_voice = voice

        def worker() -> None:
            try:
                if mode == "Kokoro ONNX":
                    wav = post_tts(base, text, voice)
                else:
                    wav = post_tts(base, text, mandarin_voice)
                save_voice = voice if mode == "Kokoro ONNX" else mandarin_voice
                save_path = self._resolve_output_path(mode, save_voice)
                save_path.parent.mkdir(parents=True, exist_ok=True)
                save_path.write_bytes(wav)
                self.root.after(0, lambda: self._on_preview_ok(voice, wav, save_path))
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", errors="replace")
                err_msg = f"HTTP {e.code}: {detail}"
                self.root.after(0, lambda m=err_msg: self._on_preview_fail(m))
            except urllib.error.URLError as e:
                err_msg = f"连接失败: {e}"
                self.root.after(0, lambda m=err_msg: self._on_preview_fail(m))
            except Exception as e:  # noqa: BLE001
                err_msg = str(e)
                self.root.after(0, lambda m=err_msg: self._on_preview_fail(m))

        threading.Thread(target=worker, daemon=True).start()

    def _on_preview_ok(self, voice: str, wav: bytes, saved_path: Path) -> None:
        try:
            play_wav_bytes(wav)
            self.status_var.set(f"试听中：{voice}，已保存：{saved_path}")
        except Exception as e:  # noqa: BLE001
            self._on_preview_fail(f"播放失败: {e}")
            return
        finally:
            self.is_busy = False

    def _on_preview_fail(self, msg: str) -> None:
        self.is_busy = False
        self.status_var.set("试听失败，请检查 JVS 与音色文件")
        messagebox.showerror("试听失败", msg)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Kokoro ONNX 音色试听 GUI（点选即播）")
    p.add_argument("--voices-dir", type=Path, default=DEFAULT_VOICES_DIR, help="音色目录（*.bin）")
    p.add_argument("--base-url", default=DEFAULT_JVS_BASE, help="JVS 地址，默认 http://127.0.0.1:18982")
    p.add_argument("--text", default=DEFAULT_TEXT, help="试听文案")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="试听 WAV 输出目录")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not args.voices_dir.exists():
        print(f"[错误] 音色目录不存在: {args.voices_dir}")
        return 1

    root = tk.Tk()
    app = VoiceAuditionApp(
        root=root,
        voices_dir=args.voices_dir,
        base_url=args.base_url,
        text=args.text,
        output_dir=args.output_dir,
    )
    if not app.voices:
        messagebox.showwarning("提示", f"目录中未找到任何 .bin 音色文件：\n{args.voices_dir}")
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
