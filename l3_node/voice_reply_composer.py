"""Fast LLM composer for voice ReplyPlan follow-up text.

The rule layer owns the boundary; this module only phrases one short spoken
reply. It is intentionally tool-free and uses a fast/economic Qwen model by
default so voice clarification does not enter the full ReAct agent path.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any


_MD_PREFIX_RE = re.compile(r"^\s*(?:[-*]\s*|#+\s*)+")


def voice_reply_composer_model() -> str:
    raw = (
        os.environ.get("JACHIN_VOICE_REPLY_COMPOSER_MODEL")
        or os.environ.get("VOICE_REPLY_COMPOSER_MODEL")
        or ""
    ).strip()
    if raw:
        return raw if raw.startswith(("dashscope/", "qwen/")) else f"dashscope/{raw}"
    try:
        from core.llm_provider import DASHSCOPE_ECON_FALLBACK_MODEL

        return DASHSCOPE_ECON_FALLBACK_MODEL
    except Exception:
        return "dashscope/qwen3.5-flash"


def _clip_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _clean_spoken_reply(text: str) -> str:
    value = str(text or "").strip()
    value = re.sub(r"```[\s\S]*?```", " ", value).strip()
    value = value.strip("` \t\r\n")
    value = _MD_PREFIX_RE.sub("", value).strip()
    value = re.sub(r"\s+", " ", value).strip()
    if not value:
        return ""
    for sep in ("。", "！", "？", "!", "?"):
        idx = value.find(sep)
        if idx >= 0:
            return value[: idx + 1].strip()
    return value[:80].strip()


def fallback_reply_from_payload(reply_plan: dict[str, Any], fallback_text: str = "") -> str:
    text = str(reply_plan.get("fallback_template") or fallback_text or "").strip()
    return _clean_spoken_reply(text)


def build_fast_composer_messages(reply_plan: dict[str, Any], user_text: str = "") -> list[dict[str, str]]:
    compact_plan = {
        "reply_intent": reply_plan.get("reply_intent"),
        "reason": reply_plan.get("reason"),
        "goal": reply_plan.get("goal"),
        "known_context": reply_plan.get("known_context") or {},
        "missing_slots": reply_plan.get("missing_slots") or [],
        "candidates": reply_plan.get("candidates") or [],
        "constraints": reply_plan.get("constraints") or [],
        "fallback_template": reply_plan.get("fallback_template") or "",
    }
    system = (
        "你是实时语音追问话术生成器。只根据 ReplyPlan 写一句要对用户说的话。"
        "禁止调用工具，禁止执行任务，禁止声称已经完成，禁止补全用户没说的信息。"
        "输出必须是一句自然中文口语，适合 TTS，最多 35 个中文字符，不要 Markdown。"
    )
    user = (
        f"用户原始语音文本：{_clip_text(user_text, 300)}\n"
        f"ReplyPlan：{compact_plan}\n"
        "请输出最终要说的一句话。"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


async def compose_voice_reply_fast(
    *,
    engine: Any,
    reply_plan: dict[str, Any],
    user_text: str = "",
    fallback_text: str = "",
    timeout_sec: float | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    if engine is None:
        fallback = fallback_reply_from_payload(reply_plan, fallback_text)
        return {"ok": bool(fallback), "reply": fallback, "source": "fallback_no_engine"}
    if not isinstance(reply_plan, dict) or not reply_plan:
        return {"ok": False, "reply": "", "source": "none", "error": "empty_reply_plan"}

    try:
        timeout = float(timeout_sec if timeout_sec is not None else os.environ.get("JACHIN_VOICE_REPLY_COMPOSER_TIMEOUT_SEC", "5"))
    except (TypeError, ValueError):
        timeout = 5.0
    timeout = max(0.5, min(timeout, 12.0))
    try:
        tokens = int(max_tokens if max_tokens is not None else os.environ.get("JACHIN_VOICE_REPLY_COMPOSER_MAX_TOKENS", "80"))
    except (TypeError, ValueError):
        tokens = 80
    tokens = max(16, min(tokens, 160))

    model = voice_reply_composer_model()
    messages = build_fast_composer_messages(reply_plan, user_text=user_text)
    started = time.perf_counter()

    async def _call() -> str:
        raw = await engine.generate_response(
            messages,
            tools=None,
            temperature=0.2,
            max_tokens=tokens,
            l3_override_model=model,
            l3_call_purpose="voice_reply_composer_fast",
            extra_body={"enable_thinking": False},
        )
        return str(raw or "")

    try:
        reply = _clean_spoken_reply(await asyncio.wait_for(_call(), timeout=timeout))
    except Exception as exc:
        fallback = fallback_reply_from_payload(reply_plan, fallback_text)
        return {
            "ok": bool(fallback),
            "reply": fallback,
            "source": "fallback_after_fast_error",
            "model": model,
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1),
            "error": f"{type(exc).__name__}: {exc}",
        }

    if not reply:
        fallback = fallback_reply_from_payload(reply_plan, fallback_text)
        return {
            "ok": bool(fallback),
            "reply": fallback,
            "source": "fallback_empty_fast_reply",
            "model": model,
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1),
        }
    return {
        "ok": True,
        "reply": reply,
        "source": "qwen_flash",
        "model": model,
        "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 1),
    }
