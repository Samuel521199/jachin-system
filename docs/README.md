# Jachin Documentation

This documentation index follows the current L3 architecture:

```text
Jachin = Cognitive Kernel + Role Agent Network + Tool Execution Layer
       + State Fabric + Memory Learning Layer
```

The old ReAct-first, hybrid-agent-first, and L1/L2/L3-first architecture
documents have been removed. `run_agent` is now treated as compatibility
execution infrastructure behind the Memory-first Cognitive Kernel.

## Architecture Source of Truth

| Document | Purpose |
| --- | --- |
| [07_memory_first_main_agent_and_voice_app_agents.md](./07_memory_first_main_agent_and_voice_app_agents.md) | Current L3 architecture specification: memory-first kernel, role agents, state snapshots, WorkOrders, verification, recovery, and TurnClosure. |
| [08_memory_first_cognitive_kernel_execution_plan.md](./08_memory_first_cognitive_kernel_execution_plan.md) | Live implementation ledger for migrating the repo to the new architecture and removing stale architecture residue. |

## Runtime And Capability References

| Document | Purpose |
| --- | --- |
| [QUICKSTART.md](./QUICKSTART.md) | Local startup guide. |
| [L3_EMBEDDED_RUNTIME.md](./L3_EMBEDDED_RUNTIME.md) | Packaged embedded Python/Node runtime. |
| [L3_SLIM_DISTRIBUTION_AND_SUBSCRIBED_ARTIFACTS.md](./L3_SLIM_DISTRIBUTION_AND_SUBSCRIBED_ARTIFACTS.md) | Slim packaged distribution and subscribed capability artifacts. |
| [MCP_SPEC.md](./MCP_SPEC.md) | MCP protocol notes. |
| [MCP_EXECUTION_MODEL.md](./MCP_EXECUTION_MODEL.md) | MCP execution model. |
| [MCP_SKILL_INDEPENDENCE.md](./MCP_SKILL_INDEPENDENCE.md) | Skill/MCP independence rules. |
| [SKILL_MD_SPEC.md](./SKILL_MD_SPEC.md) | Skill manifest and documentation format. |
| [SKILL_MCP_FLOW_AND_RECENT_CHANGES.md](./SKILL_MCP_FLOW_AND_RECENT_CHANGES.md) | Skill/MCP flow notes. |
| [SKILL_MCP_UPLOAD_SPEC.md](./SKILL_MCP_UPLOAD_SPEC.md) | Skill/MCP upload packaging rules. |
| [VOICE_AND_TTS_GUIDE.md](./VOICE_AND_TTS_GUIDE.md) | Voice and TTS operations. |
| [TESTING_GUIDE.md](./TESTING_GUIDE.md) | Testing guide. |

## Business Skills

Business capabilities must remain independent skills or MCPs. They are not
kernel logic.

| Area | Document |
| --- | --- |
| BI | [bi_daily_report/](./bi_daily_report/) |
| HR | [HR_RECRUITMENT.md](./HR_RECRUITMENT.md) |
| PMO | [../skills_repo/pmo-copilot/SKILL.md](../skills_repo/pmo-copilot/SKILL.md) |
| English Learning | [../skills_repo/com.jachin.skill.english-learning-assistant/plugin.json](../skills_repo/com.jachin.skill.english-learning-assistant/plugin.json) |

## Product

| Document | Purpose |
| --- | --- |
| [VISION.md](./VISION.md) | Product vision. |
| [USER_GUIDE_NEXUS_PUBLIC.md](./USER_GUIDE_NEXUS_PUBLIC.md) | Public Nexus guide. |
| [USER_GUIDE_NEXUS_QUICK.md](./USER_GUIDE_NEXUS_QUICK.md) | Quick Nexus guide. |
