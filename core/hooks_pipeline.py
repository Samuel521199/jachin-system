"""
Jachin Nexus v8.0 — Nexus Hook Pipeline (洋葱中间件体系)

Koa.js 风格异步洋葱模型，支持 use(middleware) 和 execute(context, next)。
允许插件无损介入 Agent 执行流：pre_intent, pre_llm, pre_tool_exec, post_tool_exec, pre_response。
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

# 标准生命周期 Hook 名称
HOOK_ON_INTENT_RECEIVED = "on_intent_received"
HOOK_BEFORE_LLM_THINK = "before_llm_think"
HOOK_BEFORE_TOOL_EXEC = "before_tool_exec"
HOOK_AFTER_TOOL_EXEC = "after_tool_exec"
HOOK_BEFORE_RESPONSE = "before_response"

# 能力协商：Layer 3 客户端能力标识
CAP_UI_RENDER = "ui_render"  # 接收 thought/action/observation 动画广播
CAP_AUDIO_PLAY = "audio_play"  # 接收 TTS 播放
CAP_HITL_POPUP = "hitl_popup"  # 接收 HITL_REQUIRED 弹窗
# Edge Mesh Swarm：worker 能力，用于接单重计算任务
CAP_WORKER_FFMPEG = "worker_ffmpeg"
CAP_WORKER_PYTHON = "worker_python"


Middleware = Callable[["PipelineContext", Callable[[], Awaitable[None]]], Awaitable[None]]
HookHandler = Callable[["PipelineContext"], Awaitable[None]]


class HookRegistry:
    """全局 Hook 注册表，供 agent_loop 在 RoleExecutionAgent 各阶段调用"""

    def __init__(self) -> None:
        self._hooks: dict[str, list[HookHandler]] = {}

    def register(self, name: str, handler: HookHandler) -> None:
        self._hooks.setdefault(name, []).append(handler)

    def unregister(self, name: str, handler: HookHandler) -> None:
        self._hooks.get(name, []).remove(handler)

    async def run(self, name: str, ctx: "PipelineContext") -> None:
        for h in self._hooks.get(name, []):
            try:
                await h(ctx)
                if ctx.aborted:
                    return
            except Exception as e:
                logger.warning("[HookRegistry] %s 异常: %s", name, e)


# 全局 Hook 注册表，供 agent_loop 与插件使用
global_hooks = HookRegistry()


class PipelineContext:
    """Pipeline 执行上下文，贯穿所有 middleware"""

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
        self.ast_json: dict[str, Any] = self.metadata.get("ast_json") or {}
        self.messages: list[dict[str, str]] = []
        self.system_prompt = ""
        self.current_response = ""
        self.parsed_action: dict[str, Any] | None = None
        self.observation = ""
        self.final_answer: str | None = None
        self.aborted = False
        self.abort_reason = ""
        self.swarm_resolved = False  # Edge Mesh：若 True，observation 已由虫群节点回传，跳过本地执行


class Pipeline:
    """
    Koa.js 风格洋葱中间件 Pipeline。
    use(middleware) 注册中间件，execute(context) 执行链。
    """

    def __init__(self) -> None:
        self._middlewares: list[Middleware] = []

    def use(self, middleware: Middleware) -> "Pipeline":
        """注册中间件，支持链式调用"""
        self._middlewares.append(middleware)
        return self

    async def execute(self, context: PipelineContext) -> None:
        """执行中间件链，洋葱模型：进入时前半段，next() 后后半段"""
        index = 0

        async def next() -> None:
            nonlocal index
            if index >= len(self._middlewares):
                return
            mw = self._middlewares[index]
            index += 1
            await mw(context, next)

        await next()
