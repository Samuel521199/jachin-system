# L3 Cognitive Kernel Runtime

`l3_node` is the local runtime for Jachin. Its architecture is the
Memory-first Cognitive Kernel:

- input envelope
- state watcher
- memory recall
- decision contract
- work order
- role executor
- verification and recovery
- turn closure and evidence ledger

All external-world actions must pass through `DecisionContract -> WorkOrder ->
RoleExecutor -> Verification`. Text reasoning is only an input protocol handled
by `RoleExecutionAgent`; it is not allowed to execute tools directly. New
business logic must live in Skill/MCP packages or in focused Role Agent
adapters.

Source of truth:

- `docs/07_memory_first_main_agent_and_voice_app_agents.md`
- `docs/08_memory_first_cognitive_kernel_execution_plan.md`
