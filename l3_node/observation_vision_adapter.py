"""Convert WorkOrder observations into multimodal user messages."""
from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Any, Union

from core.mcp_multimodal_result import (
    encode_image_bytes_as_data_url,
    parse_multimodal_observation_payload,
    strip_huge_data_urls_from_text,
)

logger = logging.getLogger(__name__)

# 与 multimodal_attachments 对齐
_MAX_IMAGE_BYTES = 5 * 1024 * 1024
_MAX_IMAGES_PER_OBS = 2

_ROLE_OBSERVATION_VISION_HINT = (
    "\n\n【截图观测】本条 Verification evidence 附有工具返回的屏幕/页面截图（image_url），"
    "请直接根据图像描述所见并继续 RoleExecutor；不要声称无法读图或缺少视觉能力。"
    "请勿仅因历史中的 http(s) 链接去调用网页抓取替代读图。"
)

# 工具名（去 mcp: 前缀后）或子串匹配
_SCREENSHOT_TOOL_MARKERS = frozenset(
    {
        "screenshot",
        "puppeteer_screenshot",
        "take_screenshot",
        "capture_screenshot",
        "browser_get_state",
        "get_parsed_screen",
        "get_holographic_screen",
    }
)

_IMAGE_PATH_RE = re.compile(
    r"(?:~[/\\]|(?:[A-Za-z]:)?[/\\])[\w\-.\\ /]+?\.(?:png|jpe?g|webp|gif|bmp)",
    re.IGNORECASE,
)


def _tool_raw_name(tool_id: str) -> str:
    t = (tool_id or "").strip().lower()
    if t.startswith("mcp:"):
        t = t[4:].strip()
    return t


def tool_may_return_screenshot(tool_id: str) -> bool:
    raw = _tool_raw_name(tool_id)
    if raw in _SCREENSHOT_TOOL_MARKERS:
        return True
    return "screenshot" in raw or raw.endswith("_screenshot")


def _maybe_resize_image_bytes(data: bytes, mime: str) -> tuple[bytes, str]:
    try:
        from l3_node.intent_gateway.multimodal_attachments import _maybe_resize_image

        return _maybe_resize_image(data, mime)
    except Exception as e:
        logger.debug("[observation_vision_adapter] resize skip: %s", e)
        return data, mime or "image/png"


def _bytes_to_data_url(data: bytes, mime: str = "image/png") -> str | None:
    if not data or len(data) < 32:
        return None
    if len(data) > _MAX_IMAGE_BYTES:
        logger.warning(
            "[observation_vision_adapter] 图片过大已跳过 bytes=%d max=%d",
            len(data),
            _MAX_IMAGE_BYTES,
        )
        return None
    try:
        data, out_mime = _maybe_resize_image_bytes(data, mime)
    except Exception:
        out_mime = mime or "image/png"
    return encode_image_bytes_as_data_url(data, out_mime)


def _load_image_path_as_data_url(path_str: str) -> str | None:
    p = Path(path_str.strip().strip('"').strip("'")).expanduser()
    try:
        if not p.is_file():
            return None
        if p.stat().st_size > _MAX_IMAGE_BYTES:
            logger.warning("[observation_vision_adapter] 路径图片过大: %s", p)
            return None
        raw = p.read_bytes()
        mime = "image/png"
        suf = p.suffix.lower()
        if suf in (".jpg", ".jpeg"):
            mime = "image/jpeg"
        elif suf == ".webp":
            mime = "image/webp"
        elif suf == ".gif":
            mime = "image/gif"
        return _bytes_to_data_url(raw, mime)
    except OSError as e:
        logger.debug("[observation_vision_adapter] 读图失败 path=%s err=%s", p, e)
        return None


def _extract_data_urls_from_observation_text(observation: str, tool_id: str) -> list[str]:
    """从纯文本 / JSON 字段中挖掘 data URL 或本地截图路径。"""
    urls: list[str] = []
    raw = (observation or "").strip()
    if not raw:
        return urls

    # 嵌入式 data:image
    for m in re.finditer(
        r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+",
        raw,
    ):
        u = m.group(0).strip()
        if len(u) > 80 and u not in urls:
            if len(u.encode("utf-8", errors="ignore")) <= _MAX_IMAGE_BYTES * 2:
                urls.append(re.sub(r"\s+", "", u))
        if len(urls) >= _MAX_IMAGES_PER_OBS:
            return urls[:_MAX_IMAGES_PER_OBS]

    # JSON 常见字段
    if raw.startswith("{") or raw.startswith("["):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            for key in (
                "data_url",
                "image_url",
                "screenshot",
                "screenshot_path",
                "path",
                "file_path",
                "local_path",
                "image_path",
            ):
                v = obj.get(key)
                if isinstance(v, str):
                    if v.strip().lower().startswith("data:image/"):
                        urls.append(v.strip())
                    elif tool_may_return_screenshot(tool_id) or key.endswith("path"):
                        du = _load_image_path_as_data_url(v)
                        if du:
                            urls.append(du)
                if len(urls) >= _MAX_IMAGES_PER_OBS:
                    break
            b64 = obj.get("base64") or obj.get("image_base64") or obj.get("data")
            if isinstance(b64, str) and b64.strip() and len(urls) < _MAX_IMAGES_PER_OBS:
                try:
                    data = base64.b64decode(re.sub(r"\s+", "", b64.strip()), validate=False)
                    du = _bytes_to_data_url(data, str(obj.get("mime") or "image/png"))
                    if du:
                        urls.append(du)
                except Exception:
                    pass

    if urls or not tool_may_return_screenshot(tool_id):
        return urls[:_MAX_IMAGES_PER_OBS]

    # 路径正则（PyAutoGUI 等可能只在文本里写保存路径）
    for m in _IMAGE_PATH_RE.finditer(raw):
        du = _load_image_path_as_data_url(m.group(0))
        if du and du not in urls:
            urls.append(du)
        if len(urls) >= _MAX_IMAGES_PER_OBS:
            break

    return urls[:_MAX_IMAGES_PER_OBS]


def _normalize_data_url_for_llm(url: str) -> str:
    """解码 data URL → 可选缩放 → 再编码，控制送入 LLM 的体积。"""
    u = (url or "").strip()
    if not u.lower().startswith("data:image/"):
        return u
    try:
        from l3_node.intent_gateway.multimodal_attachments import _decode_data_image_url

        raw, mime = _decode_data_image_url(u)
        if raw is None:
            return u
        du = _bytes_to_data_url(raw, mime or "image/png")
        return du or u
    except Exception:
        return u


def extract_observation_image_data_urls(
    observation_full: str,
    tool_id: str,
) -> list[str]:
    """
    从工具 Verification evidence（含 MCP 多模态 JSON 信封）提取可送入 LLM 的 data URL 列表。
    """
    text, envelope_urls = parse_multimodal_observation_payload(observation_full)
    urls = [_normalize_data_url_for_llm(u) for u in envelope_urls]
    if len(urls) < _MAX_IMAGES_PER_OBS:
        for u in _extract_data_urls_from_observation_text(
            text if envelope_urls else observation_full,
            tool_id,
        ):
            nu = _normalize_data_url_for_llm(u)
            if nu and nu not in urls:
                urls.append(nu)
            if len(urls) >= _MAX_IMAGES_PER_OBS:
                break
    return urls[:_MAX_IMAGES_PER_OBS]


def build_observation_user_content(
    observation_text: str,
    tool_id: str,
    *,
    followup_builder: Any,
) -> Union[str, list[dict[str, Any]]]:
    """
    构建工具后写入 messages 的 user content。

    followup_builder:  Callable[[str, str], str] — 通常为 role executor follow-up builder
    """
    obs_full = str(observation_text or "")
    data_urls = extract_observation_image_data_urls(obs_full, tool_id)
    if not data_urls:
        return followup_builder(obs_full, tool_id)

    text_body, envelope_urls = parse_multimodal_observation_payload(obs_full)
    if envelope_urls and text_body:
        short_obs = text_body
    else:
        short_obs = strip_huge_data_urls_from_text(obs_full)
    tail = followup_builder(short_obs, tool_id)
    if _ROLE_OBSERVATION_VISION_HINT not in tail:
        tail = f"{tail}{_ROLE_OBSERVATION_VISION_HINT}"

    parts: list[dict[str, Any]] = [{"type": "text", "text": tail}]
    for u in data_urls:
        parts.append({"type": "image_url", "image_url": {"url": u}})
    logger.info(
        "[L3 Agent][observation_vision_adapter] tool=%s 已注入 %d 张截图到 Verification evidence user 消息",
        (tool_id or "")[:80],
        len(data_urls),
    )
    return parts


def observation_display_text_for_emit(
    observation_for_llm: str,
    tool_id: str,
) -> str:
    """Sensory/UI 展示用：避免向界面刷屏 base64。"""
    urls = extract_observation_image_data_urls(observation_for_llm, tool_id)
    if not urls:
        return observation_for_llm
    text, _ = parse_multimodal_observation_payload(observation_for_llm)
    base = strip_huge_data_urls_from_text(text or observation_for_llm)
    if len(base) > 4000:
        base = base[:4000] + "…(截断)"
    return f"{base}\n\n[已附加 {len(urls)} 张截图供模型视觉识别（未在界面重复展示像素数据）]"
