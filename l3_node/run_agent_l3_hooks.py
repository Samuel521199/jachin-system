"""
单体 L3 run_agent 增强钩子（路线图 §〇 未实现项 · 单机优先）

- DAG 自动规划（JACHIN_DAG_AUTO_PLAN=1）
- DAG 续跑意图前缀（JACHIN_DAG_RESUME_HINT=1）
- task_plan.md 启动时回写 active.json（JACHIN_TASK_PLAN_DAG_SYNC=1）
- GlobalTaskRegistry 注册 / resource_tags 抢占（JACHIN_GLOBAL_REGISTRY_ENABLE=1）
- 定时任务入口先登记 P2（scheduled_global_registry.py，随 GLOBAL_REGISTRY 默认开）
- Experience 回合成功/ExecutionBrief 自动沉淀（JACHIN_EXPERIENCE_AUTO_RECORD / JACHIN_EXPERIENCE_AUTO_RECORD_FAIL）
- Level3 ExecutionBrief 诊断注入（JACHIN_LEVEL3_BRIEF_HEAL=1）
- Redis 全局任务 TTL 续期（JACHIN_GLOBAL_REGISTRY_REDIS_TOUCH=1）
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def maybe_prepend_dag_resume_hint(user_input: str, *, delegate_depth: int = 0) -> str:
    """若 active.json 有待办节点，为 run 前缀续跑提示（不阻塞）。"""
    if delegate_depth != 0:
        return user_input
    if (os.environ.get("JACHIN_DAG_RESUME_HINT") or "").strip().lower() not in (
        "1", "true", "yes",
    ):
        return user_input
    intent = (user_input or "").strip()
    if not intent or intent == "/clear":
        return user_input
    try:
        from l3_node.task_engine.dag_resume import build_resume_intent_from_active_dag
    except ImportError:
        return user_input
    hint = (build_resume_intent_from_active_dag() or "").strip()
    if not hint:
        return user_input
    if hint[:40] in intent:
        return user_input
    return hint + "\n\n" + intent


def sync_task_plan_on_run_start() -> None:
    try:
        from l3_node.task_engine.task_plan_dag_bridge import mirror_task_plan_md_to_active_json

        mirror_task_plan_md_to_active_json()
    except Exception:
        pass


def schedule_dag_auto_plan(user_input: str, *, run_id: str, delegate_depth: int = 0) -> None:
    """复杂意图时后台触发 TaskDAG Planner（不阻塞主 RoleExecutionAgent）。"""
    if delegate_depth != 0:
        return
    intent = (user_input or "").strip()
    if not intent or intent == "/clear":
        return
    try:
        from l3_node.task_engine.dag_planner import auto_plan_enabled, should_auto_plan
    except ImportError:
        return
    if not auto_plan_enabled() or not should_auto_plan(intent):
        return

    async def _run() -> None:
        try:
            from l3_node.task_engine.dag_planner import plan_task_dag

            result = await plan_task_dag(intent)
            if result.ok:
                logger.info(
                    "[L3Hooks] dag auto plan ok run_id=%s nodes=%d written=%s",
                    (run_id or "")[:12],
                    len(result.nodes),
                    result.written_to,
                )
            else:
                logger.debug(
                    "[L3Hooks] dag auto plan skipped run_id=%s err=%s",
                    (run_id or "")[:12],
                    result.error,
                )
        except Exception as e:
            logger.debug("[L3Hooks] dag auto plan failed: %s", e)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run(), name=f"dag_auto_plan_{(run_id or '')[:8]}")
    except RuntimeError:
        pass


def register_global_foreground_task(
    run_id: str,
    *,
    channel: str,
    session_key: str,
    resource_tags: list[str] | None,
    implicit_attribution: dict[str, Any] | None,
) -> None:
    """进程内 task_runtime_registry + 可选 GlobalTaskRegistry SSOT / 抢占。"""
    try:
        from l3_node.task_runtime_registry import register_foreground_task

        _rtags = resource_tags
        if not _rtags and implicit_attribution and isinstance(implicit_attribution, dict):
            raw = implicit_attribution.get("resource_tags")
            if isinstance(raw, list):
                _rtags = [str(x).strip()[:64] for x in raw if str(x).strip()][:8]
            elif raw is not None and str(raw).strip():
                _rtags = [str(raw).strip()[:64]]
        if not _rtags:
            _c = (channel or "unknown").strip()[:48] or "unknown"
            _rtags = [f"channel:{_c}"]
        register_foreground_task(
            run_id=run_id,
            channel=channel or "unknown",
            session_key=session_key,
            resource_tags=_rtags,
        )
        resource_tags = _rtags
    except Exception:
        logger.debug("[L3Hooks] task_runtime_registry.register 跳过", exc_info=True)
        return

    try:
        from l3_node.global_task_registry import (
            TaskPriority,
            check_and_preempt,
            global_registry_enabled,
            register_task,
        )
    except ImportError:
        return
    if not global_registry_enabled():
        return

    if isinstance(implicit_attribution, dict) and implicit_attribution.get(
        "_scheduled_global_registry_active"
    ):
        logger.debug(
            "[L3Hooks] skip inner global register (scheduled outer run_id=%s)",
            str(implicit_attribution.get("_scheduled_parent_run_id") or "")[:20],
        )
        return

    prio: TaskPriority = "P1"
    if implicit_attribution and isinstance(implicit_attribution, dict):
        raw_p = str(implicit_attribution.get("task_priority") or "").strip().upper()
        if raw_p in ("P1", "P2", "P3", "P4"):
            prio = raw_p  # type: ignore[assignment]

    register_task(
        run_id,
        channel=channel or "unknown",
        session_key=session_key,
        priority=prio,
        resource_tags=resource_tags,
    )
    try:
        pr = check_and_preempt(run_id, prio, resource_tags or [])
        if pr.preempted_run_ids:
            logger.info("[L3Hooks] global preempt %s", pr.message)
    except Exception as e:
        logger.debug("[L3Hooks] check_and_preempt skipped: %s", e)


def unregister_global_foreground_task(run_id: str) -> None:
    try:
        from l3_node.global_task_registry import global_registry_enabled, unregister_task

        if global_registry_enabled():
            unregister_task(run_id)
            return
    except ImportError:
        pass
    try:
        from l3_node.task_runtime_registry import unregister_foreground_task

        unregister_foreground_task(run_id)
    except Exception:
        logger.debug("[L3Hooks] unregister foreground 跳过", exc_info=True)


def try_auto_record_experience_run(
    user_intent: str,
    final_answer: str,
    tools_used: list[str] | set[str] | None,
    *,
    aborted: bool = False,
) -> None:
    """主路径成功结束时可选写入 Experience JSONL（run_agent:success）。"""
    if aborted:
        return
    ans = (final_answer or "").strip()
    if "[ExecutionBrief]" in ans:
        try_auto_record_experience_brief(
            user_intent,
            final_answer,
            tools_used,
            aborted=aborted,
        )
        return
    try:
        from l3_node.experience_memory import auto_record_run_enabled, save_run_success_episode
    except ImportError:
        return
    if not auto_record_run_enabled():
        return
    save_run_success_episode(user_intent, final_answer, tools_used)


def touch_global_registry_if_needed(run_id: str, *, role_execution_iteration: int = 0) -> None:
    """长 run 每 N 轮续期 Redis 任务键（默认每 5 轮）。"""
    if role_execution_iteration <= 0 or role_execution_iteration % 5 != 4:
        return
    try:
        from l3_node.global_registry_redis import redis_touch_enabled, touch_task_redis

        if redis_touch_enabled():
            touch_task_redis(run_id)
    except Exception:
        pass


def schedule_level3_brief_healing(
    user_intent: str,
    final_answer: str,
    *,
    session_key: str = "",
    tools_used: list[str] | set[str] | None = None,
) -> None:
    try:
        from l3_node.run_brief_healing import schedule_brief_healing_after_run

        schedule_brief_healing_after_run(
            user_intent,
            final_answer,
            session_key=session_key,
            tools_used=list(tools_used) if tools_used else None,
        )
    except Exception:
        pass


def try_auto_record_experience_brief(
    user_intent: str,
    final_answer: str,
    tools_used: list[str] | set[str] | None,
    *,
    aborted: bool = False,
    reason: str | None = None,
) -> None:
    """主路径 ExecutionBrief 时可选写入 Experience JSONL（run_agent:brief）。"""
    if aborted:
        return
    try:
        from l3_node.experience_memory import auto_record_fail_enabled, save_run_failure_episode
    except ImportError:
        return
    if not auto_record_fail_enabled():
        return
    save_run_failure_episode(
        user_intent,
        final_answer,
        tools_used=tools_used,
        reason=reason,
    )
