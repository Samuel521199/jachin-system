"""
幽灵大脑（Router）：AOP 装饰器拦截异步 Playwright 原子操作，按 MoE 调度专家自愈后重试。

多专家协作（Multi-Expert Coordination）：
  当单个专家无法处理复合错误（DOM 错误叠加网络问题）时，
  可通过 ``with_phantom_guard(skills=["dom", "network"], parallel_triage=True)``
  启用**并行多专家会诊**：所有匹配专家同时运行诊断，任意一个成功即视为自愈。
"""

from __future__ import annotations

import asyncio
import functools
import logging
import traceback
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

from l3_client.local_mcps.agentic_mesh.memory_bank import get_memory_bank

logger = logging.getLogger("agentic_mesh.router")

P = ParamSpec("P")
R = TypeVar("R")


def _phantom_log(msg: str) -> None:
    logger.info(msg)


def _resolve_page(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    p = kwargs.get("page")
    if p is not None:
        return p
    if args and hasattr(args[0], "url"):
        return args[0]
    return None


def _should_pass_through(exc: BaseException) -> bool:
    """用户取消、键盘中断等不得进入自愈循环（避免循环导入具体业务异常类型）。"""
    name = type(exc).__name__
    if name in ("KeyboardInterrupt", "SystemExit", "GeneratorExit"):
        return True
    if name in ("CancelledError", "AbortFlow"):
        return True
    if name == "KalarokoE2EUserCancelled":
        return True
    return False


async def _run_parallel_experts(
    page: Any,
    error_msg: str,
    skill_set: list[str],
    bank: Any,
) -> bool:
    """
    并行多专家会诊：所有与当前错误匹配的专家同时运行，任意一个成功即视为整体自愈。
    适用于复合型错误（DOM 超时叠加网络波动，无法在序列中确定先后）。

    Returns
    -------
    bool: 至少一个专家自愈成功返回 True，否则 False。
    """
    el = error_msg.lower()
    expert_coros: list[Any] = []
    expert_names: list[str] = []

    if ("timeout" in el or "locator" in el or "waiting for" in el) and "dom" in skill_set:
        from l3_client.local_mcps.agentic_mesh.experts.dom_healer import DomHealer
        dom_healer = DomHealer(page)
        expert_coros.append(dom_healer.attempt_heal(error_context=error_msg))
        expert_names.append("dom")

    if ("net::" in el or "403" in error_msg or " 403" in error_msg) and "network" in skill_set:
        from l3_client.local_mcps.agentic_mesh.experts.network_sec import NetworkRecoveryExpert
        net_ex = NetworkRecoveryExpert(page)
        expert_coros.append(net_ex.attempt_recover(error_context=error_msg))
        expert_names.append("network")

    if not expert_coros:
        return False

    if len(expert_coros) == 1:
        name = expert_names[0]
        _phantom_log(f"[Phantom Mesh·Parallel] 单专家运行: [{name}]")
        result = await expert_coros[0]
        if result:
            bank.remember(error_msg[:512], expert=name, action="parallel_single", ok=True)
        return bool(result)

    _phantom_log(f"[Phantom Mesh·Parallel] 并行会诊 {len(expert_coros)} 位专家: {expert_names}")
    results = await asyncio.gather(*expert_coros, return_exceptions=True)
    any_healed = False
    for name, res in zip(expert_names, results):
        if isinstance(res, Exception):
            _phantom_log(f"[Phantom Mesh·Parallel] 专家 [{name}] 抛出异常: {res}")
            continue
        if res:
            _phantom_log(f"[Phantom Mesh·Parallel] 专家 [{name}] 自愈成功")
            bank.remember(error_msg[:512], expert=name, action="parallel_winner", ok=True)
            any_healed = True
    return any_healed


def with_phantom_guard(
    skills: list[str] | None = None,
    *,
    max_retries: int = 1,
    parallel_triage: bool = False,
):
    """
    Agent 时代的核心防御场。
    任何被此装饰器包裹的函数，一旦抛出异常，都会被拦截并交由幽灵大脑（Router）进行诊断和自愈。

    Parameters
    ----------
    skills:
        启用的专家技能列表，默认 ["dom", "network"]
    max_retries:
        最大重试次数（每次重试前由专家进行自愈尝试）
    parallel_triage:
        是否启用**并行多专家会诊**模式。
        True 时，所有匹配的专家同时运行（适合复合型错误）；
        False 时（默认），按错误特征串行匹配最相关专家（适合单一错误类型）。
    """
    skill_set = skills if skills is not None else ["dom", "network"]
    retries = max(0, int(max_retries))

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            page = _resolve_page(args, kwargs)
            bank = get_memory_bank()
            last_healed_error: str | None = None

            for attempt in range(retries + 1):
                try:
                    out = await func(*args, **kwargs)
                    if last_healed_error:
                        bank.remember(
                            last_healed_error,
                            expert="mesh",
                            action="post_expert_retry",
                            ok=True,
                        )
                    return out
                except BaseException as e:
                    if _should_pass_through(e):
                        raise
                    if not isinstance(e, Exception):
                        raise
                    if attempt >= retries or page is None:
                        _phantom_log(
                            f"[Phantom Mesh] 次数用尽或无 page，终止: {type(e).__name__}"
                        )
                        raise

                    error_msg = str(e)
                    first = error_msg.splitlines()[0] if error_msg else type(e).__name__
                    _phantom_log(f"[Phantom Mesh] 拦截异常: {first}")

                    # 优先复用记忆库中的成功策略，跳过重复专家调用
                    preferred = bank.prefer_action(error_msg)
                    if preferred:
                        _phantom_log(f"[Phantom Mesh] 记忆库命中策略 '{preferred}'，直接重试…")
                        last_healed_error = error_msg[:512]
                        continue

                    if parallel_triage:
                        _phantom_log("[Phantom Mesh] 触发并行多专家会诊（MoE·Parallel）…")
                        if logger.isEnabledFor(logging.DEBUG):
                            _phantom_log(traceback.format_exc())
                        healed = await _run_parallel_experts(page, error_msg, skill_set, bank)
                    else:
                        _phantom_log("[Phantom Mesh] 触发串行评估链（MoE·Sequential）…")
                        if logger.isEnabledFor(logging.DEBUG):
                            _phantom_log(traceback.format_exc())

                        healed = False
                        el = error_msg.lower()

                        if "timeout" in el or "locator" in el or "waiting for" in el:
                            if "dom" in skill_set:
                                from l3_client.local_mcps.agentic_mesh.experts.dom_healer import (
                                    DomHealer,
                                )

                                _phantom_log("[Phantom Mesh] 调度 [视觉愈合专家] …")
                                healer = DomHealer(page)
                                healed = await healer.attempt_heal(error_context=error_msg)

                        elif "net::" in el or "403" in error_msg or " 403" in error_msg:
                            if "network" in skill_set:
                                from l3_client.local_mcps.agentic_mesh.experts.network_sec import (
                                    NetworkRecoveryExpert,
                                )

                                _phantom_log("[Phantom Mesh] 调度 [网络破壁专家] …")
                                net_ex = NetworkRecoveryExpert(page)
                                healed = await net_ex.attempt_recover(error_context=error_msg)

                    if healed:
                        _phantom_log("[Phantom Mesh] 专家已执行修复动作，恢复时间流并重试 …")
                        last_healed_error = error_msg[:512]
                        continue

                    _phantom_log("[Phantom Mesh] 专家未达成可验证自愈，提交死亡报告（原异常）")
                    raise

        return wrapper

    return decorator
