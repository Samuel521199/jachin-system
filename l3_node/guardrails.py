"""
Guardrails — RoleExecutionAgent 主循环安全护栏（AN）

在 run_agent RoleExecutionAgent 主循环的每一步入口检查多维度上限，防止失控迭代、工具滥用、
Token 超支。遇到违规时返回 GuardrailsViolation，调用方可按 action 执行：
    warn      — 将警告注入下一条 user 消息，继续运行
    truncate  — 强制结束当前 RoleExecutionAgent 轮次，返回 ExecutionBrief
    abort     — 立即停止，抛出 GuardrailsAbortError

环境变量（所有默认关闭 / 宽松值）
--------------------------------------
JACHIN_GUARDRAILS_ENABLE=1             开启 Guardrails（默认关）
JACHIN_GR_MAX_ITERATIONS=20            单次 run_agent 最大 RoleExecutionAgent 轮次（默认 20）
JACHIN_GR_MAX_TOOL_CALLS=40            单次 run_agent 最大工具调用次数（默认 40）
JACHIN_GR_MAX_TOKENS=200000            单次 run_agent 最大 token 消耗（默认 200000）
JACHIN_GR_FORBIDDEN_TOOLS             逗号分隔，禁止调用的工具 id 前缀（默认空）
JACHIN_GR_REPEAT_TOOL_ACTION_MAX=3    同一工具+同参数最多重复调用次数（默认 3）
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置读取
# ---------------------------------------------------------------------------

def guardrails_enabled() -> bool:
    return (os.environ.get("JACHIN_GUARDRAILS_ENABLE") or "").strip().lower() in (
        "1", "true", "yes"
    )


def _cfg_int(key: str, default: int) -> int:
    raw = (os.environ.get(key) or "").strip()
    try:
        return max(1, int(raw)) if raw else default
    except ValueError:
        return default


def _cfg_max_iterations() -> int:
    return _cfg_int("JACHIN_GR_MAX_ITERATIONS", 20)


def _cfg_max_tool_calls() -> int:
    return _cfg_int("JACHIN_GR_MAX_TOOL_CALLS", 40)


def _cfg_max_tokens() -> int:
    return _cfg_int("JACHIN_GR_MAX_TOKENS", 200_000)


def _cfg_repeat_tool_max() -> int:
    return _cfg_int("JACHIN_GR_REPEAT_TOOL_ACTION_MAX", 3)


def _cfg_forbidden_tools() -> list[str]:
    raw = (os.environ.get("JACHIN_GR_FORBIDDEN_TOOLS") or "").strip()
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class GuardrailsViolation:
    rule: str
    action: Literal["warn", "truncate", "abort"]
    message: str
    context: dict[str, Any] = field(default_factory=dict)


class GuardrailsAbortError(RuntimeError):
    """action=abort 时抛出，强制停止 run_agent。"""
    def __init__(self, violation: GuardrailsViolation) -> None:
        super().__init__(violation.message)
        self.violation = violation


# ---------------------------------------------------------------------------
# 运行时状态（每个 run_agent 实例独立）
# ---------------------------------------------------------------------------

@dataclass
class GuardrailsState:
    iterations: int = 0
    tool_calls: int = 0
    tokens_used: int = 0
    # 工具调用指纹 → 调用次数（用于重复调用检测）
    tool_call_fingerprints: dict[str, int] = field(default_factory=dict)

    def record_tool_call(self, tool_id: str, work_order_input: str) -> None:
        self.tool_calls += 1
        fp = _fingerprint(tool_id, work_order_input)
        self.tool_call_fingerprints[fp] = self.tool_call_fingerprints.get(fp, 0) + 1

    def tool_call_repeat_count(self, tool_id: str, work_order_input: str) -> int:
        return self.tool_call_fingerprints.get(_fingerprint(tool_id, work_order_input), 0)


def _fingerprint(tool_id: str, work_order_input: str) -> str:
    """生成工具调用指纹（工具 id + 参数的 hash，容忍空白差异）。"""
    normalized = json.dumps(
        {"t": tool_id, "a": work_order_input.strip()},
        ensure_ascii=False, sort_keys=True
    )
    return hashlib.md5(normalized.encode()).hexdigest()


# ---------------------------------------------------------------------------
# 检查器
# ---------------------------------------------------------------------------

class GuardrailsChecker:
    """
    在 run_agent RoleExecutionAgent 循环中调用 check_*，若返回 GuardrailsViolation，
    调用方按 violation.action 决定 warn / truncate / abort。
    """

    def __init__(self, state: GuardrailsState | None = None) -> None:
        self._state = state or GuardrailsState()
        self._max_iter = _cfg_max_iterations()
        self._max_tc = _cfg_max_tool_calls()
        self._max_tok = _cfg_max_tokens()
        self._repeat_max = _cfg_repeat_tool_max()
        self._forbidden = _cfg_forbidden_tools()

    @property
    def state(self) -> GuardrailsState:
        return self._state

    def record_iteration(self) -> None:
        self._state.iterations += 1

    def record_tokens(self, delta: int) -> None:
        self._state.tokens_used += max(0, delta)

    def check_iteration_limit(self) -> GuardrailsViolation | None:
        """检查 RoleExecutionAgent 迭代次数。"""
        if self._state.iterations >= self._max_iter:
            return GuardrailsViolation(
                rule="max_iterations",
                action="truncate",
                message=(
                    f"[Guardrails] 迭代次数达到上限 {self._max_iter}，"
                    "强制结束本轮 RoleExecutionAgent，产出 ExecutionBrief。"
                ),
                context={"iterations": self._state.iterations, "limit": self._max_iter},
            )
        return None

    def check_tool_call(self, tool_id: str, work_order_input: str) -> GuardrailsViolation | None:
        """
        在执行工具前调用：
        1. 检查工具调用总次数。
        2. 检查是否被 JACHIN_GR_FORBIDDEN_TOOLS 禁止。
        3. 检查同工具同参数的重复调用次数。
        """
        # 禁止工具
        for prefix in self._forbidden:
            if tool_id.startswith(prefix):
                return GuardrailsViolation(
                    rule="forbidden_tool",
                    action="abort",
                    message=f"[Guardrails] 工具 {tool_id!r} 在禁止列表中（前缀 {prefix!r}），立即停止。",
                    context={"tool_id": tool_id, "forbidden_prefix": prefix},
                )

        # 工具总调用次数
        if self._state.tool_calls >= self._max_tc:
            return GuardrailsViolation(
                rule="max_tool_calls",
                action="truncate",
                message=(
                    f"[Guardrails] 工具调用次数达到上限 {self._max_tc}，"
                    "强制结束本轮 RoleExecutionAgent。"
                ),
                context={"tool_calls": self._state.tool_calls, "limit": self._max_tc},
            )

        # 重复调用
        repeat = self._state.tool_call_repeat_count(tool_id, work_order_input)
        if repeat >= self._repeat_max:
            return GuardrailsViolation(
                rule="repeat_tool_action",
                action="warn",
                message=(
                    f"[Guardrails] 工具 {tool_id!r} 以相同参数已调用 {repeat} 次，"
                    f"达到上限 {self._repeat_max}。请换策略或换参数，否则下一次将强制截断。"
                ),
                context={
                    "tool_id": tool_id,
                    "repeat_count": repeat,
                    "limit": self._repeat_max,
                    "work_order_input_preview": work_order_input[:200],
                },
            )
        # 记录本次调用
        self._state.record_tool_call(tool_id, work_order_input)
        return None

    def check_token_budget(self) -> GuardrailsViolation | None:
        """检查 token 消耗预算。"""
        if self._state.tokens_used >= self._max_tok:
            return GuardrailsViolation(
                rule="max_tokens",
                action="truncate",
                message=(
                    f"[Guardrails] 本轮 token 消耗 {self._state.tokens_used} "
                    f"达到上限 {self._max_tok}，强制结束。"
                ),
                context={"tokens_used": self._state.tokens_used, "limit": self._max_tok},
            )
        return None

    def check_all_pre_tool(self, tool_id: str, work_order_input: str) -> GuardrailsViolation | None:
        """工具执行前的聚合检查（顺序：forbidden → total_calls → repeat）。"""
        if not guardrails_enabled():
            return None
        return self.check_tool_call(tool_id, work_order_input)

    def check_all_pre_iteration(self) -> GuardrailsViolation | None:
        """每轮 RoleExecutionAgent 迭代开始时的聚合检查（迭代次数 + token）。"""
        if not guardrails_enabled():
            return None
        v = self.check_iteration_limit()
        if v:
            return v
        return self.check_token_budget()

    def execution_brief(self) -> str:
        """生成 ExecutionBrief 摘要，供 truncate 时注入 User-facing result。"""
        return (
            f"[ExecutionBrief·Guardrails] 本轮执行摘要：\n"
            f"- RoleExecutionAgent 迭代次数：{self._state.iterations}（上限 {self._max_iter}）\n"
            f"- 工具调用次数：{self._state.tool_calls}（上限 {self._max_tc}）\n"
            f"- Token 消耗估算：{self._state.tokens_used}（上限 {self._max_tok}）\n"
            f"- 建议：检查意图是否过于复杂，或分拆为多个子任务。"
        )


async def emit_guardrails_execution_brief(
    ctx: Any,
    *,
    rule: str,
    brief_body: str,
    violation: "GuardrailsViolation | None" = None,
) -> str:
    """
    Guardrails truncate/abort 统一打 HOOK_ON_EXECUTION_BRIEF 并返回 User-facing result 行。
    brief_body 为 execution_brief() 正文（不含 User-facing result 前缀）。
    """
    from l3_node.engine.hooks_pipeline import HOOK_ON_EXECUTION_BRIEF, global_hooks

    reason = f"guardrails:{rule}"
    if violation is not None:
        try:
            ctx.metadata["_guardrails_violation"] = {
                "rule": violation.rule,
                "action": violation.action,
                "message": (violation.message or "")[:500],
            }
        except Exception:
            pass
    try:
        ctx.metadata["_execution_brief_reason"] = reason
    except Exception:
        pass
    final_line = f"User-facing result: {brief_body}"
    try:
        ctx.final_answer = brief_body
    except Exception:
        pass
    try:
        await global_hooks.run(HOOK_ON_EXECUTION_BRIEF, ctx)
    except Exception as e:
        logger.debug("[Guardrails] HOOK_ON_EXECUTION_BRIEF failed: %s", e)
    return final_line


async def emit_guardrails_abort_brief(ctx: Any, violation: "GuardrailsViolation") -> None:
    """abort 路径：落盘 Brief Hook 后再由调用方 raise GuardrailsAbortError。"""
    brief = (
        f"[ExecutionBrief·Guardrails·Abort] {violation.message}\n"
        f"规则：{violation.rule}，动作：{violation.action}。"
    )
    await emit_guardrails_execution_brief(
        ctx,
        rule=violation.rule,
        brief_body=brief,
        violation=violation,
    )
