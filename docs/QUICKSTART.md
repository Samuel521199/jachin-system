# Jachin Quickstart

Current architecture:

```text
L3 Desktop -> Cognitive Kernel -> Tool Execution Layer
L3 Desktop -> L1 Catalog/Profile -> installed Skill/MCP/Model registry
L2 is optional extension infrastructure, not a required startup dependency.
```

## Development Mode

Use this when developing the repo, local skills, or local MCPs.

```powershell
.\scripts\start-layer3.ps1
```

Choose development mode when prompted. Development mode may read repo-local
skills and MCPs so authors can iterate before publishing.

## Packaged Mode

Use this to test the product as a fresh user machine.

```powershell
.\scripts\start-layer3.ps1
```

Choose packaged mode when prompted. Packaged mode uses `dist_jachin_desktop`
and installed artifacts under `~/.jachin`; it must not depend on repo-local
business skills.

## L1 Capability Flow

1. Open the Jachin console.
2. Set the L1 profile URL, for example `http://47.86.39.173:3000`.
3. Open Capability Install Center.
4. Refresh catalog.
5. Install the business skill you need.

Installing a business skill should also install its declared MCP/model
dependencies. The local installed registry and the L1 catalog are reconciled
by capability id, version, and source profile.

## Core References

- [07_memory_first_main_agent_and_voice_app_agents.md](./07_memory_first_main_agent_and_voice_app_agents.md)
- [08_memory_first_cognitive_kernel_execution_plan.md](./08_memory_first_cognitive_kernel_execution_plan.md)
- [MCP_SKILL_INDEPENDENCE.md](./MCP_SKILL_INDEPENDENCE.md)
- [SKILL_MCP_UPLOAD_SPEC.md](./SKILL_MCP_UPLOAD_SPEC.md)
- [L3_SLIM_DISTRIBUTION_AND_SUBSCRIBED_ARTIFACTS.md](./L3_SLIM_DISTRIBUTION_AND_SUBSCRIBED_ARTIFACTS.md)
