"""
统帅行为侧写（Persona）：周期性从 User_Persona/General_Chat 流水提炼浓缩侧写，写入 Core_Profile。

离线任务；失败不应拖死调度器（由调度层捕获并告警）。
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_USER_WING = "User_Persona"
_GENERAL_CHAT_ROOM = "General_Chat"
_CORE_PROFILE_ROOM = "Core_Profile"

_MAX_TRANSCRIPT_CHARS = 120_000

_SYSTEM_PROMPT_ZH = """你是 Jachin AI OS 的顶级人类行为侧写师。请阅读以下近期统帅与 Jachin 的交互记录，提取并更新统帅的【核心行为侧写 (Persona)】。
请从以下几个维度进行极其精炼的总结（只输出客观规律，不讲废话）：
1. 代码与技术偏好（例：喜欢直给代码还是原理解释？讨厌哪些格式？）
2. 沟通与汇报风格（例：喜欢赛博朋克/军武风？喜欢表格还是文字？）
3. 关注的业务核心（例：最近极度关注 Kalaroko E2E 巡检稳定性？）
直接输出 Markdown 格式的侧写总结，这将作为系统的核心规则（System Prompt）永久生效。"""


def _truncate_transcript(raw: str) -> str:
    s = (raw or "").strip()
    if len(s) <= _MAX_TRANSCRIPT_CHARS:
        return s
    head = _MAX_TRANSCRIPT_CHARS // 2
    tail = _MAX_TRANSCRIPT_CHARS - head - 80
    return (
        f"[文本过长已截断：保留首部 {head} 字 + 尾部 {tail} 字]\n\n"
        f"{s[:head]}\n\n…\n\n{s[-tail:]}"
    )


def _persona_litellm_completion(transcript: str) -> str:
    """同步调用 LiteLLM；超时由 kwargs['timeout'] 控制（秒）。"""
    import litellm

    from core.brain.llm.dashscope_regional import litellm_apply_dashscope_credentials

    model = (os.environ.get("JACHIN_PERSONA_PROFILER_MODEL") or "dashscope/qwen-max").strip()
    if model.lower().startswith("qwen") and "/" not in model:
        model = f"dashscope/{model}"

    timeout_sec = float(os.environ.get("JACHIN_PERSONA_PROFILER_TIMEOUT_SEC") or "120")
    timeout_sec = max(60.0, timeout_sec)

    body = _truncate_transcript(transcript)
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _SYSTEM_PROMPT_ZH},
        {
            "role": "user",
            "content": (
                "以下为近期统帅与 Jachin 的交互记录（按时间新近优先排列的多条抽屉文本）。\n\n" + body
            ),
        },
    ]

    kwargs_chat: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.35,
        "max_tokens": 4096,
        "timeout": timeout_sec,
    }
    litellm_apply_dashscope_credentials(model, kwargs_chat, explicit_api_key=None)

    resp = litellm.completion(**kwargs_chat)
    choice = resp.choices[0] if getattr(resp, "choices", None) else None
    if not choice:
        return ""
    msg = getattr(choice, "message", None)
    content = getattr(msg, "content", None) if msg is not None else None
    return str(content or "").strip()


async def generate_weekly_persona_profile() -> dict[str, Any]:
    """
    1. 拉取 General_Chat 近期流水；2. Qwen-Max 提炼侧写；3. 写入 Core_Profile（先清空房间再 commit，保证该房间仅保留最新一份侧写）。
    """
    from l3_client.local_mcps.jachin_memory_nexus.memory_backend import (
        commit_drawer,
        delete_drawers_in_room,
        recall_room,
    )

    res = recall_room(wing=_USER_WING, room=_GENERAL_CHAT_ROOM, limit=100)
    if not res.get("ok"):
        err = res.get("error") or "recall_room failed"
        logger.warning("[persona_profiler] recall General_Chat 失败: %s", err)
        return {"ok": False, "error": err, "stage": "recall"}

    drawers = res.get("drawers") or []
    if not drawers:
        logger.info("[persona_profiler] General_Chat 无数据，跳过侧写")
        return {"ok": True, "skipped": True, "reason": "no_general_chat"}

    chunks: list[str] = []
    for d in drawers:
        if not isinstance(d, dict):
            continue
        t = (d.get("text") or "").strip()
        if t:
            chunks.append(t)
    transcript = "\n\n---\n\n".join(chunks)
    if not transcript.strip():
        return {"ok": True, "skipped": True, "reason": "empty_transcript"}

    try:
        llm_text = await asyncio.to_thread(_persona_litellm_completion, transcript)
    except Exception as e:
        logger.exception("[persona_profiler] LLM 侧写失败: %s", e)
        return {"ok": False, "error": repr(e), "stage": "llm"}

    if not (llm_text or "").strip():
        return {"ok": False, "error": "empty_llm_output", "stage": "llm"}

    try:
        await asyncio.to_thread(delete_drawers_in_room, _USER_WING, _CORE_PROFILE_ROOM)
    except Exception as e:
        logger.warning("[persona_profiler] 清空 Core_Profile 失败（仍将尝试写入）: %s", e)

    try:
        drawer_id = await asyncio.to_thread(
            commit_drawer,
            llm_text,
            _USER_WING,
            _CORE_PROFILE_ROOM,
            {"source": "weekly_persona_profiler", "kind": "commander_persona_markdown"},
        )
    except Exception as e:
        logger.exception("[persona_profiler] commit Core_Profile 失败: %s", e)
        return {"ok": False, "error": repr(e), "stage": "commit"}

    logger.info(
        "[persona_profiler] 侧写已写入 Core_Profile drawer_id=%s chars=%d",
        drawer_id,
        len(llm_text),
    )
    return {"ok": True, "drawer_id": drawer_id, "chars": len(llm_text)}
