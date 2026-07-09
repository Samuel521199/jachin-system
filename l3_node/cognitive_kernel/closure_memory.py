"""TurnClosure memory write execution through MemoryWriteAgent."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Awaitable, Callable

from .contracts import TurnClosure, WorkOrder
from .dispatcher import DispatchResult, dispatch_tool_work_order
from .ledger import append_event
from .memory_lifecycle import write_lifecycle_memory

ClosureMemoryExecutor = Callable[[WorkOrder], Awaitable[str]]


def closure_memory_write_enabled() -> bool:
    raw = os.environ.get("JACHIN_DISABLE_TURN_CLOSURE_MEMORY_WRITE", "")
    return raw.strip().lower() not in {"1", "true", "yes", "on"}


def closure_memory_write_timeout_sec() -> float:
    raw = os.environ.get("JACHIN_TURN_CLOSURE_MEMORY_WRITE_TIMEOUT_SEC", "3").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 3.0
    return max(0.5, min(value, 30.0))


async def execute_turn_closure_memory_writes(
    closure: TurnClosure,
    *,
    executor: ClosureMemoryExecutor | None = None,
) -> list[DispatchResult]:
    """Materialize TurnClosure memory writes via MemoryWriteAgent.

    ``close_turn`` records the intent to write memory. This helper turns those
    intents into real WorkOrders so the closure phase follows the same
    DecisionContract -> WorkOrder -> RoleExecution -> Verification ledger as
    every other external side effect.
    """

    if not closure_memory_write_enabled() or not closure.memory_write_requests:
        return []
    results: list[DispatchResult] = []
    timeout = closure_memory_write_timeout_sec()
    for index, request in enumerate(closure.memory_write_requests):
        try:
            lifecycle_record = write_lifecycle_memory(request)
            append_event(
                "turn_closure_memory_lifecycle_indexed",
                closure.turn_id,
                {
                    "index": index,
                    "memory_id": lifecycle_record.memory_id,
                    "memory_type": lifecycle_record.memory_type,
                    "expires_at_ms": lifecycle_record.expires_at_ms,
                },
            )
        except Exception as exc:
            append_event(
                "turn_closure_memory_lifecycle_failed",
                closure.turn_id,
                {
                    "index": index,
                    "memory_type": request.memory_type,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
        action_input = json.dumps(
            {
                "content": request.content,
                "tags": _memory_tags(closure, request.memory_type),
                "source_event": request.source_event,
                "memory_type": request.memory_type,
                "ttl": request.ttl,
                "merge_policy": request.merge_policy,
                "confidence": request.confidence,
            },
            ensure_ascii=False,
        )
        try:
            result = await asyncio.wait_for(
                dispatch_tool_work_order(
                    turn_id=closure.turn_id,
                    goal=f"write TurnClosure memory request {index + 1}",
                    tool="core:local_memory_append",
                    action_input=action_input,
                    executor=executor or _missing_memory_executor_should_not_run,
                ),
                timeout=timeout,
            )
            results.append(result)
        except Exception as exc:
            append_event(
                "turn_closure_memory_write_failed",
                closure.turn_id,
                {
                    "index": index,
                    "memory_type": request.memory_type,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "timeout_sec": timeout,
                },
            )
    if results:
        append_event(
            "turn_closure_memory_write_finished",
            closure.turn_id,
            {
                "request_count": len(closure.memory_write_requests),
                "executed_count": len(results),
                "ok_count": sum(1 for item in results if item.verification.ok),
            },
        )
    return results


async def _missing_memory_executor_should_not_run(_work_order: WorkOrder) -> str:
    raise RuntimeError("MemoryWriteAgent should handle TurnClosure memory writes directly")


def _memory_tags(closure: TurnClosure, memory_type: str) -> list[str]:
    tags = ["turn_closure", str(memory_type or "memory"), str(closure.closure_type.value)]
    if closure.verification_status:
        tags.append(f"verification:{closure.verification_status}")
    return tags
