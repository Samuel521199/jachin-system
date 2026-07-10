"""Workspace write-back guards owned by the FileExecutor capability layer."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from l3_node.cognitive_kernel.capability_hook_bridge import build_work_order_suggestion
from l3_node.engine.hooks_pipeline import HOOK_ON_RETRY, PipelineContext, global_hooks

logger = logging.getLogger(__name__)

_READ_FLAG = "_work_order_did_workspace_read"
_WRITE_FLAG = "_work_order_did_workspace_write"
_RETRY_FLAG = "_work_order_writeback_guard_retry_done"


def user_text_requests_workspace_writeback(text: str) -> bool:
    """Whether the user explicitly asked to write generated content back to a file."""

    t = (text or "").strip()
    if not t:
        return False
    has_file_hint = bool(re.search(r"[/\\]|\.(?:txt|md|json|csv|py|docx?)\b|workspace|文件|文档|源码", t, re.I))
    has_write_hint = bool(re.search(r"覆盖|改写|重写|替换|写回|写入|保存|update|overwrite|rewrite|replace|save", t, re.I))
    return has_file_hint and has_write_hint


def user_intent_requests_workspace_writeback(messages: list[dict[str, Any]] | None) -> bool:
    """Find write-back intent in real user turns, ignoring injected system nudges."""

    for item in reversed(messages or []):
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        text = str(item.get("content") or "").strip()
        if not text or "jachin-kernel:work-order-suggestion" in text:
            continue
        if user_text_requests_workspace_writeback(text):
            return True
    return False


def observation_suggests_workspace_read_ok(tool: str, obs: str) -> bool:
    tid = (tool or "").lower()
    if "fs_read" not in tid and "read_file" not in tid:
        return False
    text = (obs or "").strip().lower()
    if not text:
        return False
    return not any(
        marker in text
        for marker in (
            "securityexception",
            "enoent",
            "not found",
            "failed to read",
            "missing_path",
            "invalid arguments",
            "mcp error",
            '"ok": false',
            '"ok":false',
        )
    )


def observation_suggests_workspace_write_ok(tool: str, base_tool: str, obs: str) -> bool:
    tid = (tool or "").lower()
    base = (base_tool or "").lower()
    text = (obs or "").strip().lower()
    if not text:
        return False
    if any(
        marker in text
        for marker in (
            "securityexception",
            "enoent",
            "errno",
            "not found",
            "missing_path",
            "invalid arguments",
            "mcp error",
            '"ok": false',
            '"ok":false',
        )
    ):
        return False
    return "fs_write" in tid or "apply_patch" in tid or base in {"write_file", "create_file", "edit_file", "search_replace"}


def mark_workspace_io_flags(ctx: PipelineContext, tool: str, observation_full: str) -> None:
    """Update read/write evidence flags for later final-answer guards."""

    base_tool = (tool or "").replace("mcp:", "").strip()
    obs = str(observation_full or "")
    if observation_suggests_workspace_read_ok(tool, obs):
        ctx.metadata[_READ_FLAG] = True
    if observation_suggests_workspace_write_ok(tool, base_tool, obs):
        ctx.metadata[_WRITE_FLAG] = True


def build_writeback_missing_prompt() -> str:
    return build_work_order_suggestion(
        tool="core:fs_write",
        work_order_input={
            "path": "$requested_source_path",
            "content": "$verified_replacement_content",
            "mode": "overwrite",
        },
        reason="workspace_writeback_missing",
        role_agent="FileExecutorAgent",
        visible_message=(
            "用户要求写回文件，但本轮尚未产生写盘证据；已生成文件写入 WorkOrder 建议，"
            "由认知内核重新裁决、校验路径和覆盖风险。"
        ),
    )


def reject_workspace_writeback_missing_guard(
    ctx: PipelineContext,
    messages: list[dict[str, Any]],
    response: str,
    ans: str,
    *,
    via: str,
) -> bool:
    """Reject a final answer when the requested file write-back has not happened."""

    _ = ans
    if ctx.metadata.get(_RETRY_FLAG):
        return False
    if not ctx.metadata.get(_READ_FLAG):
        return False
    if ctx.metadata.get(_WRITE_FLAG):
        return False
    if not user_intent_requests_workspace_writeback(messages):
        return False

    ctx.metadata[_RETRY_FLAG] = True
    ctx.metadata["_retry_reason"] = "workspace_writeback_guard"
    try:
        asyncio.get_running_loop().create_task(global_hooks.run(HOOK_ON_RETRY, ctx))
    except RuntimeError:
        pass
    logger.warning(
        "[CapabilityHook][workspace_writeback] via=%s missing write-back; WorkOrder suggestion injected",
        via,
    )
    messages.append({"role": "assistant", "content": response})
    messages.append({"role": "user", "content": build_writeback_missing_prompt()})
    return True
