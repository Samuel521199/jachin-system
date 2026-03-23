"""
L3 胶水 — 供 native 工具与 workflow 步骤调用的薄封装（避免循环 import）。
"""
from __future__ import annotations

from typing import Any

from l3_node.orchestration.domain_registry import run_domain


def dispatch_domain_workflow(
    domain_id: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return run_domain(domain_id, params)
