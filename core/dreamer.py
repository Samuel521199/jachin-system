"""
Jachin Nexus Layer 2 - 梦境引擎 (The Dream Sequence)

v8.0 划时代设计：在凌晨 3 点或设备闲置时，对海马体短期日志执行「梦境回放」，
提纯出高密度核心记忆，写入大脑皮层。像人一样睡觉、遗忘和成长。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from core.biological_memory import (
    add_core_memory,
    delete_short_term_after_dream,
    export_core_memory_to_markdown,
    get_short_term_for_dream,
    prune_short_term_older_than_24h,
)

logger = logging.getLogger(__name__)

_DREAM_SYSTEM_PROMPT = """你是一个 Jachin 边缘智能体的「梦境引擎」。你的任务是对今日的交互日志进行「梦境回放」，提取值得永久记住的核心信息。

规则：
1. 只提取对长期服务有价值的信息：主人偏好、习惯、重要错误、服务器/设备异常、关键配置等。
2. 遗忘无用的废话：查天气、闲聊、一次性指令等。
3. 每条核心记忆必须有 tag 和 content。tag 示例：preference、user_habit、server_alert、error_pattern、config_hint。
4. 输出格式必须为 JSON 数组，每项形如 {"tag": "xxx", "content": "xxx"}。不要输出其他文字，只输出 JSON。"""


def _format_logs_for_dream(logs: list[dict[str, Any]]) -> str:
    """将短期日志格式化为 LLM 可读的文本"""
    lines = []
    for log in logs:
        role = log.get("role", "user")
        content = (log.get("content") or "").strip()
        meta = log.get("meta") or {}
        if content:
            meta_str = f" [meta: {json.dumps(meta, ensure_ascii=False)}]" if meta else ""
            lines.append(f"[{role}] {content}{meta_str}")
    return "\n".join(lines) if lines else "(今日无交互)"


def _parse_dream_output(text: str) -> list[dict[str, str]]:
    """
    解析 LLM 梦境输出，提取 tag + content 列表。
    支持 JSON 数组或行内 JSON 块。
    """
    text = (text or "").strip()
    if not text:
        return []

    # 尝试提取 JSON 数组
    m = re.search(r"\[[\s\S]*?\]", text)
    if m:
        try:
            arr = json.loads(m.group())
            if isinstance(arr, list):
                result = []
                for item in arr:
                    if isinstance(item, dict):
                        tag = (item.get("tag") or "").strip()
                        content = (item.get("content") or str(item.get("content", ""))).strip()
                        if tag and content:
                            result.append({"tag": tag, "content": content})
                return result
        except json.JSONDecodeError:
            pass

    return []


async def _call_llm_for_dream(logs_text: str) -> str:
    """调用 LLM 执行梦境回放"""
    try:
        from core.brain.llm.factory import LLMProviderFactory

        router = LLMProviderFactory.create_router()
        provider = router.local_adapter or router.qwen_adapter
        if not provider:
            try:
                provider = LLMProviderFactory.create_provider("local")
            except Exception:
                try:
                    provider = LLMProviderFactory.create_provider("qwen")
                except Exception:
                    pass
        if not provider:
            logger.warning("[Dreamer] LLM 未配置，跳过梦境回放")
            return ""

        messages = [
            {"role": "system", "content": _DREAM_SYSTEM_PROMPT},
            {"role": "user", "content": f"今日交互日志：\n\n{logs_text}\n\n请进行梦境回放，提取核心记忆，输出 JSON 数组。"},
        ]
        result = await provider.chat(
            messages,
            temperature=0.3,
            max_tokens=2048,
        )
        return (result or "").strip()
    except Exception as e:
        logger.warning("[Dreamer] LLM 调用失败: %s", e)
        return ""


async def run_dream_sequence(limit: int = 500) -> int:
    """
    执行一次梦境序列：读取短期日志 -> LLM 提纯 -> 写入核心记忆 -> 清理短期日志。

    Returns:
        写入的核心记忆条数
    """
    logs = get_short_term_for_dream(limit=limit)
    if not logs:
        logger.info("[Dreamer] 今日无短期日志，跳过梦境")
        prune_short_term_older_than_24h()
        return 0

    logs_text = _format_logs_for_dream(logs)
    raw_output = await _call_llm_for_dream(logs_text)
    extracted = _parse_dream_output(raw_output)

    count = 0
    for item in extracted:
        tag = item.get("tag", "general")
        content = item.get("content", "")
        if content:
            add_core_memory(tag=tag, content=content, source_summary="梦境提纯")
            count += 1

    ids = [log["id"] for log in logs]
    delete_short_term_after_dream(ids)
    prune_short_term_older_than_24h()

    # 定期导出 core_memory 为 Markdown，便于人类查看和版本控制
    try:
        path = export_core_memory_to_markdown()
        if path:
            logger.info("[Dreamer] 核心记忆已导出至 %s", path)
    except Exception as e:
        logger.debug("[Dreamer] Markdown 导出跳过: %s", e)

    logger.info("[Dreamer] 梦境完成，提纯 %d 条核心记忆，已遗忘 %d 条短期日志", count, len(ids))
    return count


def run_dream_sequence_sync(limit: int = 500) -> int:
    """同步包装，供守护进程或 cron 调用"""
    return asyncio.run(run_dream_sequence(limit=limit))
