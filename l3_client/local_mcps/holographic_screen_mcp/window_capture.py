"""按窗口标题截取区域（Windows），用于计算器等同屏多窗口场景。"""
from __future__ import annotations

import logging
import sys
from io import BytesIO
from typing import Sequence

logger = logging.getLogger("holographic.window_capture")

_DEFAULT_TITLE_KEYWORDS = ("计算器", "calculator", "calc")


def _find_window_rect_win32(keywords: Sequence[str]) -> tuple[int, int, int, int] | None:
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    matches: list[tuple[int, int, int, int, str, int]] = []
    keys = [k.lower() for k in keywords if k]

    def _callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd) + 1
        if length <= 1:
            return True
        buf = ctypes.create_unicode_buffer(length)
        user32.GetWindowTextW(hwnd, buf, length)
        title = (buf.value or "").strip()
        if not title:
            return True
        tl = title.lower()
        if not any(k in tl for k in keys):
            return True
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        w = rect.right - rect.left
        h = rect.bottom - rect.top
        if w < 120 or h < 120:
            return True
        matches.append((rect.left, rect.top, w, h, title, w * h))
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(_callback), 0)
    if not matches:
        return None
    left, top, w, h, title, _area = max(matches, key=lambda x: x[5])
    logger.info("[window_capture] 命中窗口 title=%r region=(%d,%d,%d,%d)", title, left, top, w, h)
    return left, top, w, h


def capture_region_png(
    region: tuple[int, int, int, int],
) -> tuple[bytes, int, int, str]:
    """region = (left, top, width, height)。"""
    try:
        import pyautogui
    except ImportError as e:
        return b"", 0, 0, f"pyautogui_not_installed:{e}"
    left, top, w, h = region
    try:
        img = pyautogui.screenshot(region=(int(left), int(top), int(w), int(h)))
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue(), int(w), int(h), ""
    except Exception as e:
        return b"", 0, 0, f"screenshot_region_failed:{e!r}"


def capture_window_png(
    title_keywords: Sequence[str] | None = None,
) -> tuple[bytes, int, int, str, tuple[int, int, int, int] | None]:
    """
    尝试按标题关键字截窗口；失败则返回 error（调用方回退全屏）。
    Returns:
        (png, w, h, error, region_or_none)
    """
    keys = tuple(title_keywords or _DEFAULT_TITLE_KEYWORDS)
    rect = _find_window_rect_win32(keys)
    if rect is None:
        return b"", 0, 0, "calculator_window_not_found", None
    png, w, h, err = capture_region_png(rect)
    return png, w, h, err, rect
