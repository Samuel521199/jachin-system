"""
Windows：为 L3 子进程分配独立控制台，使打包/桌面拉起时也能看到与开发机类似的实时日志。

- 已由用户终端启动（已有控制台）时不处理。
- PyInstaller frozen 且未显式关闭时默认启用；源码 `python -m l3_node` 默认不弹窗（避免重复控制台）。
"""
from __future__ import annotations

import os
import sys


def wants_l3_console_window() -> bool:
    """是否应为当前进程分配可见控制台。"""
    v = (os.environ.get("JACHIN_L3_CONSOLE") or "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    # 打包 exe 由 GUI（Tauri）拉起时通常无控制台；默认弹出「Jachin L3」窗口
    return bool(getattr(sys, "frozen", False))


def maybe_attach_windows_console() -> None:
    if sys.platform != "win32":
        return
    if not wants_l3_console_window():
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        if kernel32.GetConsoleWindow():
            return
        if not kernel32.AllocConsole():
            return
        try:
            kernel32.SetConsoleTitleW("Jachin L3")
        except Exception:
            pass
    except Exception:
        return
    try:
        con = open(  # noqa: SIM115
            "CONOUT$",
            "w",
            encoding="utf-8",
            errors="replace",
            buffering=1,
        )
        sys.stdout = con
        sys.stderr = con
    except Exception:
        pass
