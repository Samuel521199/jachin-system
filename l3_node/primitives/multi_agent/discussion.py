"""
讨论/辩论模式多 Agent 编排器（§2.4 模式 B）

适用场景：
- 复杂决策（方案评审、架构选型）
- 有争议的判断（需要多角度验证）
- 重要执行前的风险排查

讨论流程：
    Round 1（并行）：planner 提出方案草稿 + critic 列出质疑点
    Round 2（串行）：planner 根据批评修订方案
    Round N（可选）：继续批评 + 修订，直到 critic 无新质疑点 OR 达到 max_rounds
    Final：summarizer 输出最终共识（可选）

使用方式（在 agent_core delegate 分支中）：
    from l3_node.primitives.multi_agent.discussion import run_discussion, DiscussionConfig

    result = await run_discussion(
        config=DiscussionConfig(
            topic="是否采用微服务架构",
            context="当前系统是单体，用户量预计翻 10 倍",
            roles=["planner", "critic"],
            max_rounds=3,
        ),
        engine=engine,
        delegate_depth=current_depth + 1,
    )
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from l3_node.primitives.multi_agent.result_merger import SubAgentResult, StructuredResultMerger

logger = logging.getLogger("multi_agent.discussion")

_STOP_KEYWORDS = ("无新质疑", "无更多质疑", "没有新的质疑", "方案已完善", "无进一步意见", "无异议")


@dataclass
class DiscussionConfig:
    topic: str
    context: str = ""
    roles: list[str] = field(default_factory=lambda: ["planner", "critic"])
    max_rounds: int = 3
    use_summarizer: bool = True          # 是否在最后加 summarizer 角色
    item_max_iterations: int = 3


@dataclass
class DiscussionResult:
    status: str                          # "completed" | "aborted"
    rounds_completed: int
    final_output: str
    rounds_detail: list[list[SubAgentResult]] = field(default_factory=list)
    execution_brief: str = ""
    elapsed_sec: float = 0.0

    def format_summary(self) -> str:
        return (
            f"[Discussion] {self.rounds_completed} 轮 ({self.status})，"
            f"耗时 {self.elapsed_sec:.1f}s"
        )


async def run_discussion(
    config: DiscussionConfig,
    engine: Any,
    *,
    delegate_depth: int = 1,
) -> DiscussionResult:
    """
    执行多轮讨论，返回 DiscussionResult。

    Parameters
    ----------
    config:
        讨论配置（topic、roles、max_rounds 等）
    engine:
        LiteLLMEngine 实例
    delegate_depth:
        子 Agent 的 delegate_depth
    """
    from l3_node.agent_core import _run_sub_agent

    t0 = time.monotonic()
    merger = StructuredResultMerger()
    all_rounds: list[list[SubAgentResult]] = []
    current_plan = ""            # 当前方案文本（在轮次间传递）
    consensus_reached = False

    planner_role = next((r for r in config.roles if "planner" in r.lower()), config.roles[0])
    critic_role = next((r for r in config.roles if "critic" in r.lower()), None)
    other_roles = [r for r in config.roles if r not in (planner_role, critic_role) and r != "summarizer"]

    for round_idx in range(1, config.max_rounds + 1):
        round_results: list[SubAgentResult] = []
        t_round = time.monotonic()

        if round_idx == 1:
            # Round 1：planner 提初稿 + critic 并行质疑
            tasks = []
            plan_task_desc = (
                f"请就以下议题提出一份方案草稿：\n\n议题：{config.topic}\n\n背景：{config.context}"
            )
            tasks.append({"role": planner_role, "task": plan_task_desc,
                          "max_iterations": config.item_max_iterations})

            if critic_role:
                critic_task_desc = (
                    f"请针对以下议题，提出至少 3 个质疑点（风险/漏洞/缺陷）：\n\n"
                    f"议题：{config.topic}\n\n背景：{config.context}"
                )
                tasks.append({"role": critic_role, "task": critic_task_desc,
                              "max_iterations": config.item_max_iterations})

            sub_results = await asyncio.gather(
                *[_run_sub_agent(t, engine, delegate_depth=delegate_depth) for t in tasks],
                return_exceptions=True,
            )

            for i, (task_spec, res) in enumerate(zip(tasks, sub_results)):
                role = task_spec["role"]
                if isinstance(res, Exception):
                    r = SubAgentResult(role_id=role, task=task_spec["task"][:80],
                                      output=str(res), status="failed")
                else:
                    r = SubAgentResult(role_id=role, task=task_spec["task"][:80],
                                      output=str(res), status="success")
                    if role == planner_role:
                        current_plan = str(res)
                round_results.append(r)

        else:
            # Round 2+：planner 根据上一轮批评修订，critic 再次审查
            critic_feedback = ""
            for prev_round in reversed(all_rounds):
                for r in prev_round:
                    if critic_role and r.role_id == critic_role and r.status == "success":
                        critic_feedback = r.output
                        break
                if critic_feedback:
                    break

            # 检查 critic 是否表示无新质疑（终止条件）
            if critic_feedback and any(kw in critic_feedback for kw in _STOP_KEYWORDS):
                logger.info("[Discussion] Round %d: critic 无新质疑，提前终止", round_idx)
                consensus_reached = True
                break

            revise_task = (
                f"请根据以下批评意见，修订你的方案草稿：\n\n"
                f"原方案：\n{current_plan[:1500]}\n\n"
                f"批评意见：\n{critic_feedback[:1000]}\n\n"
                f"议题：{config.topic}"
            )
            try:
                revised = await _run_sub_agent(
                    {"role": planner_role, "task": revise_task,
                     "max_iterations": config.item_max_iterations},
                    engine, delegate_depth=delegate_depth,
                )
                current_plan = str(revised)
                round_results.append(SubAgentResult(
                    role_id=planner_role, task=revise_task[:80],
                    output=str(revised), status="success",
                ))
            except Exception as e:
                round_results.append(SubAgentResult(
                    role_id=planner_role, task=revise_task[:80],
                    output=str(e), status="failed",
                ))

            # Critic 再次审查修订后的方案
            if critic_role:
                re_review_task = (
                    f"请审查以下修订后方案，若仍有质疑请列出；若方案已完善请明确说明「无新质疑」：\n\n"
                    f"{current_plan[:1500]}"
                )
                try:
                    critic_res = await _run_sub_agent(
                        {"role": critic_role, "task": re_review_task,
                         "max_iterations": config.item_max_iterations},
                        engine, delegate_depth=delegate_depth,
                    )
                    round_results.append(SubAgentResult(
                        role_id=critic_role, task=re_review_task[:80],
                        output=str(critic_res), status="success",
                    ))
                    if any(kw in str(critic_res) for kw in _STOP_KEYWORDS):
                        consensus_reached = True
                except Exception as e:
                    round_results.append(SubAgentResult(
                        role_id=critic_role, task=re_review_task[:80],
                        output=str(e), status="failed",
                    ))

        all_rounds.append(round_results)
        logger.info("[Discussion] Round %d 完成 (%.1fs)", round_idx, time.monotonic() - t_round)

        if consensus_reached:
            break

    # Final：summarizer 输出最终共识（可选）
    if config.use_summarizer and all_rounds and current_plan:
        summary_task = (
            f"请将以下多轮讨论的结果整理为结构化报告："
            f"共识点 / 分歧点 / 推荐行动 / 风险提示。\n\n"
            f"最终方案：\n{current_plan[:2000]}\n\n"
            f"议题：{config.topic}"
        )
        try:
            summary = await _run_sub_agent(
                {"role": "summarizer", "task": summary_task,
                 "max_iterations": config.item_max_iterations},
                engine, delegate_depth=delegate_depth,
            )
            all_rounds.append([SubAgentResult(
                role_id="summarizer", task=summary_task[:80],
                output=str(summary), status="success",
            )])
            current_plan = str(summary)
        except Exception as e:
            logger.warning("[Discussion] summarizer failed: %s", e)

    elapsed = time.monotonic() - t0
    final_output = merger.merge_discussion(all_rounds)

    result = DiscussionResult(
        status="completed",
        rounds_completed=len(all_rounds),
        final_output=final_output,
        rounds_detail=all_rounds,
        elapsed_sec=elapsed,
    )
    logger.info("[Discussion] %s", result.format_summary())
    return result
