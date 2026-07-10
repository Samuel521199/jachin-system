"""SQLite/data grounding guards owned by the database capability layer."""

from __future__ import annotations

import logging
import re
from typing import Any

from l3_node.cognitive_kernel.capability_hook_bridge import build_work_order_suggestion
from l3_node.engine.hooks_pipeline import PipelineContext
from l3_node.primitives.tools.loader import tool_entry_looks_like_sqlite_family

logger = logging.getLogger(__name__)


def tools_include_sqlite_mcp(tools: list[dict[str, Any]] | None) -> bool:
    return any(tool_entry_looks_like_sqlite_family(t) for t in (tools or []))


def last_non_system_user_text(messages: list[dict[str, Any]], *, max_scan: int = 32) -> str:
    seen_user = 0
    for item in reversed(messages or []):
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        seen_user += 1
        if seen_user > max_scan:
            break
        text = str(item.get("content") or "").strip()
        if not text or "jachin-kernel:work-order-suggestion" in text:
            continue
        return text
    return ""


def user_text_requests_workspace_sqlite_verification(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    return bool(
        re.search(r"sqlite|\.db\b|\.sqlite\b|数据库|查库|查询表|库存|缺货|test_db", t, re.I)
        and re.search(r"workspace|工作区|本地|查询|查一下|核验|验证|读取|select|sql", t, re.I)
    )


def final_answer_is_honest_sqlite_capability_denial(text: str) -> bool:
    s = text or ""
    if len(s) < 12:
        return False
    return bool(re.search(r"无法|不能|没有.*工具|未注册|未授权|不可访问|缺少.*SQLite|缺少.*sqlite", s, re.I))


def final_answer_claims_sqlite_was_queried(text: str) -> bool:
    s = text or ""
    if final_answer_is_honest_sqlite_capability_denial(s):
        return False
    return bool(
        re.search(r"\.sqlite|\.db\b|数据库|SQL|sqlite", s, re.I)
        and re.search(r"查询结果|查询显示|实际查询|根据.*查询|表明|结果是|库存|缺货", s, re.I)
    )


def build_sqlite_has_tool_denial_prompt() -> str:
    return build_work_order_suggestion(
        tool="mcp:list_tables",
        work_order_input={},
        reason="sqlite_tool_available_but_denied",
        role_agent="FileExecutorAgent",
        visible_message="SQLite 工具已可用但回答声称不可查询；已生成 list_tables WorkOrder 建议。",
    )


def build_sqlite_requires_observation_prompt() -> str:
    return build_work_order_suggestion(
        tool="mcp:list_tables",
        work_order_input={},
        reason="sqlite_requires_observation",
        role_agent="FileExecutorAgent",
        visible_message="该问题需要数据库事实支撑；已生成 list_tables WorkOrder 建议，先获取真实表结构。",
    )


def build_sqlite_fake_query_prompt(has_sqlite: bool) -> str:
    if has_sqlite:
        return build_work_order_suggestion(
            tool="mcp:list_tables",
            work_order_input={},
            reason="sqlite_fake_query_claim",
            role_agent="FileExecutorAgent",
            visible_message="检测到未查询却声称已有数据库结果；已生成 list_tables WorkOrder 建议重新接地。",
        )
    return (
        "当前可见能力中没有 SQLite 查询工具，无法真实查询数据库；请安装或启用 SQLite MCP 后再执行。"
    )


def reject_ungrounded_sqlite_final_answer(
    ctx: PipelineContext,
    messages: list[dict[str, Any]],
    response: str,
    ans: str,
    *,
    via: str,
) -> bool:
    """Reject final answers that skipped SQLite tools for DB-grounded questions."""

    skills = ctx.metadata.get("_skills_unfiltered") or ctx.metadata.get("_skills") or []
    has_sqlite = tools_include_sqlite_mcp(skills)
    anchor = last_non_system_user_text(messages)
    if not user_text_requests_workspace_sqlite_verification(f"{ctx.intent or ''}\n{anchor}"):
        return False

    invoked = int(ctx.metadata.get("_work_order_tool_invocations") or 0)
    answer = str(ans or "")

    if invoked < 1 and has_sqlite and final_answer_is_honest_sqlite_capability_denial(answer):
        logger.warning("[CapabilityHook][sqlite_grounding] via=%s denied mounted sqlite; WorkOrder suggestion injected", via)
        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": build_sqlite_has_tool_denial_prompt()})
        return True

    if invoked < 1 and has_sqlite and not final_answer_is_honest_sqlite_capability_denial(answer):
        logger.warning("[CapabilityHook][sqlite_grounding] via=%s answered sqlite question before observation", via)
        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": build_sqlite_requires_observation_prompt()})
        return True

    if invoked < 1 and final_answer_claims_sqlite_was_queried(answer):
        logger.warning("[CapabilityHook][sqlite_grounding] via=%s fake sqlite query claim has_sqlite=%s", via, has_sqlite)
        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": build_sqlite_fake_query_prompt(has_sqlite)})
        return True

    return False
