"""
StructuredResultMerger — 多 Agent 结果结构化合并器（§2.5）

将多个 SubAgent 的输出合并为有来源标注的结构化 Observation，
供主 Agent ReAct 消费。支持并行模式（列表式）和讨论模式（轮次演化）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


def _markdown_cell(s: str, max_len: int) -> str:
    """单行 Markdown 表格单元：去换行、避开管道符，便于渲染。"""
    t = " ".join(str(s or "").split())
    t = t.replace("|", "·")
    if len(t) > max_len:
        return t[: max_len - 1] + "…"
    return t


@dataclass
class SubAgentResult:
    role_id: str
    task: str
    output: str
    status: Literal["success", "partial", "failed"]
    token_hint: int = 0          # 可选：估算 token 消耗
    elapsed_sec: float = 0.0


class StructuredResultMerger:
    """将多个 SubAgent 结果合并为结构化 Observation 字符串。"""

    def merge_parallel(
        self,
        results: list[SubAgentResult],
        *,
        max_output_chars: int = 4000,
        with_index_table: bool = True,
    ) -> str:
        """
        并行模式：列表式合并，保留来源标注。

        输出格式：
        ────────────────────────────
        （可选）Markdown 索引表：# / 角色 / 状态 / 输出预览
        [并行子任务汇总] ok/total 成功
        [子任务 i/N] 角色：xxx  状态：✓/✗
        任务：...
        输出：...
        ────────────────────────────
        """
        if not results:
            return "[并行子任务] 无结果"

        ok = sum(1 for r in results if r.status == "success")
        parts: list[str] = []
        if with_index_table:
            rows = [
                "| # | 角色 | 状态 | 输出预览 |",
                "|---|------|------|---------|",
            ]
            for i, r in enumerate(results, 1):
                icon = "✓" if r.status == "success" else ("⚠" if r.status == "partial" else "✗")
                rows.append(
                    "| "
                    + " | ".join(
                        [
                            str(i),
                            _markdown_cell(r.role_id, 28),
                            icon,
                            _markdown_cell(r.output, 96),
                        ]
                    )
                    + " |"
                )
            parts.append("[并行子任务·索引表]\n" + "\n".join(rows))
        parts.append(f"[并行子任务汇总] {ok}/{len(results)} 成功")
        per_item_cap = max(300, max_output_chars // max(len(results), 1))

        for i, r in enumerate(results, 1):
            icon = "✓" if r.status == "success" else ("⚠" if r.status == "partial" else "✗")
            block = (
                f"\n{'─' * 44}\n"
                f"[子任务 {i}/{len(results)}] 角色：{r.role_id}  状态：{icon}\n"
                f"任务：{r.task[:120]}\n"
                f"输出：{r.output[:per_item_cap]}"
                + ("…（已截断）" if len(r.output) > per_item_cap else "")
            )
            parts.append(block)

        parts.append(f"\n{'─' * 44}")
        return "\n".join(parts)

    def merge_discussion(
        self,
        rounds: list[list[SubAgentResult]],
        *,
        max_output_chars: int = 5000,
    ) -> str:
        """
        讨论模式：按轮次展示共识演化，最终输出决策建议。

        输出格式：
        [讨论结果] N 轮  共识已达成/未达成
        === 第 1 轮 ===
        [planner] ...
        [critic]  ...
        === 第 2 轮（修订）===
        ...
        === 最终建议 ===
        ...
        """
        if not rounds:
            return "[讨论模式] 无轮次结果"

        n_rounds = len(rounds)
        per_round_cap = max(500, max_output_chars // max(n_rounds, 1))
        parts = [f"[讨论结果] 共 {n_rounds} 轮"]

        for round_idx, round_results in enumerate(rounds, 1):
            label = "修订" if round_idx > 1 else "初稿"
            parts.append(f"\n{'=' * 44}\n=== 第 {round_idx} 轮（{label}）===")
            per_role_cap = max(200, per_round_cap // max(len(round_results), 1))
            for r in round_results:
                icon = "✓" if r.status == "success" else "✗"
                snippet = r.output[:per_role_cap] + ("…" if len(r.output) > per_role_cap else "")
                parts.append(f"[{r.role_id} {icon}]  {snippet}")

        # 最后一轮的第一个结果视为最终共识输出
        final_round = rounds[-1]
        final_text = ""
        for r in final_round:
            if r.status == "success" and r.role_id in ("summarizer", "planner"):
                final_text = r.output
                break
        if not final_text and final_round:
            final_text = final_round[0].output

        parts.append(f"\n{'=' * 44}\n=== 最终建议 ===\n{final_text[:1500]}")
        return "\n".join(parts)
