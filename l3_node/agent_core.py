"""
Jachin L3 RoleExecutionAgent transport.

The architecture is the Memory-first Cognitive Kernel described in
``docs/07_memory_first_main_agent_and_voice_app_agents.md``. This module is
only the chat/stream input transport for RoleExecutionAgent and
UserFacingReplyAgent. Every external-world action must be authorized and
executed through DecisionContract -> WorkOrder -> Dispatcher -> RoleExecutor.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Optional

from core.deep_execution_log import (
    log_pipeline_phase,
    log_run_agent_start,
    log_tool_execution,
)
from l3_node.engine.hooks_pipeline import (
    HOOK_AFTER_TOOL_EXEC,
    HOOK_BEFORE_RESPONSE,
    HOOK_BEFORE_TOOL_EXEC,
    HOOK_ON_EXECUTION_BRIEF,
    HOOK_ON_EXPERIENCE_LEARNED,
    HOOK_ON_INTENT_RECEIVED,
    HOOK_ON_MEMORY_COMMIT,
    HOOK_ON_RETRY,
    HOOK_ON_TASK_DECOMPOSE,
    HOOK_ON_TASK_NODE_DONE,
    HOOK_ON_TASK_NODE_START,
    Pipeline,
    PipelineContext,
    global_hooks,
)
from l3_node.llm_client import LiteLLMEngine, RunCancelledError, SecurityContext
from l3_node.capability_catalog import (
    build_capability_prompt_inject_for_tools,
    tools_include_akshare_native,
    tools_include_recruitment,
    tools_include_holographic_ui,
    tools_include_vision_ui,
)
from l3_node.skill_md_hot_reload import (
    HR_SKILL_MD_BODY_END,
    HR_SKILL_MD_BODY_START,
)
from l3_node.routing.intent_signals import (
    user_message_suggests_a_share_analysis,
    user_message_suggests_recruitment_domain,
)
from l3_node.exec_trace import exec_trace
from l3_node.primitives import get_mcp_registry, load_tools, run_tool
from l3_node.primitives.tools.loader import tool_entry_looks_like_sqlite_family
from l3_node.primitives.tools.tool_pool import (
    assemble_tool_pool,
    expand_allowed_skills_with_implicit_sqlite_read,
    expand_allowed_skills_with_local_mcp,
)
from l3_node.intent_gateway.pushback_copy import (
    L3_SERVICE_ETHOS_RETRY_BLOCK,
    L3_SERVICE_ETHOS_RETRY_BLOCK_SLIM,
)
from l3_node.memory_nexus_bridge import schedule_nexus_turn_commit_async
from l3_node.cognitive_kernel.capability_work_order_adapter import try_execute_capability_work_order
from l3_node.cognitive_kernel.direct_mainline import try_execute_cognitive_direct_plan
from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn
from l3_node.cognitive_kernel.pipeline import build_cognitive_turn_context
from l3_node.cognitive_kernel.runtime import close_turn, close_turn_waiting_user

logger = logging.getLogger(__name__)


def _gateway_prior_brief(prior_messages: list[dict[str, Any]], max_chars: int = 1200) -> str:
    """Bounded chat recap helper; main-loop memory enters via MemoryRecallAgent."""
    parts: list[str] = []
    for m in prior_messages[-8:]:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        c = (m.get("content") or "") if isinstance(m.get("content"), str) else str(m.get("content") or "")
        parts.append(f"{role}: {c[:400]}")
    s = "\n".join(parts)
    return s[:max_chars] if len(s) > max_chars else s


_VOICE_EXACT_TEMPLATE_ALIASES: dict[str, str] = {
    "\u4f60\u597d": "hello",
    "\u4f60\u597d\u5440": "hello",
    "\u54c8\u55bd": "hello",
    "hello": "hello",
    "hi": "hello",
    "\u5728\u5417": "available",
    "\u5728\u561b": "available",
    "\u5728\u4e48": "available",
    "\u4f60\u5728\u5417": "available",
    "\u542c\u5f97\u5230\u5417": "can_hear",
    "\u542c\u89c1\u5417": "can_hear",
    "\u8c22\u8c22": "thanks",
    "\u8c22\u4e86": "thanks",
    "\u6ca1\u4e8b": "never_mind",
    "\u7b97\u4e86": "never_mind",
    "\u4e0d\u7528": "never_mind",
    "\u4e0d\u53ef\u4ee5\u4e0d\u53ef\u4ee5": "no_no",
    "\u4e0d\u53ef\u4ee5\u4e0d\u53ef\u4ee5\U0001f614": "no_no",
    "\u8bb2\u8bdd\u8bb2\u8bdd": "hello",
    "\u8bf4\u8bdd\u8bf4\u8bdd": "hello",
    "\u6d63\u72b2\u30bd": "hello",
    "\u9366\u3125\u60a7": "available",
    "\u7481\u8336\u763d\u7481\u8336\u763d": "hello",
    "\u6309\u8bb2\u8bdd": "available",
    "\u6309\u4f4f\u8bb2\u8bdd": "available",
    "\u6309\u7740\u8bb2\u8bdd": "available",
    "\u4e0d\u8bb2\u8bdd": "available",
    "\u4f60\u4e0d\u8bb2\u8bdd": "available",
    "\u4f60\u600e\u4e48\u4e0d\u8bb2\u8bdd": "available",
    "\u4f60\u600e\u4e48\u4e0d\u8bf4\u8bdd": "available",
    "\u8bf4\u8bdd": "available",
    "\u8bb2\u8bdd": "available",
}

_VOICE_EXACT_TEMPLATE_POOLS: dict[str, tuple[str, ...]] = {
    "hello": ("\u5728\u5462", "\u6211\u5728", "\u542c\u7740\u5462", "\u600e\u4e48\u5566"),
    "available": ("\u5728\u5462", "\u968f\u65f6\u5f85\u547d", "\u542c\u7740\u5462", "\u600e\u4e48\u5566"),
    "can_hear": ("\u542c\u5230\u4e86", "\u542c\u5f97\u5f88\u6e05\u695a", "\u5728\u542c", "\u6211\u542c\u89c1\u4e86"),
    "thanks": ("\u4e0d\u5ba2\u6c14", "\u597d\u7684", "\u5c0f\u4e8b", "\u6536\u5230"),
    "never_mind": ("\u597d\uff0c\u542c\u4f60\u7684", "\u597d\u7684", "\u90a3\u5c31\u5148\u653e\u4e00\u653e", "\u6536\u5230"),
    "no_no": ("\u597d\uff0c\u542c\u4f60\u7684", "\u597d\uff0c\u4e0d\u52c9\u5f3a", "\u6536\u5230\uff0c\u6211\u5148\u505c\u4e0b", "\u597d\uff0c\u6211\u660e\u767d\u4e86"),
}


_VOICE_TASK_SIGNAL_RE = re.compile(
    r"lark|feishu|flybook|飞书|打开|启动|切换|发消息|发送|发给|给.+发|"
    r"vivian|ethan|neil|联系人|计算|总结|整理|项目|文件|删除|创建",
    re.I,
)


def _pick_voice_exact_template_reply(text: str, *extra_texts: str) -> str | None:
    joined = "\n".join([str(text or ""), *(str(x or "") for x in extra_texts)])
    if _VOICE_TASK_SIGNAL_RE.search(joined):
        return None
    t = re.sub(r"\s+", "", (text or "").strip())
    if not t:
        return None
    key = _VOICE_EXACT_TEMPLATE_ALIASES.get(t)
    if not key:
        return None
    pool = _VOICE_EXACT_TEMPLATE_POOLS.get(key) or ()
    if not pool:
        return None
    seed = f"{t}|{time.time_ns()}|{uuid.uuid4().hex}".encode("utf-8", errors="ignore")
    idx = int(hashlib.sha256(seed).hexdigest(), 16) % len(pool)
    return pool[idx]


def _schedule_voice_template_turn_commit_async(user_message: str, assistant_reply: str) -> None:
    um = (user_message or "").strip()
    ar = (assistant_reply or "").strip()
    if not um or not ar:
        return
    text = f"User: {um[:12000]}\nJachin: {ar[:12000]}"

    async def _commit() -> None:
        try:
            from l3_client.local_mcps.jachin_memory_nexus.memory_backend import commit_drawer

            await asyncio.to_thread(commit_drawer, text, "User_Persona", "General_Chat")
        except Exception as e:
            logger.debug("[Memory Nexus] voice template turn commit skipped: %s", e, exc_info=True)

    try:
        asyncio.get_running_loop().create_task(_commit())
    except RuntimeError:
        logger.debug("[Memory Nexus] voice template turn commit skipped: no running event loop")


def _max_delegate_depth_cfg() -> int:
    try:
        from l3_node.nexus_config import get_nexus_config

        cfg = get_nexus_config() or {}
        ag = cfg.get("agent") or {}
        return max(0, int(ag.get("max_delegate_depth", 2)))
    except Exception:
        return 2


def _delegate_max_concurrent_cfg() -> int:
    """单次 delegate 最多同时并发运行的子 Agent 数（Semaphore 上限）。
    可通过 nexus_config agent.delegate_max_concurrent 覆盖，默认 4。
    设为 0 表示不限制（仅在已知子任务数较少时建议）。
    """
    try:
        from l3_node.nexus_config import get_nexus_config

        cfg = get_nexus_config() or {}
        ag = cfg.get("agent") or {}
        v = int(ag.get("delegate_max_concurrent", 4))
        return max(0, v)
    except Exception:
        return 4


def _discuss_max_rounds_cfg() -> int:
    """mode: discuss 默认 max_rounds。环境变量 JACHIN_DISCUSS_MAX_ROUNDS 优先；
    否则 nexus_config.json → multi_agent.max_discussion_rounds（默认 3，clamp 1..12）。
    """
    try:
        raw = (os.environ.get("JACHIN_DISCUSS_MAX_ROUNDS") or "").strip()
        if raw:
            return max(1, min(12, int(raw)))
    except Exception:
        pass
    try:
        from l3_node.nexus_config import get_nexus_config

        cfg = get_nexus_config() or {}
        ma = cfg.get("multi_agent") or {}
        return max(1, min(12, int(ma.get("max_discussion_rounds", 3))))
    except Exception:
        return 3


def _discuss_item_max_iterations_cfg() -> int:
    """讨论模式每角色子任务 max_iterations。JACHIN_DISCUSS_ITEM_MAX_ITER 优先；
    否则 multi_agent.discussion_item_max_iterations（默认 3，clamp 1..24）。
    """
    try:
        raw = (os.environ.get("JACHIN_DISCUSS_ITEM_MAX_ITER") or "").strip()
        if raw:
            return max(1, min(24, int(raw)))
    except Exception:
        pass
    try:
        from l3_node.nexus_config import get_nexus_config

        cfg = get_nexus_config() or {}
        ma = cfg.get("multi_agent") or {}
        return max(1, min(24, int(ma.get("discussion_item_max_iterations", 3))))
    except Exception:
        return 3


def _intent_surface_for_experience(ctx: Any, messages: list[dict[str, Any]] | None) -> str:
    ui = (getattr(ctx, "intent", None) or "").strip()
    if ui:
        return ui[:8000]
    for _m in reversed(messages or []):
        if isinstance(_m, dict) and _m.get("role") == "user":
            return str(_m.get("content") or "").strip()[:4000]
    return ""


def _schedule_multi_agent_experience_record(
    ctx: Any,
    *,
    kind: Literal["discuss", "parallel_delegate"],
    intent_surface: str,
    payload: dict[str, Any],
) -> None:
    """异步落盘 multi_agent 经验并触发 HOOK_ON_EXPERIENCE_LEARNED（失败静默）。"""
    try:
        from l3_node.experience_memory import (
            experience_multi_agent_record_enabled,
            save_multi_agent_episode,
        )

        if not experience_multi_agent_record_enabled():
            return

        async def _go() -> None:
            try:

                def _write() -> None:
                    save_multi_agent_episode(
                        kind=kind,
                        intent_surface=intent_surface,
                        payload=payload,
                    )

                await asyncio.to_thread(_write)
            except Exception:
                return
            try:
                _hc = PipelineContext(
                    intent=(intent_surface or "")[:4000],
                    source="l3_agent",
                    run_id=str(getattr(ctx, "run_id", None) or ""),
                    metadata={"executed_tool": f"multi_agent:{kind}", "path": "multi_agent_episode"},
                )
                await global_hooks.run(HOOK_ON_EXPERIENCE_LEARNED, _hc)
            except Exception:
                pass

        asyncio.create_task(_go())
    except Exception:
        pass


def _llm_token_budget_for_run(delegate_depth: int) -> int | None:
    try:
        from l3_node.nexus_config import get_nexus_config

        cfg = get_nexus_config() or {}
        ag = cfg.get("agent") or {}
        key = "sub_agent_max_total_tokens" if delegate_depth > 0 else "main_max_total_tokens"
        v = ag.get(key)
        if v is None:
            return 190_000 if delegate_depth > 0 else None
        vi = int(v)
        return None if vi <= 0 else vi
    except Exception:
        return 190_000 if delegate_depth > 0 else None



# L4：数据/MCP 库操作 SOP（与业务语义层 YAML 配套；见 docs/07_memory_first_main_agent_and_voice_app_agents.md）
_L4_AGENT_SOP_PROBE_MAP_EXECUTE = """【L4 智能体 SOP 法则】：当你处理数据查询、MCP 数据库操作或模糊业务词汇（如「缺货」「最贵」）时，绝对禁止直接生成最终的 SQL 或代码。你必须严格按以下三步执行：

<probe>：若不清楚表结构，必须先调用 mcp:list_tables 或相关只读工具探查真实 Schema。

<map>：结合查到的 Schema 和上方的【业务语义层字典】，在 <thinking> 标签内写出你的逻辑推导过程。

<execute>：最后才能调用 write_query 或 read_query 执行动作。

4. <continuous-tool-execution>（后台连续执行）：如果统帅的指令包含【先查询数据、后修改数据】的复杂目标，你**必须在本次思考链路中，连续、依次地调用工具**直至彻底完成！
**致命禁令**：绝对禁止在只完成查询步骤后，就输出 User-facing result 宣告中断或要求统帅下达下一步指令！
**正确流程**：
步骤 1：生成 WorkOrder（`mcp:read_query` 等）查数据。
步骤 2：收到系统的 Verification evidence 数据后，继续在脑内思考，并紧接着输出新的 WorkOrder（`mcp:write_query` 等）执行修改。
只有当所有的修改动作都已成功执行，并且拿到最后的成功 Verification evidence 后，你才能输出 User-facing result 向统帅汇报最终战果。

5. <proactive-journaling>（主动记忆与规划更新）：当你完成了一个极其复杂的跨会话任务（例如重构了代码、排查了深度 Bug、完成了数据清洗），在输出 User-facing result 之前，你**必须主动**考虑当前工作区的规划状态。如果有必要，请先调用 `core:fs_write` 或 `core:apply_patch` 工具，主动更新工作区中的 `progress.md` 或 `task_plan.md`，记录下你的最新进展和踩坑心得，然后再向统帅汇报。"""


# L5 动态上下文路由：长线任务关键词（提升 task_plan / progress 后缀保活 rank）
_MEMORY_ROUTE_LONG_HORIZON_CN = (
    "总结",
    "规划",
    "项目进度",
    "大纲",
    "路线图",
    "里程碑",
    "跨会话",
    "长期任务",
    "阶段性",
    "task_plan",
    "progress.md",
    "task_plan.md",
)
_MEMORY_ROUTE_LONG_HORIZON_ASCII = ("roadmap", "milestone", "sprint plan", "project status")
# 短平快：跳过磁盘规划上下文注入，把后缀预算让给语义层与经验 Few-Shot
_MEMORY_ROUTE_SHORT_HOP_CN = (
    "查数据库",
    "数据库里",
    "sqlite",
    "写一条sql",
    "改个配置",
    "改一下配置",
    "单个配置",
    "什么是",
    "解释一下",
    "一句话",
    "知识点",
    "查表",
    "表结构",
    "几条数据",
)
_MEMORY_ROUTE_SHORT_HOP_ASCII = (
    "select ",
    "pragma ",
    "read_query",
    "write_query",
    "show tables",
    "sqlite",
)


def _memory_attention_route_mode(user_text: str) -> str:
    """
    L5 Attention Budgeting：纯启发式，不调用 LLM。
    返回 'long' | 'short' | 'default'。
    """
    t = (user_text or "").strip()
    if not t:
        return "default"
    low = t.lower()
    for k in _MEMORY_ROUTE_LONG_HORIZON_CN:
        if k in t:
            return "long"
    for k in _MEMORY_ROUTE_LONG_HORIZON_ASCII:
        if k in low:
            return "long"
    for k in _MEMORY_ROUTE_SHORT_HOP_CN:
        if k in t:
            return "short"
    for k in _MEMORY_ROUTE_SHORT_HOP_ASCII:
        if k in low:
            return "short"
    return "default"


def _tools_include_sqlite_mcp(tools: list[dict[str, Any]] | None) -> bool:
    """是否可见 MCP SQLite（与 loader.tool_entry_looks_like_sqlite_family 一致）。"""
    return any(tool_entry_looks_like_sqlite_family(t) for t in (tools or []))


def _tools_include_workspace_filesystem_for_prompt(tools: list[dict[str, Any]] | None) -> bool:
    """是否可见工作区文件类工具，用于注入「须实地列目录、勿信被动记忆里的目录快照」铁律。"""
    if not tools:
        return False
    needles = (
        "write_file",
        "list_directory",
        "edit_file",
        "read_text_file",
        "read_multiple_files",
        "search_files",
        "get_file_info",
        "move_file",
        "directory_tree",
        "list_allowed_directories",
        "fs_write",
        "fs_read",
        "shell_exec",
    )
    for t in tools:
        if not isinstance(t, dict):
            continue
        tid = str(t.get("id") or "").lower()
        if any(n in tid for n in needles):
            return True
    return False


def _last_non_system_user_text(messages: list[dict[str, Any]], *, max_scan: int = 32) -> str:
    """
    从对话末尾向前取最近一条「真实用户」正文。
    跳过 RoleExecutionAgent 续跑时注入的 user 块（以【系统校验】等开头），避免把纠偏文案当成「最新用户意图」。
    """
    seen_user = 0
    for m in reversed(messages or []):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        seen_user += 1
        if seen_user > max_scan:
            break
        c = str(m.get("content") or "").strip()
        if not c:
            continue
        if c.startswith(("【系统校验·SQLite】", "【系统校验】", "【系统纠偏】", "【strict】")):
            continue
        return c
    return ""


def _user_text_requests_workspace_sqlite_verification(text: str) -> bool:
    """用户本轮是否在问工作区内 SQLite/库存等须工具核验的事实。"""
    t = (text or "").strip()
    if not t:
        return False
    tl = t.lower()
    if ".sqlite" in tl or "test_db" in tl:
        return True
    if re.search(r"sqlite|\.db\b", tl) and re.search(r"工作区|workspace|查一下|查询|查库", t, re.I):
        return True
    if re.search(r"工作区|workspace", t, re.I) and re.search(r"数据库|缺货|库存", t, re.I):
        return True
    return False


from l3_node.capability_agent_hooks import (
    apply_capability_metadata_seed,
    capability_publisher_tool_lock_enabled,
)
from l3_node.capability_policies.hr_recruitment import (
    answer_claims_job_published as _hr_policy_answer_claims_job_published,
    answer_claims_unmanned_scheduler_running as _hr_policy_answer_claims_unmanned_scheduler_running,
)



def _hr_thread_forbids_atom_post(messages: list | None) -> bool:
    """预检 3b / 分支 B 等已在 user 消息里写明：禁止 atom_post_job_boss。"""
    if not messages:
        return False
    markers = (
        "当前表述仅为 **收网/打招呼/调度参数**",
        "与 Boss **发帖**无关",
        "【系统·分支B】当前为「已有岗位·轻量收网」",
        "**禁止**调用 mcp:atom_post_job_boss",
    )
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        c = msg.get("content") or ""
        if any(m in c for m in markers):
            return True
    return False


def _hr_active_jd_marked_published_on_disk() -> bool:
    """指针或 fallback 对应的 jd.json 是否已标记发帖成功（声称已发布不算幻觉）。"""
    from pathlib import Path as _Path

    paths: list = []
    try:
        from l3_node.local_memory import get_hr_recruitment_workflow_pointer

        jp = (get_hr_recruitment_workflow_pointer().get("jd_config_path") or "").strip()
        if jp:
            paths.append(_Path(jp))
    except Exception:
        pass
    try:
        jd = _load_last_jd_pending()
        if isinstance(jd, dict):
            jt = (jd.get("job_title") or "").strip()
            if jt:
                from l3_node.hr_loader import _get_hr_recruitment_plugin_root

                pr = _get_hr_recruitment_plugin_root()
                if pr and (pr / "tools" / "hr_data_paths.py").exists():
                    import sys as _sys

                    ps = str(pr.resolve())
                    if ps not in _sys.path:
                        _sys.path.insert(0, ps)
                    from tools.hr_data_paths import (
                        get_job_jd_path_by_folder_key,
                        resolve_recruitment_data_folder_key,
                    )

                    _fk = resolve_recruitment_data_folder_key(jd_doc=jd, job_title=jt)
                    if _fk:
                        paths.append(_Path(get_job_jd_path_by_folder_key(_fk)))
    except Exception:
        pass
    for path in paths:
        if not path.exists():
            continue
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        try:
            from l3_node.hr_loader import _get_hr_recruitment_plugin_root as _gr

            _pr = _gr()
            if _pr and _pr.exists():
                import sys as _sys

                _ps = str(_pr.resolve())
                if _ps not in _sys.path:
                    _sys.path.insert(0, _ps)
                from tools.hr_data_paths import jd_boss_post_marked_published

                if jd_boss_post_marked_published(doc):
                    return True
        except Exception:
            if doc.get("boss_post_published") is True:
                return True
    return False


def _hr_skip_force_atom_post_hallucination_guard(messages: list | None, _ctx: Any) -> bool:
    """为真时：不要求助手「本轮必须再调 atom_post」才能结束（收网/调度语境或 jd 已标记发帖）。"""
    if _hr_thread_forbids_atom_post(messages):
        return True
    if _hr_branch_b_recruitment_context(messages):
        return True
    if _hr_active_jd_marked_published_on_disk():
        return True
    return False


def _apply_hr_recruitment_final_answer_table_sync(text: str, ctx: Any) -> str:
    """
    若本轮已成功执行 add_automated_recruitment_task，将 User-facing result 里 Markdown 表中
    「收网目标 / 自动分析阈值 / 透析阈值」等行的份数统一为工具返回的同一数值，避免模型杜撰不一致。
    """
    s = (text or "").strip()
    if not s or "|" not in s:
        return text or ""
    tools = getattr(ctx, "_executed_tools_this_run", None) or set()
    if "add_automated_recruitment_task" not in tools:
        return text or ""
    try:
        import l3_node.primitives.mcp.registry as _mr

        payload = getattr(_mr, "last_add_automated_recruitment_task_payload", None)
    except Exception:
        payload = None
    if not isinstance(payload, dict) or not payload.get("ok"):
        return text or ""
    try:
        n = int(payload.get("resume_collect_target") or payload.get("analyze_threshold") or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return text or ""
    val = f"{n} 份"
    # Markdown 表：| 收网目标 | 5 份 |  —— 替换第二列单元格（长标签优先，避免「分析阈值」误伤「自动分析阈值」行）
    row_labels = (
        "自动分析阈值",
        "简历收集目标",
        "收网目标",
        "透析阈值",
        "分析阈值",
    )
    out_lines: list[str] = []
    for line in s.split("\n"):
        stripped = line.strip()
        if "|" not in stripped:
            out_lines.append(line)
            continue
        new_line = line
        for label in row_labels:
            if label not in stripped:
                continue
            if label == "分析阈值" and "自动分析阈值" in stripped:
                continue
            # 允许 | **收网目标** | 5 份 |
            el = re.escape(label)
            pat = rf"(\|\s*(?:\*\*)?\s*{el}\s*(?:\*\*)?\s*\|\s*)([^|]+)(\|)"

            def _repl(m: re.Match, v: str = val) -> str:
                return f"{m.group(1)}{v}{m.group(3)}"

            new_line = re.sub(pat, _repl, new_line, count=1, flags=re.IGNORECASE)
        out_lines.append(new_line)
    return "\n".join(out_lines)


def _hr_recruitment_success_answer(ctx: Any, ans: str) -> bool:
    """招聘流程成功收尾：含发帖成功，或仅启动无人值守/收网（已有在招岗位，无需发帖）。"""
    if _hr_policy_answer_claims_job_published(ans):
        return True
    if "极速测试模式" in (ans or "") or "TASK_AUTO" in (ans or ""):
        return True
    tools = getattr(ctx, "_executed_tools_this_run", None) or []
    if "add_automated_recruitment_task" in tools and any(
        k in (ans or "")
        for k in ("无人值守", "收网", "抓简历", "调度", "已添加", "自动化招聘", "已启动")
    ):
        return True
    if "hr_scheduler_send_confirm_prompt" in tools and any(
        k in (ans or "")
        for k in ("飞书", "调度", "同意调度", "定时任务", "无人值守", "参数")
    ):
        return True
    return False


MAX_KERNEL_TRANSPORT_ITERATIONS = 8
NATIVE_TOOL_IDS = (
    "core:fs_read",
    "core:fs_write",
    "core:shell_exec",
    "core:shell_job_status",
    "core:shell_job_cancel",
    "core:apply_patch",
    "core:apply_patch_rollback",
    "core:workflow_run",
    "core:shell_hitl_approve",
    "core:local_memory_search",
    "core:local_memory_append",
)
RECALL_MEMORY_TOOL_ID = "recall_memory"
COORDINATE_TOOL_ID = "coordinate"

from l3_node.primitives.multi_agent.verification_agent import (
    VERIFICATION_SYSTEM_PROMPT,
    VERIFICATION_TOOLS_WITH_EXEC,
)
from l3_node.primitives.multi_agent.readonly_agent import (
    READONLY_ROLE_ALLOWED_SKILLS,
    READONLY_ROLE_PROMPTS,
)

# 子 Agent 角色预设（分身时使用）
SUB_AGENT_PROMPTS: dict[str, str] = {
    "coder": (
        "你是资深程序员，只负责编写代码。"
        "使用 core:fs_read 读取文件，core:fs_write 写入代码，core:shell_exec 运行测试命令。"
        "完成后输出「代码已写入」及关键修改点摘要。"
    ),
    "writer": (
        "你是技术文档工程师，只负责撰写或更新文档。"
        "使用 core:fs_read 读取参考资料，core:fs_write 写入文档内容。"
        "完成后输出「文档已更新」及内容要点。"
    ),
    "researcher": (
        "你是研究员，负责查阅、分析和收集信息。"
        "使用 core:fs_read 读取本地文件，core:shell_exec 执行查询命令。"
        "完成后以结构化格式输出调研结论与数据来源。"
    ),
    "analyst": (
        "你是数据分析师，专注于数据读取、统计与洞察提炼。"
        "使用 core:fs_read 读取 CSV/JSON 数据，core:shell_exec 运行分析脚本。"
        "完成后输出核心指标、趋势与异常点摘要，尽量使用表格格式。"
    ),
    "planner": (
        "你是任务规划专家，负责将复杂任务拆解为可执行的子步骤并评估依赖关系。"
        "使用 core:fs_read 读取相关文档和代码，理解上下文。"
        "完成后以有序列表格式输出：任务步骤、每步的前置条件、预期产出和潜在风险。"
    ),
    "reviewer": (
        "你是代码审查专家，负责检查代码质量、安全性和可维护性。"
        "使用 core:fs_read 读取待审查文件，core:shell_exec 运行静态检查工具。"
        "完成后按「严重/警告/建议」三级分类输出审查意见，并标注具体行号或文件路径。"
        "（若需对抗性验证交付物是否真的能 work，请使用 role=verification 而非 reviewer。）"
    ),
    "verification": VERIFICATION_SYSTEM_PROMPT,
    "readonly_explore": READONLY_ROLE_PROMPTS["readonly_explore"],
    "readonly_researcher": READONLY_ROLE_PROMPTS["readonly_researcher"],
    "readonly_analyst": READONLY_ROLE_PROMPTS["readonly_analyst"],
    "readonly_planner": READONLY_ROLE_PROMPTS["readonly_planner"],
    "summarizer": (
        "你是文档摘要专家，负责从大量文本中提炼关键信息。"
        "使用 core:fs_read 读取文件内容。"
        "完成后输出：核心要点（3-5条）、关键数字/日期、需要关注的风险或待办事项。"
    ),
    "data_processor": (
        "你是数据处理专家，负责数据清洗、格式转换和批量处理。"
        "使用 core:fs_read 读取原始数据，core:fs_write 写出处理结果，core:shell_exec 运行数据处理脚本。"
        "完成后输出：处理记录数、成功/失败条数，以及输出文件路径。"
    ),
    "tester": (
        "你是测试工程师，负责编写和执行测试用例，验证功能正确性。"
        "使用 core:fs_read 读取代码和测试文件，core:shell_exec 执行测试命令，core:fs_write 写入测试报告。"
        "完成后输出：测试通过/失败数量，失败用例详情与复现步骤。"
    ),
    "critic": (
        "你是严格的评审者与批评者，负责查找方案漏洞、风险与遗漏。"
        "以只读工具核对事实为主；输出须含若干条「质疑点」及风险等级（高/中/低）。"
    ),
    "executor": (
        "你是执行专家：少复述、多行动，优先直接调用工具完成子任务并给出结果摘要。"
    ),
    "domain_expert": (
        "你是领域专家：严格依据任务描述中的业务上下文作答；不确定处明确说明，禁止臆造。"
    ),
    "default": (
        "你是专业助手，完成指定子任务。"
        "可用工具：core:fs_read、core:fs_write、core:shell_exec、core:shell_job_status（查后台任务）。"
        "任务完成后给出简洁的执行摘要。"
    ),
}

# 子 Agent 独立工具集（按角色裁剪，绝不给发邮件等敏感技能）
SUB_AGENT_ALLOWED_SKILLS: dict[str, list[str]] = {
    "coder": [
        "core:fs_read", "core:fs_write", "core:apply_patch",
        "core:shell_exec", "core:shell_job_status", "core:shell_job_cancel",
    ],
    "writer": ["core:fs_read", "core:fs_write"],
    "researcher": ["core:fs_read", "core:shell_exec", "core:shell_job_status", "core:shell_job_cancel"],
    "analyst": [
        "core:fs_read", "core:shell_exec", "core:shell_job_status",
        "core:local_memory_search",
    ],
    "planner": ["core:fs_read", "core:local_memory_search"],
    "reviewer": ["core:fs_read", "core:shell_exec", "core:shell_job_status"],
    "verification": list(VERIFICATION_TOOLS_WITH_EXEC),
    "readonly_explore": list(READONLY_ROLE_ALLOWED_SKILLS["readonly_explore"]),
    "readonly_researcher": list(READONLY_ROLE_ALLOWED_SKILLS["readonly_researcher"]),
    "readonly_analyst": list(READONLY_ROLE_ALLOWED_SKILLS["readonly_analyst"]),
    "readonly_planner": list(READONLY_ROLE_ALLOWED_SKILLS["readonly_planner"]),
    "summarizer": ["core:fs_read", "core:local_memory_search"],
    "data_processor": [
        "core:fs_read", "core:fs_write",
        "core:shell_exec", "core:shell_job_status", "core:shell_job_cancel",
    ],
    "tester": [
        "core:fs_read", "core:fs_write",
        "core:shell_exec", "core:shell_job_status", "core:shell_job_cancel",
    ],
    "critic": ["core:fs_read", "core:local_memory_search"],
    "executor": [
        "core:fs_read", "core:fs_write",
        "core:shell_exec", "core:shell_job_status", "core:shell_job_cancel",
    ],
    "domain_expert": ["core:fs_read", "core:shell_exec", "core:shell_job_status", "core:local_memory_search"],
    "default": [
        "core:fs_read", "core:fs_write",
        "core:shell_exec", "core:shell_job_status", "core:shell_job_cancel",
    ],
}

# 子 Agent 注册表：sub_agent_id -> SubAgent 实例，供复用
_sub_agent_registry: dict[str, "SubAgent"] = {}


def _is_hallucinated_weather_service_error_json(text: str) -> bool:
    """
    模型未调用 util:get_weather_lite，却输出仿 API 的 {"status":"error","message":"天气服务…"}。
    真实工具包装为 {"ok": true|false, "result"|"error": ...}，不会使用顶层 status+message 这种形态。
    """
    s = (text or "").strip()
    if len(s) > 4000:
        return False
    if not (s.startswith("{") and s.endswith("}")):
        return False
    try:
        o = json.loads(s)
    except json.JSONDecodeError:
        return False
    if not isinstance(o, dict):
        return False
    # 真实 run_tool 包装
    if "ok" in o:
        return False
    if str(o.get("status") or "").lower() != "error":
        return False
    msg = str(o.get("message") or "")
    sug = str(o.get("suggestion") or "")
    if ("天气" in msg or "weather" in msg.lower()) and (
        "不可用" in msg or "暂时" in msg or "无法获取" in msg or "无法查询" in msg
    ):
        return True
    if "wttr" in sug.lower() and "curl" in sug.lower():
        return True
    return False


def _infer_mcp_write_path_from_user_messages(messages: list) -> str | None:
    """
    write_file/create_file 常漏传 path。从最近一条用户消息推断工作区相对路径（与 fetch 的 URL 补全同思路）。
    """
    blob = ""
    for m in reversed(messages or []):
        if isinstance(m, dict) and m.get("role") == "user":
            blob = str(m.get("content") or "")
            if blob.strip():
                break
    if not blob.strip():
        return None
    m_py = re.search(r"\b([A-Za-z0-9_][A-Za-z0-9_\-]*\.py)\b", blob)
    if not m_py:
        return None
    fn = m_py.group(1)
    use_scripts = bool(
        re.search(
            r"(?:scripts|名为\s*scripts|文件夹.*scripts|scripts\s*文件夹|在\s*scripts|scripts\s*目录)",
            blob,
            re.I,
        )
    )
    if use_scripts:
        return f"scripts/{fn}"
    return fn


def _extract_jd_config_from_conversation(messages: list, current_response: str) -> str:
    """
    从对话中提取 HR 确认的 JD 配置。优先当前回复，其次历史 assistant 消息。
    支持多种格式：```json、裸 JSON、含 job_title 的任意 JSON 块。
    """
    def _find_jd_json(text: str) -> dict | None:
        if not text or not isinstance(text, str):
            return None
        # 1. ```json ... ``` 或 ``` ... ```（括号非贪婪，匹配配对）
        for pattern in (r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", r"```\s*(\{[\s\S]*?\})\s*```"):
            for m in re.finditer(pattern, text):
                try:
                    raw = m.group(1).strip()
                    obj = json.loads(raw)
                    if isinstance(obj, dict) and (obj.get("job_title") or obj.get("jd_full")):
                        return obj
                except json.JSONDecodeError:
                    pass
        # 2. 裸 { ... } 按花括号配对提取（支持嵌套）
        depth = 0
        start = -1
        for i, c in enumerate(text):
            if c == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0 and start >= 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                        if isinstance(obj, dict) and (obj.get("job_title") or obj.get("jd_full")):
                            return obj
                    except json.JSONDecodeError:
                        pass
                    start = -1
        # 3. 按 "job_title" 定位后向前找 {，向后找配对的 }
        idx = text.find('"job_title"')
        if idx < 0:
            idx = text.find("'job_title'")
        if idx >= 0:
            for start in range(idx, max(-1, idx - 500), -1):
                if text[start] == "{":
                    depth = 1
                    for j in range(start + 1, min(len(text), start + 8000)):
                        if text[j] == "{":
                            depth += 1
                        elif text[j] == "}":
                            depth -= 1
                            if depth == 0:
                                try:
                                    obj = json.loads(text[start : j + 1])
                                    if isinstance(obj, dict) and (obj.get("job_title") or obj.get("jd_full")):
                                        return obj
                                except json.JSONDecodeError:
                                    pass
                                break
                    break
        return None

    jd = _find_jd_json(current_response or "")
    if jd:
        return json.dumps({"jd_config": jd}, ensure_ascii=False)
    for msg in reversed(messages or []):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            content = msg.get("content") or ""
            jd = _find_jd_json(content)
            if jd:
                return json.dumps({"jd_config": jd}, ensure_ascii=False)
    return ""


def _jd_config_dict_from_conversation(messages: list, current_response: str = "") -> dict | None:
    """从对话中解析 HR 待确认的 JD 对象（含 job_title / jd_full）；无则 None。"""
    raw = _extract_jd_config_from_conversation(messages, current_response)
    if not raw:
        return None
    try:
        w = json.loads(raw)
        jd = w.get("jd_config") if isinstance(w, dict) else None
        return jd if isinstance(jd, dict) else None
    except Exception:
        return None


_JACHIN_ROOT = Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin"))).resolve()
LAST_JD_PENDING_PATH = _JACHIN_ROOT / "l3_last_jd_pending.json"
JD_PENDING_BY_CHAT_PATH = _JACHIN_ROOT / "memory" / "l3_jd_pending_by_chat.json"
_JD_PENDING_TTL_SEC = 7200  # 2 小时内有效


def _read_global_jd_pending_file() -> dict | None:
    """仅读全局 ``l3_last_jd_pending.json``（无 chat 回退 / 桌面端）。"""
    if not LAST_JD_PENDING_PATH.exists():
        return None
    try:
        data = json.loads(LAST_JD_PENDING_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        jd = data.get("jd_config")
        ts = data.get("updated_at", 0)
        if isinstance(jd, dict) and (jd.get("job_title") or jd.get("jd_full")):
            age = time.time() - ts if ts else 999999
            if age < _JD_PENDING_TTL_SEC:
                return jd
    except Exception as e:
        logger.debug("[Agent] 加载全局 last_jd_pending 失败: %s", e)
    return None


def _jd_pending_by_chat_read_store() -> dict[str, Any]:
    if not JD_PENDING_BY_CHAT_PATH.exists():
        return {}
    try:
        data = json.loads(JD_PENDING_BY_CHAT_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _jd_pending_by_chat_write_store(data: dict[str, Any]) -> None:
    JD_PENDING_BY_CHAT_PATH.parent.mkdir(parents=True, exist_ok=True)
    JD_PENDING_BY_CHAT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _jd_pending_by_chat_get(cid: str) -> dict | None:
    if not (cid or "").strip():
        return None
    entry = _jd_pending_by_chat_read_store().get(cid.strip())
    if not isinstance(entry, dict):
        return None
    jd = entry.get("jd_config")
    ts = entry.get("updated_at", 0)
    if isinstance(jd, dict) and (jd.get("job_title") or jd.get("jd_full")):
        age = time.time() - ts if ts else 999999
        if age < _JD_PENDING_TTL_SEC:
            return jd
    return None


def _jd_pending_by_chat_set(cid: str, jd_config: dict) -> None:
    data = _jd_pending_by_chat_read_store()
    data[cid.strip()] = {"jd_config": jd_config, "updated_at": time.time()}
    _jd_pending_by_chat_write_store(data)


def _jd_pending_by_chat_delete(cid: str) -> None:
    cid = (cid or "").strip()
    if not cid:
        return
    data = _jd_pending_by_chat_read_store()
    if cid in data:
        del data[cid]
        _jd_pending_by_chat_write_store(data)


def _clear_global_jd_pending_file() -> None:
    try:
        if LAST_JD_PENDING_PATH.exists():
            LAST_JD_PENDING_PATH.unlink()
    except Exception:
        pass


def _save_last_jd_pending(jd_config: dict, *, chat_id: str = "") -> None:
    """保存待确认 JD：有 ``chat_id`` 时写入按会话文件；无 chat 时写全局（桌面等）。"""
    if not jd_config or not isinstance(jd_config, dict):
        return
    if not (jd_config.get("job_title") or jd_config.get("jd_full")):
        return
    cid = (chat_id or "").strip()
    try:
        if cid:
            _jd_pending_by_chat_set(cid, jd_config)
            logger.info(
                "[Agent] 已保存待确认 JD（会话）chat_id=%s job_title=%s",
                cid[:28],
                jd_config.get("job_title", ""),
            )
        else:
            LAST_JD_PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
            LAST_JD_PENDING_PATH.write_text(
                json.dumps({"jd_config": jd_config, "updated_at": time.time()}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("[Agent] 已保存待确认 JD（全局）job_title=%s", jd_config.get("job_title", ""))
    except Exception as e:
        logger.debug("[Agent] 保存 last_jd_pending 失败: %s", e)


def _resolve_last_jd_pending(chat_id: str = "") -> tuple[dict | None, str]:
    """先按 chat 读 pending，再全局；返回 (jd, 'chat'|'global'|'')。"""
    cid = (chat_id or "").strip()
    if cid:
        jd = _jd_pending_by_chat_get(cid)
        if jd:
            return jd, "chat"
    g = _read_global_jd_pending_file()
    if g:
        return g, "global"
    return None, ""


def _load_last_jd_pending(chat_id: str = "") -> dict | None:
    """加载待确认 JD：有 chat 时优先该会话，否则全局文件。"""
    jd, _ = _resolve_last_jd_pending(chat_id)
    return jd


def _clear_jd_pending_source(chat_id: str, source: str) -> None:
    """仅清除本次解析所用的来源（chat 或 global）。"""
    src = (source or "").strip().lower()
    if src == "chat" and (chat_id or "").strip():
        _jd_pending_by_chat_delete(chat_id.strip())
    elif src == "global":
        _clear_global_jd_pending_file()


def _clear_last_jd_pending(chat_id: str = "") -> None:
    """发布成功等场景：清除指定会话条目并清除全局文件，避免串岗。"""
    if (chat_id or "").strip():
        _jd_pending_by_chat_delete(chat_id.strip())
    _clear_global_jd_pending_file()


def _hr_user_intent_skip_boss_post(messages: list | None) -> bool:
    """
    HR 已表达「Boss 上已有在招岗 / 只收网不发帖」等：pending 应带 skip_boss_post，
    飞书裸「同意」时不再强制 atom_post_job_boss。
    扫描最近几条 user 消息（含当前轮之前）。
    """
    if not messages:
        return False
    pat = re.compile(
        r"只(?:抓|收)简历|仅(?:抓|收)(?:取)?简历|只收网|仅收网|不用发(?:帖|职位)?|不要发(?:帖|职位)?|不(?:用|要)?发(?:帖|职位)|"
        r"不重新发帖|别再发帖|职位已在|已在Boss|Boss已有|已有(?:在招)?职位|只开调度|只要抓简历|"
        r"轻量收网|已有岗|不调发帖|别发(?:帖|职位)|不要发布职位",
        re.I,
    )
    n = 0
    for msg in reversed(messages):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        c = (msg.get("content") or "").strip()
        if pat.search(c):
            return True
        n += 1
        if n >= 6:
            break
    return False


def _hr_assistant_declares_skip_boss_post(text: str) -> bool:
    """助手本轮回复若明确「只收网 / 已有在招 / 不发帖」等，pending 带 skip 以免裸「同意」误触发发帖。"""
    if not (text or "").strip():
        return False
    return bool(
        re.search(
            r"只收网|只抓简历|仅收网|仅抓(?:取)?简历|无需发(?:帖|职位)?|不(?:需要|用)发(?:帖|职位)|"
            r"不重新发帖|本次[^\n]{0,60}不[^\n]{0,12}发帖|不(?:再)?调用发帖|仅配置收网|"
            r"Boss.*已有|已有在招|已有职位|轻量收网|分支\s*B|"
            r"仅(?:开)?调度|不调\s*atom_post|不发Boss",
            text,
            re.I,
        )
    )


def _hr_branch_b_recruitment_context(messages: list | None) -> bool:
    """
    对话是否为「分支 B / 已有在招岗位·轻量收网」：只加调度收网，禁止走 atom_post_job_boss 发帖短路。
    """
    if not messages:
        return False
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        c = msg.get("content") or ""
        if re.search(
            r"分支\s*B|分支B|轻量收网|已有在招岗位|"
            r"运行模式|累计收网目标|透析触发份数|仅配置无人值守|无人值守收网前|"
            r"请确认以下\s*\d\s*项",
            c,
            re.I,
        ):
            return True
        for m in re.finditer(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", c):
            try:
                obj = json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            jn = (obj.get("job_name") or "").strip()
            if not jn:
                continue
            if not (
                "resume_collect_target" in obj
                or "enable_greet_recommend" in obj
                or (obj.get("jd_select") or "").strip()
            ):
                continue
            return True
    return False


def _hr_arg_bool(val: Any, default: bool) -> bool:
    """与 MCP _arg_bool 一致：避免 bool('false')==True。"""
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in ("0", "false", "no", "否", "关", "off"):
        return False
    if s in ("1", "true", "yes", "是", "开", "on"):
        return True
    return default


def _branch_b_json_block_qualifies(obj: dict[str, Any]) -> bool:
    jn = (obj.get("job_name") or "").strip()
    if not jn:
        return False
    return bool(
        obj.get("resume_collect_target") is not None
        or "enable_greet_recommend" in obj
        or (obj.get("jd_select") or "").strip()
    )


def _branch_b_obj_to_task_payload(obj: dict[str, Any]) -> dict[str, Any] | None:
    """将单个 JSON 对象转为 add_automated_recruitment_task 参数字典（不含无效块）。"""
    if not _branch_b_json_block_qualifies(obj):
        return None
    jn = (obj.get("job_name") or "").strip()
    payload: dict[str, Any] = {"job_name": jn}
    if "enable_greet_recommend" in obj:
        payload["enable_greet_recommend"] = _hr_arg_bool(obj["enable_greet_recommend"], True)
    if obj.get("resume_collect_target") is not None:
        try:
            payload["resume_collect_target"] = int(obj["resume_collect_target"])
        except (TypeError, ValueError):
            pass
    if obj.get("analyze_threshold") is not None:
        try:
            payload["analyze_threshold"] = int(obj["analyze_threshold"])
        except (TypeError, ValueError):
            pass
    _mch = obj.get("max_count_per_harvest_tick")
    if _mch is None and obj.get("max_count") is not None:
        _mch = obj.get("max_count")
    if _mch is not None:
        try:
            payload["max_count_per_harvest_tick"] = int(_mch)
        except (TypeError, ValueError):
            pass
    if obj.get("greet_target") is not None:
        try:
            payload["greet_target"] = int(obj["greet_target"])
        except (TypeError, ValueError):
            pass
    if obj.get("greet_harvest_switch_interval_minutes") is not None:
        try:
            payload["greet_harvest_switch_interval_minutes"] = int(obj["greet_harvest_switch_interval_minutes"])
        except (TypeError, ValueError):
            pass
    js = (obj.get("jd_select") or "").strip()
    if js:
        payload["jd_select"] = js
    jcp = (obj.get("jd_config_path") or "").strip()
    if jcp:
        payload["jd_config_path"] = jcp
    return payload


def _score_branch_b_task_payload(p: dict[str, Any]) -> int:
    """分越高表示配置越完整；优先含 enable_greet_recommend 的块（避免误选仅含 job_name 的首个 json）。"""
    sc = len(p) * 3
    if "enable_greet_recommend" in p:
        sc += 40
    if "resume_collect_target" in p:
        sc += 15
    if (p.get("jd_select") or "").strip():
        sc += 8
    if p.get("analyze_threshold") is not None:
        sc += 5
    if p.get("greet_harvest_switch_interval_minutes") is not None:
        sc += 5
    if p.get("max_count_per_harvest_tick") is not None:
        sc += 6
    if p.get("greet_target") is not None:
        sc += 6
    return sc


def _extract_branch_b_add_task_payload(messages: list | None) -> dict[str, Any] | None:
    """从 assistant 消息中所有 ```json``` 块里选取「最完整」的分支 B 配置，再交给 add_automated_recruitment_task。"""
    if not messages:
        return None
    best: dict[str, Any] | None = None
    best_sc = -1
    for msg in reversed(messages):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        text = msg.get("content") or ""
        for m in re.finditer(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text):
            try:
                obj = json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            cand = _branch_b_obj_to_task_payload(obj)
            if not cand:
                continue
            sc = _score_branch_b_task_payload(cand)
            if sc > best_sc:
                best_sc = sc
                best = cand
    return best


def _last_assistant_asks_ab_scheduler_choice(messages: list | None) -> bool:
    """助手刚问过「选项 A / B」类自动透析确认时，用户单行 A/B 应视为分支 B 最终确认。"""
    if not messages:
        return False
    for msg in reversed(messages):
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        t = msg.get("content") or ""
        return bool(
            re.search(r"选项\s*[AB]|请回复\s*[`「『]?\s*[AB]\s*[`」』]?\s*或", t, re.I)
        )
    return False


def _branch_b_user_ab_choice(user_input: str) -> str | None:
    s = (user_input or "").strip()
    if re.fullmatch(r"(?i)[ab]", s):
        return s.upper()
    return None


def _extract_branch_b_scheduler_hints_from_markdown(messages: list | None) -> dict[str, Any]:
    """
    从 assistant 的 Markdown 表里抽取分支 B 调度参数（模型常只画表不输出 ```json```，
    随后单行「A」又不触发「同意」预检，导致 MCP 省略参数、回退 jd.json 默认）。
    取各字段在对话中的**最后一次**匹配。
    """
    out: dict[str, Any] = {}
    if not messages:
        return out
    chunks: list[str] = []
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            chunks.append(msg.get("content") or "")
    blob = "\n".join(chunks[-12:])
    if len(blob) > 12000:
        blob = blob[-12000:]

    def _last_int(regex: str, group: int = 1) -> int | None:
        ms = list(re.finditer(regex, blob, re.I))
        if not ms:
            return None
        try:
            return int(ms[-1].group(group))
        except (TypeError, ValueError, IndexError):
            return None

    ms_jn = list(
        re.finditer(r"\|[^|\n]*岗位名称[^|\n]*\|\s*([^|]+?)\s*\|", blob, re.I)
    )
    if ms_jn:
        jn = ms_jn[-1].group(1).strip().strip("*").strip("`").strip()
        if jn and len(jn) < 240:
            out["job_name"] = jn

    ms_gr = list(re.finditer(r"\|[^|\n]*推荐牛人[^|\n]*\|\s*([^|]+?)\s*\|", blob, re.I))
    if ms_gr:
        cell = ms_gr[-1].group(1)
        if re.search(r"关|否|不要|❌|仅收网|不(?:需要|打)|\bno\b|\boff\b", cell, re.I):
            out["enable_greet_recommend"] = False
        elif re.search(r"\b是\b|需要(?!\s*吗)|✅|开|启用|\byes\b|\bon\b", cell, re.I):
            out["enable_greet_recommend"] = True

    cap = _last_int(
        r"\|[^|\n]*(?:累计抓取简历目标|简历目标|收集(?:上)?限)[^|\n]*\|\s*(\d+)\s*份",
    )
    if cap is not None and cap > 0:
        out["resume_collect_target"] = cap
        out["analyze_threshold"] = cap

    for msg in reversed(messages):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        uc = msg.get("content") or ""
        um = re.search(
            r"仅(?:抓|收)(?:取)?简历\s*(\d+)\s*份|(\d+)\s*份\s*$",
            uc.strip(),
            re.I,
        )
        if um:
            try:
                n = int(um.group(1) or um.group(2))
                if 0 < n < 10000:
                    out["resume_collect_target"] = n
                    out["analyze_threshold"] = n
                    break
            except (TypeError, ValueError):
                pass
        if re.search(r"仅(?:抓|收)(?:取)?简历|只抓简历|仅收网|只收网", uc, re.I):
            um2 = re.search(
                r"(?:仅收网|只收网)\s*(\d{1,4})\s*份|(\d{1,4})\s*份",
                uc,
                re.I,
            )
            if um2:
                try:
                    n = int(um2.group(1) or um2.group(2))
                    if 0 < n < 10000:
                        out["resume_collect_target"] = n
                        out["analyze_threshold"] = n
                        break
                except (TypeError, ValueError):
                    pass

    return out


def _apply_last_user_line_to_add_task_args(messages: list | None, out: dict[str, Any]) -> None:
    """
    最近一条 HR 用户话里的调度短句：模型常只传 job_name，须覆盖 jd 默认（交替+4 份+3 人+10 分钟）。
    支持：仅收网10份；交替模式，打招呼10人，收简历5份，间隔2分钟；整句可带「【系统】…」前缀。
    """
    if not messages:
        return
    for msg in reversed(messages):
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        raw = (msg.get("content") or "").strip()
        if not raw:
            continue
        if not re.search(
            r"仅收网|只收网|仅抓|只抓简历|交替|↔|打招呼|再抓|收网改成|多抓|enable_greet|"
            r"收简历|间隔|轮换|推荐间隔|牛人|运行模式",
            raw,
            re.I,
        ):
            break
        # 仅收网优先于「交替」字样（避免歧义句）
        if re.search(
            r"仅收网|只收网|仅抓(?:取)?简历|只抓简历|不要打招呼|关闭打招呼|不打招呼|"
            r"只要简历|只下简历|enable_greet[^\n]*false",
            raw,
            re.I,
        ):
            out["enable_greet_recommend"] = False
        elif re.search(
            r"交替|打招呼\s*↔|↔\s*收|开.*打招呼|打开打招呼|推荐.*打招呼|enable_greet[^\n]*true",
            raw,
            re.I,
        ):
            out["enable_greet_recommend"] = True

        m = re.search(
            r"(?:仅收网|只收网)\s*(\d{1,4})\s*份|"
            r"(?:仅抓|只抓)(?:取)?简历\s*(\d{1,4})\s*份|"
            r"(?:再抓|多抓|收网(?:改成)?)\s*(\d{1,4})\s*份",
            raw,
            re.I,
        )
        if m:
            for g in m.groups():
                if g:
                    try:
                        n = int(g)
                        if 0 < n < 10000:
                            out["resume_collect_target"] = n
                            out["analyze_threshold"] = n
                    except ValueError:
                        pass

        # 交替模式下的自然语言：打招呼 N 人、收简历 M 份、间隔 K 分钟
        _want_alt = bool(
            re.search(r"交替", raw, re.I) or out.get("enable_greet_recommend") is True
        )
        if _want_alt and not re.search(
            r"仅收网|只收网|仅抓(?:取)?简历|只抓简历",
            raw,
            re.I,
        ):
            mg = re.search(r"打招呼\s*(\d{1,3})\s*人", raw, re.I)
            if not mg:
                mg = re.search(
                    r"(?:推荐|牛人|牛人沟通).{0,24}?(\d{1,3})\s*人",
                    raw,
                    re.I,
                )
            if mg:
                try:
                    gv = int(mg.group(1))
                    if 0 < gv <= 500:
                        out["greet_target"] = gv
                except ValueError:
                    pass
            mcv = re.search(
                r"收简历\s*(\d{1,4})\s*份|"
                r"抓\s*(\d{1,4})\s*份(?:简历)?|"
                r"累计(?:收网|收简历)?\s*(\d{1,4})\s*份",
                raw,
                re.I,
            )
            if mcv:
                for gg in mcv.groups():
                    if gg:
                        try:
                            n = int(gg)
                            if 0 < n < 10000:
                                out["resume_collect_target"] = n
                                out["analyze_threshold"] = n
                        except ValueError:
                            pass
                        break
            mi = re.search(
                r"间隔\s*(\d{1,3})\s*分钟|轮换\s*(\d{1,3})\s*分钟|"
                r"推荐间隔\s*(\d{1,3})\s*分钟",
                raw,
                re.I,
            )
            if mi:
                for gg in mi.groups():
                    if gg:
                        try:
                            k = int(gg)
                            if 0 < k <= 120:
                                out["greet_harvest_switch_interval_minutes"] = k
                                out["recommend_interval_minutes"] = k
                        except ValueError:
                            pass
                        break
        break


def _merge_branch_b_into_add_automated_args(args: dict[str, Any], messages: list | None) -> dict[str, Any]:
    """用 ```json```、Markdown 表与**最近用户短句**补全 add_automated_recruitment_task 入参（防 jd 默认）。"""
    if not isinstance(args, dict):
        return args
    out = dict(args)
    if _hr_branch_b_recruitment_context(messages):
        json_pl = _extract_branch_b_add_task_payload(messages)
        if json_pl:
            for k, v in json_pl.items():
                if v is None:
                    continue
                if k not in out or out.get(k) in (None, ""):
                    out[k] = v
        hints = _extract_branch_b_scheduler_hints_from_markdown(messages)
        # 仅补全 MCP 省略的键；勿用历史 assistant 成功文案里的「仅收网」表覆盖本轮显式 true/false
        if hints.get("enable_greet_recommend") is not None:
            if "enable_greet_recommend" not in out or out.get("enable_greet_recommend") is None:
                out["enable_greet_recommend"] = hints["enable_greet_recommend"]
        if hints.get("resume_collect_target") is not None:
            if "resume_collect_target" not in out or out.get("resume_collect_target") is None:
                try:
                    n = int(hints["resume_collect_target"])
                    if n > 0:
                        out["resume_collect_target"] = n
                        out["analyze_threshold"] = int(
                            hints.get("analyze_threshold") or hints["resume_collect_target"]
                        )
                except (TypeError, ValueError):
                    pass
        jh = (hints.get("job_name") or "").strip()
        if jh and not (out.get("job_name") or "").strip():
            out["job_name"] = jh
    _apply_last_user_line_to_add_task_args(messages, out)
    return out


def _hr_user_input_is_solitary_boss_job_select_line(user_input: str) -> bool:
    """
    飞书「绑定/换岗」常见：整句基本就是一行 Boss 选岗，无调度参数。
    此类消息已在外层 apply_job_select 写 jd_select；不应让模型立刻 add_automated 吃 jd 默认。
    """
    s = (user_input or "").strip()
    # jachin_mcp_write_ack 含下划线，会被 boss_utils.canonicalize 误解析成「职位 _ 城市」行，触发错误招聘预检
    if "jachin_mcp_write_ack" in s.lower():
        return False
    if len(s) < 6 or len(s) > 160:
        return False
    if re.search(
        r"仅收网|仅打招呼|交替|抓\s*\d|份简历|同意调度|再抓|打招呼改成|收网改成|推荐间隔|透析|"
        r"启动无人值守|注册.*任务|进度|停止|分析简历|恢复挂起|继续|需要吗|选项\s*[AB]",
        s,
        re.I,
    ):
        return False
    if any(x in s for x in (",", "，", "\n", ";", "；")):
        return False
    import sys

    try:
        from l3_node.hr_loader import _get_hr_recruitment_plugin_root

        root = _get_hr_recruitment_plugin_root()
        if not root or not root.exists():
            return False
        ps = str(root.resolve())
        inserted = ps not in sys.path
        if inserted:
            sys.path.insert(0, ps)
        try:
            from tools.boss_utils import extract_job_select_line_for_boss_from_hr_chat

            line = (extract_job_select_line_for_boss_from_hr_chat(s) or "").strip()
        finally:
            if inserted:
                try:
                    sys.path.remove(ps)
                except ValueError:
                    pass
        if not line:
            return False

        def _nz(x: str) -> str:
            return re.sub(r"\s+", "", (x or "").lower())

        return _nz(s) == _nz(line)
    except Exception:
        return False


async def _execute_publish_bypass(
    jd_config: dict,
    *,
    allowed_skills: list[str] | None = None,
    lark_chat_id: str = "",
) -> str | None:
    """
    当「同意」但会话丢失时，直接执行发布，不经过 LLM。
    返回成功文案，失败返回 None 交给正常流程处理。
    """
    if not jd_config or not isinstance(jd_config, dict):
        return None
    job_title = (jd_config.get("job_title") or "").strip()
    if not job_title:
        return None
    try:
        path = _persist_jd_config_before_publish(jd_config)
        if not path:
            return None
        from pathlib import Path as _Path

        jd_doc: dict = {}
        try:
            raw_j = json.loads(_Path(path).read_text(encoding="utf-8"))
            if isinstance(raw_j, dict):
                jd_doc = raw_j
        except Exception:
            pass
        force_pub = bool(jd_config.get("force_republish"))
        already_on_boss = False
        try:
            from l3_node.hr_loader import _get_hr_recruitment_plugin_root as _gr

            _pr = _gr()
            if _pr and _pr.exists():
                import sys as _sys

                _ps = str(_pr.resolve())
                if _ps not in _sys.path:
                    _sys.path.insert(0, _ps)
                from tools.hr_data_paths import jd_boss_post_marked_published

                already_on_boss = jd_boss_post_marked_published(jd_doc)
        except Exception as _e:
            logger.debug("[Agent] 读取 boss_post 标记失败: %s", _e)

        def _send_sched_confirm() -> None:
            try:
                from l3_node.hr_loader import _get_hr_recruitment_plugin_root

                pr = _get_hr_recruitment_plugin_root()
                if pr and pr.exists():
                    import sys

                    ps = str(pr.resolve())
                    if ps not in sys.path:
                        sys.path.insert(0, ps)
                    from tools.hr_scheduler_confirm_prompt import hr_scheduler_send_confirm_prompt

                    hr_scheduler_send_confirm_prompt(job_name=job_title, jd_config_path=path)
            except Exception as e:
                logger.warning("[Agent] 发送调度确认失败: %s", e)

        if already_on_boss and not force_pub:
            _clear_last_jd_pending(lark_chat_id)
            _send_sched_confirm()
            return (
                "本岗位 **已在 Boss 发布过**（jd.json 中 `boss_post_published=true`），本次 **未再次执行发帖**。\n\n"
                "已按您确认的 JSON **合并更新** jd.json（职位描述与调度字段彼此独立，均可只改文件不重发帖）。"
                "已向飞书发送 **无人值守调度参数确认单**。\n\n"
                "**定时任务尚未启动** — 请在飞书回复 **「同意调度」**；仅改数字也可在飞书发「打招呼改成N人」「收网改成M人」等。\n"
                "若确需在 Boss **重新发帖**，请在 JSON 中加 `\"force_republish\": true` 或让助手调用 `atom_post_job_boss` 并传 `force_republish`。"
            )

        mcp_registry = get_mcp_registry()
        inp = json.dumps(
            {
                "jd_config_path": path,
                "cdp_url": "http://127.0.0.1:9222",
                "force_republish": force_pub,
            },
            ensure_ascii=False,
        )
        obs = await mcp_registry.invoke("mcp:atom_post_job_boss", inp, allowed_skills=allowed_skills)
        result = json.loads(obs) if (obs or "").strip().startswith("{") else {}
        posted_ok = bool(result.get("posted")) or bool(result.get("already_published"))
        if not posted_ok and "需要登录" in str(result.get("error", "")):
            return "已为您打开 Boss 直聘登录页，请扫码登录。登录完成后请回复「已登录」或「继续发布」。"
        if not posted_ok:
            err = result.get("error", obs) or ""
            err_preview = (err[:200] + "…") if len(str(err)) > 200 else err
            logger.warning("[Agent] 直接发布未成功: %s", err_preview)
            if "playwright" in str(err).lower():
                return "发布失败：缺少 playwright 组件。若使用 exe：请重新执行 `python scripts/build_l3_sidecar.py --force` 后重启。若使用 Python：请执行 `pip install playwright` 后重启 L3。"
            return None
        _clear_last_jd_pending(lark_chat_id)
        _send_sched_confirm()
        if result.get("already_published"):
            return (
                "Boss 侧 **已记录过发帖成功**，本次未重复发帖；已尝试发送飞书 **调度参数确认单**。\n\n"
                "**定时任务尚未启动** — 请飞书回复 **「同意调度」** 或执行 **add_automated_recruitment_task**。"
            )
        return (
            "职位已在 Boss 发布成功。\n\n"
            "已向飞书发送 **无人值守调度参数确认单**（默认：推荐间隔、每轮打招呼人数、打招呼后衔接抓简历延迟、每轮收网上限、简历累计目标与透析阈值等）。\n\n"
            "**定时任务尚未启动** — 请在飞书核对参数后回复 **「同意调度」** 以正式启动；也可先按消息说明修改（如「打招呼改成5人」「推荐间隔20分钟」）再回复同意调度。\n"
            "若当前环境未配置飞书推送，请在对话中让助手执行 **add_automated_recruitment_task**（参数已写入 jd.json，可省略）。"
        )
    except Exception as e:
        logger.warning("[Agent] 直接发布异常: %s", e)
        return None


async def _execute_branch_b_harvest_bypass(
    task_payload: dict[str, Any],
    *,
    allowed_skills: list[str] | None = None,
) -> str | None:
    """
    分支 B：Boss 上职位已在招，用户确认后仅向调度器添加任务，不调用 atom_post_job_boss。
    """
    if not task_payload or not isinstance(task_payload, dict):
        return None
    jn = (task_payload.get("job_name") or "").strip()
    if not jn:
        return None
    try:
        mcp_registry = get_mcp_registry()
        clean = {k: v for k, v in task_payload.items() if v is not None}
        inp = json.dumps(clean, ensure_ascii=False)
        obs = await mcp_registry.invoke("mcp:add_automated_recruitment_task", inp, allowed_skills=allowed_skills)
        result: dict[str, Any] = {}
        if (obs or "").strip().startswith("{"):
            try:
                _parsed = json.loads(obs)
                result = _parsed if isinstance(_parsed, dict) else {}
            except json.JSONDecodeError:
                result = {}
        if not isinstance(result, dict) or result.get("ok") is not True:
            err = (result.get("error", obs) if isinstance(result, dict) else obs) or ""
            logger.warning("[Agent] 分支B 收网调度未成功: %s", (str(err))[:200])
            return None
        greet_on = bool(task_payload.get("enable_greet_recommend", True))
        rct = task_payload.get("resume_collect_target", "")
        return (
            f"✅ 已按「分支B·轻量收网」启动无人值守：岗位「{jn}」，打招呼={'开启' if greet_on else '关闭'}，"
            f"简历收集目标约 {rct} 份。本次**未**调用发帖工具（atom_post_job_boss）。"
        )
    except Exception as e:
        logger.warning("[Agent] 分支B 直接收网异常: %s", e)
        return None


def _persist_jd_config_before_publish(jd_config: dict) -> str | None:
    """
    HR 同意后、打开 Chrome 发布前【必须自动先执行】：在 ~/.jachin/workspace/hr_recruitment/{岗位名}/ 下
    复制模板填 jd.json，创建 pending/processed/result、排行榜_Summary.md。完成后返回 jd_config_path 供后续发布使用。
    """
    if not jd_config or not isinstance(jd_config, dict):
        return None
    job_title = (jd_config.get("job_title") or "").strip()
    if not job_title:
        return None
    try:
        from l3_node.hr_loader import _get_hr_recruitment_plugin_root
        plugin_root = _get_hr_recruitment_plugin_root()
        if not plugin_root or not (plugin_root / "tools" / "hr_data_paths.py").exists():
            logger.warning("[Agent] HR 招聘 plugin 路径不存在，无法持久化 JD 配置")
            return None
        import sys
        if str(plugin_root) not in sys.path:
            sys.path.insert(0, str(plugin_root))
        from tools.boss_utils import canonicalize_boss_job_select
        from tools.hr_data_paths import init_job_jd_from_template, resolve_recruitment_data_folder_key

        ov = dict(jd_config) if isinstance(jd_config, dict) else {}
        sel_raw = (ov.get("jd_select") or "").strip()
        canon_sel = (canonicalize_boss_job_select(sel_raw) or sel_raw).strip() if sel_raw else ""
        jt_fk = (ov.get("job_title") or job_title or "").strip()
        data_fk = resolve_recruitment_data_folder_key(
            jd_select_canon=canon_sel,
            job_title=jt_fk,
            jd_doc=ov,
        )
        jd_path = init_job_jd_from_template(
            job_title, overrides=jd_config, data_folder_key=data_fk
        )
        logger.info(
            "[Agent] HR 已确认，已在 hr_recruitment/%s/ 复制模板填 jd.json、创建 pending/processed/result",
            data_fk,
        )
        return str(jd_path)
    except Exception as e:
        logger.warning("[Agent] 持久化 JD 配置失败: %s", e)
        return None


def _load_hr_recruitment_skill_content() -> str | None:
    """
    加载 HR 招聘 Skill 内容（SKILL.md）。
    架构：Skill 包 (hr-recruitment/) 含 SKILL.md 定义流程；MCP 包 (com.jachin.hr.recruitment) 含工具。
    优先 Skill 包，其次 MCP 包（向后兼容，支持 l3_mcp_cache 的 UUID 目录名）。
    """
    import os
    root = Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin")))
    proj = Path(__file__).resolve().parent.parent
    hr_plugin_root = None
    try:
        from l3_node.hr_loader import _get_hr_recruitment_plugin_root
        hr_plugin_root = _get_hr_recruitment_plugin_root()
    except Exception:
        pass
    candidates = [
        proj / "skills_repo" / "hr-recruitment" / "SKILL.md",  # Skill 包：纯流程定义
        root / "l3_skill_cache" / "hr-recruitment" / "SKILL.md",  # 订阅拉取后的 Skill 包
    ]
    if hr_plugin_root:
        candidates.append(hr_plugin_root / "SKILL.md")  # MCP 包内 SKILL.md（支持 UUID 目录）
    candidates.extend([
        root / "l3_mcp_cache" / "com.jachin.hr.recruitment" / "SKILL.md",  # 精确路径
        proj / "skills_repo" / "plugin" / "com.jachin.hr.recruitment" / "SKILL.md",
    ])
    try:
        from l3_node.paths import get_app_root
        app_proj = get_app_root()
        if app_proj and app_proj != proj:
            candidates.insert(0, app_proj / "skills_repo" / "hr-recruitment" / "SKILL.md")
    except Exception:
        pass
    for p in candidates:
        if p.exists() and p.is_file():
            try:
                raw = p.read_text(encoding="utf-8")
                # 去掉 YAML frontmatter，只保留 Markdown 正文
                if raw.strip().startswith("---"):
                    parts = raw.split("---", 2)
                    if len(parts) >= 3:
                        raw = parts[2].strip()
                return raw
            except Exception as e:
                logger.debug("[Agent] 读取 HR Skill 失败 %s: %s", p, e)
    return None


def _load_ui_qa_skill_content() -> str | None:
    """加载桌面视觉 UI QA Skill（ui_qa_skill.md）。"""
    proj = Path(__file__).resolve().parent
    candidates = [
        proj / "skills" / "ui_qa" / "ui_qa_skill.md",
    ]
    try:
        from l3_node.paths import get_app_root

        app = get_app_root()
        if app:
            candidates.insert(0, app / "l3_node" / "skills" / "ui_qa" / "ui_qa_skill.md")
    except Exception:
        pass
    for p in candidates:
        if p.is_file():
            try:
                return p.read_text(encoding="utf-8").strip()
            except Exception as e:
                logger.debug("[Agent] 读取 UI QA Skill 失败 %s: %s", p, e)
    return None


def _load_holographic_ui_skill_content() -> str | None:
    """加载 OmniParser 全息屏幕 Skill（holographic_ui_skill.md）。"""
    proj = Path(__file__).resolve().parent
    candidates = [
        proj / "skills" / "ui_qa" / "holographic_ui_skill.md",
    ]
    try:
        from l3_node.paths import get_app_root

        app = get_app_root()
        if app:
            candidates.insert(0, app / "l3_node" / "skills" / "ui_qa" / "holographic_ui_skill.md")
    except Exception:
        pass
    for p in candidates:
        if p.is_file():
            try:
                return p.read_text(encoding="utf-8").strip()
            except Exception as e:
                logger.debug("[Agent] 读取 Holographic UI Skill 失败 %s: %s", p, e)
    return None


def _get_l2_config() -> dict[str, Any] | None:
    """从 l2_gateway_config.json 读取 L2 配置（已配对时）。含 permissions_snapshot。"""
    try:
        from l3_node.jachin_config import get_jachin_root

        cfg_path = get_jachin_root() / "l2_gateway_config.json"
    except ImportError:
        cfg_path = Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin"))) / "l2_gateway_config.json"
    if not cfg_path.exists():
        return None
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        if not data.get("paired"):
            return None
        base = data.get("l2_base_url", "").rstrip("/")
        if not base:
            return None
        return {
            "l2_base_url": base,
            "sub_account_id": data.get("sub_account_id", ""),
            "node_id": data.get("node_id", ""),
            "permissions_snapshot": data.get("permissions_snapshot") or {},
        }
    except Exception:
        return None


def _get_allowed_skills() -> list[str] | None:
    """
    获取 L2 下发的 Skill 白名单。None=未配对/全开，[]=显式无权限，非空=白名单。
    硬拦截层：仅此列表中的 skill 可加载、可执行。
    """
    cfg = _get_l2_config()
    if not cfg:
        return None
    snap = cfg.get("permissions_snapshot") or {}
    allowed = snap.get("allowed_skills")
    if allowed is None:
        return None
    return list(allowed) if isinstance(allowed, list) else []


def _get_service_switches() -> list[str] | None:
    """
    获取 L2 下发的 delegate 角色白名单。None=全开，非空=仅允许这些角色。
    """
    cfg = _get_l2_config()
    if not cfg:
        return None
    snap = cfg.get("permissions_snapshot") or {}
    switches = snap.get("service_switches")
    if switches is None:
        return None
    return list(switches) if isinstance(switches, list) else []


async def _recall_memory_search(query: str) -> str:
    """
    检索 Memory Nexus（SQLite / deep_search），与 ``core:local_memory_search`` 同源。
    不依赖 L2；伪动作 ``recall_memory`` 仅为 RoleExecutionAgent 兼容别名。
    """
    import json

    from l3_node.tool_call_cache import store_if_cacheable, try_get_cached

    qn = (query or "").strip()
    cache_inp = json.dumps({"q": qn, "backend": "memory_nexus"}, sort_keys=True, ensure_ascii=False)
    hit = try_get_cached("recall_memory", cache_inp)
    if hit is not None:
        return hit

    try:
        from l3_node.local_memory_search import async_search_local_memories, get_local_memory_search_timeout_sec

        _sl = max(2.0, get_local_memory_search_timeout_sec() * 0.1 + 1.0)
        res = await asyncio.wait_for(
            async_search_local_memories(qn, top_k=10, candidate_pool=48),
            timeout=get_local_memory_search_timeout_sec() + _sl,
        )
        if not res.get("ok"):
            err = res.get("error") or "unknown"
            out = f"[记忆检索失败: {err}]"
        else:
            nar = (res.get("formatted_text") or "").strip()
            if not nar:
                out = "[未找到相关记忆]"
            elif "[memory_nexus] 未找到相关记忆" in nar:
                out = "[未找到相关记忆]"
            else:
                out = nar
        return store_if_cacheable("recall_memory", cache_inp, out)
    except asyncio.TimeoutError:
        return store_if_cacheable(
            "recall_memory",
            cache_inp,
            "[记忆检索失败: timeout]",
        )
    except Exception as e:
        return store_if_cacheable("recall_memory", cache_inp, f"[记忆检索失败: {e}]")


async def _coordinate_task(
    payload: dict[str, Any],
    config: dict[str, str],
    engine: LiteLLMEngine,
) -> str:
    """
    向 L2 请求协同：提交任务、执行本节点分配的子任务、轮询直至完成。
    单节点时子任务会分配给自身，多节点时分配给其他 L3。
    """
    import httpx

    base = config["l2_base_url"].rstrip("/")
    headers = {"X-Sub-Account-Id": config.get("sub_account_id", ""), "Content-Type": "application/json"}
    node_id = config.get("node_id", "")
    parent_node_id = payload.get("parent_node_id") or node_id
    parent_node_id = parent_node_id or node_id
    sub_tasks = payload.get("sub_tasks") or []
    intent = payload.get("intent", "")

    req_payload = {
        "parent_node_id": parent_node_id,
        "parent_l3_node_id": parent_node_id,
        "intent": intent,
        "sub_tasks": [],
    }
    for st in sub_tasks:
        entry = {
            "intent": st.get("intent") or st.get("task", ""),
            "skill_required": st.get("skill_required", ""),
            "input_data": st.get("input_data"),
        }
        if st.get("timeout_seconds") is not None:
            entry["timeout_seconds"] = st["timeout_seconds"]
        req_payload["sub_tasks"].append(entry)
    if payload.get("timeout_seconds") is not None:
        req_payload["timeout_seconds"] = payload["timeout_seconds"]
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{base}/api/v2/coordinate/task",
                json=req_payload,
                headers=headers,
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return f"[协同请求失败: {e}]"

    task_id = data.get("task_id")
    if not task_id:
        return "[L2 未返回 task_id]"

    max_wait = 120
    poll_interval = 2
    elapsed = 0

    while elapsed < max_wait:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        try:
            from l3_node.telemetry import collect_hardware_telemetry
            telemetry = collect_hardware_telemetry()
            params = {"node_id": node_id, "limit": 10}
            if telemetry.get("cpu_load") is not None:
                params["cpu_load"] = telemetry["cpu_load"]
            if telemetry.get("memory_free") is not None:
                params["memory_free"] = telemetry["memory_free"]
            if telemetry.get("has_gpu") is not None:
                params["has_gpu"] = telemetry["has_gpu"]
            cfg_path = Path.home() / ".jachin" / "l2_gateway_config.json"
            l3_url = "http://127.0.0.1:18991"
            if cfg_path.exists():
                try:
                    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                    l3_url = (cfg.get("l3_http_url") or l3_url).strip()
                except Exception:
                    pass
            if l3_url:
                params["l3_http_url"] = l3_url
            try:
                reg = get_mcp_registry()
                known = list(reg.known_mcp_tools)
                if known:
                    raw = [t.replace("mcp:", "", 1).strip() for t in known if t]
                    if raw:
                        params["mcp_tools"] = ",".join(raw)
            except Exception:
                pass
            async with httpx.AsyncClient(timeout=15.0) as client:
                poll_r = await client.get(
                    f"{base}/api/v2/coordinate/poll",
                    params=params,
                    headers=headers,
                )
                poll_r.raise_for_status()
                poll_data = poll_r.json()
        except Exception as e:
            logger.warning("coordinate poll error: %s", e)
            continue

        for t in poll_data.get("tasks", []):
            timeout_sec = t.get("timeout_seconds")
            if timeout_sec is None or timeout_sec <= 0:
                timeout_sec = 60.0
            try:
                raw_in = t.get("input_data", "")
                inp_dict: dict[str, Any] = {}
                if isinstance(raw_in, dict):
                    inp_dict = raw_in
                elif isinstance(raw_in, str) and raw_in.strip().startswith("{"):
                    try:
                        parsed = json.loads(raw_in)
                        if isinstance(parsed, dict):
                            inp_dict = parsed
                    except json.JSONDecodeError:
                        inp_dict = {}

                use_native = False
                try:
                    from l3_node.intelligence_p1 import get_intel_p1_config

                    if get_intel_p1_config().get("coordinate_native_tool_dispatch") is not False:
                        use_native = inp_dict.get("type") == "native_tool"
                except ImportError:
                    use_native = inp_dict.get("type") == "native_tool"

                if use_native:
                    tid = (inp_dict.get("tool_id") or "").strip()
                    ai = inp_dict.get("work_order_input", "")
                    if isinstance(ai, dict):
                        ai = json.dumps(ai, ensure_ascii=False)
                    else:
                        ai = str(ai or "")
                    allowed_coord = _get_allowed_skills()
                    from l3_node.cognitive_kernel.dispatcher import dispatch_tool_work_order

                    async def _coordinate_raw_transport(work_order):
                        return run_tool(
                            str(work_order.inputs.get("tool") or tid),
                            str(work_order.inputs.get("work_order_input") or ai),
                            allowed_skills=allowed_coord,
                        )

                    dispatched = await dispatch_tool_work_order(
                        turn_id=f"coordinate-{task_id}-{t.get('subtask_id')}",
                        goal=str(t.get("intent") or intent or tid),
                        tool=tid,
                        work_order_input=ai,
                        executor=_coordinate_raw_transport,
                    )
                    result = dispatched.observation
                else:
                    result = await asyncio.wait_for(
                        run_agent(t["intent"], engine, max_iterations=3),
                        timeout=float(timeout_sec),
                    )
                async with httpx.AsyncClient(timeout=15.0) as client:
                    await client.post(
                        f"{base}/api/v2/coordinate/result",
                        json={"subtask_id": t["subtask_id"], "result": result},
                        headers=headers,
                    )
            except asyncio.TimeoutError:
                err_msg = f"[子任务超时: {timeout_sec}s 熔断]"
                logger.warning("coordinate subtask timeout: %s", t.get("subtask_id"))
                try:
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        await client.post(
                            f"{base}/api/v2/coordinate/result",
                            json={"subtask_id": t["subtask_id"], "result": err_msg},
                            headers=headers,
                        )
                except Exception:
                    pass
            except Exception as e:
                logger.warning("coordinate subtask error: %s", e)
                try:
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        await client.post(
                            f"{base}/api/v2/coordinate/result",
                            json={"subtask_id": t["subtask_id"], "result": f"[执行失败: {e}]"},
                            headers=headers,
                        )
                except Exception:
                    pass

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                status_r = await client.get(
                    f"{base}/api/v2/coordinate/status",
                    params={"task_id": task_id},
                    headers=headers,
                )
                status_r.raise_for_status()
                status_data = status_r.json()
        except Exception as e:
            logger.warning("coordinate status error: %s", e)
            continue

        if status_data.get("status") == "done":
            result = status_data.get("result")
            if isinstance(result, list):
                return "\n\n---\n\n".join(str(x) for x in result)
            return str(result) if result else "[协同完成，无结果]"

    return f"[协同超时: {max_wait}s 内未完成]"



class SubAgent:
    """
    子 Agent 实体：独立 system_prompt、会话上下文、裁剪工具集。
    支持生命周期管理与复用。
    """

    def __init__(
        self,
        sub_agent_id: str,
        system_prompt: str,
        allowed_skills: list[str],
        messages: Optional[list[dict[str, Any]]] = None,
        *,
        role_id: str = "default",
    ) -> None:
        self.sub_agent_id = sub_agent_id
        self.system_prompt = system_prompt
        self.allowed_skills = allowed_skills
        self.role_id = (role_id or "default").strip().lower()
        self.messages = list(messages) if messages else []

    async def run_once(
        self,
        task: str,
        engine: LiteLLMEngine,
        max_iterations: int = 3,
        *,
        delegate_depth: int = 1,
    ) -> str:
        """执行一次思考，将 task 追加到 messages 并运行 Agent，结果写入 messages。"""
        logger.debug(
            "[SubAgent] sub_agent_id=%s max_iterations=%d delegate_depth=%d task_preview=%s",
            self.sub_agent_id,
            max_iterations,
            delegate_depth,
            task[:120],
        )
        result = await run_agent(
            task,
            engine,
            max_iterations=max_iterations,
            _initial_messages=self.messages,
            implicit_attribution={
                "channel": "delegate_sub_agent",
                "sub_agent_id": self.sub_agent_id,
                "sub_agent_role": self.role_id,
                "sub_agent_system_prompt": self.system_prompt,
            },
            _delegate_depth=delegate_depth,
            _allowed_skills_override=self.allowed_skills,
        )
        self.messages.append({"role": "user", "content": task})
        self.messages.append({"role": "assistant", "content": result})
        return result


async def spawn_sub_agent(
    role: str,
    task: str,
    engine: LiteLLMEngine,
    *,
    sub_agent_id: Optional[str] = None,
) -> tuple[str, str]:
    """
    创建并唤醒子 Agent，执行一次任务。
    若 sub_agent_id 已存在则复用该分身（携带之前的 messages）。
    Returns:
        (result, sub_agent_id)
    """
    return await _spawn_sub_agent_async(role, task, engine, sub_agent_id)


def terminate_sub_agent(sub_agent_id: str) -> bool:
    """显式销毁分身，释放内存。"""
    if sub_agent_id in _sub_agent_registry:
        del _sub_agent_registry[sub_agent_id]
        return True
    return False


def _build_allowed_ids(allowed_skills: list[str]) -> set[str]:
    """白名单 id 集合（与 loader 逻辑一致）。"""
    from l3_node.primitives.tools.loader import _build_allowed_ids as _loader_ids
    return _loader_ids(allowed_skills)


def _sanitize_inline_role(
    role_spec: dict[str, Any],
    parent_allowed_skills: list[str] | None,
) -> tuple[str, str, list[str]]:
    """
    动态角色安全沙箱（§2.4 模式 C）。

    将 delegate sub_tasks[i]["role"] 为 dict 的内联角色规格解析并校验：
    - role_id：仅允许字母数字下划线
    - system_prefix：移除常见提示注入字符
    - allowed_tools：只能是主 Agent 当前工具集的子集（防升权）；不含 delegate（防递归）

    返回 (role_id, system_prefix, allowed_tools)。
    """
    import re

    raw_id = str(role_spec.get("id") or "dynamic_role").strip()
    role_id = re.sub(r"[^a-z0-9_]", "_", raw_id.lower())[:32] or "dynamic_role"

    # 防 prompt 注入：移除 Ignore previous instructions / system override 等危险词组
    _DANGEROUS_PATTERNS = re.compile(
        r"ignore\s+previous|system\s+override|forget\s+above|<\s*system\s*>|"
        r"\[INST\]|\[\/INST\]|<\|im_start\|>|<\|im_end\|>",
        re.IGNORECASE,
    )
    raw_prefix = str(role_spec.get("system_prefix") or "")
    prefix_max = role_spec.get("system_prefix_max_chars")
    try:
        prefix_limit = int(prefix_max) if prefix_max is not None else 1200
    except (TypeError, ValueError):
        prefix_limit = 1200
    prefix_limit = max(400, min(prefix_limit, 8000))
    system_prefix = _DANGEROUS_PATTERNS.sub("[REDACTED]", raw_prefix)[:prefix_limit]

    # allowed_tools 只能是父级工具子集，且不含 delegate
    raw_tools = role_spec.get("allowed_tools")
    if isinstance(raw_tools, str) and raw_tools.strip() == "*":
        # "*" 意图全部工具：继承父级允许工具（但仍禁 delegate）
        raw_tools_list = list(parent_allowed_skills or [])
    elif isinstance(raw_tools, list):
        raw_tools_list = [str(t) for t in raw_tools]
    else:
        raw_tools_list = list(parent_allowed_skills or [])

    # 强制剔除 delegate（禁止动态角色再创建动态角色）
    allowed_tools = [t for t in raw_tools_list if "delegate" not in t.lower()]
    # 与父级白名单取交集（防升权）
    if parent_allowed_skills is not None:
        parent_ids = set(parent_allowed_skills)
        allowed_tools = [t for t in allowed_tools if t in parent_ids]
    try:
        from l3_node.primitives.multi_agent.readonly_agent import (
            is_readonly_subagent_role,
            sanitize_allowed_skills_for_readonly,
        )

        if is_readonly_subagent_role(role_id):
            allowed_tools = sanitize_allowed_skills_for_readonly(allowed_tools)
    except Exception:
        pass
    return role_id, system_prefix, allowed_tools


async def _run_sub_agent(
    task_spec: dict[str, Any],
    engine: LiteLLMEngine,
    *,
    delegate_depth: int = 1,
    _parent_allowed_skills: list[str] | None = None,
) -> str:
    """运行子 Agent，完成指定子任务。内部调用 _spawn_sub_agent_async（一次性，不复用）。

    task_spec 支持字段：
      - role: str | dict  子 Agent 角色（内置名称字符串，或动态角色 dict）
      - task: str        任务描述
      - context_data: str|dict  附加上下文数据（如数据样本、前序结果等），追加到 task 末尾
      - max_iterations: int  可选，覆盖此子任务的最大迭代次数

    动态角色（role 为 dict）格式（§2.4 模式 C）：
      {"id": "pricing_strategist", "name": "定价策略专家",
       "system_prefix": "你是...", "allowed_tools": [...]}
    """
    role_raw = task_spec.get("role") or "default"
    _inline_prompt: str | None = None
    _inline_allowed: list[str] | None = None

    if isinstance(role_raw, dict):
        # 动态角色：安全沙箱校验
        _role_id, _inline_prompt, _inline_allowed = _sanitize_inline_role(
            role_raw, _parent_allowed_skills
        )
        role = _role_id
        logger.info(
            "[L3 Agent] 动态角色 %s 已沙箱校验（prefix=%d chars, tools=%s）",
            role, len(_inline_prompt), _inline_allowed[:5] if _inline_allowed else [],
        )
    else:
        role = str(role_raw).lower()

    task = task_spec.get("task", "")
    # 支持 context_data：将附加数据注入到任务描述末尾，减少子 Agent 须自行读取的开销
    ctx_data = task_spec.get("context_data")
    if ctx_data:
        if isinstance(ctx_data, dict):
            ctx_str = json.dumps(ctx_data, ensure_ascii=False, indent=2)
        else:
            ctx_str = str(ctx_data)
        ctx_max = int(task_spec.get("context_max_chars") or 4000)
        if ctx_max > 0:
            task = f"{task}\n\n【上下文数据】\n{ctx_str[:ctx_max]}"
        else:
            task = f"{task}\n\n【上下文数据】\n{ctx_str}"
    result, _ = await _spawn_sub_agent_async(
        role, task, engine,
        delegate_depth=delegate_depth,
        max_iterations=int(task_spec.get("max_iterations") or 0) or None,
        _inline_system_prefix=_inline_prompt,
        _inline_allowed_skills=_inline_allowed,
    )
    return result


async def _run_sub_agent_hooked(
    task_spec: dict[str, Any],
    engine: LiteLLMEngine,
    *,
    delegate_depth: int,
    node_index: int,
    parent_ctx: PipelineContext,
) -> str:
    """delegate 子任务：在进入/离开 SubAgent 时触发 ON_TASK_NODE_START/DONE（无注册则无开销）。"""

    def _parent_tools_from_ctx(ctx: PipelineContext) -> list[str] | None:
        sk = (ctx.metadata or {}).get("_skills")
        if sk is None:
            return None
        if isinstance(sk, (list, tuple)):
            return [str(x) for x in sk if x]
        return None

    _meta = dict(parent_ctx.metadata or {})
    _meta["delegate_sub_task_index"] = node_index
    _meta["delegate_sub_task_role"] = str(task_spec.get("role") or "")
    _sc = PipelineContext(
        intent=parent_ctx.intent,
        source=parent_ctx.source,
        session_id=parent_ctx.session_id,
        run_id=parent_ctx.run_id,
        metadata=_meta,
    )
    try:
        await global_hooks.run(HOOK_ON_TASK_NODE_START, _sc)
    except Exception:
        pass
    try:
        out = await _run_sub_agent(
            task_spec,
            engine,
            delegate_depth=delegate_depth,
            _parent_allowed_skills=_parent_tools_from_ctx(parent_ctx),
        )
    except Exception as ex:
        _sd = PipelineContext(
            intent=parent_ctx.intent,
            source=parent_ctx.source,
            session_id=parent_ctx.session_id,
            run_id=parent_ctx.run_id,
            metadata={**_meta, "task_node_error": str(ex)[:500]},
        )
        try:
            await global_hooks.run(HOOK_ON_TASK_NODE_DONE, _sd)
        except Exception:
            pass
        raise
    _sd = PipelineContext(
        intent=parent_ctx.intent,
        source=parent_ctx.source,
        session_id=parent_ctx.session_id,
        run_id=parent_ctx.run_id,
        metadata={**_meta, "task_node_result_preview": (out or "")[:800]},
    )
    try:
        await global_hooks.run(HOOK_ON_TASK_NODE_DONE, _sd)
    except Exception:
        pass
    return out


async def _spawn_sub_agent_async(
    role: str,
    task: str,
    engine: LiteLLMEngine,
    sub_agent_id: Optional[str] = None,
    *,
    delegate_depth: int = 1,
    max_iterations: Optional[int] = None,
    _inline_system_prefix: Optional[str] = None,
    _inline_allowed_skills: Optional[list[str]] = None,
) -> tuple[str, str]:
    """异步版 spawn_sub_agent，供 delegate 流程调用。

    max_iterations: 覆盖此次 SubAgent 运行的最大 RoleExecutionAgent 迭代次数；None 时使用 SubAgent.run_once 默认值（3）。
    _inline_system_prefix: 动态角色的 system prompt 前缀（已经安全沙箱处理），覆盖 SUB_AGENT_PROMPTS。
    _inline_allowed_skills: 动态角色的工具白名单（已与父级取交集），覆盖 SUB_AGENT_ALLOWED_SKILLS。
    """
    switches = _get_service_switches()
    if switches is not None:
        if len(switches) == 0 or role.lower() not in switches:
            return "当前子账号未开启该项服务支持", ""
    role_lower = (role or "default").lower()
    eff_engine = engine
    if role_lower == "coder":
        try:
            from core.llm_provider import get_coder_model_litellm_id

            cm = get_coder_model_litellm_id()
            if cm and engine._normalize_model(cm) != engine._normalize_model(engine.model_name):
                eff_engine = LiteLLMEngine(
                    security_context=engine.ctx,
                    model_name=cm,
                    fallback_models=list(engine.fallback_models or []),
                    timeout=engine.timeout,
                    max_attempts=engine.max_attempts,
                )
                logger.info(
                    "[L3 Agent] 子 Agent role=coder 使用编码模型 %s（主模型 %s）",
                    eff_engine.model_name,
                    engine.model_name,
                )
        except Exception as e:
            logger.debug("[L3 Agent] coder 模型切换跳过: %s", e)

    # 动态角色：使用 inline prompt/tools；普通角色：使用内置字典
    if _inline_system_prefix is not None:
        prompt = _inline_system_prefix
    else:
        prompt = SUB_AGENT_PROMPTS.get(role_lower, SUB_AGENT_PROMPTS["default"])
    if _inline_allowed_skills is not None:
        allowed = _inline_allowed_skills
    else:
        allowed = SUB_AGENT_ALLOWED_SKILLS.get(role_lower, SUB_AGENT_ALLOWED_SKILLS["default"])

    global_allowed = _get_allowed_skills()
    if global_allowed is not None:
        allowed = [s for s in allowed if s in _build_allowed_ids(global_allowed)]

    try:
        from l3_node.primitives.multi_agent.readonly_agent import (
            is_readonly_subagent_role,
            sanitize_allowed_skills_for_readonly,
        )

        if is_readonly_subagent_role(role_lower):
            allowed = sanitize_allowed_skills_for_readonly(list(allowed))
    except Exception:
        pass

    _run_kwargs: dict[str, Any] = {"delegate_depth": delegate_depth}
    if max_iterations and max_iterations > 0:
        _run_kwargs["max_iterations"] = max_iterations

    if sub_agent_id and sub_agent_id in _sub_agent_registry:
        agent = _sub_agent_registry[sub_agent_id]
        result = await agent.run_once(task, eff_engine, **_run_kwargs)
        return result, sub_agent_id

    sid = sub_agent_id or f"sub-{uuid.uuid4().hex[:8]}"
    agent = SubAgent(sid, prompt, allowed, role_id=role_lower)
    _sub_agent_registry[sid] = agent
    result = await agent.run_once(task, eff_engine, **_run_kwargs)
    return result, sid



async def _build_direct_system_prompt(
    *,
    prompt_cycle: int | None,
    json_mode: bool,
    general_chitchat: bool = False,
    desktop_companion_mode: bool = False,
    desktop_companion_context: Optional[dict[str, Any]] = None,
    voice_fast_lane_prompt: bool = False,
) -> str:
    """UserFacingReplyAgent LLM path: no tool table, no external-world action."""
    from l3_node.cognitive_kernel.kernel_prompts import build_user_facing_reply_agent_system_prompt

    # prompt_cycle：保留与调用方签名对齐（当前直连模板未使用）。
    _ = prompt_cycle
    _dc_ctx = desktop_companion_context if isinstance(desktop_companion_context, dict) else {}
    _voice_fast_prompt = bool(voice_fast_lane_prompt or _dc_ctx.get("voice_fast_lane") or _dc_ctx.get("server_voice_fast_lane"))
    # 【闲聊避让】纯寒暄且非 JSON：仅轻量 system + 工作区规则
    _dc_just_interrupted = bool(
        _dc_ctx.get("just_interrupted") or _dc_ctx.get("barge_in") or _dc_ctx.get("just_barged_in")
    )
    if general_chitchat and not json_mode:
        lines: list[str] = [
            build_user_facing_reply_agent_system_prompt(),
            "本轮由认知内核授权给 UserFacingReplyAgent 回复；不得执行或声称执行任何外部动作。",
            "语气自然、简洁、友善。",
            "用户本轮多为寒暄或简短礼貌用语：用一两句自然中文回应即可，可适度用常见礼貌用语。",
            "围绕用户话题简短回应，避免主动引入与当前消息无关的长篇领域话术。",
            "不要输出 Reasoning、WorkOrder、Verification evidence、User-facing result 等 RoleExecutionAgent 标签行。",
        ]
        if desktop_companion_mode:
            lines.append(
                "你是桌面右下角的 Jachin 伴侣：像 Jarvis/Samantha 一样温和机敏、高共情、带轻度极客质感。"
            )
            lines.append(
                "输出口语化短句（优先 15 字内），便于 TTS；避免长段、表格、复杂 Markdown 和密集标点。"
            )
            lines.append(
                "若用户只是点名或问“在吗”，首句强制 1-3 字（如“在呢”“嗯？”“怎么啦”）。"
            )
            lines.append("闲聊时别客服腔，禁止说教、禁止强行总结、禁止追问“还有什么问题吗”。")
            if _dc_just_interrupted:
                lines.append("系统提示刚被打断：不要复读，直接接住新指令，可说“好的，听你的”。")
        lines.append(_MERMAID_SAFE_RULES_SYSTEM_BLOCK_SLIM.strip())
        try:
            from l3_node.jachin_workspace_rules import get_jachin_workspace_rules_snippet

            jr = get_jachin_workspace_rules_snippet()
            if (jr or "").strip():
                lines.append("\n【工作区规则摘录】\n" + jr.strip())
        except ImportError:
            pass
        return "\n".join(lines)

    # Memory SSOT: direct completions do not pull L0/L1 from Nexus.
    # The top-level cognitive context carries memory through RelevantMemoryBundle.
    lines: list[str] = []
    lines.extend(
        [
            build_user_facing_reply_agent_system_prompt(),
            "本轮由认知内核授权给 UserFacingReplyAgent 回复；不得执行或声称执行任何外部动作。",
            "高精度遵从用户指令。不要问候语，不要输出可见的思考过程，不要使用 Markdown 章节标题行作开场。",
            "不要输出 Reasoning、WorkOrder、Verification evidence、User-facing result 等 RoleExecutionAgent 套话。",
        ]
    )
    if desktop_companion_mode and not json_mode:
        lines.append(
            "你同时是桌面伴侣 Jachin：像 Jarvis/Samantha 一样温和机敏、高共情，可提及自己在右下角 Orb。"
        )
        lines.append("输出口语化短句（优先 15 字内），避免长段、表格、复杂 Markdown 与密集标点。")
        lines.append("若用户仅点名/问候（如“Jachin”“在吗”），首句控制在 1-3 字。")
        lines.append("闲聊先接住情绪，不用客服腔；禁止说教、禁止强行总结、不要追问“还有什么问题吗”。")
        if _dc_just_interrupted:
            lines.append("系统提示刚被打断：直接进入新任务，不复读上轮内容。")
    if json_mode:
        lines.append(
            "你只输出一个合法 JSON 对象。不要 markdown 代码围栏，不要解释性前后缀，除非用户明确要求。"
        )
    else:
        lines.append("只输出用户要求的正文。")
    if _voice_fast_prompt and not json_mode:
        lines.append("本轮来自语音快路径：只回 1-2 个短句，优先 30 字内；禁止长安抚、解释、总结和 Markdown。")
        if str(_dc_ctx.get("voice_fast_lane_kind") or "").strip().lower() == "light_query":
            lines.append("\u8fd9\u4e00\u8f6e\u662f\u8bed\u97f3\u8f7b\u95ee\u7b54\uff0c\u5fc5\u987b\u56de\u7b54\u7528\u6237\u95ee\u7684\u5177\u4f53\u95ee\u9898\uff0c\u4e0d\u8981\u53ea\u8bf4\u2018\u6211\u5728\u2019\u3001\u2018\u542c\u7740\u5462\u2019\u6216\u5176\u4ed6 presence ack\u3002")
    lines.append(_MERMAID_SAFE_RULES_SYSTEM_BLOCK_SLIM.strip())

    try:
        from l3_node.jachin_workspace_rules import get_jachin_workspace_rules_snippet

        jr = get_jachin_workspace_rules_snippet()
        if (jr or "").strip():
            lines.append("\n【工作区规则摘录】\n" + jr.strip())
    except ImportError:
        pass
    return "\n".join(lines)


async def _run_direct_llm_completion(
    *,
    messages: list[dict[str, Any]],
    engine: LiteLLMEngine,
    prompt_cycle: int | None,
    json_mode: bool,
    on_chunk: Optional[Callable[[str], Awaitable[None]]],
    run_id: str,
    token_acc: dict[str, int],
    token_budget: int | None,
    cancel_event: asyncio.Event,
    model_override: str | None = None,
    general_chitchat: bool = False,
    desktop_companion_mode: bool = False,
    desktop_companion_context: Optional[dict[str, Any]] = None,
) -> str:
    sys_p = await _build_direct_system_prompt(
        prompt_cycle=prompt_cycle,
        json_mode=json_mode,
        general_chitchat=general_chitchat,
        desktop_companion_mode=desktop_companion_mode,
        desktop_companion_context=desktop_companion_context,
        voice_fast_lane_prompt=bool((desktop_companion_context or {}).get("voice_fast_lane") or (desktop_companion_context or {}).get("server_voice_fast_lane")),
    )
    api_messages: list[dict[str, Any]] = [{"role": "system", "content": sys_p}]
    api_messages.extend(messages)
    _dc_ctx = desktop_companion_context if isinstance(desktop_companion_context, dict) else {}
    _voice_fast_direct = bool(_dc_ctx.get("voice_fast_lane") or _dc_ctx.get("server_voice_fast_lane"))
    _voice_fast_max_tokens = 64
    if _voice_fast_direct:
        try:
            _voice_fast_max_tokens = max(16, min(128, int(os.environ.get("JACHIN_VOICE_FAST_LANE_MAX_TOKENS", "64"))))
        except (TypeError, ValueError):
            _voice_fast_max_tokens = 64
    base_kw: dict[str, Any] = {
        "l3_call_purpose": "voice_fast_lane_user_facing_reply_agent" if _voice_fast_direct else "user_facing_reply_agent",
        "l3_token_accumulator": token_acc,
        "l3_token_budget_max": token_budget,
        "l3_cancel_event": cancel_event,
        "temperature": 0.25 if _voice_fast_direct else 0.2,
        "max_tokens": _voice_fast_max_tokens if _voice_fast_direct else 8192,
    }
    if _voice_fast_direct:
        base_kw["extra_body"] = {"enable_thinking": False}
    if (model_override or "").strip():
        base_kw["l3_override_model"] = (model_override or "").strip()
    elif _voice_fast_direct:
        base_kw["l3_override_model"] = os.environ.get("JACHIN_VOICE_FAST_LANE_MODEL", "dashscope/qwen3.5-flash")
    attempts_kw: list[dict[str, Any]] = []
    if json_mode:
        attempts_kw.append({**base_kw, "response_format": {"type": "json_object"}})
        attempts_kw.append(dict(base_kw))
    else:
        attempts_kw.append(dict(base_kw))

    last_err: BaseException | None = None
    for i, kw in enumerate(attempts_kw):
        try:
            if on_chunk:
                stream_kw = dict(kw)
                stream_kw["l3_run_id"] = run_id
                text = await engine.generate_response_stream(
                    api_messages,
                    chunk_callback=on_chunk,
                    **stream_kw,
                )
            else:
                raw = await engine.generate_response(api_messages, tools=None, **kw)
                if isinstance(raw, dict):
                    text = (raw.get("content") or "") or ""
                else:
                    text = str(raw or "")
            return (text or "").strip()
        except Exception as e:
            last_err = e
            if json_mode and i == 0 and len(attempts_kw) > 1:
                logger.warning(
                    "[L3 Agent] direct_llm json_object 被拒，重试无 response_format: %s",
                    e,
                )
                continue
            raise
    raise last_err or RuntimeError("user_facing_reply_agent failed")


async def _paraphrase_abort_slot_reply_async(
    *,
    base_msg: str,
    user_input: str,
    engine: LiteLLMEngine,
) -> str:
    """§8.1：槽位 Abort 后单次闲聊式改写，不执行挂起任务。"""
    fm = [
        {
            "role": "system",
            "content": (
                "你是温和的前台助手。用户任务因必填信息多次未补齐而中止。"
                "请将下列系统说明改写为一两句自然中文：不编造已执行的操作，可建议用户用一句完整指令重试。"
                "不要使用 Markdown 标题。"
            ),
        },
        {
            "role": "user",
            "content": f"系统说明：\n{base_msg}\n\n用户原话：\n{(user_input or '')[:800]}",
        },
    ]
    try:
        raw = await engine.generate_response(
            fm,
            temperature=0.45,
            max_tokens=320,
            l3_call_purpose="abort_slot_chat_fallback",
        )
        text = (raw.get("content", raw) if isinstance(raw, dict) else str(raw or "")).strip()
        return text or base_msg
    except Exception as e:
        logger.debug("[L3 Agent] abort_slot_chat_fallback 失败: %s", e)
        return base_msg


async def _close_kernel_plan_without_text_transport(
    *,
    plan: Any,
    engine: LiteLLMEngine,
    user_input: str,
    prior_messages: list[dict[str, Any]],
    run_id: str,
) -> str:
    """Close a turn after Kernel planning without falling back to text actions.

    Hard-migration rule: once the Cognitive Kernel has planned the turn, the
    main flow must not return to the old Reasoning/WorkOrder/Verification evidence loop. If no
    direct RoleExecutor path exists, close the turn with explicit evidence.
    """

    if plan is None:
        final_text = "当前没有形成有效的认知内核计划，已停止执行；没有回退到旧工具循环。"
        close_turn(turn_id=run_id, final_text=final_text, executed_work_orders=[], verification_reports=[], aborted=True)
        return final_text

    contract = getattr(plan, "decision_contract", None)
    summary = getattr(plan, "review_summary", None)
    task_type = str(getattr(contract, "task_type", "") or getattr(summary, "task_type", "") or "conversation")
    turn_id = str(getattr(contract, "turn_id", "") or run_id)

    if task_type == "conversation":
        messages = list(prior_messages or [])
        messages.append({"role": "user", "content": user_input or ""})
        try:
            final_text = await _run_direct_llm_completion(
                messages=messages,
                engine=engine,
                prompt_cycle=None,
                json_mode=False,
                on_chunk=None,
                run_id=run_id,
                token_acc={},
                token_budget=None,
                cancel_event=asyncio.Event(),
                general_chitchat=True,
            )
        except Exception as exc:
            logger.warning("[CognitiveKernel] UserFacingReplyAgent kernel-only reply failed: %s", exc)
            final_text = "我已理解你的问题，但当前回复生成失败；没有执行任何外部操作。"
        close_turn(turn_id=turn_id, final_text=final_text, executed_work_orders=[], verification_reports=[], aborted=False)
        return final_text

    clarification = str(getattr(contract, "clarification_question", "") or "").strip()
    if clarification:
        close_turn_waiting_user(
            turn_id=turn_id,
            final_text=clarification,
            pending_decision=getattr(contract, "to_dict", lambda: {})(),
            next_turn_hints=["请补充缺失信息后重新发起任务。"],
        )
        return clarification

    work_orders = list(getattr(plan, "work_orders", []) or [])
    role = ""
    tool = ""
    if work_orders:
        wo = work_orders[0]
        role = str(getattr(wo, "role_agent", "") or "")
        inputs = getattr(wo, "inputs", {}) or {}
        if isinstance(inputs, dict):
            tool = str(inputs.get("tool") or "")
    final_text = (
        "该任务已进入认知内核，但当前没有可用的 direct RoleExecutor 通道，"
        "因此已停止执行，避免回退到旧的文本工具循环。"
    )
    if role or tool:
        final_text += f" 待接入角色/工具：{role or 'unknown'} / {tool or 'unknown'}。"
    close_turn(turn_id=turn_id, final_text=final_text, executed_work_orders=[], verification_reports=[], aborted=True)
    return final_text


class _DirectBypassCtx:
    __slots__ = ("_executed_tools_this_run",)

    def __init__(self) -> None:
        self._executed_tools_this_run: set[str] = set()


async def run_agent(
    user_input: str,
    engine: LiteLLMEngine,
    *,
    max_iterations: int = MAX_KERNEL_TRANSPORT_ITERATIONS,
    on_step: Optional[Callable[[str, str, str], None]] = None,
    on_chunk: Optional[Callable[[str], Awaitable[None]]] = None,
    _system_prompt_override: Optional[str] = None,
    _initial_messages: Optional[list[dict[str, Any]]] = None,
    _session_messages: Optional[list[dict[str, Any]]] = None,
    implicit_signals: Optional[dict[str, Any]] = None,
    implicit_attribution: Optional[dict[str, Any]] = None,
    _allowed_skills_override: Optional[list[str]] = None,
    _delegate_depth: int = 0,
    attachments_metadata: Optional[list[dict[str, Any]]] = None,
    gateway_context_bundle: Any = None,
    short_memory_context: str = "",
    gateway_system_state: Optional[str] = None,
    gateway_clarification_handle: str = "",
    gateway_clarification_deadline_ts: float = 0.0,
    gateway_workspace_dir: str | None = None,
) -> str:
    """
    ?? L3 Memory-first Cognitive Kernel ???????????????????? WorkOrder?
    支持 _system_prompt_override 供子 Agent 使用。
    _session_messages: 若提供，将作为历史上下文并在调用结束后被更新为完整对话（含本轮），供多轮对话复用。
    implicit_signals: 可选 {"skip": true, "dwell_sec"|"dwell_ms": n, "assistant_echo": "...", "source": "lark"} → 见 docs/IMPLICIT_SIGNALS.md。
    implicit_attribution: 可选 {"channel": "lark_im"|"websocket"|"http_agent_run", "lark_chat_id": "..."} → 每轮写 implicit_turn_attribution；**lark_chat_id** 用于按会话隔离待确认 JD（同意/兜底）。
    _allowed_skills_override: 非 None 时覆盖 _get_allowed_skills()（供后台任务沿用投递时的白名单快照）。
    _delegate_depth: delegate 嵌套深度（子 Agent 由 delegate 路径传入，用于 max_delegate_depth 与 Token 子预算）。
    attachments_metadata: §12.1 附件列表（元数据 + 可选 local_path/base64 等实体），入 GatewayContextBundle；
        run_agent 内会组装 OpenAI 多模态 user content（图片 data URL；PDF/docx/txt 抽文本拼入）。
    gateway_context_bundle: 若传入则沿用；否则由 user_input 构造（战役一 GatewayContextBundle）。
    short_memory_context: 兼容旧签名；主循环短期记忆只通过 MemoryRecallAgent 进入 RelevantMemoryBundle。
    gateway_system_state: 如 AWAITING_CLARIFICATION，配合 gateway_clarification_* 驱动澄清门控（仅在未传 gateway_context_bundle 时生效）。
    gateway_workspace_dir: 显式 Git/嗅探工作区目录（绝对路径为佳）；空则尝试 implicit_attribution 的
        workspace_dir / git_workspace_dir / effective_workspace_root，再回退 ~/.jachin/workspace。
    """
    run_id = str(uuid.uuid4())
    try:
        from l3_node.engine.persistent_hook_log import ensure_persistent_hook_log_registered

        ensure_persistent_hook_log_registered()
    except Exception:
        pass
    _ws_tok = None
    _mem_shard_tok = None
    _lark_cid = ""
    _bg_channel = ""
    _voice_diagnostics = None
    if implicit_attribution and isinstance(implicit_attribution, dict):
        _lark_cid = str(
            implicit_attribution.get("lark_chat_id") or implicit_attribution.get("chat_id") or ""
        ).strip()
        _bg_channel = str(implicit_attribution.get("channel") or "").strip()
        _vd = implicit_attribution.get("voice_diagnostics")
        _voice_diagnostics = _vd if isinstance(_vd, dict) else None
    _desktop_companion_ctx: dict[str, Any] = {}
    if implicit_signals and isinstance(implicit_signals, dict):
        for _ck in ("just_interrupted", "barge_in", "just_barged_in", "wake_triggered_recently"):
            if _ck in implicit_signals:
                _desktop_companion_ctx[_ck] = bool(implicit_signals.get(_ck))
        for _ck in (
            "voice_decision_id",
            "voice_dispatch_tier",
            "voice_intent_class",
            "voice_dispatch_lane",
            "voice_interrupt_verdict",
            "voice_route_source",
            "voice_route_notes",
            "voice_confidence",
            "voice_task_title",
            "voice_active_task_ids",
            "voice_raw_stt_text",
            "voice_asr_raw_text",
            "voice_corrected_text",
            "voice_final_text",
            "voice_routed_text",
            "voice_stt_source",
            "voice_stt_confidence",
            "voice_stt_backend",
            "voice_stt_user_message",
            "voice_stt_user_message_source",
            "voice_reply_composer",
            "voice_reply_plan",
            "target_task_id",
            "task_context_summary",
            "source",
            "voice_fast_lane_kind",
            "voice_allow_template_reply",
            "voice_route_evidence",
            "force_background",
            "acceptance_round",
            "inject_task_context",
            "max_foreground_tool_sec",
            "awaiting_confirmation",
            "clarification_pending",
        ):
            if _ck in implicit_signals:
                _desktop_companion_ctx[_ck] = implicit_signals.get(_ck)
    _desktop_companion_mode = bool(
        (implicit_signals and isinstance(implicit_signals, dict) and implicit_signals.get("desktop_companion"))
        or (((_lark_cid or "").strip() == "") and str(_bg_channel or "").startswith("websocket_"))
    )
    def _looks_like_voice_fast_lane_text(_text: str) -> bool:
        _t = (_text or "").strip()
        if not _t or len(_t) > 60:
            return False
        _lower = _t.lower()
        if any(_x in _lower for _x in ("http://", "https://", "```")):
            return False
        if any(_x in _t for _x in ("\\", "/", "#", "@", ".md", ".py", ".ts", ".json")):
            return False
        _heavy_words = (
            "\u6587\u4ef6", "\u76ee\u5f55", "\u9879\u76ee", "\u4ee3\u7801", "\u811a\u672c",
            "\u62a5\u544a", "\u603b\u7ed3", "\u6458\u8981", "\u751f\u6210", "\u4fee\u6539",
            "\u5220\u9664", "\u8fd0\u884c", "\u6267\u884c", "\u641c\u7d22", "\u67e5\u627e",
            "\u5206\u6790", "\u8868\u683c", "\u6570\u636e\u5e93", "\u98de\u4e66", "\u540e\u53f0",
            "\u4efb\u52a1", "\u5929\u6c14", "\u6c14\u6e29", "\u51e0\u70b9", "\u65f6\u95f4",
            "\u63d0\u9192", "\u95f9\u949f", "\u6253\u5f00", "\u5173\u95ed", "\u5199",
            "\u4f5c\u6587", "\u5e2e\u6211", "\u7ed9\u6211", "\u8bf7\u4f60",
        )
        if any(_w in _t for _w in _heavy_words):
            return False
        _light_words = (
            "\u4f60\u597d", "\u5728\u5417", "\u4f60\u5728\u5417", "\u65e9\u4e0a\u597d",
            "\u4e2d\u5348\u597d", "\u665a\u4e0a\u597d", "\u542c\u5f97\u5230\u5417",
            "\u542c\u89c1\u5417", "\u8c22\u8c22", "\u6ca1\u4e8b", "\u7b97\u4e86",
            "\u597d\u7684", "\u55ef", "\u54e6", "\u8bb2\u8bdd\u8bb2\u8bdd",
            "\u8bf4\u8bdd\u8bf4\u8bdd", "\u8bf4\u70b9\u8bdd", "\u8ddf\u6211\u8bf4",
        )
        if any(_w in _t for _w in _light_words):
            return True
        return len(_t) <= 32

    _client_voice_chitchat_fast_lane = False
    _server_voice_fast_lane = False
    _skip_context_retrieval = bool(
        implicit_signals
        and isinstance(implicit_signals, dict)
        and implicit_signals.get("skip_context_retrieval")
    )
    _voice_fast_lane = False
    _skip_context_sniffer = bool(
        implicit_signals
        and isinstance(implicit_signals, dict)
        and (implicit_signals.get("skip_context_sniffer") or _skip_context_retrieval or _voice_fast_lane)
    )
    _skip_gateway_enrich = bool(
        implicit_signals
        and isinstance(implicit_signals, dict)
        and (implicit_signals.get("skip_gateway_enrich") or _skip_context_retrieval or _voice_fast_lane)
    )
    _skip_experience_rag = bool(
        implicit_signals
        and isinstance(implicit_signals, dict)
        and (implicit_signals.get("skip_experience_rag") or _skip_context_retrieval or _voice_fast_lane)
    )
    if _voice_fast_lane:
        _desktop_companion_ctx["voice_fast_lane"] = True
    if _server_voice_fast_lane:
        _desktop_companion_ctx["server_voice_fast_lane"] = True
    _lark_turn_dbg_extra: dict[str, str] = (
        {"lark_chat_id": _lark_cid, "lark_reply_chat_id": _lark_cid} if _lark_cid else {}
    )
    _turn_dbg: dict[str, Any] = {}
    _original_user_input = user_input or ""
    try:
        from l3_node.cognitive_kernel.input_adapter import adapt_input_for_cognitive_kernel

        _input_adaptation = adapt_input_for_cognitive_kernel(
            turn_id=run_id,
            user_input=user_input or "",
            session_id=_lark_cid or "",
            channel=_bg_channel or "",
            desktop_companion_context=_desktop_companion_ctx,
            implicit_attribution=implicit_attribution if isinstance(implicit_attribution, dict) else None,
        )
        _desktop_companion_ctx.update(_input_adaptation.desktop_companion_context)
        if _input_adaptation.source.value == "voice" and _input_adaptation.normalized_text:
            user_input = _input_adaptation.normalized_text
            _desktop_companion_ctx["voice_language_raw_input"] = _original_user_input
            _desktop_companion_ctx["voice_language_normalized_text"] = _input_adaptation.normalized_text
            _desktop_companion_ctx["voice_language_changed"] = _input_adaptation.changed
    except Exception as _input_adapter_ex:
        logger.debug("[InputAdapter] skipped: %s", _input_adapter_ex)
    try:
        from l3_node.terminal_turn_debug_log import ensure_turn_started

        ensure_turn_started(
            user_input or "",
            extra={
                "run_id": run_id,
                "channel": _bg_channel or "run_agent",
                "max_iterations": max_iterations,
                **(
                    {
                        "voice_asr_raw_text": str(_desktop_companion_ctx.get("voice_asr_raw_text") or "")[:300],
                        "voice_corrected_text": str(_desktop_companion_ctx.get("voice_corrected_text") or "")[:300],
                        "voice_final_text": str(_desktop_companion_ctx.get("voice_final_text") or "")[:300],
                        "voice_routed_text": str(_desktop_companion_ctx.get("voice_routed_text") or "")[:300],
                        "voice_stt_source": str(_desktop_companion_ctx.get("voice_stt_source") or "")[:80],
                    }
                    if _desktop_companion_ctx
                    else {}
                ),
                **(
                    {
                        "lark_chat_id": _lark_cid,
                        "lark_reply_chat_id": _lark_cid,
                    }
                    if _lark_cid
                    else {}
                ),
                **({"voice_diagnostics": _voice_diagnostics} if _voice_diagnostics else {}),
            },
        )
    except Exception:
        pass
    _gateway_sniffer_ws = ""
    if gateway_workspace_dir and str(gateway_workspace_dir).strip():
        _gateway_sniffer_ws = str(gateway_workspace_dir).strip()
    elif implicit_attribution and isinstance(implicit_attribution, dict):
        for _gwk in ("workspace_dir", "git_workspace_dir", "effective_workspace_root"):
            _gwv = implicit_attribution.get(_gwk)
            if _gwv is not None and str(_gwv).strip():
                _gateway_sniffer_ws = str(_gwv).strip()
                break
    logger.debug("[L3 Agent] run_agent 开始 input_len=%d history=%d", len(user_input or ""), len(_session_messages or []) + len(_initial_messages or []))
    allowed = _allowed_skills_override if _allowed_skills_override is not None else _get_allowed_skills()
    from l3_node.primitives.multi_agent.readonly_agent import (
        is_readonly_subagent_role,
        parse_readonly_role_from_implicit,
        sanitize_allowed_skills_for_readonly,
    )

    _sub_agent_role = parse_readonly_role_from_implicit(
        implicit_attribution if isinstance(implicit_attribution, dict) else None
    )
    _readonly_subagent = is_readonly_subagent_role(_sub_agent_role)
    if _readonly_subagent and allowed is not None and len(allowed) > 0:
        allowed = sanitize_allowed_skills_for_readonly(list(allowed))
    allowlist_diag_source: list[str] | None = list(allowed) if allowed is not None else None
    from l3_node.primitives.tools.tool_pool import allowlist_is_native_only, allowlist_is_tools_denied

    _tools_denied = allowlist_is_tools_denied(_allowed_skills_override)
    _native_only_sub_agent = (
        _delegate_depth > 0
        and _allowed_skills_override is not None
        and not _tools_denied
    )
    if _native_only_sub_agent:
        _native_only_sub_agent = allowlist_is_native_only(_allowed_skills_override)
    _capability_publisher_tool_lock = capability_publisher_tool_lock_enabled(implicit_attribution)
    if _tools_denied:
        allowed = []
    elif not _native_only_sub_agent and not _capability_publisher_tool_lock:
        allowed = expand_allowed_skills_with_implicit_sqlite_read(allowed)
        allowed = expand_allowed_skills_with_local_mcp(allowed)
    # 优先使用 _session_messages（多轮对话），否则用 _initial_messages（须先于 MCP 拉取与 Gateway 流水线）
    if _session_messages is not None:
        messages = list(_session_messages)
    elif _initial_messages:
        messages = list(_initial_messages)
    else:
        messages = []
    prior_messages = list(messages)

    _cognitive_kernel_plan = None
    _cognitive_kernel_prompt_block = ""
    try:
        _cognitive_ctx = await build_cognitive_turn_context(
            run_id=run_id,
            user_input=user_input or "",
            channel=_bg_channel or "",
            session_id=_lark_cid or "",
            prior_messages=prior_messages,
            attachments_metadata=attachments_metadata,
            implicit_attribution=implicit_attribution,
            desktop_companion_context=_desktop_companion_ctx,
            gateway_system_state=_gateway_sniffer_ws,
        )
        try:
            from l3_node.terminal_turn_debug_log import log_cognitive_mainline_context

            log_cognitive_mainline_context(_cognitive_ctx)
        except Exception:
            pass
        _cognitive_kernel_plan = plan_cognitive_turn(_cognitive_ctx, emit_non_execution_closure=False)
        try:
            from l3_node.terminal_turn_debug_log import log_cognitive_mainline_plan

            log_cognitive_mainline_plan(_cognitive_kernel_plan)
        except Exception:
            pass
        _cognitive_kernel_prompt_block = (
            _cognitive_ctx.prompt_block(max_chars=4000)
            + "\n[Cognitive Kernel Plan]\n"
            + json.dumps(_cognitive_kernel_plan.to_dict(), ensure_ascii=False, default=str)[:5000]
        )
        try:
            from l3_node.terminal_turn_debug_log import log_main_agent_effective_prompt

            log_main_agent_effective_prompt(
                stage="cognitive_kernel_planning_context",
                cognitive_kernel_prompt_block=_cognitive_kernel_prompt_block,
                tools_count=0,
                messages_count=len(prior_messages),
                sent_to_llm=False,
                note="Cognitive Kernel is the only top-level main loop. Text LLM paths run only as authorized role agents after kernel planning.",
            )
        except Exception:
            pass
        logger.info(
            "[CognitiveKernel] planned turn=%s task=%s workflow=%s work_orders=%d",
            run_id[:12],
            _cognitive_kernel_plan.decision_contract.task_type,
            _cognitive_kernel_plan.decision_contract.selected_workflow,
            len(_cognitive_kernel_plan.work_orders),
        )
    except Exception as _ck_plan_ex:
        logger.debug("[CognitiveKernel] planning skipped: %s", _ck_plan_ex)

    if _voice_fast_lane and not attachments_metadata and _delegate_depth == 0:
        _template_reply = _pick_voice_exact_template_reply(
            user_input or "",
            str(_desktop_companion_ctx.get("voice_raw_stt_text") or ""),
            str(_desktop_companion_ctx.get("voice_asr_raw_text") or ""),
            str(_desktop_companion_ctx.get("voice_corrected_text") or ""),
            str(_desktop_companion_ctx.get("voice_routed_text") or ""),
        )
        if _template_reply is not None:
            if _desktop_companion_mode and engine is not None:
                try:
                    from l3_node.companion_reply_adapter import adapt_companion_reply_async

                    _template_reply = await adapt_companion_reply_async(
                        base_msg=_template_reply,
                        user_input=user_input or "",
                        engine=engine,
                        reason="voice_exact_template",
                    )
                except Exception:
                    pass
            if _session_messages is not None:
                messages.append({"role": "user", "content": user_input or ""})
                messages.append({"role": "assistant", "content": _template_reply})
                _session_messages.clear()
                _session_messages.extend(messages[-30:])
            _schedule_voice_template_turn_commit_async(user_input or "", _template_reply)
            _turn_dbg["answer"] = _template_reply
            exec_trace(
                logger,
                "Voice Fast Path: exact template reply run_id=%s input_len=%d reply_len=%d",
                run_id[:12],
                len(user_input or ""),
                len(_template_reply),
            )
            try:
                from l3_node.terminal_turn_debug_log import finalize_top_level_turn

                finalize_top_level_turn(
                    _template_reply,
                    delegate_depth=_delegate_depth,
                    run_id=run_id,
                    channel=_bg_channel,
                    extra={**(_lark_turn_dbg_extra or {}), "voice_template_reply": "1"},
                )
            except Exception:
                pass
            return _template_reply

    # ── 改造点 B：弱化历史摘要的指令性 ─────────────────────────────────────
    # 对 messages 里已有的 role=system 消息（由上游 memory / summary 注入的历史摘要）
    # 包裹弱化标签，避免被模型当作「当前执行指令」而覆盖用户原始问题。
    # 仅处理内容里含「历史摘要」/ 「摘要」特征词的 system 消息，跳过工具描述等。
    _HIST_SUMMARY_SIGNALS = ("历史摘要", "【历史摘要】", "history_summary", "[历史摘要]")
    _history_isolation_enabled = (
        os.environ.get("JACHIN_HISTORY_ISOLATION_DISABLE", "").strip().lower()
        not in ("1", "true", "yes")
    )
    if _history_isolation_enabled:
        _wrapped_count = 0
        for _idx, _hm in enumerate(messages):
            if not isinstance(_hm, dict) or _hm.get("role") != "system":
                continue
            _hc = str(_hm.get("content") or "")
            if not any(sig in _hc for sig in _HIST_SUMMARY_SIGNALS):
                continue
            _already_wrapped = "[以下仅为历史聊天记录摘要" in _hc
            if _already_wrapped:
                continue
            messages[_idx] = {
                "role": "system",
                "content": (
                    "[以下仅为历史聊天记录摘要，仅供提供背景信息，"
                    "绝对不要将其中提到的旧任务视为当前执行指令]\n"
                    f"{_hc}\n"
                    "[历史摘要结束，请以最新用户消息为准执行当前任务]"
                ),
            }
            _wrapped_count += 1
        if _wrapped_count:
            logger.debug(
                "[L3 Agent] 历史摘要弱化（改造B）：包裹了 %d 条 system 历史摘要 run_id=%s",
                _wrapped_count,
                run_id[:12],
            )
    # ──────────────────────────────────────────────────────────────────────

    # L5 盲测：/clear 在进入网关与 LLM 前清空会话缓冲，避免短期上下文「作弊」
    if (user_input or "").strip() == "/clear":
        if _session_messages is not None:
            _session_messages.clear()
        logger.info("[L3 Agent] /clear：已清空会话消息缓冲 run_id=%s", run_id[:12])
        return "[System] 后端上下文已强制清空。"

    if (
        implicit_attribution
        and isinstance(implicit_attribution, dict)
        and str(implicit_attribution.get("lark_busy_followup_kind") or "") == "supplement"
    ):
        user_input = (
            "【飞书·排队补充意图】上一条主任务仍在执行时用户续发此条，话术上多为**对当前任务的补充/纠正**（未必是全新大任务）。"
            "请与上文用户目标合并理解；若明确是新任务请先简要确认再分步处理。\n\n"
            + (user_input or "")
        )

    exec_trace(
        logger,
        "run_agent 开始 run_id=%s channel=%s input_len=%d history_msgs=%d",
        run_id[:12],
        (_bg_channel or "-"),
        len(user_input or ""),
        len(messages),
    )

    _gateway_bridge_fmt: Any = None
    _gateway_bundle = gateway_context_bundle
    if _delegate_depth == 0 and not _skip_context_retrieval:
        try:
            from l3_node.intent_gateway.bundle import build_gateway_bundle
            from l3_node.intent_gateway.gateway_pipeline import apply_gateway_ingress_pipeline

            # Memory SSOT: Gateway no longer receives a separate short-memory summary.
            # Conversation history is recalled by MemoryRecallAgent into RelevantMemoryBundle.
            _gw_mem = ""
            if _gateway_bundle is None:
                _gateway_bundle = build_gateway_bundle(
                    user_input=user_input or "",
                    short_memory_context=_gw_mem,
                    correlation_id=run_id,
                    session_id=_lark_cid,
                    implicit_attribution=implicit_attribution,
                    attachments_metadata=attachments_metadata,
                    system_state=gateway_system_state or "NORMAL",
                    clarification_handle=gateway_clarification_handle or "",
                    clarification_deadline_ts=float(gateway_clarification_deadline_ts or 0.0),
                )
            if _gateway_bundle is not None:
                await apply_gateway_ingress_pipeline(
                    _gateway_bundle,
                    user_input or "",
                    prior_messages,
                    on_step=on_step,
                    run_id=run_id,
                    workspace_dir=_gateway_sniffer_ws,
                    skip_context_sniffer=_skip_context_sniffer,
                )
            exec_trace(logger, "网关入站流水线完成 run_id=%s", run_id[:12])
            try:
                from l3_node.intent_gateway.config import get_intent_gateway_config
                from l3_node.intent_gateway.format_signals_cache import (
                    format_signals_from_dict,
                    format_signals_to_dict,
                )
                from l3_node.intent_gateway.gateway_enrich import enrich_gateway_async
                from l3_node.intent_gateway.semantic_cache import get_semantic_cache
                from l3_node.intent_gateway.semantic_router import infer_semantic_route_hint, merge_route_hints
                from l3_node.routing.output_format_signals import analyze_output_format_signals

                _ig0 = get_intent_gateway_config()
                if _skip_gateway_enrich:
                    _gateway_bridge_fmt = analyze_output_format_signals(_gateway_bundle.classification_text or user_input or "")
                    try:
                        from l3_node.intent_gateway.semantic_router import infer_semantic_route_hint, merge_route_hints

                        _gateway_bundle.extra["semantic_route_merged"] = merge_route_hints(
                            infer_semantic_route_hint(_gateway_bundle.classification_text),
                            _gateway_bundle.extra.get("embedding_route"),
                        )
                    except Exception:
                        pass
                    raise RuntimeError("voice_fast_lane_skip_gateway_enrich")
                _ct0_key = (_gateway_bundle.classification_text or "").strip() or (
                    _gateway_bundle.user_input or ""
                )
                _any_enrich = bool(
                    _ig0.get("embedding_router_enabled")
                    or _ig0.get("classification_llm_rewrite_enabled")
                    or _ig0.get("multimodal_routing_head_enabled")
                )
                _enrich_keys = (
                    "embedding_route",
                    "embedding_ood_sparse",
                    "multimodal_route_head",
                    "classification_llm_routing_rewrite",
                )
                _cache_hit = False
                if _ig0.get("semantic_cache_enabled"):
                    _c0 = get_semantic_cache()
                    _k0 = _c0.make_key(
                        _gateway_bundle.tenant_id or "default",
                        _ct0_key,
                        _gateway_bundle.registry_version,
                        session_id=_gateway_bundle.session_id or "",
                    )
                    _snap0 = _c0.get(_k0)
                    if isinstance(_snap0, dict) and isinstance(_snap0.get("output_format_signals"), dict):
                        if not _any_enrich or _snap0.get("gateway_enrich") is not None:
                            ge = _snap0.get("gateway_enrich") or {}
                            if isinstance(ge, dict):
                                for _gk, _gv in ge.items():
                                    _gateway_bundle.extra[_gk] = _gv
                            if _snap0.get("routing_utterance_cached"):
                                _gateway_bundle.routing_utterance = str(_snap0["routing_utterance_cached"])
                            _gateway_bundle.rebuild_classification_text()
                            _gateway_bridge_fmt = format_signals_from_dict(_snap0["output_format_signals"])
                            _cache_hit = True
                if not _cache_hit:
                    await enrich_gateway_async(
                        _gateway_bundle, engine, user_input or "", prior_messages
                    )
                    _gateway_bundle.rebuild_classification_text()
                    _ct_after = _gateway_bundle.classification_text
                    _gateway_bridge_fmt = analyze_output_format_signals(_ct_after)
                    if _ig0.get("semantic_cache_enabled"):
                        _c1 = get_semantic_cache()
                        _k1 = _c1.make_key(
                            _gateway_bundle.tenant_id or "default",
                            _ct0_key,
                            _gateway_bundle.registry_version,
                            session_id=_gateway_bundle.session_id or "",
                        )
                        _ge_snap = {
                            k: _gateway_bundle.extra.get(k)
                            for k in _enrich_keys
                            if k in _gateway_bundle.extra
                        }
                        _c1.set(
                            _k1,
                            {
                                "gateway_enrich": _ge_snap,
                                "routing_utterance_cached": _gateway_bundle.routing_utterance,
                                "output_format_signals": format_signals_to_dict(_gateway_bridge_fmt),
                            },
                        )
                _gateway_bundle.extra["semantic_route_merged"] = merge_route_hints(
                    infer_semantic_route_hint(_gateway_bundle.classification_text),
                    _gateway_bundle.extra.get("embedding_route"),
                )
            except Exception as _ge:
                logger.debug("[L3 Agent] gateway enrich/cache 跳过: %s", _ge)
                try:
                    from l3_node.intent_gateway.semantic_router import infer_semantic_route_hint, merge_route_hints

                    _gateway_bundle.extra["semantic_route_merged"] = merge_route_hints(
                        infer_semantic_route_hint(_gateway_bundle.classification_text),
                        _gateway_bundle.extra.get("embedding_route"),
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.warning("[L3 Agent] GatewayContextBundle 构造失败，回退裸字符串: %s", e)
            _gateway_bundle = None

    # 实时外部知识意图：独立于 enrich / semantic cache，避免缓存命中 enrich 跳过时的漏判
    if _delegate_depth == 0 and _gateway_bundle is not None and not _voice_fast_lane and not _skip_context_retrieval:
        try:
            from l3_node.intent_gateway.classification_llm import infer_requires_realtime_knowledge_async
            from l3_node.intent_gateway.config import get_intent_gateway_config

            _ig_rt = get_intent_gateway_config()
            _skip_rt_for_multimodal_image = False
            try:
                for _x in attachments_metadata or []:
                    if not isinstance(_x, dict):
                        continue
                    if _x.get("has_image"):
                        _skip_rt_for_multimodal_image = True
                        break
                    _m = str(_x.get("mime") or "").lower()
                    if _m.startswith("image/"):
                        _skip_rt_for_multimodal_image = True
                        break
                if not _skip_rt_for_multimodal_image:
                    for _sm in getattr(_gateway_bundle, "attachments_sanitized", None) or []:
                        if getattr(_sm, "has_image", False):
                            _skip_rt_for_multimodal_image = True
                            break
            except Exception:
                pass
            if _skip_rt_for_multimodal_image:
                _gateway_bundle.requires_realtime_knowledge = False
                _gateway_bundle.extra["requires_realtime_knowledge"] = False
                logger.info(
                    "[IntentGatewayObs] requires_realtime_knowledge=False（本轮含图片附件，跳过 Tavily 预取以免与会话摘要串味）"
                )
            elif bool(_ig_rt.get("realtime_knowledge_llm_enabled", True)):
                try:
                    _to_rt = float(_ig_rt.get("realtime_knowledge_llm_timeout_sec", 2.5))
                except (TypeError, ValueError):
                    _to_rt = 2.5
                _gateway_bundle.requires_realtime_knowledge = await infer_requires_realtime_knowledge_async(
                    engine=engine,
                    user_input=user_input or "",
                    classification_text=_gateway_bundle.classification_text or "",
                    timeout_sec=_to_rt,
                )
                _gateway_bundle.extra["requires_realtime_knowledge"] = _gateway_bundle.requires_realtime_knowledge
                logger.info(
                    "[IntentGatewayObs] requires_realtime_knowledge=%s",
                    _gateway_bundle.requires_realtime_knowledge,
                )
        except Exception as _rt_e:
            logger.debug("[L3 Agent] realtime_knowledge classify 跳过: %s", _rt_e)

    _domain_experts_list: list[str] = []
    if _delegate_depth == 0 and _gateway_bundle is not None and not _voice_fast_lane and not _skip_context_retrieval:
        try:
            from l3_node.intent_gateway.classification_llm import infer_domain_experts_async
            from l3_node.intent_gateway.config import get_intent_gateway_config

            _ig_de = get_intent_gateway_config()
            if bool(_ig_de.get("domain_experts_llm_enabled", True)):
                try:
                    _to_de = float(_ig_de.get("domain_experts_llm_timeout_sec", 3.0))
                except (TypeError, ValueError):
                    _to_de = 3.0
                _gateway_bundle.domain_experts = await infer_domain_experts_async(
                    engine=engine,
                    user_input=user_input or "",
                    classification_text=_gateway_bundle.classification_text or "",
                    timeout_sec=_to_de,
                )
                _gateway_bundle.extra["domain_experts"] = list(_gateway_bundle.domain_experts or [])
                logger.info(
                    "[IntentGatewayObs] domain_experts=%s",
                    json.dumps(_gateway_bundle.domain_experts, ensure_ascii=False),
                )
        except Exception as _de_e:
            logger.debug("[L3 Agent] domain_experts 分类跳过: %s", _de_e)
        try:
            _domain_experts_list = [
                str(x).strip()
                for x in (getattr(_gateway_bundle, "domain_experts", None) or [])
                if str(x).strip()
            ][:3]
        except Exception:
            _domain_experts_list = []

    _vision_forbid_web_fetch = False
    if _skip_context_retrieval:
        tools = []
        exec_trace(
            logger,
            "Fast Path: 跳过工具池加载 run_id=%s reason=skip_context_retrieval",
            run_id[:12],
        )
    else:
        try:
            tools = await assemble_tool_pool(
                allowed_skills=allowed,
                gateway_bundle=_gateway_bundle,
                bg_channel=_bg_channel or None,
                logger=logger,
                allowlist_diag_source=allowlist_diag_source,
                readonly_mode=_readonly_subagent,
            )
            try:
                from l3_node.multimodal_tool_policy import filter_tools_for_vision_image_turn

                tools, _vision_forbid_web_fetch = filter_tools_for_vision_image_turn(
                    tools,
                    user_input=user_input or "",
                    attachments_metadata=attachments_metadata,
                )
            except Exception as _mtp_e:
                logger.debug("[L3 Agent] multimodal_tool_policy 跳过: %s", _mtp_e)

            try:
                if (_bg_channel or "") != "background_task":
                    from l3_node.memory_nexus_bridge import (
                        async_filter_tools_for_dynamic_retrieval,
                        dynamic_tool_retrieval_enabled,
                    )

                    if dynamic_tool_retrieval_enabled():
                        _raw_k = (os.environ.get("JACHIN_DYNAMIC_TOOL_TOP_K") or "5").strip()
                        try:
                            _top_k = int(_raw_k or "5")
                        except ValueError:
                            _top_k = 5
                        try:
                            tools = await async_filter_tools_for_dynamic_retrieval(
                                tools,
                                user_input or "",
                                limit=_top_k,
                            )
                        except Exception as _dtr_inner:
                            logger.warning(
                                "[L3 Agent] 动态工具检索异常，保持全量工具池: %s",
                                _dtr_inner,
                            )
            except Exception as _dtr_e:
                logger.debug("[L3 Agent] 动态工具检索跳过（保持全量池）: %s", _dtr_e)
        except Exception as _pool_ex:
            import traceback

            logger.exception("[L3 Agent] assemble_tool_pool 失败，降级为仅内置工具: %s", _pool_ex)
            try:
                from l3_node.terminal_turn_debug_log import append_section

                append_section(
                    "[run_agent] assemble_tool_pool 异常（已降级内置池）",
                    traceback.format_exc(),
                )
            except Exception:
                pass
            tools = load_tools(allowed_skills=allowed)
            _vision_forbid_web_fetch = False
            if _readonly_subagent:
                from l3_node.primitives.multi_agent.readonly_agent import filter_tools_for_readonly_subagent

                tools = filter_tools_for_readonly_subagent(tools)
    _intent_orchestrator_decision = None
    _hidca_strip_lark_identity = False
    try:
        from l3_node.intent_orchestrator import (
            analyze_intent_async,
            format_hidca_prompt_block,
            prune_tools_for_hidca,
            sandbox_implicit_attribution,
            write_router_evidence,
        )

        _intent_orchestrator_decision = await analyze_intent_async(
            user_input or "",
            tools=tools,
            allowed=allowed,
            implicit_attribution=implicit_attribution if isinstance(implicit_attribution, dict) else None,
            engine=engine,
        )
        tools, _hidca_prune_meta = prune_tools_for_hidca(tools, _intent_orchestrator_decision)
        _sandboxed_implicit, _hidca_stripped_keys = sandbox_implicit_attribution(
            implicit_attribution if isinstance(implicit_attribution, dict) else None,
            _intent_orchestrator_decision,
        )
        if _hidca_stripped_keys:
            implicit_attribution = _sandboxed_implicit
            _hidca_strip_lark_identity = True
        _gw_inject = (_gw_inject or "") + format_hidca_prompt_block(_intent_orchestrator_decision)
        _intent_orchestrator_decision.hidca.update(_hidca_prune_meta)
        _intent_orchestrator_decision.hidca["stripped_context_keys"] = list(_hidca_stripped_keys or [])
        _io_evidence_path = write_router_evidence(_intent_orchestrator_decision)
        exec_trace(
            logger,
            "[IntentOrchestrator] run_id=%s domain=%s tools=%d->%d chosen=%s stripped=%s evidence=%s",
            run_id[:12],
            _intent_orchestrator_decision.hidca.get("semantic_router_domain"),
            _intent_orchestrator_decision.hidca.get("tools_before_prune"),
            _intent_orchestrator_decision.hidca.get("tools_after_prune"),
            _intent_orchestrator_decision.chosen.get("tool_id"),
            ",".join(_hidca_stripped_keys or []),
            _io_evidence_path,
        )
        try:
            from l3_node.terminal_turn_debug_log import append_section

            append_section(
                "[IntentOrchestrator] decision package",
                json.dumps(_intent_orchestrator_decision.to_dict(), ensure_ascii=False, indent=2),
            )
        except Exception:
            pass
    except Exception as _io_ex:
        logger.warning("[IntentOrchestrator] skipped: %s", _io_ex)
    exec_trace(
        logger,
        "工具列表就绪 run_id=%s count=%d bg_channel=%s",
        run_id[:12],
        len(tools),
        (_bg_channel or "-"),
    )
    try:
        log_run_agent_start(
            run_id=run_id,
            user_input=user_input or "",
            history_msgs=len(messages),
            max_iterations=max_iterations,
            n_tools=len(tools),
            channel=_bg_channel or "",
        )
        log_pipeline_phase(
            "run_agent_tools_ready",
            f"run_id={run_id[:12]} n_tools={len(tools)} allowlist_set={allowed is not None}",
        )
    except Exception:
        pass
    try:
        from core.deep_execution_log import format_messages_for_deep, format_tools_brief
        from l3_node.terminal_turn_debug_log import append_section

        append_section(
            "[run_agent] 本轮可见工具池（assemble_tool_pool 之后）",
            f"run_id={run_id}\nn_tools={len(tools)}\n{format_tools_brief(tools)}",
        )
        append_section(
            "[run_agent] 当前会话 messages 快照（进入主流程前）",
            format_messages_for_deep(messages, max_per_content=32_000, max_total=500_000),
        )
    except Exception:
        pass

    _mainline_direct_reply = await try_execute_cognitive_direct_plan(
        plan=_cognitive_kernel_plan,
        tools=tools,
        allowed_skills=allowed,
        run_tool_func=run_tool,
        user_input=user_input or "",
        session_id=_lark_cid or "",
        channel=_bg_channel or "",
    )
    if _mainline_direct_reply is not None:
        if _session_messages is not None:
            messages.append({"role": "user", "content": user_input or ""})
            messages.append({"role": "assistant", "content": _mainline_direct_reply})
            _session_messages.clear()
            _session_messages.extend(messages[-30:])
        try:
            from l3_node.terminal_turn_debug_log import finalize_top_level_turn

            finalize_top_level_turn(
                _mainline_direct_reply,
                delegate_depth=_delegate_depth,
                run_id=run_id,
                channel=_bg_channel,
                extra={**(_lark_turn_dbg_extra or {}), "cognitive_kernel_direct_mainline": "1"},
            )
        except Exception:
            pass
        _turn_dbg["answer"] = _mainline_direct_reply
        return _mainline_direct_reply

    _capability_work_order_reply = await try_execute_capability_work_order(
        user_input=user_input or "",
        tools=tools,
        allowed_skills=allowed,
        run_tool_func=run_tool,
        run_id=run_id,
        intent_decision=_intent_orchestrator_decision,
    )
    if _capability_work_order_reply is not None:
        if _session_messages is not None:
            messages.append({"role": "user", "content": user_input or ""})
            messages.append({"role": "assistant", "content": _capability_work_order_reply})
            _session_messages.clear()
            _session_messages.extend(messages[-30:])
        try:
            from l3_node.terminal_turn_debug_log import finalize_top_level_turn

            finalize_top_level_turn(
                _capability_work_order_reply,
                delegate_depth=_delegate_depth,
                run_id=run_id,
                channel=_bg_channel,
                extra={**(_lark_turn_dbg_extra or {}), "cognitive_kernel_capability_work_order": "1"},
            )
        except Exception:
            pass
        _turn_dbg["answer"] = _capability_work_order_reply
        return _capability_work_order_reply

    _kernel_only_reply = await _close_kernel_plan_without_text_transport(
        plan=_cognitive_kernel_plan,
        engine=engine,
        user_input=user_input or "",
        prior_messages=prior_messages,
        run_id=run_id,
    )
    if _session_messages is not None:
        messages.append({"role": "user", "content": user_input or ""})
        messages.append({"role": "assistant", "content": _kernel_only_reply})
        _session_messages.clear()
        _session_messages.extend(messages[-30:])
    try:
        from l3_node.terminal_turn_debug_log import finalize_top_level_turn

        finalize_top_level_turn(
            _kernel_only_reply,
            delegate_depth=_delegate_depth,
            run_id=run_id,
            channel=_bg_channel,
            extra={**(_lark_turn_dbg_extra or {}), "cognitive_kernel_hard_migration": "1"},
        )
    except Exception:
        pass
    _turn_dbg["answer"] = _kernel_only_reply
    return _kernel_only_reply

# ---------------------------------------------------------------------------
# 记忆：主路径为 Cognitive Kernel Memory Growth 与 MemoryRecallAgent。
# ---------------------------------------------------------------------------
