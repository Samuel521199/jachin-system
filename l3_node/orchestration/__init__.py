"""
Jachin 长期编排 — 三层架构入口。

- **L1** `skill_routing`：意图 → 向量技能候选（大规模 skill 收窄）
- **L2** `domain_registry`：领域子图（HR、未来 BI 等）
- **L3** `workflow_spec_runner` + YAML `domain_ref`：跨域 glue

详见 `docs/07_memory_first_main_agent_and_voice_app_agents.md`。
"""
from __future__ import annotations

from l3_node.orchestration.domain_registry import list_domains, register_domain, run_domain
from l3_node.orchestration.layers import OrchestrationLayer
from l3_node.orchestration.skill_routing import is_skill_routing_enabled, suggest_skills_from_intent

__all__ = [
    "OrchestrationLayer",
    "is_skill_routing_enabled",
    "suggest_skills_from_intent",
    "register_domain",
    "run_domain",
    "list_domains",
]
