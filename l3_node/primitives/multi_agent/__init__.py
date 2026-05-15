"""
多 Agent 编排原语包。

提供两种核心模式：
- fanout: 扇出并行（同一层多 SubAgent 并发处理独立子任务）
- pipeline: 流水线串联（Planner → Executor → Reviewer 等角色链）

这两种模式建立在 agent_core.SubAgent / _spawn_sub_agent_async 之上，
不引入新的 Agent 生命周期概念，遵守四大原语 SSOT（Agent Tasks 分支）。
"""
from l3_node.primitives.multi_agent.fanout import fanout_parallel
from l3_node.primitives.multi_agent.pipeline import run_pipeline

__all__ = ["fanout_parallel", "run_pipeline"]
