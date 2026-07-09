"""Lightweight State Watcher for the Memory-first Cognitive Kernel.

The watcher is deliberately cheap: it samples process/resource/task state and
persists a latest snapshot. Heavy UI, filesystem, or OCR sensing stays as
explicit tools so the main turn does not stall.
"""

from __future__ import annotations

import os
import platform
import threading
import time
from collections import deque
from typing import Any

from .paths import state_dir

_LOCK = threading.RLock()
_LATEST: dict[str, Any] = {}
_STARTED = False
_RECENT_WINDOW_EVENTS: deque[dict[str, Any]] = deque(maxlen=30)
_LAST_FOREGROUND_KEY = ""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _sample_psutil() -> dict[str, Any]:
    try:
        import psutil  # type: ignore
    except Exception as exc:
        return {"available": False, "error": exc.__class__.__name__}
    try:
        vm = psutil.virtual_memory()
        disk = psutil.disk_usage(str(os.path.expanduser("~")))
        proc = psutil.Process(os.getpid())
        return {
            "available": True,
            "pid": os.getpid(),
            "process_name": proc.name(),
            "process_cpu_percent": proc.cpu_percent(interval=None),
            "process_memory_mb": round(proc.memory_info().rss / 1024 / 1024, 2),
            "cpu_percent": psutil.cpu_percent(interval=None),
            "memory_percent": vm.percent,
            "memory_available_mb": round(vm.available / 1024 / 1024, 2),
            "disk_home_free_gb": round(disk.free / 1024 / 1024 / 1024, 2),
            "process_count": len(psutil.pids()),
        }
    except Exception as exc:
        return {"available": False, "error": exc.__class__.__name__}


def _sample_windows_foreground() -> dict[str, Any]:
    if platform.system().lower() != "windows":
        return {}
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return {}
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(max(length + 1, 512))
        user32.GetWindowTextW(hwnd, buf, len(buf))
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        process_name = ""
        try:
            import psutil  # type: ignore

            process_name = psutil.Process(int(pid.value)).name()
        except Exception:
            process_name = ""
        title = (buf.value or "").strip()
        return {
            "hwnd": int(hwnd),
            "pid": int(pid.value),
            "process_name": process_name,
            "title": title,
            "window_title": title,
            "app_name": _friendly_app_name(process_name, title),
        }
    except Exception as exc:
        return {"error": exc.__class__.__name__}


def _sample_running_apps(limit: int = 40) -> list[dict[str, Any]]:
    try:
        import psutil  # type: ignore
    except Exception:
        return []
    apps: list[dict[str, Any]] = []
    seen: set[int] = set()
    for proc in psutil.process_iter(["pid", "name", "exe", "username", "cpu_percent", "memory_info", "create_time"]):
        try:
            info = proc.info
            pid = int(info.get("pid") or 0)
            if pid in seen:
                continue
            seen.add(pid)
            name = str(info.get("name") or "")
            if not name:
                continue
            lower = name.lower()
            if lower in {"idle", "system", "registry"}:
                continue
            mem = info.get("memory_info")
            apps.append(
                {
                    "pid": pid,
                    "name": name,
                    "app_name": _friendly_app_name(name, ""),
                    "exe": str(info.get("exe") or ""),
                    "memory_mb": round(getattr(mem, "rss", 0) / 1024 / 1024, 2) if mem else 0,
                    "created_at": float(info.get("create_time") or 0),
                }
            )
        except Exception:
            continue
    apps.sort(key=lambda item: float(item.get("memory_mb") or 0), reverse=True)
    return apps[:limit]


def _friendly_app_name(process_name: str, title: str) -> str:
    low = f"{process_name} {title}".lower()
    if "lark" in low or "feishu" in low:
        return "Lark"
    if "chrome" in low:
        return "Chrome"
    if "msedge" in low or "edge" in low:
        return "Edge"
    if "explorer" in low:
        return "Explorer"
    if "calculator" in low or "calc" in low:
        return "Calculator"
    if "notepad" in low:
        return "Notepad"
    if "powershell" in low or "cmd" in low or "windows terminal" in low:
        return "Terminal"
    return process_name or title


def _risk_state_from_window(active_window: dict[str, Any]) -> dict[str, Any]:
    title = str(active_window.get("title") or active_window.get("window_title") or "").lower()
    proc = str(active_window.get("process_name") or "").lower()
    unsaved = any(marker in title for marker in ("*", "未保存", "unsaved"))
    modal = any(marker in title for marker in ("save as", "另存为", "confirm", "确认", "warning", "警告"))
    return {
        "unsaved_documents": "possible" if unsaved else "unknown",
        "modal_dialogs": "possible" if modal else "unknown",
        "permission_prompts": "possible" if any(x in title for x in ("administrator", "uac", "permission", "权限")) else "unknown",
        "foreground_process": proc,
    }


def _record_window_event(active_window: dict[str, Any], sampled_at_ms: int) -> None:
    global _LAST_FOREGROUND_KEY
    key = f"{active_window.get('hwnd')}:{active_window.get('pid')}:{active_window.get('title')}"
    if not active_window or key == _LAST_FOREGROUND_KEY:
        return
    _LAST_FOREGROUND_KEY = key
    _RECENT_WINDOW_EVENTS.appendleft(
        {
            "ts_ms": sampled_at_ms,
            "event": "foreground_changed",
            "app_name": active_window.get("app_name") or "",
            "process_name": active_window.get("process_name") or "",
            "title": active_window.get("title") or "",
            "pid": active_window.get("pid") or 0,
            "hwnd": active_window.get("hwnd") or 0,
        }
    )


def sample_state() -> dict[str, Any]:
    sampled_at_ms = _now_ms()
    active_window = _sample_windows_foreground()
    _record_window_event(active_window, sampled_at_ms)
    return {
        "sampled_at_ms": sampled_at_ms,
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "active_window": active_window,
        "running_apps": _sample_running_apps(),
        "recent_app_events": list(_RECENT_WINDOW_EVENTS),
        "resource_state": _sample_psutil(),
        "risk_state": _risk_state_from_window(active_window),
    }


def persist_latest_state(snapshot: dict[str, Any]) -> None:
    with _LOCK:
        _LATEST.clear()
        _LATEST.update(snapshot)
        try:
            path = state_dir() / "latest_state.json"
            import json

            path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        except Exception:
            pass


def get_latest_state(max_age_ms: int = 30_000) -> dict[str, Any]:
    with _LOCK:
        latest = dict(_LATEST)
    now = _now_ms()
    if latest and now - int(latest.get("sampled_at_ms") or 0) <= max_age_ms:
        return latest
    snapshot = sample_state()
    persist_latest_state(snapshot)
    return snapshot


def start_state_watcher(interval_sec: float = 5.0) -> None:
    global _STARTED
    with _LOCK:
        if _STARTED:
            return
        _STARTED = True

    def _loop() -> None:
        while True:
            try:
                persist_latest_state(sample_state())
            except Exception:
                pass
            time.sleep(max(1.0, float(interval_sec or 5.0)))

    t = threading.Thread(target=_loop, name="cognitive-state-watcher", daemon=True)
    t.start()
