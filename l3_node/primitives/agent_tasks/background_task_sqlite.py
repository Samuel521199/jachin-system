"""Background task queue SQLite persistence: WAL, enqueue mirror, cold-start recovery, and shutdown flush."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()


def _db_path() -> Path:
    root = Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin")))
    d = root / "workspace" / ".background_tasks"
    d.mkdir(parents=True, exist_ok=True)
    return d / "queue.sqlite3"


def _disabled() -> bool:
    return os.environ.get("JACHIN_BACKGROUND_SQLITE", "").strip().lower() in ("0", "false", "no", "off")


def _conn() -> sqlite3.Connection:
    p = _db_path()
    c = sqlite3.connect(str(p), timeout=30.0, isolation_level=None)
    c.execute("PRAGMA journal_mode=WAL;")
    c.execute("PRAGMA synchronous=NORMAL;")
    return c


def init_schema() -> None:
    if _disabled():
        return
    with _lock:
        try:
            c = _conn()
            try:
                c.execute(
                    """
                    CREATE TABLE IF NOT EXISTS bg_pending (
                        task_id TEXT PRIMARY KEY,
                        payload TEXT NOT NULL,
                        created_at REAL NOT NULL
                    );
                    """
                )
            finally:
                c.close()
        except Exception as e:
            logger.warning("[BgSqlite] init_schema 失败: %s", e)


def insert_pending(task_id: str, payload: dict[str, Any]) -> None:
    if _disabled():
        return
    init_schema()
    raw = json.dumps(payload, ensure_ascii=False)
    now = time.time()
    with _lock:
        try:
            c = _conn()
            try:
                c.execute(
                    "INSERT OR REPLACE INTO bg_pending (task_id, payload, created_at) VALUES (?,?,?)",
                    (task_id, raw, now),
                )
            finally:
                c.close()
        except Exception as e:
            logger.warning("[BgSqlite] insert_pending %s: %s", task_id, e)


def delete_pending(task_id: str) -> None:
    if _disabled():
        return
    with _lock:
        try:
            c = _conn()
            try:
                c.execute("DELETE FROM bg_pending WHERE task_id=?", (task_id,))
            finally:
                c.close()
        except Exception as e:
            logger.debug("[BgSqlite] delete_pending %s: %s", task_id, e)


def list_pending_rows() -> list[tuple[str, str]]:
    if _disabled():
        return []
    init_schema()
    with _lock:
        try:
            c = _conn()
            try:
                cur = c.execute("SELECT task_id, payload FROM bg_pending ORDER BY created_at ASC")
                return [(str(r[0]), str(r[1])) for r in cur.fetchall()]
            finally:
                c.close()
        except Exception as e:
            logger.warning("[BgSqlite] list_pending: %s", e)
            return []


def job_to_payload(job: Any) -> dict[str, Any]:
    """BackgroundJob or dict-like."""
    if hasattr(job, "task_id"):
        return {
            "task_id": job.task_id,
            "intent": job.intent,
            "require_skills": list(job.require_skills or []),
            "max_iterations": int(job.max_iterations),
            "allowed_skills": job.allowed_skills,
            "created_at": getattr(job, "created_at", time.time()),
        }
    return dict(job)  # type: ignore[arg-type]


