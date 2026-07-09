"""SQLite Action Critic pre-execution hook."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from l3_node.engine.hooks_pipeline import PipelineContext
from l3_node.exec_trace import exec_trace
from l3_node.primitives.tools.loader import tool_entry_looks_like_sqlite_family

logger = logging.getLogger(__name__)


def mark_sqlite_experience_save_gate(ctx: PipelineContext, tool: str) -> None:
    """Allow later experience logging for SQLite read/write tools when critic is off."""

    if not tool_entry_looks_like_sqlite_family({"id": tool}):
        return
    try:
        from l3_node.experience_memory import tool_id_is_sqlite_read_or_write

        if tool_id_is_sqlite_read_or_write(tool):
            try:
                from l3_node.critic_agent import action_critic_enabled

                if not action_critic_enabled():
                    ctx.metadata["_l4_exp_save_gate"] = True
            except Exception:
                ctx.metadata["_l4_exp_save_gate"] = True
    except Exception:
        pass


async def maybe_reject_sqlite_action(
    *,
    ctx: PipelineContext,
    tool: str,
    inp: str,
    response: str,
    messages: list[dict[str, Any]],
    observation_excerpt_fn: Callable[[list[dict[str, Any]] | None], str],
    followup_user_text_fn: Callable[[str, str], str],
) -> bool:
    """Run Action Critic for SQLite tools and inject a corrective Observation on failure."""

    if not tool_entry_looks_like_sqlite_family({"id": tool}):
        return False
    try:
        from l3_node.critic_agent import (
            action_critic_enabled,
            action_critic_max_fails,
            evaluate_action,
        )

        if not action_critic_enabled():
            return False

        sem_crit: dict[str, Any] = {}
        gateway_bundle = ctx.metadata.get("_gateway_bundle")
        if gateway_bundle is not None:
            sx = getattr(gateway_bundle, "extra", {}).get("semantic_layer")
            if isinstance(sx, dict):
                sem_crit = sx

        proposed_action = {
            "tool_id": tool,
            "action_input": (inp or "")[:12000],
            "assistant_react_excerpt": (response or "")[:8000],
        }
        user_intent = (ctx.intent or "").strip()
        if not user_intent:
            for msg in reversed(messages or []):
                if isinstance(msg, dict) and msg.get("role") == "user":
                    user_intent = str(msg.get("content") or "").strip()[:4000]
                    break

        on_step = ctx.metadata.get("_on_step")
        if on_step:
            try:
                on_step(
                    "system_status",
                    json.dumps({"status": "🛡️ Critic 审查中…"}, ensure_ascii=False),
                    ctx.run_id,
                )
            except Exception:
                pass

        obs_for_critic = observation_excerpt_fn(messages)
        ok, critique = await evaluate_action(
            user_intent,
            proposed_action,
            sem_crit,
            react_observation_excerpt=obs_for_critic,
        )
        if ok:
            if on_step:
                try:
                    on_step(
                        "system_status",
                        json.dumps({"status": "✅ 审查通过，即将执行"}, ensure_ascii=False),
                        ctx.run_id,
                    )
                except Exception:
                    pass
            ctx.metadata["_l4_critic_reject_streak"] = 0
            try:
                from l3_node.experience_memory import tool_id_is_sqlite_read_or_write

                if tool_id_is_sqlite_read_or_write(tool):
                    ctx.metadata["_l4_exp_save_gate"] = True
            except Exception:
                pass
            return False

        if on_step:
            try:
                on_step(
                    "system_status",
                    json.dumps({"status": "❌ Critic 未通过，已打回重做"}, ensure_ascii=False),
                    ctx.run_id,
                )
            except Exception:
                pass

        max_fails = action_critic_max_fails()
        streak = int(ctx.metadata.get("_l4_critic_reject_streak") or 0) + 1
        ctx.metadata["_l4_critic_reject_streak"] = streak
        logger.info(
            "[CapabilityHook][sqlite_action_critic] block tool=%s streak=%d/%d critique_preview=%r",
            tool,
            streak,
            max_fails,
            (critique or "")[:240],
        )
        exec_trace(
            logger,
            "ActionCritic block streak=%s/%s tool=%s",
            streak,
            max_fails,
            (tool or "")[:80],
        )
        if streak >= max_fails:
            body = (
                f"[System Critic Error] 已连续 {max_fails} 次未通过逻辑审查！警报！\n"
                "绝对禁止输出 Final Answer 放弃任务！绝对禁止把任务推给统帅！\n"
                "现在，你必须立刻、马上输出一个合法的只读 Action（如 mcp:query 配合 SELECT，或 mcp:read_records / read_query / list_tables），"
                "去获取必要的数据 Observation。只有拿到数据后，再在下一步执行修改！立刻重试！\n"
                f"（上一轮审查意见供你修正：{critique}）"
            )
        else:
            body = (
                f"[System Critic Error] 你的 Action 未通过逻辑审查：{critique} "
                "请严格按 L4 SOP：<probe> 探查 Schema，<map> 结合业务语义层，<execute> 使用实际工具："
                "只读可用 mcp:query(SELECT)、mcp:read_records、list_tables；"
                "写入可用 mcp:update_records、write_query 或 mcp:query(UPDATE)；同一对话内连续执行，勿 Final Answer 中断。"
            )
        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": followup_user_text_fn(body, str(tool or ""))})
        return True
    except Exception as exc:
        logger.debug("[CapabilityHook][sqlite_action_critic] skipped: %s", exc)
        try:
            from l3_node.experience_memory import tool_id_is_sqlite_read_or_write

            if tool_id_is_sqlite_read_or_write(tool):
                ctx.metadata["_l4_exp_save_gate"] = True
        except Exception:
            pass
        return False
