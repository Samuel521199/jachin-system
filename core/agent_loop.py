"""Archived import shim for the Memory-first Cognitive Kernel.

The old work-order agent loop has been removed. Remaining Layer2 callers keep
importing ``core.agent_loop.run`` during the transition, but execution is now
delegated to ``l3_node.agent_core.run_agent`` and must pass through the
DecisionContract -> WorkOrder -> Dispatcher mainline.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable


class SecurityException(Exception):
    """Raised when a kernel gate or caller-level policy rejects execution."""


async def run(
    user_input: str,
    ast_json: dict[str, Any] | None = None,
    run_id: str = "",
    on_step: Callable[[str, str], None] | None = None,
    on_hitl_request: Callable[[str, str], None] | None = None,
    on_chunk: Callable[[str], Awaitable[None]] | None = None,
    **kwargs: Any,
) -> str:
    """Delegate old Layer2 calls to the Memory-first L3 agent entrypoint."""

    from l3_node.agent_core import run_agent

    metadata = dict(kwargs.pop("metadata", {}) or {})
    if ast_json:
        metadata["ast_json"] = ast_json
    if on_hitl_request:
        metadata["hitl_callback_attached"] = True
    if on_chunk:
        metadata["chunk_callback_attached"] = True

    if on_step:
        on_step("kernel", "Delegated to Memory-first Cognitive Kernel")

    return await run_agent(
        user_input,
        run_id=run_id or None,
        implicit_attribution=metadata,
        **kwargs,
    )
