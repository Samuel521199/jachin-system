"""Context path 账本：path_key × 最近 ReAct 轮次，供 prefetch 滑窗拦截。"""
from __future__ import annotations

from typing import Any


def _ledger(meta: dict[str, Any]) -> dict[str, int]:
    raw = meta.get("_context_path_ledger")
    if not isinstance(raw, dict):
        raw = {}
        meta["_context_path_ledger"] = raw
    return raw  # path_key -> last_seen_react_iteration


def should_block_prefetch_path(
    meta: dict[str, Any],
    path_key: str,
    current_react_iteration: int,
    iteration_window: int,
) -> bool:
    """
    同 iteration 内重复路径 → 拦截。
    若 last_seen 与 current 差值 <= iteration_window → 仍拦截（远轮后才允许再 prefetch）。
    iteration_window=0 时仅同轮去重。
    """
    if not path_key:
        return False
    if current_react_iteration <= 0:
        return False
    led = _ledger(meta)
    last = led.get(path_key)
    if last is None:
        return False
    try:
        li = int(last)
        cur = int(current_react_iteration)
    except (TypeError, ValueError):
        return False
    if li == cur:
        return True
    return (cur - li) <= int(iteration_window)


def touch_prefetch_path_iteration(meta: dict[str, Any], path_key: str, current_react_iteration: int) -> None:
    if not path_key or current_react_iteration <= 0:
        return
    led = _ledger(meta)
    led[path_key] = int(current_react_iteration)


def touch_tool_read_path_iteration(meta: dict[str, Any], path_key: str, current_react_iteration: int) -> None:
    touch_prefetch_path_iteration(meta, path_key, current_react_iteration)
