"""
多模态读图轮次的工具策略：避免 mcp:fetch / 联网检索 与「看用户上传的图」任务冲突，
防止会话历史里的 URL、Verification evidence 正文反客为主污染视觉输出。
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def _trim_att(attachments_metadata: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not attachments_metadata:
        return []
    try:
        from l3_node.intent_gateway.sanitize import trim_attachments_metadata_list

        return trim_attachments_metadata_list([x for x in attachments_metadata if isinstance(x, dict)])
    except Exception:
        return [x for x in attachments_metadata if isinstance(x, dict)]


def attachments_include_image(attachments_metadata: list[dict[str, Any]] | None) -> bool:
    for x in _trim_att(attachments_metadata):
        if x.get("has_image") or x.get("is_image"):
            return True
        m = str(x.get("mime") or "").lower()
        if m.startswith("image/"):
            return True
    return False


def _user_explicitly_prioritizes_url_over_image(user_input: str) -> bool:
    """
    用户明确以「给定 http(s) 链接」为主任务（与读上传图无关）时，不剔除 fetch。
    """
    ui = (user_input or "").strip()
    if not ui:
        return False
    if not re.search(r"https?://", ui, re.I):
        return False
    # 明确要求对链接本身动手
    if any(
        k in ui
        for k in (
            "抓取",
            "拉取",
            "打开链接",
            "这个链接",
            "该链接",
            "该 url",
            "该网址",
            "fetch",
            "正文",
            "网页内容",
        )
    ):
        return True
    return False


def tool_id_is_web_pull_family(tid: str) -> bool:
    t = (tid or "").strip().lower()
    if t in ("mcp:fetch", "fetch"):
        return True
    if t.startswith("mcp:") and "tavily" in t:
        return True
    if "web_scraper" in t or t.endswith("atom_web_scraper"):
        return True
    return False


def filter_tools_for_vision_image_turn(
    tools: list[dict[str, Any]],
    *,
    user_input: str,
    attachments_metadata: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], bool]:
    """
    若本轮含图片附件且用户并非「只对某 URL 做网页任务」，则从工具池移除网页抓取/联网检索类工具。

    Returns:
        (filtered_tools, forbid_fetch_runtime) — 后者为 True 时，宿主应对 mcp:fetch 二次拦截。
    """
    if not attachments_include_image(attachments_metadata):
        return tools, False
    if _user_explicitly_prioritizes_url_over_image(user_input):
        return tools, False

    before = len(tools)
    out = [t for t in tools if not tool_id_is_web_pull_family(str(t.get("id") or ""))]
    dropped = before - len(out)
    if dropped:
        logger.info(
            "[L3 Agent][multimodal_tool_policy] 读图轮已移除 %d 个网页拉取类工具（避免与图像输入冲突）",
            dropped,
        )
    return out, True
