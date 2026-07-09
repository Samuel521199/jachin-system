"""Workspace file write-back guard for ReAct compatibility turns."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from l3_node.engine.hooks_pipeline import HOOK_ON_RETRY, PipelineContext, global_hooks

logger = logging.getLogger(__name__)


def user_text_requests_workspace_writeback(text: str) -> bool:
    """Whether the user explicitly asked to write generated content back to a file."""

    t = (text or "").strip()
    if not t:
        return False
    if re.search(
        r"(覆盖|改写|重写|替换|写回).{0,80}(源|原文|文档|文件|该\s*文|该\s*份|本\s*文)",
        t,
        re.I,
    ):
        return True
    if re.search(r"(源|原文|文档|文件).{0,80}(覆盖|改写|重写|替换)", t, re.I):
        return True
    if "将源文档" in t or "将原文" in t or "源文档内容" in t:
        return True
    if re.search(r"(用|将).{0,48}(总结|摘要|提炼|改写|重写).{0,72}(覆盖|写入|保存|写回)", t, re.I):
        return True
    return False


def user_intent_requests_workspace_writeback(messages: list[dict[str, Any]] | None) -> bool:
    """Find write-back intent in real user turns, ignoring injected system nudges."""

    for m in reversed(messages or []):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        t = str(m.get("content") or "").strip()
        if not t:
            continue
        if t.startswith("【") or t.startswith("Observation:"):
            continue
        if not re.search(
            r"[/\\]|\.(?:txt|md|json|csv|py|docx?)\b|workspace|工作区|文件",
            t,
            re.I,
        ):
            continue
        if user_text_requests_workspace_writeback(t):
            return True
    return False


def observation_suggests_workspace_read_ok(tool: str, obs: str) -> bool:
    tl = (tool or "").lower()
    if "fs_read" not in tl and "read_file" not in tl:
        return False
    o = (obs or "").strip()
    if not o:
        return False
    ol = o.lower()
    return not any(
        x in ol
        for x in (
            "securityexception",
            "路径越界",
            "路径无效",
            "-32602",
            "enoent",
            "not found",
            "failed to read",
            "missing_path",
            "invalid arguments",
            "[read_file]",
        )
    )


def observation_suggests_workspace_write_ok(tool: str, base_tool: str, obs: str) -> bool:
    tl = (tool or "").lower()
    bt = (base_tool or "").lower()
    o = (obs or "").strip()
    if not o:
        return False
    ol = o.lower()
    if any(
        x in ol
        for x in (
            "-32602",
            "securityexception",
            "路径越界",
            "路径无效",
            "enoent",
            "errno",
            "error_class",
            '"ok": false',
            '"ok":false',
            "missing_path",
            "invalid arguments",
            "mcp error",
        )
    ):
        return False
    if "fs_write" in tl or "apply_patch" in tl:
        return True
    return bt in ("write_file", "create_file", "edit_file", "search_replace")


def mark_workspace_io_flags(ctx: PipelineContext, tool: str, observation_full: str) -> None:
    """Update read/write evidence flags for later final-answer guards."""

    base_tool = (tool or "").replace("mcp:", "").strip()
    obs = str(observation_full or "")
    if observation_suggests_workspace_read_ok(tool, obs):
        ctx.metadata["_react_did_workspace_read"] = True
    if observation_suggests_workspace_write_ok(tool, base_tool, obs):
        ctx.metadata["_react_did_workspace_write"] = True


def build_writeback_missing_prompt() -> str:
    return (
        "【系统校验】用户要求将总结/提炼后的内容**写回源文件**完成覆盖；"
        "你已通过读类工具（如 core:fs_read、mcp:read_file）取得原文，但尚未执行**写盘**工具完成覆盖。\n"
        "禁止用 core:local_memory_append 或仅口头复述代替写文件。\n"
        "请下一步输出 ReAct：\n"
        "Thought: …\n"
        "Action: core:fs_write（或白名单内的 mcp:write_file / mcp:create_file）\n"
        "Action Input: JSON，须含 path 或 file_path（与用户给出的路径一致，可为绝对路径或相对 ~/.jachin/workspace）"
        "与 content（提炼后的完整替换正文）。工具返回成功后再输出 Final Answer，并明确说明已覆盖该路径。"
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
    if ctx.metadata.get("_react_writeback_guard_retry_done"):
        return False
    if not ctx.metadata.get("_react_did_workspace_read"):
        return False
    if ctx.metadata.get("_react_did_workspace_write"):
        return False
    if not user_intent_requests_workspace_writeback(messages):
        return False
    ctx.metadata["_react_writeback_guard_retry_done"] = True
    ctx.metadata["_retry_reason"] = "workspace_writeback_guard"
    try:
        asyncio.get_running_loop().create_task(global_hooks.run(HOOK_ON_RETRY, ctx))
    except RuntimeError:
        pass
    logger.warning(
        "[CapabilityHook][workspace_writeback] trace=%s via=%s missing write-back; retry injected",
        str(ctx.metadata.get("_react_step_trace") or ""),
        via,
    )
    messages.append({"role": "assistant", "content": response})
    messages.append({"role": "user", "content": build_writeback_missing_prompt()})
    return True
