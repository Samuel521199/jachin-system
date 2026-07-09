"""Production-oriented StateFabric service for the Cognitive Kernel.

The lightweight ``state_watcher`` samples one snapshot. This module turns that
sampler into a small always-on state fabric: periodic updates, freshness
metadata, event deltas, persisted history, and an explicit service status API.
It intentionally stays cheap; heavy OCR/file scans remain explicit WorkOrders.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .ledger import append_event
from .paths import state_dir
from .state_watcher import sample_state

_LOCK = threading.RLock()
_SERVICE: "StateFabricService | None" = None


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(slots=True)
class StateFabricStatus:
    running: bool
    sample_count: int = 0
    last_sample_at_ms: int = 0
    last_error: str = ""
    interval_sec: float = 1.0
    snapshot_path: str = ""
    history_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "sample_count": self.sample_count,
            "last_sample_at_ms": self.last_sample_at_ms,
            "last_error": self.last_error,
            "interval_sec": self.interval_sec,
            "snapshot_path": self.snapshot_path,
            "history_path": self.history_path,
        }


@dataclass
class StateFabricService:
    interval_sec: float = 1.0
    history_limit: int = 120
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None
    _latest: dict[str, Any] = field(default_factory=dict)
    _history: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=120))
    _last_error: str = ""
    _sample_count: int = 0
    _last_key: str = ""

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._history = deque(self._history, maxlen=max(10, int(self.history_limit or 120)))
        self._thread = threading.Thread(target=self._run, name="jachin-state-fabric", daemon=True)
        self._thread.start()
        append_event("state_fabric_started", "state-fabric", {"interval_sec": self.interval_sec})

    def stop(self, timeout_sec: float = 2.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(0.1, timeout_sec))
        append_event("state_fabric_stopped", "state-fabric", self.status().to_dict())

    def sample_once(self) -> dict[str, Any]:
        sampled = sample_state()
        snapshot = self._normalize_snapshot(sampled)
        with _LOCK:
            self._latest = snapshot
            self._history.appendleft(snapshot)
            self._sample_count += 1
        self._persist(snapshot)
        self._emit_delta(snapshot)
        return snapshot

    def latest(self, max_age_ms: int = 5_000) -> dict[str, Any]:
        with _LOCK:
            latest = dict(self._latest)
        if latest and _now_ms() - int(latest.get("sampled_at_ms") or 0) <= max_age_ms:
            return latest
        return self.sample_once()

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        with _LOCK:
            return [dict(item) for item in list(self._history)[: max(0, limit)]]

    def status(self) -> StateFabricStatus:
        with _LOCK:
            latest = dict(self._latest)
            count = self._sample_count
            err = self._last_error
        return StateFabricStatus(
            running=bool(self._thread and self._thread.is_alive()),
            sample_count=count,
            last_sample_at_ms=int(latest.get("sampled_at_ms") or 0),
            last_error=err,
            interval_sec=float(self.interval_sec),
            snapshot_path=str(_snapshot_path()),
            history_path=str(_history_path()),
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.sample_once()
                self._last_error = ""
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                append_event("state_fabric_sample_failed", "state-fabric", {"error": self._last_error})
            self._stop.wait(max(0.2, float(self.interval_sec or 1.0)))

    def _normalize_snapshot(self, sampled: dict[str, Any]) -> dict[str, Any]:
        now = _now_ms()
        out = dict(sampled or {})
        out.setdefault("sampled_at_ms", now)
        out["fabric_generated_at_ms"] = now
        out["freshness_ms"] = max(0, now - int(out.get("sampled_at_ms") or now))
        out.setdefault("active_window", {})
        out.setdefault("running_apps", [])
        out.setdefault("recent_app_events", [])
        out.setdefault("resource_state", {})
        out.setdefault("risk_state", {})
        out["service"] = {"name": "StateFabric", "version": 1}
        return out

    def _persist(self, snapshot: dict[str, Any]) -> None:
        _snapshot_path().write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        line = json.dumps(snapshot, ensure_ascii=False, default=str)
        with _history_path().open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _emit_delta(self, snapshot: dict[str, Any]) -> None:
        active = snapshot.get("active_window") if isinstance(snapshot.get("active_window"), dict) else {}
        key = f"{active.get('hwnd')}:{active.get('pid')}:{active.get('title') or active.get('window_title')}"
        if key and key != self._last_key:
            self._last_key = key
            append_event(
                "state_fabric_window_changed",
                "state-fabric",
                {
                    "active_window": active,
                    "sampled_at_ms": snapshot.get("sampled_at_ms"),
                    "freshness_ms": snapshot.get("freshness_ms"),
                },
            )


def _snapshot_path():
    return state_dir() / "state_fabric_latest.json"


def _history_path():
    return state_dir() / "state_fabric_history.jsonl"


def get_state_fabric_service() -> StateFabricService:
    global _SERVICE
    with _LOCK:
        if _SERVICE is None:
            interval = _env_float("JACHIN_STATE_FABRIC_INTERVAL_SEC", 1.0, 0.2, 30.0)
            limit = int(_env_float("JACHIN_STATE_FABRIC_HISTORY_LIMIT", 120, 10, 1000))
            _SERVICE = StateFabricService(interval_sec=interval, history_limit=limit)
        return _SERVICE


def start_state_fabric_service() -> StateFabricService:
    service = get_state_fabric_service()
    service.start()
    return service


def stop_state_fabric_service() -> None:
    service = get_state_fabric_service()
    service.stop()


def get_state_fabric_snapshot(max_age_ms: int = 5_000) -> dict[str, Any]:
    service = start_state_fabric_service()
    return service.latest(max_age_ms=max_age_ms)


def get_state_fabric_status() -> dict[str, Any]:
    return get_state_fabric_service().status().to_dict()


def _env_float(name: str, default: float, low: float, high: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        value = float(raw) if raw else float(default)
    except ValueError:
        value = float(default)
    return max(low, min(high, value))
