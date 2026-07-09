"""
只读 SubAgent 角色与工具层硬隔离（SSOT）。

内置 ``readonly_*`` 角色在 **白名单裁剪** 与 **assemble_tool_pool** 两层剔除写操作工具；
``run_agent`` 执行期 ``_invoke_work_order_tool_transport`` 再次拦截（防升权/漏网）。

不依赖提示词「请不要改文件」——写类工具对只读 SubAgent **不可见且不可调用**。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

READONLY_ROLE_PREFIX = "readonly_"

# 内置只读角色 id（role 以 readonly_ 开头亦视为只读）
READONLY_BUILTIN_ROLE_IDS: frozenset[str] = frozenset(
    {
        "readonly_explore",
        "readonly_researcher",
        "readonly_analyst",
        "readonly_planner",
    }
)

# Native / 编排类：对只读 SubAgent 一律禁止
NATIVE_WRITE_OR_SIDE_EFFECT_TOOL_IDS: frozenset[str] = frozenset(
    {
        "core:fs_write",
        "fs_write",
        "core:apply_patch",
        "apply_patch",
        "core:apply_patch_rollback",
        "apply_patch_rollback",
        "core:shell_exec",
        "shell_exec",
        "core:shell_job_status",
        "shell_job_status",
        "core:shell_job_cancel",
        "shell_job_cancel",
        "core:submit_background_task",
        "submit_background_task",
        "core:check_background_task",
        "core:local_memory_append",
        "local_memory_append",
        "core:db_write",
        "db_write",
        "delegate",
        "coordinate",
        "core:workflow_run",
        "core:domain_workflow_run",
        "core:pmo_macro_dashboard_push",
    }
)

# 只读 SubAgent 允许的基础工具（各角色从此子集选取）
READONLY_SAFE_TOOL_IDS: frozenset[str] = frozenset(
    {
        "core:fs_read",
        "fs_read",
        "core:local_memory_search",
        "local_memory_search",
        "core:recall_memory",
        "recall_memory",
    }
)

READONLY_EXPLORE_PROMPT = (
    "你是只读探索专员（readonly_explore）。\n"
    "职责：在代码库/文档中**查找与阅读**，回答「在哪里、是什么、怎么串起来」。\n"
    "⛔ **禁止修改**任何文件、禁止执行可能写入磁盘的命令、禁止发消息/投递后台任务。\n"
    "可用：core:fs_read、core:local_memory_search。\n"
    "完成后给出路径清单 + 简要结论；若未找到，明确说明搜索范围与缺口。"
)

READONLY_RESEARCHER_PROMPT = (
    "你是只读研究员（readonly_researcher）。\n"
    "职责：查阅本地资料、整理调研结论；**只读**，不落地修改。\n"
    "⛔ 禁止 fs_write / apply_patch / shell_exec / 任何 MCP 写操作。\n"
    "可用：core:fs_read、core:local_memory_search。\n"
    "输出须标注信息来源（文件路径或记忆检索 query）。"
)

READONLY_ANALYST_PROMPT = (
    "你是只读分析师（readonly_analyst）。\n"
    "职责：阅读数据文件、提炼指标与异常；**只分析、不写入**。\n"
    "⛔ 禁止修改文件或执行 shell；若需计算，基于已读内容推理并标注假设。\n"
    "可用：core:fs_read、core:local_memory_search。\n"
    "输出宜含核心数字、趋势、异常点；表格可用 Markdown。"
)

READONLY_PLANNER_PROMPT = (
    "你是只读规划专员（readonly_planner）。\n"
    "职责：阅读代码/文档，设计实现方案与步骤；**只规划、不动手改**。\n"
    "⛔ 禁止一切写操作与 shell_exec。\n"
    "可用：core:fs_read、core:local_memory_search。\n"
    "方案末尾必须列出 **3–5 个最关键的实现文件路径**（绝对或相对 workspace）。"
)

READONLY_ROLE_PROMPTS: dict[str, str] = {
    "readonly_explore": READONLY_EXPLORE_PROMPT,
    "readonly_researcher": READONLY_RESEARCHER_PROMPT,
    "readonly_analyst": READONLY_ANALYST_PROMPT,
    "readonly_planner": READONLY_PLANNER_PROMPT,
}

READONLY_ROLE_ALLOWED_SKILLS: dict[str, list[str]] = {
    "readonly_explore": ["core:fs_read", "core:local_memory_search"],
    "readonly_researcher": ["core:fs_read", "core:local_memory_search"],
    "readonly_analyst": ["core:fs_read", "core:local_memory_search"],
    "readonly_planner": ["core:fs_read", "core:local_memory_search"],
}

_MCP_WRITE_RAW_NAMES: frozenset[str] = frozenset(
    {
        "write_query",
        "write_records",
        "insert",
        "update",
        "delete",
        "execute",
        "exec",
    }
)


def is_readonly_subagent_role(role: str | None) -> bool:
    """role 为 readonly_* 或内置只读 id 时返回 True。"""
    r = (role or "").strip().lower()
    if not r:
        return False
    if r.startswith(READONLY_ROLE_PREFIX):
        return True
    return r in READONLY_BUILTIN_ROLE_IDS


def _normalize_tool_id(tool_id: str) -> str:
    return (tool_id or "").strip().lower()


def _mcp_raw_name(tool_id: str) -> str:
    tid = _normalize_tool_id(tool_id)
    if tid.startswith("mcp:"):
        return tid[4:].strip().lower()
    return tid


def is_write_or_side_effect_tool(tool_id: str) -> bool:
    """只读 SubAgent 禁止调用的工具（含 MCP 写族与 util 副作用）。"""
    tid = _normalize_tool_id(tool_id)
    if not tid:
        return False
    if tid in NATIVE_WRITE_OR_SIDE_EFFECT_TOOL_IDS:
        return True
    if tid.startswith("util:") or tid.startswith("sys:"):
        # util:lark_send_text 等；sys 亦可能暴露环境（只读场景仍放行 sys:health 需白名单显式允许）
        if tid.startswith("util:"):
            return True
        if tid not in ("sys:health_stats", "sys:list_env_safe"):
            return True
    raw = _mcp_raw_name(tid)
    if raw in _MCP_WRITE_RAW_NAMES:
        return True
    if "write_query" in raw and "read_query" not in raw:
        return True
    # jpp Wasm 技能默认可能含副作用；只读通道仅当白名单显式枚举 jpp:id 时才可见，此处不额外放行
    if tid.startswith("jpp:"):
        # 只读内置角色不应包含 jpp；若动态升权误入，保守拦截
        return True
    return False


def sanitize_allowed_skills_for_readonly(allowed: list[str]) -> list[str]:
    """从白名单剔除写/副作用工具，并仅保留 READONLY_SAFE 交集（显式枚举 jpp/mcp 时仍剔除写族）。"""
    out: list[str] = []
    for raw in allowed:
        s = str(raw).strip()
        if not s:
            continue
        sl = s.lower()
        if sl in ("mcp:*", "native:*", "jpp:*"):
            # 通配符在只读模式下降级：不允许通过通配符引入写工具
            continue
        if is_write_or_side_effect_tool(sl):
            continue
        if sl.startswith("mcp:"):
            raw_name = _mcp_raw_name(sl)
            if raw_name in _MCP_WRITE_RAW_NAMES or "write_query" in raw_name:
                continue
            out.append(s)
            continue
        if sl.startswith("jpp:"):
            continue
        if sl in READONLY_SAFE_TOOL_IDS or sl.replace("core:", "") in {
            x.replace("core:", "") for x in READONLY_SAFE_TOOL_IDS
        }:
            out.append(s)
    # 去重保序
    seen: set[str] = set()
    deduped: list[str] = []
    for x in out:
        xl = x.lower()
        if xl in seen:
            continue
        seen.add(xl)
        deduped.append(x)
    return deduped


def filter_tools_for_readonly_subagent(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """assemble_tool_pool 之后：从工具池物理移除写/副作用项。"""
    kept: list[dict[str, Any]] = []
    dropped: list[str] = []
    for t in tools:
        tid = str(t.get("id") or t.get("label") or "")
        if is_write_or_side_effect_tool(tid):
            dropped.append(tid)
            continue
        kept.append(t)
    if dropped:
        logger.info(
            "[L3 Agent][readonly] 已从只读 SubAgent 工具池剔除 %d 项: %s",
            len(dropped),
            dropped[:12],
        )
    return kept


def readonly_tool_block_observation(tool_id: str) -> str:
    """WorkOrder tool transport 最后一道防线。"""
    return json.dumps(
        {
            "status": "blocked",
            "reason": "readonly_subagent_forbidden",
            "message": (
                f"工具 {tool_id!r} 对只读 SubAgent 不可用（系统层硬隔离）。"
                "请改用 core:fs_read / core:local_memory_search 完成查阅，"
                "或请主 Agent 派 non-readonly 角色执行写操作。"
            ),
            "tool": tool_id,
        },
        ensure_ascii=False,
    )


def parse_readonly_role_from_implicit(implicit: dict[str, Any] | None) -> str:
    if not implicit or not isinstance(implicit, dict):
        return ""
    return str(
        implicit.get("sub_agent_role") or implicit.get("role") or ""
    ).strip().lower()
