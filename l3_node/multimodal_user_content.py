"""
将 §12.1 attachments_metadata 转为 LLM user content（文本或 OpenAI 风格 multimodal 数组）。
前端可传 data_url（data:image/...;base64,...）与 text_content（纯文本附件）。
"""
from __future__ import annotations

from typing import Any, List, Union


def build_user_content_for_llm(
    user_input: str,
    attachments_raw: list[dict[str, Any]] | None,
) -> Union[str, List[dict[str, Any]]]:
    """
    - 无附件：返回 user_input（与历史行为一致）。
    - 有附件：返回 content 数组，含 text / image_url 块（LiteLLM/OpenAI 兼容）。
    """
    raw = attachments_raw or []
    if not raw:
        return user_input

    parts: list[dict[str, Any]] = []
    ui = (user_input or "").strip()
    if ui:
        parts.append({"type": "text", "text": ui})

    for att in raw:
        if not isinstance(att, dict):
            continue
        du = att.get("data_url")
        if isinstance(du, str) and du.startswith("data:image"):
            parts.append({"type": "image_url", "image_url": {"url": du}})
            continue
        txt = att.get("text_content")
        if isinstance(txt, str) and txt.strip():
            name = str(att.get("name") or att.get("filename") or "attachment")
            parts.append(
                {
                    "type": "text",
                    "text": f"\n\n---\n【附件·{name}】\n{txt}\n",
                }
            )

    if not parts:
        return user_input
    if len(parts) == 1 and parts[0].get("type") == "text":
        return str(parts[0].get("text") or user_input)
    return parts
