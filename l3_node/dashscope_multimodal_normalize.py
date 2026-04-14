"""
可选：将 user 多模态从 OpenAI 形转为百炼文档中的 ``{"text"}/{"image"}``（无 ``type``）。

**默认关闭**：LiteLLM 在 ``acompletion`` 前会执行 ``validate_chat_completion_user_messages``，
要求每条 content 块带 ``type``（如 ``text`` / ``image_url``）。转为原生块会导致
``invalid content type=None`` / ``Invalid user message at index N``。

若将来走**不经 LiteLLM 校验**的直连 SDK，可设 ``JACHIN_LITELLM_DASHSCOPE_NATIVE_MULTIMODAL=1`` 试验。
"""
from __future__ import annotations

import os
from copy import deepcopy
from typing import Any

import logging

logger = logging.getLogger(__name__)


def _part_is_openai_multimodal(p: dict[str, Any]) -> bool:
    t = str(p.get("type") or "").strip().lower()
    return t in ("text", "image_url")


def _openai_parts_to_dashscope_native(parts: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in parts:
        if not isinstance(p, dict):
            continue
        t = str(p.get("type") or "").strip().lower()
        if t == "text":
            out.append({"text": str(p.get("text") or "")})
        elif t == "image_url":
            iu = p.get("image_url")
            u = ""
            if isinstance(iu, dict):
                u = str(iu.get("url") or "").strip()
            elif isinstance(iu, str):
                u = iu.strip()
            if u:
                out.append({"image": u})
        elif "image" in p and "type" not in p:
            u = str(p.get("image") or "").strip()
            if u:
                out.append({"image": u})
        elif "text" in p and "type" not in p:
            out.append({"text": str(p.get("text") or "")})
    return out


def _should_convert_user_content(content: Any) -> bool:
    if not isinstance(content, list) or not content:
        return False
    n = sum(1 for p in content if isinstance(p, dict) and _part_is_openai_multimodal(p))
    return n >= 1 and any(
        isinstance(p, dict) and str(p.get("type") or "").strip().lower() == "image_url" for p in content
    )


def maybe_normalize_messages_for_dashscope_litellm(
    messages: list[dict[str, Any]] | None,
    *,
    model: str,
) -> list[dict[str, Any]]:
    """
    返回**新列表**（深拷贝消息 dict），不修改调用方传入的 messages。
    非 dashscope 或未开启时原样返回浅拷贝列表。
    """
    if not messages:
        return []
    raw = os.environ.get("JACHIN_LITELLM_DASHSCOPE_NATIVE_MULTIMODAL", "0").strip().lower()
    if raw not in ("1", "true", "yes", "on"):
        return list(messages)
    ml = (model or "").strip().lower()
    if not ml.startswith("dashscope/"):
        return list(messages)

    out: list[dict[str, Any]] = []
    n_conv = 0
    for m in messages:
        role = (m.get("role") or "").strip()
        c = m.get("content")
        if role == "user" and _should_convert_user_content(c):
            native = _openai_parts_to_dashscope_native(c)  # type: ignore[arg-type]
            if native:
                mm = deepcopy(m)
                mm["content"] = native
                out.append(mm)
                n_conv += 1
                continue
        out.append(m)
    if n_conv:
        logger.info(
            "[L3 LLM] DashScope 多模态：已将 %d 条 user 消息从 OpenAI image_url 格式转为原生 image/text 块（model=%s）",
            n_conv,
            model,
        )
    return out
