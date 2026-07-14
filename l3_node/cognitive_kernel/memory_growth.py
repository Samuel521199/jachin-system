"""AI self-growing knowledge system filesystem layer.

The Memory Growth layer is intentionally filesystem-first:

- Raw evidence is append-only JSONL.
- Concepts/playbooks/outputs/reviews are human-readable Markdown surfaces.
- Graph/semantic engines can be attached later without changing the raw event
  contract.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .contracts import TurnClosure
from .paths import kernel_home

SCHEMA_VERSION = 1

RAW_CATEGORIES = (
    "conversations",
    "evidence",
    "files",
    "app_activity",
    "skill_runs",
    "mcp_runs",
    "lark_messages",
    "reports",
)

WIKI_CATEGORIES = (
    "concepts",
    "playbooks",
    "outputs",
    "reviews",
    "indexes",
    "conflicts",
    "graph",
)

_LOCK = threading.RLock()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _day() -> str:
    return time.strftime("%Y%m%d")


def memory_growth_dir() -> Path:
    path = kernel_home() / "memory_growth"
    path.mkdir(parents=True, exist_ok=True)
    return path


def raw_dir(category: str) -> Path:
    clean = _normalize_category(category)
    path = memory_growth_dir() / "raw" / clean
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_memory_growth_scaffold() -> Path:
    """Create the runtime Memory Growth directory skeleton.

    The files are deliberately tiny. They make the runtime folder explainable
    when a user opens it, while actual data remains append-only JSONL.
    """

    root = memory_growth_dir()
    (root / "raw").mkdir(parents=True, exist_ok=True)
    for category in RAW_CATEGORIES:
        (root / "raw" / category).mkdir(parents=True, exist_ok=True)
    for category in WIKI_CATEGORIES:
        (root / category).mkdir(parents=True, exist_ok=True)
    (root / "reviews" / "patches").mkdir(parents=True, exist_ok=True)

    _write_once(
        root / "README.md",
        "# Jachin Memory Growth\n\n"
        "This directory stores Jachin's AI self-growing knowledge system.\n\n"
        "- raw/: append-only original evidence\n"
        "- concepts/: high-value Markdown concepts\n"
        "- playbooks/: reusable methods grown from repeated tasks\n"
        "- outputs/: final artifacts and user-facing outputs\n"
        "- reviews/: daily/weekly digestion results\n"
        "- indexes/: overview and lookup indexes\n"
        "- conflicts/: conflicting or unconfirmed facts\n",
    )
    _write_once(
        root / "raw" / "README.md",
        "# Raw Evidence\n\n"
        "Append-only JSONL event streams. Do not edit historical lines in place.\n",
    )
    _write_once(
        root / "concepts" / "README.md",
        "# Concepts\n\n"
        "Stable, high-value facts extracted from raw evidence. Each fact should cite source_refs.\n",
    )
    _write_once(
        root / "playbooks" / "README.md",
        "# Playbooks\n\n"
        "Reusable task methods grown from successful and failed executions.\n",
    )
    _write_once(
        root / "reviews" / "README.md",
        "# Reviews\n\n"
        "Daily and weekly digestion outputs. Review patches are proposals and should not overwrite concepts directly.\n",
    )
    _write_once(
        root / "graph" / "README.md",
        "# Graph Sync\n\n"
        "Local entity/relation sync events derived from Markdown wiki pages. External graph engines can consume this later.\n",
    )
    return root


def append_raw_event(
    *,
    category: str,
    source: str,
    payload: dict[str, Any],
    source_refs: list[dict[str, Any]] | None = None,
    review: dict[str, Any] | None = None,
    stream: str = "events",
) -> Path:
    """Append a Memory Growth raw event and return the JSONL path."""

    ensure_memory_growth_scaffold()
    clean_category = _normalize_category(category)
    clean_stream = _safe_stream(stream)
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_id": f"raw_{_now_ms()}_{uuid.uuid4().hex[:10]}",
        "ts_ms": _now_ms(),
        "date": time.strftime("%Y-%m-%d"),
        "category": clean_category,
        "source": str(source or "unknown"),
        "source_refs": list(source_refs or []),
        "review": review
        or {
            "review_candidate": True,
            "promotion_targets": ["concepts", "playbooks"],
            "priority": "normal",
        },
        "payload": payload,
    }
    path = raw_dir(clean_category) / f"{_day()}.{clean_stream}.jsonl"
    line = json.dumps(event, ensure_ascii=False, default=str)
    with _LOCK:
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    return path


def record_turn_closure_raw(closure: TurnClosure) -> Path:
    """Record a TurnClosure as raw evidence for later digestion."""

    closure_dict = closure.to_dict()
    source_refs = [
        {
            "type": "cognitive_kernel_ledger",
            "event_type": "turn_closure",
            "turn_id": closure.turn_id,
        }
    ]
    if closure.executed_work_orders:
        source_refs.extend(
            {
                "type": "work_order",
                "work_order_id": work_order_id,
                "turn_id": closure.turn_id,
            }
            for work_order_id in closure.executed_work_orders
        )
    review_priority = "high" if closure.memory_write_requests or closure.pending_decision else "normal"
    if closure.verification_status == "failed":
        review_priority = "high"
    return append_raw_event(
        category="evidence",
        source="turn_closure_agent",
        stream="turn_closure",
        payload={
            "turn_id": closure.turn_id,
            "closure": closure_dict,
            "promotion_hints": _promotion_hints_for_closure(closure),
        },
        source_refs=source_refs,
        review={
            "review_candidate": True,
            "promotion_targets": ["concepts", "playbooks", "outputs"],
            "priority": review_priority,
            "reason": "turn_closed",
        },
    )


def _promotion_hints_for_closure(closure: TurnClosure) -> dict[str, Any]:
    hints: dict[str, Any] = {
        "has_executed_work_orders": bool(closure.executed_work_orders),
        "has_pending_decision": bool(closure.pending_decision),
        "verification_status": closure.verification_status,
        "closure_type": getattr(closure.closure_type, "value", str(closure.closure_type)),
    }
    if closure.memory_write_requests:
        hints["memory_types"] = [req.memory_type for req in closure.memory_write_requests]
        hints["source_events"] = [req.source_event for req in closure.memory_write_requests]
    return hints


def _normalize_category(category: str) -> str:
    raw = str(category or "evidence").strip().lower()
    if raw in RAW_CATEGORIES:
        return raw
    return "evidence"


def _safe_stream(stream: str) -> str:
    raw = str(stream or "events").strip().lower()
    clean = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in raw)
    return clean or "events"


def _write_once(path: Path, text: str) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
