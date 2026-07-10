"""
RoleExecutionAgent 中途 TaskDAG 重规划（路线图 §3 · TaskDAG P2）

在 run_agent RoleExecutionAgent 循环内，根据进度与用户热并入触发 LLM 更新 active.json（保留已完成节点状态）。

环境变量
--------
JACHIN_DAG_REPLAN_MID_RUN=1           开启中途重规划（默认关）
JACHIN_DAG_REPLAN_EVERY_N_ITER=3      每 N 轮 RoleExecutionAgent 迭代至少评估一次（默认 3）
JACHIN_DAG_REPLAN_MAX_PER_RUN=3       单次 run 最多重规划次数（默认 3）
JACHIN_DAG_REPLAN_MIN_ITER_GAP=2      两次重规划之间最少间隔迭代数（默认 2）
JACHIN_DAG_REPLAN_MODEL=              覆盖规划 LLM（默认同 JACHIN_DAG_PLAN_MODEL）
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)


def mid_replan_enabled() -> bool:
    return (os.environ.get("JACHIN_DAG_REPLAN_MID_RUN") or "").strip().lower() in (
        "1", "true", "yes",
    )


def _every_n_iter() -> int:
    try:
        return max(1, min(12, int(os.environ.get("JACHIN_DAG_REPLAN_EVERY_N_ITER") or "3")))
    except ValueError:
        return 3


def _max_per_run() -> int:
    try:
        return max(1, min(8, int(os.environ.get("JACHIN_DAG_REPLAN_MAX_PER_RUN") or "3")))
    except ValueError:
        return 3


def _min_iter_gap() -> int:
    try:
        return max(0, min(10, int(os.environ.get("JACHIN_DAG_REPLAN_MIN_ITER_GAP") or "2")))
    except ValueError:
        return 2


def _build_replan_intent(
    *,
    primary_intent: str,
    trigger: str,
    hot_user_lines: list[str] | None = None,
    iteration: int = 0,
) -> str:
    from l3_node.task_engine.task_dag import format_active_task_dag_prompt_suffix

    dag_block = (format_active_task_dag_prompt_suffix(max_nodes=32, max_chars=2400) or "").strip()
    parts = [
        "【TaskDAG 中途重规划】",
        f"触发原因：{trigger}",
        f"原始用户任务：{(primary_intent or '').strip()[:2000]}",
    ]
    if dag_block:
        parts.append("当前 active.json 进度摘要：")
        parts.append(dag_block)
    if hot_user_lines:
        parts.append("用户在本轮推理期间的新进线（须纳入剩余步骤）：")
        for line in hot_user_lines[:8]:
            parts.append(f"- {line[:300]}")
    parts.append(
        "请输出更新后的 TaskDAG JSON：保留 node_id 与已完成（done/completed）节点；"
        "可调整 pending 节点顺序、标题与依赖；可追加必要步骤；节点数 2~12。"
        f"（RoleExecutionAgent 迭代={iteration + 1}）"
    )
    return "\n".join(parts)


def refresh_task_dag_block_in_system_prompt(system_prompt: str) -> str:
    """用磁盘最新 active.json 替换 system prompt 中的 TaskDAG 段。"""
    from l3_node.task_engine.task_dag import format_active_task_dag_prompt_suffix

    fresh = (format_active_task_dag_prompt_suffix() or "").strip()
    if not fresh:
        return system_prompt
    block = "\n" + fresh + "\n"
    pat = re.compile(
        r"\n?【TaskDAG·[\s\S]*?关闭本段注入。\n?",
        re.MULTILINE,
    )
    if pat.search(system_prompt or ""):
        return pat.sub(block, system_prompt, count=1)
    return (system_prompt or "") + block


async def maybe_replan_during_role_execution(
    ctx: Any,
    iteration: int,
    *,
    trigger: str = "periodic",
    hot_user_lines: list[str] | None = None,
) -> bool:
    """
    RoleExecutionAgent 迭代内调用。返回是否成功写回 active.json 并刷新了 ctx.system_prompt。
    """
    if not mid_replan_enabled():
        return False
    if int(ctx.metadata.get("_delegate_depth") or 0) != 0:
        return False

    count = int(ctx.metadata.get("_dag_replan_count") or 0)
    if count >= _max_per_run():
        return False

    last_iter = int(ctx.metadata.get("_dag_last_replan_iter") or -99)
    if iteration - last_iter < _min_iter_gap() and trigger == "periodic":
        return False

    if trigger == "periodic" and (iteration + 1) % _every_n_iter() != 0:
        return False

    from l3_node.task_engine.task_dag import active_task_dag_path

    if not active_task_dag_path().is_file():
        return False

    primary = str(ctx.intent or ctx.metadata.get("_sticky_goal") or "").strip()
    replan_intent = _build_replan_intent(
        primary_intent=primary,
        trigger=trigger,
        hot_user_lines=hot_user_lines,
        iteration=iteration,
    )

    try:
        from l3_node.task_engine.dag_planner import plan_task_dag

        result = await plan_task_dag(replan_intent, force=True)
    except Exception as e:
        logger.debug("[DagReplan] plan failed: %s", e)
        return False

    if not result.ok:
        logger.debug("[DagReplan] skipped trigger=%s err=%s", trigger, result.error)
        return False

    ctx.metadata["_dag_replan_count"] = count + 1
    ctx.metadata["_dag_last_replan_iter"] = iteration
    try:
        ctx.system_prompt = refresh_task_dag_block_in_system_prompt(
            getattr(ctx, "system_prompt", "") or ""
        )
        full = ctx.metadata.get("_role_execution_system_prompt_full")
        if isinstance(full, str):
            ctx.metadata["_role_execution_system_prompt_full"] = refresh_task_dag_block_in_system_prompt(full)
    except Exception:
        pass

    logger.info(
        "[DagReplan] ok run_id=%s trigger=%s iter=%d nodes=%d count=%d",
        str(ctx.run_id or "")[:12],
        trigger,
        iteration + 1,
        len(result.nodes),
        count + 1,
    )
    return True
