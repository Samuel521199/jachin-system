# L3 Cognitive Kernel Runtime

`l3_node` is the local runtime for Jachin. The active architecture is the
Memory-first Cognitive Kernel:

- input envelope
- state watcher
- memory recall
- decision contract
- work order
- role executor
- verification and recovery
- turn closure and evidence ledger

The historic text ReAct loop still exists as a compatibility transport inside
`agent_core.py`, but it is no longer the architecture boundary. New business
logic must live in Skill/MCP packages or in focused Role Agent adapters.

Source of truth:

- `docs/07_memory_first_main_agent_and_voice_app_agents.md`
- `docs/08_memory_first_cognitive_kernel_execution_plan.md`
