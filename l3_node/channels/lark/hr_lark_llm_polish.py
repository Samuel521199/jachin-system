"""
飞书 HR 通知：用大模型把系统草稿润色成非技术同事能读懂的短消息。

可通过环境变量 JACHIN_HR_LARK_LLM_POLISH=0|false|off 关闭（失败时始终回退为草稿原文）。
可选 JACHIN_HR_LARK_LLM_MODEL 指定模型（否则与 L3 单机引擎一致：L3_MODEL / LLM_MODEL）。
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

_ENGINE: Any = None

_SYSTEM_PROMPT = """你是企业内部「招聘助手」的文案编辑。用户会给你一段发给 HR（人事）的飞书群消息草稿，以及可选的技术背景说明。

要求（必须遵守）：
1. 读者是完全不懂技术、不懂参数名的 HR，用语要像微信工作群一样自然、简短。
2. 只输出最终要发到飞书里的正文，不要标题如「润色后：」，不要代码块围栏（不要用 ```）。
3. 禁止在正文里出现英文参数名、字段名、路径、文件名（如 pending、workflow、PDF、jd.json、Wasm、APScheduler 等）；技术背景仅供你理解，不要复述进正文。
4. 保留草稿里的关键数字与事实（岗位名、份数、分钟数、是否已暂停、要回复哪几个字等），可以改写得更好懂。
5. 篇幅尽量短：一般 8～20 行内，列表用「·」即可；不要写长篇大论。
6. 语气专业、友好，不要用「亲」等电商口吻。"""


def _hr_lark_polish_enabled() -> bool:
    v = (os.environ.get("JACHIN_HR_LARK_LLM_POLISH") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _sync_run_coro(coro: Any, *, timeout: float) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        fut = pool.submit(asyncio.run, coro)
        return fut.result(timeout=timeout)


def _strip_model_artifacts(text: str) -> str:
    s = (text or "").strip()
    if "`</think>`" in s:
        s = s.split("`</think>`", 1)[-1].strip()
    if s.startswith("```"):
        lines = s.split("\n")
        if lines:
            lines = lines[1:]
        while lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    s = re.sub(r"^```[a-zA-Z0-9]*\s*", "", s)
    if s.endswith("```"):
        s = s[: s.rfind("```")].strip()
    return s.strip()


def _build_polish_engine():
    from l3_node.llm_client import LiteLLMEngine, SecurityContext

    ctx = SecurityContext()
    dash = os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("QWEN_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")
    if dash:
        ctx.set_key("dashscope", dash.strip())
    if openai_key:
        ctx.set_key("openai", openai_key.strip())

    fallback = None
    default_model = "gpt-4o-mini"
    if ctx.get_key("dashscope"):
        try:
            from core.llm_provider import DASHSCOPE_ECON_FALLBACK_MODEL

            fallback = [DASHSCOPE_ECON_FALLBACK_MODEL]
        except ImportError:
            fallback = ["dashscope/qwen3.5-flash-2026-02-23"]
        default_model = os.environ.get("LLM_MODEL", "qwen3.5-plus")

    model_name = (os.environ.get("JACHIN_HR_LARK_LLM_MODEL") or os.environ.get("L3_MODEL") or default_model).strip()
    _timeout = float(os.environ.get("LLM_TIMEOUT", "180"))
    return LiteLLMEngine(
        security_context=ctx,
        model_name=model_name,
        fallback_models=fallback,
        timeout=_timeout,
        max_attempts=2,
    )


def _get_engine():
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = _build_polish_engine()
    return _ENGINE


async def _polish_async(
    draft: str,
    *,
    technical_detail: str | None,
    message_kind: str,
) -> str:
    engine = _get_engine()
    kind = (message_kind or "general").strip() or "general"
    tech = (technical_detail or "").strip()
    user_content = (
        f"消息场景类型：{kind}\n\n"
        f"【系统草稿】\n{draft.strip()}\n\n"
        f"【内部技术背景（勿出现在飞书正文）】\n{tech if tech else '（无）'}\n\n"
        "请只输出润色后的飞书正文。"
    )
    raw = await engine.generate_response(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0.35,
        max_tokens=1200,
        l3_call_purpose="hr_lark_polish",
    )
    if isinstance(raw, dict):
        raw = str(raw.get("content") or "")
    return _strip_model_artifacts(str(raw))


def polish_hr_lark_message_sync(
    draft: str,
    *,
    technical_detail: str | None = None,
    message_kind: str = "hr_progress",
) -> str:
    """
    同步入口：内部跑异步 LLM。失败或未启用时返回 draft。
    """
    draft = (draft or "").strip()
    if not draft or not _hr_lark_polish_enabled():
        return draft
    timeout = float(os.environ.get("JACHIN_HR_LARK_LLM_TIMEOUT", "240"))
    try:
        out = _sync_run_coro(
            _polish_async(draft, technical_detail=technical_detail, message_kind=message_kind),
            timeout=timeout,
        )
        out = _strip_model_artifacts(str(out))
        if len(out) < 8:
            logger.warning("[Lark HR] LLM 润色结果过短，使用草稿")
            return draft
        logger.info(
            "[Lark HR] LLM 润色完成 kind=%s draft_chars=%d out_chars=%d",
            message_kind,
            len(draft),
            len(out),
        )
        return out
    except Exception as e:
        logger.warning("[Lark HR] LLM 润色失败，使用草稿: %s", e)
        return draft
