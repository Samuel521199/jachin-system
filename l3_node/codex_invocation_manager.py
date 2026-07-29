"""Persistent single-machine lifecycle manager for Codex desktop invocations."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ACTIVE_STATUSES = {"queued", "running", "waiting"}
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
VALID_STATUSES = ACTIVE_STATUSES | TERMINAL_STATUSES


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class CodexInvocationManager:
    def __init__(
        self,
        root: str | Path | None = None,
        *,
        lease_ttl_seconds: int | None = None,
        recover: bool = True,
    ) -> None:
        default_root = (
            os.environ.get("JACHIN_WORK_LEDGER_HOME")
            or str(Path.home() / ".jachin" / "work_ledger")
        )
        self.root = Path(root or default_root).expanduser().resolve() / "codex_invocations"
        self.records_dir = self.root / "records"
        self.lease_path = self.root / "desktop.lease.json"
        self.records_dir.mkdir(parents=True, exist_ok=True)
        self.lease_ttl_seconds = max(
            30,
            int(
                lease_ttl_seconds
                or os.environ.get("JACHIN_CODEX_LEASE_TTL_SECONDS")
                or "900"
            ),
        )
        self._lock = threading.RLock()
        if recover:
            self.recover_orphans()

    def _record_path(self, invocation_id: str) -> Path:
        clean = "".join(
            char
            for char in str(invocation_id or "")
            if char.isalnum() or char in {"-", "_"}
        )[:80]
        if not clean:
            raise ValueError("invocation_id_required")
        return self.records_dir / f"{clean}.json"

    def _read_json(self, path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write_json(self, path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        temp.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        os.replace(temp, path)

    def register(
        self,
        invocation_id: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        path = self._record_path(invocation_id)
        with self._lock:
            existing = self._read_json(path)
            if existing:
                return existing
            now = _now_iso()
            record = {
                "schema_version": 1,
                "invocation_id": invocation_id,
                "status": "queued",
                "cancel_requested": False,
                "created_at": now,
                "updated_at": now,
                "started_at": "",
                "finished_at": "",
                "owner_pid": 0,
                "stage": "queued",
                "detail": "waiting_for_codex_desktop_lease",
                "metadata": dict(metadata or {}),
                "history": [
                    {
                        "at": now,
                        "status": "queued",
                        "stage": "queued",
                        "detail": "invocation_registered",
                    }
                ],
            }
            self._write_json(path, record)
            return record

    def get(self, invocation_id: str) -> dict[str, Any]:
        with self._lock:
            return self._read_json(self._record_path(invocation_id))

    def list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = [
            self._read_json(path)
            for path in self.records_dir.glob("*.json")
        ]
        rows = [row for row in rows if row]
        rows.sort(
            key=lambda row: str(row.get("updated_at") or ""),
            reverse=True,
        )
        return rows[: max(1, min(int(limit or 100), 1000))]

    def transition(
        self,
        invocation_id: str,
        status: str,
        *,
        stage: str = "",
        detail: str = "",
        metadata_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in VALID_STATUSES:
            raise ValueError(f"invalid_invocation_status:{status}")
        path = self._record_path(invocation_id)
        with self._lock:
            record = self._read_json(path) or self.register(invocation_id)
            now = _now_iso()
            record["status"] = status
            record["updated_at"] = now
            if stage:
                record["stage"] = stage
            if detail:
                record["detail"] = detail
            if status in {"running", "waiting"}:
                record["owner_pid"] = os.getpid()
                if not record.get("started_at"):
                    record["started_at"] = now
            if status in TERMINAL_STATUSES:
                record["finished_at"] = now
            if metadata_patch:
                metadata = (
                    record.get("metadata")
                    if isinstance(record.get("metadata"), dict)
                    else {}
                )
                metadata.update(metadata_patch)
                record["metadata"] = metadata
            history = (
                record.get("history")
                if isinstance(record.get("history"), list)
                else []
            )
            history.append(
                {
                    "at": now,
                    "status": status,
                    "stage": stage or record.get("stage"),
                    "detail": detail or record.get("detail"),
                }
            )
            record["history"] = history[-120:]
            self._write_json(path, record)
            return record

    def cancel(self, invocation_id: str, *, reason: str = "user_cancelled") -> dict[str, Any]:
        path = self._record_path(invocation_id)
        with self._lock:
            record = self._read_json(path)
            if not record:
                raise ValueError("codex_invocation_not_found")
            if record.get("status") in TERMINAL_STATUSES:
                return record
            record["cancel_requested"] = True
            record["cancel_reason"] = reason
            record["updated_at"] = _now_iso()
            self._write_json(path, record)
            if record.get("status") == "queued":
                return self.transition(
                    invocation_id,
                    "cancelled",
                    stage="cancelled_before_acquire",
                    detail=reason,
                )
            return record

    def is_cancel_requested(self, invocation_id: str) -> bool:
        return bool(self.get(invocation_id).get("cancel_requested"))

    def _lease(self) -> dict[str, Any]:
        return self._read_json(self.lease_path)

    def _lease_stale(self, lease: dict[str, Any]) -> bool:
        if not lease:
            return True
        heartbeat = float(lease.get("heartbeat_epoch") or 0)
        if heartbeat <= 0 or time.time() - heartbeat > self.lease_ttl_seconds:
            return True
        return not _pid_alive(int(lease.get("owner_pid") or 0))

    def _remove_stale_lease(self) -> bool:
        with self._lock:
            lease = self._lease()
            if not lease or not self._lease_stale(lease):
                return False
            stale_id = str(lease.get("invocation_id") or "")
            try:
                self.lease_path.unlink(missing_ok=True)
            except OSError:
                return False
            if stale_id:
                record = self.get(stale_id)
                if record.get("status") in ACTIVE_STATUSES:
                    self.transition(
                        stale_id,
                        "failed",
                        stage="orphan_recovery",
                        detail="codex_desktop_lease_became_stale",
                    )
            return True

    def acquire(
        self,
        invocation_id: str,
        *,
        metadata: dict[str, Any] | None = None,
        timeout_seconds: int = 120,
        poll_seconds: float = 0.2,
    ) -> dict[str, Any]:
        self.register(invocation_id, metadata=metadata)
        deadline = time.monotonic() + max(1, int(timeout_seconds or 120))
        while time.monotonic() < deadline:
            if self.is_cancel_requested(invocation_id):
                self.transition(
                    invocation_id,
                    "cancelled",
                    stage="cancelled_before_acquire",
                    detail="cancel_requested_while_queued",
                )
                return {"ok": False, "cancelled": True, "detail": "cancelled"}
            self._remove_stale_lease()
            payload = {
                "schema_version": 1,
                "invocation_id": invocation_id,
                "owner_pid": os.getpid(),
                "acquired_at": _now_iso(),
                "heartbeat_epoch": time.time(),
            }
            try:
                fd = os.open(
                    self.lease_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
            except FileExistsError:
                time.sleep(max(0.05, float(poll_seconds or 0.2)))
                continue
            try:
                os.write(
                    fd,
                    json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                )
            finally:
                os.close(fd)
            self.transition(
                invocation_id,
                "running",
                stage="lease_acquired",
                detail="codex_desktop_lease_acquired",
            )
            return {"ok": True, "detail": "lease_acquired", "lease": payload}
        self.transition(
            invocation_id,
            "failed",
            stage="queue_timeout",
            detail="codex_invocation_queue_timeout",
        )
        return {"ok": False, "cancelled": False, "detail": "queue_timeout"}

    def heartbeat(
        self,
        invocation_id: str,
        *,
        status: str = "running",
        stage: str,
        detail: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            lease = self._lease()
            if str(lease.get("invocation_id") or "") != invocation_id:
                return {"ok": False, "detail": "lease_not_owned"}
            lease["heartbeat_epoch"] = time.time()
            lease["stage"] = stage
            self._write_json(self.lease_path, lease)
        record = self.transition(
            invocation_id,
            status if status in {"running", "waiting"} else "running",
            stage=stage,
            detail=detail or stage,
        )
        return {"ok": True, "record": record}

    def release(
        self,
        invocation_id: str,
        *,
        status: str,
        stage: str,
        detail: str,
    ) -> dict[str, Any]:
        if status not in TERMINAL_STATUSES:
            raise ValueError("terminal_status_required")
        with self._lock:
            lease = self._lease()
            if str(lease.get("invocation_id") or "") == invocation_id:
                self.lease_path.unlink(missing_ok=True)
        return self.transition(
            invocation_id,
            status,
            stage=stage,
            detail=detail,
        )

    def recover_orphans(self) -> dict[str, Any]:
        recovered: list[str] = []
        self._remove_stale_lease()
        live_lease = self._lease()
        live_id = str(live_lease.get("invocation_id") or "")
        for record in self.list(limit=1000):
            invocation_id = str(record.get("invocation_id") or "")
            if not invocation_id or record.get("status") not in ACTIVE_STATUSES:
                continue
            if invocation_id == live_id and not self._lease_stale(live_lease):
                continue
            self.transition(
                invocation_id,
                "failed",
                stage="orphan_recovery",
                detail="orphaned_invocation_recovered_after_restart",
            )
            recovered.append(invocation_id)
        return {"ok": True, "recovered_count": len(recovered), "invocation_ids": recovered}


class CodexInvocationLeaseGuard:
    """Ensures an acquired desktop lease reaches a terminal state."""

    def __init__(
        self,
        manager: CodexInvocationManager,
        invocation_id: str,
    ) -> None:
        self.manager = manager
        self.invocation_id = invocation_id
        self.finished = False

    def heartbeat(
        self,
        stage: str,
        *,
        status: str = "running",
        detail: str = "",
    ) -> dict[str, Any]:
        return self.manager.heartbeat(
            self.invocation_id,
            status=status,
            stage=stage,
            detail=detail,
        )

    def cancel_requested(self) -> bool:
        return self.manager.is_cancel_requested(self.invocation_id)

    def finish(
        self,
        status: str,
        *,
        stage: str,
        detail: str,
    ) -> dict[str, Any]:
        if self.finished:
            return self.manager.get(self.invocation_id)
        self.finished = True
        return self.manager.release(
            self.invocation_id,
            status=status,
            stage=stage,
            detail=detail,
        )

    def __del__(self) -> None:
        if self.finished:
            return
        try:
            self.finish(
                "failed",
                stage="unexpected_exit",
                detail="codex_invocation_exited_without_terminal_status",
            )
        except Exception:
            pass


_DEFAULT_MANAGER: CodexInvocationManager | None = None
_DEFAULT_MANAGER_LOCK = threading.Lock()


def get_codex_invocation_manager() -> CodexInvocationManager:
    global _DEFAULT_MANAGER
    with _DEFAULT_MANAGER_LOCK:
        if _DEFAULT_MANAGER is None:
            _DEFAULT_MANAGER = CodexInvocationManager()
        return _DEFAULT_MANAGER
