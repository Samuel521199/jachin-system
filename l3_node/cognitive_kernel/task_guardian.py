"""Proactive task guardian for Cognitive Kernel DAGs and pending work."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any

from .ledger import append_event
from .task_dag import TaskDag, list_task_dags, ready_nodes, save_task_dag

_LOCK = threading.RLock()
_GUARDIAN: "TaskGuardian | None" = None


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(slots=True)
class TaskGuardianStatus:
    running: bool
    scan_count: int
    last_scan_at_ms: int
    watched_dag_count: int
    ready_node_count: int
    last_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "scan_count": self.scan_count,
            "last_scan_at_ms": self.last_scan_at_ms,
            "watched_dag_count": self.watched_dag_count,
            "ready_node_count": self.ready_node_count,
            "last_error": self.last_error,
        }


class TaskGuardian:
    def __init__(self, interval_sec: float = 3.0) -> None:
        self.interval_sec = max(0.5, float(interval_sec or 3.0))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._scan_count = 0
        self._last_scan_at_ms = 0
        self._last_error = ""
        self._last_ready_count = 0
        self._last_watched_count = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="jachin-task-guardian", daemon=True)
        self._thread.start()
        append_event("task_guardian_started", "task-guardian", {"interval_sec": self.interval_sec})

    def stop(self, timeout_sec: float = 2.0) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(0.1, timeout_sec))
        append_event("task_guardian_stopped", "task-guardian", self.status().to_dict())

    def scan_once(self) -> dict[str, Any]:
        dags = [dag for dag in list_task_dags(limit=200) if dag.status in {"planned", "running", "blocked"}]
        ready = 0
        stale = 0
        now = _now_ms()
        for dag in dags:
            ready_nodes_for_dag = ready_nodes(dag)
            if ready_nodes_for_dag:
                ready += len(ready_nodes_for_dag)
                save_task_dag(dag)
                append_event(
                    "task_guardian_ready_nodes",
                    dag.turn_id,
                    {
                        "dag_id": dag.dag_id,
                        "ready_nodes": [node.to_dict() for node in ready_nodes_for_dag],
                    },
                )
            if dag.updated_at_ms and now - dag.updated_at_ms > 30 * 60 * 1000:
                stale += 1
                append_event(
                    "task_guardian_stale_task",
                    dag.turn_id,
                    {
                        "dag_id": dag.dag_id,
                        "status": dag.status,
                        "age_ms": now - dag.updated_at_ms,
                        "suggested_action": "resume_or_report_status",
                    },
                )
        self._scan_count += 1
        self._last_scan_at_ms = now
        self._last_ready_count = ready
        self._last_watched_count = len(dags)
        payload = {
            "watched_dag_count": len(dags),
            "ready_node_count": ready,
            "stale_task_count": stale,
            "scan_count": self._scan_count,
        }
        append_event("task_guardian_scan", "task-guardian", payload)
        return payload

    def status(self) -> TaskGuardianStatus:
        return TaskGuardianStatus(
            running=bool(self._thread and self._thread.is_alive()),
            scan_count=self._scan_count,
            last_scan_at_ms=self._last_scan_at_ms,
            watched_dag_count=self._last_watched_count,
            ready_node_count=self._last_ready_count,
            last_error=self._last_error,
        )

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_once()
                self._last_error = ""
            except Exception as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                append_event("task_guardian_scan_failed", "task-guardian", {"error": self._last_error})
            self._stop.wait(self.interval_sec)


def get_task_guardian() -> TaskGuardian:
    global _GUARDIAN
    with _LOCK:
        if _GUARDIAN is None:
            raw = os.environ.get("JACHIN_TASK_GUARDIAN_INTERVAL_SEC", "3").strip()
            try:
                interval = float(raw)
            except ValueError:
                interval = 3.0
            _GUARDIAN = TaskGuardian(interval_sec=interval)
        return _GUARDIAN


def start_task_guardian() -> TaskGuardian:
    guardian = get_task_guardian()
    guardian.start()
    return guardian


def stop_task_guardian() -> None:
    get_task_guardian().stop()


def scan_tasks_once() -> dict[str, Any]:
    return get_task_guardian().scan_once()
