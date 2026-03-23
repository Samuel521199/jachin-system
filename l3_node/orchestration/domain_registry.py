"""
L2 — 领域子图注册表：domain_id → runner(params) -> dict。

插件 / 其它包可调用 register_domain() 挂载新领域，无需改 YAML 引擎核心。
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from core.intelligence_workspace import emit_intelligence_event

logger = logging.getLogger(__name__)

DomainRunner = Callable[[dict[str, Any] | None], dict[str, Any]]

_DOMAINS: dict[str, DomainRunner] = {}
_builtins_registered = False


def register_domain(domain_id: str, runner: DomainRunner) -> None:
    key = (domain_id or "").strip().lower()
    if not key:
        raise ValueError("domain_id 不能为空")
    _DOMAINS[key] = runner
    logger.info("[Orchestration L2] 已注册领域: %s", key)


def list_domains() -> list[str]:
    _ensure_builtin_domains()
    return sorted(_DOMAINS.keys())


def run_domain(domain_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    执行已注册领域子图。

    params 结构由各领域自行约定；HR 常见字段：workflow_id, include_analyze, context。
    """
    _ensure_builtin_domains()
    key = (domain_id or "").strip().lower()
    p_in = params if isinstance(params, dict) else {}
    if not key:
        return {"ok": False, "error": "缺少 domain_id", "layer": 2}
    fn = _DOMAINS.get(key)
    if not fn:
        return {
            "ok": False,
            "error": f"未知领域: {domain_id!r}，已注册: {list_domains()}",
            "layer": 2,
        }
    try:
        out = fn(p_in)
        if not isinstance(out, dict):
            out = {"ok": False, "error": "领域 runner 须返回 dict", "raw": str(out)[:500]}
        else:
            out.setdefault("layer", 2)
        emit_intelligence_event(
            "domain_workflow_run",
            {"domain": key, "ok": bool(out.get("ok"))},
        )
        return out
    except Exception as e:
        logger.exception("[Orchestration L2] 领域 %s 执行失败", key)
        emit_intelligence_event("domain_workflow_failed", {"domain": key, "error": str(e)})
        return {"ok": False, "domain": key, "error": str(e), "layer": 2}


def _ensure_builtin_domains() -> None:
    global _builtins_registered
    if _builtins_registered:
        return
    try:
        from l3_node.orchestration.domain_hr import run_hr_recruitment_domain

        register_domain("hr_recruitment", run_hr_recruitment_domain)
    except Exception as e:
        logger.debug("[Orchestration L2] 内置 HR 注册跳过: %s", e)
    _builtins_registered = True
