"""
规划静态扫描器：从 task_plan.md（或任意计划文本）中提取工具 id，与允许列表比对，防「假借条」工具幻觉。
规格见 docs/L3_AMBIGUOUS_INTENT_ARCHITECTURE.md §9.2。
"""
from __future__ import annotations

import re
from typing import Iterable, List, Set

# mcp:foo.bar、core:shell_exec、jpp:pkg.plugin 等
_TOOL_ID_RE = re.compile(
    r"\b((?:mcp|core|jpp):[\w][\w._-]{0,127})\b",
    re.IGNORECASE,
)


def extract_tool_mentions(plan_text: str) -> Set[str]:
    t = plan_text or ""
    found = {m.group(1).strip() for m in _TOOL_ID_RE.finditer(t)}
    return {x for x in found if x}


def lint_plan_against_allowlist(
    plan_text: str,
    allowed_tool_ids: Iterable[str] | None,
    *,
    case_insensitive: bool = True,
) -> List[str]:
    """
    返回人类可读错误列表；空列表表示未发现「不在白名单内的工具提及」。
    allowlist 为 None 或空：跳过校验（开发态），不报错。
    """
    if not allowed_tool_ids:
        return []
    mentions = extract_tool_mentions(plan_text)
    if not mentions:
        return []

    if case_insensitive:
        allow = {x.strip().casefold() for x in allowed_tool_ids if str(x).strip()}
        bad = [m for m in mentions if m.casefold() not in allow]
    else:
        allow = frozenset(str(x).strip() for x in allowed_tool_ids if str(x).strip())
        bad = [m for m in mentions if m not in allow]

    if not bad:
        return []
    return [
        "计划中提及以下工具 id，但不在当前节点可用白名单内（可能是幻觉或越权）："
        + ", ".join(sorted(set(bad)))
        + "。请重写计划，仅使用已声明工具，或输出 [Needs_Info: …] 说明无法执行的原因。"
    ]
