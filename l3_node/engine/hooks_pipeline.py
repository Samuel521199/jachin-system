"""
L3 洋葱中间件 (Koa.js 风格)

支持 pre_intent, pre_llm, pre_tool_exec, post_tool_exec, pre_response。
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

HOOK_ON_INTENT_RECEIVED = "on_intent_received"
HOOK_BEFORE_LLM_THINK = "before_llm_think"
HOOK_BEFORE_TOOL_EXEC = "before_tool_exec"
HOOK_AFTER_TOOL_EXEC = "after_tool_exec"
HOOK_BEFORE_RESPONSE = "before_response"

# AGI 路线图 §3.2.3：生命周期 / 韧性 / 记忆（插件按需注册；未注册则无开销）
HOOK_ON_TASK_DECOMPOSE = "on_task_decompose"
HOOK_ON_TASK_NODE_START = "on_task_node_start"
HOOK_ON_TASK_NODE_DONE = "on_task_node_done"
HOOK_ON_TASK_DAG_COMPLETE = "on_task_dag_complete"
HOOK_ON_AGENT_TEAM_ASSEMBLED = "on_agent_team_assembled"
HOOK_ON_DISCUSSION_ROUND_START = "on_discussion_round_start"
HOOK_ON_DISCUSSION_ROUND_END = "on_discussion_round_end"
HOOK_ON_CONSENSUS_REACHED = "on_consensus_reached"
HOOK_ON_RETRY = "on_retry"
HOOK_ON_STRATEGY_SHIFT = "on_strategy_shift"
HOOK_ON_EXECUTION_BRIEF = "on_execution_brief"
HOOK_ON_MEMORY_COMMIT = "on_memory_commit"
HOOK_ON_EXPERIENCE_LEARNED = "on_experience_learned"

Middleware = Callable[["PipelineContext", Callable[[], Awaitable[None]]], Awaitable[None]]
HookHandler = Callable[["PipelineContext"], Awaitable[None]]


class HookRegistry:
    def __init__(self) -> None:
        self._hooks: dict[str, list[HookHandler]] = {}

    def register(self, name: str, handler: HookHandler) -> None:
        self._hooks.setdefault(name, []).append(handler)

    async def run(self, name: str, ctx: "PipelineContext") -> None:
        for h in self._hooks.get(name, []):
            try:
                await h(ctx)
                if ctx.aborted:
                    return
            except Exception as e:
                logger.warning("[HookRegistry] %s 异常: %s", name, e)


global_hooks = HookRegistry()


class PipelineContext:
    def __init__(
        self,
        intent: str,
        source: str = "",
        session_id: str = "",
        run_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.intent = intent
        self.source = source
        self.session_id = session_id
        self.run_id = run_id
        self.metadata = metadata or {}
        self.messages: list[dict[str, str]] = []
        self.system_prompt = ""
        self.current_response = ""
        self.parsed_action: dict[str, Any] | None = None
        self.observation = ""
        self.final_answer: str | None = None
        self.aborted = False
        self.abort_reason = ""
        self.swarm_resolved = False


class Pipeline:
    def __init__(self) -> None:
        self._middlewares: list[Middleware] = []

    def use(self, middleware: Middleware) -> "Pipeline":
        self._middlewares.append(middleware)
        return self

    async def execute(self, ctx: PipelineContext) -> None:
        index = 0

        async def next_fn() -> None:
            nonlocal index
            if index < len(self._middlewares):
                m = self._middlewares[index]
                index += 1
                await m(ctx, next_fn)

        await next_fn()
