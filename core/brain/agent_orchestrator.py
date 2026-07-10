"""Kernel-backed orchestrator shim.

The historical JSON/work-order orchestrator has been removed. This class keeps
the old import surface available while routing execution through the
Memory-first Cognitive Kernel.
"""

from __future__ import annotations

from typing import Any

import ray


@ray.remote(num_cpus=0.2, num_gpus=0)
class AgentOrchestrator:
    """Compatibility actor that delegates to the Cognitive Kernel."""

    async def run(self, user_input: str) -> dict[str, Any]:
        from l3_node.agent_core import run_agent

        answer = await run_agent(
            user_input,
            implicit_attribution={"source": "core.brain.agent_orchestrator"},
        )
        return {"success": True, "answer": answer, "kernel": "memory_first"}
