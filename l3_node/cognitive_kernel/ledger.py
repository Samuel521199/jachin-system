"""Append-only task ledger for the Memory-first Cognitive Kernel."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .contracts import (
    DecisionContract,
    RecoveryPlan,
    TaskLedgerEntry,
    TurnClosure,
    VerificationReport,
    WorkOrder,
)
from .paths import ledger_dir

_LOCK = threading.RLock()


def _now_ms() -> int:
    return int(time.time() * 1000)


def current_ledger_path() -> Path:
    day = time.strftime("%Y%m%d")
    return ledger_dir() / f"cognitive_kernel_{day}.jsonl"


def append_event(event_type: str, turn_id: str, payload: dict[str, Any] | None = None) -> None:
    event = {
        "ts_ms": _now_ms(),
        "event_type": event_type,
        "turn_id": turn_id,
        "payload": payload or {},
    }
    line = json.dumps(event, ensure_ascii=False, default=str)
    with _LOCK:
        path = current_ledger_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def record_turn_started(entry: TaskLedgerEntry) -> None:
    append_event("turn_started", entry.turn_id, entry.to_dict())


def record_decision(contract: DecisionContract) -> None:
    append_event("decision_contract", contract.turn_id, contract.to_dict())


def record_work_order(work_order: WorkOrder, turn_id: str) -> None:
    append_event("work_order", turn_id, work_order.to_dict())


def record_verification(report: VerificationReport, turn_id: str) -> None:
    append_event("verification_report", turn_id, report.to_dict())


def record_recovery(plan: RecoveryPlan) -> None:
    append_event("recovery_plan", plan.turn_id, plan.to_dict())


def record_turn_closure(closure: TurnClosure) -> None:
    append_event("turn_closure", closure.turn_id, closure.to_dict())
