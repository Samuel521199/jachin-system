"""
Planning Gate：composite 阶段工具白名单、Needs_Info 网关、task_plan 静态 linter 放行。
"""
from __future__ import annotations

import re
from typing import Any

from l3_node.intent_gateway.intent_tracker import emit_intent_tracker_event

_NEEDS_INFO_RE = re.compile(r"\[Needs_Info:\s*([^\]]+)\]", re.IGNORECASE)

# 规划锁定期内仅允许「读计划 / 写 task_plan / 本地记忆检索」类原子工具
_PLANNING_NATIVE_ALLOW = frozenset(
    {
        "core:fs_read",
        "core:fs_write",
        "core:local_memory_search",
        "core:local_memory_append",
    }
)


def extract_needs_info(text: str) -> str | None:
    m = _NEEDS_INFO_RE.search(text or "")
    if not m:
        return None
    s = (m.group(1) or "").strip()
    return s or None


def _planning_gate_config_on() -> bool:
    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config

        return bool(get_intent_gateway_config().get("planning_composite_gate_enabled", False))
    except Exception:
        return False


def is_composite_planning_locked(ctx: Any) -> bool:
    if not _planning_gate_config_on():
        return False
    gb = ctx.metadata.get("_gateway_bundle") if hasattr(ctx, "metadata") else None
    if gb is None:
        return False
    ex = getattr(gb, "extra", None) or {}
    if ex.get("execution_tier") != "composite":
        return False
    if ex.get("planning_composite_released"):
        return False
    return True


def filter_skills_for_planning_composite(skills: list[dict[str, Any]], ctx: Any) -> list[dict[str, Any]]:
    if not is_composite_planning_locked(ctx):
        return skills
    out: list[dict[str, Any]] = []
    for s in skills or []:
        tid = str(s.get("id") or "").strip().lower()
        if tid in _PLANNING_NATIVE_ALLOW:
            out.append(s)
    emit_intent_tracker_event(
        "planning_gate_skills_filtered",
        {"allowed_count": len(out), "total": len(skills or [])},
    )
    return out if out else skills


def planning_composite_gate_blocks_action(parsed: dict[str, Any] | None, ctx: Any) -> bool:
    """True → 拦截该 WorkOrder，要求先完成 task_plan + linter 放行。"""
    if parsed is None or not is_composite_planning_locked(ctx):
        return False
    ptype = parsed.get("type")
    if ptype == "answer":
        return True
    if ptype in ("delegate", "coordinate"):
        return True
    if ptype == "recall":
        return False
    if ptype == "native":
        tool = str(parsed.get("tool") or "").strip().lower()
        inp = str(parsed.get("input") or "")
        if tool == "core:fs_write":
            try:
                from l3_node.task_plan_policy import fs_write_targets_workspace_task_plan

                if fs_write_targets_workspace_task_plan(tool, inp):
                    return False
            except Exception:
                pass
            return True
        if tool in _PLANNING_NATIVE_ALLOW:
            return False
        return True
    return False


def try_release_planning_composite_after_task_plan_write(ctx: Any) -> str | None:
    """
    在 task_plan.md 写入后调用：跑静态 linter；失败返回需注入 user 的反馈文案；成功则释放 composite 锁。
    """
    gb = ctx.metadata.get("_gateway_bundle") if hasattr(ctx, "metadata") else None
    if gb is None:
        return None
    ex = getattr(gb, "extra", None) or {}
    if ex.get("execution_tier") != "composite" or ex.get("planning_composite_released"):
        return None
    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config
        from l3_node.intent_gateway.plan_static_linter import lint_plan_against_allowlist
        from l3_node.task_planning import read_task_plan, task_plan_is_substantial

        if not task_plan_is_substantial():
            return None
        body = read_task_plan()
        allow = [
            str(s.get("id") or "").strip()
            for s in (ctx.metadata.get("_skills_unfiltered") or [])
            if isinstance(s, dict) and (s.get("id") or "").strip()
        ]
        errs = lint_plan_against_allowlist(body, allow)
        if not errs:
            gb.extra["planning_composite_released"] = True
            emit_intent_tracker_event("planning_gate_released", {"linter": "ok"})
            return None
        retries = int(ctx.metadata.get("_planning_linter_retries") or 0)
        try:
            max_r = int(get_intent_gateway_config().get("planning_static_linter_max_retries", 2))
        except (TypeError, ValueError):
            max_r = 2
        max_r = max(0, min(max_r, 10))
        emit_intent_tracker_event(
            "planning_gate_linter_fail",
            {"retry": retries, "max": max_r, "errs_head": (errs[0][:200] if errs else "")},
        )
        if retries >= max_r:
            gb.extra["planning_composite_released"] = True
            gb.extra["planning_linter_forced_release"] = True
            emit_intent_tracker_event("planning_gate_released", {"linter": "forced_after_max_retries"})
            return None
        ctx.metadata["_planning_linter_retries"] = retries + 1
        return "【规划静态扫描】" + "\n".join(errs)
    except Exception:
        return None
