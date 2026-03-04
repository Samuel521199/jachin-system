"""
Jachin Nexus v8.0 - Dream Weaver (梦境重塑与记忆自愈)

系统空闲时，对 LanceDB 中的近期记忆碎片进行聚类、去重、融合，
将「喜欢咖啡」+「每天喝美式」➡️「每天早上习惯喝美式咖啡」，
删掉旧碎片，存入高密度核心认知。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.theme import Theme

from core.memory_store import (
    delete_memories,
    get_unconsolidated_memories,
    insert_consolidated_memory,
)

logger = logging.getLogger(__name__)
console = Console(
    theme=Theme({
        "dream": "#a78bfa",
        "dim": "dim",
        "success": "#22c55e",
    })
)

_CONSOLIDATION_THRESHOLD = 10  # 碎片数量达到此阈值才触发梦境重塑

_DREAM_SYSTEM_PROMPT = """你是一个 Jachin 边缘智能体的「潜意识整理器」。请分析以下记忆碎片，完成以下任务：

1. **合并重复**：将语义相同或高度重叠的内容合并为一条高密度表述。
2. **解决冲突**：若存在逻辑冲突（如「喜欢茶」与「只喝咖啡」），打上「需用户澄清」标签。
3. **去除冗余**：删除无价值的闲聊、一次性指令、查天气等临时信息。
4. **提炼事实**：输出高密度、结构化的核心事实（Facts）。

示例：
- 输入：「喜欢咖啡」「每天喝美式」「早上必喝一杯」
- 输出：「每天早上习惯喝美式咖啡」

输出格式：仅输出 JSON 数组，每项形如 {"fact": "高密度事实描述", "needs_clarification": false}。
若需用户澄清，则 needs_clarification 为 true。不要输出其他文字，只输出 JSON 数组。"""


def _parse_consolidated_output(text: str) -> list[dict[str, Any]]:
    """解析 LLM 输出的结构化事实列表"""
    text = (text or "").strip()
    if not text:
        return []
    m = re.search(r"\[[\s\S]*?\]", text)
    if m:
        try:
            arr = json.loads(m.group())
            if isinstance(arr, list):
                return [
                    {"fact": str(item.get("fact", "")).strip(), "needs_clarification": bool(item.get("needs_clarification"))}
                    for item in arr
                    if isinstance(item, dict) and item.get("fact")
                ]
        except json.JSONDecodeError:
            pass
    return []


async def _call_llm_for_consolidation(fragments_text: str) -> str:
    """调用 LLM 执行记忆融合"""
    try:
        from core.llm_provider import CognitiveEngineFactory
        engine = CognitiveEngineFactory.get_engine()
        messages = [
            {"role": "system", "content": _DREAM_SYSTEM_PROMPT},
            {"role": "user", "content": f"记忆碎片：\n\n{fragments_text}\n\n请进行聚类、去重、融合，输出 JSON 数组。"},
        ]
        result = await engine.generate_response(messages, temperature=0.3, max_tokens=2048)
        if isinstance(result, dict):
            result = result.get("content", "") or ""
        return (result or "").strip()
    except Exception as e:
        logger.warning("[DreamWeaver] LLM 调用失败: %s", e)
        return ""


class DreamWeaver:
    """
    v8.0 梦境重塑引擎：压缩、提纯 LanceDB 记忆碎片。
    """

    def __init__(self, threshold: int = _CONSOLIDATION_THRESHOLD) -> None:
        self.threshold = threshold

    async def weave_dreams(self) -> int:
        """
        执行一次梦境重塑：拉取未整合碎片 → LLM 融合 → 删除旧碎片 → 写入高密度事实。

        Returns:
            写入的核心认知条数
        """
        fragments = get_unconsolidated_memories(limit=100)
        if len(fragments) < self.threshold:
            logger.debug("[DreamWeaver] 碎片数量 %d < 阈值 %d，跳过", len(fragments), self.threshold)
            return 0

        console.print(Panel(
            f"[dream]系统进入深度睡眠... 正在提取 [bold]{len(fragments)}[/bold] 条记忆碎片。[/dream]",
            title="[bold magenta]🌙 Dream Weaver[/bold magenta]",
            border_style="magenta",
        ))

        fragments_text = "\n".join(f"- {f['text']}" for f in fragments)
        raw_output = await _call_llm_for_consolidation(fragments_text)
        facts = _parse_consolidated_output(raw_output)

        # 过滤需澄清的，仅写入确定事实
        to_insert = [f["fact"] for f in facts if not f.get("needs_clarification")]
        inserted = 0
        for fact in to_insert:
            if fact and insert_consolidated_memory(fact):
                inserted += 1

        old_ids = [f["id"] for f in fragments]
        delete_memories(old_ids)

        console.print(Panel(
            f"[success]潜意识重塑完成：压缩为 [bold]{len(to_insert)}[/bold] 条核心高级认知，已清理 [bold]{len(old_ids)}[/bold] 条冗余神经元。[/success]",
            title="[bold green]✨ Dream Weaver[/bold green]",
            border_style="green",
        ))
        logger.info("[DreamWeaver] 梦境完成：%d 条碎片 → %d 条核心认知", len(fragments), inserted)
        return inserted


async def run_weave_dreams(threshold: int = _CONSOLIDATION_THRESHOLD) -> int:
    """便捷入口：执行一次梦境重塑"""
    return await DreamWeaver(threshold=threshold).weave_dreams()
