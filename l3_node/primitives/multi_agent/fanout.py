"""
扇出并行（Fan-out Parallel）多 Agent 编排器。

适用场景：
- 批量文档分析（每份文档独立用 analyst SubAgent 处理）
- 多数据源并行查询（每个数据源一个 researcher SubAgent）
- 候选人简历并行初筛（每份简历一个 analyst SubAgent）
- 多模块代码并行检查（每个模块一个 reviewer SubAgent）

使用方式：
    from l3_node.primitives.multi_agent.fanout import fanout_parallel

    results = await fanout_parallel(
        items=[
            {"role": "analyst", "task": "分析 Q1 销售数据", "context_data": q1_csv},
            {"role": "analyst", "task": "分析 Q2 销售数据", "context_data": q2_csv},
        ],
        engine=engine,
        max_concurrent=3,
        delegate_depth=1,
    )

返回 FanoutResult，含 ok_items（成功结果列表）、failed_items（失败明细）和 RunReport 摘要。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("multi_agent.fanout")


@dataclass
class FanoutItemResult:
    index: int
    role: str
    task_preview: str
    ok: bool
    result: str = ""
    error: str = ""
    error_class: str = ""
    elapsed_sec: float = 0.0


@dataclass
class FanoutResult:
    """扇出执行结果，兼容执行韧性契约中的 RunReport 结构。"""
    status: str                             # "completed" | "partial" | "failed"
    ok_count: int
    failed_count: int
    total: int
    degraded: bool
    items: list[FanoutItemResult] = field(default_factory=list)
    elapsed_sec: float = 0.0

    @property
    def ok_items(self) -> list[FanoutItemResult]:
        return [i for i in self.items if i.ok]

    @property
    def failed_items(self) -> list[FanoutItemResult]:
        return [i for i in self.items if not i.ok]

    def as_run_report(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "ok_count": self.ok_count,
            "failed_count": self.failed_count,
            "total": self.total,
            "degraded": self.degraded,
            "elapsed_sec": round(self.elapsed_sec, 2),
            "failed_items": [
                {
                    "index": i.index,
                    "role": i.role,
                    "task_preview": i.task_preview,
                    "error": i.error,
                    "error_class": i.error_class,
                }
                for i in self.failed_items
            ],
        }

    def format_summary(self) -> str:
        lines = [
            f"[FanOut RunReport] {self.ok_count}/{self.total} 成功"
            + (f"，{self.failed_count} 失败" if self.failed_count else "")
            + f"，耗时 {self.elapsed_sec:.1f}s",
        ]
        for item in self.items:
            marker = "✓" if item.ok else "✗"
            preview = item.task_preview[:60]
            lines.append(f"  [{marker}] 子任务 {item.index}·{item.role}：{preview}")
            if not item.ok:
                lines.append(f"       错误: {item.error[:120]}")
        return "\n".join(lines)


async def fanout_parallel(
    items: list[dict[str, Any]],
    engine: Any,
    *,
    max_concurrent: int = 4,
    delegate_depth: int = 1,
    item_max_iterations: int = 3,
) -> FanoutResult:
    """
    并发执行多个子 Agent 任务，返回 FanoutResult。

    Parameters
    ----------
    items:
        子任务列表，每项为 dict，支持字段：
        - role: str            SubAgent 角色（默认 "default"）
        - task: str            任务描述
        - context_data: any    附加数据上下文（str 或 dict，自动序列化并追加到任务描述）
        - max_iterations: int  覆盖本子任务最大迭代次数
    engine:
        LiteLLMEngine 实例（来自主 run_agent 上下文）
    max_concurrent:
        最大并发 SubAgent 数（Semaphore 上限），0 = 不限制
    delegate_depth:
        传给子 Agent 的 delegate_depth（建议 = 主 Agent 深度 + 1）
    item_max_iterations:
        子任务默认最大迭代次数（可被 items[i].max_iterations 覆盖）
    """
    from l3_node.agent_core import _run_sub_agent

    t0 = time.monotonic()
    n = len(items)
    if n == 0:
        return FanoutResult(status="completed", ok_count=0, failed_count=0, total=0, degraded=False)

    sem = asyncio.Semaphore(max_concurrent) if max_concurrent > 0 else None

    async def _run_one(idx: int, spec: dict[str, Any]) -> FanoutItemResult:
        role = (spec.get("role") or "default").lower()
        task_preview = str(spec.get("task", ""))[:80]
        # 注入默认 max_iterations（若 spec 未显式设置）
        eff_spec = dict(spec)
        eff_spec.setdefault("max_iterations", item_max_iterations)
        t1 = time.monotonic()
        try:
            if sem is not None:
                async with sem:
                    result = await _run_sub_agent(eff_spec, engine, delegate_depth=delegate_depth)
            else:
                result = await _run_sub_agent(eff_spec, engine, delegate_depth=delegate_depth)
            elapsed = time.monotonic() - t1
            return FanoutItemResult(
                index=idx + 1, role=role, task_preview=task_preview,
                ok=True, result=result, elapsed_sec=elapsed,
            )
        except Exception as e:
            elapsed = time.monotonic() - t1
            err_str = str(e)
            err_class = "transient" if "timeout" in err_str.lower() else "per_item"
            logger.warning("[FanOut] 子任务 %d·%s 失败: %s", idx + 1, role, err_str[:200])
            return FanoutItemResult(
                index=idx + 1, role=role, task_preview=task_preview,
                ok=False, error=err_str, error_class=err_class, elapsed_sec=elapsed,
            )

    raw_results = await asyncio.gather(*[_run_one(i, spec) for i, spec in enumerate(items)])
    item_results: list[FanoutItemResult] = list(raw_results)
    ok_count = sum(1 for r in item_results if r.ok)
    failed_count = n - ok_count
    status = "completed" if failed_count == 0 else ("partial" if ok_count > 0 else "failed")
    elapsed = time.monotonic() - t0

    result = FanoutResult(
        status=status,
        ok_count=ok_count,
        failed_count=failed_count,
        total=n,
        degraded=failed_count > 0,
        items=item_results,
        elapsed_sec=elapsed,
    )
    logger.info("[FanOut] %s", result.format_summary().splitlines()[0])
    return result
