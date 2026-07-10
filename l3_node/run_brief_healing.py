"""
run_agent ExecutionBrief 路径的 Level 3 轻量自愈（路线图 · 无人值守 P2）

在顶层 run 以 ``[ExecutionBrief]`` 结束时，用 Experience RAG 检索相似成功案例，
可选飞书通知，并将诊断摘要写入会话热注入队列供**下一轮** RoleExecutionAgent 合并理解。

环境变量
--------
JACHIN_LEVEL3_BRIEF_HEAL=1           开启（默认关）
JACHIN_LEVEL3_BRIEF_HEAL_INJECT=1    将诊断写入 session 热注入（默认开，需 BRIEF_HEAL）
JACHIN_LEVEL3_BRIEF_HEAL_NOTIFY=0    飞书推送（默认关，避免 Brief 刷屏）
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def brief_heal_enabled() -> bool:
    return (os.environ.get("JACHIN_LEVEL3_BRIEF_HEAL") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def brief_heal_inject_enabled() -> bool:
    if not brief_heal_enabled():
        return False
    v = (os.environ.get("JACHIN_LEVEL3_BRIEF_HEAL_INJECT") or "1").strip().lower()
    return v in ("1", "true", "yes", "on")


def brief_heal_notify_enabled() -> bool:
    return (os.environ.get("JACHIN_LEVEL3_BRIEF_HEAL_NOTIFY") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _build_inject_text(diagnosis: Any) -> str:
    tools = ", ".join(getattr(diagnosis, "suggested_tools", None) or [])[:200]
    action = str(getattr(diagnosis, "suggested_action", "") or "")[:800]
    lines = [
        "【Level3·ExecutionBrief 诊断】上一轮未完整交付，请参考以下经验继续（勿重复同参失败调用）：",
    ]
    if action:
        lines.append(action)
    if tools:
        lines.append(f"建议优先工具：{tools}")
    hits = getattr(diagnosis, "similar_successes", None) or []
    if hits:
        h0 = hits[0]
        lines.append(
            f"历史成功案例工具：{str(h0.get('executed_tool') or '')[:80]}"
        )
    return "\n".join(lines)[:2000]


def diagnose_run_execution_brief(
    user_intent: str,
    final_answer: str,
    *,
    tools_used: list[str] | None = None,
) -> Any | None:
    """同步诊断；无命中仍可能返回带 suggested_action 的 diagnosis。"""
    if not brief_heal_enabled():
        return None
    ui = (user_intent or "").strip()
    ans = (final_answer or "").strip()
    if not ui or "[ExecutionBrief]" not in ans:
        return None
    try:
        from l3_node.autonomy.level3_healer import HealingDiagnosis
        from l3_node.experience_memory import experience_rag_enabled, retrieve_experience
    except ImportError:
        return None
    if not experience_rag_enabled():
        return None

    err = ans[:500]
    if tools_used:
        err += f" | tools={','.join(list(tools_used)[:8])}"

    diagnosis = HealingDiagnosis(
        intent_id="run_agent:brief",
        intent_description=ui[:800],
        consecutive_failures=1,
        last_error=err,
    )
    try:
        top_k = max(1, min(8, int(os.environ.get("JACHIN_LEVEL3_RAG_TOP_K") or "3")))
        hits = retrieve_experience(ui, top_k=top_k)
        diagnosis.similar_successes = hits
    except Exception as e:
        logger.debug("[BriefHeal] RAG failed: %s", e)
        hits = []

    seen: list[str] = []
    for h in hits:
        t = str(h.get("executed_tool") or "")
        if t and t not in seen:
            seen.append(t)
    diagnosis.suggested_tools = seen[:5]
    if hits:
        diagnosis.suggested_action = (
            f"参考历史案例工具 {hits[0].get('executed_tool')!r}；"
            "调整参数或降级批量后重试；若仍失败应更新 ExecutionBrief 后停止扩张。"
        )
    else:
        diagnosis.suggested_action = (
            "Experience 无相似案例：缩小任务范围、换只读探查工具，或请用户补充约束。"
        )
    return diagnosis


async def apply_brief_healing_async(
    user_intent: str,
    final_answer: str,
    *,
    session_key: str = "",
    tools_used: list[str] | None = None,
) -> dict[str, Any]:
    """异步：诊断 + 可选注入 + 可选飞书。"""
    out: dict[str, Any] = {"applied": False}
    diag = diagnose_run_execution_brief(
        user_intent, final_answer, tools_used=tools_used
    )
    if diag is None:
        return out
    out["applied"] = True
    out["suggested_tools"] = list(diag.suggested_tools)
    sk = (session_key or "").strip()
    if sk and brief_heal_inject_enabled():
        try:
            from l3_node.session_hot_user_inject import record_pending_session_user_text

            record_pending_session_user_text(sk, _build_inject_text(diag))
            out["injected_session"] = sk
        except Exception as e:
            logger.debug("[BriefHeal] inject failed: %s", e)
    if brief_heal_notify_enabled():
        try:
            from l3_node.channels.lark.im import send_text_to_default_chat

            await send_text_to_default_chat(
                f"[Jachin·BriefHeal]\n{diag.format_report()[:1400]}"
            )
            out["notified"] = True
        except Exception as e:
            logger.debug("[BriefHeal] notify failed: %s", e)
    return out


def schedule_brief_healing_after_run(
    user_intent: str,
    final_answer: str,
    *,
    session_key: str = "",
    tools_used: list[str] | None = None,
) -> None:
    """fire-and-forget，不阻塞 run_agent 返回。"""
    if not brief_heal_enabled():
        return
    if "[ExecutionBrief]" not in (final_answer or ""):
        return

    async def _run() -> None:
        await apply_brief_healing_async(
            user_intent,
            final_answer,
            session_key=session_key,
            tools_used=tools_used,
        )

    try:
        asyncio.get_running_loop().create_task(_run(), name="brief_heal")
    except RuntimeError:
        pass
