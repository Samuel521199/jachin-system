"""LLM Token 预算（主/子 Agent，usage 累计）。见 docs/L3_LIMITATIONS_AND_REMEDIATION_ROADMAP.md §〇、§2。"""
from __future__ import annotations


class BudgetExhaustedError(Exception):
    """累计 token 超过硬顶。"""

    def __init__(self, used: int, limit: int, *, message: str = "") -> None:
        self.used = used
        self.limit = limit
        msg = message or f"Token 预算已用尽（累计 {used} > 上限 {limit}）。"
        super().__init__(msg)


def extract_usage_tokens(response: object) -> tuple[int, int]:
    """从 litellm 响应对象取 (prompt_tokens, completion_tokens)。"""
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0
    pt = getattr(usage, "prompt_tokens", None)
    if pt is None:
        pt = getattr(usage, "input_tokens", None)
    ct = getattr(usage, "completion_tokens", None)
    if ct is None:
        ct = getattr(usage, "output_tokens", None)
    try:
        pi = int(pt or 0)
    except (TypeError, ValueError):
        pi = 0
    try:
        ci = int(ct or 0)
    except (TypeError, ValueError):
        ci = 0
    return pi, ci


def accumulate_and_check(
    accumulator: dict[str, int],
    prompt_delta: int,
    completion_delta: int,
    max_total: int | None,
) -> None:
    accumulator["prompt"] = int(accumulator.get("prompt", 0)) + int(prompt_delta)
    accumulator["completion"] = int(accumulator.get("completion", 0)) + int(completion_delta)
    tot = accumulator["prompt"] + accumulator["completion"]
    if max_total is not None and tot > int(max_total):
        raise BudgetExhaustedError(tot, int(max_total))
