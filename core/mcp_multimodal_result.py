"""
MCP 工具返回中的图片块序列化为 RoleExecutionAgent Verification evidence 可解析的 JSON 信封。

stdio MCP（如 mcp-pyautogui-server screenshot）常返回 ImageContent；若仅拼接 text，
主 RoleExecutionAgent 循环无法「看图」。本模块供 core.mcp_client 写出、l3_node.role_execution_observation_vision 读出。
"""
from __future__ import annotations

import base64
import json
import re
from typing import Any

JACHIN_MCP_MULTIMODAL_KEY = "_jachin_mcp_multimodal"
JACHIN_MCP_MULTIMODAL_VERSION = 1


def encode_image_bytes_as_data_url(data: bytes, mime: str = "image/png") -> str:
    m = (mime or "image/png").strip() or "image/png"
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{m};base64,{b64}"


def build_multimodal_observation_payload(
    *,
    text_parts: list[str],
    image_data_urls: list[str],
) -> str:
    """将 MCP 文本 + 图片 data URL 序列化为 Verification evidence 字符串（JSON）。"""
    urls = [u.strip() for u in image_data_urls if isinstance(u, str) and u.strip()]
    text = "\n".join(p.strip() for p in text_parts if (p or "").strip()).strip()
    if not urls:
        return text or "[无输出]"
    payload: dict[str, Any] = {
        JACHIN_MCP_MULTIMODAL_KEY: JACHIN_MCP_MULTIMODAL_VERSION,
        "text": text or None,
        "images": [{"data_url": u} for u in urls[:4]],
    }
    return json.dumps(payload, ensure_ascii=False)


def parse_multimodal_observation_payload(observation: str) -> tuple[str, list[str]]:
    """
    从 Verification evidence 解析多模态信封。

    Returns:
        (text_for_prompt, data_url_list)
    """
    raw = (observation or "").strip()
    if not raw:
        return "", []
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return raw, []
    if not isinstance(obj, dict) or obj.get(JACHIN_MCP_MULTIMODAL_KEY) is None:
        return raw, []
    text = str(obj.get("text") or "").strip()
    urls: list[str] = []
    imgs = obj.get("images")
    if isinstance(imgs, list):
        for it in imgs:
            if isinstance(it, str) and it.strip().lower().startswith("data:image/"):
                urls.append(it.strip())
                continue
            if not isinstance(it, dict):
                continue
            u = it.get("data_url") or it.get("url")
            if isinstance(u, str) and u.strip():
                urls.append(u.strip())
    return text or "[工具返回截图]", urls


def strip_huge_data_urls_from_text(text: str) -> str:
    """从纯文本 Verification evidence 中移除嵌入式 data:image base64，避免重复占满上下文。"""
    if not text or "data:image/" not in text:
        return text
    return re.sub(
        r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\s]{80,}",
        "[data:image 已剥离至多模态 image_url 块]",
        text,
        count=0,
    )
