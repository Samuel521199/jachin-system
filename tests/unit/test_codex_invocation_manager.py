from __future__ import annotations

import json
import threading
import time

from l3_node.codex_invocation_manager import CodexInvocationManager


def test_invocation_lifecycle_is_persisted(tmp_path):
    manager = CodexInvocationManager(tmp_path, recover=False)
    acquired = manager.acquire(
        "jcx-lifecycle",
        metadata={"project_name": "Jachin", "session_id": "work-1"},
        timeout_seconds=1,
    )
    assert acquired["ok"]
    assert manager.get("jcx-lifecycle")["status"] == "running"

    manager.heartbeat(
        "jcx-lifecycle",
        status="waiting",
        stage="wait_reply",
        detail="waiting for Codex",
    )
    waiting = manager.get("jcx-lifecycle")
    assert waiting["status"] == "waiting"
    assert waiting["stage"] == "wait_reply"

    finished = manager.release(
        "jcx-lifecycle",
        status="succeeded",
        stage="reply_validated",
        detail="reply matched invocation",
    )
    assert finished["status"] == "succeeded"
    assert finished["finished_at"]
    assert not manager.lease_path.exists()


def test_second_invocation_waits_for_exclusive_desktop_lease(tmp_path):
    first = CodexInvocationManager(tmp_path, recover=False)
    second = CodexInvocationManager(tmp_path, recover=False)
    assert first.acquire("jcx-first", timeout_seconds=1)["ok"]

    outcome: dict = {}

    def acquire_second():
        outcome.update(second.acquire("jcx-second", timeout_seconds=3))

    thread = threading.Thread(target=acquire_second)
    thread.start()
    time.sleep(0.25)
    assert second.get("jcx-second")["status"] == "queued"
    first.release(
        "jcx-first",
        status="succeeded",
        stage="done",
        detail="first finished",
    )
    thread.join(timeout=3)

    assert not thread.is_alive()
    assert outcome["ok"]
    assert second.get("jcx-second")["status"] == "running"
    second.release(
        "jcx-second",
        status="succeeded",
        stage="done",
        detail="second finished",
    )


def test_queued_invocation_can_be_cancelled(tmp_path):
    first = CodexInvocationManager(tmp_path, recover=False)
    second = CodexInvocationManager(tmp_path, recover=False)
    assert first.acquire("jcx-owner", timeout_seconds=1)["ok"]

    outcome: dict = {}

    def acquire_waiter():
        outcome.update(second.acquire("jcx-waiter", timeout_seconds=3))

    thread = threading.Thread(target=acquire_waiter)
    thread.start()
    deadline = time.time() + 2
    while time.time() < deadline:
        if second.get("jcx-waiter").get("status") == "queued":
            break
        time.sleep(0.02)
    cancelled = second.cancel("jcx-waiter", reason="test_cancel")
    thread.join(timeout=3)

    assert cancelled["status"] == "cancelled"
    assert outcome["cancelled"]
    assert second.get("jcx-waiter")["status"] == "cancelled"
    first.release(
        "jcx-owner",
        status="succeeded",
        stage="done",
        detail="owner finished",
    )


def test_restart_recovery_marks_stale_invocation_failed(tmp_path):
    manager = CodexInvocationManager(tmp_path, recover=False, lease_ttl_seconds=30)
    manager.register("jcx-orphan")
    manager.transition(
        "jcx-orphan",
        "running",
        stage="wait_reply",
        detail="process disappeared",
    )
    manager.lease_path.write_text(
        json.dumps(
            {
                "invocation_id": "jcx-orphan",
                "owner_pid": 99999999,
                "heartbeat_epoch": time.time() - 120,
            }
        ),
        encoding="utf-8",
    )

    recovered = CodexInvocationManager(
        tmp_path,
        recover=True,
        lease_ttl_seconds=30,
    )
    record = recovered.get("jcx-orphan")
    assert record["status"] == "failed"
    assert record["stage"] == "orphan_recovery"
    assert not recovered.lease_path.exists()
