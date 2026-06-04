"""
流水线（Pipeline）多 Agent 编排器。

适用场景：
- Planner → Executor → Reviewer 三阶段编程任务
- Researcher → Analyst → Writer 报告生成流水线
- 数据采集 → 清洗 → 汇总分析的批处理流水线

每个阶段（Stage）对应一个 SubAgent 角色，上一阶段的输出自动注入为下一阶段的 context_data。

使用方式：
    from l3_node.primitives.multi_agent.pipeline import run_pipeline, PipelineStage

    result = await run_pipeline(
        stages=[
            PipelineStage(role="planner",  task="为以下需求拆解实现步骤：{goal}"),
            PipelineStage(role="coder",    task="根据计划实现代码"),
            PipelineStage(role="reviewer", task="审查上述代码的质量与安全性"),
        ],
        initial_context={"goal": "实现用户登录 API"},
        engine=engine,
        delegate_depth=1,
    )

若中间某阶段失败，默认策略为 on_failure="stop"（返回 ExecutionBrief 并中止）。
可设置 on_failure="continue" 跳过失败阶段继续执行（适合非阻塞采集场景）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from l3_node.primitives.multi_agent.role_utils import sub_agent_role_label

logger = logging.getLogger("multi_agent.pipeline")


@dataclass
class PipelineStage:
    """流水线中的一个阶段。"""
    role: str | dict[str, Any]
    task: str
    max_iterations: int = 3
    on_failure: Literal["stop", "continue"] = "stop"
    pass_context: bool = True
    debug_phase: int = 0
    debug_phase_label: str = ""
    debug_agent_label: str = ""
    debug_role_label: str = ""
    debug_task_preview: str = ""


@dataclass
class PipelineStageResult:
    stage_index: int
    role: str
    ok: bool
    result: str = ""
    error: str = ""
    elapsed_sec: float = 0.0


@dataclass
class PipelineResult:
    """流水线执行结果，含每阶段产出与整体状态。"""
    status: str                 # "completed" | "partial" | "failed" | "aborted"
    completed_stages: int
    total_stages: int
    stage_results: list[PipelineStageResult] = field(default_factory=list)
    final_output: str = ""
    execution_brief: str = ""
    elapsed_sec: float = 0.0

    def format_summary(self) -> str:
        lines = [
            f"[Pipeline] {self.completed_stages}/{self.total_stages} 阶段完成 "
            f"({self.status})，耗时 {self.elapsed_sec:.1f}s",
        ]
        for sr in self.stage_results:
            marker = "✓" if sr.ok else "✗"
            lines.append(f"  [{marker}] Stage {sr.stage_index}·{sr.role}")
            if not sr.ok:
                lines.append(f"       失败: {sr.error[:100]}")
        if self.execution_brief:
            lines.append(f"  Brief: {self.execution_brief[:200]}")
        return "\n".join(lines)


async def run_pipeline(
    stages: list[PipelineStage],
    engine: Any,
    *,
    initial_context: dict[str, Any] | str | None = None,
    delegate_depth: int = 1,
    parent_allowed_skills: list[str] | None = None,
) -> PipelineResult:
    """
    顺序执行流水线各阶段，每阶段输出自动流向下一阶段 context_data。

    Parameters
    ----------
    stages:
        有序阶段列表，每个 PipelineStage 描述一个 SubAgent 角色与任务
    engine:
        LiteLLMEngine 实例
    initial_context:
        初始上下文数据（注入第一阶段 context_data）；可为 dict 或字符串
    delegate_depth:
        子 Agent 的 delegate_depth
    """
    from l3_node.agent_core import _spawn_sub_agent_async

    t0 = time.monotonic()
    n = len(stages)
    if n == 0:
        return PipelineResult(
            status="completed", completed_stages=0, total_stages=0,
            final_output="", elapsed_sec=0.0,
        )

    stage_results: list[PipelineStageResult] = []
    prev_output: str = ""
    completed = 0

    # 将 initial_context 转为字符串
    if isinstance(initial_context, dict):
        import json
        ctx_str = json.dumps(initial_context, ensure_ascii=False, indent=2)
    elif initial_context:
        ctx_str = str(initial_context)
    else:
        ctx_str = ""

    for idx, stage in enumerate(stages):
        t1 = time.monotonic()
        task_text = stage.task

        # 将上一阶段输出注入到当前阶段 context_data
        context_payload: str | None = None
        if stage.pass_context:
            parts = []
            if idx == 0 and ctx_str:
                parts.append(ctx_str)
            if prev_output:
                prev_role = sub_agent_role_label(stages[idx - 1].role)
                parts.append(f"【上一阶段（{prev_role}）输出】\n{prev_output[:3000]}")
            if parts:
                context_payload = "\n\n".join(parts)

        spec: dict[str, Any] = {
            "role": stage.role,
            "task": task_text,
            "max_iterations": stage.max_iterations,
        }
        if context_payload:
            spec["context_data"] = context_payload

        debug_token = None
        if stage.debug_phase:
            try:
                from l3_node.pmo_copilot_debug_file import (
                    append_pmo_debug_agent_begin,
                    reset_ma_debug_context,
                    set_ma_debug_context,
                )

                agent_label = stage.debug_agent_label or sub_agent_role_label(stage.role)
                role_label = stage.debug_role_label or sub_agent_role_label(stage.role)
                task_short = stage.debug_task_preview or task_text[:120]
                debug_token = set_ma_debug_context(
                    phase=int(stage.debug_phase),
                    phase_label=stage.debug_phase_label,
                    agent_label=agent_label,
                    role_label=role_label,
                    task_preview=task_short,
                    max_iterations=stage.max_iterations,
                )
                append_pmo_debug_agent_begin(
                    agent_label=agent_label,
                    role_label=role_label,
                    task_preview=task_short,
                    max_iterations=stage.max_iterations,
                )
            except Exception:
                debug_token = None

        try:
            from l3_node.agent_core import _run_sub_agent
            result_str = await _run_sub_agent(
                spec, engine, delegate_depth=delegate_depth,
                _parent_allowed_skills=parent_allowed_skills,
            )
            elapsed = time.monotonic() - t1
            role_label = sub_agent_role_label(stage.role)
            if debug_token is not None:
                try:
                    from l3_node.pmo_copilot_debug_file import append_pmo_debug_agent_finish

                    append_pmo_debug_agent_finish(
                        agent_label=stage.debug_agent_label or role_label,
                        ok=True,
                        result_preview=str(result_str or "")[:300],
                        elapsed_sec=elapsed,
                    )
                except Exception:
                    pass
            sr = PipelineStageResult(
                stage_index=idx + 1, role=role_label,
                ok=True, result=result_str, elapsed_sec=elapsed,
            )
            stage_results.append(sr)
            prev_output = result_str
            completed += 1
            logger.info(
                "[Pipeline] Stage %d/%d·%s 完成 (%.1fs)",
                idx + 1, n, role_label, elapsed,
            )
        except Exception as e:
            elapsed = time.monotonic() - t1
            err_str = str(e)
            role_label = sub_agent_role_label(stage.role)
            if debug_token is not None:
                try:
                    from l3_node.pmo_copilot_debug_file import append_pmo_debug_agent_finish

                    append_pmo_debug_agent_finish(
                        agent_label=stage.debug_agent_label or role_label,
                        ok=False,
                        error=err_str,
                        elapsed_sec=elapsed,
                    )
                except Exception:
                    pass
            sr = PipelineStageResult(
                stage_index=idx + 1, role=role_label,
                ok=False, error=err_str, elapsed_sec=elapsed,
            )
            stage_results.append(sr)
            logger.warning(
                "[Pipeline] Stage %d/%d·%s 失败: %s",
                idx + 1, n, role_label, err_str[:200],
            )
            if stage.on_failure == "stop":
                brief = (
                    f"流水线在第 {idx+1} 阶段（{role_label}）失败并中止。"
                    f"已完成阶段: {completed}/{n}。"
                    f"失败原因: {err_str[:300]}。"
                    f"建议：检查上下文数据格式，或将 on_failure 设为 continue 跳过失败阶段。"
                )
                logger.warning("[Pipeline] [ExecutionBrief] %s", brief)
                return PipelineResult(
                    status="aborted",
                    completed_stages=completed,
                    total_stages=n,
                    stage_results=stage_results,
                    final_output=prev_output,
                    execution_brief=brief,
                    elapsed_sec=time.monotonic() - t0,
                )
            # on_failure="continue"：跳过失败阶段，prev_output 保持不变
        finally:
            if debug_token is not None:
                try:
                    from l3_node.pmo_copilot_debug_file import reset_ma_debug_context

                    reset_ma_debug_context(debug_token)
                except Exception:
                    pass

    total_elapsed = time.monotonic() - t0
    failed_count = sum(1 for sr in stage_results if not sr.ok)
    status = "completed" if failed_count == 0 else "partial"

    pipeline_result = PipelineResult(
        status=status,
        completed_stages=completed,
        total_stages=n,
        stage_results=stage_results,
        final_output=prev_output,
        elapsed_sec=total_elapsed,
    )
    logger.info("[Pipeline] %s", pipeline_result.format_summary().splitlines()[0])
    return pipeline_result
