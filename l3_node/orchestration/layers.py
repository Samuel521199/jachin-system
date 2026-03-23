"""
长期编排架构 — 分层常量（文档与遥测用）。

三层模型：
  L1 Skill 发现/路由 — 大规模技能 → 小候选集（向量路由、白名单、意图表）
  L2 领域子图 — 强业务状态机（HR DAGWorkflow、未来 BI/合规等）
  L3 通用 Glue — YAML workflow_spec + 可选 domain_ref 跨域串联
"""
from __future__ import annotations

from enum import IntEnum


class OrchestrationLayer(IntEnum):
    SKILL_ROUTING = 1
    DOMAIN_SUBGRAPH = 2
    YAML_GLUE = 3
