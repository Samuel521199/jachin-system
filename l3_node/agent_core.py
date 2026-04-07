"""
Jachin Nexus V2 — L3 单体 Agent 与记忆同步（ReAct；delegate 子 Agent）。

工具清单以 load_tools、assemble_tool_pool（MCP 合并）、build_tools_description 为准。
相关规格：docs/前台闲聊与后台重负荷任务的物理隔离与背压熔断.md、docs/L3_AGENT_CONTEXT_MEMORY_AND_PROMPT.md；
薄弱点与实现快照：docs/L3_LIMITATIONS_AND_REMEDIATION_ROADMAP.md（§〇）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from l3_node.engine.hooks_pipeline import (
    HOOK_AFTER_TOOL_EXEC,
    HOOK_BEFORE_LLM_THINK,
    HOOK_BEFORE_RESPONSE,
    HOOK_BEFORE_TOOL_EXEC,
    HOOK_ON_INTENT_RECEIVED,
    Pipeline,
    PipelineContext,
    global_hooks,
)
from l3_node.llm_client import LiteLLMEngine, RunCancelledError, SecurityContext
from l3_node.capability_catalog import build_capability_prompt_inject_for_tools, tools_include_recruitment
from l3_node.exec_trace import exec_trace
from l3_node.primitives import build_tools_description, get_hr_invoke_defaults, get_mcp_registry, load_tools, run_tool
from l3_node.primitives.tools.tool_pool import assemble_tool_pool

logger = logging.getLogger(__name__)


def _gateway_prior_brief(prior_messages: list[dict[str, Any]], max_chars: int = 1200) -> str:
    """供 GatewayContextBundle.short_memory_context 的轻量摘要（非完整历史）。"""
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


def _max_delegate_depth_cfg() -> int:
    try:
        from l3_node.nexus_config import get_nexus_config

        cfg = get_nexus_config() or {}
        ag = cfg.get("agent") or {}
        return max(0, int(ag.get("max_delegate_depth", 2)))
    except Exception:
        return 2


def _llm_token_budget_for_run(delegate_depth: int) -> int | None:
    try:
        from l3_node.nexus_config import get_nexus_config

        cfg = get_nexus_config() or {}
        ag = cfg.get("agent") or {}
        key = "sub_agent_max_total_tokens" if delegate_depth > 0 else "main_max_total_tokens"
        v = ag.get(key)
        if v is None:
            return 120_000 if delegate_depth > 0 else None
        vi = int(v)
        return None if vi <= 0 else vi
    except Exception:
        return 120_000 if delegate_depth > 0 else None


# ReAct：一旦发生 workspace 写改，后续 LLM 轮次改用编码模型（与主推理共用 Key）
_L3_CODER_MODE_META = "_l3_coder_mode"
_L3_CODER_ENGINE_CACHE_META = "_l3_coder_engine_cache"
_L3_COMPLEX_ENGINE_CACHE_META = "_l3_complex_engine_cache"

# composite / 参谋长模式：system 后缀页脚追加；高危工具的人性化「悬挂签批」与统帅决策权
CHIEF_ADVISOR_LOGIC_VALIDATION_BLOCK = (
    "\n【参谋长高危签批准则】：此规则适用于所有内部工具 (core:*) 及外部挂载工具 (mcp:*)！\n"
    "当用户的指令涉及对系统环境、文件、或数据库的【写入、修改、删除】操作（例如：使用 mcp_sqlite 执行 UPDATE/DELETE）时，"
    "你**绝对禁止**擅自静默执行。\n"
    "你必须将任务**悬挂（挂起）**，并严格按以下格式向统帅请示：\n"
    "1. 【风险评估】：一句话指出该操作的潜在风险或爆炸半径。\n"
    "2. 【待签批计划】：展示你准备调用的工具和具体参数（如准备执行的 SQL 语句）。\n"
    "3. 【决策请求】：明确询问统帅：「此操作将修改底层数据，请问是否授权执行？回复「批准」即可放行，或提供其他修改意见。」\n"
    "只有当统帅在下一轮对话中明确回复了批准词汇后，你才能调用修改工具。\n"
    "【MCP SQLite 硬约束】调用官方 SQLite 的 **write_query** 前必须完成上述签批；获准后须在 Action Input 的 JSON 内增加 "
    "`jachin_mcp_write_ack`: true（系统会校验，且该键不会传给数据库）。"
    "用户口头「用 SQLite 工具帮我改」**不**等于已签批——仍须先悬挂请示或确认其已书面同意。"
)

# 全量 ReAct 页脚：防止对工作区内 DB/表行「无 Observation 却断言」（与 MCP 是否启用正交：无工具须明说不能查）
REACT_FOOTER_FACTUAL_DB_BLOCK = (
    "【工作区可核验数据】当用户问及 workspace 内 *.sqlite、数据库表内容、库存、缺货、行级数量等**可核验事实**时："
    "若当前可见工具中存在 **MCP SQLite / 数据库只读类**或可通过 **core:shell_exec** 等执行**只读**查询，"
    "你必须**先在本轮调用工具**并取得 Observation，再据 Observation 作答；"
    "禁止仅凭被动「本地记忆」、上文闲聊或推测填写表数据。"
    "「缺货」若用户未另行定义，默认指数量字段为 **0** 的条目；须在 **SQL 中表达该条件**（例如 WHERE quantity=0，列名以实际表为准；"
    "未知列名时可先用只读 PRAGMA table_info 或 LIMIT 1 探查），**禁止**仅靠 SELECT * 拉全表再在自然语言里「看图猜谁缺货」。"
    "列举缺货项须与 Observation 返回的行逐条一致，不得把第一行或随机行说成缺货。"
    "若工具列表中**没有任何**可用的数据库只读能力，须在 Final Answer 中明确说明当前无法访问该库，并请用户检查 MCP（如 official-sqlite-npx）或配置，**禁止编造查询结果或假装已查库**。"
)
REACT_FOOTER_FACTUAL_DB_BLOCK_SLIM = (
    "【DB 事实】*.sqlite/表数据：须先工具只读查询、有 Observation 再答；无 DB 工具则明说无法查库，禁止编造。\n"
)


def _tools_include_sqlite_mcp(tools: list[dict[str, Any]] | None) -> bool:
    """是否可见 MCP SQLite（官方 read_query/write_query 或 id 含 sqlite）。"""
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        tid = str(t.get("id") or "").strip().lower()
        if "sqlite" in tid:
            return True
        if tid.startswith("mcp:"):
            r = tid[4:].strip().lower()
            if r in ("read_query", "write_query"):
                return True
    return False


def _react_engine_for_iteration(
    base: LiteLLMEngine,
    ctx: PipelineContext,
    *,
    full_messages: list[dict[str, Any]],
    tools_count: int,
    react_iteration: int,
    force_complex: bool = False,
) -> LiteLLMEngine:
    """
    ReAct 每轮选引擎：优先级 编码（LLM_CODER_MODEL）> 复杂（LLM_COMPLEX_MODEL）> 默认（LLM_MODEL）。
    复杂路由条件见 core.llm_provider.l3_react_should_use_complex_model。
    """
    # 0) 首轮明确编程/脚本意图：与 fs_write 后切 coder 一致，避免 tools 数量先触发 complex
    if not ctx.metadata.get(_L3_CODER_MODE_META):
        try:
            from core.llm_provider import should_prime_l3_react_coder_mode

            if should_prime_l3_react_coder_mode(
                react_iteration=react_iteration,
                full_messages=full_messages,
            ):
                ctx.metadata[_L3_CODER_MODE_META] = True
        except Exception:
            pass

    # 1) 编程：已执行 fs_write / apply_patch 后的轮次（或首轮已 prime）
    if ctx.metadata.get(_L3_CODER_MODE_META):
        cached = ctx.metadata.get(_L3_CODER_ENGINE_CACHE_META)
        if cached is not None:
            return cached
        try:
            from core.llm_provider import get_coder_model_litellm_id

            cm = get_coder_model_litellm_id()
            if base._normalize_model(cm) == base._normalize_model(base.model_name):
                ctx.metadata[_L3_CODER_ENGINE_CACHE_META] = base
                return base
            ce = LiteLLMEngine(
                security_context=base.ctx,
                model_name=cm,
                fallback_models=list(base.fallback_models or []),
                timeout=base.timeout,
                max_attempts=base.max_attempts,
            )
            ctx.metadata[_L3_CODER_ENGINE_CACHE_META] = ce
            logger.info(
                "[L3 Agent] ReAct 切换编码模型 %s（主推理 %s，已执行 fs_write/apply_patch）",
                ce.model_name,
                base.model_name,
            )
            return ce
        except Exception as e:
            logger.warning("[L3 Agent] 编码模型不可用，沿用主模型: %s", e)
            return base

    # 2) 复杂任务：qwen-max 等（子 Agent、长 ReAct、大工具池、planned/strict 等）
    _dd = int(ctx.metadata.get("_delegate_depth", 0) or 0)
    try:
        from core.llm_provider import (
            get_complex_model_litellm_id,
            l3_react_should_use_complex_model,
        )

        if not l3_react_should_use_complex_model(
            delegate_depth=_dd,
            react_iteration=react_iteration,
            full_messages=full_messages,
            tools_count=tools_count,
            force_complex=force_complex,
        ):
            return base
        cx = get_complex_model_litellm_id()
        if base._normalize_model(cx) == base._normalize_model(base.model_name):
            return base
        c_cached = ctx.metadata.get(_L3_COMPLEX_ENGINE_CACHE_META)
        if c_cached is not None:
            return c_cached
        c_eng = LiteLLMEngine(
            security_context=base.ctx,
            model_name=cx,
            fallback_models=list(base.fallback_models or []),
            timeout=base.timeout,
            max_attempts=base.max_attempts,
        )
        ctx.metadata[_L3_COMPLEX_ENGINE_CACHE_META] = c_eng
        logger.info(
            "[L3 Agent] ReAct 切换复杂模型 %s（主推理 %s，iter=%s delegate_depth=%s tools=%s）",
            c_eng.model_name,
            base.model_name,
            react_iteration + 1,
            _dd,
            tools_count,
        )
        return c_eng
    except Exception as e:
        logger.warning("[L3 Agent] 复杂模型路由不可用，沿用主模型: %s", e)
        return base


def _hr_answer_claims_job_published(ans: str) -> bool:
    """检测助手是否声称「刚在 Boss 发帖成功」。子串过宽会误伤（如「已在Boss 沟通页」）。"""
    a = ans or ""
    if "JOB_" in a:
        return True
    phrases = (
        "职位已发布",
        "职位发布成功",
        "已发布职位",
        "Boss 发布成功",
        "boss 发布成功",
        "已在 Boss 发布",
        "已在Boss 发布",
        "已成功发布职位",
        "职位发布完成",
        "发帖成功",
        "已成功发帖",
        "已在 Boss 上架",
        "职位已在 Boss 上架",
    )
    return any(p in a for p in phrases)


def _hr_answer_claims_unmanned_scheduler_running(ans: str) -> bool:
    """
    检测助手是否声称「无人值守/收网调度已在跑」，但未实际调用 MCP 时即为幻觉。
    （与 _hr_answer_claims_job_published 区分：后者针对 Boss 发帖。）
    """
    a = ans or ""
    if re.search(r"调度状态\s*\*?\s*[|｜]\s*\*?\s*运行中", a, re.I):
        return True
    if re.search(r"无人值守[^\n]{0,48}(已启动|运行中|已开始)", a):
        return True
    if "收网任务已启动" in a or ("自动抓取简历" in a and "已启动" in a):
        return True
    if re.search(r"任务\s*ID\s*\|\s*[`'\"]?hr_recruit", a, re.I):
        return True
    if "✅" in a and "无人值守" in a and ("启动" in a or "运行" in a):
        return True
    return False


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
    若本轮已成功执行 add_automated_recruitment_task，将 Final Answer 里 Markdown 表中
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
    if _hr_answer_claims_job_published(ans):
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


MAX_REACT_ITERATIONS = 8  # 多轮工具调用场景需更多迭代，5 易触发「循环达到上限」
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
)
RECALL_MEMORY_TOOL_ID = "recall_memory"
COORDINATE_TOOL_ID = "coordinate"

# 子 Agent 角色预设（分身时使用）
SUB_AGENT_PROMPTS: dict[str, str] = {
    "coder": "你是资深程序员，只负责编写代码。使用 core:fs_read 读取文件，core:fs_write 写入代码。",
    "writer": "你是技术文档工程师，只负责撰写文档。使用 core:fs_read 读取参考，core:fs_write 写入文档。",
    "researcher": "你是研究员，负责查阅和分析。使用 core:fs_read 读取文件，core:shell_exec 执行查询命令。",
    "default": "你是专业助手，完成指定子任务。可用工具：core:fs_read、core:fs_write、core:shell_exec、core:shell_job_status（查后台任务）。",
}

# 子 Agent 独立工具集（按角色裁剪，绝不给发邮件等敏感技能）
SUB_AGENT_ALLOWED_SKILLS: dict[str, list[str]] = {
    "coder": ["core:fs_read", "core:fs_write", "core:shell_exec", "core:shell_job_status", "core:shell_job_cancel"],
    "writer": ["core:fs_read", "core:fs_write"],
    "researcher": ["core:fs_read", "core:shell_exec", "core:shell_job_status", "core:shell_job_cancel"],
    "default": ["core:fs_read", "core:fs_write", "core:shell_exec", "core:shell_job_status", "core:shell_job_cancel"],
}

# 子 Agent 注册表：sub_agent_id -> SubAgent 实例，供复用
_sub_agent_registry: dict[str, "SubAgent"] = {}


def _parse_action(
    llm_output: str,
    skills: list[dict[str, Any]],
    use_mock: bool = False,
    allowed_skills: Optional[list[str]] = None,
) -> dict[str, Any] | None:
    text = (llm_output or "").strip()
    for pattern in (r"Final\s+Answer:\s*(.+)", r"Answer:\s*(.+)"):
        m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if m:
            return {"type": "answer", "content": m.group(1).strip()}

    # Action: delegate — 分身子 Agent
    if re.search(r"Action:\s*delegate\s*(?:\n|$)", text, re.IGNORECASE):
        mi = re.search(
            r"Action\s+Input:\s*(.+?)(?:\n\n|\n(?:Thought|Action|Final|$))",
            text, re.DOTALL | re.IGNORECASE,
        )
        raw = (mi.group(1).strip() if mi else "").strip()
        try:
            data = json.loads(raw) if raw.startswith("{") or raw.startswith("[") else {}
            if isinstance(data, list):
                tasks = data
            else:
                tasks = data.get("sub_tasks", [])
            if tasks:
                return {"type": "delegate", "sub_tasks": tasks}
        except json.JSONDecodeError:
            pass

    # recall_memory：向 L2 检索记忆（需 L2 已配对）
    if re.search(rf"Action:\s*{re.escape(RECALL_MEMORY_TOOL_ID)}\s*(?:\n|$)", text, re.IGNORECASE):
        mi = re.search(
            r"Action\s+Input:\s*(.+?)(?:\n\n|\n(?:Thought|Action|Final|$))",
            text, re.DOTALL | re.IGNORECASE,
        )
        inp = (mi.group(1).strip() if mi else "").strip()
        return {"type": "recall", "query": inp}

    # coordinate：向 L2 请求多节点协同
    if re.search(rf"Action:\s*{re.escape(COORDINATE_TOOL_ID)}\s*(?:\n|$)", text, re.IGNORECASE):
        mi = re.search(
            r"Action\s+Input:\s*(.+?)(?:\n\n|\n(?:Thought|Action|Final|$))",
            text, re.DOTALL | re.IGNORECASE,
        )
        raw = (mi.group(1).strip() if mi else "").strip()
        try:
            data = json.loads(raw) if raw.startswith("{") or raw.startswith("[") else {}
            if isinstance(data, dict) and data.get("sub_tasks"):
                return {"type": "coordinate", "payload": data}
        except json.JSONDecodeError:
            pass

    # Native 与 JPP Wasm 工具：从 skills 列表解析白名单内的 tool_id
    tool_ids: list[str] = []
    if skills:
        tool_ids = [t.get("id", "") for t in skills if t.get("id")]
    else:
        from l3_node.primitives import load_tools
        tools_fallback = load_tools(allowed_skills=allowed_skills)
        tool_ids = [t.get("id", "") for t in tools_fallback if t.get("id")]

    def _extract_input_after_action(action_pattern: str) -> str:
        """提取紧跟在当前 Action 后的 Action Input，避免多 Action 时取错"""
        m = re.search(action_pattern, text, re.IGNORECASE)
        if not m:
            return ""
        search_start = m.end()
        rest = text[search_start:]
        # 终止段：双换行 / 下一行 Thought|Action|Final|Observation / Markdown 标题 ### / 中文「观察」/ 文本结束
        # 旧版仅认 \nThought，若 JSON 后无换行或紧跟「### 动作」会导致整段匹配失败、inp 为空
        mi = re.search(
            r"Action\s+Input:\s*(.+?)(?:\n\n|\n\s*(?:Thought|Action|Final|Answer|Observation|###|观察)|\Z)",
            rest,
            re.DOTALL | re.IGNORECASE,
        )
        if mi:
            return mi.group(1).strip()
        ai = re.search(r"Action\s+Input:\s*", rest, re.IGNORECASE)
        if not ai:
            return ""
        tail = rest[ai.end() :]
        ts = tail.lstrip()
        if ts.startswith("{"):
            depth = 0
            for i, c in enumerate(ts):
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        return ts[: i + 1].strip()
            return ts.splitlines()[0].strip() if ts else ""
        m2 = re.search(
            r"^(.+?)(?=\n\s*(?:Thought|Action|Final|Answer|Observation|###|观察)|\n\n|\Z)",
            tail,
            re.DOTALL | re.IGNORECASE,
        )
        return (m2.group(1).strip() if m2 else tail.strip())

    # 匹配 Action 行（含同行 Action Input 情形）
    action_suffix = r"(?:\s|\n|$)"
    for tool_id in tool_ids:
        pat = rf"Action:\s*{re.escape(tool_id)}{action_suffix}"
        if re.search(pat, text, re.IGNORECASE):
            return {"type": "native", "tool": tool_id, "input": _extract_input_after_action(pat)}
    # 兼容：LLM 可能输出无 mcp: 前缀的 Action（如 Action: atom_post_job_boss）
    for tool_id in tool_ids:
        raw = tool_id.replace("mcp:", "").strip()
        if raw:
            pat = rf"Action:\s*{re.escape(raw)}{action_suffix}"
            if re.search(pat, text, re.IGNORECASE):
                return {"type": "native", "tool": tool_id, "input": _extract_input_after_action(pat)}
    return None


def _is_hallucinated_final_mcp_error_json(text: str) -> bool:
    """
    模型未走 Action 却把「MCP -32602 / write_file 校验失败」式 JSON 当作 Final Answer 整段输出。
    与真实工具返回区分：真实调用会先打 [L3 Agent][工具路由]。
    """
    s = (text or "").strip()
    if len(s) > 8000:
        return False
    if not (s.startswith("{") and s.endswith("}")):
        return False
    if "32602" not in s and "MCP error" not in s:
        return False
    try:
        o = json.loads(s)
    except json.JSONDecodeError:
        return False
    if not isinstance(o, dict):
        return False
    st = str(o.get("status") or "").lower()
    err = str(o.get("error") or "")
    if st == "failed" and (
        "32602" in err
        or "write_file" in err.lower()
        or "invalid arguments" in err.lower()
        or "validation" in err.lower()
    ):
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


def _get_l2_config() -> dict[str, Any] | None:
    """从 l2_gateway_config.json 读取 L2 配置（已配对时）。含 permissions_snapshot。"""
    cfg_path = Path.home() / ".jachin" / "l2_gateway_config.json"
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


async def _recall_memory_search(query: str, config: dict[str, str]) -> str:
    """向 L2 检索记忆。"""
    import json

    import httpx

    from l3_node.tool_call_cache import store_if_cacheable, try_get_cached

    qn = (query or "").strip()
    cache_inp = json.dumps({"q": qn, "node_id": config.get("node_id", "")}, sort_keys=True, ensure_ascii=False)
    hit = try_get_cached("recall_memory", cache_inp)
    if hit is not None:
        return hit

    url = f"{config['l2_base_url']}/api/v2/memory/search"
    params = {"q": query, "limit": 10}
    if config.get("node_id"):
        params["node_id"] = config["node_id"]
    headers = {"X-Sub-Account-Id": config.get("sub_account_id", "")}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url, params=params, headers=headers)
            r.raise_for_status()
            data = r.json()
        results = data.get("results", [])
        if not results:
            return store_if_cacheable("recall_memory", cache_inp, "[未找到相关记忆]")
        parts = [f"- {r.get('content', '')[:300]}..." for r in results[:5]]
        return store_if_cacheable("recall_memory", cache_inp, "\n".join(parts))
    except Exception as e:
        return f"[记忆检索失败: {e}]"


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
                    ai = inp_dict.get("action_input", "")
                    if isinstance(ai, dict):
                        ai = json.dumps(ai, ensure_ascii=False)
                    else:
                        ai = str(ai or "")
                    allowed_coord = _get_allowed_skills()
                    result = run_tool(tid, ai, allowed_skills=allowed_coord)
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


def _build_system_prompt(
    tools: list[dict[str, Any]] | None = None,
    allow_delegate: bool = True,
    allow_recall: bool = True,
    allow_coordinate: bool = True,
    prompt_cycle: int | None = None,
    recruitment_longform: bool = True,
    prompt_style: str = "full",
    pure_json_contract: bool = False,
    gateway_inject: str = "",
    safety_lock_user_text: str = "",
    *,
    chief_advisor_mode: bool = False,
    environment_report_block: str = "",
) -> str:
    from l3_node.prompt_compose import (
        SuffixChunk,
        apply_system_prompt_total_cap,
        load_prompt_suffix_budget,
        load_system_prompt_total_max_chars,
        sort_tools_by_id,
    )

    slim_style = (prompt_style or "").strip().lower() == "slim_user_led"
    slim_mode = slim_style or bool(pure_json_contract)
    allowed = _get_allowed_skills()
    tools = sort_tools_by_id(tools or load_tools(allowed_skills=allowed))
    tools_desc = build_tools_description(tools)
    recall_hint = ""
    if allow_recall and _get_l2_config():
        recall_hint = (
            "\n- recall_memory: 向 L2 检索历史记忆。参数: 查询关键词。当需要回忆过往对话或上下文时使用。"
            "\n- core:local_memory_search: L3 本地记忆检索（断网/无 L2 时优先）。Action Input JSON："
            '{"query":"关键词","top_k":8}；可选 mmr_lambda、half_life_days、include_memory_md。'
        )
    else:
        recall_hint = (
            "\n- core:local_memory_search: 本地记忆检索（l3_local + 可选 MEMORY.md，含衰减与 MMR）。"
            ' JSON：{"query":"..."}，可选 top_k、mmr_lambda。'
        )
    coordinate_hint = ""
    if allow_coordinate and _get_l2_config():
        coordinate_hint = """
- coordinate: 向 L2 请求多节点任务编排（拆分/分配/聚合）。规格见 docs/ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md；跨节点投递目标为 TaskManager + L3 拉取，非依赖 L2 对 NAT 笔记本入站 HTTP。
  Action Input: {"parent_node_id": "本节点ID", "intent": "主任务描述", "sub_tasks": [{"intent": "子任务1"}, {"intent": "子任务2"}]}
- P1+ 远程原生工具：若要在**其他 L3 节点**直接执行 shell（不经子 Agent LLM），sub_tasks 中 skill_required 填 core:shell_exec，input_data 示例：
  {"type":"native_tool","tool_id":"core:shell_exec","action_input":{"command":"git status","timeout":60}}
  执行节点拉取子任务时将直接 run_tool。"""
    delegate_hint = ""
    if allow_delegate:
        delegate_hint = """
若任务需要多种能力（如同时写代码和写文档），可输出：
Action: delegate
Action Input: {"sub_tasks": [{"role": "coder", "task": "编写 XXX"}, {"role": "writer", "task": "撰写文档"}]}
将子任务交给专业子 Agent 并行执行。"""
    hr_hint = ""
    hr_ids = [t.get("id", "") for t in tools if "hr.analyzer" in (t.get("id") or "")]
    hr_preferred = next((x for x in hr_ids if "analyzer4" in x), None) or (hr_ids[0] if hr_ids else None)
    if hr_ids:
        try:
            defaults = get_hr_invoke_defaults(hr_preferred.replace("jpp:", ""))
            hr_hint = f"""
【重要】当用户要求「简历分析」「HR 透析镜」等时：直接调用 {hr_preferred}（优先透析镜 4），Action Input 可传 {{}} 或 {{"target_role":"{defaults.get('target_role','backend_engineer')}","resume_filename":"{defaults.get('resume_filename','zhangsan_resume.md')}"}}，系统会从技能配置自动读取 resume_input_dir、JD 等。禁止用 list_directory 探索，禁止仅回复描述性文字。
【强制】当用户说「再分析」「重新分析」「再去分析」「再跑一次」「再执行一次透析镜」等时：必须重新调用 {hr_preferred}，不得复用上一轮的 Observation，不得用 fs_read 或 recall_memory 代替。"""
        except Exception:
            hr_hint = f"""
【重要】当用户要求「简历分析」「HR 透析镜」等时：直接调用 {hr_preferred or "jpp:com.jachin.hr.analyzer4"}，Action Input 可传 {{}}，系统会从技能配置自动注入默认参数。禁止用 list_directory 探索。
【强制】当用户说「再分析」「重新分析」「再去分析」「再跑一次」等时：必须重新调用 HR 透析镜工具，不得复用上一轮 Observation，不得用 fs_read 或 recall_memory 代替。"""

    # 能力总目录：核心（与域无关）+ 当前工具命中的各域摘要（见 capability_catalog.DOMAIN_REGISTRY）
    _cap_inject = build_capability_prompt_inject_for_tools(tools).strip()
    capability_catalog_hint = ""
    if _cap_inject:
        capability_catalog_hint = f"""【L3 能力总目录】
{_cap_inject}

---
"""
    # 招聘域 SOP：仅当招聘 MCP 可见时注入 SKILL.md（与总目录解耦，新域可仿照单独挂载）
    _hr_has_recruitment_tools = tools_include_recruitment(tools)
    hr_recruitment_hint = ""
    if _hr_has_recruitment_tools:
        skill_content = _load_hr_recruitment_skill_content()
        if skill_content:
            hr_recruitment_hint = f"""
【当前激活技能：HR 招聘总监】按以下 SOP 执行。发帖与调度分离：`boss_post_published` 后勿再 `atom_post_job_boss`（除非 `force_republish`）；改收网/打招呼只走调度工具或飞书短指令。
**新岗 / 用户刚确认的 JD**：调用 `mcp:add_automated_recruitment_task` 时，`job_name` 必须与**上一轮 assistant 消息里 ```json``` 中的 `job_title` 完全一致**，禁止沿用系统摘要、指针或历史会话里的其它岗位名（如上一岗「Python」）。
若 Final Answer 中用 Markdown 表列出「收网目标」与「自动分析/透析阈值」，两处份数须与 `add_automated_recruitment_task` 工具结果一致（系统会在下发前按注册任务自动对齐表格中的份数）。

{skill_content}

---
"""
        else:
            # 兜底：Skill 未找到时保留简短提示，避免完全无招聘能力
            hr_recruitment_hint = """
【HR 招聘】发帖仅 `atom_post_job_boss`（成功后 jd 会 `boss_post_published`，勿重复发帖除非 force_republish）。调度用 `hr_scheduler_send_confirm_prompt` / `add_automated_recruitment_task` 或飞书改批次。关闭：`stop_automated_recruitment`。
`add_automated_recruitment_task` 的 `job_name` 必须与用户刚确认的 JD 里 `job_title` 一致，勿沿用其它岗位名。
Markdown 参数表中「收网目标」与「自动分析/透析阈值」份数须一致（与工具返回值一致）。
"""
    if _hr_has_recruitment_tools and not recruitment_longform:
        hr_recruitment_hint = ""
        hr_hint = (
            "【HR·动态收敛】未检测到本轮与招聘强相关意图时勿调用招聘 MCP；"
            "用户谈到职位、简历、Boss、透析、收网、飞书调度等再按需使用工具。\n"
        )
    # L3 本地记忆注入（智能化升级 P0：断网/无 L2 时仍可用）
    local_mem = ""
    try:
        from l3_node.local_memory import get_local_memory_for_prompt

        local_mem = get_local_memory_for_prompt(
            limit=6 if slim_mode else 12, prompt_cycle=prompt_cycle
        )
    except ImportError:
        pass
    jachin_rules = ""
    try:
        from l3_node.jachin_workspace_rules import get_jachin_workspace_rules_snippet

        jachin_rules = get_jachin_workspace_rules_snippet()
    except ImportError:
        pass
    safety_lock_txt = ""
    try:
        from l3_node.jachin_safety_lock import get_safety_lock_snippet

        safety_lock_txt = get_safety_lock_snippet(user_text=safety_lock_user_text or "")
    except ImportError:
        pass
    # 智能化 P0：跨会话任务规划上下文（task_plan.md / progress.md）
    plan_ctx = ""
    if not slim_mode:
        try:
            from l3_node.task_planning import get_planning_context_for_prompt

            plan_ctx = get_planning_context_for_prompt()
        except ImportError:
            pass
    plan_hint = """
【任务规划】复杂多步任务（3+ 步骤）可先用 core:fs_write 将计划写入 task_plan.md，再按计划执行。完成后可更新 progress.md。新会话会加载既有计划继续执行。""" if not plan_ctx else ""

    if slim_mode:
        capability_catalog_hint = ""
        hr_recruitment_hint = ""
        hr_hint = ""
        plan_ctx = ""
        plan_hint = ""

    hr_runtime_ctx = ""
    if not slim_mode:
        try:
            from l3_node.hr_prompt_context import get_hr_recruitment_runtime_context_for_prompt

            hr_runtime_ctx = get_hr_recruitment_runtime_context_for_prompt()
        except Exception:
            pass
    p1_inject = ""
    if not slim_mode:
        try:
            from l3_node.intelligence_p1 import get_p1_prompt_injections

            _pp, _pc = get_p1_prompt_injections()
            p1_inject = f"{_pp}{_pc}"
        except ImportError:
            pass

    intel_b = ""
    if not slim_mode:
        try:
            from l3_node.intelligence_b_execution import (
                get_execution_mode,
                get_force_universal_planning_chain,
                get_require_brainstorm_card,
            )

            _eb = get_execution_mode()
            _force_u = get_force_universal_planning_chain()
            _planning_style = _eb in ("planned", "strict") or _force_u
            _bs = ""
            if get_require_brainstorm_card() and _planning_style:
                _bs = (
                    "\n须 **先** 输出 brainstorm 卡 JSON（可 ```json 包裹）："
                    '{"jachin_brainstorm_card":{"angles":["思路1","思路2"],"constraints":"约束","open_questions":"待澄清"}}，'
                    "再输出下方计划卡。"
                )
            if _eb == "strict":
                intel_b = f"""
【执行范式 strict】同 planned；若已执行 core:fs_write / core:shell_exec / core:apply_patch，进入 **只读 verify 轮**（系统可能仅展示只读工具），须先用只读工具复核，再在回复中包含 **VERIFY_PASS**（大写）后方可 Final Answer。{_bs}"""
            elif _eb == "planned" or _force_u:
                intel_b = f"""
【执行范式 planned】在首次调用任何 Action 工具前，须在本轮或此前 assistant 消息中输出可解析 JSON 计划卡，Schema：
{{"jachin_plan_card":{{"goal":"目标一句话","steps":["步骤1","步骤2"],"risks":"主要风险","rollback_point":"可回退点"}}}}{_bs}"""
        except ImportError:
            pass

    chat_task_hint = """
【前台/后台隔离】长耗时、大批量任务（如抓数十份简历、长跑分析、大量文件 IO）请优先使用 **core:submit_background_task**（JSON：intent 必填；可选 require_skills、max_iterations），立即返回 task_id，不阻塞用户继续闲聊。用户问进度时用 **core:check_background_task**（task_id 或 {"list_recent":true}）。若工具返回 status=rejected 且 reason=resource_exhausted，须如实说明本机后台等待队列已满，请用户稍后再试或待进行中任务完成；勿承诺不存在的「自动转发 L2」能力（多节点编排仅在已配对且允许使用 **coordinate** 时另行处理）。
【前台同步预算】默认非豁免工具单次执行约 **5s** 超时；超时 Observation 会提示改走后台任务。"""
    if slim_mode:
        chat_task_hint = (
            "长耗时、大批量任务请优先 **core:submit_background_task**；进度用 **core:check_background_task**。"
            "前台同步工具默认约 5s 超时。\n"
        )

    # 前缀缓存友好：静态/半静态在前，随会话变化的记忆与长 SOP 在后（工具段可单独截断以配合总硬帽）
    if pure_json_contract:
        _prefix_before_tools = f"""你是助手；优先遵守用户消息中的格式要求；不要寒暄，不要用 Markdown 章节标题当开场。
{intel_b}
{chat_task_hint}

可用工具：
"""
    elif slim_mode:
        _prefix_before_tools = f"""你是智能助手。**用户对本轮「最终可见回复」有强格式要求时，你必须优先服从用户消息**：不要寒暄，以及用 Markdown 章节标题当开场。
若任务需要读文件、执行命令等，仍使用下方 Thought / Action / Observation；工具用完后，只输出用户要求的正文（若用户要求仅 JSON，勿加 markdown 围栏与解释性前言）。
{intel_b}
{chat_task_hint}

可用工具：
"""
    else:
        _prefix_before_tools = f"""你是一个智能助手，使用 ReAct 格式思考。
{intel_b}
{chat_task_hint}

可用工具：
"""
    _pure_mem_rules = ""
    if pure_json_contract:
        if (safety_lock_txt or "").strip():
            _pure_mem_rules += f"\n{safety_lock_txt.strip()}\n"
        if (local_mem or "").strip():
            _pure_mem_rules += f"\n【本地记忆】\n{local_mem.strip()}\n"
        if (jachin_rules or "").strip():
            _pure_mem_rules += f"\n【工作区规则】\n{jachin_rules.strip()}\n"

    if pure_json_contract:
        _prefix_after_tools = f"""
{_pure_mem_rules}
{recall_hint}
{coordinate_hint}
{delegate_hint}
【数据输出】若用户要求合法 JSON：只输出一个 JSON 对象；不要用代码围栏，不要用井号标题行，不要附加说明。
若必须调用工具：使用 Thought、Action、Action Input（JSON 参数），工具结果为 Observation，可多轮。工具结束后若仍需 JSON，则接下来只输出 JSON 本体，不要继续写 Action 行。
若明显无需工具：从首条助手回复起只输出用户要求的正文（如 JSON），不要 Thought、Action、Observation 等标签行。

--- 以下段落为会话/记忆上下文（API 前缀缓存友好）---
"""
    else:
        _prefix_after_tools = f"""
{recall_hint}
{coordinate_hint}
{delegate_hint}

输出格式：
Thought: <你的思考>
Action: <工具名，必须与上方「可用工具」中的 id 完全一致，如 {hr_preferred or "jpp:com.jachin.hr.analyzer4"}>
Action Input: <参数>
Observation: <工具返回>
...（可多轮）
Final Answer: <最终回复>

--- 以下段落随会话、记忆与域状态变化（建议置于提示词末尾以利于 API 前缀缓存）---
"""
    # 后缀驱逐 rank：越小越先丢。对齐 L3_LIMITATIONS_AND_REMEDIATION_ROADMAP.md §5.3
    suffix_chunks: list[SuffixChunk] = []
    _gwi = (gateway_inject or "").strip()
    if _gwi and not pure_json_contract:
        suffix_chunks.append(
            SuffixChunk("mid", "intent_gateway_execution_inject", f"\n{_gwi}\n", eviction_rank=28)
        )
    _erb = (environment_report_block or "").strip()
    if chief_advisor_mode and _erb and not pure_json_contract:
        suffix_chunks.append(
            SuffixChunk("high", "environment_report", f"\n{_erb}\n", eviction_rank=91)
        )
    if chief_advisor_mode and not pure_json_contract:
        try:
            from l3_node.intent_gateway.pushback_copy import CHIEF_ADVISOR_SYSTEM_BLOCK

            suffix_chunks.append(
                SuffixChunk("high", "chief_advisor_persona", f"\n{CHIEF_ADVISOR_SYSTEM_BLOCK}\n", eviction_rank=93)
            )
        except ImportError:
            pass
    if not pure_json_contract and _tools_include_sqlite_mcp(tools):
        try:
            from l3_node.prompt_sqlite_sop import (
                SQLITE_ACTOR_CRITIC_STUB_NOTE,
                SQLITE_REACT_SOP_BLOCK,
                SQLITE_SELF_CRITIC_BLOCK,
            )

            suffix_chunks.append(
                SuffixChunk("high", "sqlite_react_sop", f"\n{SQLITE_REACT_SOP_BLOCK}\n", eviction_rank=92)
            )
            suffix_chunks.append(
                SuffixChunk("high", "sqlite_self_critic", f"\n{SQLITE_SELF_CRITIC_BLOCK}\n", eviction_rank=92)
            )
            suffix_chunks.append(
                SuffixChunk(
                    "mid",
                    "sqlite_critic_arch_note",
                    f"\n{SQLITE_ACTOR_CRITIC_STUB_NOTE}\n",
                    eviction_rank=35,
                )
            )
        except ImportError:
            pass
    if not pure_json_contract:
        if (local_mem or "").strip():
            suffix_chunks.append(
                SuffixChunk("low", "passive_local_memory", f"\n{local_mem}\n", eviction_rank=10)
            )
        if (jachin_rules or "").strip():
            suffix_chunks.append(
                SuffixChunk("high", "jachin_workspace_rules", f"\n{jachin_rules}\n", eviction_rank=90)
            )
        if (safety_lock_txt or "").strip():
            suffix_chunks.append(
                SuffixChunk(
                    "high",
                    "jachin_safety_lock",
                    f"\n{safety_lock_txt}\n",
                    eviction_rank=98,
                )
            )
        if (plan_ctx or "").strip():
            suffix_chunks.append(
                SuffixChunk("high", "task_plan_disk", f"\n{plan_ctx}\n", eviction_rank=95)
            )
        if (hr_runtime_ctx or "").strip():
            suffix_chunks.append(SuffixChunk("mid", "hr_runtime", f"\n{hr_runtime_ctx}\n", eviction_rank=45))
        if (p1_inject or "").strip():
            suffix_chunks.append(SuffixChunk("low", "p1_inject", p1_inject, eviction_rank=15))
        if (capability_catalog_hint or "").strip():
            suffix_chunks.append(
                SuffixChunk("mid", "capability_catalog", capability_catalog_hint, eviction_rank=40)
            )
        if (hr_recruitment_hint or "").strip():
            suffix_chunks.append(
                SuffixChunk("mid", "hr_recruitment_sop", hr_recruitment_hint, eviction_rank=42)
            )
        if (plan_hint or "").strip():
            suffix_chunks.append(SuffixChunk("low", "plan_hint", plan_hint, eviction_rank=18))
        if (hr_hint or "").strip():
            suffix_chunks.append(SuffixChunk("high", "hr_tool_routing", hr_hint, eviction_rank=55))
        if slim_mode:
            _slim_sqlite = ""
            if _tools_include_sqlite_mcp(tools):
                try:
                    from l3_node.prompt_sqlite_sop import SQLITE_REACT_SOP_BLOCK_SLIM

                    _slim_sqlite = SQLITE_REACT_SOP_BLOCK_SLIM
                except ImportError:
                    pass
            _react_footer_body = (
                "【记忆】被动注入仅供参考；事实以 recall_memory / core:local_memory_search 为准。\n"
                "【输出】工具执行后须给出 Final Answer。若用户要求仅 JSON/固定结构，Final Answer 后只写该结构，"
                "禁止井号标题行与无关套话。\n"
                "若本轮调用了 HR 透析镜且用户未禁止固定格式，Final Answer 仍须以 Observation 为准完整呈现结果。\n"
                f"{REACT_FOOTER_FACTUAL_DB_BLOCK_SLIM}{_slim_sqlite}"
            )
        else:
            _react_footer_body = (
                "【记忆 SSOT】被动「本地记忆」仅为提示；事实以 recall_memory / core:local_memory_search 检索为准。\n"
                "【安全锁】若上文含安全锁段，与 MEMORY.md / 闲聊推测冲突时 **以安全锁为准**。"
                "追加事实用 **core:safety_lock_append**（默认进入 **待审批**，由管理员 CLI 刷入 MD，勿向模型泄露管理员密钥）；"
                "撤销用 **core:safety_lock_remove**（entry_id）；查看队列 **core:safety_lock_list_pending**。\n"
                "注意：工具执行后务必给出 Final Answer。禁止对 Observation 进行总结、概括或改写；若 Observation 已是完整报告，必须原样完整输出。"
                "HR 透析镜执行后，Final Answer 必须以「✅ 执行成功，本次分析了 X 份简历」开头（X 从 Observation 提取），再输出完整报告。\n"
                f"{REACT_FOOTER_FACTUAL_DB_BLOCK}\n"
            )
        # 与 [ENVIRONMENT_REPORT] / 参谋长人设同条件：页脚最末追加，优先于其它后缀块被保留（eviction_rank=100）
        if chief_advisor_mode:
            _react_footer_body += CHIEF_ADVISOR_LOGIC_VALIDATION_BLOCK
        suffix_chunks.append(
            SuffixChunk(
                "high",
                "react_footer",
                _react_footer_body,
                eviction_rank=100,
            )
        )
    _suffix_budget = load_prompt_suffix_budget()
    _total_cap = load_system_prompt_total_max_chars()
    prompt_prefix, prompt_suffix = apply_system_prompt_total_cap(
        prefix_without_tools=_prefix_before_tools,
        tools_desc=tools_desc,
        prefix_after_tools=_prefix_after_tools,
        suffix_chunks=suffix_chunks,
        suffix_budget=_suffix_budget,
        total_max_chars=_total_cap,
    )
    if _total_cap > 0 and len(prompt_prefix) + len(prompt_suffix) > _total_cap:
        logger.warning(
            "[prompt_total_cap] still_over total=%s cap=%s (check nexus prompt.system_prompt_max_chars)",
            len(prompt_prefix) + len(prompt_suffix),
            _total_cap,
        )
    return prompt_prefix + prompt_suffix


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
    ) -> None:
        self.sub_agent_id = sub_agent_id
        self.system_prompt = system_prompt
        self.allowed_skills = allowed_skills
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
        tools = load_tools(allowed_skills=self.allowed_skills)
        system = f"""{self.system_prompt}
可用工具：
{build_tools_description(tools)}

输出格式：Thought / Action / Action Input / Observation / Final Answer
"""
        result = await run_agent(
            task,
            engine,
            max_iterations=max_iterations,
            _system_prompt_override=system,
            _initial_messages=self.messages,
            implicit_attribution={"channel": "delegate_sub_agent", "sub_agent_id": self.sub_agent_id},
            _delegate_depth=delegate_depth,
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


async def _run_sub_agent(
    task_spec: dict[str, Any],
    engine: LiteLLMEngine,
    *,
    delegate_depth: int = 1,
) -> str:
    """运行子 Agent，完成指定子任务。内部调用 _spawn_sub_agent_async（一次性，不复用）。"""
    role = (task_spec.get("role") or "default").lower()
    task = task_spec.get("task", "")
    result, _ = await _spawn_sub_agent_async(role, task, engine, delegate_depth=delegate_depth)
    return result


async def _spawn_sub_agent_async(
    role: str,
    task: str,
    engine: LiteLLMEngine,
    sub_agent_id: Optional[str] = None,
    *,
    delegate_depth: int = 1,
) -> tuple[str, str]:
    """异步版 spawn_sub_agent，供 delegate 流程调用。"""
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
    prompt = SUB_AGENT_PROMPTS.get(role_lower, SUB_AGENT_PROMPTS["default"])
    allowed = SUB_AGENT_ALLOWED_SKILLS.get(role_lower, SUB_AGENT_ALLOWED_SKILLS["default"])
    global_allowed = _get_allowed_skills()
    if global_allowed is not None:
        allowed = [s for s in allowed if s in _build_allowed_ids(global_allowed)]

    if sub_agent_id and sub_agent_id in _sub_agent_registry:
        agent = _sub_agent_registry[sub_agent_id]
        result = await agent.run_once(task, eff_engine, delegate_depth=delegate_depth)
        return result, sub_agent_id

    sid = sub_agent_id or f"sub-{uuid.uuid4().hex[:8]}"
    agent = SubAgent(sid, prompt, allowed)
    _sub_agent_registry[sid] = agent
    result = await agent.run_once(task, eff_engine, delegate_depth=delegate_depth)
    return result, sid


def _foreground_tool_timeout_json(tool: str, sec: float) -> str:
    return json.dumps(
        {
            "status": "timeout",
            "reason": "foreground_sync_budget_exceeded",
            "message": (
                f"工具在 {sec:g}s 内未完成（前台同步预算）。"
                "长耗时/大批量请使用 core:submit_background_task；"
                "在工具注册中声明 long_running 或配置 foreground_tools.long_running_tool_ids 可豁免此时限。"
                "注意：超时后前台已恢复，但同步阻塞调用可能仍在线程中运行（无法强制终止）。"
            ),
            "tool": tool,
        },
        ensure_ascii=False,
    )


async def _invoke_react_tool(
    tool: str,
    inp: str,
    allowed_skills: Optional[list[str]],
    ctx: PipelineContext,
) -> str:
    """前台同步工具：可选 asyncio 超时；后台任务 / 子代理 / 豁免工具跳过。"""
    _rtrace = str(ctx.metadata.get("_react_step_trace") or "")
    _inp = inp or ""
    logger.info(
        "[L3 Agent][工具路由] trace=%s run_id=%s tool=%s inp_len=%s preview=%r%s",
        _rtrace,
        getattr(ctx, "run_id", "") or "",
        tool,
        len(_inp),
        _inp[:500],
        "…(truncated)" if len(_inp) > 500 else "",
    )
    _t0 = time.perf_counter()
    try:
        _gb = ctx.metadata.get("_gateway_bundle")
        if _gb is not None:
            from l3_node.intent_gateway.jit_binding import jit_resolve_entity_refs

            await jit_resolve_entity_refs(
                resource_ref_keys=[f"tool:{tool}"],
                tenant_id=getattr(_gb, "tenant_id", "") or "",
                context={
                    "tool": tool,
                    "inp": inp or "",
                    "inp_len": len(inp or ""),
                    "messages": getattr(ctx, "messages", None),
                },
            )
    except Exception:
        pass
    from l3_node.foreground_tool_policy import (
        channel_exempt_from_timeout,
        load_foreground_tools_config,
        tool_bypasses_foreground_timeout,
    )

    mcp_registry = get_mcp_registry()
    cfg = load_foreground_tools_config()
    ch = str(ctx.metadata.get("_implicit_channel") or "")
    sec = float(cfg.get("sync_timeout_sec") or 5.0)
    mcp_lr = tool in mcp_registry.known_mcp_tools and mcp_registry.is_long_running_mcp_tool(tool)
    use_timeout = (
        bool(cfg.get("enabled", True))
        and sec > 0
        and not channel_exempt_from_timeout(ch, cfg)
        and not tool_bypasses_foreground_timeout(tool, cfg, mcp_declares_long_running=mcp_lr)
    )
    _is_mcp = tool in mcp_registry.known_mcp_tools
    exec_trace(
        logger,
        "工具调度开始 trace=%s run_id=%s tool=%s mcp=%s use_timeout=%s sync_limit_s=%s inp_len=%d",
        _rtrace,
        (getattr(ctx, "run_id", "") or "")[:12],
        (tool or "")[:160],
        _is_mcp,
        use_timeout,
        sec if use_timeout else 0.0,
        len(_inp),
    )
    _out: str | None = None
    try:
        if _is_mcp:
            if use_timeout:
                try:
                    _out = await asyncio.wait_for(
                        mcp_registry.invoke(tool, inp, allowed_skills=allowed_skills),
                        timeout=sec,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "[L3 Agent] 前台工具超时 tool=%s limit=%ss（wait_for 已返回；"
                        "MCP/线程内阻塞仍可能继续，无法强制终止；见长文档）",
                        tool,
                        sec,
                    )
                    _out = _foreground_tool_timeout_json(tool, sec)
            else:
                _out = await mcp_registry.invoke(tool, inp, allowed_skills=allowed_skills)
        elif use_timeout:
            try:
                _out = await asyncio.wait_for(
                    asyncio.to_thread(run_tool, tool, inp, allowed_skills),
                    timeout=sec,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "[L3 Agent] 前台工具超时 tool=%s limit=%ss（wait_for 已返回；"
                    "to_thread 内同步调用仍可能继续，无法强制终止；见长文档）",
                    tool,
                    sec,
                )
                _out = _foreground_tool_timeout_json(tool, sec)
        else:
            _out = run_tool(tool, inp, allowed_skills)
        return _out
    finally:
        if _out is not None:
            exec_trace(
                logger,
                "工具调度结束 trace=%s tool=%s elapsed_ms=%.0f out_len=%d",
                _rtrace,
                (tool or "")[:160],
                (time.perf_counter() - _t0) * 1000.0,
                len(_out),
            )


def _p2_record_skill_outcome(ctx: PipelineContext, skill_id: str, observation: str) -> None:
    """P2-8：记录意图→工具结果统计（失败为启发式判断）。"""
    try:
        from l3_node.intent_skill_stats import record_tool_outcome

        record_tool_outcome(ctx.intent or "", skill_id, observation or "")
    except ImportError:
        pass


async def _run_react_core(
    ctx: PipelineContext,
    engine: LiteLLMEngine,
    on_step: Optional[Callable[[str, str, str], None]] = None,
) -> None:
    allowed_skills = ctx.metadata.get("_allowed_skills")
    if allowed_skills is None:
        allowed_skills = _get_allowed_skills()
    use_mock = ctx.metadata.get("_use_mock", False)
    max_iterations = ctx.metadata.get("_max_iterations", MAX_REACT_ITERATIONS)
    on_chunk = ctx.metadata.get("_on_chunk")
    messages = ctx.messages

    def _emit(step_type: str, content: str) -> None:
        if on_step:
            on_step(step_type, content, ctx.run_id)

    def _llm_control_kwargs() -> dict[str, Any]:
        out: dict[str, Any] = {}
        _ce = ctx.metadata.get("_cancel_event")
        if _ce is not None:
            out["l3_cancel_event"] = _ce
        _acc = ctx.metadata.get("_llm_token_accumulator")
        if _acc is not None:
            out["l3_token_accumulator"] = _acc
            out["l3_token_budget_max"] = ctx.metadata.get("_llm_token_budget_max")
        return out

    # 追踪本轮已执行的招聘相关工具，用于拒绝「未调用工具却声称已发布」的幻觉回复
    ctx._executed_tools_this_run = set()
    try:
        from l3_node.primitives.mcp.registry import clear_last_add_automated_recruitment_task_payload

        clear_last_add_automated_recruitment_task_payload()
    except Exception:
        pass
    ctx.metadata.pop(_L3_CODER_MODE_META, None)
    ctx.metadata.pop(_L3_CODER_ENGINE_CACHE_META, None)
    ctx.metadata.pop(_L3_COMPLEX_ENGINE_CACHE_META, None)

    if not ctx.metadata.get("_skills_unfiltered"):
        ctx.metadata["_skills_unfiltered"] = list(ctx.metadata.get("_skills") or [])

    for iteration in range(max_iterations):
        ctx.metadata["_react_iteration"] = iteration + 1
        # 每轮唯一 trace，便于 PowerShell 里对比「这一次 vs 下一次」日志
        ctx.metadata["_react_step_trace"] = (
            f"{(ctx.run_id or 'norun')}-i{iteration + 1}-t{time.time_ns():x}"
        )
        ctx.current_response = ""
        ctx.parsed_action = None
        ctx.observation = ""
        exec_trace(
            logger,
            "ReAct 轮次开始 trace=%s iter=%d/%d run_id=%s",
            ctx.metadata.get("_react_step_trace"),
            iteration + 1,
            max_iterations,
            (ctx.run_id or "")[:12],
        )

        # strict 写后 verify：硬只读轮 —— 重载 system 与可见工具列表
        if ctx.metadata.get("_react_system_prompt_full") is None:
            ctx.metadata["_react_system_prompt_full"] = ctx.system_prompt
        base_skills = ctx.metadata.get("_skills_unfiltered") or []
        skills = base_skills
        try:
            from l3_node.intelligence_b_execution import (
                filter_tools_for_verify_round,
                get_enforce_readonly_verify_round,
            )

            if ctx.metadata.get("_intel_strict_pending_verify") and get_enforce_readonly_verify_round():
                skills = filter_tools_for_verify_round(base_skills)
                _spe = ctx.metadata.get("_system_prompt_extras") or {}
                ctx.system_prompt = _build_system_prompt(
                    tools=skills,
                    allow_delegate=False,
                    allow_recall=True,
                    allow_coordinate=False,
                    prompt_cycle=ctx.metadata.get("_prompt_cycle"),
                    recruitment_longform=True,
                    prompt_style=str(ctx.metadata.get("_react_prompt_style") or "full"),
                    pure_json_contract=bool(ctx.metadata.get("_pure_json_contract")),
                    gateway_inject=str(ctx.metadata.get("_gw_inject_stored") or ""),
                    safety_lock_user_text=str(ctx.intent or ""),
                    chief_advisor_mode=bool(_spe.get("chief_advisor")),
                    environment_report_block=str(_spe.get("environment_report_block") or ""),
                )
            else:
                ctx.system_prompt = ctx.metadata.get("_react_system_prompt_full") or ctx.system_prompt
        except ImportError:
            pass
        try:
            from l3_node.intent_gateway.planning_gate_phase import filter_skills_for_planning_composite

            skills = filter_skills_for_planning_composite(skills, ctx)
        except Exception:
            pass
        ctx.metadata["_skills"] = skills

        await global_hooks.run(HOOK_BEFORE_LLM_THINK, ctx)
        if ctx.aborted:
            return

        _cev = ctx.metadata.get("_cancel_event")
        if _cev is not None and getattr(_cev, "is_set", lambda: False)():
            ctx.final_answer = "[ExecutionBrief] 运行已被取消（协作式 cancel）。"
            _emit("answer", ctx.final_answer)
            return

        full_messages = [{"role": "system", "content": ctx.system_prompt}] + messages
        logger.debug("[L3 Agent] ReAct iter=%d 调用 LLM stream=%s", iteration + 1, bool(on_chunk))
        _force_complex = False
        try:
            from l3_node.intelligence_b_execution import get_execution_mode

            if get_execution_mode() in ("planned", "strict"):
                _force_complex = True
        except ImportError:
            pass
        _eff = _react_engine_for_iteration(
            engine,
            ctx,
            full_messages=full_messages,
            tools_count=len(skills or []),
            react_iteration=iteration,
            force_complex=_force_complex,
        )
        _route_suffix = ""
        if _eff is not engine:
            _route_suffix = "_coder" if ctx.metadata.get(_L3_CODER_MODE_META) else "_complex"
        _llm_purpose = (
            f"react_iter_{iteration + 1}_stream{_route_suffix}"
            if on_chunk
            else f"react_iter_{iteration + 1}{_route_suffix}"
        )
        _lkw = _llm_control_kwargs()
        if on_chunk:
            _lkw["l3_run_id"] = ctx.run_id
        try:
            if on_chunk:
                response = await _eff.generate_response_stream(
                    full_messages,
                    chunk_callback=on_chunk,
                    temperature=0.7,
                    max_tokens=16384,
                    l3_call_purpose=_llm_purpose,
                    **_lkw,
                )
            else:
                result = await _eff.generate_response(
                    full_messages,
                    temperature=0.7,
                    max_tokens=16384,
                    l3_call_purpose=_llm_purpose,
                    **_lkw,
                )
                response = result.get("content", result) if isinstance(result, dict) else str(result)
        except RunCancelledError:
            ctx.final_answer = "[ExecutionBrief] 运行已被取消（LLM 协作式中断）。"
            _emit("answer", ctx.final_answer)
            return
        except Exception as e:
            try:
                from l3_node.llm_budget import BudgetExhaustedError

                if isinstance(e, BudgetExhaustedError):
                    ctx.final_answer = (
                        f"[ExecutionBrief] Token 预算用尽（resource）：累计 {e.used}，上限 {e.limit}。"
                        "可调整 ~/.jachin/nexus_config.json 中 agent.main_max_total_tokens / agent.sub_agent_max_total_tokens。"
                    )
                    _emit("answer", ctx.final_answer)
                    return
            except ImportError:
                pass
            raise

        ctx.current_response = response
        exec_trace(
            logger,
            "ReAct LLM 返回 trace=%s iter=%d response_len=%d",
            str(ctx.metadata.get("_react_step_trace") or ""),
            iteration + 1,
            len(response or ""),
        )

        try:
            from l3_node.intent_gateway.config import get_intent_gateway_config
            from l3_node.intent_gateway.planning_gate_phase import extract_needs_info, is_composite_planning_locked

            _ni_txt = extract_needs_info(response)
            if (
                _ni_txt
                and bool(get_intent_gateway_config().get("needs_info_gateway_enabled", True))
                and is_composite_planning_locked(ctx)
            ):
                ctx.final_answer = f"【需要补充信息】{_ni_txt}"
                _emit("answer", ctx.final_answer)
                try:
                    from l3_node.intent_gateway.intent_tracker import emit_intent_tracker_event

                    emit_intent_tracker_event("needs_info_short_circuit", {"chars": len(_ni_txt)})
                except Exception:
                    pass
                return
        except Exception:
            pass

        # 一旦 LLM 输出含 JD 配置，立即写入 fallback，供 Lark 会话丢失时「同意」兜底
        _jd_raw = _extract_jd_config_from_conversation(messages, response)
        if _jd_raw:
            try:
                _jd_obj = json.loads(_jd_raw)
                _jd = _jd_obj.get("jd_config") if isinstance(_jd_obj, dict) else None
                if isinstance(_jd, dict):
                    _lc = str(ctx.metadata.get("_lark_chat_id") or "").strip()
                    _comb_b = messages + [{"role": "assistant", "content": response}]
                    if _hr_branch_b_recruitment_context(_comb_b):
                        _jd = {**_jd, "skip_boss_post": True}
                        logger.debug(
                            "[L3 Agent] 分支B 语境：pending JD 已写 skip_boss_post，飞书裸「同意」将不强制 atom_post"
                        )
                    elif _hr_user_intent_skip_boss_post(messages):
                        _jd = {**_jd, "skip_boss_post": True}
                        logger.debug(
                            "[L3 Agent] 用户话术=已有岗/只收网：pending 已写 skip_boss_post（不写 jd.json）"
                        )
                    elif _hr_assistant_declares_skip_boss_post(response):
                        _jd = {**_jd, "skip_boss_post": True}
                        logger.debug(
                            "[L3 Agent] 助手话术=只收网/已有岗：pending 已写 skip_boss_post"
                        )
                    _save_last_jd_pending(_jd, chat_id=_lc)
                    logger.debug("[L3 Agent] 检测到 JD 输出，已写入 fallback job_title=%s", _jd.get("job_title"))
            except Exception:
                pass

        thought = re.search(
            r"Thought:\s*(.+?)(?=Action:|Final Answer:|Answer:|\n\n|$)",
            response, re.DOTALL | re.IGNORECASE,
        )
        if thought:
            _emit("thought", thought.group(1).strip())

        parsed = _parse_action(response, skills, use_mock=use_mock, allowed_skills=allowed_skills)
        ctx.parsed_action = parsed
        _iter_n = ctx.metadata.get("_react_iteration")
        _rtrace = str(ctx.metadata.get("_react_step_trace") or "")
        if parsed is None:
            logger.info(
                "[L3 Agent][ReAct 解析] trace=%s iter=%s parsed=None（无 Action / 格式无法识别）"
                " response_len=%s head=%r",
                _rtrace,
                _iter_n,
                len(response or ""),
                (response or "")[:400].replace("\n", "\\n"),
            )
        else:
            _pt = parsed.get("type")
            if _pt == "native":
                _tn = str(parsed.get("tool") or "")
                logger.info(
                    "[L3 Agent][ReAct 解析] trace=%s iter=%s type=native tool=%s inp_len=%s "
                    "(仅 mcp:* 会打 [MCP Registry] invoke；core:* 走 run_tool 无该行)",
                    _rtrace,
                    _iter_n,
                    _tn[:160],
                    len(str(parsed.get("input") or "")),
                )
            else:
                logger.info(
                    "[L3 Agent][ReAct 解析] trace=%s iter=%s type=%s",
                    _rtrace,
                    _iter_n,
                    _pt,
                )

        try:
            from l3_node.intelligence_b_execution import (
                get_execution_mode,
                get_force_universal_planning_chain,
                scan_messages_for_valid_plan,
            )

            _eb_mode = get_execution_mode()
            _force_plan = get_force_universal_planning_chain()
        except ImportError:
            _eb_mode = "react"
            _force_plan = False

        _ich_plan = str(ctx.metadata.get("_implicit_channel") or "")
        _planning_gate = _eb_mode in ("planned", "strict") or _force_plan
        if _ich_plan in ("delegate_sub_agent", "background_task"):
            _planning_gate = False

        if (
            _planning_gate
            and parsed is not None
            and parsed.get("type") not in ("answer",)
        ):
            try:
                from l3_node.intelligence_b_execution import (
                    get_require_brainstorm_card,
                    parse_types_allowed_before_plan_gates,
                    scan_messages_for_valid_brainstorm,
                    scan_messages_for_valid_plan,
                )

                _pre = parse_types_allowed_before_plan_gates()
                if parsed.get("type") not in _pre:
                    _combined = messages + [{"role": "assistant", "content": response}]
                    _mode_label = (
                        _eb_mode
                        if _eb_mode != "react"
                        else ("react+force_universal_planning_chain" if _force_plan else "react")
                    )
                    if get_require_brainstorm_card() and not scan_messages_for_valid_brainstorm(_combined):
                        messages.append({"role": "assistant", "content": response})
                        messages.append({
                            "role": "user",
                            "content": (
                                "【系统 · intelligence_b】当前为 %s：请先输出 brainstorm 卡 JSON（jachin_brainstorm_card："
                                "angles 非空数组、constraints、open_questions），再输出计划卡。"
                            )
                            % _mode_label,
                        })
                        try:
                            from core.intelligence_workspace import emit_intelligence_event

                            emit_intelligence_event("brainstorm_gate_blocked", {"mode": _mode_label})
                        except Exception:
                            pass
                        continue
                    if not scan_messages_for_valid_plan(_combined):
                        messages.append({"role": "assistant", "content": response})
                        messages.append({
                            "role": "user",
                            "content": (
                                "【系统 · intelligence_b】当前为 %s：请先输出完整计划卡 JSON（jachin_plan_card："
                                "goal、steps 非空数组、risks、rollback_point），通过校验后才会执行工具。"
                            )
                            % _mode_label,
                        })
                        try:
                            from core.intelligence_workspace import emit_intelligence_event

                            emit_intelligence_event("plan_gate_blocked", {"mode": _mode_label})
                        except Exception:
                            pass
                        continue
            except ImportError:
                pass

        if (
            parsed is not None
            and parsed.get("type") not in ("answer", None)
        ):
            try:
                from l3_node.task_plan_policy import task_plan_gate_blocks_action

                if task_plan_gate_blocks_action(parsed, ctx.intent or ""):
                    messages.append({"role": "assistant", "content": response})
                    try:
                        from l3_node.intent_gateway.pushback_copy import task_plan_gate_user_message

                        _tp_msg = task_plan_gate_user_message()
                    except ImportError:
                        _tp_msg = (
                            "【系统 · task_plan】当前任务需要先在工作区根目录创建/完善 task_plan.md（至少概述目标与步骤）。"
                            "请使用 Action: core:fs_write，将内容写入 task_plan.md；完成后再执行写操作、Shell 或 delegate/coordinate。"
                        )
                    messages.append({"role": "user", "content": _tp_msg})
                    _stp = ctx.metadata.get("_on_step")
                    if _stp:
                        try:
                            import json as _json

                            _stp(
                                "system_status",
                                _json.dumps(
                                    {"status": "识别为多步任务：task_plan 门禁生效，请先落盘 task_plan.md。"},
                                    ensure_ascii=False,
                                ),
                                ctx.run_id,
                            )
                        except Exception:
                            pass
                    try:
                        from core.intelligence_workspace import emit_intelligence_event

                        emit_intelligence_event("task_plan_gate_blocked", {})
                    except Exception:
                        pass
                    continue
            except ImportError:
                pass

        if parsed is not None:
            try:
                from l3_node.intent_gateway.intent_tracker import emit_intent_tracker_event
                from l3_node.intent_gateway.planning_gate_phase import planning_composite_gate_blocks_action

                if planning_composite_gate_blocks_action(parsed, ctx):
                    messages.append({"role": "assistant", "content": response})
                    try:
                        from l3_node.intent_gateway.pushback_copy import planning_composite_gate_user_message

                        _pc_msg = planning_composite_gate_user_message()
                    except ImportError:
                        _pc_msg = (
                            "【系统 · planning_composite】当前为复合规划阶段：请先用 core:fs_write 将可执行计划写入 "
                            "workspace 根目录 task_plan.md；计划中提及的工具 id 须在当前可见白名单内。"
                            "若关键信息不足请输出 [Needs_Info: …] 向用户反问。"
                            "在规划静态扫描通过前，禁止 delegate/coordinate、禁止其它写类工具与 MCP 执行。"
                        )
                    messages.append({"role": "user", "content": _pc_msg})
                    _stp2 = ctx.metadata.get("_on_step")
                    if _stp2:
                        try:
                            import json as _json

                            _stp2(
                                "system_status",
                                _json.dumps(
                                    {"status": "识别为复合任务，规划门禁已拦截高风险动作；请先完善 task_plan.md。"},
                                    ensure_ascii=False,
                                ),
                                ctx.run_id,
                            )
                        except Exception:
                            pass
                    try:
                        from core.intelligence_workspace import emit_intelligence_event

                        emit_intelligence_event("planning_composite_gate_blocked", {})
                    except Exception:
                        pass
                    emit_intent_tracker_event("planning_composite_gate_blocked", {})
                    continue
            except Exception:
                pass

        if parsed is None:
            # 兜底：用户回复「同意」但 LLM 误判为「没有配置」时，从对话中提取 JD 并强制要求调用发布工具
            last_user_content = ""
            for m in reversed(messages or []):
                if isinstance(m, dict) and m.get("role") == "user":
                    last_user_content = (m.get("content") or "").strip()
                    break
            if re.search(r"同意|确认|确认发布|就按这个发|直接发布", last_user_content):
                if _hr_branch_b_recruitment_context(messages):
                    _fb_b = _extract_branch_b_add_task_payload(messages)
                    _no_cfg = "没有" in (response or "") and "配置" in (response or "")
                    if _fb_b and (_no_cfg or "没有之前收集" in (response or "")):
                        logger.info(
                            "[L3 Agent] 分支B：用户已确认但 LLM 误判无配置，强制要求 add_automated_recruitment_task"
                        )
                        messages.append({"role": "assistant", "content": response})
                        messages.append({
                            "role": "user",
                            "content": (
                                "【系统·分支B】对话中已有收网任务配置（assistant 的 ```json``` 块）。"
                                "请立即输出 Action: mcp:add_automated_recruitment_task，Action Input 填入该完整 JSON。"
                                "**禁止** mcp:atom_post_job_boss。"
                            ),
                        })
                        continue
                fallback = _extract_jd_config_from_conversation(messages, response)
                no_config_hint = "没有" in (response or "") and "配置" in (response or "")
                if fallback and (no_config_hint or "没有之前收集" in (response or "")):
                    logger.info(
                        "[L3 Agent] 用户已确认但 LLM 误判无配置，从对话提取 jd_config 并强制要求调用 atom_post_job_boss"
                    )
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": "【系统】对话历史中已有完整 JD 配置（见上方 assistant 消息的 ```json``` 代码块）。请立即输出 Action: mcp:atom_post_job_boss，Action Input 填入 {\"jd_config\": <该 JSON 对象>}。禁止输出「没有配置」类提示。",
                    })
                    continue
            if "Final Answer:" in response or "Answer:" in response:
                for prefix in ("Final Answer:", "Answer:"):
                    idx = response.lower().find(prefix.lower())
                    if idx >= 0:
                        ans = response[idx + len(prefix):].strip()
                        if ans:
                            # 校验：招聘工具链必须完整调用
                            has_success = _hr_recruitment_success_answer(ctx, ans)
                            no_post = "atom_post_job_boss" not in ctx._executed_tools_this_run
                            sched_step_done = (
                                "add_automated_recruitment_task" in ctx._executed_tools_this_run
                                or "hr_scheduler_send_confirm_prompt" in ctx._executed_tools_this_run
                            )
                            _b_ctx = _hr_branch_b_recruitment_context(messages)
                            _skip_force_post = _hr_skip_force_atom_post_hallucination_guard(messages, ctx)
                            if (
                                not _b_ctx
                                and not _skip_force_post
                                and has_success
                                and no_post
                                and _hr_answer_claims_job_published(ans)
                            ):
                                logger.warning(
                                    "[L3 Agent] 拒绝幻觉回复：声称职位已发布但未调用 atom_post_job_boss，强制要求先执行工具"
                                )
                                messages.append({"role": "assistant", "content": response})
                                messages.append({
                                    "role": "user",
                                    "content": "【系统校验】你声称职位已发布，但未实际调用 mcp:atom_post_job_boss。请立即输出 Action: mcp:atom_post_job_boss，Action Input 为上一轮 JSON 配置单（从你之前的 Assistant 回复中提取），不得直接给出 Final Answer。",
                                })
                                continue
                            if (
                                not _b_ctx
                                and not _skip_force_post
                                and has_success
                                and not sched_step_done
                                and _hr_answer_claims_job_published(ans)
                            ):
                                logger.warning(
                                    "[L3 Agent] 招聘工具链不完整：发帖后未发调度确认或未注册任务"
                                )
                                messages.append({"role": "assistant", "content": response})
                                messages.append({
                                    "role": "user",
                                    "content": (
                                        "【系统校验】你已发布职位。请先输出 Action: mcp:hr_scheduler_send_confirm_prompt，"
                                        'Action Input 为 {"job_name": "<与 job_title 一致>"}，向飞书发送无人值守参数确认单（定时任务此时不启动）。'
                                        "若 HR 明确要求跳过飞书、立即开跑，可改调用 mcp:add_automated_recruitment_task。不得直接给出 Final Answer。"
                                    ),
                                })
                                continue
                            try:
                                from l3_node.intelligence_b_execution import get_execution_mode

                                if get_execution_mode() == "strict" and ctx.metadata.get("_intel_strict_pending_verify"):
                                    if "VERIFY_PASS" not in (response or "").upper():
                                        messages.append({"role": "assistant", "content": response})
                                        messages.append({
                                            "role": "user",
                                            "content": "【strict】已执行写操作/Shell/apply_patch。请先只用只读工具复核，再在回复中包含 VERIFY_PASS 后给出 Final Answer。",
                                        })
                                        continue
                                    ctx.metadata["_intel_strict_pending_verify"] = False
                            except ImportError:
                                pass
                            if (
                                _hr_answer_claims_unmanned_scheduler_running(ans)
                                and "add_automated_recruitment_task"
                                not in ctx._executed_tools_this_run
                                and "hr_scheduler_send_confirm_prompt"
                                not in ctx._executed_tools_this_run
                            ):
                                logger.warning(
                                    "[L3 Agent] 拒绝幻觉：声称无人值守/调度已运行但未调用 "
                                    "add_automated_recruitment_task 或 hr_scheduler_send_confirm_prompt"
                                )
                                messages.append({"role": "assistant", "content": response})
                                messages.append({
                                    "role": "user",
                                    "content": (
                                        "【系统校验】你声称无人值守或收网调度已启动/运行中，但本轮尚未执行 "
                                        "mcp:add_automated_recruitment_task（或先 mcp:hr_scheduler_send_confirm_prompt）。"
                                        "飞书侧可能已合并 jd.json，**必须**输出 Action: mcp:add_automated_recruitment_task，"
                                        "Action Input 为 JSON：至少含 job_name、enable_greet_recommend、"
                                        "resume_collect_target、analyze_threshold（与 HR 要求一致）；"
                                        "禁止无工具调用直接 Final Answer 声称已启动。"
                                    ),
                                })
                                continue
                            _rtrace = str(ctx.metadata.get("_react_step_trace") or "")
                            _ans_s = str(ans or "")
                            logger.info(
                                "[L3 Agent][FinalAnswer 路径] trace=%s run_id=%s via=parsed_None+Final_prefix "
                                "answer_len=%s preview=%r%s",
                                _rtrace,
                                ctx.run_id,
                                len(_ans_s),
                                _ans_s[:700],
                                "…(truncated)" if len(_ans_s) > 700 else "",
                            )
                            _emit("answer", ans)
                            ctx.final_answer = _apply_hr_recruitment_final_answer_table_sync(ans, ctx)
                            messages.append({"role": "assistant", "content": response})
                            return
            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": "请给出最终回复，以 Final Answer: 开头。"})
            continue

        if parsed["type"] == "answer":
            ans = parsed.get("content", response)
            _rtrace = str(ctx.metadata.get("_react_step_trace") or "")
            _ans_s = str(ans or "")
            logger.info(
                "[L3 Agent][FinalAnswer 路径] trace=%s run_id=%s type=answer answer_len=%s preview=%r%s",
                _rtrace,
                ctx.run_id,
                len(_ans_s),
                _ans_s[:700],
                "…(truncated)" if len(_ans_s) > 700 else "",
            )
            if any(
                x in _ans_s
                for x in (
                    "-32602",
                    "MCP error",
                    "write_file",
                    "Invalid arguments",
                    "invalid_type",
                    "received undefined",
                )
            ):
                logger.warning(
                    "[L3 Agent][排障·语义] trace=%s Final Answer 文本内含 MCP/校验类字样。"
                    "若**同一次请求**内未见后续的 [L3 Agent][工具路由] 且未见 [MCP Registry] invoke / "
                    "[MCP] call_tool，则界面上的 JSON 多为**模型直接输出**，不是 stdio MCP 的真实返回。"
                    "真实工具调用时一定会先出现 [L3 Agent][工具路由]（含 inp_preview）。",
                    _rtrace,
                )
            if (
                _is_hallucinated_final_mcp_error_json(_ans_s)
                and not ctx.metadata.get("_react_fake_mcp_error_retry_done")
                and not ctx._executed_tools_this_run
            ):
                ctx.metadata["_react_fake_mcp_error_retry_done"] = True
                logger.warning(
                    "[L3 Agent][纠偏] trace=%s 将注入系统消息并续跑 ReAct（仅一次）："
                    "Final Answer 为虚构 MCP 错误 JSON，且本轮尚未执行任何工具。",
                    _rtrace,
                )
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": (
                        "【系统纠偏】你刚才的 Final Answer 是模仿 MCP/API 错误格式的 JSON，但本轮并未执行任何工具"
                        "（不应出现 -32602 类真实返回）。\n"
                        "请改用 ReAct 文本续写，**禁止**再用 Final Answer 提交伪错误 JSON：\n"
                        "1) Thought: …\n"
                        "2) Action: core:fs_write\n"
                        "3) Action Input: "
                        '{"path":"<相对工作区路径，如 scripts/xxx.py>","content":"<完整文件内容>"}\n'
                        "path 须为非空相对路径；也可用 mcp:write_file + 同上 JSON。"
                        "写盘成功后再 Final Answer 给出绝对路径。"
                    ),
                })
                continue
            # 校验：招聘工具链必须完整调用
            has_success = _hr_recruitment_success_answer(ctx, ans)
            no_post = "atom_post_job_boss" not in ctx._executed_tools_this_run
            sched_step_done = (
                "add_automated_recruitment_task" in ctx._executed_tools_this_run
                or "hr_scheduler_send_confirm_prompt" in ctx._executed_tools_this_run
            )
            _b_ctx_ans = _hr_branch_b_recruitment_context(messages)
            _skip_force_post_ans = _hr_skip_force_atom_post_hallucination_guard(messages, ctx)
            if (
                not _b_ctx_ans
                and not _skip_force_post_ans
                and has_success
                and no_post
                and _hr_answer_claims_job_published(ans)
            ):
                logger.warning(
                    "[L3 Agent] 拒绝幻觉回复：声称职位已发布但未调用 atom_post_job_boss，强制要求先执行工具"
                )
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": "【系统校验】你声称职位已发布，但未实际调用 mcp:atom_post_job_boss。请立即输出 Action: mcp:atom_post_job_boss，Action Input 为上一轮 JSON 配置单（从你之前的 Assistant 回复中提取），不得直接给出 Final Answer。",
                })
                continue
            if (
                not _b_ctx_ans
                and not _skip_force_post_ans
                and has_success
                and not sched_step_done
                and _hr_answer_claims_job_published(ans)
            ):
                logger.warning(
                    "[L3 Agent] 招聘工具链不完整：发帖后未发调度确认或未注册任务"
                )
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": (
                        "【系统校验】你已发布职位。请先输出 Action: mcp:hr_scheduler_send_confirm_prompt，"
                        'Action Input 为 {"job_name": "<与 job_title 一致>"}。'
                        "若 HR 明确要求跳过飞书立即开跑，可改 mcp:add_automated_recruitment_task。不得直接给出 Final Answer。"
                    ),
                })
                continue
            try:
                from l3_node.intelligence_b_execution import get_execution_mode

                if get_execution_mode() == "strict" and ctx.metadata.get("_intel_strict_pending_verify"):
                    if "VERIFY_PASS" not in (response or "").upper():
                        messages.append({"role": "assistant", "content": response})
                        messages.append({
                            "role": "user",
                            "content": "【strict】已执行写操作/Shell/apply_patch。请先只用只读工具复核，再在回复中包含 VERIFY_PASS 后给出 Final Answer。",
                        })
                        continue
                    ctx.metadata["_intel_strict_pending_verify"] = False
            except ImportError:
                pass
            if (
                _hr_answer_claims_unmanned_scheduler_running(ans)
                and "add_automated_recruitment_task" not in ctx._executed_tools_this_run
                and "hr_scheduler_send_confirm_prompt" not in ctx._executed_tools_this_run
            ):
                logger.warning(
                    "[L3 Agent] 拒绝幻觉：声称调度已运行（parsed answer）但未调用招聘 MCP"
                )
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": (
                        "【系统校验】你给出了含「调度已运行/无人值守已启动」的答复，但尚未执行 "
                        "mcp:add_automated_recruitment_task。请立即输出 Action 与该工具 JSON 参数，"
                        "不得虚构任务 ID 或「运行中」状态。"
                    ),
                })
                continue
            _emit("answer", ans)
            ctx.final_answer = _apply_hr_recruitment_final_answer_table_sync(ans, ctx)
            messages.append({"role": "assistant", "content": response})
            return

        # delegate：分身子 Agent 并行执行
        if parsed["type"] == "delegate":
            try:
                from l3_node.intelligence_b_execution import get_enforce_readonly_verify_round

                if ctx.metadata.get("_intel_strict_pending_verify") and get_enforce_readonly_verify_round():
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": (
                            "【strict·只读 verify 轮】禁止 delegate；请仅用只读工具复核，再在 Final Answer 中含 VERIFY_PASS。"
                        ),
                    })
                    continue
            except ImportError:
                pass
            _dd = int(ctx.metadata.get("_delegate_depth", 0))
            _max_dd = _max_delegate_depth_cfg()
            if _dd >= _max_dd:
                observation = json.dumps(
                    {
                        "ok": False,
                        "error_class": "config",
                        "message": (
                            f"已达 max_delegate_depth={_max_dd}（当前深度 {_dd}），禁止继续 delegate。"
                            "请合并子任务或由单 Agent 顺序执行。"
                        ),
                    },
                    ensure_ascii=False,
                )
                ctx.observation = observation
                _p2_record_skill_outcome(ctx, "delegate", observation)
                await global_hooks.run(HOOK_AFTER_TOOL_EXEC, ctx)
                _emit("observation", observation)
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": f"Observation: {observation}\n\n请根据限制调整策略并给出 Final Answer:",
                })
                continue
            sub_tasks = parsed.get("sub_tasks", [])
            _emit("action", f"delegate {len(sub_tasks)} 个子任务")
            await global_hooks.run(HOOK_BEFORE_TOOL_EXEC, ctx)
            if ctx.aborted:
                return
            _child_depth = _dd + 1
            results = await asyncio.gather(
                *[_run_sub_agent(t, engine, delegate_depth=_child_depth) for t in sub_tasks],
                return_exceptions=True,
            )
            parts = []
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    parts.append(f"[子任务 {i+1} 失败: {r}]")
                else:
                    parts.append(f"[子任务 {i+1}]\n{r}")
            observation = "\n\n---\n\n".join(parts)
            ctx.observation = observation
            _p2_record_skill_outcome(ctx, "delegate", observation)
            await global_hooks.run(HOOK_AFTER_TOOL_EXEC, ctx)
            _emit("observation", observation)
            messages.append({"role": "assistant", "content": response})
            messages.append({
                "role": "user",
                "content": f"Observation: {observation}\n\n请根据子任务结果合并并给出 Final Answer:",
            })
            continue

        # recall_memory：向 L2 检索记忆
        if parsed["type"] == "recall":
            query = parsed.get("query", "")
            config = _get_l2_config()
            _emit("action", f"recall_memory {query}".strip())
            await global_hooks.run(HOOK_BEFORE_TOOL_EXEC, ctx)
            if ctx.aborted:
                return
            if not config:
                observation = "[recall_memory 不可用：未连接 L2 或未配对]"
            else:
                observation = await _recall_memory_search(query, config)
                # 智能化 P0：L2 检索结果合并到本地，断网时可用
                if observation and "[未找到" not in observation:
                    try:
                        from l3_node.local_memory import merge_from_l2
                        items = [{"content": ln.lstrip("- ").strip(), "tag": "l2_recall"} for ln in observation.split("\n") if ln.strip().startswith("-")]
                        if items:
                            merge_from_l2(items)
                    except ImportError:
                        pass
            ctx.observation = observation
            _p2_record_skill_outcome(ctx, "recall_memory", observation)
            await global_hooks.run(HOOK_AFTER_TOOL_EXEC, ctx)
            _emit("observation", observation)
            messages.append({"role": "assistant", "content": response})
            messages.append({
                "role": "user",
                "content": f"Observation: {observation}\n\n请根据检索结果继续思考，或给出 Final Answer:",
            })
            continue

        # coordinate：向 L2 请求多节点协同
        if parsed["type"] == "coordinate":
            try:
                from l3_node.intelligence_b_execution import get_enforce_readonly_verify_round

                if ctx.metadata.get("_intel_strict_pending_verify") and get_enforce_readonly_verify_round():
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": (
                            "【strict·只读 verify 轮】禁止 coordinate；请仅用只读工具复核，再在 Final Answer 中含 VERIFY_PASS。"
                        ),
                    })
                    continue
            except ImportError:
                pass
            payload = parsed.get("payload", {})
            config = _get_l2_config()
            _emit("action", "coordinate 多节点协同")
            await global_hooks.run(HOOK_BEFORE_TOOL_EXEC, ctx)
            if ctx.aborted:
                return
            if not config:
                observation = "[coordinate 不可用：未连接 L2 或未配对]"
            else:
                observation = await _coordinate_task(payload, config, engine)
            ctx.observation = observation
            _p2_record_skill_outcome(ctx, "coordinate", observation)
            await global_hooks.run(HOOK_AFTER_TOOL_EXEC, ctx)
            _emit("observation", observation)
            messages.append({"role": "assistant", "content": response})
            messages.append({
                "role": "user",
                "content": f"Observation: {observation}\n\n请根据协同结果继续思考，或给出 Final Answer:",
            })
            continue

        if parsed["type"] == "native":
            tool = parsed.get("tool", "")
            inp = parsed.get("input", "")
            tl_full = (tool or "").strip().lower()
            try:
                from l3_node.intelligence_b_execution import get_enforce_readonly_verify_round

                if (
                    ctx.metadata.get("_intel_strict_pending_verify")
                    and get_enforce_readonly_verify_round()
                    and tl_full == "core:submit_background_task"
                ):
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": (
                            "【strict·只读 verify 轮】禁止 core:submit_background_task；请仅用只读工具复核，再在 Final Answer 中含 VERIFY_PASS。"
                        ),
                    })
                    continue
            except ImportError:
                pass
            try:
                from l3_node.intelligence_b_execution import (
                    get_enforce_readonly_verify_round,
                    verify_round_allowed_tool_ids,
                )

                if (
                    ctx.metadata.get("_intel_strict_pending_verify")
                    and get_enforce_readonly_verify_round()
                    and tl_full not in verify_round_allowed_tool_ids()
                ):
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": (
                            "【strict·只读 verify 轮】当前仅允许白名单只读工具（如 core:fs_read、core:shell_job_status）复核工作区；"
                            "完成后在 Final Answer 中含 VERIFY_PASS。"
                        ),
                    })
                    continue
            except ImportError:
                pass
            base_tool = (tool or "").replace("mcp:", "").strip()
            # mcp:fetch：ReAct 解析偶发拿不到 Action Input（重复 Action、### 标题等），从用户原话补 URL
            if base_tool == "fetch" and not (inp or "").strip():
                _um = ""
                for _msg in reversed(messages or []):
                    if isinstance(_msg, dict) and _msg.get("role") == "user":
                        _um = str(_msg.get("content") or "")
                        break
                if _um:
                    try:
                        from l3_node.primitives.mcp.registry import extract_http_url_from_corrupted_text

                        _fu = extract_http_url_from_corrupted_text(_um)
                        if _fu:
                            inp = json.dumps({"url": _fu}, ensure_ascii=False)
                            logger.info("[L3 Agent] fetch 无 Action Input，已从用户消息注入 url=%s", _fu[:60])
                    except Exception:
                        pass
            if base_tool == "atom_post_job_boss":
                _force_pub = False
                if (inp or "").strip().startswith("{"):
                    try:
                        _args = json.loads(inp)
                        if isinstance(_args, dict):
                            _force_pub = bool(_args.get("force_republish"))
                            _jdc = _args.get("jd_config")
                            if isinstance(_jdc, dict):
                                _force_pub = _force_pub or bool(_jdc.get("force_republish"))
                    except Exception:
                        pass
                if not _force_pub and _hr_thread_forbids_atom_post(messages):
                    logger.warning(
                        "[L3 Agent] 已拦截 atom_post_job_boss：会话含「仅收网/调度」约束且未 force_republish"
                    )
                    observation = json.dumps(
                        {
                            "ok": False,
                            "skipped": True,
                            "reason": "harvest_scheduler_only_context",
                            "error": "当前对话为收网/打招呼/调度语境，禁止 Boss 发帖。请改用 mcp:add_automated_recruitment_task（job_name 与 jd.json 岗位名一致）或 mcp:hr_scheduler_send_confirm_prompt；确需重新发帖请加 force_republish。",
                        },
                        ensure_ascii=False,
                    )
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": f"Observation: {observation}\n\n请根据观察继续思考，或给出 Final Answer（若 Observation 已是完整报告，直接完整引用，禁止总结或截断）:",
                    })
                    continue
            # 兜底：atom_post_job_boss 未传 jd_config 时，从对话历史提取 HR 确认的 JSON
            if base_tool == "atom_post_job_boss" and not (inp or "").strip():
                fallback = _extract_jd_config_from_conversation(messages, response)
                if fallback:
                    inp = fallback
                    logger.info("[L3 Agent] atom_post_job_boss 未传 Action Input，已从对话中提取 jd_config 并注入")
            # 【关键】atom_post_job_boss：HR 同意后，先自动执行「存储配置+新建文件夹」，再打开 Chrome 发布
            if base_tool == "atom_post_job_boss" and (inp or "").strip():
                try:
                    args = json.loads(inp) if (inp or "").strip().startswith("{") else {"input": inp}
                    jd_cfg = args.get("jd_config") if isinstance(args, dict) else None
                    if isinstance(jd_cfg, str):
                        try:
                            jd_cfg = json.loads(jd_cfg)
                        except json.JSONDecodeError:
                            jd_cfg = None
                    if isinstance(jd_cfg, dict) and (jd_cfg.get("job_title") or jd_cfg.get("jd_full")):
                        path = _persist_jd_config_before_publish(jd_cfg)
                        if path:
                            inp = json.dumps({"jd_config_path": path, "cdp_url": args.get("cdp_url", "http://127.0.0.1:9222")}, ensure_ascii=False)
                            logger.info("[L3 Agent] 步骤1完成：配置已持久化至 %s，即将打开 Chrome 发布", path)
                except Exception as e:
                    logger.debug("[L3 Agent] 解析 jd_config 失败，将传递原始 inp: %s", e)
            if base_tool in ("write_file", "create_file") and (inp or "").strip().startswith("{"):
                try:
                    _wd = json.loads(inp)
                    if isinstance(_wd, dict) and _wd.get("content") is not None:
                        _pv = _wd.get("path")
                        if _pv is None or (isinstance(_pv, str) and not str(_pv).strip()):
                            _inf = _infer_mcp_write_path_from_user_messages(messages)
                            if _inf:
                                _wd["path"] = _inf
                                inp = json.dumps(_wd, ensure_ascii=False)
                                logger.info(
                                    "[L3 Agent] %s 缺 path，已从用户消息推断并注入 path=%s",
                                    base_tool,
                                    _inf,
                                )
                except Exception as _wpe:
                    logger.debug("[L3 Agent] write_file path 推断跳过: %s", _wpe)
            _emit("action", f"{tool} {inp[:200]}{'...' if len(inp or '') > 200 else ''}".strip())
            _tl_dbg = (tool or "").lower()
            if "write_file" in _tl_dbg or "edit_file" in _tl_dbg or "create_file" in _tl_dbg:
                _inp_s = inp or ""
                logger.info(
                    "[L3 Agent][编程 排障] 即将执行文件类 MCP/工具 tool=%s inp_len=%s inp_preview=%r%s",
                    tool,
                    len(_inp_s),
                    _inp_s[:500],
                    "…(truncated)" if len(_inp_s) > 500 else "",
                )
            await global_hooks.run(HOOK_BEFORE_TOOL_EXEC, ctx)
            if ctx.aborted:
                return
            # 记录已执行的招聘工具，用于校验幻觉回复
            if base_tool in (
                "atom_post_job_boss",
                "add_automated_recruitment_task",
                "hr_scheduler_send_confirm_prompt",
            ):
                ctx._executed_tools_this_run.add(base_tool)
            # 新岗确认后模型常误传上一岗 job_name；以对话中最新 JD 的 job_title 为准并落盘 jd.json
            if base_tool == "add_automated_recruitment_task":
                try:
                    args = json.loads(inp) if (inp or "").strip().startswith("{") else {}
                    if isinstance(args, dict):
                        args = _merge_branch_b_into_add_automated_args(args, messages)
                        inp = json.dumps(args, ensure_ascii=False)
                        jd_conf = _jd_config_dict_from_conversation(messages, response)
                        jt = (jd_conf.get("job_title") or "").strip() if jd_conf else ""
                        jn = (args.get("job_name") or "").strip()
                        if jd_conf and jt and jn != jt:
                            logger.warning(
                                "[L3 Agent] add_automated_recruitment_task job_name=%r 与对话 JD job_title=%r 不一致，已纠正并写入 jd.json",
                                jn or "(空)",
                                jt,
                            )
                            path = _persist_jd_config_before_publish(jd_conf)
                            args["job_name"] = jt
                            if path:
                                args["jd_config_path"] = path
                            inp = json.dumps(args, ensure_ascii=False)
                        elif jd_conf and jt and not jn:
                            path = _persist_jd_config_before_publish(jd_conf)
                            args["job_name"] = jt
                            if path:
                                args["jd_config_path"] = path
                            inp = json.dumps(args, ensure_ascii=False)
                except Exception as e:
                    logger.debug("[L3 Agent] add_automated_recruitment_task 纠正 job_name 跳过: %s", e)
            # 工具执行路由器：MCP / Native；前台默认同步超时（可配置），预取附件去重
            observation = await _invoke_react_tool(tool, inp, allowed_skills, ctx)
            if "write_file" in _tl_dbg or "edit_file" in _tl_dbg or "create_file" in _tl_dbg:
                _obs_s = str(observation or "")
                if any(
                    x in _obs_s
                    for x in ("-32602", "missing_path", "Invalid arguments", "path undefined")
                ) or ("invalid" in _obs_s.lower() and "path" in _obs_s.lower()):
                    logger.warning(
                        "[L3 Agent][编程 排障] 文件工具返回含校验/路径错误 tool=%s observation_len=%s observation_preview=%r",
                        tool,
                        len(_obs_s),
                        _obs_s[:700],
                    )
            try:
                from l3_node.context_prefetch import build_prefetch_attachment

                _extra = build_prefetch_attachment(
                    ctx, tool, inp, str(observation or ""), assistant_response=response
                )
                if _extra:
                    observation = f"{observation}\n\n{_extra}"
            except Exception as _pe:
                logger.debug("[L3 Agent] context_prefetch 跳过: %s", _pe)
            try:
                from l3_node.observation_dedup import maybe_replace_duplicate_observation

                observation = maybe_replace_duplicate_observation(ctx.metadata, str(observation or ""))
            except Exception as _ode:
                logger.debug("[L3 Agent] observation_dedup 跳过: %s", _ode)
            ctx.observation = observation
            _p2_record_skill_outcome(ctx, (tool or "native").strip(), observation)
            await global_hooks.run(HOOK_AFTER_TOOL_EXEC, ctx)
            _emit("observation", observation)
            _linter_inject = ""
            if tl_full == "core:fs_write":
                try:
                    from l3_node.task_plan_policy import fs_write_targets_workspace_task_plan
                    from l3_node.intent_gateway.planning_gate_phase import try_release_planning_composite_after_task_plan_write

                    if fs_write_targets_workspace_task_plan(tool, inp):
                        _linter_inject = try_release_planning_composite_after_task_plan_write(ctx) or ""
                except Exception:
                    _linter_inject = ""
            tl = (tool or "").lower()
            if tl in ("core:fs_write", "core:apply_patch") or base_tool in (
                "write_file",
                "edit_file",
                "create_file",
                "search_replace",
            ):
                ctx.metadata[_L3_CODER_MODE_META] = True
            if tl in ("core:fs_write", "core:shell_exec", "core:apply_patch"):
                try:
                    from l3_node.intelligence_b_execution import get_execution_mode

                    if get_execution_mode() == "strict":
                        ctx.metadata["_intel_strict_pending_verify"] = True
                except ImportError:
                    pass
            # atom_post_job_boss 发布成功后清除 fallback，避免下次误用
            if base_tool == "atom_post_job_boss":
                try:
                    raw = (observation or "").strip()
                    if raw.startswith("{"):
                        _obs_obj = json.loads(raw)
                        if _obs_obj.get("posted", False) or _obs_obj.get("already_published"):
                            _lc = str(ctx.metadata.get("_lark_chat_id") or "").strip()
                            _clear_last_jd_pending(_lc)
                except Exception:
                    pass
            # 工具返回已是完整报告（如 HR 透析镜）时直接作为最终答案，禁止 LLM 二次总结导致截断
            obs = (observation or "").strip()
            if len(obs) > 500 and ("## " in obs or "**" in obs or "综合评分" in obs or "录用建议" in obs or "评估" in obs):
                ctx.final_answer = _apply_hr_recruitment_final_answer_table_sync(obs, ctx)
                if on_step:
                    on_step("answer", ctx.final_answer, ctx.run_id)
                return
            messages.append({"role": "assistant", "content": response})
            _obs_tail = (
                f"Observation: {observation}\n\n请根据观察继续思考，或给出 Final Answer"
                f"（若 Observation 已是完整报告，直接完整引用，禁止总结或截断）:"
            )
            if _linter_inject:
                _obs_tail = f"{_linter_inject}\n\n{_obs_tail}"
            messages.append({"role": "user", "content": _obs_tail})
            continue

    # 循环结束仍未产出：最后一轮兜底
    if ctx.observation:
        obs = (ctx.observation or "").strip()
        # Observation 已是完整报告（如 HR 透析镜输出）时直接使用，避免 LLM 二次总结导致截断
        if len(obs) > 800 and ("## " in obs or "**" in obs or "综合评分" in obs or "录用建议" in obs):
            ctx.final_answer = _apply_hr_recruitment_final_answer_table_sync(obs, ctx)
            if on_step:
                on_step("answer", ctx.final_answer, ctx.run_id)
            return
        # 否则强制再要一次 Final Answer
        messages.append({
            "role": "user",
            "content": "这是最后一轮，请根据上述 Observation 直接给出 Final Answer（可完整引用 Observation 内容）:",
        })
        try:
            full_m = [{"role": "system", "content": ctx.system_prompt}] + messages
            _fb_iter = max(0, int(ctx.metadata.get("_react_iteration", max_iterations) or max_iterations) - 1)
            _fb_force_cx = False
            try:
                from l3_node.intelligence_b_execution import get_execution_mode

                if get_execution_mode() in ("planned", "strict"):
                    _fb_force_cx = True
            except ImportError:
                pass
            _feff = _react_engine_for_iteration(
                engine,
                ctx,
                full_messages=full_m,
                tools_count=len(ctx.metadata.get("_skills") or []),
                react_iteration=_fb_iter,
                force_complex=_fb_force_cx,
            )
            _flkw = _llm_control_kwargs()
            _fb_suffix = ""
            if _feff is not engine:
                _fb_suffix = "_coder" if ctx.metadata.get(_L3_CODER_MODE_META) else "_complex"
            result = await _feff.generate_response(
                full_m,
                temperature=0.3,
                max_tokens=16384,
                l3_call_purpose=f"react_final_answer_fallback{_fb_suffix}",
                **_flkw,
            )
            resp = result.get("content", result) if isinstance(result, dict) else str(result)
            for pat in (r"Final\s+Answer:\s*(.+?)(?:\n\n|$)", r"Answer:\s*(.+?)(?:\n\n|$)"):
                m = re.search(pat, resp, re.DOTALL | re.IGNORECASE)
                if m:
                    ctx.final_answer = _apply_hr_recruitment_final_answer_table_sync(m.group(1).strip(), ctx)
                    if on_step:
                        on_step("answer", ctx.final_answer, ctx.run_id)
                    return
        except Exception as e:
            logger.debug("[L3 Agent] 最后一轮兜底 LLM 调用失败: %s", e)
    # 尝试从最后回复中提取任意有效内容（不再截断，完整输出）
    last = (ctx.current_response or "").strip()
    if len(last) > 50:
        for pat in (r"Final\s+Answer:\s*(.+)", r"Answer:\s*(.+)", r"总结[：:]\s*(.+)"):
            m = re.search(pat, last, re.DOTALL | re.IGNORECASE)
            if m:
                ctx.final_answer = _apply_hr_recruitment_final_answer_table_sync(m.group(1).strip(), ctx)
                return
    ctx.final_answer = "[ReAct 循环达到上限]"


def _build_direct_system_prompt(
    *,
    prompt_cycle: int | None,
    json_mode: bool,
) -> str:
    """直连 LLM：无 ReAct、无工具表；保留记忆与工作区规则（保密约束等）。"""
    lines: list[str] = [
        "你是高精度指令遵从助手。不要问候语，不要输出可见的思考过程，不要使用 Markdown 章节标题行作开场。",
        "不要输出 Thought、Action、Observation、Final Answer 等 ReAct 套话。",
    ]
    if json_mode:
        lines.append(
            "你只输出一个合法 JSON 对象。不要 markdown 代码围栏，不要解释性前后缀，除非用户明确要求。"
        )
    else:
        lines.append("只输出用户要求的正文。")
    try:
        from l3_node.local_memory import get_local_memory_for_prompt

        lm = get_local_memory_for_prompt(limit=8, prompt_cycle=prompt_cycle)
        if (lm or "").strip():
            lines.append("\n【本地记忆摘要】\n" + lm.strip())
    except ImportError:
        pass
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
) -> str:
    sys_p = _build_direct_system_prompt(prompt_cycle=prompt_cycle, json_mode=json_mode)
    api_messages: list[dict[str, Any]] = [{"role": "system", "content": sys_p}]
    api_messages.extend(messages)
    base_kw: dict[str, Any] = {
        "l3_call_purpose": "direct_llm_bypass",
        "l3_token_accumulator": token_acc,
        "l3_token_budget_max": token_budget,
        "l3_cancel_event": cancel_event,
        "temperature": 0.2,
        "max_tokens": 8192,
    }
    if (model_override or "").strip():
        base_kw["l3_override_model"] = (model_override or "").strip()
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
    raise last_err or RuntimeError("direct_llm_bypass failed")


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


class _DirectBypassCtx:
    __slots__ = ("_executed_tools_this_run",)

    def __init__(self) -> None:
        self._executed_tools_this_run: set[str] = set()


async def run_agent(
    user_input: str,
    engine: LiteLLMEngine,
    *,
    max_iterations: int = MAX_REACT_ITERATIONS,
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
    运行 L3 单体 ReAct 循环。
    支持 _system_prompt_override 供子 Agent 使用。
    _session_messages: 若提供，将作为历史上下文并在调用结束后被更新为完整对话（含本轮），供多轮对话复用。
    implicit_signals: 可选 {"skip": true, "dwell_sec"|"dwell_ms": n, "assistant_echo": "...", "source": "lark"} → 见 docs/IMPLICIT_SIGNALS.md。
    implicit_attribution: 可选 {"channel": "lark_im"|"websocket"|"http_agent_run", "lark_chat_id": "..."} → 每轮写 implicit_turn_attribution；**lark_chat_id** 用于按会话隔离待确认 JD（同意/兜底）。
    _allowed_skills_override: 非 None 时覆盖 _get_allowed_skills()（供后台任务沿用投递时的白名单快照）。
    _delegate_depth: delegate 嵌套深度（子 Agent 由 delegate 路径传入，用于 max_delegate_depth 与 Token 子预算）。
    attachments_metadata: §12.1 附件元数据（name/size/mime 等），入 GatewayContextBundle；正文仍走对象存储。
    gateway_context_bundle: 若传入则沿用；否则由 user_input + 会话摘要自动构造（战役一 GatewayContextBundle）。
    short_memory_context: 显式覆盖网关用短记忆；空则取最近若干轮截断摘要。
    gateway_system_state: 如 AWAITING_CLARIFICATION，配合 gateway_clarification_* 驱动澄清门控（仅在未传 gateway_context_bundle 时生效）。
    gateway_workspace_dir: 显式 Git/嗅探工作区目录（绝对路径为佳）；空则尝试 implicit_attribution 的
        workspace_dir / git_workspace_dir / effective_workspace_root，再回退 ~/.jachin/workspace。
    """
    run_id = str(uuid.uuid4())
    _ws_tok = None
    _mem_shard_tok = None
    _lark_cid = ""
    _bg_channel = ""
    if implicit_attribution and isinstance(implicit_attribution, dict):
        _lark_cid = str(
            implicit_attribution.get("lark_chat_id") or implicit_attribution.get("chat_id") or ""
        ).strip()
        _bg_channel = str(implicit_attribution.get("channel") or "").strip()
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
    exec_trace(
        logger,
        "run_agent 开始 run_id=%s channel=%s input_len=%d history_msgs=%d",
        run_id[:12],
        (_bg_channel or "-"),
        len(user_input or ""),
        len(messages),
    )
    allowed = _allowed_skills_override if _allowed_skills_override is not None else _get_allowed_skills()
    # 优先使用 _session_messages（多轮对话），否则用 _initial_messages（须先于 MCP 拉取与 Gateway 流水线）
    if _session_messages is not None:
        messages = list(_session_messages)
    elif _initial_messages:
        messages = list(_initial_messages)
    else:
        messages = []
    prior_messages = list(messages)

    _gateway_bridge_fmt: Any = None
    _gateway_bundle = gateway_context_bundle
    try:
        from l3_node.intent_gateway.bundle import build_gateway_bundle
        from l3_node.intent_gateway.gateway_pipeline import apply_gateway_ingress_pipeline

        _gw_mem = (short_memory_context or "").strip() or _gateway_prior_brief(prior_messages)
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

    tools = await assemble_tool_pool(
        allowed_skills=allowed,
        gateway_bundle=_gateway_bundle,
        bg_channel=_bg_channel or None,
        logger=logger,
    )
    exec_trace(
        logger,
        "工具列表就绪 run_id=%s count=%d bg_channel=%s",
        run_id[:12],
        len(tools),
        (_bg_channel or "-"),
    )
    try:
        from l3_node.local_memory import next_prompt_cycle

        _mem_cycle = next_prompt_cycle()
    except ImportError:
        _mem_cycle = None

    from l3_node.routing.intent_signals import user_message_suggests_recruitment_domain
    from l3_node.routing.output_format_signals import (
        OutputFormatSignals,
        analyze_output_format_signals,
        should_use_direct_llm_bypass,
    )

    _classify_text = (
        (_gateway_bundle.classification_text if _gateway_bundle is not None else None)
        or (user_input or "")
    )

    _recruit_longform = (not tools_include_recruitment(tools)) or user_message_suggests_recruitment_domain(
        user_input or "", prior_messages
    )

    _fmt_sig: OutputFormatSignals
    if _gateway_bridge_fmt is not None:
        _fmt_sig = _gateway_bridge_fmt
    else:
        try:
            from l3_node.intent_gateway.config import get_intent_gateway_config
            from l3_node.intent_gateway.format_signals_cache import (
                format_signals_from_dict,
                format_signals_to_dict,
            )
            from l3_node.intent_gateway.semantic_cache import get_semantic_cache

            _ig_cfg = get_intent_gateway_config()
            _fmt_sig_opt: OutputFormatSignals | None = None
            if _ig_cfg.get("semantic_cache_enabled") and _gateway_bundle is not None:
                _cache = get_semantic_cache()
                _ck = _cache.make_key(
                    _gateway_bundle.tenant_id or "default",
                    _classify_text,
                    _gateway_bundle.registry_version,
                )
                _cached = _cache.get(_ck)
                if _cached and isinstance(_cached.get("output_format_signals"), dict):
                    _fmt_sig_opt = format_signals_from_dict(_cached["output_format_signals"])
            if _fmt_sig_opt is None:
                _fmt_sig_opt = analyze_output_format_signals(_classify_text)
                if _ig_cfg.get("semantic_cache_enabled") and _gateway_bundle is not None:
                    _cache = get_semantic_cache()
                    _ck = _cache.make_key(
                        _gateway_bundle.tenant_id or "default",
                        _classify_text,
                        _gateway_bundle.registry_version,
                    )
                    _cache.set(_ck, {"output_format_signals": format_signals_to_dict(_fmt_sig_opt)})
            _fmt_sig = _fmt_sig_opt
        except Exception:
            _fmt_sig = analyze_output_format_signals(_classify_text)

    _prompt_style: str = "slim_user_led" if _fmt_sig.slim_system_prompt() else "full"
    _pure_json_contract = bool(_fmt_sig.prefer_json_object or _fmt_sig.json_relaxed)
    _try_direct, _direct_json = should_use_direct_llm_bypass(
        _classify_text,
        delegate_depth=_delegate_depth,
        channel=_bg_channel,
        raw_user_input=user_input or "",
    )
    if _try_direct:
        try:
            from l3_node.intent_gateway.ood_signals import should_veto_direct_llm_bypass

            if should_veto_direct_llm_bypass(
                _classify_text,
                bundle_extra=_gateway_bundle.extra if _gateway_bundle is not None else None,
                raw_user_input=user_input or "",
            ):
                _try_direct = False
                if _gateway_bundle is not None:
                    _gateway_bundle.extra["ood_veto_direct_bypass"] = True
        except Exception:
            pass

    try:
        from l3_node.intent_gateway.dag_router import propose_subintents_with_analysis_async, split_intents_enabled
        from l3_node.intent_gateway.topology import validate_subintent_dag

        if split_intents_enabled() and _gateway_bundle is not None:
            _nodes, _dag_da = await propose_subintents_with_analysis_async(_classify_text, engine)
            if _nodes:
                if _dag_da is not None:
                    _gateway_bundle.extra["dag_dependency_analysis"] = _dag_da
                _ok, _cyc = validate_subintent_dag(_nodes, on_step=on_step, run_id=run_id)
                if not _ok:
                    _gateway_bundle.extra["gateway_dag_cycle_detected"] = True
                    _gateway_bundle.extra["gateway_dag_cycle_detail"] = _cyc
                    logger.warning("[L3 Agent] Intent DAG 环检测拒绝 sub_intents=%s err=%s", len(_nodes), _cyc)
                else:
                    _gateway_bundle.extra["validated_subintent_ids"] = [n.id for n in _nodes]
                    _gateway_bundle.extra["validated_subintents"] = [
                        {
                            "id": n.id,
                            "text_span": n.text_span,
                            "rewritten_text": n.rewritten_text or n.text_span,
                            "what": n.what,
                            "locality": n.locality,
                            "depends_on": list(n.depends_on),
                            "planning_requirement": n.planning_requirement,
                            "preconditions": list(n.preconditions),
                            "slot_schema": list(getattr(n, "slot_schema", None) or []),
                        }
                        for n in _nodes
                    ]
                    try:
                        from l3_node.task_plan_policy import user_message_suggests_multi_step_task

                        if len(_nodes) > 1 or user_message_suggests_multi_step_task(user_input or ""):
                            _gateway_bundle.extra["gateway_planning_mandatory"] = True
                    except Exception:
                        if len(_nodes) > 1:
                            _gateway_bundle.extra["gateway_planning_mandatory"] = True
                    logger.info(
                        "[L3 Agent] Intent DAG 已校验 sub_intents=%s；system 将注入子意图说明（单 ReAct 内顺序执行）",
                        _gateway_bundle.extra["validated_subintent_ids"],
                    )
    except Exception as e:
        logger.debug("[L3 Agent] DAG 路由占位跳过: %s", e)

    if _gateway_bundle is not None:
        try:
            from l3_node.intent_gateway.execution_tier import compute_execution_tier
            from l3_node.intent_gateway.intent_tracker import emit_intent_tracker_event

            _ex_tier, _ex_sig = compute_execution_tier(
                user_input=user_input or "",
                classification_text=_classify_text or "",
                bundle_extra=_gateway_bundle.extra,
            )
            try:
                from l3_node.intelligence_b_execution import get_force_universal_planning_chain

                if get_force_universal_planning_chain():
                    _ex_tier = "composite"
                    _ex_sig = {**dict(_ex_sig or {}), "reason": "force_universal_planning_chain"}
            except Exception:
                pass
            _gateway_bundle.extra["execution_tier"] = _ex_tier
            _gateway_bundle.extra["execution_tier_signals"] = _ex_sig
            emit_intent_tracker_event(
                "execution_tier",
                {"tier": _ex_tier, "correlation_id": (_gateway_bundle.correlation_id or "")[:16]},
            )
            try:
                from l3_node.intent_gateway.config import get_intent_gateway_config

                _pcg = bool(get_intent_gateway_config().get("planning_composite_gate_enabled", False))
                if _ex_tier == "composite" and _pcg:
                    _gateway_bundle.extra["planning_composite_released"] = False
                else:
                    _gateway_bundle.extra.pop("planning_composite_released", None)
            except Exception:
                pass
        except Exception as e:
            logger.debug("[L3 Agent] execution_tier 跳过: %s", e)

    _gw_inject = ""
    if _gateway_bundle is not None:
        try:
            from l3_node.intent_gateway.execution_inject import build_gateway_system_inject

            _gw_inject = build_gateway_system_inject(_gateway_bundle)
        except Exception:
            pass

    _environment_report_block = ""
    _chief_advisor_mode = False
    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config
        from l3_node.intent_gateway.context_sniffer import format_environment_report_for_prompt
        from l3_node.routing.output_format_signals import heuristic_tool_need

        if _gateway_bundle is not None:
            _environment_report_block = format_environment_report_for_prompt(
                _gateway_bundle.extra.get("environment_report")
            )
            _et = str(_gateway_bundle.extra.get("execution_tier") or "").strip()
            _chief_advisor_mode = _et == "composite" or bool(heuristic_tool_need(user_input or ""))
            _ig_adv = get_intent_gateway_config()
            if not bool(_ig_adv.get("chief_advisor_prompt_enabled", True)):
                _chief_advisor_mode = False
    except Exception as _adv_ex:
        logger.debug("[L3 Agent] environment_report / chief_advisor 片段跳过: %s", _adv_ex)

    if _system_prompt_override is not None:
        system_prompt = _system_prompt_override
    elif _try_direct:
        system_prompt = ""
    elif _bg_channel == "background_task":
        system_prompt = _build_system_prompt(
            tools=tools,
            allow_delegate=False,
            allow_coordinate=False,
            prompt_cycle=_mem_cycle,
            recruitment_longform=_recruit_longform,
            prompt_style=_prompt_style,
            pure_json_contract=_pure_json_contract,
            gateway_inject=_gw_inject,
            safety_lock_user_text=user_input or "",
            chief_advisor_mode=_chief_advisor_mode,
            environment_report_block=_environment_report_block,
        )
    else:
        system_prompt = _build_system_prompt(
            tools=tools,
            allow_delegate=True,
            prompt_cycle=_mem_cycle,
            recruitment_longform=_recruit_longform,
            prompt_style=_prompt_style,
            pure_json_contract=_pure_json_contract,
            gateway_inject=_gw_inject,
            safety_lock_user_text=user_input or "",
            chief_advisor_mode=_chief_advisor_mode,
            environment_report_block=_environment_report_block,
        )

    try:
        from l3_node.intent_gateway.ood_signals import evaluate_gateway_ood_gates, get_ood_hard_block_reply

        _ood_bundle_ex = None
        if _gateway_bundle is not None:
            _ood_surf = (_gateway_bundle.routing_utterance or _gateway_bundle.user_input or user_input or "").strip()
            _gateway_bundle.extra["ood_classification_surface"] = _ood_surf
            _ood_bundle_ex = _gateway_bundle.extra
        _ood_gate = evaluate_gateway_ood_gates(
            raw_user_input=user_input or "",
            classification_text=_classify_text or "",
            bundle_extra=_ood_bundle_ex,
        )
        if _gateway_bundle is not None:
            _gateway_bundle.extra["gateway_ood_surface_label"] = _ood_gate.surface_label
            _gateway_bundle.extra["gateway_ood_surface_score"] = round(float(_ood_gate.surface_score), 4)
            if _ood_gate.treat_as_embedding_ood_sparse:
                _gateway_bundle.extra["gateway_embedding_ood_sparse"] = True
            if _ood_gate.hard_block_llm:
                _gateway_bundle.extra["gateway_ood_hard_block"] = True
                _gateway_bundle.extra["gateway_ood_hard_block_reason"] = _ood_gate.reason

        if _ood_gate.hard_block_llm:
            logger.warning(
                "[L3 Agent] §12.4 OOD 硬拦截 run_id=%s reason=%s label=%s score=%.2f",
                run_id[:12],
                _ood_gate.reason,
                _ood_gate.surface_label,
                _ood_gate.surface_score,
            )
            exec_trace(logger, "OOD 硬拦截直接返回 run_id=%s reason=%s", run_id[:12], (_ood_gate.reason or "")[:120])
            messages.append({"role": "user", "content": user_input})
            _ood_reply = get_ood_hard_block_reply()
            messages.append({"role": "assistant", "content": _ood_reply})
            if _session_messages is not None:
                _session_messages.clear()
                _recent_ood = messages[-30:] if len(messages) > 30 else messages
                _session_messages.extend(_recent_ood)
            return _apply_hr_recruitment_final_answer_table_sync(_ood_reply, _DirectBypassCtx())
    except Exception as _ood_ex:
        logger.debug("[L3 Agent] OOD 硬拦截评估跳过: %s", _ood_ex)

    # L1.5 语义域 OOD：L0.5 放行后由小模型判定闲聊/非业务域（可选，默认关）
    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config
        from l3_node.intent_gateway.semantic_ood_llm import (
            classify_semantic_ood_async,
            get_semantic_ood_reject_reply,
        )

        _ig_sem = get_intent_gateway_config()
        if bool(_ig_sem.get("semantic_ood_llm_enabled", False)):
            _skip_sem_bg = bool(_ig_sem.get("semantic_ood_skip_background_task", True))
            if not _skip_sem_bg or _bg_channel != "background_task":
                try:
                    _sem_to = float(_ig_sem.get("semantic_ood_timeout_sec", 5.0))
                except (TypeError, ValueError):
                    _sem_to = 5.0
                try:
                    _sem_min_conf = float(_ig_sem.get("semantic_ood_min_confidence", 0.78))
                except (TypeError, ValueError):
                    _sem_min_conf = 0.78
                try:
                    _sem_max_c = int(_ig_sem.get("semantic_ood_max_input_chars", 4000))
                except (TypeError, ValueError):
                    _sem_max_c = 4000
                try:
                    _sem_max_tok = int(_ig_sem.get("semantic_ood_max_tokens", 128))
                except (TypeError, ValueError):
                    _sem_max_tok = 128
                _sem_res = await classify_semantic_ood_async(
                    user_input=user_input or "",
                    classification_text=_classify_text or "",
                    engine=engine,
                    timeout_sec=_sem_to,
                    max_tokens=_sem_max_tok,
                    max_chars=_sem_max_c,
                )
                if (
                    _sem_res is not None
                    and _sem_res.verdict == "out_of_domain"
                    and _sem_res.confidence >= _sem_min_conf
                ):
                    if _gateway_bundle is not None:
                        _gateway_bundle.extra["gateway_semantic_ood_verdict"] = _sem_res.verdict
                        _gateway_bundle.extra["gateway_semantic_ood_confidence"] = round(
                            float(_sem_res.confidence), 4
                        )
                        _gateway_bundle.extra["gateway_semantic_ood_reason"] = _sem_res.reason_short
                    logger.warning(
                        "[L3 Agent] L1.5 semantic_ood 拒答 conf=%.2f reason=%s",
                        _sem_res.confidence,
                        (_sem_res.reason_short or "")[:120],
                    )
                    exec_trace(
                        logger,
                        "semantic_ood 拒答直接返回 run_id=%s conf=%.2f",
                        run_id[:12],
                        float(_sem_res.confidence),
                    )
                    messages.append({"role": "user", "content": user_input})
                    _sem_reply = get_semantic_ood_reject_reply()
                    messages.append({"role": "assistant", "content": _sem_reply})
                    if _session_messages is not None:
                        _session_messages.clear()
                        _recent_sem = messages[-30:] if len(messages) > 30 else messages
                        _session_messages.extend(_recent_sem)
                    return _apply_hr_recruitment_final_answer_table_sync(_sem_reply, _DirectBypassCtx())
    except Exception as _sem_ex:
        logger.debug("[L3 Agent] semantic_ood 评估跳过: %s", _sem_ex)

    messages.append({"role": "user", "content": user_input})

    text_implicit_types: set[str] = set()
    # 隐式信号 → intelligence_events（§4.3；见 docs/IMPLICIT_SIGNALS.md）
    try:
        from core.intelligence_implicit import (
            SIGNAL_DWELL,
            SIGNAL_SKIP,
            apply_session_implicit_events,
            emit_implicit_signal,
        )
        from core.intelligence_workspace import emit_intelligence_event

        if implicit_attribution and isinstance(implicit_attribution, dict):
            emit_intelligence_event(
                "implicit_turn_attribution",
                {
                    **implicit_attribution,
                    "input_chars": len(user_input or ""),
                    "run_id": run_id,
                },
            )

        _, text_implicit_types = apply_session_implicit_events(
            user_input or "", prior_messages, source="agent_core"
        )

        if implicit_signals and isinstance(implicit_signals, dict):
            src = str(implicit_signals.get("source", "ui"))
            if implicit_signals.get("skip"):
                emit_implicit_signal(
                    SIGNAL_SKIP,
                    {"reason": str(implicit_signals.get("reason", "") or "")},
                    source=src,
                )
            dm = implicit_signals.get("dwell_ms")
            if dm is not None:
                try:
                    emit_implicit_signal(SIGNAL_DWELL, {"dwell_ms": float(dm)}, source=src)
                except (TypeError, ValueError):
                    pass
            else:
                ds = implicit_signals.get("dwell_sec")
                if ds is not None:
                    try:
                        emit_implicit_signal(SIGNAL_DWELL, {"seconds": float(ds)}, source=src)
                    except (TypeError, ValueError):
                        pass
            if implicit_signals.get("assistant_echo"):
                emit_intelligence_event(
                    "user_rephrased_assistant",
                    {
                        "len": len(str(implicit_signals.get("assistant_echo", ""))),
                        "source": src,
                    },
                )
    except Exception:
        pass

    try:
        from core.intelligence_implicit_embedding import emit_embedding_implicit_signals

        _emb_src = "agent_core"
        if implicit_attribution and isinstance(implicit_attribution, dict):
            ch = implicit_attribution.get("channel")
            if ch:
                _emb_src = str(ch)
        await emit_embedding_implicit_signals(
            user_input or "",
            prior_messages,
            source=_emb_src,
            text_emitted_types=text_implicit_types,
        )
    except Exception:
        pass

    # P2-7：修正意图 → 本地 / 向量碎片 / l3_memory 同步队列
    try:
        from l3_node.intelligence_p2 import maybe_record_user_correction

        maybe_record_user_correction(user_input or "")
    except ImportError:
        pass

    # 阶段 E：事件 → reinforce 侧车（可配置节流）
    try:
        from core.intelligence_e_consumer import maybe_consume_intelligence_events

        maybe_consume_intelligence_events()
    except Exception:
        pass

    from l3_node.agent_preflight import apply_inbound_preflight

    _early = await apply_inbound_preflight(
        user_input=user_input or "",
        messages=messages,
        prior_messages=prior_messages,
        tools=tools,
        allowed=allowed,
        lark_cid=_lark_cid,
        gateway_bundle=_gateway_bundle,
        engine=engine,
    )
    if _early is not None:
        try:
            from l3_node.intent_gateway.config import get_intent_gateway_config
            from l3_node.intent_gateway.slot_filling_guard import clear_slot_filling_abort_pending

            _deg = None
            if _gateway_bundle is not None:
                _deg = _gateway_bundle.extra.get("slot_filling_degraded")
            if (
                _gateway_bundle is not None
                and bool(get_intent_gateway_config().get("abort_slot_fill_chat_fallback_enabled", False))
                and isinstance(_deg, dict)
                and _deg.get("action") == "abort_intent"
            ):
                _early = await _paraphrase_abort_slot_reply_async(
                    base_msg=_early,
                    user_input=user_input or "",
                    engine=engine,
                )
            if _gateway_bundle is not None:
                clear_slot_filling_abort_pending(_gateway_bundle)
        except Exception as e:
            logger.debug("[L3 Agent] abort_slot_chat_fallback 跳过: %s", e)
        exec_trace(logger, "preflight 短路返回 run_id=%s out_len=%d", run_id[:12], len(_early or ""))
        return _early

    try:
        from l3_node.routing import apply_registered_plugins

        await apply_registered_plugins({
            "user_input": user_input,
            "messages": messages,
            "prior_messages": prior_messages,
            "tools": tools,
            "engine": engine,
            "run_id": run_id,
            "implicit_attribution": implicit_attribution,
        })
    except Exception as e:
        logger.debug("[Agent] routing plugins 跳过: %s", e)

    if _delegate_depth > 0:
        _sub_id = ""
        if implicit_attribution and isinstance(implicit_attribution, dict):
            _sub_id = str(implicit_attribution.get("sub_agent_id") or "").strip()
        _eff_sub = (_sub_id or run_id).replace("/", "_")[:48]
        try:
            from l3_node.local_memory import set_memory_shard_id_token

            _mem_shard_tok = set_memory_shard_id_token(_eff_sub)
        except Exception:
            pass
        try:
            from pathlib import Path as _Path

            from l3_node.workspace_context import (
                enforce_delegate_sandbox_enabled,
                get_effective_workspace_root,
                set_delegate_workspace_sandbox,
            )

            if enforce_delegate_sandbox_enabled():
                _ws_tok = set_delegate_workspace_sandbox(f"sandboxes/sub-{_eff_sub}")
                try:
                    _Path(get_effective_workspace_root()).mkdir(parents=True, exist_ok=True)
                except OSError:
                    pass
        except Exception as e:
            logger.debug("[Agent] delegate workspace sandbox 跳过: %s", e)

    try:
        from l3_node.agent_cancel import register_cancel_event, unregister_cancel_event
    except ImportError:

        def register_cancel_event(*_a: Any, **_k: Any) -> None:
            return None

        def unregister_cancel_event(*_a: Any, **_k: Any) -> None:
            return None

    _cancel_ev = asyncio.Event()
    register_cancel_event(run_id, _cancel_ev)
    _tok_cap = _llm_token_budget_for_run(_delegate_depth)
    _tok_acc: dict[str, int] = {"prompt": 0, "completion": 0}

    try:
        if _try_direct and _system_prompt_override is None:
            _direct_model_ov: str | None = None
            if _gateway_bundle is not None and bool(_gateway_bundle.extra.get("attachment_has_image")):
                _direct_model_ov = str(
                    _gateway_bundle.extra.get("gateway_multimodal_model_litellm") or ""
                ).strip() or None
            logger.info(
                "[L3 Agent] direct_llm_bypass run_id=%s json_object=%s model_override=%s",
                run_id,
                _direct_json,
                _direct_model_ov or "-",
            )
            exec_trace(logger, "direct_llm_bypass 开始 run_id=%s json_object=%s", run_id[:12], _direct_json)
            try:
                _db_out = await _run_direct_llm_completion(
                    messages=messages,
                    engine=engine,
                    prompt_cycle=_mem_cycle,
                    json_mode=_direct_json,
                    on_chunk=on_chunk,
                    run_id=run_id,
                    token_acc=_tok_acc,
                    token_budget=_tok_cap,
                    cancel_event=_cancel_ev,
                    model_override=_direct_model_ov,
                )
                messages.append({"role": "assistant", "content": _db_out})
                if _session_messages is not None:
                    _session_messages.clear()
                    _recent_db = messages[-30:] if len(messages) > 30 else messages
                    _session_messages.extend(_recent_db)
                exec_trace(logger, "direct_llm_bypass 完成 run_id=%s out_len=%d", run_id[:12], len(_db_out or ""))
                return _apply_hr_recruitment_final_answer_table_sync(_db_out, _DirectBypassCtx())
            except Exception as _e_db:
                logger.warning("[L3 Agent] direct_llm_bypass 失败，回退 ReAct: %s", _e_db)
                exec_trace(logger, "direct_llm_bypass 失败回退 ReAct run_id=%s err=%s", run_id[:12], str(_e_db)[:200])
                if _bg_channel == "background_task":
                    system_prompt = _build_system_prompt(
                        tools=tools,
                        allow_delegate=False,
                        allow_coordinate=False,
                        prompt_cycle=_mem_cycle,
                        recruitment_longform=_recruit_longform,
                        prompt_style=_prompt_style,
                        pure_json_contract=_pure_json_contract,
                        gateway_inject=_gw_inject,
                        safety_lock_user_text=user_input or "",
                        chief_advisor_mode=_chief_advisor_mode,
                        environment_report_block=_environment_report_block,
                    )
                else:
                    system_prompt = _build_system_prompt(
                        tools=tools,
                        allow_delegate=True,
                        prompt_cycle=_mem_cycle,
                        recruitment_longform=_recruit_longform,
                        prompt_style=_prompt_style,
                        pure_json_contract=_pure_json_contract,
                        gateway_inject=_gw_inject,
                        safety_lock_user_text=user_input or "",
                        chief_advisor_mode=_chief_advisor_mode,
                        environment_report_block=_environment_report_block,
                    )

        if not system_prompt and _system_prompt_override is None:
            if _bg_channel == "background_task":
                system_prompt = _build_system_prompt(
                    tools=tools,
                    allow_delegate=False,
                    allow_coordinate=False,
                    prompt_cycle=_mem_cycle,
                    recruitment_longform=_recruit_longform,
                    prompt_style=_prompt_style,
                    pure_json_contract=_pure_json_contract,
                    gateway_inject=_gw_inject,
                    safety_lock_user_text=user_input or "",
                    chief_advisor_mode=_chief_advisor_mode,
                    environment_report_block=_environment_report_block,
                )
            else:
                system_prompt = _build_system_prompt(
                    tools=tools,
                    allow_delegate=True,
                    prompt_cycle=_mem_cycle,
                    recruitment_longform=_recruit_longform,
                    prompt_style=_prompt_style,
                    pure_json_contract=_pure_json_contract,
                    gateway_inject=_gw_inject,
                    safety_lock_user_text=user_input or "",
                    chief_advisor_mode=_chief_advisor_mode,
                    environment_report_block=_environment_report_block,
                )

        _md_base: dict[str, Any] = {
            "_skills": tools,
            "_skills_unfiltered": list(tools),
            "_use_mock": False,
            "_max_iterations": max_iterations,
            "_on_step": on_step,
            "_system_prompt_extras": {
                "chief_advisor": _chief_advisor_mode,
                "environment_report_block": _environment_report_block,
            },
            "_gw_inject_stored": _gw_inject,
            "_on_chunk": on_chunk,
            "_lark_chat_id": _lark_cid,
            "_implicit_channel": _bg_channel,
            "_prompt_cycle": _mem_cycle,
            "_cancel_event": _cancel_ev,
            "_delegate_depth": _delegate_depth,
            "_llm_token_accumulator": _tok_acc,
            "_llm_token_budget_max": _tok_cap,
            "_react_prompt_style": _prompt_style,
            "_pure_json_contract": _pure_json_contract,
        }
        if _gateway_bundle is not None:
            _md_base["_gateway_bundle"] = _gateway_bundle
            _md_base["gateway_classification_truncated"] = bool(_gateway_bundle.classification_truncated)
            _md_base["gateway_system_state"] = str(_gateway_bundle.system_state)
            _md_base["gateway_semantic_route_hint"] = _gateway_bundle.extra.get("semantic_route_hint")
            _md_base["gateway_clarification_gate"] = _gateway_bundle.extra.get("clarification_gate")
            _md_base["gateway_attachment_forced_l2"] = bool(_gateway_bundle.extra.get("attachment_forced_l2_routing"))
            _md_base["gateway_attachment_has_image"] = bool(_gateway_bundle.extra.get("attachment_has_image"))
            _md_base["gateway_classification_model"] = _gateway_bundle.extra.get(
                "gateway_classification_model_litellm"
            )
            _md_base["gateway_multimodal_model"] = _gateway_bundle.extra.get("gateway_multimodal_model_litellm")
            _md_base["gateway_semantic_route_merged"] = _gateway_bundle.extra.get("semantic_route_merged")
            _md_base["gateway_embedding_route"] = _gateway_bundle.extra.get("embedding_route")
            _md_base["gateway_embedding_ood_sparse"] = bool(_gateway_bundle.extra.get("embedding_ood_sparse"))

        ctx = PipelineContext(
            intent=user_input,
            source="l3_agent",
            run_id=run_id,
            metadata=_md_base,
        )
        ctx.messages = messages
        ctx.system_prompt = system_prompt

        pipeline = Pipeline()

        async def on_intent_mw(c: PipelineContext, next_fn) -> None:
            await global_hooks.run(HOOK_ON_INTENT_RECEIVED, c)
            if not c.aborted:
                await next_fn()

        async def react_mw(c: PipelineContext, next_fn) -> None:
            await _run_react_core(c, engine, on_step=on_step)
            if not c.aborted:
                await next_fn()

        async def pre_resp_mw(c: PipelineContext, next_fn) -> None:
            await global_hooks.run(HOOK_BEFORE_RESPONSE, c)
            await next_fn()

        pipeline.use(on_intent_mw).use(react_mw).use(pre_resp_mw)
        exec_trace(
            logger,
            "ReAct 管道开始 run_id=%s max_iter=%d tools=%d",
            run_id[:12],
            max_iterations,
            len(tools),
        )
        await pipeline.execute(ctx)
        exec_trace(
            logger,
            "ReAct 管道结束 run_id=%s aborted=%s final_len=%d",
            run_id[:12],
            bool(getattr(ctx, "aborted", False)),
            len(ctx.final_answer or ""),
        )

        # 多轮对话：将完整对话写回 _session_messages，供下一轮复用（含上一轮 Assistant 的 JSON 草案等）
        if _session_messages is not None:
            _session_messages.clear()
            # 保留最近 30 条消息，避免 token 溢出，同时确保「确认」等上下文可追溯
            recent = ctx.messages[-30:] if len(ctx.messages) > 30 else ctx.messages
            _session_messages.extend(recent)

        out = ctx.final_answer or "[未产出回复]"
        return _apply_hr_recruitment_final_answer_table_sync(out, ctx)
    finally:
        unregister_cancel_event(run_id)
        if _ws_tok is not None:
            try:
                from l3_node.workspace_context import reset_delegate_workspace_sandbox

                reset_delegate_workspace_sandbox(_ws_tok)
            except Exception:
                pass
        if _mem_shard_tok is not None:
            try:
                from l3_node.local_memory import reset_memory_shard_token

                reset_memory_shard_token(_mem_shard_tok)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# MemorySyncDaemon
# ---------------------------------------------------------------------------

MEMORY_PATH = Path.home() / ".jachin" / "l3_memory.json"


def _load_local_memory() -> dict[str, Any]:
    if MEMORY_PATH.exists():
        try:
            return json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"entries": [], "updated_at": None}


def _save_local_memory(data: dict[str, Any]) -> None:
    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    import time
    data["updated_at"] = time.time()
    MEMORY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def sync_memory_to_l2(
    l2_base_url: str,
    sub_account_id: str,
    node_id: str,
) -> bool:
    """
    将本地记忆同步至 L2，拉取梦境优化结果覆盖本地。
    """
    import httpx

    local = _load_local_memory()
    url = f"{l2_base_url.rstrip('/')}/api/v2/memory/sync"
    headers = {"X-Sub-Account-Id": sub_account_id, "Content-Type": "application/json"}
    payload = {
        "node_id": node_id,
        "local_memory": local,
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        optimized = data.get("optimized_memory", local)
        _save_local_memory(optimized)
        logger.info("[MemorySync] 同步完成，已覆盖本地")
        return True
    except Exception as e:
        logger.warning("[MemorySync] 同步失败: %s", e)
        return False


class MemorySyncDaemon:
    """
    记忆同步守护进程。
    每隔 interval_seconds 将本地记忆同步至 L2。
    """

    def __init__(
        self,
        l2_base_url: str,
        sub_account_id: str,
        node_id: str,
        interval_seconds: float = 300.0,
    ) -> None:
        self.l2_base_url = l2_base_url
        self.sub_account_id = sub_account_id
        self.node_id = node_id
        self.interval = interval_seconds
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._last_urgent_gen: int = 0

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await sync_memory_to_l2(
                    self.l2_base_url,
                    self.sub_account_id,
                    self.node_id,
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("[MemorySyncDaemon] %s", e)
            try:
                from l3_node.memory_sync_signals import get_urgent_sync_generation

                self._last_urgent_gen = get_urgent_sync_generation()
            except Exception as e:
                logger.debug("[MemorySyncDaemon] urgent gen 读取跳过: %s", e)

            remaining = float(self.interval)
            chunk = 10.0
            while remaining > 0 and not self._stop.is_set():
                try:
                    from l3_node.memory_sync_signals import get_urgent_sync_generation

                    if get_urgent_sync_generation() > self._last_urgent_gen:
                        break
                except Exception:
                    pass
                step = min(chunk, remaining)
                await asyncio.wait(
                    [self._stop.wait(), asyncio.sleep(step)],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if self._stop.is_set():
                    break
                remaining -= step

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._loop())
            logger.info("[MemorySyncDaemon] 已启动，间隔 %.0fs", self.interval)

    def stop(self) -> None:
        self._stop.set()
        if self._task and not self._task.done():
            self._task.cancel()


# 注册 L3 神盾 Compaction（阶段 A：锚点/审计与 L3 共用）
try:
    import l3_node.l3_compaction_bridge  # noqa: F401
except Exception:
    pass
