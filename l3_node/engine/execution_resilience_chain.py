"""
失败自动策略链（路线图 §3 Hook 韧性 · P2 初版）

在 HOOK_ON_RETRY / HOOK_ON_EXECUTION_BRIEF 时按 `_retry_reason` / `_execution_brief_reason`
推进策略档位，并向 ctx.metadata 写入 `[StrategyShift]` 日志字段与可选 user 注入提示。

环境变量
--------
JACHIN_RESILIENCE_STRATEGY_CHAIN=1     开启（默认关）
JACHIN_RESILIENCE_STRATEGY_MAX=4       单 run 最大策略切换次数（默认 4）
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_STRATEGIES = (
    "retry_same",
    "retry_degraded",
    "skip_per_item",
    "brief_and_stop",
)


def strategy_chain_enabled() -> bool:
    return (os.environ.get("JACHIN_RESILIENCE_STRATEGY_CHAIN") or "").strip().lower() in (
        "1", "true", "yes",
    )


def _max_shifts() -> int:
    try:
        return max(1, min(8, int(os.environ.get("JACHIN_RESILIENCE_STRATEGY_MAX") or "4")))
    except ValueError:
        return 4


def _next_strategy(current: str | None) -> str:
    if not current or current not in _STRATEGIES:
        return _STRATEGIES[0]
    idx = _STRATEGIES.index(current)
    return _STRATEGIES[min(idx + 1, len(_STRATEGIES) - 1)]


def _strategy_hint(strategy: str, reason: str) -> str:
    hints = {
        "retry_same": "保持目标不变，可微调参数后重试一次（勿同参死循环）。",
        "retry_degraded": "降级为更小批量/只读探查/单文件重试，再决定是否继续。",
        "skip_per_item": "批量任务跳过失败子项并记录 RunReport，其余继续。",
        "brief_and_stop": "产出 ExecutionBrief：已尝试策略、失败类别与建议人工动作，然后停止扩张。",
    }
    base = hints.get(strategy, "")
    return f"【策略链·{strategy}】{base}（触发原因：{reason or 'unknown'}）"


def advance_resilience_strategy(ctx: Any, *, hook: str) -> None:
    if not strategy_chain_enabled():
        return
    md = getattr(ctx, "metadata", None) or {}
    count = int(md.get("_resilience_strategy_count") or 0)
    if count >= _max_shifts():
        md["_resilience_strategy"] = "brief_and_stop"
        md["_resilience_strategy_blocked"] = True
        return
    reason = str(
        md.get("_retry_reason")
        or md.get("_execution_brief_reason")
        or hook
    )
    cur = str(md.get("_resilience_strategy") or "")
    nxt = _next_strategy(cur if cur else None)
    md["_resilience_strategy"] = nxt
    md["_resilience_strategy_count"] = count + 1
    md["_resilience_strategy_hint"] = _strategy_hint(nxt, reason)
    md["_resilience_strategy_pending_inject"] = True
    logger.info(
        "[StrategyShift] hook=%s run_id=%s strategy=%s count=%d reason=%s",
        hook,
        str(getattr(ctx, "run_id", "") or "")[:12],
        nxt,
        count + 1,
        reason[:80],
    )
    try:
        from l3_node.engine.hooks_pipeline import HOOK_ON_STRATEGY_SHIFT, global_hooks

        asyncio.get_running_loop().create_task(global_hooks.run(HOOK_ON_STRATEGY_SHIFT, ctx))
    except RuntimeError:
        pass
    except Exception:
        pass


async def _on_retry(ctx: Any) -> None:
    advance_resilience_strategy(ctx, hook="on_retry")


async def _on_execution_brief(ctx: Any) -> None:
    advance_resilience_strategy(ctx, hook="on_execution_brief")


def pop_strategy_inject_message(ctx: Any) -> dict[str, str] | None:
    """
    若策略链已推进且尚未注入本轮 ReAct，返回一条 user 消息并清除 pending 标记。
    由 agent_core 每轮 LLM 前调用（需 JACHIN_RESILIENCE_STRATEGY_CHAIN=1）。
    """
    if not strategy_chain_enabled():
        return None
    md = getattr(ctx, "metadata", None)
    if not isinstance(md, dict):
        return None
    if not md.pop("_resilience_strategy_pending_inject", False):
        return None
    hint = str(md.get("_resilience_strategy_hint") or "").strip()
    if not hint:
        return None
    strat = str(md.get("_resilience_strategy") or "")
    body = (
        "【执行韧性·策略链】本轮请遵循以下策略，勿同参死循环重试：\n"
        f"{hint}"
    )
    if strat == "brief_and_stop":
        body += "\n若仍无法推进，应产出 ExecutionBrief 并停止自动扩张。"
    return {"role": "user", "content": body}


def register_execution_resilience_hooks() -> None:
    try:
        from l3_node.engine.hooks_pipeline import (
            HOOK_ON_EXECUTION_BRIEF,
            HOOK_ON_RETRY,
            global_hooks,
        )
    except ImportError:
        return
    global_hooks.register(HOOK_ON_RETRY, _on_retry)
    global_hooks.register(HOOK_ON_EXECUTION_BRIEF, _on_execution_brief)
    logger.debug("[ResilienceChain] hooks registered")


try:
    register_execution_resilience_hooks()
except Exception:
    pass
