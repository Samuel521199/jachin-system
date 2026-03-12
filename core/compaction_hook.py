"""
Jachin Nexus v8.0 — 神盾 Compaction Hook（上下文时空折叠）

注册到 HOOK_BEFORE_LLM_THINK，当 ctx.messages 超 token 阈值时，
将中间陈旧对话折叠为【历史摘要】，防止 ContextWindowExceededError。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from rich.console import Console

from core.hooks_pipeline import HOOK_BEFORE_LLM_THINK, PipelineContext, global_hooks

logger = logging.getLogger(__name__)
console = Console()
_NEXUS_CONFIG = Path.home() / ".jachin" / "nexus_config.json"
_DEFAULT_THRESHOLD = 6000
_DEFAULT_SUMMARY_MODEL = "ollama/qwen2.5"


def _load_nexus_config() -> dict[str, Any]:
    """读取 ~/.jachin/nexus_config.json"""
    if not _NEXUS_CONFIG.exists():
        return {}
    try:
        return json.loads(_NEXUS_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """极简 token 估算：字符数 / 4（中文约 1.5 字/token，英文约 4 字/token，取折中）"""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        total = 0
        for m in messages:
            content = m.get("content") or ""
            if isinstance(content, str):
                total += len(enc.encode(content))
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        total += len(enc.encode(part.get("text", "")))
        return total
    except ImportError:
        return len(str(messages)) // 4


def _get_compaction_config() -> tuple[int, str]:
    """返回 (threshold, summary_model)"""
    cfg = _load_nexus_config()
    llm = (cfg.get("llm") or {}) if isinstance(cfg.get("llm"), dict) else {}
    threshold = int(llm.get("compaction_threshold", _DEFAULT_THRESHOLD))
    raw = str(llm.get("compaction_model") or llm.get("edge_model") or _DEFAULT_SUMMARY_MODEL).strip()
    # edge_model 可能为 "qwen2.5:0.5b"，需补全 ollama/ 前缀供 LiteLLM 使用
    if raw and "/" not in raw and ":" in raw:
        model = f"ollama/{raw}"
    else:
        model = raw or _DEFAULT_SUMMARY_MODEL
    return threshold, model


async def _generate_summary(middle_messages: list[dict[str, str]], summary_model: str) -> str:
    """异步调用 LLM 生成历史摘要"""
    from core.llm_provider import LiteLLMEngine

    engine = LiteLLMEngine(model_name=summary_model)
    content_blob = "\n\n".join(
        f"{m.get('role', '')}: {m.get('content', '')}"[:500]
        for m in middle_messages[:20]  # 最多取 20 条
    )
    summary_prompt = f"""将以下对话压缩为一段极度精简的【历史摘要】，保留关键事实、决策和用户偏好。不超过 200 字。

对话内容：
{content_blob}

历史摘要："""
    summary = await engine.generate_response(
        [{"role": "user", "content": summary_prompt}],
        temperature=0.3,
        max_tokens=256,
    )
    if isinstance(summary, dict):
        summary = summary.get("content", "") or ""
    return (summary or "").strip() or "[对话已压缩]"


async def _compaction_handler(ctx: PipelineContext) -> None:
    """
    神盾 Compaction：超载时折叠中间消息为历史摘要。
    修改 ctx.messages 原地，不阻塞主线程（异步执行）。
    """
    messages = ctx.messages
    if not messages or len(messages) < 4:
        return

    threshold, summary_model = _get_compaction_config()
    estimated = _estimate_tokens(messages)
    if estimated <= threshold:
        return

    # 保留：第一条 system + 最后 2 轮 (user + assistant)
    first_system: list[dict[str, str]] = []
    last_rounds: list[dict[str, str]] = []
    middle: list[dict[str, str]] = []

    for m in messages:
        role = (m.get("role") or "").strip().lower()
        if role == "system" and not first_system:
            first_system.append(m)
        else:
            middle.append(m)

    # 从 middle 尾部取出最后 2 轮（4 条：user, assistant, user, assistant）
    if len(middle) > 4:
        last_rounds = middle[-4:]
        middle = middle[:-4]
    else:
        last_rounds = middle
        middle = []

    if not middle:
        return

    try:
        summary_text = await _generate_summary(middle, summary_model)
        summary_msg: dict[str, str] = {
            "role": "system",
            "content": f"【历史摘要】{summary_text}",
        }
        new_messages = first_system + [summary_msg] + last_rounds
        ctx.messages.clear()
        ctx.messages.extend(new_messages)
        new_est = _estimate_tokens(ctx.messages)
        console.print(
            f"[bold blue][🛡️ 神盾][/bold blue] 上下文超载 ({estimated} tokens)，"
            f"已触发时空折叠，压缩至 {new_est} tokens。"
        )
        logger.info("[Compaction] %s -> %s tokens, summary_model=%s", estimated, new_est, summary_model)
    except Exception as e:
        logger.warning("[Compaction] 折叠失败，跳过: %s", e)
        # 不抛出，避免影响主流程；仅记录警告


def register_compaction_hook() -> None:
    """注册 Compaction 到 HOOK_BEFORE_LLM_THINK"""
    global_hooks.register(HOOK_BEFORE_LLM_THINK, _compaction_handler)


# 模块加载时自动注册
register_compaction_hook()
