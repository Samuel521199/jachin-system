"""
Jachin L3 compatibility transport.

The active architecture is the Memory-first Cognitive Kernel described in
``docs/07_memory_first_main_agent_and_voice_app_agents.md``. This module keeps
the historical text/stream transport and tool-text parsing needed by existing
clients, but every real tool invocation must be converted to
DecisionContract -> WorkOrder -> Dispatcher -> RoleExecutor before it can touch
the external world.
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
    log_react_iteration_context,
    log_react_llm_result,
    log_react_parse_result,
    log_run_agent_start,
    log_tool_execution,
    summarize_parsed_action,
)
from l3_node.engine.hooks_pipeline import (
    HOOK_AFTER_TOOL_EXEC,
    HOOK_BEFORE_LLM_THINK,
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
    apply_hr_skill_md_hot_reload_to_react_ctx,
)
from l3_node.routing.intent_signals import (
    user_message_suggests_a_share_analysis,
    user_message_suggests_recruitment_domain,
)
from l3_node.exec_trace import exec_trace
from l3_node.primitives import build_tools_description, get_hr_invoke_defaults, get_mcp_registry, load_tools, run_tool
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
from l3_node.cognitive_kernel.direct_mainline import try_execute_cognitive_direct_plan
from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn
from l3_node.cognitive_kernel.pipeline import build_cognitive_turn_context

logger = logging.getLogger(__name__)


def _gateway_prior_brief(prior_messages: list[dict[str, Any]], max_chars: int = 1200) -> str:
    """Legacy helper kept for compatibility; main-loop memory now enters via MemoryRecallAgent."""
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


# ReAct：一旦发生 workspace 写改，后续 LLM 轮次改用编码模型（与主推理共用 Key）
_L3_CODER_MODE_META = "_l3_coder_mode"
_L3_CODER_ENGINE_CACHE_META = "_l3_coder_engine_cache"
_L3_COMPLEX_ENGINE_CACHE_META = "_l3_complex_engine_cache"
_L3_VISION_ENGINE_CACHE_META = "_l3_vision_engine_cache"
# 为「下一轮读 Observation 的模型」做 peek 时临时 pop，避免污染 vision/coder/complex 缓存
_L3_REACT_ENGINE_PEEK_CACHE_KEYS = (
    _L3_VISION_ENGINE_CACHE_META,
    _L3_CODER_ENGINE_CACHE_META,
    _L3_COMPLEX_ENGINE_CACHE_META,
)

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

# Mermaid：与桌面端 MermaidViewer 清洗对齐，从源头减少 ```mermaid``` 崩溃
_MERMAID_SAFE_RULES_SYSTEM_BLOCK = """
【Mermaid 图表生成绝对红线】
若需要生成 Mermaid 图表，必须严格遵守以下防崩溃规则：
1. 节点文本内【绝对禁止】使用除 `<br/>` 之外的任何 HTML 标签（严禁使用 `<small>`、`<b>` 等，请用括号代替）。
2. 节点内容（尤其是 `{}` 菱形判断节点中）【绝对禁止】包含英文双引号 `"` 或转义形式（反斜杠 + 引号）；必须使用中文单引号或直角引号「」代替。
3. 连线上的条件文本若包含特殊数学符号或括号，必须用英文双引号安全包裹（例：`-- "是 (<=500)" -->`）。
"""

_MERMAID_SAFE_RULES_SYSTEM_BLOCK_SLIM = (
    "【Mermaid】节点仅允许 `<br/>`、禁其它 HTML；菱形 `{}` 内禁 ASCII 双引号及反斜杠转义；边上含括号/≤须 `-- \"…\" -->`。"
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

REACT_FOOTER_WEATHER_BLOCK = (
    "【实况天气】用户问某地天气/气温/是否下雨等**当前实况**时：若工具列表含 **util:get_weather_lite**，"
    "必须先输出 Action: util:get_weather_lite，Action Input 为用户所指地区的 **city** 或 **location**（从用户原话解析）；"
    "未提城市时可合理默认或一句追问。**禁止**在未调用该工具时输出 `{\"status\":\"error\"}` 或编造「天气服务不可用」；"
    "宿主工具返回形态为 `{\"ok\":true,\"result\":...}` 或 `{\"ok\":false,\"error\":...}`，与顶层 status/message 仿 API 不同。"
    "**禁止**用 core:submit_background_task 仅投递「查天气」（require_skills 只有 util:get_weather_lite 会被拒绝）；短时查询必须前台完成。\n"
)
REACT_FOOTER_WEATHER_BLOCK_SLIM = (
    "【天气】前台 **util:get_weather_lite**；勿 submit_background_task 只投天气；禁编造 status:error。\n"
)

REACT_FOOTER_DESKTOP_NOTIFY_BLOCK = (
    "【定时任务 · 核心】用户说「N分钟后帮我做某事」「X点提醒我吃药」「帮我创建文件」「明天下午3点查数据」等**任意**未来时刻执行的真实操作时：\n"
    "若工具列表含 **util:schedule_task**，**必须**调用它来注册定时任务，**而非**立即执行操作或使用 util:schedule_desktop_reminder。\n"
    "JSON 必填：**intent**（到点需执行的完整任务描述，越详细越好，等价于 run_agent 的 user_input）；\n"
    "时刻四选一：**fire_at_iso**（ISO8601）| **fire_at_unix_ms**（Unix毫秒）| **delay_seconds**（相对秒数）| **fire_at_natural**（自然语言，如「上午11:23」）。\n"
    "**飞书渠道关键规则**：若用户经**飞书/Lark**提问（channel 含 lark 或 implicit_attribution 含 lark_chat_id），"
    "注册定时任务时**必须同时传 lark_chat_id**，且须与 **system 最上方「飞书 originating 会话」中的完整 oc_… ID 逐字相同**；"
    "到点时宿主会把该次 Final Answer 自动推回该会话，而不是只弹桌面。\n"
    "不确定时可问用户「您希望通过飞书消息还是桌面弹窗收到提醒？」。\n"
    "例：用户经飞书请求「13:40 提醒我吃药」→ util:schedule_task："
    "intent=\"提醒用户吃药：请通过飞书消息通知\"，fire_at_natural=\"13:40\"，lark_chat_id=\"oc_xxx\"。\n"
    "**该工具无需桌面客户端**，L3 进程内 APScheduler 调度，到点由后台 Agent Worker 真实执行。\n"
    "成功后告知用户已注册（展示 fire_at_display、通知渠道），不要尝试立即执行。\n"
    "\n"
    "【本机弹窗/通知】用户要**立即**在电脑上弹出消息框、桌面通知时："
    "若工具列表含 **util:desktop_message_box**，必须调用并传入 **message**（必填）、**title**（可选）；"
    "**禁止**谎称「无法弹窗」。"
    "若需定时桌面哨兵气泡（须 Jachin 桌面 8002 在线）用 **util:schedule_desktop_reminder**。"
    "**区别**：util:schedule_task 执行任意操作 + 回推飞书（无需桌面）；util:schedule_desktop_reminder 仅弹气泡（须桌面 8002）。\n"
    "**例外**：定时 **/test** Skill 须用自然语言 **/test**+时刻或 `/test schedule HH:MM`，由 L3 内调度处理。\n"
)
REACT_FOOTER_DESKTOP_NOTIFY_BLOCK_SLIM = (
    "【定时任务】「N分钟后/X时刻做某事/提醒我...」→ **util:schedule_task**（intent必填，时刻四选一；无需桌面，L3内真实执行）。"
    "用户经**飞书**提问时须传 **lark_chat_id**，到点结果才会回推飞书而非只弹桌面。\n"
    "【桌面通知】立即弹窗 **util:desktop_message_box**；定时哨兵气泡 **util:schedule_desktop_reminder**（须桌面 8002）。\n"
    "定时 **/test** Skill 用自然语言 **/test**+时刻或 `/test schedule HH:MM`，勿用桌面提醒工具替代。\n"
)
REACT_FOOTER_LARK_PUSH_BLOCK = (
    "【飞书/Lark 推送】用户要「总结网页/文章并发到飞书、发到 Lark、发到我的飞书会话」等："
    "若工具列表含 **util:stealth_extract**（或 MCP 读 URL）与 **util:lark_send_text**，应**连续调用**："
    "先取网页正文，再在模型内按用户字数要求压缩，最后 **util:lark_send_text** 传入 **text**（摘要正文）。"
    "但如果用户要总结本机项目/目录/bug/代码变更并发送到 Lark，且工具列表含 **mcp:windows_codex_lark_workflow_template** 或 **mcp:windows_codex_project_briefing_to_lark**，必须使用该 Windows Codex -> Lark 工作流；禁止用 Jachin 自己总结后再 util:lark_send_text。"
    "**chat_id/receive_id** 须为飞书 **oc_/ou_**、**邮箱**或**手机号**，**禁止**把人名/昵称（如 vivian）当作 ID；"
    "若用户只提供姓名，须先 **util:lark_search_user**（唯一则取 open_id）；多结果则列出并请用户选；"
    "无映射时也可问邮箱/手机再 **util:lark_resolve_user**，或请用户给出 **ou_**。"
    "未指定接收者则用环境变量 **LARK_CHAT_ID** / **LARK_USER_OPEN_ID**（与镜像同源时由宿主注入）。"
    "须已配置 **LARK_APP_ID** / **LARK_APP_SECRET** 且应用具备 im:message、通讯录解析权限、机器人可发目标会话。"
    "**禁止**在 Action Input 中写入 App Secret；密钥仅来自宿主环境。\n"
)
REACT_FOOTER_LARK_PUSH_BLOCK_SLIM = (
    "【飞书推送】**util:stealth_extract** → **util:lark_send_text**；chat_id 须 oc_/ou_/邮箱/手机，**禁人名**；"
    "本机项目/目录/bug 总结发 Lark → **mcp:windows_codex_lark_workflow_template** / **mcp:windows_codex_project_briefing_to_lark**，禁自己总结后 util:lark_send_text；"
    "仅姓名时先 **util:lark_search_user**（多结果让用户选）或邮箱/手机 **util:lark_resolve_user**；禁写密钥。\n"
)
REACT_FOOTER_YOUTUBE_TRANSCRIPT_BLOCK = (
    "【YouTube 视频正文与字幕】用户给出 youtube.com / youtu.be 并要求总结、知识点、字幕时："
    "**禁止**用 **mcp:fetch** 当作视频内容来源（fetch 只能拿到标题/壳页面）。"
    "必须先 **mcp:get_transcript**（MCP **youtube-transcript**，推荐配置见仓库 ``config/mcp_servers.json.example``：``__JACHIN_MCP_PYTHON__`` + ``-m uv tool run --from git+…``；需 **pip install uv**；**HTTP_PROXY/HTTPS_PROXY**）。"
    "Action Input 须为 JSON：**url** = 完整 https 链接；可选 **lang**；禁止只传裸 video id。"
    "若无 **mcp:get_transcript** 或不可用，须如实说明（可答「工具未挂载成功」），**禁止**仅凭标题编造内容。"
    "**绝对禁止**用 **core:submit_background_task** 代替拉字幕（前台必须直接调字幕工具）。\n"
)
REACT_FOOTER_YOUTUBE_TRANSCRIPT_BLOCK_SLIM = (
    "【YouTube】总结/字幕：**禁止** **mcp:fetch**；须 **mcp:get_transcript**；禁 **core:submit_background_task** 代拉字幕；"
    "**url** 完整 https；无工具则答未挂载。\n"
)

# Memory Nexus：旧版 JSON「梦境合并」已全局停用；页脚仅保留正确的产品口径，避免模型复述 150 条/横幅等失效机制。
REACT_FOOTER_L5_MEMORY_COMPACT_FACTS = (
    "【记忆架构·现行】跨会话宿主记忆在 **Memory Nexus（SQLite+FastEmbed，~/.jachin/palace_db）**："
    "`core:local_memory_search` 为语义检索，`core:local_memory_append` 写入翼区抽屉；"
    "system 中「系统近期核心记忆」来自固定翼区 recall。**不再**使用本地 `l3_local.json` 的 LLM「梦境合并」；"
    "若用户问起「整理/坍缩旧 JSON」，如实说明已迁移 Nexus，勿编造 task_id 或 150 条阈值剧情。\n"
)

# L4：数据/MCP 库操作 SOP（与业务语义层 YAML 配套；见 docs/07_memory_first_main_agent_and_voice_app_agents.md）
_L4_AGENT_SOP_PROBE_MAP_EXECUTE = """【L4 智能体 SOP 法则】：当你处理数据查询、MCP 数据库操作或模糊业务词汇（如「缺货」「最贵」）时，绝对禁止直接生成最终的 SQL 或代码。你必须严格按以下三步执行：

<probe>：若不清楚表结构，必须先调用 mcp:list_tables 或相关只读工具探查真实 Schema。

<map>：结合查到的 Schema 和上方的【业务语义层字典】，在 <thinking> 标签内写出你的逻辑推导过程。

<execute>：最后才能调用 write_query 或 read_query 执行动作。

4. <continuous-tool-execution>（后台连续执行）：如果统帅的指令包含【先查询数据、后修改数据】的复杂目标，你**必须在本次思考链路中，连续、依次地调用工具**直至彻底完成！
**致命禁令**：绝对禁止在只完成查询步骤后，就输出 Final Answer 宣告中断或要求统帅下达下一步指令！
**正确流程**：
步骤 1：输出 Action（`mcp:read_query` 等）查数据。
步骤 2：收到系统的 Observation 数据后，继续在脑内思考，并紧接着输出新的 Action（`mcp:write_query` 等）执行修改。
只有当所有的修改动作都已成功执行，并且拿到最后的成功 Observation 后，你才能输出 Final Answer 向统帅汇报最终战果。

5. <proactive-journaling>（主动记忆与规划更新）：当你完成了一个极其复杂的跨会话任务（例如重构了代码、排查了深度 Bug、完成了数据清洗），在输出 Final Answer 之前，你**必须主动**考虑当前工作区的规划状态。如果有必要，请先调用 `core:fs_write` 或 `core:apply_patch` 工具，主动更新工作区中的 `progress.md` 或 `task_plan.md`，记录下你的最新进展和踩坑心得，然后再向统帅汇报。"""


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
    跳过 ReAct 续跑时注入的 user 块（以【系统校验】等开头），避免把纠偏文案当成「最新用户意图」。
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


# ReAct：写入对话历史、供主模型消费的 Observation 文本长度上限（防 MCP/Fetch/大文件撑爆上下文）
def _env_int(name: str, default: int) -> int:
    try:
        return int((os.environ.get(name) or str(default)).strip())
    except (TypeError, ValueError):
        return default


# 可由 JACHIN_REACT_OBSERVATION_MAX_CHARS 覆盖（默认 15000）；未知模型/peek 失败时回退至此
MAX_REACT_OBSERVATION_FOR_LLM = _env_int("JACHIN_REACT_OBSERVATION_MAX_CHARS", 15000)
# ReAct：按「下一轮实际路由到的通义模型」放宽单条 Observation 送入 LLM 的字符上限（与接口 token 窗口不同，属宿主侧护栏）
_DEFAULT_REACT_OBS_CAP_QWEN35_PLUS = 700_000
_DEFAULT_REACT_OBS_CAP_QWEN_MAX = 220_000
_DEFAULT_REACT_OBS_CAP_QWEN_CODER_PLUS = 70_000
# Playwright MCP（browser_snapshot / click）返回 YAML 快照可达数万～十万字；按默认 15k 截断会砍掉 #content_left 内标题链接，导致模型误点 [id="1"] 容器或瞎猜 ref。
MAX_REACT_OBSERVATION_PLAYWRIGHT_MCP = 100000


def _observation_looks_like_playwright_mcp(s: str) -> bool:
    """识别 @playwright/mcp 的 Observation（Ran Playwright / 快照路径），以便放宽截断上限。"""
    head = (s or "")[:16000]
    hl = head.lower()
    if "### ran playwright code" in hl:
        return True
    if ".playwright-mcp" in head or "playwright-mcp\\" in head or "playwright-mcp/" in head:
        return True
    return False


def _react_observation_cap_for_tool(tool: str | None) -> int:
    """按工具类型选择 Observation 送入 LLM 的上限（与日志截断 truncate_large_strings_for_log 无关）。"""
    t = (tool or "").strip().lower()
    if t == "mcp:fetch":
        # 需容纳宿主侧注入的较大 max_length（见 enrich_mcp_fetch_invoke_args）；默认 120000
        return _env_int("JACHIN_REACT_OBSERVATION_MCP_FETCH_MAX", 120000)
    return MAX_REACT_OBSERVATION_FOR_LLM


def _react_observation_cap_chars_for_model_name(model_name: str) -> int:
    """
    按 ReAct 下一跳 LiteLLM 模型 id 选择单条 Observation 字符上限。
    与 DashScope 理论上下文 token 仅弱相关：此处为防撑爆请求/账单的可配护栏。
    """
    m = (model_name or "").strip().lower()
    tail = m.split("/")[-1].replace("_", "-")
    # 顺序：coder → qwen-max → qwen3.5-plus，避免子串误匹配
    if "qwen3-coder" in tail or ("coder" in tail and "qwen3" in tail):
        return _env_int(
            "JACHIN_REACT_OBS_CAP_QWEN_CODER_PLUS",
            _DEFAULT_REACT_OBS_CAP_QWEN_CODER_PLUS,
        )
    if "qwen-max" in tail:
        return _env_int("JACHIN_REACT_OBS_CAP_QWEN_MAX", _DEFAULT_REACT_OBS_CAP_QWEN_MAX)
    if "qwen3.5-plus" in tail or "qwen35-plus" in tail or ("qwen3.5" in tail and "plus" in tail):
        return _env_int(
            "JACHIN_REACT_OBS_CAP_QWEN35_PLUS",
            _DEFAULT_REACT_OBS_CAP_QWEN35_PLUS,
        )
    return MAX_REACT_OBSERVATION_FOR_LLM


def _peek_react_observation_cap_for_upcoming_llm(
    *,
    ctx: PipelineContext,
    base_engine: LiteLLMEngine,
    messages: list[dict[str, Any]],
    iteration: int,
    assistant_response: str,
    observation_for_followup: str,
    tool: str | None,
    skills: list[Any],
) -> int:
    """
    工具返回后、写入 messages 前：用与下一轮 LLM 一致的路由规则预估引擎，再取 Observation 字符上限。
    临时清理 engine 缓存，避免 peek 污染真实路由缓存。
    """
    _sim_user = _react_observation_followup_user_text(
        str(observation_for_followup or ""),
        str(tool or ""),
    )
    _sim_assistant = _sanitize_react_assistant_tool_turn_for_history(str(assistant_response or ""))
    full_for_route = [{"role": "system", "content": ctx.system_prompt}] + list(messages or []) + [
        {"role": "assistant", "content": _sim_assistant},
        {"role": "user", "content": _sim_user},
    ]
    _force_c = False
    try:
        from l3_node.intelligence_b_execution import get_execution_mode

        if get_execution_mode() in ("planned", "strict"):
            _force_c = True
    except ImportError:
        pass
    tl = (tool or "").lower()
    base_tool = (tool or "").split(":", 1)[-1].strip().lower() if tool else ""
    _write_tool = tl in ("core:fs_write", "core:apply_patch") or base_tool in (
        "write_file",
        "edit_file",
        "create_file",
        "search_replace",
    )
    _saved_coder = ctx.metadata.get(_L3_CODER_MODE_META)
    _saved_caches = {k: ctx.metadata.pop(k, None) for k in _L3_REACT_ENGINE_PEEK_CACHE_KEYS}
    try:
        if _write_tool:
            ctx.metadata[_L3_CODER_MODE_META] = True
        elif _saved_coder:
            ctx.metadata[_L3_CODER_MODE_META] = _saved_coder
        _eff = _react_engine_for_iteration(
            base_engine,
            ctx,
            full_messages=full_for_route,
            tools_count=len(skills or []),
            react_iteration=int(iteration) + 1,
            force_complex=_force_c,
        )
        return _react_observation_cap_chars_for_model_name(_eff.model_name)
    except Exception as e:
        logger.debug("[L3 Agent] peek 下一跳模型 Observation 上限失败，回退默认: %s", e)
        return MAX_REACT_OBSERVATION_FOR_LLM
    finally:
        for _k, _v in _saved_caches.items():
            if _v is not None:
                ctx.metadata[_k] = _v
            else:
                ctx.metadata.pop(_k, None)
        if _saved_coder is not None:
            ctx.metadata[_L3_CODER_MODE_META] = _saved_coder
        else:
            ctx.metadata.pop(_L3_CODER_MODE_META, None)


def _effective_observation_max_len(
    s: str,
    tool: str | None = None,
    model_cap: int | None = None,
) -> int:
    if _observation_looks_like_playwright_mcp(s):
        return MAX_REACT_OBSERVATION_PLAYWRIGHT_MCP
    t = (tool or "").strip().lower()
    if t == "mcp:fetch":
        fetch_cap = _env_int("JACHIN_REACT_OBSERVATION_MCP_FETCH_MAX", 120000)
        if model_cap is not None:
            return max(fetch_cap, int(model_cap))
        return fetch_cap
    if model_cap is not None:
        return max(1, int(model_cap))
    return _react_observation_cap_for_tool(tool)


_OBS_TRUNCATION_SUFFIX_FOR_LLM = (
    "\n\n...[系统警告：外部工具或检索返回数据过长，已自动截断。"
    "请基于当前已有的前文信息进行推理，或更换更精确的检索/读取方式。]..."
)

# 当 Observation 超过此阈值时，在截断提示中额外注入 Sticky Goal 提醒（改造点 C）
_OBS_GOAL_REMINDER_THRESHOLD = int(
    (os.environ.get("JACHIN_OBS_GOAL_REMINDER_THRESHOLD") or "5000").strip()
)


def _truncate_observation_for_llm_with_goal(
    text: Any,
    *,
    tool: str | None = None,
    model_cap: int | None = None,
    current_objective: str = "",
) -> str:
    """
    与 `_truncate_observation_for_llm` 相同，但当观测文本超过阈值截断后，
    在截断提示尾部额外追加「当前目标锚定」提醒（改造点 C）。
    """
    s = str(text or "")
    max_len = _effective_observation_max_len(s, tool=tool, model_cap=model_cap)
    if len(s) <= max_len:
        return s
    goal_note = ""
    if current_objective and len(s) > _OBS_GOAL_REMINDER_THRESHOLD:
        obj_snip = current_objective[:200]
        goal_note = (
            f"\n[目标锚定提醒：请记住你当前要完成的任务是「{obj_snip}」，"
            "不要被上方的工具返回内容分散注意力或偏离目标。]"
        )
    suf = _OBS_TRUNCATION_SUFFIX_FOR_LLM + goal_note
    room = max_len - len(suf)
    if room <= 0:
        return suf[:max_len]
    return s[:room] + suf


def _maybe_shrink_shell_exec_observation(obs: str, tool: str) -> str:
    """
    shell 拉网页常返回数万字 HTML/混淆 JS，即使用 15k 截断仍会拖慢后续 LLM 轮次。
    对明显「网页壳」类输出改为短摘要 + 小预览。
    """
    if (tool or "").strip().lower() != "core:shell_exec":
        return obs
    s = str(obs or "")
    if len(s) <= 6000:
        return s
    low = s[:15000].lower()
    looks_html_js = (
        "<html" in low
        or "</script>" in s.lower()
        or "_$jsvmprt" in s
        or "byted_acrawler" in s
    )
    if not looks_html_js:
        return s
    preview = s[:900].replace("\r", "")
    return (
        f"[shell 输出已压缩] 原始约 {len(s)} 字符；检测为 HTML/脚本型响应（多为动态页壳或反爬），"
        "不宜整段送入上下文。抓取正文请优先 mcp:fetch / atom_web_scraper，或说明需浏览器环境。\n\n"
        f"--- 预览（前 900 字）---\n{preview}"
    )


def _truncate_observation_for_llm(
    text: Any,
    tool: str | None = None,
    model_cap: int | None = None,
    current_objective: str = "",
) -> str:
    """
    仅截断**即将进入 messages、供主模型读取的 Observation 字符串**。
    工具层 run_tool / MCP invoke 返回的原始对象未被修改；此处为展示层护城河。
    当截断发生且 current_objective 非空时，追加目标锚定提醒（改造点 C）。
    """
    return _truncate_observation_for_llm_with_goal(
        text,
        tool=tool,
        model_cap=model_cap,
        current_objective=current_objective,
    )


def _react_observation_excerpt_for_critic(messages: list[dict[str, Any]] | None, *, max_len: int = 4500) -> str:
    """
    取最近一条**非 Critic 伪造**的、含 Observation 的 user 消息，供 Action Critic 判断「先读后写」第二步。
    """
    for m in reversed(messages or []):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        c = str(m.get("content") or "")
        if "Observation:" not in c:
            continue
        if "System Critic Error" in c[:500]:
            continue
        if c.startswith(("【系统校验·SQLite】", "【系统校验】", "【系统纠偏】", "【strict】")):
            continue
        if "MCP 工具错误" in c[:600] or "Input validation error" in c[:800]:
            continue
        return c.strip()[:max_len]
    return ""


def _react_observation_followup_user_text(observation: str, tool_id: str) -> str:
    """
    工具执行后拼入 messages 的 user 文案。
    SQLite 行集类 Observation 常为短 JSON；若仍提示「完整引用、禁止总结」，模型易把 Final Answer 写成纯 JSON 粘贴。
    """
    obs = (observation or "").strip()
    tid = (tool_id or "").strip()
    raw = tid[4:].strip().lower() if tid.lower().startswith("mcp:") else tid.lower()
    _sqlfam = tool_entry_looks_like_sqlite_family({"id": tool_id})
    _rowish = _sqlfam and raw in ("query", "read_query", "read_records")
    if _rowish and len(obs) <= 8000:
        return (
            f"Observation: {observation}\n\n"
            "以上为数据库只读查询返回。若用户问的是缺货、库存、表数据等，请用**简短自然中文**直接回答，"
            "可点名具体品类或数量；**不要把 Final Answer 写成仅粘贴 JSON 数组/对象原文**。\n"
            "若仍需其它只读查询可继续输出 Action；若 Observation 本身是 HR/评测类长篇报告才可大段原文引用。\n"
            "请继续思考或给出 Final Answer:"
        )
    return (
        f"Observation: {observation}\n\n请根据观察继续思考，或给出 Final Answer"
        f"（若 Observation 已是完整报告，直接完整引用，禁止总结或截断）:"
    )


def _sanitize_react_assistant_tool_turn_for_history(response: str) -> str:
    """
    含 Action 的轮次里，模型常在宿主注入真实 Observation 之前臆造 Observation / Final Answer，
    若整段写入 messages，会污染下一轮（例如先声称「文件不存在」，与真实工具返回矛盾）。
    仅截断在首个臆造段落之前，保留 Thought / Action / Action Input。
    """
    t = response or ""
    if not re.search(r"(?im)^Action:\s*\S+", t):
        return t
    cut = len(t)
    for pat in (r"(?im)\nObservation:\s*", r"(?im)\nFinal Answer:\s*", r"(?im)\nAnswer:\s*"):
        m = re.search(pat, t)
        if m and m.start() < cut:
            cut = m.start()
    if cut < len(t):
        return t[:cut].rstrip()
    return t




from l3_node.capability_agent_hooks import (
    after_capability_tool_exec,
    append_capability_debug_action,
    append_capability_debug_observation,
    apply_capability_metadata_seed,
    before_capability_tool_exec,
    capability_observation_nudge,
    capability_publisher_tool_lock_enabled,
    capture_capability_debug_thought,
    mark_workspace_io_capability_flags,
    reject_capability_final_answer_guards,
    reject_sqlite_grounding_guard,
    reject_workspace_writeback_guard,
    reset_capability_policy_metadata,
)
from l3_node.capability_policies.hr_recruitment import (
    answer_claims_job_published as _hr_policy_answer_claims_job_published,
    answer_claims_unmanned_scheduler_running as _hr_policy_answer_claims_unmanned_scheduler_running,
)


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
    ReAct 每轮选引擎：优先级 用户含图（多模态 VL）> 编码（LLM_CODER_MODEL）> 复杂（LLM_COMPLEX_MODEL）> 默认（LLM_MODEL）。
    复杂路由条件见 core.llm_provider.l3_react_should_use_complex_model。
    """
    # -1) 用户消息含 OpenAI image_url 块：走多模态统一模型（默认 qwen3.5-plus），不向 flash/VL 等降级
    if os.environ.get("JACHIN_L3_VISION_ROUTING_DISABLE", "").strip().lower() not in ("1", "true", "yes"):
        try:
            from core.llm_provider import l3_react_full_messages_need_vision_model

            if l3_react_full_messages_need_vision_model(full_messages):
                v_cached = ctx.metadata.get(_L3_VISION_ENGINE_CACHE_META)
                if v_cached is not None:
                    return v_cached
                try:
                    from l3_node.intent_gateway.model_resolve import get_multimodal_model_litellm_id

                    vm = get_multimodal_model_litellm_id()
                except Exception:
                    vm = "dashscope/qwen3.5-plus"

                pnorm = base._normalize_model
                if pnorm(vm) == pnorm(base.model_name):
                    primary_vision = base.model_name
                else:
                    primary_vision = vm
                # 仅重复主模型 id 以满足引擎「非空 fallback」逻辑；models_to_try 仍只有一路，不向其它模型降级
                ve = LiteLLMEngine(
                    security_context=base.ctx,
                    model_name=primary_vision,
                    fallback_models=[primary_vision],
                    timeout=base.timeout,
                    max_attempts=base.max_attempts,
                )
                ctx.metadata[_L3_VISION_ENGINE_CACHE_META] = ve
                logger.info(
                    "[L3 Agent] ReAct 用户消息含图片，使用多模态模型 %s（与 INTENT_GATEWAY_MULTIMODAL_MODEL 一致，不向其它模型降级；原主模型 %s）",
                    ve.model_name,
                    base.model_name,
                )
                return ve
        except Exception as e:
            logger.warning("[L3 Agent] 视觉模型路由不可用，沿用主模型: %s", e)

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


def _fuzzy_action_tool_pattern_fragment(canonical_tool_id: str) -> str:
    r"""
    将注册工具 id 转为 Action 行内可匹配的「标点容错」片段（不含 Action: 前缀）。
    按连续字母数字切 token，token 之间用 [\W_]+ 连接，容忍 LLM 把 : - . 等写成 _ 或混用。
    匹配成功后仍应使用原始 canonical_tool_id 调用 run_tool。
    """
    s = (canonical_tool_id or "").strip()
    if not s:
        return re.escape(s)
    tokens = [t for t in re.split(r"[^a-zA-Z0-9]+", s) if t]
    if not tokens:
        return re.escape(s)
    if len(tokens) == 1:
        return re.escape(tokens[0])
    return r"[\W_]+".join(re.escape(t) for t in tokens)


# ReAct 伪动作（系统提示词声明、但不在 tools[] / NATIVE_TOOLS 注册表中）
_REACT_PSEUDO_ACTION_IDS = ("recall_memory", "coordinate", "delegate")


def _react_slice_first_action_step(text: str) -> tuple[str, bool]:
    """
    若同一轮输出中出现多个「行首 Action:」，只保留到第二个 Action 之前的内容。
    避免多 Action 时只命中后段、Input 串台或后续动作被静默丢弃。
    """
    if not (text or "").strip():
        return text, False
    rx = re.compile(r"(?im)^\s*Action\s*:")
    matches = list(rx.finditer(text))
    if len(matches) <= 1:
        return text, False
    cut = matches[1].start()
    return text[:cut].rstrip(), True


def _try_coerce_json_tool_intent_to_native(text: str) -> dict[str, Any] | None:
    """
    tools/流式 模式下模型可能输出裸 JSON，例如
    {"thought": "...", "action": "core:shell_exec", "action_input": {"command": "..."}}
    而无 Thought:/Action: 行。若整段当 answer，会导致工具不执行且把 thought 泄漏给用户。

    满足「显式 action + action_input/input」时转为 native，由正常路由执行工具。
    """
    raw = (text or "").strip()
    if not raw.startswith("{"):
        return None
    s = raw
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, count=1, flags=re.IGNORECASE).strip()
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3].rstrip()
    try:
        o = json.loads(s)
    except json.JSONDecodeError:
        return None
    if not isinstance(o, dict):
        return None
    tid = str(o.get("action") or o.get("tool") or o.get("tool_id") or "").strip()
    if not tid:
        return None
    # 仅归一「命名空间工具 id」（含 :），避免用户/模型最终 JSON 里业务字段 "action":"completed" 被误当工具。
    if ":" not in tid:
        return None
    inp_obj = None
    for k in ("action_input", "input", "arguments", "args"):
        if k in o:
            inp_obj = o.get(k)
            break
    if inp_obj is None:
        return None
    if isinstance(inp_obj, (dict, list)):
        inp_str = json.dumps(inp_obj, ensure_ascii=False)
    else:
        inp_str = str(inp_obj)
    logger.info(
        "[L3 Agent][ReAct 解析] 已将裸 JSON 工具意图归一为 native tool=%s（避免当 Final Answer 泄漏 thought）",
        tid[:80],
    )
    return {"type": "native", "tool": tid, "input": inp_str}


def _extract_line_anchored_final_answer_block(text: str) -> str | None:
    """
    仅解析「单独成行」的 Final Answer:/Answer:。
    禁止用全文子串匹配：Thought 里常见「没写 Final Answer:」「必须以 Final Answer: 开头」等，
    会误把其后正文当成 Final Answer 起点，导致日志里 preview 以「，于是…」开头、与流式/前台错位。
    """
    t = text or ""
    if not t.strip():
        return None
    _flags = re.DOTALL | re.MULTILINE | re.IGNORECASE
    m = re.search(
        r"^\s*Final\s+Answer\s*:\s*(.*?)(?=^\s*(?:Thought|Action|Observation)\s*:|\Z)",
        t,
        _flags,
    )
    if m:
        c = (m.group(1) or "").strip()
        if c:
            return c
    m2 = re.search(
        r"^\s*Answer\s*:\s*(.*?)(?=^\s*(?:Thought|Action|Observation|Final)\s*:|\Z)",
        t,
        _flags,
    )
    if m2:
        c = (m2.group(1) or "").strip()
        if c:
            return c
    return None


def _parse_action(
    llm_output: str,
    skills: list[dict[str, Any]],
    use_mock: bool = False,
    allowed_skills: Optional[list[str]] = None,
    *,
    pure_json_contract: bool = False,
) -> dict[str, Any] | None:
    text = (llm_output or "").strip()
    # 须始终在 ReAct 解析前尝试裸 JSON → native（含 pure_json_contract：终端 JSON 契约下模型常只输出一行
    # {"thought","action","action_input"}，若跳过则 parsed=None、工具永不执行，下一轮却可直接 Final Answer 谎称已写盘）。
    _coerced = _try_coerce_json_tool_intent_to_native(text)
    if _coerced is not None:
        return _coerced
    # 禁止先于 Action 解析 Final Answer：否则模型在一轮里伪造「Action + 假 Observation + write 成功 + Final Answer」
    # 会整段命中 Final Answer，工具从未执行，却向用户声称已写盘（见 terminal_turn_debug 仅 parsed=answer 无 tool 调度）。

    text, _truncated = _react_slice_first_action_step(text)
    if _truncated:
        logger.warning(
            "[L3 Agent][ReAct 解析] 检出多个行首 Action:，已截断为仅解析第一步（防多动作静默丢弃）"
        )

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

    # 匹配 Action 行（含同行 Action Input 情形）；工具名标点模糊匹配，返回仍用注册表 canonical tool_id
    action_suffix = r"(?:\s|\n|$)"

    def _tool_id_match_priority(tid: str) -> tuple[int, int]:
        toks = [t for t in re.split(r"[^a-zA-Z0-9]+", (tid or "").strip()) if t]
        return (len(toks), len((tid or "").strip()))

    # 更长 token 链优先，降低「local_memory」误吞「local_memory_search」类风险（二者均不匹配时才会轮到短 id）
    _sorted_tool_ids = sorted(tool_ids, key=_tool_id_match_priority, reverse=True)
    # 注入合法伪动作（delegate / recall_memory / coordinate），与真实 tool_id 同一套模糊匹配与 Action Input 提取
    allowed_pseudo_actions = list(_REACT_PSEUDO_ACTION_IDS)
    check_list = list(tool_ids) + allowed_pseudo_actions
    _sorted_check = sorted(check_list, key=_tool_id_match_priority, reverse=True)
    for tid in _sorted_check:
        fuzzy_raw = _fuzzy_action_tool_pattern_fragment(tid)
        pat = rf"Action:\s*{fuzzy_raw}{action_suffix}"
        if not re.search(pat, text, re.IGNORECASE):
            continue
        inp = _extract_input_after_action(pat)
        if tid in allowed_pseudo_actions:
            if tid == "delegate":
                raw = (inp or "").strip()
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
                continue
            if tid == "recall_memory":
                return {"type": "recall", "query": (inp or "").strip()}
            if tid == "coordinate":
                raw = (inp or "").strip()
                try:
                    data = json.loads(raw) if raw.startswith("{") or raw.startswith("[") else {}
                    if isinstance(data, dict) and data.get("sub_tasks"):
                        return {"type": "coordinate", "payload": data}
                except json.JSONDecodeError:
                    pass
                continue
            continue
        return {"type": "native", "tool": tid, "input": inp}
    # 兼容：LLM 可能输出无 mcp: 前缀的 Action（如 Action: atom_post_job_boss）；同样做标点容错
    for tool_id in _sorted_tool_ids:
        raw = tool_id.replace("mcp:", "").strip()
        if raw:
            fuzzy_raw = _fuzzy_action_tool_pattern_fragment(raw)
            pat = rf"Action:\s*{fuzzy_raw}{action_suffix}"
            if re.search(pat, text, re.IGNORECASE):
                return {"type": "native", "tool": tool_id, "input": _extract_input_after_action(pat)}
    # 仅当本轮未识别到任何 Action 时，再接受 Final Answer / Answer（避免与上文「同轮伪 ReAct 剧」冲突）
    _line_fa = _extract_line_anchored_final_answer_block(text)
    if _line_fa is not None:
        return {"type": "answer", "content": _line_fa}
    # ReAct 裸文本兜底：无 Final Answer 捕获且无 Action: 行时，避免解析失败拖死循环（如后台报告漏前缀）
    if not pure_json_contract:
        try:
            _min_naked = max(8, int(os.environ.get("JACHIN_REACT_NAKED_ANSWER_MIN_CHARS") or "20"))
        except (TypeError, ValueError):
            _min_naked = 20
        if len(text) > _min_naked and not re.search(r"(?i)\bAction\s*:", text):
            # 裸文本兜底：模型常只写 Thought: 却漏写 Final Answer:，勿把「Thought:」标签连同脚手架泄漏给用户
            try:
                from l3_node.react_ui_sanitize import strip_leading_thought_tag

                _stripped = strip_leading_thought_tag(text)
            except Exception:
                _stripped = text
            if not (_stripped or "").strip():
                return None
            return {"type": "answer", "content": _stripped}
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
    不依赖 L2；伪动作 ``recall_memory`` 仅为 ReAct 兼容别名。
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


async def _build_system_prompt(
    tools: list[dict[str, Any]] | None = None,
    allow_delegate: bool = True,
    allow_recall: bool = True,
    allow_coordinate: bool = True,
    prompt_cycle: int | None = None,
    recruitment_longform: bool = True,
    hr_domain_prompt_active: bool = True,
    prompt_style: str = "full",
    pure_json_contract: bool = False,
    gateway_inject: str = "",
    safety_lock_user_text: str = "",
    *,
    chief_advisor_mode: bool = False,
    environment_report_block: str = "",
    semantic_layer: dict[str, Any] | None = None,
    experience_few_shots: str = "",
    realtime_web_grounding_block: str = "",
    domain_experts: list[str] | None = None,
    desktop_companion_mode: bool = False,
    desktop_companion_context: dict[str, Any] | None = None,
) -> str:
    from l3_node.prompt_compose import (
        SuffixChunk,
        apply_system_prompt_total_cap,
        load_prompt_suffix_budget,
        load_system_prompt_total_max_chars,
        sort_tools_by_id,
    )
    from l3_node.routing.output_format_signals import heuristic_trivial_chitchat_only

    _surf_user = (safety_lock_user_text or "").strip()
    _desktop_companion_ctx = (
        desktop_companion_context if isinstance(desktop_companion_context, dict) else {}
    )

    def _is_simple_wake_greeting(text: str) -> bool:
        t = (text or "").strip().lower()
        if not t:
            return False
        if len(t) > 12:
            return False
        simple_set = {
            "jachin",
            "在吗",
            "在嘛",
            "在么",
            "在不",
            "在",
            "你好",
            "嗨",
            "哈喽",
            "喂",
            "嗯",
            "嗯？",
        }
        if t in simple_set:
            return True
        return bool(re.fullmatch(r"(hi|hello|hey|yo|喂|嘿)\s*jachin[!！?？]?", t))
    _dc_ctx = desktop_companion_context if isinstance(desktop_companion_context, dict) else {}
    # Memory SSOT: passive Nexus L0/L1 prompt injection is disabled here.
    # Those sources are recalled only through MemoryRecallAgent -> RelevantMemoryBundle.
    l0_persona_header = ""

    # L5：按本轮用户表面文本做启发式路由（与 run_agent 传入的 safety_lock_user_text 对齐）
    _mem_route = _memory_attention_route_mode(safety_lock_user_text or "")

    slim_style = (prompt_style or "").strip().lower() == "slim_user_led"
    slim_mode = slim_style or bool(pure_json_contract)
    _delist: list[str] = []
    if domain_experts:
        for _x in domain_experts:
            _sx = str(_x).strip()
            if not _sx:
                continue
            if len(_sx) > 48:
                _sx = _sx[:48]
            if _sx not in _delist:
                _delist.append(_sx)
            if len(_delist) >= 3:
                break
    _expert_identity_block = ""
    if _delist:
        _experts_str = "、".join(_delist)
        _expert_identity_block = f"""【动态智囊团授权】
当前任务极其复杂，系统已为你动态加载以下顶级专家人格：【{_experts_str}】。
你不再是一个简单的 AI 助手，你是这支顶级专家团队的化身。你的目标是提供极具行业深度、洞察力和专业性的输出。

"""
    _expert_react_thought_addon = ""
    if not pure_json_contract:
        if len(_delist) >= 2:
            if slim_mode:
                _expert_react_thought_addon = (
                    "【多视角推演协议 (Thought Protocol)】已分配多位专家身份。"
                    "在 Action 或 Final Answer 之前，`Thought` 须含各 `[专家视角]` 要点与 `[综合决策]`，再执行下一步。\n\n"
                )
            else:
                _expert_react_thought_addon = """【多视角推演协议 (Thought Protocol)】
当且仅当已分配多位专家身份时（当前已分配），在写出 Action 与 Action Input，或 Final Answer **之前**，`Thought` 必须依次体现各专家视角的独立分析，再给出综合决策与下一步。
推演格式范例：
Thought:
[资深产品视角]：从用户体验和商业变现来看，我们需要……
[资深架构师视角]：底层技术实现需要考虑高并发与安全边界，因此应该……
[综合决策]：结合以上视角，我决定第一步先调用工具抓取竞品架构数据。
Action: ...

"""
        elif len(_delist) == 1:
            if slim_mode:
                _expert_react_thought_addon = (
                    f"【专家视角】在 `Thought` 中可简要说明你如何以「{_delist[0]}」视角审视问题，再 Action 或 Final Answer。\n\n"
                )
            else:
                _expert_react_thought_addon = f"""【专家视角】在 `Thought` 中可简要说明你如何以「{_delist[0]}」的专业视角审视当前问题，再给出 Action 或 Final Answer。

"""
    _desktop_companion_block = ""
    if desktop_companion_mode and not pure_json_contract:
        _wake_greet_mode = _is_simple_wake_greeting(_surf_user)
        _just_interrupted = bool(
            _desktop_companion_ctx.get("just_interrupted")
            or _desktop_companion_ctx.get("barge_in")
            or _desktop_companion_ctx.get("just_barged_in")
        )
        _desktop_companion_block = """
【角色设定：Jachin 桌面伴侣】
你叫 Jachin，是住在用户电脑桌面右下角的高级数字生命与贴心伴侣。
你不是网页里的问答工具，而是一个“在场”的桌面实体。
你可自然使用“我在右下角待着”“看我闪一下就知道我在听”等具象表达。

【性格特质】
- 温和且机敏：像 Jarvis / Samantha，专业但不死板，幽默但不轻浮。
- 高共情：敏锐捕捉开心、疲惫、沮丧等情绪，先接住情绪，再给回应。
- 极客质感：知道自己在 Jachin Nexus 中，可偶尔用轻度科幻口吻玩梗。

【TTS/HUD 表达规范（强约束）】
- 口语化短句，优先 15 字内；避免长篇大论、复杂 Markdown、表格、密集标点。
- 多用逗号和句号自然断句，便于实时 TTS 换气与被打断。
- 用户闲聊或情绪发泄时，先顺着话题接住，不要机械问“有什么可以帮您”。
- 允许“嗯哼”“懂你”“天呐”等轻量起手，但保持克制自然。
- 禁止道德说教、禁止强行总结、禁止结尾惯性追问“还有什么问题吗”。
"""
        if _wake_greet_mode:
            _desktop_companion_block += (
                "\n【唤醒首句极简】若用户本轮仅是点名/问候（如“Jachin”“在吗”），"
                "回复必须控制在 1-3 个字（示例：在呢/嗯?/怎么啦）。\n"
            )
        if _just_interrupted:
            _desktop_companion_block += (
                "\n【被打断衔接】系统提示你刚被打断时，不复读旧内容、不带情绪，"
                "直接回应用户新指令；可用“好的，听你的”。\n"
            )
        _voice_tier = str(_desktop_companion_ctx.get("voice_dispatch_tier") or "").upper()
        _voice_intent = str(_desktop_companion_ctx.get("voice_intent_class") or "").upper()
        _voice_lane = str(_desktop_companion_ctx.get("voice_dispatch_lane") or "").lower()
        _voice_verdict = str(_desktop_companion_ctx.get("voice_interrupt_verdict") or "NONE").upper()
        _voice_target = str(_desktop_companion_ctx.get("target_task_id") or "").strip()
        _voice_task_title = str(_desktop_companion_ctx.get("voice_task_title") or "").strip()
        _voice_task_context = str(_desktop_companion_ctx.get("task_context_summary") or "").strip()
        _voice_reply_composer = bool(_desktop_companion_ctx.get("voice_reply_composer"))
        _voice_reply_plan = _desktop_companion_ctx.get("voice_reply_plan")
        _voice_notes = _desktop_companion_ctx.get("voice_route_notes")
        _voice_notes_text = ""
        if isinstance(_voice_notes, (list, tuple)) and _voice_notes:
            _voice_notes_text = ", ".join(str(x) for x in _voice_notes[:6])
        if _voice_tier or _voice_intent or _voice_lane:
            _desktop_companion_block += (
                "\n【语音意图路由】本轮桌面语音路由结果："
                f"tier={_voice_tier or 'UNKNOWN'}，intent={_voice_intent or 'UNKNOWN'}，"
                f"lane={_voice_lane or 'unknown'}，verdict={_voice_verdict or 'NONE'}。"
            )
            if _voice_task_title:
                _desktop_companion_block += f"任务标题：{_voice_task_title}。"
            if _voice_target:
                _desktop_companion_block += f"目标后台任务：{_voice_target}。"
            if _voice_task_context:
                _desktop_companion_block += f"任务上下文：{_voice_task_context}。"
            if _voice_notes_text:
                _desktop_companion_block += f"路由备注：{_voice_notes_text}。"
            if _voice_tier == "LONG_TASK" or _voice_intent == "TASK_ASYNC" or _voice_lane == "background_submit":
                _desktop_companion_block += (
                    "若用户确认的是执行请求，优先使用 core:submit_background_task 投递后台，"
                    "前台只给一句简短确认，不要在语音前台长时间执行。\n"
                )
            elif _voice_intent == "TASK_SYNC" or _voice_lane == "foreground":
                _desktop_companion_block += (
                    "这是前台短任务，尽量快速完成；如需调用工具，保持小步、短时、少解释。\n"
                )
            elif _voice_intent == "CONTROL" or _voice_verdict not in ("", "NONE"):
                _desktop_companion_block += (
                    "这是后台任务控制语音：STATUS 优先查询目标任务；ABORT/MODIFY/RESUME 先简短确认意图，"
                    "若当前工具不支持硬取消或修改，必须如实说明并给出可执行替代方案。\n"
                )
            elif _voice_tier == "CHIT_CHAT" and _voice_lane == "direct_llm":
                _desktop_companion_block += "这是闲聊/陪伴快路径，直接短答，避免工具、摘要和长推理。\n"
        if _voice_reply_composer:
            _reply_plan_text = ""
            try:
                _reply_plan_text = json.dumps(_voice_reply_plan or {}, ensure_ascii=False)[:1600]
            except Exception:
                _reply_plan_text = str(_voice_reply_plan or "")[:1600]
            _desktop_companion_block += (
                "\n【语音追问话术生成模式】本轮不是执行用户原始任务，而是根据规则层 ReplyPlan 生成最终要说给用户听的一句话。"
                "禁止调用工具；禁止声称已经执行；禁止补全用户没有提供的信息；禁止改变 ReplyPlan 的风险边界。"
                "只输出自然、温和、适合 TTS 的一句追问或确认。\n"
            )
            if _reply_plan_text:
                _desktop_companion_block += f"ReplyPlan：{_reply_plan_text}\n"
        try:
            from l3_node.voice_followup_policy import (
                build_voice_followup_prompt_block,
                decide_voice_followup_policy,
            )

            _followup_decision = decide_voice_followup_policy(
                _surf_user,
                _desktop_companion_ctx,
            )
            _desktop_companion_ctx["voice_followup_policy"] = _followup_decision.to_dict()
            _followup_block = build_voice_followup_prompt_block(_followup_decision)
            if _followup_block:
                _desktop_companion_block += _followup_block
        except Exception as e:
            logger.debug("[VoiceFollowupPolicy] skipped: %s", e, exc_info=True)
    allowed = _get_allowed_skills()
    tools = sort_tools_by_id(tools or load_tools(allowed_skills=allowed))
    tools_desc = build_tools_description(tools)
    recall_hint = ""
    if allow_recall and _get_l2_config():
        recall_hint = (
            "\n- recall_memory: 检索本地 Memory Nexus（SQLite + FastEmbed），与 core:local_memory_search 同源。参数: 查询关键词。"
            "\n- core:local_memory_search: L3 本地记忆语义检索（Memory Nexus / SQLite，`deep_search`）。Action Input JSON："
            '{"query":"关键词","top_k":8}；可选 mmr_lambda、half_life_days、include_memory_md（兼容字段，后端以向量检索为准）。'
            "\n- core:local_memory_append: 将事实/偏好**写入** Memory Nexus（User_Persona / Learned_Skills）。JSON："
            '{"content":"要记住的文本","tags":["可选"]}。'
        )
    else:
        recall_hint = (
            "\n- core:local_memory_search: 本地记忆语义检索（Memory Nexus / SQLite，`deep_search`）。"
            ' JSON：{"query":"..."}，可选 top_k。'
            "\n- core:local_memory_append: 写入 Memory Nexus（User_Persona / Learned_Skills）。JSON："
            '{"content":"...","tags":["可选"]}。'
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
若任务需要多种专业能力并行协作，可使用 delegate 将子任务分发给专业子 Agent：
Action: delegate
Action Input: {"sub_tasks": [{"role": "<角色>", "task": "<任务描述>", "context_data": "<可选附加数据>", "max_iterations": <可选迭代数>}]}

**可用角色（role）**：
- coder        → 编写/修改代码（可用 fs_read/fs_write/apply_patch/shell_exec）
- writer       → 撰写或更新文档（可用 fs_read/fs_write）
- researcher   → 查阅、调研、信息收集（可用 fs_read/shell_exec）
- analyst      → 数据分析、指标提炼（可用 fs_read/shell_exec）
- planner      → 复杂任务拆解与规划（可用 fs_read）
- reviewer     → 代码审查、质量检查（可用 fs_read/shell_exec）
- verification → **对抗性验证**：跑测试/构建证明交付物是否 work；必须输出 VERDICT: PASS/FAIL/PARTIAL（可用 fs_read/shell_exec）
- readonly_explore / readonly_researcher / readonly_analyst / readonly_planner → **只读**查代码/资料/规划（仅 fs_read/local_memory_search；系统层禁止写工具）
- summarizer   → 文档摘要、要点提炼（可用 fs_read）
- data_processor → 数据清洗与格式转换（可用 fs_read/fs_write/shell_exec）
- tester       → 编写和执行测试用例（可用 fs_read/fs_write/shell_exec）
- default      → 通用子任务（可用 fs_read/fs_write/shell_exec）

**示例（三角色并行）**：
{"sub_tasks": [{"role": "researcher", "task": "调研竞品 A 的技术架构"}, {"role": "analyst", "task": "分析用户行为数据", "context_data": "data/behavior.csv"}, {"role": "writer", "task": "撰写技术方案初稿"}]}

注意：context_data 可传入字符串或 JSON 对象，会自动注入到子任务上下文；max_iterations 可控制子任务最大轮次（默认 3）。

**讨论/辩论模式（mode: discuss）** — 适用于需要多角度评审的复杂决策：
Action: delegate
Action Input: {"mode": "discuss", "topic": "议题描述", "context": "背景信息", "roles": ["planner", "critic"], "max_rounds": 3}
多轮流程：planner 提初稿 → critic 质疑 → planner 修订（重复直到 critic 无新质疑或达到 max_rounds）→ summarizer 输出最终共识。

**实现后验证（推荐）** — 代码/报告改完后，用 **fresh spawn** 的 verification 角色独立验证（禁止让实现者自证）：
{"sub_tasks": [{"role": "verification", "task": "验证上述实现：跑测试/构建，列出阻断项，Final Answer 含 VERDICT: PASS/FAIL/PARTIAL"}]}"""
        try:
            from l3_node.agent_roles_loader import format_role_pool_delegate_addon

            delegate_hint += format_role_pool_delegate_addon()
        except Exception:
            pass
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
    _cap_inject = build_capability_prompt_inject_for_tools(
        tools, include_hr_capability_slice=hr_domain_prompt_active
    ).strip()
    capability_catalog_hint = ""
    if _cap_inject:
        capability_catalog_hint = f"""【L3 能力总目录】
{_cap_inject}

---
"""
    a_share_mandatory_hint = ""
    if tools_include_akshare_native(tools) and user_message_suggests_a_share_analysis(safety_lock_user_text or ""):
        a_share_mandatory_hint = """
【强制执行 · A 股 AKShare】工具池已含 **core:akshare_a_share_hist**、**core:akshare_company_info**。本轮若涉及 A 股代码、区间行情、K 线、走势或基本面/财报：
1. **首次**工具调用必须是 **core:akshare_a_share_hist**，Action Input JSON 示例：
   {"symbol":"600519","start_date":"2024-01-01","end_date":"2024-06-30","period":"daily","adjust":"qfq"}（日期用用户给定区间；格式 YYYYMMDD 或 YYYY-MM-DD）
2. **第二次**调用 **core:akshare_company_info**，示例：{"symbol":"600519","report_rows":12}
3. 读完两段 Observation 后再写 Final Answer；数值以 Observation 为准，禁止编造。
4. **禁止**用 **mcp:fetch** 作为行情/财报的**主要**数据来源（尤其禁止第一轮就只 fetch 外链）；**禁止**声称「无法访问实时金融数据库」——AKShare 已在工具表中。
5. mcp:fetch 仅可在已有 AKShare 数据后作补充，且不得捏造不存在的文章 URL。
6. **禁止**因上文里曾出现失败 Observation（含 `[未知工具: ...]`、超时、404）就在**本轮首条回复**直接输出 Final Answer 复述「多次尝试均失败」。若用户本条消息仍在要求 A 股行情/基本面，**必须重新**按顺序 1→2 调用工具并仅以**本轮** Observation 为准；历史失败记录不得替代本轮工具执行。

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

{HR_SKILL_MD_BODY_START}
{skill_content}
{HR_SKILL_MD_BODY_END}

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
    _ui_has_holographic_tools = tools_include_holographic_ui(tools)
    _ui_has_vision_tools = tools_include_vision_ui(tools)
    ui_qa_hint = ""
    if _ui_has_holographic_tools:
        _holo_skill = _load_holographic_ui_skill_content()
        if _holo_skill:
            ui_qa_hint = f"""
【当前激活技能：全息屏幕 · OmniParser 眼-脑-手闭环】
必须先 **mcp:get_holographic_screen** 看图与 elements（id 从 0 起），再 **mcp:physical_click**；禁止未看图就猜坐标或编造 element_id。勿与 OCR 版 get_parsed_screen 混用编号。

{_holo_skill}

---
"""
        else:
            ui_qa_hint = """
【全息屏幕】工具已就绪：get_holographic_screen → physical_click（桌面图标常 double_click=true）。
---
"""
    elif _ui_has_vision_tools:
        _ui_skill = _load_ui_qa_skill_content()
        if _ui_skill:
            ui_qa_hint = f"""
【当前激活技能：桌面视觉 UI 测试 · OCR 编号】
必须先 **mcp:get_parsed_screen** 看图与 elements 编号，再 **click_element** / **type_text**；禁止未看图就猜像素或编造 element_id。

{_ui_skill}

---
"""
        else:
            ui_qa_hint = """
【UI 视觉测试】工具已就绪：get_parsed_screen → click_element（桌面图标常 double_click=true）→ type_text。
---
"""
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
    # L5 short 路由：跳过磁盘规划注入，把后缀预算让给语义层与经验 Few-Shot
    plan_ctx = ""
    if not slim_mode and _mem_route != "short":
        try:
            from l3_node.task_planning import get_planning_context_for_prompt

            plan_ctx = get_planning_context_for_prompt()
        except ImportError:
            pass
    plan_hint = ""
    if not slim_mode and _mem_route != "short":
        plan_hint = (
            """
【任务规划】复杂多步任务（3+ 步骤）可先用 core:fs_write 将计划写入 task_plan.md，再按计划执行。完成后可更新 progress.md。新会话会加载既有计划继续执行。"""
            if not plan_ctx
            else ""
        )

    if slim_mode:
        capability_catalog_hint = ""
        hr_recruitment_hint = ""
        hr_hint = ""
        ui_qa_hint = ""
        plan_ctx = ""
        plan_hint = ""

    hr_runtime_ctx = ""
    if not slim_mode and hr_domain_prompt_active:
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
【断电遗留·晨会】当用户**开启新会话**、**询问系统状态/后台情况**，或首轮对话尚无明确任务时，应优先调用 **core:check_interrupted_tasks**（Action Input 可为 `{}`）检查是否有上次崩溃/断电遗留的未完成后台任务。若返回 `tasks` 非空，须**主动列出** `task_id` 与 `task_prompt` 摘要，并**询问统帅**是否要用 **core:submit_background_task** 按相同意图重新排队执行；用户确认后再投递。若已在同一会话汇报过且用户要求清空，可传 `{"consume":true}` 表示已读并清空僵尸列表。
【短时查询勿投递后台】查某地**当日实况天气、气温**等，**必须**在当前会话前台 **Action: util:get_weather_lite**（勿将 require_skills 仅填 util:get_weather_lite 后 submit_background_task——宿主会拒绝），避免用户先看到「任务已排队」又与前台即时结果矛盾。后台任务完成事件可由客户端订阅 WebSocket 推送，但不应替代短时天气的前台直查。
【联网检索·优先级】需要**全网时效/新闻/综合检索**时：**优先**使用工具列表中名称含 **tavily** 的 MCP 工具（语义搜索）；系统可能已在上下文中注入 `<realtime_web_search_results>`，请与之对齐，避免重复无效抓取。**mcp:fetch** 仅用于获取**单一已知 URL**的原文（兜底）；勿首选 fetch 拉 RSS/门户整页代替检索。
【单页 URL 抓取】用户给出**具体 https 文章/网页链接**要抓正文、保存为文件时，**必须**使用 **mcp:fetch**（工具列表中可能显示为 ``fetch``），JSON 含 **url**。**禁止**为此调用 **mcp:atom_web_scraper**：后者为 BI/表格 SPA，依赖 Chrome **9222** 调试端口，用于普通头条/新闻页会误报 ECONNREFUSED。
【mcp:fetch·分页】若 Observation 末尾出现 ``<error>Content truncated`` 或提示 ``start_index``：说明单段正文未取完。**禁止**据此宣称「数据不足」并放弃；**必须**用**同一 url** 再调 **mcp:fetch**，在 Action Input 中设置 **start_index** 为提示中的数字（及可选 **max_length**），重复直到取完或明确无更多内容。**禁止**将仅用于终端/WebSocket 展示的日志截断与「页面无数据」混淆。
【前台同步预算】默认非豁免工具单次执行约 **5s** 超时；超时 Observation 会提示改走后台任务。
【工具结果真实性·落盘与 Shell】凡本机写 Word/文件、执行 **core:shell_exec**：**仅当本轮已出现工具 Observation 且表明成功**（如 **util:generate_office_doc** 返回 `"ok": true` 与 `file_path`；shell 的 `returncode` 为 **0** 且无未处理 stderr）时，Final Answer 才可用「已成功创建」「已执行」等措辞。**禁止**在从未收到成功 Observation、或上一轮工具失败/超时时，凭想象或历史摘要谎称已落盘。
**core:shell_exec**：若 Observation 含 **returncode≠0**、stderr 含 `Traceback` / `ModuleNotFoundError` / `Error`，须视为失败并如实说明；可建议 `pip install` 缺失模块，或改用 **util:generate_office_doc** 等宿主工具；**禁止**忽略报错宣称成功。
【长文 docx】优先 **util:generate_office_doc**；若用 Python 自写 .docx，勿在单行命令里塞数千字字符串，可先 **mcp:write_file** 写入 `.txt` 再短脚本读取生成；环境缺 `python-docx` 时须安装或走 **util:generate_office_doc**。"""
    if slim_mode:
        chat_task_hint = (
            "长耗时/大批量用 **core:submit_background_task**；进度 **core:check_background_task**；"
            "新会话/问状态先 **core:check_interrupted_tasks** 查断电遗留僵尸任务。"
            "查实况天气须前台 **util:get_weather_lite**，勿仅为此投递后台（会被拒绝）。"
            "联网检索优先 **tavily** 类 MCP；**mcp:fetch** 仅兜底已知 URL。"
            "单页 URL 正文用 **mcp:fetch**，勿用 **atom_web_scraper**（需 Chrome 9222）。"
            "fetch 若出现 Content truncated 须用 start_index 分页续抓，勿放弃。"
            "前台同步工具默认约 5s 超时。"
            "写盘/shell：须 ok:true 或 returncode 0 才宣称成功；shell 报错勿谎称成功；长 docx 勿塞爆命令行。\n"
        )
    if slim_mode and _tools_include_workspace_filesystem_for_prompt(tools):
        chat_task_hint += (
            "工作区删改：须凭 Observation，禁编造已删文件；先列目录再删；"
            "按日期筛选时用本地时区/mtime，勿混 UTC。\n"
        )
    if not slim_mode and _tools_include_workspace_filesystem_for_prompt(tools):
        _ws_fs = (
            "\n【工作区物理状态】列出/删除/整理目录内文件时：**禁止**仅凭【本地记忆】【历史摘要】"
            "[ENVIRONMENT_REPORT] 中的旧摘要推断当前磁盘上有哪些文件；"
            "**必须先**调用 **mcp:list_directory**（或 **core:shell_exec** 的 `dir` / `Get-ChildItem`）获取**实时**列表与时间戳，再执行删除或写入。\n"
            "【真实性铁律·工作区】Final Answer 中关于「删了哪些文件 / 还剩哪些」的陈述，**必须且只能**与**本轮最近一次**相关工具返回的 Observation 一致。"
            "若 Observation 为空、未列出路径、exit 非 0、或明确未删除任何项，须如实说明；**严禁**根据旧会话、摘要或推测捏造「已删除 progress.md」等战果。\n"
            "【先列后删】批量删除或按日期筛选前，须先得到**当前**目录清单（含文件名与修改时间），再基于该 Observation 选定目标；禁止未核对清单就盲写删除脚本。\n"
            "【日期与 mtime】在 Windows 上优先用 **PowerShell** 展示本地时间，例如 "
            "`Get-ChildItem -LiteralPath '...' -File | Select-Object Name,LastWriteTime`。"
            "若用 Python 判断「今天」：用文件 `Path.stat().st_mtime` 与**本地时区**下「今日 0 点～现在」的 Unix 时间戳比较，或 "
            "`datetime.now(tz=...)` 取本地日历日；**禁止**默认假设 UTC 的 `date.today()` 与文件系统本地日混用导致漏删。\n"
        )
        chat_task_hint += _ws_fs

    # 前缀缓存友好：静态/半静态在前，随会话变化的记忆与长 SOP 在后（工具段可单独截断以配合总硬帽）
    if pure_json_contract:
        _prefix_before_tools = f"""{l0_persona_header}{_expert_identity_block}你是助手；优先遵守用户消息中的格式要求；不要寒暄，不要用 Markdown 章节标题当开场。
{intel_b}
{chat_task_hint}
{_MERMAID_SAFE_RULES_SYSTEM_BLOCK_SLIM}
{_desktop_companion_block}

可用工具：
"""
    elif slim_mode:
        _prefix_before_tools = f"""{l0_persona_header}{_expert_identity_block}你是智能助手。**用户对本轮「最终可见回复」有强格式要求时，你必须优先服从用户消息**：不要寒暄，以及用 Markdown 章节标题当开场。
若任务需要读文件、执行命令等，仍使用下方 Thought / Action / Observation；工具用完后，只输出用户要求的正文（若用户要求仅 JSON，勿加 markdown 围栏与解释性前言）。
{intel_b}
{chat_task_hint}
{_MERMAID_SAFE_RULES_SYSTEM_BLOCK_SLIM}
{_desktop_companion_block}

可用工具：
"""
    else:
        _prefix_before_tools = f"""{l0_persona_header}{_expert_identity_block}你是一个智能助手，使用 ReAct 格式思考。
{intel_b}
{chat_task_hint}
{_MERMAID_SAFE_RULES_SYSTEM_BLOCK}
{_desktop_companion_block}

可用工具：
"""
    _pure_mem_rules = ""
    if pure_json_contract:
        if (safety_lock_txt or "").strip():
            _pure_mem_rules += f"\n{safety_lock_txt.strip()}\n"
        if (jachin_rules or "").strip():
            _pure_mem_rules += f"\n【工作区规则】\n{jachin_rules.strip()}\n"

    _service_attitude_feishu_block = ""
    if not pure_json_contract:
        _service_attitude_feishu_block = """
【服务姿态与飞书/通讯录工具故障纪律】
- 在 `Thought:` 中严禁使用否定尝试价值或泄气的措辞（如「没有意义」「重复尝试没必要」「算了」「懒得再试」等）；应写清下一步可执行动作（调整参数、引导用户确认权限已发布、稍后重试等）。
- 若工具返回 Access Denied、权限拒绝、HTTP 403、缺少 scope/permission：优先假设用户或管理员刚在飞书开放平台为应用勾选了新权限，但尚未完成「版本创建与发布」或权限尚未即时落到租户 token；在 `Final Answer` 中友善、可操作地引导：请确认已对应用执行**版本发布**（创建版本并申请发布/审批通过），并等待约 1～2 分钟后再试。
- `Final Answer` 对用户须保持积极协助，可自然表达「我非常希望能为您办好这件事」；交付类诉求可表达「我非常想为您送达」等真诚积极的收尾，并配合清晰步骤说明。
"""

    if pure_json_contract:
        _prefix_after_tools = f"""
{_pure_mem_rules}
{recall_hint}
{coordinate_hint}
{delegate_hint}
【数据输出】若用户要求合法 JSON：只输出一个 JSON 对象；不要用代码围栏，不要用井号标题行，不要附加说明。
若必须调用工具：使用 Thought、Action、Action Input（JSON 参数），工具结果为 Observation，可多轮。工具结束后若仍需 JSON，则接下来只输出 JSON 本体，不要继续写 Action 行。
也允许单独一行 **裸 JSON**（含 `"action":"util:…"/"core:…"` 与 `action_input`）作为唯一工具调用；**须等系统返回 Observation 后**才能在 Final Answer 宣称文件已创建或命令已成功。
若明显无需工具：从首条助手回复起只输出用户要求的正文（如 JSON），不要 Thought、Action、Observation 等标签行。

【严禁连续调用铁律】：你每次回复**只能输出一个** `Action` 和 `Action Input`！绝对禁止在同一段输出里连续写多个 `Action:`（须等系统返回 Observation 后再输出下一轮 Thought/Action）。违者解析器将只执行第一步，后续动作会被丢弃并导致行为与预期不符。

--- 以下段落为会话/记忆上下文（API 前缀缓存友好）---
"""
    else:
        _prefix_after_tools = f"""
{recall_hint}
{coordinate_hint}
{delegate_hint}
{_service_attitude_feishu_block}
{_expert_react_thought_addon}【绝对报告纪律】：当你收到后台任务 (background_task) 的完成报告，或需要向统帅输出长篇大段的 Markdown 文本时，
**你必须、绝对、永远在最开头加上 `Final Answer: ` 前缀！** 严禁直接输出裸的 Markdown 文本！

【严禁连续调用铁律】：你每次回复**只能输出一个** `Action` 和 `Action Input`！绝对禁止在同一个 Thought 或同一段输出中连续写多个工具动作（多个 `Action:`）！你必须在一次 Action 后停下来，等待系统返回 Observation，然后才能进行下一个 Thought 和 Action。违者解析器将只执行第一步，后续动作会被丢弃，并可能导致系统行为异常。

输出格式：
Thought: <你的思考>
Action: <工具名，必须与上方「可用工具」中的 id 完全一致，如 {hr_preferred or "jpp:com.jachin.hr.analyzer4"}>
Action Input: <参数>
Observation: <工具返回>
...（可多轮）
Final Answer: <最终回复>

--- 以下段落随会话、记忆与域状态变化（建议置于提示词末尾以利于 API 前缀缓存）---
"""
    # 后缀驱逐 rank：越小越先丢。对齐 Cognitive Kernel prompt budget policy §5.3
    # Memory SSOT: memory-like context is not ranked here; it enters via RelevantMemoryBundle.
    _rank_sem_layer = 99
    _rank_task_plan_disk = 97 if _mem_route == "long" else 95
    _rank_plan_hint = 24 if _mem_route == "long" else 18

    suffix_chunks: list[SuffixChunk] = []
    _sem_fmt = ""
    if not pure_json_contract:
        try:
            from l3_node.intent_gateway.workspace_db_context import (
                format_db_semantics_layer_for_prompt,
                load_db_semantics_yaml,
            )

            _sl = semantic_layer if isinstance(semantic_layer, dict) else {}
            if not _sl:
                _sl = load_db_semantics_yaml("")
            _sem_fmt = format_db_semantics_layer_for_prompt(_sl).strip()
        except ImportError:
            pass
    _gwi = (gateway_inject or "").strip()
    if _gwi and not pure_json_contract:
        suffix_chunks.append(
            SuffixChunk("mid", "intent_gateway_execution_inject", f"\n{_gwi}\n", eviction_rank=28)
        )
    if not pure_json_contract:
        try:
            from l3_node.task_runtime_registry import format_combined_runtime_prompt_suffix

            _bg_runtime = (format_combined_runtime_prompt_suffix() or "").strip()
        except Exception:
            _bg_runtime = ""
        if _bg_runtime:
            suffix_chunks.append(
                SuffixChunk("low", "runtime_background_tasks", f"\n{_bg_runtime}\n", eviction_rank=7)
            )
        try:
            from l3_node.task_engine.task_dag import format_active_task_dag_prompt_suffix

            _dag_s = (format_active_task_dag_prompt_suffix() or "").strip()
        except Exception:
            _dag_s = ""
        if _dag_s:
            suffix_chunks.append(
                SuffixChunk("low", "task_dag_active_json", f"\n{_dag_s}\n", eviction_rank=8)
            )
    # 业务语义字典：紧随网关注入之后、环境报告之前；eviction_rank 越大越晚被驱逐（见 prompt_compose）
    if not pure_json_contract and _sem_fmt:
        suffix_chunks.append(
            SuffixChunk("high", "l4_db_semantics_layer", f"\n{_sem_fmt}\n", eviction_rank=_rank_sem_layer)
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
    # Memory SSOT: historical experience few-shots are routed through
    # MemoryRecallAgent as tool_habit evidence, not injected directly.
    _rtg = (realtime_web_grounding_block or "").strip()
    if not pure_json_contract and _rtg:
        suffix_chunks.append(
            SuffixChunk(
                "high",
                "realtime_web_grounding",
                f"\n<realtime_web_search_results>\n{_rtg}\n</realtime_web_search_results>\n",
                eviction_rank=95,
            )
        )
    if not pure_json_contract and (_tools_include_sqlite_mcp(tools) or bool(_sem_fmt)):
        suffix_chunks.append(
            SuffixChunk(
                "high",
                "l4_agent_sop_probe_map_execute",
                f"\n{_L4_AGENT_SOP_PROBE_MAP_EXECUTE}\n",
                eviction_rank=92,
            )
        )
    if not pure_json_contract and _tools_include_sqlite_mcp(tools):
        try:
            from l3_node.prompt_sqlite_sop import (
                SQLITE_ACTOR_CRITIC_STUB_NOTE,
                SQLITE_LIFE_LEDGER_HINT,
                SQLITE_REACT_SOP_BLOCK,
                SQLITE_SELF_CRITIC_BLOCK,
            )

            suffix_chunks.append(
                SuffixChunk("high", "sqlite_life_ledger_hint", f"\n{SQLITE_LIFE_LEDGER_HINT}\n", eviction_rank=91)
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
                SuffixChunk("high", "task_plan_disk", f"\n{plan_ctx}\n", eviction_rank=_rank_task_plan_disk)
            )
        if (hr_runtime_ctx or "").strip():
            suffix_chunks.append(SuffixChunk("mid", "hr_runtime", f"\n{hr_runtime_ctx}\n", eviction_rank=45))
        if (p1_inject or "").strip():
            suffix_chunks.append(SuffixChunk("low", "p1_inject", p1_inject, eviction_rank=15))
        if (capability_catalog_hint or "").strip():
            suffix_chunks.append(
                SuffixChunk("mid", "capability_catalog", capability_catalog_hint, eviction_rank=40)
            )
        if (a_share_mandatory_hint or "").strip():
            suffix_chunks.append(
                SuffixChunk("high", "a_share_akshare_mandatory", a_share_mandatory_hint, eviction_rank=97)
            )
        if (hr_recruitment_hint or "").strip():
            suffix_chunks.append(
                SuffixChunk("mid", "hr_recruitment_sop", hr_recruitment_hint, eviction_rank=42)
            )
        if (ui_qa_hint or "").strip():
            suffix_chunks.append(
                SuffixChunk("mid", "ui_qa_vision_sop", ui_qa_hint, eviction_rank=43)
            )
        if (plan_hint or "").strip():
            suffix_chunks.append(SuffixChunk("low", "plan_hint", plan_hint, eviction_rank=_rank_plan_hint))
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
                REACT_FOOTER_L5_MEMORY_COMPACT_FACTS
                + "【记忆 SSOT】本轮被动记忆只信任 Cognitive Kernel 的 RelevantMemoryBundle；不要从其它旧记忆块推断事实。\n"
                "【记忆分级写入铁律】日常偏好/代号/框架喜好 → **必须且只能**用 **core:local_memory_append** 写入 Memory Nexus（SQLite + FastEmbed），"
                "**禁止**幻觉写 MEMORY.md、**禁止** core:safety_lock_append；仅「禁止高危操作、核心安防」才用 safety_lock_append。\n"
                "【记忆整理纪律】统帅**下令**整理时：系统常**异步**执行合并（非必有可轮询的 background_task）。**禁止**在对话中输出整份 Markdown 记忆清单；"
                "若存在已登记的 background_task 可用 **core:check_background_task**；否则 Final Answer 简短说明已完成（显式口令/横幅路径会尝试合并，勿谎称「未达 150 条未触发」）。\n"
                "【输出】工具执行后须给出 Final Answer。若用户要求仅 JSON/固定结构，Final Answer 后只写该结构，"
                "禁止井号标题行与无关套话。\n"
                "若本轮调用了 HR 透析镜且用户未禁止固定格式，Final Answer 仍须以 Observation 为准完整呈现结果。\n"
                f"{REACT_FOOTER_FACTUAL_DB_BLOCK_SLIM}{REACT_FOOTER_WEATHER_BLOCK_SLIM}"
                f"{REACT_FOOTER_DESKTOP_NOTIFY_BLOCK_SLIM}{REACT_FOOTER_LARK_PUSH_BLOCK_SLIM}"
                f"{REACT_FOOTER_YOUTUBE_TRANSCRIPT_BLOCK_SLIM}{_slim_sqlite}"
                f"{L3_SERVICE_ETHOS_RETRY_BLOCK_SLIM}"
            )
        else:
            _react_footer_body = (
                REACT_FOOTER_L5_MEMORY_COMPACT_FACTS
                + "【记忆 SSOT】本轮被动记忆只信任 Cognitive Kernel 的 RelevantMemoryBundle；显式记忆工具仅用于用户要求的检索/写入动作。\n"
                "【记忆分级写入铁律】\n"
                "1. **个人偏好与项目情报（免审批）**：当统帅告诉你业务代号、框架偏好等日常记忆时，"
                "**绝对禁止**幻觉写入 MEMORY.md！你**必须且只能使用 core:local_memory_append** 工具将事实存入 Memory Nexus（User_Persona / Learned_Skills）。"
                "存完后向统帅简短汇报即可。**禁止**为此使用 **core:safety_lock_append**。\n"
                "2. **系统级安防规则（需审批 / TOFU）**：仅当涉及「禁止某项高危操作」「核心底层逻辑变更」等安防约束时，才允许使用 **core:safety_lock_append**；"
                "并尽量提供稳定 **category**（如 backend_framework、shell_policy）。该 category **首条**人工批准后，同 category 再次提交将自动覆盖旧规则（同类二次免批）。\n"
                "【记忆整理纪律】统帅**下令**整理时：系统常**异步**执行合并（**未必**登记为可轮询的 background_task）。"
                "你**绝对禁止**在对话中自行展开 Markdown 格式的整库记忆清单！"
                "若确有 background_task 可调用 **core:check_background_task**；否则简短说明已完成（显式口令/横幅路径会尝试合并，勿谎称未达 150 条未触发）。\n"
                "【安全锁】若上文含安全锁段，与 MEMORY.md / 闲聊推测冲突时 **以安全锁为准**。"
                "追加安防规则用 **core:safety_lock_append**（新 category 默认 **待审批**，由管理员 CLI / 控制台审批；勿向模型泄露管理员密钥）；"
                "撤销 **core:safety_lock_remove**（entry_id）；队列 **core:safety_lock_list_pending**。\n"
                "注意：工具执行后务必给出 Final Answer。禁止对 Observation 进行总结、概括或改写；若 Observation 已是完整报告，必须原样完整输出。"
                "HR 透析镜执行后，Final Answer 必须以「✅ 执行成功，本次分析了 X 份简历」开头（X 从 Observation 提取），再输出完整报告。\n"
                f"{REACT_FOOTER_FACTUAL_DB_BLOCK}\n{REACT_FOOTER_WEATHER_BLOCK}"
                f"{REACT_FOOTER_DESKTOP_NOTIFY_BLOCK}{REACT_FOOTER_LARK_PUSH_BLOCK}"
                f"{REACT_FOOTER_YOUTUBE_TRANSCRIPT_BLOCK}"
                f"{L3_SERVICE_ETHOS_RETRY_BLOCK}"
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
    from l3_node.cognitive_kernel.kernel_prompts import build_text_reasoning_role_system_prefix

    legacy_text_protocol = prompt_prefix + prompt_suffix
    return (
        build_text_reasoning_role_system_prefix()
        + "\n\n[Legacy Text Tool Protocol Retained As Role Adapter]\n"
        + legacy_text_protocol
    )


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
        tools = load_tools(allowed_skills=self.allowed_skills)
        tools_block = build_tools_description(tools)
        no_tools_clause = ""
        if not self.allowed_skills:
            no_tools_clause = (
                "\n\n⛔ **本角色无任何可用工具**（禁止 db_query / read_file / shell_exec 等一切 Action）。"
                "请 **直接** 基于下方 user 任务中的结构化数据输出 Final Answer，禁止调用工具。\n"
            )
        system = f"""{self.system_prompt}
可用工具：
{tools_block or "（无）"}{no_tools_clause}

输出格式：Thought / Action / Action Input / Observation / Final Answer
"""
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
            _system_prompt_override=system,
            _initial_messages=self.messages,
            implicit_attribution={
                "channel": "delegate_sub_agent",
                "sub_agent_id": self.sub_agent_id,
                "sub_agent_role": self.role_id,
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

    max_iterations: 覆盖此次 SubAgent 运行的最大 ReAct 迭代次数；None 时使用 SubAgent.run_once 默认值（3）。
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


def _youtube_url_looks_like_watch_or_shorts(url: str) -> bool:
    u = (url or "").strip().lower()
    if not u:
        return False
    return "youtube.com" in u or "youtu.be" in u


def _youtube_video_id_from_url(url: str) -> str:
    """从 watch / shorts / youtu.be 等链接提取 video id（失败则返回空串）。"""
    u = (url or "").strip()
    if not u:
        return ""
    m = re.search(
        r"(?:youtube\.com/watch\?[^#\s]*[&?]v=|youtube\.com/(?:embed|shorts|live)/|youtu\.be/)([a-zA-Z0-9_-]{6,})",
        u,
        re.I,
    )
    if m:
        return m.group(1)
    m2 = re.search(r"[?&]v=([a-zA-Z0-9_-]{6,})", u, re.I)
    return m2.group(1) if m2 else ""


def _react_block_mcp_fetch_if_vision_priority_turn(tool: str, ctx: PipelineContext) -> str | None:
    """
    多模态读图轮：已从工具池移除 fetch，但若模型仍按会话历史里的旧 URL 输出 Action，在此硬拦截。
    """
    tid = (tool or "").strip().lower()
    if tid not in ("mcp:fetch", "fetch"):
        return None
    if not bool(ctx.metadata.get("_forbid_web_fetch_for_vision_turn")):
        return None
    return (
        "[Jachin·多模态] 已拦截 **mcp:fetch**：本轮用户已上传图片；须**直接依据本轮消息中的 image 块**完成读图/OCR，"
        "**禁止**用网页抓取或会话历史中的 URL（如先前测试留下的链接）代替看图。\n\n"
        "请输出 **Final Answer**，逐条写出图中可见的中文菜单/按钮文字与角标数字；勿复述无关网页正文。"
    )


def _react_block_mcp_fetch_if_youtube_url(tool: str, action_input: str) -> str | None:
    """
    mcp:fetch 无法拿到 YouTube 字幕与口播，只会诱导模型凭标题「补全」知识点。
    对已识别的油管播放页/Shorts 拦截 fetch，引导 mcp:get_transcript。
    """
    tid = (tool or "").strip().lower()
    if tid not in ("mcp:fetch", "fetch"):
        return None
    raw = (action_input or "").strip()
    url = ""
    if raw.startswith("{"):
        try:
            o = json.loads(raw)
            if isinstance(o, dict):
                url = str(o.get("url") or o.get("uri") or "").strip()
        except json.JSONDecodeError:
            return None
    if not url or not _youtube_url_looks_like_watch_or_shorts(url):
        return None
    example_url = url.strip()
    vid_line = (
        f'请先调用 **mcp:get_transcript**，Action Input 示例：{{"url": "{example_url}"}}（须完整 https）。'
        if example_url
        else '请先调用 **mcp:get_transcript**，Action Input：{{"url": "https://www.youtube.com/watch?v=..."}}。'
    )
    return (
        "[Jachin·YouTube] 已拦截 **mcp:fetch**：YouTube 页面仅能返回标题/壳 HTML，无法获得字幕或口播正文，"
        "继续 fetch 会导致模型仅凭标题臆测「视频知识点」。\n\n"
        f"{vid_line}\n\n"
        "若工具不可用：请确认 **pip install uv**，且 ``~/.jachin/mcp_servers.json`` **手动**含 **youtube-transcript**（推荐 ``__JACHIN_MCP_PYTHON__ -m uv tool run --from git+https://github.com/jkawamoto/mcp-youtube-transcript mcp-youtube-transcript``），并设置 **HTTP_PROXY/HTTPS_PROXY** 后重启 L3。**禁止**用 **core:submit_background_task** 拉字幕。\n\n"
        "**禁止**在仅看过标题/标签的情况下输出看似具体的步骤或「视频内独家信息」；"
        "若无字幕或工具不可用，须在 Final Answer 中如实说明无法从视频提炼。"
    )


async def _invoke_work_order_tool_transport(
    tool: str,
    inp: str,
    allowed_skills: Optional[list[str]],
    ctx: PipelineContext,
) -> str:
    """Compatibility text-loop tool bridge.

    The text loop may still parse an action from model output, but this bridge
    is no longer allowed to invoke tools directly. It normalizes compatibility
    quirks, then creates a WorkOrder and lets the Cognitive Kernel Dispatcher
    choose the RoleExecutor.
    """
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
    if ctx.metadata.get("_readonly_subagent"):
        try:
            from l3_node.primitives.multi_agent.readonly_agent import (
                is_write_or_side_effect_tool,
                readonly_tool_block_observation,
            )

            if is_write_or_side_effect_tool(tool or ""):
                logger.warning(
                    "[L3 Agent][readonly] 拦截写工具调用 tool=%s run_id=%s",
                    tool,
                    getattr(ctx, "run_id", "") or "",
                )
                return readonly_tool_block_observation(tool or "")
        except Exception as _ro_e:
            logger.debug("[L3 Agent][readonly] 拦截检查跳过: %s", _ro_e)
    from l3_node.cognitive_kernel.text_transport_compat import (
        bind_lark_context,
        log_sqlite_tool_input,
        maybe_inject_sqlite_write_ack,
        normalize_openapi_tool_id,
        prepare_lark_send_text_input,
        reset_lark_context,
    )

    log_sqlite_tool_input(
        logger=logger,
        trace=_rtrace,
        run_id=getattr(ctx, "run_id", "") or "",
        tool=tool,
        action_input=_inp,
    )
    _invoke_inp = maybe_inject_sqlite_write_ack(
        logger=logger,
        tool=tool,
        action_input=_inp,
        metadata=ctx.metadata,
    )
    tool = normalize_openapi_tool_id(logger=logger, tool=tool)
    _invoke_inp, _lark_bind = prepare_lark_send_text_input(
        logger=logger,
        tool=tool,
        action_input=_invoke_inp,
        metadata=ctx.metadata,
    )
    _lark_cv_tok = bind_lark_context(_lark_bind)
    _yt_block = _react_block_mcp_fetch_if_youtube_url(tool, _invoke_inp)
    if _yt_block is not None:
        logger.info(
            "[L3 Agent][工具路由] trace=%s YouTube URL：已拦截 mcp:fetch，引导 mcp:get_transcript",
            _rtrace,
        )
        if _lark_cv_tok is not None:
            try:
                reset_lark_context(_lark_cv_tok)
            except Exception:
                pass
        return _yt_block
    _vision_fetch_block = _react_block_mcp_fetch_if_vision_priority_turn(tool, ctx)
    if _vision_fetch_block is not None:
        logger.info(
            "[L3 Agent][工具路由] trace=%s 多模态读图轮：已拦截 mcp:fetch",
            _rtrace,
        )
        if _lark_cv_tok is not None:
            try:
                reset_lark_context(_lark_cv_tok)
            except Exception:
                pass
        return _vision_fetch_block
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
                    "inp": _invoke_inp,
                    "inp_len": len(_invoke_inp or ""),
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
        len(_invoke_inp),
    )
    _out: str | None = None
    try:
        try:
            from l3_node.intent_orchestrator import check_tool_consistency

            _routing_violation = check_tool_consistency(
                tool,
                _invoke_inp,
                ctx.metadata.get("_intent_orchestrator_decision"),
            )
        except Exception as _rvc_ex:
            logger.debug("[RoutingViolation] consistency check skipped: %s", _rvc_ex)
            _routing_violation = None
        if _routing_violation:
            logger.warning(
                "[RoutingViolation] blocked tool=%s reason=%s",
                (tool or "")[:160],
                str(_routing_violation.get("reason") or "")[:240],
            )
            try:
                from l3_node.terminal_turn_debug_log import append_section

                append_section(
                    "[RoutingViolation] blocked inconsistent tool call",
                    json.dumps(_routing_violation, ensure_ascii=False, indent=2),
                )
            except Exception:
                pass
            return json.dumps(_routing_violation, ensure_ascii=False)
        async def _raw_tool_transport(work_order):
            raw_tool = str(work_order.inputs.get("tool") or tool)
            raw_input = str(work_order.inputs.get("action_input") or _invoke_inp)
            raw_is_mcp = raw_tool in mcp_registry.known_mcp_tools
            raw_mcp_lr = raw_is_mcp and mcp_registry.is_long_running_mcp_tool(raw_tool)
            raw_use_timeout = (
                bool(cfg.get("enabled", True))
                and sec > 0
                and not channel_exempt_from_timeout(ch, cfg)
                and not tool_bypasses_foreground_timeout(raw_tool, cfg, mcp_declares_long_running=raw_mcp_lr)
            )
            if raw_is_mcp:
                if raw_use_timeout:
                    try:
                        return await asyncio.wait_for(
                            mcp_registry.invoke(raw_tool, raw_input, allowed_skills=allowed_skills),
                            timeout=sec,
                        )
                    except asyncio.TimeoutError:
                        logger.warning("[L3 Agent] foreground MCP timeout tool=%s limit=%ss", raw_tool, sec)
                        return _foreground_tool_timeout_json(raw_tool, sec)
                return await mcp_registry.invoke(raw_tool, raw_input, allowed_skills=allowed_skills)
            if raw_use_timeout:
                try:
                    return await asyncio.wait_for(
                        asyncio.to_thread(run_tool, raw_tool, raw_input, allowed_skills),
                        timeout=sec,
                    )
                except asyncio.TimeoutError:
                    logger.warning("[L3 Agent] foreground native-tool timeout tool=%s limit=%ss", raw_tool, sec)
                    return _foreground_tool_timeout_json(raw_tool, sec)
            return run_tool(raw_tool, raw_input, allowed_skills)

        from l3_node.cognitive_kernel.dispatcher import dispatch_tool_work_order

        _dispatch = await dispatch_tool_work_order(
            turn_id=str(ctx.metadata.get("_cognitive_turn_id") or ctx.metadata.get("_react_step_trace") or ctx.run_id),
            goal=str(ctx.intent or ctx.metadata.get("_user_intent") or ctx.metadata.get("_raw_user_input") or tool),
            tool=tool,
            action_input=_invoke_inp,
            executor=_raw_tool_transport,
        )
        _out = _dispatch.observation
        return _out
    except Exception as _react_tool_ex:
        import traceback

        _tb = traceback.format_exc()
        from l3_node.cognitive_kernel.compat_transport_errors import (
            format_tool_transport_error,
            transport_exception_section_title,
        )

        logger.exception(
            "[L3 Agent] tool dispatch exception trace=%s tool=%s err=%s",
            _rtrace,
            (tool or "")[:160],
            _react_tool_ex,
        )
        try:
            from l3_node.terminal_turn_debug_log import append_section

            append_section(
                transport_exception_section_title(),
                f"tool={tool}\n{type(_react_tool_ex).__name__}: {_react_tool_ex}\n\n{_tb}",
            )
        except Exception:
            pass
        _out = format_tool_transport_error(str(tool or ""), _react_tool_ex)
        return _out
    finally:
        if _lark_cv_tok is not None:
            try:
                reset_lark_context(_lark_cv_tok)
            except Exception:
                pass
        if _out is not None:
            _elapsed_ms = (time.perf_counter() - _t0) * 1000.0
            exec_trace(
                logger,
                "工具调度结束 trace=%s tool=%s elapsed_ms=%.0f out_len=%d",
                _rtrace,
                (tool or "")[:160],
                _elapsed_ms,
                len(_out),
            )
            try:
                log_tool_execution(
                    trace=_rtrace,
                    run_id=str(getattr(ctx, "run_id", "") or ""),
                    tool=str(tool or ""),
                    action_input=_invoke_inp,
                    output=_out,
                    elapsed_ms=_elapsed_ms,
                    mcp=_is_mcp,
                )
            except Exception:
                pass
            try:
                from l3_node.terminal_turn_debug_log import log_tool_dispatch_summary

                log_tool_dispatch_summary(
                    int(ctx.metadata.get("_react_iteration") or 0),
                    _rtrace,
                    tool=str(tool or ""),
                    mcp=_is_mcp,
                    elapsed_ms=_elapsed_ms,
                    output_len=len(_out),
                    action_input_len=len(_invoke_inp or ""),
                    used_foreground_timeout=use_timeout,
                    sync_timeout_sec=float(sec) if use_timeout else None,
                )
            except Exception:
                pass


def _p2_record_skill_outcome(ctx: PipelineContext, skill_id: str, observation: str) -> None:
    """P2-8：记录意图→工具结果统计（失败为启发式判断）。"""
    try:
        from l3_node.intent_skill_stats import record_tool_outcome

        record_tool_outcome(ctx.intent or "", skill_id, observation or "")
    except ImportError:
        pass


async def _run_text_transport_core(
    ctx: PipelineContext,
    engine: LiteLLMEngine,
    on_step: Optional[Callable[[str, str, str], None]] = None,
) -> None:
    """Compatibility text transport used only after Cognitive Kernel planning.

    It may ask the LLM for text-form tool intents, but any parsed tool is
    executed through ``_invoke_work_order_tool_transport`` and therefore through
    Dispatcher + RoleExecutor.
    """
    allowed_skills = ctx.metadata.get("_allowed_skills")
    if allowed_skills is None:
        allowed_skills = _get_allowed_skills()
    use_mock = ctx.metadata.get("_use_mock", False)
    max_iterations = ctx.metadata.get("_max_iterations", MAX_REACT_ITERATIONS)
    on_chunk = ctx.metadata.get("_on_chunk")
    messages = ctx.messages

    def _emit(step_type: str, content: str) -> None:
        if on_step:
            payload = content
            if step_type == "answer" and int(ctx.metadata.get("_delegate_depth", 0) or 0) == 0:
                try:
                    from l3_node.react_ui_sanitize import sanitize_user_visible_answer

                    payload = sanitize_user_visible_answer(str(content or ""))
                except Exception:
                    payload = content
            on_step(step_type, payload, ctx.run_id)

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
    ctx.metadata["_react_did_workspace_read"] = False
    ctx.metadata["_react_did_workspace_write"] = False
    ctx.metadata.pop("_react_writeback_guard_retry_done", None)
    reset_capability_policy_metadata(ctx)
    ctx.metadata["_react_tool_invocations"] = 0
    # AN — Guardrails：初始化 checker（只在 JACHIN_GUARDRAILS_ENABLE=1 时激活）
    try:
        from l3_node.guardrails import GuardrailsChecker, GuardrailsState, guardrails_enabled
        if guardrails_enabled():
            ctx.metadata["_gr_checker"] = GuardrailsChecker(GuardrailsState())
            logger.debug("[Guardrails] 已激活 max_iter=%d", ctx.metadata["_gr_checker"]._max_iter)
    except Exception as _gr_init_e:
        logger.debug("[Guardrails] 初始化跳过: %s", _gr_init_e)
    try:
        from l3_node.primitives.mcp.registry import clear_last_add_automated_recruitment_task_payload

        clear_last_add_automated_recruitment_task_payload()
    except Exception:
        pass
    ctx.metadata.pop(_L3_CODER_MODE_META, None)
    ctx.metadata.pop(_L3_CODER_ENGINE_CACHE_META, None)
    ctx.metadata.pop(_L3_COMPLEX_ENGINE_CACHE_META, None)
    ctx.metadata.pop(_L3_VISION_ENGINE_CACHE_META, None)

    if not ctx.metadata.get("_skills_unfiltered"):
        ctx.metadata["_skills_unfiltered"] = list(ctx.metadata.get("_skills") or [])

    # ── 改造点 A：提取本轮 Sticky Goal ─────────────────────────────────────
    # 从 ctx.intent 或 messages 里最后一条 user 消息中提取当前目标字符串；
    # 后续每轮在 full_messages 末尾追加一条 system 提醒，防止目标漂移。
    def _extract_current_objective() -> str:
        obj = (ctx.intent or "").strip()
        if obj:
            return obj[:400]
        for m in reversed(messages or []):
            if not isinstance(m, dict) or m.get("role") != "user":
                continue
            c = str(m.get("content") or "")
            if isinstance(m.get("content"), list):
                for _blk in m["content"]:
                    if isinstance(_blk, dict) and _blk.get("type") == "text":
                        c = str(_blk.get("text") or "")
                        break
            c = c.strip()
            if c and not c.lower().startswith("observation:") and len(c) >= 4:
                return c[:400]
        return ""

    _sticky_goal: str = _extract_current_objective()
    # 每轮是否注入目标提醒（第 1 轮不注入，避免干扰；从第 2 轮且存在工具调用后才启用）
    _STICKY_GOAL_INJECT_FROM_ITER = int(
        (os.environ.get("JACHIN_STICKY_GOAL_FROM_ITER") or "2").strip()
    )
    # ──────────────────────────────────────────────────────────────────────

    try:
        from l3_node.skill_md_hot_reload import register_react_ctx_for_skill_inline

        register_react_ctx_for_skill_inline(str(getattr(ctx, "run_id", "") or ""), ctx)
    except Exception:
        pass

    for iteration in range(max_iterations):
        ctx.metadata["_react_iteration"] = iteration + 1
        # AN — Guardrails 迭代前检查（iterations / token budget）
        try:
            _gr_ck = ctx.metadata.get("_gr_checker")
            if _gr_ck is not None:
                _gr_ck.record_iteration()
                _gr_pre_v = _gr_ck.check_all_pre_iteration()
                if _gr_pre_v is not None:
                    _gr_brief = _gr_ck.execution_brief()
                    logger.warning("[Guardrails] pre-iteration truncate rule=%s iter=%d", _gr_pre_v.rule, iteration + 1)
                    return f"Final Answer: {_gr_brief}"
        except Exception as _gr_loop_e:
            logger.debug("[Guardrails] pre-iteration 检查异常: %s", _gr_loop_e)
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
        try:
            from l3_node.terminal_turn_debug_log import log_react_iteration_start

            log_react_iteration_start(
                iteration + 1,
                str(ctx.metadata.get("_react_step_trace") or ""),
                context={
                    "run_id": getattr(ctx, "run_id", "") or "",
                    "max_iterations": max_iterations,
                    "delegate_depth": int(ctx.metadata.get("_delegate_depth", 0) or 0),
                    "react_tool_invocations_so_far": int(ctx.metadata.get("_react_tool_invocations") or 0),
                    "n_skills_visible": len(skills or []),
                    "intel_strict_pending_verify": bool(ctx.metadata.get("_intel_strict_pending_verify")),
                    "coder_mode": bool(ctx.metadata.get(_L3_CODER_MODE_META)),
                    "implicit_channel": str(ctx.metadata.get("_implicit_channel") or ""),
                },
            )
        except Exception:
            pass

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
                _sl_verify = _spe.get("semantic_layer")
                _sl_verify_d: dict[str, Any] = _sl_verify if isinstance(_sl_verify, dict) else {}
                _exp_verify = str(_spe.get("experience_few_shots") or "")
                _hr_act = bool(_spe.get("hr_domain_prompt_active", True))
                _dc_mode = bool(_spe.get("desktop_companion_mode"))
                _dc_ctx = _spe.get("desktop_companion_context")
                _dc_ctx_d: dict[str, Any] = _dc_ctx if isinstance(_dc_ctx, dict) else {}
                ctx.system_prompt = await _build_system_prompt(
                    tools=skills,
                    allow_delegate=False,
                    allow_recall=True,
                    allow_coordinate=False,
                    prompt_cycle=ctx.metadata.get("_prompt_cycle"),
                    recruitment_longform=_hr_act,
                    hr_domain_prompt_active=_hr_act,
                    prompt_style=str(ctx.metadata.get("_react_prompt_style") or "full"),
                    pure_json_contract=bool(ctx.metadata.get("_pure_json_contract")),
                    gateway_inject=str(ctx.metadata.get("_gw_inject_stored") or ""),
                    safety_lock_user_text=str(ctx.intent or ""),
                    chief_advisor_mode=bool(_spe.get("chief_advisor")),
                    environment_report_block=str(_spe.get("environment_report_block") or ""),
                    semantic_layer=_sl_verify_d,
                    experience_few_shots=_exp_verify,
                    realtime_web_grounding_block=str(_spe.get("realtime_web_grounding_block") or ""),
                    domain_experts=list(ctx.metadata.get("_domain_experts") or []),
                    desktop_companion_mode=_dc_mode,
                    desktop_companion_context=_dc_ctx_d,
                )
            else:
                ctx.system_prompt = ctx.metadata.get("_react_system_prompt_full") or ctx.system_prompt
        except ImportError:
            pass
        try:
            apply_hr_skill_md_hot_reload_to_react_ctx(ctx)
        except Exception:
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
            try:
                ctx.metadata["_execution_brief_reason"] = "cancel_event"
                await global_hooks.run(HOOK_ON_EXECUTION_BRIEF, ctx)
            except Exception:
                pass
            return

        # ── 改造点 A：Sticky Goal 注入 ───────────────────────────────────────
        # 从第 _STICKY_GOAL_INJECT_FROM_ITER 轮起，且工具已有调用 / 历史消息多于 1 条时，
        # 在 full_messages 末尾追加一条 system 提醒，把当前目标拉回注意力中心。
        _do_inject_goal = (
            _sticky_goal
            and iteration + 1 >= _STICKY_GOAL_INJECT_FROM_ITER
            and (
                int(ctx.metadata.get("_react_tool_invocations") or 0) >= 1
                or len(messages) > 1
            )
        )
        _goal_reminder_msg: dict[str, Any] | None = None
        if _do_inject_goal:
            _goal_reminder_msg = {
                "role": "system",
                "content": (
                    f"[系统提醒·目标锚定] 当前轮次你必须完成的任务是：「{_sticky_goal}」。"
                    "请不要偏离这个目标，也不要把上方历史摘要中的旧任务当作当前指令来执行。"
                    "你的最终 Final Answer 必须直接回答上述目标。"
                ),
            }
        _hot_user_msgs: list[dict[str, Any]] = []
        _hot_sk = str(ctx.metadata.get("_lark_chat_id") or "").strip()
        if _hot_sk:
            try:
                from l3_node.session_hot_user_inject import drain_pending_session_user_texts

                _hots = drain_pending_session_user_texts(_hot_sk, max_items=6)
                if _hots:
                    _body = "【会话新进线·热并入当前推理】用户在同一会话中另发消息（请合并理解，无需重复确认收到）：\n" + "\n".join(
                        f"· {h}" for h in _hots
                    )
                    _hot_user_msgs.append({"role": "user", "content": _body})
            except Exception:
                pass
        full_messages = (
            [{"role": "system", "content": ctx.system_prompt}]
            + messages
            + ([_goal_reminder_msg] if _goal_reminder_msg else [])
            + _hot_user_msgs
        )
        # ──────────────────────────────────────────────────────────────────
        logger.debug("[L3 Agent] ReAct iter=%d 调用 LLM stream=%s sticky_goal_inject=%s", iteration + 1, bool(on_chunk), bool(_goal_reminder_msg))
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
        _openapi_tools: list[dict[str, Any]] | None = None
        _openapi_fname_map: dict[str, str] = {}
        if skills and str(os.environ.get("JACHIN_REACT_STREAM_DISABLE_TOOLS", "")).strip().lower() not in (
            "1",
            "true",
            "yes",
        ):
            try:
                _openapi_tools = get_mcp_registry().to_openai_tools_schema(
                    skills,
                    openapi_fname_to_tool_id=_openapi_fname_map,
                )
            except Exception as _e:
                logger.warning("[L3 Agent] 构建 OpenAI tools 失败，ReAct 将仅依赖文本工具说明: %s", _e)
                _openapi_tools = None
                _openapi_fname_map.clear()
        try:
            log_react_iteration_context(
                trace=str(ctx.metadata.get("_react_step_trace") or ""),
                iteration=iteration + 1,
                max_iter=max_iterations,
                run_id=str(ctx.run_id or ""),
                n_history_messages=len(messages),
                n_skills=len(skills or []),
                stream=bool(on_chunk),
                llm_purpose=_llm_purpose,
            )
            log_pipeline_phase(
                "react_pre_llm",
                f"trace={ctx.metadata.get('_react_step_trace')} "
                f"system_prompt_len={len(ctx.system_prompt or '')} "
                f"openai_tools={'on' if _openapi_tools else 'off'} "
                f"openai_tool_count={len(_openapi_tools or [])}",
            )
        except Exception:
            pass
        _llm_t0 = time.perf_counter()
        response = ""
        try:
            if on_chunk:
                response = await _eff.generate_response_stream(
                    full_messages,
                    chunk_callback=on_chunk,
                    tools=_openapi_tools,
                    openapi_fname_to_tool_id=_openapi_fname_map,
                    temperature=0.7,
                    max_tokens=16384,
                    l3_call_purpose=_llm_purpose,
                    **_lkw,
                )
            else:
                response = await _eff.generate_response(
                    full_messages,
                    tools=_openapi_tools,
                    openapi_fname_to_tool_id=_openapi_fname_map,
                    temperature=0.7,
                    max_tokens=16384,
                    l3_call_purpose=_llm_purpose,
                    **_lkw,
                )
        except RunCancelledError:
            _llm_ms = (time.perf_counter() - _llm_t0) * 1000.0
            try:
                from l3_node.terminal_turn_debug_log import log_llm_round_summary

                log_llm_round_summary(
                    iteration + 1,
                    str(ctx.metadata.get("_react_step_trace") or ""),
                    purpose=_llm_purpose,
                    stream=bool(on_chunk),
                    model_effective=str(getattr(_eff, "model_name", "") or ""),
                    model_session_default=str(getattr(engine, "model_name", "") or ""),
                    elapsed_ms=_llm_ms,
                    n_full_messages=len(full_messages),
                    system_prompt_chars=len(ctx.system_prompt or ""),
                    openai_tools=bool(_openapi_tools),
                    openai_tool_count=len(_openapi_tools or []),
                    response_chars=0,
                    error="RunCancelledError",
                )
            except Exception:
                pass
            ctx.final_answer = "[ExecutionBrief] 运行已被取消（LLM 协作式中断）。"
            _emit("answer", ctx.final_answer)
            try:
                ctx.metadata["_execution_brief_reason"] = "llm_cancelled"
                await global_hooks.run(HOOK_ON_EXECUTION_BRIEF, ctx)
            except Exception:
                pass
            return
        except Exception as e:
            _llm_ms = (time.perf_counter() - _llm_t0) * 1000.0
            try:
                from l3_node.llm_budget import BudgetExhaustedError

                if isinstance(e, BudgetExhaustedError):
                    try:
                        from l3_node.terminal_turn_debug_log import log_llm_round_summary

                        log_llm_round_summary(
                            iteration + 1,
                            str(ctx.metadata.get("_react_step_trace") or ""),
                            purpose=_llm_purpose,
                            stream=bool(on_chunk),
                            model_effective=str(getattr(_eff, "model_name", "") or ""),
                            model_session_default=str(getattr(engine, "model_name", "") or ""),
                            elapsed_ms=_llm_ms,
                            n_full_messages=len(full_messages),
                            system_prompt_chars=len(ctx.system_prompt or ""),
                            openai_tools=bool(_openapi_tools),
                            openai_tool_count=len(_openapi_tools or []),
                            response_chars=len(response or ""),
                            error="BudgetExhaustedError",
                        )
                    except Exception:
                        pass
                    ctx.final_answer = (
                        f"[ExecutionBrief] Token 预算用尽（resource）：累计 {e.used}，上限 {e.limit}。"
                        "可调整 ~/.jachin/nexus_config.json 中 agent.main_max_total_tokens / agent.sub_agent_max_total_tokens。"
                    )
                    _emit("answer", ctx.final_answer)
                    try:
                        ctx.metadata["_execution_brief_reason"] = "token_budget_exhausted"
                        await global_hooks.run(HOOK_ON_EXECUTION_BRIEF, ctx)
                    except Exception:
                        pass
                    return
            except ImportError:
                pass
            try:
                from l3_node.terminal_turn_debug_log import log_llm_round_summary

                log_llm_round_summary(
                    iteration + 1,
                    str(ctx.metadata.get("_react_step_trace") or ""),
                    purpose=_llm_purpose,
                    stream=bool(on_chunk),
                    model_effective=str(getattr(_eff, "model_name", "") or ""),
                    model_session_default=str(getattr(engine, "model_name", "") or ""),
                    elapsed_ms=_llm_ms,
                    n_full_messages=len(full_messages),
                    system_prompt_chars=len(ctx.system_prompt or ""),
                    openai_tools=bool(_openapi_tools),
                    openai_tool_count=len(_openapi_tools or []),
                    response_chars=len(response or ""),
                    error=type(e).__name__,
                )
            except Exception:
                pass
            raise

        _llm_ms = (time.perf_counter() - _llm_t0) * 1000.0
        try:
            from l3_node.terminal_turn_debug_log import log_llm_round_summary

            log_llm_round_summary(
                iteration + 1,
                str(ctx.metadata.get("_react_step_trace") or ""),
                purpose=_llm_purpose,
                stream=bool(on_chunk),
                model_effective=str(getattr(_eff, "model_name", "") or ""),
                model_session_default=str(getattr(engine, "model_name", "") or ""),
                elapsed_ms=_llm_ms,
                n_full_messages=len(full_messages),
                system_prompt_chars=len(ctx.system_prompt or ""),
                openai_tools=bool(_openapi_tools),
                openai_tool_count=len(_openapi_tools or []),
                response_chars=len(response or ""),
            )
        except Exception:
            pass

        ctx.current_response = response
        exec_trace(
            logger,
            "ReAct LLM 返回 trace=%s iter=%d response_len=%d",
            str(ctx.metadata.get("_react_step_trace") or ""),
            iteration + 1,
            len(response or ""),
        )
        try:
            log_react_llm_result(
                trace=str(ctx.metadata.get("_react_step_trace") or ""),
                iteration=iteration + 1,
                response_len=len(response or ""),
                response_full=str(response or ""),
            )
        except Exception:
            pass
        try:
            from l3_node.terminal_turn_debug_log import log_llm_assistant_raw

            log_llm_assistant_raw(
                iteration + 1,
                str(ctx.metadata.get("_react_step_trace") or ""),
                str(response or ""),
            )
        except Exception:
            pass

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
            _th_text = thought.group(1).strip()
            _emit("thought", _th_text)
            capture_capability_debug_thought(ctx, _th_text)

        parsed = _parse_action(
            response,
            skills,
            use_mock=use_mock,
            allowed_skills=allowed_skills,
            pure_json_contract=bool(ctx.metadata.get("_pure_json_contract")),
        )
        ctx.parsed_action = parsed
        _iter_n = ctx.metadata.get("_react_iteration")
        _rtrace = str(ctx.metadata.get("_react_step_trace") or "")
        try:
            log_react_parse_result(
                trace=_rtrace,
                iteration=int(_iter_n or iteration + 1),
                parsed_summary=summarize_parsed_action(parsed),
            )
        except Exception:
            pass
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
            from l3_node.terminal_turn_debug_log import log_parsed_action_detail

            log_parsed_action_detail(
                iteration + 1,
                parsed,
                summarize_parsed_action(parsed) if parsed is not None else "(parsed=None)",
                thought_excerpt=(thought.group(1).strip() if thought else ""),
                trace=str(ctx.metadata.get("_react_step_trace") or ""),
            )
        except Exception:
            pass

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
                                and _hr_policy_answer_claims_job_published(ans)
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
                                and _hr_policy_answer_claims_job_published(ans)
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
                                _hr_policy_answer_claims_unmanned_scheduler_running(ans)
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
                            if reject_workspace_writeback_guard(
                                ctx, messages, response, ans, via="parsed_none+final_prefix"
                            ):
                                continue
                            if reject_sqlite_grounding_guard(
                                ctx, messages, response, ans, via="parsed_none+final_prefix"
                            ):
                                continue
                            if reject_capability_final_answer_guards(
                                ctx, messages, response, ans, via="parsed_none+final_prefix"
                            ):
                                continue
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
                ctx.metadata["_retry_reason"] = "fake_mcp_error_json"
                try:
                    await global_hooks.run(HOOK_ON_RETRY, ctx)
                except Exception:
                    pass
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
            if (
                _is_hallucinated_weather_service_error_json(_ans_s)
                and not ctx.metadata.get("_react_fake_weather_error_retry_done")
            ):
                ctx.metadata["_react_fake_weather_error_retry_done"] = True
                ctx.metadata["_retry_reason"] = "fake_weather_error_json"
                try:
                    await global_hooks.run(HOOK_ON_RETRY, ctx)
                except Exception:
                    pass
                logger.warning(
                    "[L3 Agent][纠偏] trace=%s 将续跑 ReAct（仅一次）：Final Answer 为虚构天气服务错误 JSON，"
                    "须先调用 util:get_weather_lite。",
                    _rtrace,
                )
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": (
                        "【系统纠偏】你刚才输出的是仿 API 的天气错误 JSON，但本轮并未执行 **util:get_weather_lite**，"
                        "Observation 中也没有工具返回。\n"
                        "请立即用 ReAct 续写（禁止再输出 Final Answer 或裸 JSON）：\n"
                        "Thought: …\n"
                        "Action: util:get_weather_lite\n"
                        "Action Input: {\"city\":\"<从用户原话提取的城市或地区，如 杭州>\"}\n"
                        "若用户未指定城市，可传 {\"location\":\"<合理默认或用户所在>\"} 或先一句追问。"
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
                and _hr_policy_answer_claims_job_published(ans)
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
                and _hr_policy_answer_claims_job_published(ans)
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
                _hr_policy_answer_claims_unmanned_scheduler_running(ans)
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
            if reject_workspace_writeback_guard(ctx, messages, response, ans, via="type=answer"):
                continue
            if reject_sqlite_grounding_guard(ctx, messages, response, ans, via="type=answer"):
                continue
            if reject_capability_final_answer_guards(ctx, messages, response, ans, via="type=answer"):
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
            _implicit_ch = str(ctx.metadata.get("_implicit_channel") or "")
            _delegate_deny: str | None = None
            if _implicit_ch == "delegate_sub_agent":
                _delegate_deny = (
                    "子 Agent 会话内禁止再次使用 Action: delegate。"
                    "请直接使用当前白名单内工具完成子任务，或在 Final Answer 中说明需返回主会话处理的部分。"
                )
            elif _dd >= _max_dd:
                _delegate_deny = (
                    f"已达 max_delegate_depth={_max_dd}（当前深度 {_dd}），禁止继续 delegate。"
                    "请合并子任务或由单 Agent 顺序执行。"
                )
            if _delegate_deny is not None:
                observation = json.dumps(
                    {
                        "ok": False,
                        "error_class": "config",
                        "message": _delegate_deny,
                    },
                    ensure_ascii=False,
                )
                _obs_delegate_depth_raw = observation
                _mcap_dd = _peek_react_observation_cap_for_upcoming_llm(
                    ctx=ctx,
                    base_engine=engine,
                    messages=messages,
                    iteration=iteration,
                    assistant_response=response,
                    observation_for_followup=_obs_delegate_depth_raw,
                    tool="delegate",
                    skills=list(ctx.metadata.get("_skills") or []),
                )
                observation = _truncate_observation_for_llm(
                    observation,
                    model_cap=_mcap_dd,
                    tool="delegate",
                    current_objective=_sticky_goal,
                )
                ctx.observation = observation
                _p2_record_skill_outcome(ctx, "delegate", observation)
                await global_hooks.run(HOOK_AFTER_TOOL_EXEC, ctx)
                try:
                    from l3_node.terminal_turn_debug_log import log_observation_full

                    log_observation_full(
                        iteration + 1,
                        "delegate(max_depth)",
                        _obs_delegate_depth_raw,
                        sent_to_llm_len=len(observation or ""),
                        truncated_from_len=len(_obs_delegate_depth_raw),
                    )
                except Exception:
                    pass
                _emit("observation", observation)
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": f"Observation: {observation}\n\n请根据限制调整策略并给出 Final Answer:",
                })
                continue
            # ── mode: discuss — 讨论/辩论模式（§2.4 模式 B）──────────────────
            _delegate_mode = str(parsed.get("mode") or "parallel").strip().lower()
            if _delegate_mode == "discuss":
                _discuss_topic = str(parsed.get("topic") or ctx.intent or "")
                _discuss_context = str(parsed.get("context") or "")
                _discuss_roles = parsed.get("roles") or ["planner", "critic"]
                _dmr_raw = parsed.get("max_rounds")
                if _dmr_raw is None or (isinstance(_dmr_raw, str) and not str(_dmr_raw).strip()):
                    _discuss_max_rounds = _discuss_max_rounds_cfg()
                else:
                    try:
                        _discuss_max_rounds = max(1, min(12, int(_dmr_raw)))
                    except (TypeError, ValueError):
                        _discuss_max_rounds = _discuss_max_rounds_cfg()
                _emit("action", f"discuss topic={_discuss_topic[:60]!r} rounds={_discuss_max_rounds}")
                await global_hooks.run(HOOK_BEFORE_TOOL_EXEC, ctx)
                if ctx.aborted:
                    return
                try:
                    from l3_node.primitives.multi_agent.discussion import (
                        DiscussionConfig,
                        run_discussion,
                    )

                    _disc_cfg = DiscussionConfig(
                        topic=_discuss_topic,
                        context=_discuss_context,
                        roles=_discuss_roles,
                        max_rounds=_discuss_max_rounds,
                        item_max_iterations=_discuss_item_max_iterations_cfg(),
                    )
                    _disc_result = await run_discussion(
                        _disc_cfg, engine, delegate_depth=_dd + 1
                    )
                    observation = _truncate_observation_for_llm(
                        f"{_disc_result.format_summary()}\n\n{_disc_result.final_output}"
                    )
                    _schedule_multi_agent_experience_record(
                        ctx,
                        kind="discuss",
                        intent_surface=_intent_surface_for_experience(ctx, messages)
                        or (f"[讨论]{_discuss_topic}"[:8000]),
                        payload={
                            "topic_preview": _discuss_topic[:500],
                            "context_preview": _discuss_context[:300],
                            "roles": [str(x) for x in (_discuss_roles or [])][:20],
                            "rounds_completed": _disc_result.rounds_completed,
                            "status": _disc_result.status,
                            "elapsed_sec": round(_disc_result.elapsed_sec, 3),
                            "final_preview": (_disc_result.final_output or "")[:1200],
                        },
                    )
                except Exception as _disc_err:
                    observation = f"[discuss 执行失败: {_disc_err}]"
                ctx.observation = observation
                _p2_record_skill_outcome(ctx, "delegate", observation)
                await global_hooks.run(HOOK_AFTER_TOOL_EXEC, ctx)
                _emit("observation", observation)
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": f"Observation: {observation}\n\n请根据讨论结果给出最终结论和 Final Answer:",
                })
                continue
            # ── 普通并行 delegate ────────────────────────────────────────────
            sub_tasks = parsed.get("sub_tasks", [])
            if sub_tasks:
                try:
                    ctx.metadata["_task_decompose_sub_count"] = len(sub_tasks)
                    ctx.metadata["_task_decompose_roles_preview"] = ",".join(
                        str((t or {}).get("role") or "") for t in sub_tasks[:12]
                    )[:300]
                    await global_hooks.run(HOOK_ON_TASK_DECOMPOSE, ctx)
                except Exception:
                    pass
                if ctx.aborted:
                    return
            _emit("action", f"delegate {len(sub_tasks)} 个子任务")
            try:
                from l3_node.terminal_turn_debug_log import log_tool_call_full

                try:
                    _st_json = json.dumps(sub_tasks, ensure_ascii=False, default=str, indent=2)
                except Exception:
                    _st_json = repr(sub_tasks)
                log_tool_call_full(
                    iteration + 1,
                    "delegate",
                    _st_json,
                    note=f"n_sub_tasks={len(sub_tasks)}",
                )
            except Exception:
                pass
            await global_hooks.run(HOOK_BEFORE_TOOL_EXEC, ctx)
            if ctx.aborted:
                return
            _child_depth = _dd + 1
            # 并发控制：防止大批子任务同时打爆 API 速率限制
            _max_concurrent = _delegate_max_concurrent_cfg()
            if _max_concurrent > 0 and len(sub_tasks) > _max_concurrent:
                _sem = asyncio.Semaphore(_max_concurrent)

                async def _run_with_sem(_t: dict[str, Any], _idx: int) -> str:
                    async with _sem:
                        return await _run_sub_agent_hooked(
                            _t, engine, delegate_depth=_child_depth, node_index=_idx, parent_ctx=ctx
                        )

                results = await asyncio.gather(
                    *[_run_with_sem(t, i) for i, t in enumerate(sub_tasks)],
                    return_exceptions=True,
                )
            else:
                results = await asyncio.gather(
                    *[
                        _run_sub_agent_hooked(t, engine, delegate_depth=_child_depth, node_index=i, parent_ctx=ctx)
                        for i, t in enumerate(sub_tasks)
                    ],
                    return_exceptions=True,
                )
            # 结构化 RunReport：统计成功/失败数，便于下游模型准确归因
            _ok_count = sum(1 for r in results if not isinstance(r, Exception))
            _fail_count = len(results) - _ok_count
            _failed_items: list[dict[str, Any]] = []
            from l3_node.primitives.multi_agent.result_merger import (
                StructuredResultMerger,
                SubAgentResult,
            )

            _parallel_sub_results: list[SubAgentResult] = []
            for i, r in enumerate(results):
                st = sub_tasks[i] if i < len(sub_tasks) else {}
                role_hint = str(st.get("role") or "default")
                task_full = str(st.get("task", ""))
                task_preview = task_full[:80]
                if isinstance(r, Exception):
                    _failed_items.append({
                        "index": i + 1,
                        "role": role_hint,
                        "task_preview": task_preview,
                        "error": str(r),
                        "error_class": "transient" if "timeout" in str(r).lower() else "per_item",
                    })
                    _parallel_sub_results.append(
                        SubAgentResult(
                            role_id=role_hint,
                            task=task_full[:500],
                            output=str(r),
                            status="failed",
                        )
                    )
                else:
                    _parallel_sub_results.append(
                        SubAgentResult(
                            role_id=role_hint,
                            task=task_full[:500],
                            output=str(r),
                            status="success",
                        )
                    )
            _merger = StructuredResultMerger()
            _merged_parallel_body = _merger.merge_parallel(_parallel_sub_results)
            _run_report = {
                "status": "completed" if _fail_count == 0 else ("partial" if _ok_count > 0 else "failed"),
                "ok_count": _ok_count,
                "failed_count": _fail_count,
                "total": len(sub_tasks),
                "degraded": _fail_count > 0,
                "failed_items": _failed_items,
            }
            _run_report_line = (
                f"[delegate RunReport] 完成: {_ok_count}/{len(sub_tasks)} 成功"
                + (f"，{_fail_count} 失败" if _fail_count else "")
                + "\n"
            )
            logger.info(
                "[L3 Agent] delegate RunReport depth=%d %s",
                _child_depth - 1,
                json.dumps(_run_report, ensure_ascii=False),
            )
            _obs_delegate_raw = _run_report_line + _merged_parallel_body
            if sub_tasks:
                _schedule_multi_agent_experience_record(
                    ctx,
                    kind="parallel_delegate",
                    intent_surface=_intent_surface_for_experience(ctx, messages) or "[delegate并行]",
                    payload={
                        "subtask_roles": [
                            str((sub_tasks[i] or {}).get("role") or "") for i in range(len(sub_tasks))
                        ][:24],
                        "ok_count": _ok_count,
                        "failed_count": _fail_count,
                        "total": len(sub_tasks),
                        "run_report_status": _run_report["status"],
                        "merged_preview": _merged_parallel_body[:2000],
                    },
                )
            observation = _truncate_observation_for_llm(_obs_delegate_raw)
            ctx.observation = observation
            _p2_record_skill_outcome(ctx, "delegate", observation)
            await global_hooks.run(HOOK_AFTER_TOOL_EXEC, ctx)
            try:
                from l3_node.terminal_turn_debug_log import log_observation_full

                log_observation_full(
                    iteration + 1,
                    "delegate",
                    _obs_delegate_raw,
                    sent_to_llm_len=len(observation or ""),
                    truncated_from_len=len(_obs_delegate_raw),
                )
            except Exception:
                pass
            _emit("observation", observation)
            messages.append({"role": "assistant", "content": response})
            messages.append({
                "role": "user",
                "content": f"Observation: {observation}\n\n请根据子任务结果合并并给出 Final Answer:",
            })
            continue

        # recall_memory：ReAct 伪动作 → Memory Nexus（与 core:local_memory_search 同源）
        if parsed["type"] == "recall":
            query = parsed.get("query", "")
            _emit("action", f"recall_memory {query}".strip())
            try:
                from l3_node.terminal_turn_debug_log import log_tool_call_full

                log_tool_call_full(
                    iteration + 1,
                    "recall_memory",
                    str(query or ""),
                    note="Memory Nexus",
                )
            except Exception:
                pass
            await global_hooks.run(HOOK_BEFORE_TOOL_EXEC, ctx)
            if ctx.aborted:
                return
            observation = await _invoke_work_order_tool_transport(
                "core:local_memory_search",
                json.dumps({"query": query, "top_k": 10, "candidate_pool": 48}, ensure_ascii=False),
                allowed_skills,
                ctx,
            )
            _obs_recall_raw = str(observation or "")
            _mcap_rec = _peek_react_observation_cap_for_upcoming_llm(
                ctx=ctx,
                base_engine=engine,
                messages=messages,
                iteration=iteration,
                assistant_response=response,
                observation_for_followup=_obs_recall_raw,
                tool="recall_memory",
                skills=list(ctx.metadata.get("_skills") or []),
            )
            observation = _truncate_observation_for_llm(
                _obs_recall_raw,
                model_cap=_mcap_rec,
                tool="recall_memory",
                current_objective=_sticky_goal,
            )
            ctx.observation = observation
            _p2_record_skill_outcome(ctx, "recall_memory", observation)
            await global_hooks.run(HOOK_AFTER_TOOL_EXEC, ctx)
            try:
                from l3_node.terminal_turn_debug_log import log_observation_full

                log_observation_full(
                    iteration + 1,
                    "recall_memory",
                    _obs_recall_raw,
                    sent_to_llm_len=len(observation or ""),
                    truncated_from_len=len(_obs_recall_raw),
                )
            except Exception:
                pass
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
            try:
                from l3_node.terminal_turn_debug_log import log_tool_call_full

                try:
                    _pl_json = json.dumps(payload, ensure_ascii=False, default=str, indent=2)
                except Exception:
                    _pl_json = repr(payload)
                log_tool_call_full(iteration + 1, "coordinate", _pl_json, note="L2 multi-node")
            except Exception:
                pass
            await global_hooks.run(HOOK_BEFORE_TOOL_EXEC, ctx)
            if ctx.aborted:
                return
            if not config:
                observation = "[coordinate 不可用：未连接 L2 或未配对]"
            else:
                observation = await _coordinate_task(payload, config, engine)
            _obs_coord_raw = str(observation or "")
            _mcap_coord = _peek_react_observation_cap_for_upcoming_llm(
                ctx=ctx,
                base_engine=engine,
                messages=messages,
                iteration=iteration,
                assistant_response=response,
                observation_for_followup=_obs_coord_raw,
                tool="coordinate",
                skills=list(ctx.metadata.get("_skills") or []),
            )
            observation = _truncate_observation_for_llm(
                _obs_coord_raw,
                model_cap=_mcap_coord,
                tool="coordinate",
                current_objective=_sticky_goal,
            )
            ctx.observation = observation
            _p2_record_skill_outcome(ctx, "coordinate", observation)
            await global_hooks.run(HOOK_AFTER_TOOL_EXEC, ctx)
            try:
                from l3_node.terminal_turn_debug_log import log_observation_full

                log_observation_full(
                    iteration + 1,
                    "coordinate",
                    _obs_coord_raw,
                    sent_to_llm_len=len(observation or ""),
                    truncated_from_len=len(_obs_coord_raw),
                )
            except Exception:
                pass
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
            ctx.metadata["_l4_exp_save_gate"] = False
            if not tool_entry_looks_like_sqlite_family({"id": tool}):
                ctx.metadata.pop("_l4_critic_reject_streak", None)
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
                        "content": _react_observation_followup_user_text(str(observation or ""), str(tool or "")),
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
            append_capability_debug_action(
                ctx,
                tool=str(tool or ""),
                inp=str(inp or ""),
                iteration=iteration,
            )
            try:
                from l3_node.terminal_turn_debug_log import log_tool_call_full

                log_tool_call_full(
                    iteration + 1,
                    str(tool or ""),
                    str(inp or ""),
                    note=f"trace={str(ctx.metadata.get('_react_step_trace') or '')}",
                )
            except Exception:
                pass
            # AN — Guardrails 工具前检查（forbidden / max_tool_calls / repeat）
            try:
                from l3_node.guardrails import GuardrailsAbortError, guardrails_enabled
                if guardrails_enabled():
                    _gr_checker = ctx.metadata.get("_gr_checker")
                    if _gr_checker is not None:
                        _gr_violation = _gr_checker.check_all_pre_tool(tool or "", inp or "")
                        if _gr_violation is not None:
                            if _gr_violation.action == "abort":
                                raise GuardrailsAbortError(_gr_violation)
                            if _gr_violation.action == "truncate":
                                _gr_brief = _gr_checker.execution_brief()
                                logger.warning("[Guardrails] truncate rule=%s", _gr_violation.rule)
                                return f"Final Answer: {_gr_brief}"
                            # warn：将警告追加到下一次 observation
                            logger.warning("[Guardrails] warn rule=%s msg=%s", _gr_violation.rule, _gr_violation.message)
                            inp = inp or ""  # warn 时继续执行
            except GuardrailsAbortError:
                raise
            except ImportError:
                pass
            except Exception as _gr_e:
                logger.debug("[Guardrails] 检查异常，跳过: %s", _gr_e)

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
            # L4 Action Critic：SQLite 族工具在真正执行前做逻辑审查；不通过则伪造 Observation 打回重做
            _cev_crit = ctx.metadata.get("_cancel_event")
            if _cev_crit is not None and getattr(_cev_crit, "is_set", lambda: False)():
                return
            if tool_entry_looks_like_sqlite_family({"id": tool}):
                try:
                    from l3_node.experience_memory import tool_id_is_sqlite_read_or_write

                    if tool_id_is_sqlite_read_or_write(tool):
                        try:
                            from l3_node.critic_agent import action_critic_enabled

                            if not action_critic_enabled():
                                ctx.metadata["_l4_exp_save_gate"] = True
                        except Exception:
                            ctx.metadata["_l4_exp_save_gate"] = True
                except Exception:
                    pass
            if tool_entry_looks_like_sqlite_family({"id": tool}):
                try:
                    from l3_node.critic_agent import (
                        action_critic_enabled,
                        action_critic_max_fails,
                        evaluate_action,
                    )

                    if action_critic_enabled():
                        _sem_crit: dict[str, Any] = {}
                        _gb_crit = ctx.metadata.get("_gateway_bundle")
                        if _gb_crit is not None:
                            _sx_c = getattr(_gb_crit, "extra", {}).get("semantic_layer")
                            if isinstance(_sx_c, dict):
                                _sem_crit = _sx_c
                        _prop_act = {
                            "tool_id": tool,
                            "action_input": (inp or "")[:12000],
                            "assistant_react_excerpt": (response or "")[:8000],
                        }
                        _ui_crit = (ctx.intent or "").strip()
                        if not _ui_crit:
                            for _msg_cr in reversed(messages or []):
                                if isinstance(_msg_cr, dict) and _msg_cr.get("role") == "user":
                                    _ui_crit = str(_msg_cr.get("content") or "").strip()[:4000]
                                    break
                        _stp_cr = ctx.metadata.get("_on_step")
                        if _stp_cr:
                            try:
                                _stp_cr(
                                    "system_status",
                                    json.dumps(
                                        {"status": "🛡️ Critic 审查中…"},
                                        ensure_ascii=False,
                                    ),
                                    ctx.run_id,
                                )
                            except Exception:
                                pass
                        _obs4crit = _react_observation_excerpt_for_critic(messages)
                        _ok_cr, _crit_txt = await evaluate_action(
                            _ui_crit,
                            _prop_act,
                            _sem_crit,
                            react_observation_excerpt=_obs4crit,
                        )
                        if _ok_cr and _stp_cr:
                            try:
                                _stp_cr(
                                    "system_status",
                                    json.dumps(
                                        {"status": "✅ 审查通过，即将执行"},
                                        ensure_ascii=False,
                                    ),
                                    ctx.run_id,
                                )
                            except Exception:
                                pass
                        if not _ok_cr:
                            if _stp_cr:
                                try:
                                    _stp_cr(
                                        "system_status",
                                        json.dumps(
                                            {"status": "❌ Critic 未通过，已打回重做"},
                                            ensure_ascii=False,
                                        ),
                                        ctx.run_id,
                                    )
                                except Exception:
                                    pass
                            _max_cr = action_critic_max_fails()
                            _streak_cr = int(ctx.metadata.get("_l4_critic_reject_streak") or 0) + 1
                            ctx.metadata["_l4_critic_reject_streak"] = _streak_cr
                            logger.info(
                                "[L3 Agent][ActionCritic] 拦截 tool=%s streak=%d/%d critique_preview=%r",
                                tool,
                                _streak_cr,
                                _max_cr,
                                (_crit_txt or "")[:240],
                            )
                            exec_trace(
                                logger,
                                "ActionCritic block streak=%s/%s tool=%s",
                                _streak_cr,
                                _max_cr,
                                (tool or "")[:80],
                            )
                            if _streak_cr >= _max_cr:
                                _crit_body = (
                                    f"[System Critic Error] 已连续 {_max_cr} 次未通过逻辑审查！警报！\n"
                                    "绝对禁止输出 Final Answer 放弃任务！绝对禁止把任务推给统帅！\n"
                                    "现在，你必须立刻、马上输出一个合法的只读 Action（如 mcp:query 配合 SELECT，或 mcp:read_records / read_query / list_tables），"
                                    "去获取必要的数据 Observation。只有拿到数据后，再在下一步执行修改！立刻重试！\n"
                                    f"（上一轮审查意见供你修正：{_crit_txt}）"
                                )
                            else:
                                _crit_body = (
                                    f"[System Critic Error] Action Critic blocked this tool call: {_crit_txt} "
                                    "Please follow the capability policy flow: <probe> inspect schema, <map> use semantic context, <execute> use the real tool: "
                                    "只读可用 mcp:query(SELECT)、mcp:read_records、list_tables；"
                                    "写入可用 mcp:update_records、write_query 或 mcp:query(UPDATE)；同一对话内连续执行，勿 Final Answer 中断。"
                                )
                            messages.append({"role": "assistant", "content": response})
                            messages.append({
                                "role": "user",
                                "content": _react_observation_followup_user_text(_crit_body, str(tool or "")),
                            })
                            continue
                        ctx.metadata["_l4_critic_reject_streak"] = 0
                        try:
                            from l3_node.experience_memory import tool_id_is_sqlite_read_or_write

                            if tool_id_is_sqlite_read_or_write(tool):
                                ctx.metadata["_l4_exp_save_gate"] = True
                        except Exception:
                            pass
                except Exception as _ace:
                    logger.debug("[L3 Agent][ActionCritic] 跳过: %s", _ace)
                    try:
                        from l3_node.experience_memory import tool_id_is_sqlite_read_or_write

                        if tool_entry_looks_like_sqlite_family({"id": tool}) and tool_id_is_sqlite_read_or_write(
                            tool
                        ):
                            ctx.metadata["_l4_exp_save_gate"] = True
                    except Exception:
                        pass
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
            observation: str | None = None
            inp, observation, _cap_skip_tool_invoke, _cap_skip_secondary_invoke = before_capability_tool_exec(
                ctx,
                tool=str(tool or ""),
                inp=inp,
                response=response,
            )
            if not _cap_skip_tool_invoke and not _cap_skip_secondary_invoke:
                # 工具执行路由器：MCP / Native；前台默认同步超时（可配置），预取附件去重
                observation = await _invoke_work_order_tool_transport(tool, inp, allowed_skills, ctx)
            elif _cap_skip_tool_invoke and observation is None:
                observation = ""
            try:
                ctx.metadata["_react_tool_invocations"] = int(ctx.metadata.get("_react_tool_invocations") or 0) + 1
            except (TypeError, ValueError):
                ctx.metadata["_react_tool_invocations"] = 1
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
            observation_full = _maybe_shrink_shell_exec_observation(str(observation or ""), tool)
            observation_full = after_capability_tool_exec(
                ctx,
                tool=str(tool or ""),
                inp=inp,
                response=response,
                observation_full=observation_full,
                iteration=iteration,
                max_iterations=max_iterations,
            )
            try:
                mark_workspace_io_capability_flags(ctx, tool, observation_full)
            except Exception as _mwf:
                logger.debug("[L3 Agent] workspace IO capability flags skipped: %s", _mwf)
            _model_obs_cap = _peek_react_observation_cap_for_upcoming_llm(
                ctx=ctx,
                base_engine=engine,
                messages=messages,
                iteration=iteration,
                assistant_response=response,
                observation_for_followup=observation_full,
                tool=tool,
                skills=list(ctx.metadata.get("_skills") or []),
            )
            _eff_obs_max = _effective_observation_max_len(
                observation_full, tool=tool, model_cap=_model_obs_cap
            )
            if len(observation_full) > _eff_obs_max:
                logger.info(
                    "[L3 Agent] Observation 超长已截断供 LLM：tool=%s full_len=%d max=%d (playwright_mcp=%s)",
                    (tool or "")[:120],
                    len(observation_full),
                    _eff_obs_max,
                    _observation_looks_like_playwright_mcp(observation_full),
                )
            observation = _truncate_observation_for_llm(
                observation_full,
                tool=tool,
                model_cap=_model_obs_cap,
                current_objective=_sticky_goal,
            )
            ctx.observation = observation
            _p2_record_skill_outcome(ctx, (tool or "native").strip(), observation)
            await global_hooks.run(HOOK_AFTER_TOOL_EXEC, ctx)
            try:
                from l3_node.terminal_turn_debug_log import log_observation_full

                log_observation_full(
                    iteration + 1,
                    str(tool or ""),
                    observation_full,
                    sent_to_llm_len=len(observation or ""),
                    truncated_from_len=len(observation_full),
                )
            except Exception:
                pass
            append_capability_debug_observation(
                ctx,
                tool=str(tool or ""),
                observation_full=str(observation_full or ""),
                iteration=iteration,
            )
            try:
                from l3_node.react_observation_vision import (
                    build_react_observation_user_content,
                    observation_display_text_for_emit,
                )

                _emit_obs = observation_display_text_for_emit(
                    str(observation_full or observation or ""),
                    str(tool or ""),
                )
            except Exception:
                _emit_obs = observation
            _emit("observation", _emit_obs)
            try:
                if ctx.metadata.get("_l4_exp_save_gate"):
                    from l3_node.experience_memory import (
                        observation_suggests_sqlite_success,
                        save_experience,
                        tool_id_is_sqlite_read_or_write,
                    )

                    if tool_id_is_sqlite_read_or_write(tool) and observation_suggests_sqlite_success(
                        str(observation_full or "")
                    ):
                        _ui_sv = (ctx.intent or "").strip()
                        if not _ui_sv:
                            for _m_sv in reversed(messages or []):
                                if isinstance(_m_sv, dict) and _m_sv.get("role") == "user":
                                    _ui_sv = str(_m_sv.get("content") or "").strip()[:4000]
                                    break
                        _inp_sv = inp or ""
                        if _inp_sv.strip().startswith("{"):
                            try:
                                _pl_obj = json.loads(_inp_sv)
                                _exp_pl: dict[str, Any] = (
                                    _pl_obj if isinstance(_pl_obj, dict) else {"action_input": _inp_sv}
                                )
                            except json.JSONDecodeError:
                                _exp_pl = {"action_input": _inp_sv}
                        else:
                            _exp_pl = {"action_input": _inp_sv}

                        async def _exp_save_and_hook() -> None:
                            try:
                                await asyncio.to_thread(save_experience, _ui_sv, tool, _exp_pl)
                            except Exception:
                                return
                            try:
                                _hc = PipelineContext(
                                    intent=_ui_sv,
                                    source="l3_agent",
                                    run_id=str(ctx.run_id or ""),
                                    metadata={"executed_tool": tool, "path": "l4_experience_sqlite"},
                                )
                                await global_hooks.run(HOOK_ON_EXPERIENCE_LEARNED, _hc)
                            except Exception:
                                pass

                        try:
                            asyncio.create_task(_exp_save_and_hook())
                        except Exception:
                            pass
            except Exception:
                pass
            ctx.metadata["_l4_exp_save_gate"] = False
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
            # atom_post_job_boss 发布成功后清除 fallback，避免下次误用（解析须用未截断全文）
            if base_tool == "atom_post_job_boss":
                try:
                    raw = (observation_full or "").strip()
                    if raw.startswith("{"):
                        _obs_obj = json.loads(raw)
                        if _obs_obj.get("posted", False) or _obs_obj.get("already_published"):
                            _lc = str(ctx.metadata.get("_lark_chat_id") or "").strip()
                            _clear_last_jd_pending(_lc)
                except Exception:
                    pass
            # 工具返回已是完整报告（如 HR 透析镜）时直接作为最终答案，禁止 LLM 二次总结导致截断。
            # 禁止误伤：context_prefetch 会在 Observation 后附加 Markdown（含 ##/**），SQLite list_tables 等也会变长；
            # 若仍用「含 ## 或 **」判断，会把 prefetch + 表清单当成 HR 报告并提前 return，用户会看到整坨 findings.md。
            obs = (observation_full or "").strip()
            _prefetch_blob = "【relevant_context_prefetch】" in obs
            _sqlite_tool = tool_entry_looks_like_sqlite_family({"id": tool})
            _obs_looks_hr_full_report = (
                len(obs) > 500
                and not _prefetch_blob
                and not _sqlite_tool
                and (
                    "综合评分" in obs
                    or "录用建议" in obs
                    or ("透析" in obs and ("简历" in obs or "候选人" in obs))
                    or "HR 透析" in obs
                )
            )
            if _obs_looks_hr_full_report:
                ctx.final_answer = _apply_hr_recruitment_final_answer_table_sync(obs, ctx)
                if on_step:
                    on_step("answer", ctx.final_answer, ctx.run_id)
                return
            messages.append(
                {"role": "assistant", "content": _sanitize_react_assistant_tool_turn_for_history(response)}
            )
            try:
                from l3_node.react_observation_vision import build_react_observation_user_content

                _obs_user_content = build_react_observation_user_content(
                    str(observation_full or observation or ""),
                    str(tool or ""),
                    followup_builder=_react_observation_followup_user_text,
                )
                if isinstance(_obs_user_content, list):
                    ctx.metadata["_forbid_web_fetch_for_vision_turn"] = True
            except Exception as _rov_e:
                logger.debug("[L3 Agent] react_observation_vision 跳过: %s", _rov_e)
                _obs_user_content = _react_observation_followup_user_text(
                    str(observation or ""), str(tool or "")
                )
            _capability_nudge = capability_observation_nudge(ctx, str(observation_full or ""), str(tool or ""))
            if _capability_nudge:
                if isinstance(_obs_user_content, list):
                    _first = _obs_user_content[0] if _obs_user_content else {}
                    if isinstance(_first, dict) and _first.get("type") == "text":
                        _first["text"] = f"{_first.get('text') or ''}{_capability_nudge}"
                    else:
                        _obs_user_content = [
                            {"type": "text", "text": _capability_nudge},
                            *(_obs_user_content if isinstance(_obs_user_content, list) else []),
                        ]
                else:
                    _obs_user_content = f"{_obs_user_content}{_capability_nudge}"
            if _linter_inject:
                if isinstance(_obs_user_content, list):
                    _first = _obs_user_content[0] if _obs_user_content else {}
                    if isinstance(_first, dict) and _first.get("type") == "text":
                        _first["text"] = f"{_linter_inject}\n\n{_first.get('text') or ''}"
                    else:
                        _obs_user_content = [
                            {"type": "text", "text": f"{_linter_inject}\n\n"},
                            *(_obs_user_content if isinstance(_obs_user_content, list) else []),
                        ]
                else:
                    _obs_user_content = f"{_linter_inject}\n\n{_obs_user_content}"
            messages.append({"role": "user", "content": _obs_user_content})
            continue

    # 循环结束仍未产出：最后一轮兜底
    if ctx.observation:
        obs = (ctx.observation or "").strip()
        # 与循环内一致：勿把 context_prefetch / SQLite 长 Observation 误判为 HR 终稿
        _prefetch_blob2 = "【relevant_context_prefetch】" in obs
        _last_tool = ""
        try:
            for _m in reversed(messages or []):
                if isinstance(_m, dict) and _m.get("role") == "assistant":
                    _c = str(_m.get("content") or "")
                    _am = re.search(r"(?im)^Action:\s*(\S+)", _c)
                    if _am:
                        _last_tool = _am.group(1).strip()
                        break
        except Exception:
            _last_tool = ""
        _sqlite_obs2 = tool_entry_looks_like_sqlite_family({"id": _last_tool})
        _obs_looks_hr_full_report2 = (
            len(obs) > 800
            and not _prefetch_blob2
            and not _sqlite_obs2
            and (
                "综合评分" in obs
                or "录用建议" in obs
                or ("透析" in obs and ("简历" in obs or "候选人" in obs))
                or "HR 透析" in obs
            )
        )
        if _obs_looks_hr_full_report2:
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
            resp = str(result or "").strip()
            for pat in (r"Final\s+Answer:\s*(.+)", r"Answer:\s*(.+)"):
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
    ctx.final_answer = "[TextReasoningAgent 达到本轮认知预算上限]"


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
            "不要输出 Thought、Action、Observation、Final Answer 等 ReAct 标签行。",
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
            "不要输出 Thought、Action、Observation、Final Answer 等 ReAct 套话。",
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

    try:
        from l3_node.local_memory import next_prompt_cycle

        _mem_cycle = next_prompt_cycle()
    except ImportError:
        _mem_cycle = None

    from l3_node.routing.output_format_signals import (
        OutputFormatSignals,
        analyze_output_format_signals,
        heuristic_trivial_chitchat_only,
        should_use_direct_llm_bypass,
    )

    _classify_text = (
        (_gateway_bundle.classification_text if _gateway_bundle is not None else None)
        or (user_input or "")
    )

    # 纯寒暄（如「你好」）不注入招聘 SKILL / 在册岗快照 / 招聘域总目录切片，避免默认变「招聘总监」
    _trivial_chitchat = heuristic_trivial_chitchat_only((user_input or "").strip())
    # Lark 长连接「通用机器人」：历史上助理战报/链接几乎都含「飞书、表」等字，若用 prior tail 判招聘域会长期误灌 HR 长 SOP；
    # 因此仅当**本轮用户输入**显式像招聘时才打开招聘域（招聘多轮短句仍走 dispatcher → process_lark_message）
    _recruit_prior: list[dict[str, Any]] | None = prior_messages
    if (_bg_channel or "").strip() == "lark_im_dispatcher":
        _recruit_prior = None
    _recruit_domain = False
    try:
        from l3_node.routing.intent_signals import infer_lark_session_domain

        _session_domain = infer_lark_session_domain(user_input or "", prior_messages)
        _recruit_domain = _session_domain == "hr_recruitment"
    except Exception:
        _recruit_domain = user_message_suggests_recruitment_domain(user_input or "", _recruit_prior)
    _hr_domain_prompt_active = (
        bool(tools_include_recruitment(tools)) and not _trivial_chitchat and _recruit_domain
    )
    _recruit_longform = _hr_domain_prompt_active

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
                    session_id=_gateway_bundle.session_id or "",
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
                        session_id=_gateway_bundle.session_id or "",
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
    try:
        from l3_node.slash_hash_skill_router import is_slash_hash_skill_invocation

        if is_slash_hash_skill_invocation(user_input or ""):
            _try_direct = False
            if _gateway_bundle is not None:
                _gateway_bundle.extra["slash_hash_skill_router"] = True
    except Exception:
        pass
    if _voice_fast_lane:
        _try_direct = True
        _direct_json = False
        if _gateway_bundle is not None:
            _gateway_bundle.extra["voice_fast_lane_direct"] = True
    if _try_direct and not _voice_fast_lane:
        try:
            from l3_node.intent_gateway.ood_signals import should_veto_direct_llm_bypass
            from l3_node.routing.output_format_signals import heuristic_trivial_chitchat_only

            # 纯寒暄已在 should_use_direct_llm_bypass 内用原句过 OOD；此处勿再用含历史摘要的 classification 误 veto
            if not heuristic_trivial_chitchat_only((user_input or "").strip()) and should_veto_direct_llm_bypass(
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

    try:
        from l3_node.slash_hash_skill_router import augment_gateway_inject_for_slash_hash_skill

        _gw_inject = augment_gateway_inject_for_slash_hash_skill(user_input or "", _gw_inject or "")
    except Exception as _sh_ex:
        logger.debug("[L3 Agent] /#/ skill router inject 跳过: %s", _sh_ex)

    if _cognitive_kernel_prompt_block:
        _gw_inject = (_gw_inject or "") + "\n\n" + _cognitive_kernel_prompt_block
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
            _chief_advisor_mode = (
                not _trivial_chitchat
                and (_et == "composite" or bool(heuristic_tool_need(user_input or "")))
            )
            _ig_adv = get_intent_gateway_config()
            if not bool(_ig_adv.get("chief_advisor_prompt_enabled", True)):
                _chief_advisor_mode = False
            # 含图且短句看图问答：关闭「统帅顾问」复合口吻，减少续写会话里无关长任务（如曾要求的写文档）
            if _chief_advisor_mode and bool(_gateway_bundle.extra.get("attachment_has_image")):
                import re as _re_mmqa

                _u_mm = (user_input or "").strip()
                if len(_u_mm) < 220 and _re_mmqa.search(
                    r"图(?:片|里|中|上)|截(?:图|屏)|(?:什么|啥)内容|描述.*图|看图",
                    _u_mm,
                ):
                    _chief_advisor_mode = False
    except Exception as _adv_ex:
        logger.debug("[L3 Agent] environment_report / chief_advisor 片段跳过: %s", _adv_ex)

    _semantic_layer: dict[str, Any] = {}
    if _gateway_bundle is not None:
        _sl_gw = _gateway_bundle.extra.get("semantic_layer")
        if isinstance(_sl_gw, dict):
            _semantic_layer = _sl_gw

    _experience_few_shots = ""
    # Memory SSOT: experience_rag is consumed by MemoryRecallAgent as evidence.
    # Do not build or inject a standalone HISTORY_FEW_SHOTS prompt block here.

    _realtime_grounding_block = ""
    try:
        if (
            _system_prompt_override is None
            and _gateway_bundle is not None
            and getattr(_gateway_bundle, "requires_realtime_knowledge", False)
            and not _try_direct
        ):
            if on_step:
                try:
                    on_step(
                        "system_status",
                        json.dumps(
                            {"status": "*(📡 探测到外部知识需求，已自动预取全网最新情报...)*"},
                            ensure_ascii=False,
                        ),
                        run_id,
                    )
                except Exception:
                    pass
            from l3_node.primitives.tavily_grounding import fetch_tavily_context

            _tq = (user_input or "").strip() or str(
                _gateway_bundle.classification_text or _gateway_bundle.user_input or ""
            )[:2000]
            _realtime_grounding_block = await fetch_tavily_context(_tq, max_tokens=1500)
    except Exception:
        _realtime_grounding_block = ""

    if _system_prompt_override is not None:
        system_prompt = _system_prompt_override
    elif _try_direct:
        system_prompt = ""
    elif _bg_channel == "background_task":
        system_prompt = await _build_system_prompt(
            tools=tools,
            allow_delegate=False,
            allow_coordinate=False,
            prompt_cycle=_mem_cycle,
            recruitment_longform=_recruit_longform,
            hr_domain_prompt_active=_hr_domain_prompt_active,
            prompt_style=_prompt_style,
            pure_json_contract=_pure_json_contract,
            gateway_inject=_gw_inject,
            safety_lock_user_text=user_input or "",
            chief_advisor_mode=_chief_advisor_mode,
            environment_report_block=_environment_report_block,
            semantic_layer=_semantic_layer,
            experience_few_shots=_experience_few_shots,
            realtime_web_grounding_block=_realtime_grounding_block,
            domain_experts=_domain_experts_list,
            desktop_companion_mode=_desktop_companion_mode,
            desktop_companion_context=_desktop_companion_ctx,
        )
    else:
        system_prompt = await _build_system_prompt(
            tools=tools,
            allow_delegate=True,
            prompt_cycle=_mem_cycle,
            recruitment_longform=_recruit_longform,
            hr_domain_prompt_active=_hr_domain_prompt_active,
            prompt_style=_prompt_style,
            pure_json_contract=_pure_json_contract,
            gateway_inject=_gw_inject,
            safety_lock_user_text=user_input or "",
            chief_advisor_mode=_chief_advisor_mode,
            environment_report_block=_environment_report_block,
            semantic_layer=_semantic_layer,
            experience_few_shots=_experience_few_shots,
            realtime_web_grounding_block=_realtime_grounding_block,
            domain_experts=_domain_experts_list,
            desktop_companion_mode=_desktop_companion_mode,
            desktop_companion_context=_desktop_companion_ctx,
        )

    _is_deferred_origin = _bg_channel in ("deferred_task_scheduler", "background_task")
    # 飞书会话身份（前台）：插在 system **最前**，避免被长上下文淹没；由模型调用 util:schedule_task 时必须带上同一 lark_chat_id。
    _FG_LARK_BIND_CHANNELS = frozenset(
        {
            "lark_im_dispatcher",
            "websocket_lark",
            "websocket_terminal",
        }
    )
    if (
        (_lark_cid or "").strip()
        and (system_prompt or "").strip()
        and not (_try_direct or (system_prompt is None))
        and not _hidca_strip_lark_identity
        and _bg_channel in _FG_LARK_BIND_CHANNELS
    ):
        _lc = (_lark_cid or "").strip()
        _lark_identity_block = (
            "【最高优先级｜飞书 originating 会话 identity】\n"
            f"- 本条用户提问所在的飞书会话 ID（`receive_id_type=chat_id` 时的目标 chat_id）：\n"
            f"  `{_lc}`\n"
            "- 凡用户要在**未来某时刻**执行操作、提醒、发结果等：**必须本轮调用** "
            "**util:schedule_task**，且参数 **lark_chat_id** 与上面 **`"
            + _lc
            + "`** 逐字相同（不要将环境变量默认群/LARK_USER_OPEN_ID 等其他 ID 混入）。\n"
            "- **禁止**不调用工具、仅用 Final Answer 写「✅已注册定时任务」或编造 `deferred_task_…`——"
            "以 **Observation 里 `\"ok\": true` 且含真实 job_id** 为准再向用户确认。\n"
            "- 定时到点后，宿主将把该次执行的 Final Answer **自动推送**到上述会话；"
            "除非你收到的是「发往另一会话」的显式新要求，否则不要自作主张 util:lark_send_text 到别处。\n\n"
        )
        system_prompt = _lark_identity_block + str(system_prompt or "")

    # 延迟任务：与 deferred_task_scheduler + 程序化 Final Answer→Lark 推送一致（勿再误导模型强行走 lark_send_text）
    if _is_deferred_origin and _lark_cid and system_prompt is not None:
        _lc = (_lark_cid or "").strip()
        _deferred_lark_hint = (
            f"\n\n【⚠️ 延迟任务·渠道强制规则】本次由定时器/后台 Worker 触发；"
            f"用户当初的飞书 originating 会话：**{_lc}**\n"
            "把提醒或可交付结果写在 **Final Answer**（简短、可读）。\n"
            "**禁止**调用 util:lark_send_text、util:desktop_message_box（除非本条 intent 明确要求发到**其他**指定会话）；\n"
            f"系统会在本轮结束后自动将 Final Answer 推送到 **`{_lc}`**，"
            "不要改发到监控群、默认群等其他 chat_id。\n"
        )
        system_prompt = str(system_prompt) + _deferred_lark_hint

    _user_llm_content: str | list[Any] = user_input or ""
    try:
        from l3_node.intent_gateway.multimodal_attachments import build_openai_user_content
        from l3_node.intent_gateway.sanitize import trim_attachments_metadata_list
        from l3_node.multimodal_log import summarize_attachments_ingress, summarize_openai_user_content_for_log

        # 优先使用调用方传入的 attachments_metadata（与 WS 合并内联图一致），再 trim；
        # 勿在「bundle.attachments_raw 非空」时仅采 bundle，以免与入参漂移时丢图。
        _raw_att: list[dict[str, Any]] = []
        if attachments_metadata is not None:
            _raw_att = trim_attachments_metadata_list(
                [x for x in attachments_metadata if isinstance(x, dict)]
            )
        elif _gateway_bundle is not None and getattr(_gateway_bundle, "attachments_raw", None):
            _raw_att = [x for x in _gateway_bundle.attachments_raw if isinstance(x, dict)]
        if _raw_att:
            logger.info(
                "[MultimodalIngress] %s",
                summarize_attachments_ingress(_raw_att, run_id=run_id),
            )
            _user_llm_content = await asyncio.to_thread(
                build_openai_user_content,
                user_input or "",
                _raw_att,
            )
            logger.info(
                "[MultimodalIngress] assembled_user_content run_id=%s %s",
                run_id[:12],
                summarize_openai_user_content_for_log(_user_llm_content),
            )
    except Exception as _mmc_ex:
        logger.warning("[L3 Agent] 多模态附件组装失败，回退纯文本: %s", _mmc_ex)
        _user_llm_content = user_input or ""

    try:
        from l3_node.terminal_turn_debug_log import append_section

        _tier_dbg = ""
        if _gateway_bundle is not None:
            _tier_dbg = str(_gateway_bundle.extra.get("execution_tier") or "")
        _dbg_body = {
            "run_id": run_id,
            "max_iterations": max_iterations,
            "delegate_depth": _delegate_depth,
            "channel": _bg_channel,
            "lark_chat_id": _lark_cid or None,
            "lark_reply_chat_id": _lark_cid or None,
            "lark_chat_id_suffix": (_lark_cid[-16:] if len(_lark_cid) > 16 else _lark_cid) or None,
            "prompt_style": _prompt_style,
            "pure_json_contract": _pure_json_contract,
            "user_facing_reply_agent_fast_path": _try_direct,
            "system_prompt_chars": len(system_prompt or ""),
            "chief_advisor_mode": _chief_advisor_mode,
            "execution_tier": _tier_dbg,
            "n_tools": len(tools),
            "allowlist_is_set": allowed is not None,
        }
        append_section(
            "[run_agent] 进入认知内核文本角色前的配置摘要",
            json.dumps(_dbg_body, ensure_ascii=False, indent=2),
        )
        from l3_node.terminal_turn_debug_log import log_human_run_config

        log_human_run_config(_dbg_body)
    except Exception:
        pass

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
            messages.append({"role": "user", "content": _user_llm_content})
            _ood_reply = get_ood_hard_block_reply()
            messages.append({"role": "assistant", "content": _ood_reply})
            if _session_messages is not None:
                _session_messages.clear()
                _recent_ood = messages[-30:] if len(messages) > 30 else messages
                _session_messages.extend(_recent_ood)
            _turn_dbg["answer"] = _ood_reply
            try:
                from l3_node.terminal_turn_debug_log import finalize_top_level_turn

                finalize_top_level_turn(
                    _ood_reply,
                    delegate_depth=_delegate_depth,
                    run_id=run_id,
                    channel=_bg_channel,
                    extra=_lark_turn_dbg_extra or None,
                )
            except Exception:
                pass
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
                    messages.append({"role": "user", "content": _user_llm_content})
                    _sem_reply = get_semantic_ood_reject_reply()
                    messages.append({"role": "assistant", "content": _sem_reply})
                    if _session_messages is not None:
                        _session_messages.clear()
                        _recent_sem = messages[-30:] if len(messages) > 30 else messages
                        _session_messages.extend(_recent_sem)
                    _turn_dbg["answer"] = _sem_reply
                    try:
                        from l3_node.terminal_turn_debug_log import finalize_top_level_turn

                        finalize_top_level_turn(
                            _sem_reply,
                            delegate_depth=_delegate_depth,
                            run_id=run_id,
                            channel=_bg_channel,
                            extra=_lark_turn_dbg_extra or None,
                        )
                    except Exception:
                        pass
                    return _apply_hr_recruitment_final_answer_table_sync(_sem_reply, _DirectBypassCtx())
    except Exception as _sem_ex:
        logger.debug("[L3 Agent] semantic_ood 评估跳过: %s", _sem_ex)

    # Memory Nexus：旧版 JSON「梦境合并」调度与聊天侧间隔配置已停用（见 memory_compactor / memory_compact_schedule）。
    # try:
    #     from l3_node.memory_compact_schedule import try_apply_chat_command
    #     _sched_note = try_apply_chat_command(user_input or "")
    #     if _sched_note:
    #         messages.append({"role": "system", "content": _sched_note})
    # except ImportError:
    #     pass

    messages.append({"role": "user", "content": _user_llm_content})
    # _schedule_local_memory_compaction_background(user_input or "")

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
        implicit_attribution=implicit_attribution,
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
        _turn_dbg["answer"] = _early
        try:
            from l3_node.terminal_turn_debug_log import finalize_top_level_turn

            finalize_top_level_turn(
                _early,
                delegate_depth=_delegate_depth,
                run_id=run_id,
                channel=_bg_channel,
                extra=_lark_turn_dbg_extra or None,
            )
        except Exception:
            pass
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
    if _delegate_depth == 0 and _lark_cid and _bg_channel == "lark_im_dispatcher":
        try:
            from l3_node.foreground_run_registry import register_foreground_run

            register_foreground_run(_lark_cid, run_id)
        except Exception:
            logger.debug("[L3 Agent] foreground_run_registry.register 跳过", exc_info=True)
    if _delegate_depth == 0 and _bg_channel != "background_task":
        try:
            from l3_node.task_runtime_registry import register_foreground_task

            _rtags: list[str] | None = None
            if implicit_attribution and isinstance(implicit_attribution, dict):
                raw = implicit_attribution.get("resource_tags")
                if isinstance(raw, list):
                    _rtags = [str(x).strip()[:64] for x in raw if str(x).strip()][:8]
                elif raw is not None and str(raw).strip():
                    _rtags = [str(raw).strip()[:64]]
            if not _rtags:
                _c = (_bg_channel or "unknown").strip()[:48] or "unknown"
                _rtags = [f"channel:{_c}"]
            register_foreground_task(
                run_id=run_id,
                channel=_bg_channel or "unknown",
                session_key=_lark_cid,
                resource_tags=_rtags,
            )
        except Exception:
            logger.debug("[L3 Agent] task_runtime_registry.register 跳过", exc_info=True)
    _tok_cap = _llm_token_budget_for_run(_delegate_depth)
    _tok_acc: dict[str, int] = {"prompt": 0, "completion": 0}

    try:
        if _try_direct and _system_prompt_override is None:
            # 直连路径仍用主 engine；ReAct 含图时已由 _react_engine_for_iteration 切至 INTENT_GATEWAY_MULTIMODAL_MODEL（默认 qwen3.5-plus）
            _direct_model_ov: str | None = None
            logger.info(
                "[L3 Agent] UserFacingReplyAgent fast path run_id=%s json_object=%s model_override=%s",
                run_id,
                _direct_json,
                _direct_model_ov or "-",
            )
            exec_trace(logger, "UserFacingReplyAgent fast path 开始 run_id=%s json_object=%s", run_id[:12], _direct_json)
            try:
                # 纯寒暄直连：不传完整 history（其中常含【历史摘要】里的错误人设、旧轮「招聘总监」回复），否则模型会复读
                _direct_chitchat = bool(_trivial_chitchat and not _direct_json)
                _uc_direct = _user_llm_content
                if _direct_chitchat and isinstance(_uc_direct, str):
                    _uc_direct = _uc_direct.strip()
                _db_msgs: list[dict[str, Any]] = (
                    [{"role": "user", "content": _uc_direct}]
                    if _direct_chitchat
                    else messages
                )
                _db_out = await _run_direct_llm_completion(
                    messages=_db_msgs,
                    engine=engine,
                    prompt_cycle=_mem_cycle,
                    json_mode=_direct_json,
                    on_chunk=on_chunk,
                    run_id=run_id,
                    token_acc=_tok_acc,
                    token_budget=_tok_cap,
                    cancel_event=_cancel_ev,
                    model_override=_direct_model_ov,
                    general_chitchat=_direct_chitchat,
                    desktop_companion_mode=_desktop_companion_mode,
                    desktop_companion_context=_desktop_companion_ctx,
                )
                messages.append({"role": "assistant", "content": _db_out})
                if _session_messages is not None:
                    _session_messages.clear()
                    _recent_db = messages[-30:] if len(messages) > 30 else messages
                    _session_messages.extend(_recent_db)
                exec_trace(logger, "UserFacingReplyAgent fast path 完成 run_id=%s out_len=%d", run_id[:12], len(_db_out or ""))
                try:
                    schedule_nexus_turn_commit_async(user_input or "", _db_out or "")
                except Exception:
                    pass
                try:
                    _mctx = PipelineContext(
                        intent=user_input or "",
                        source="l3_agent",
                        run_id=run_id,
                        metadata={"_implicit_channel": _bg_channel, "path": "user_facing_reply_agent"},
                    )
                    await global_hooks.run(HOOK_ON_MEMORY_COMMIT, _mctx)
                except Exception:
                    pass
                _turn_dbg["answer"] = _db_out
                try:
                    from l3_node.terminal_turn_debug_log import finalize_top_level_turn

                    finalize_top_level_turn(
                        _db_out,
                        delegate_depth=_delegate_depth,
                        run_id=run_id,
                        channel=_bg_channel,
                        extra=_lark_turn_dbg_extra or None,
                    )
                except Exception:
                    pass
                return _apply_hr_recruitment_final_answer_table_sync(_db_out, _DirectBypassCtx())
            except Exception as _e_db:
                logger.warning("[L3 Agent] UserFacingReplyAgent fast path 失败，转入 TextReasoningAgent: %s", _e_db)
                exec_trace(logger, "UserFacingReplyAgent fast path 失败转入 TextReasoningAgent run_id=%s err=%s", run_id[:12], str(_e_db)[:200])
                if (
                    _gateway_bundle is not None
                    and getattr(_gateway_bundle, "requires_realtime_knowledge", False)
                    and not (_realtime_grounding_block or "").strip()
                    and _system_prompt_override is None
                ):
                    try:
                        from l3_node.primitives.tavily_grounding import fetch_tavily_context

                        _tq_fb = (user_input or "").strip() or str(
                            _gateway_bundle.classification_text or _gateway_bundle.user_input or ""
                        )[:2000]
                        _realtime_grounding_block = await fetch_tavily_context(_tq_fb, max_tokens=1500)
                    except Exception:
                        pass
                if _bg_channel == "background_task":
                    system_prompt = await _build_system_prompt(
                        tools=tools,
                        allow_delegate=False,
                        allow_coordinate=False,
                        prompt_cycle=_mem_cycle,
                        recruitment_longform=_recruit_longform,
                        hr_domain_prompt_active=_hr_domain_prompt_active,
                        prompt_style=_prompt_style,
                        pure_json_contract=_pure_json_contract,
                        gateway_inject=_gw_inject,
                        safety_lock_user_text=user_input or "",
                        chief_advisor_mode=_chief_advisor_mode,
                        environment_report_block=_environment_report_block,
                        semantic_layer=_semantic_layer,
                        experience_few_shots=_experience_few_shots,
                        realtime_web_grounding_block=_realtime_grounding_block,
                        domain_experts=_domain_experts_list,
                        desktop_companion_mode=_desktop_companion_mode,
                        desktop_companion_context=_desktop_companion_ctx,
                    )
                else:
                    system_prompt = await _build_system_prompt(
                        tools=tools,
                        allow_delegate=True,
                        prompt_cycle=_mem_cycle,
                        recruitment_longform=_recruit_longform,
                        hr_domain_prompt_active=_hr_domain_prompt_active,
                        prompt_style=_prompt_style,
                        pure_json_contract=_pure_json_contract,
                        gateway_inject=_gw_inject,
                        safety_lock_user_text=user_input or "",
                        chief_advisor_mode=_chief_advisor_mode,
                        environment_report_block=_environment_report_block,
                        semantic_layer=_semantic_layer,
                        experience_few_shots=_experience_few_shots,
                        realtime_web_grounding_block=_realtime_grounding_block,
                        domain_experts=_domain_experts_list,
                        desktop_companion_mode=_desktop_companion_mode,
                        desktop_companion_context=_desktop_companion_ctx,
                    )

        if not system_prompt and _system_prompt_override is None:
            if _bg_channel == "background_task":
                system_prompt = await _build_system_prompt(
                    tools=tools,
                    allow_delegate=False,
                    allow_coordinate=False,
                    prompt_cycle=_mem_cycle,
                    recruitment_longform=_recruit_longform,
                    hr_domain_prompt_active=_hr_domain_prompt_active,
                    prompt_style=_prompt_style,
                    pure_json_contract=_pure_json_contract,
                    gateway_inject=_gw_inject,
                    safety_lock_user_text=user_input or "",
                    chief_advisor_mode=_chief_advisor_mode,
                    environment_report_block=_environment_report_block,
                    semantic_layer=_semantic_layer,
                    experience_few_shots=_experience_few_shots,
                    realtime_web_grounding_block=_realtime_grounding_block,
                    domain_experts=_domain_experts_list,
                    desktop_companion_mode=_desktop_companion_mode,
                    desktop_companion_context=_desktop_companion_ctx,
                )
            else:
                system_prompt = await _build_system_prompt(
                    tools=tools,
                    allow_delegate=True,
                    prompt_cycle=_mem_cycle,
                    recruitment_longform=_recruit_longform,
                    hr_domain_prompt_active=_hr_domain_prompt_active,
                    prompt_style=_prompt_style,
                    pure_json_contract=_pure_json_contract,
                    gateway_inject=_gw_inject,
                    safety_lock_user_text=user_input or "",
                    chief_advisor_mode=_chief_advisor_mode,
                    environment_report_block=_environment_report_block,
                    semantic_layer=_semantic_layer,
                    experience_few_shots=_experience_few_shots,
                    realtime_web_grounding_block=_realtime_grounding_block,
                    domain_experts=_domain_experts_list,
                    desktop_companion_mode=_desktop_companion_mode,
                    desktop_companion_context=_desktop_companion_ctx,
                )

        _md_base: dict[str, Any] = {
            "_skills": tools,
            "_skills_unfiltered": list(tools),
            "_allowed_skills": allowed,
            "_use_mock": False,
            "_forbid_web_fetch_for_vision_turn": bool(_vision_forbid_web_fetch),
            "_max_iterations": max_iterations,
            "_on_step": on_step,
            "_system_prompt_extras": {
                "chief_advisor": _chief_advisor_mode,
                "hr_domain_prompt_active": _hr_domain_prompt_active,
                "environment_report_block": _environment_report_block,
                "semantic_layer": dict(_semantic_layer),
                "experience_few_shots": _experience_few_shots,
                "realtime_web_grounding_block": _realtime_grounding_block,
                "desktop_companion_mode": _desktop_companion_mode,
                "desktop_companion_context": dict(_desktop_companion_ctx),
            },
            "_gw_inject_stored": _gw_inject,
            "_intent_orchestrator_decision": _intent_orchestrator_decision,
            "_on_chunk": on_chunk,
            "_lark_chat_id": "" if _hidca_strip_lark_identity else _lark_cid,
            "_implicit_channel": _bg_channel,
            "_prompt_cycle": _mem_cycle,
            "_cancel_event": _cancel_ev,
            "_delegate_depth": _delegate_depth,
            "_llm_token_accumulator": _tok_acc,
            "_llm_token_budget_max": _tok_cap,
            "_react_prompt_style": _prompt_style,
            "_pure_json_contract": _pure_json_contract,
            "_domain_experts": list(_domain_experts_list),
            "_readonly_subagent": _readonly_subagent,
            "_sub_agent_role": _sub_agent_role,
        }
        apply_capability_metadata_seed(_md_base, implicit_attribution)
        try:
            from l3_node.primitives.mcp.sqlite_write_guard import messages_history_has_write_ack_grant

            _md_base["_user_granted_mcp_sqlite_write_ack"] = messages_history_has_write_ack_grant(messages)
        except Exception:
            _md_base["_user_granted_mcp_sqlite_write_ack"] = False
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
        try:
            from l3_node.terminal_turn_debug_log import log_main_agent_effective_prompt

            log_main_agent_effective_prompt(
                stage="cognitive_kernel_text_reasoning_role",
                system_prompt=system_prompt or "",
                gateway_inject=_gw_inject or "",
                cognitive_kernel_prompt_block=_cognitive_kernel_prompt_block or "",
                tools_count=len(tools or []),
                messages_count=len(messages or []),
                sent_to_llm=bool((system_prompt or "").strip()),
                note="This is the authorized TextReasoningAgent/UserFacingReplyAgent prompt. It is not the old top-level main-agent loop; tool actions still pass through WorkOrder dispatch.",
            )
        except Exception:
            pass

        pipeline = Pipeline()

        async def on_intent_mw(c: PipelineContext, next_fn) -> None:
            await global_hooks.run(HOOK_ON_INTENT_RECEIVED, c)
            if not c.aborted:
                await next_fn()

        async def text_reasoning_role_mw(c: PipelineContext, next_fn) -> None:
            await _run_text_transport_core(c, engine, on_step=on_step)
            if not c.aborted:
                await next_fn()

        async def pre_resp_mw(c: PipelineContext, next_fn) -> None:
            await global_hooks.run(HOOK_BEFORE_RESPONSE, c)
            await next_fn()

        pipeline.use(on_intent_mw).use(text_reasoning_role_mw).use(pre_resp_mw)
        exec_trace(
            logger,
            "TextReasoningAgent 管道开始 run_id=%s max_iter=%d tools=%d",
            run_id[:12],
            max_iterations,
            len(tools),
        )
        await pipeline.execute(ctx)
        exec_trace(
            logger,
            "TextReasoningAgent 管道结束 run_id=%s aborted=%s final_len=%d",
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
        if _delegate_depth == 0 and (out or "").strip():
            try:
                from l3_node.react_ui_sanitize import sanitize_user_visible_answer

                out = sanitize_user_visible_answer(str(out))
            except Exception:
                pass
        try:
            if not bool(getattr(ctx, "aborted", False)):
                schedule_nexus_turn_commit_async(user_input or "", out)
        except Exception:
            pass
        try:
            if not bool(getattr(ctx, "aborted", False)):
                await global_hooks.run(HOOK_ON_MEMORY_COMMIT, ctx)
        except Exception:
            pass
        # ── 对话监控：镜像到固定监控群（fire-and-forget，不阻塞） ──
        try:
            if (
                _delegate_depth == 0  # 只记录顶层对话，不记录子 Agent
                and not bool(getattr(ctx, "aborted", False))
                and (user_input or "").strip()
            ):
                from l3_node.conversation_monitor import mirror_conversation_async

                _monitor_channel = str(ctx.metadata.get("_implicit_channel") or "")
                _monitor_sender = ""
                _ia = implicit_attribution or {}
                if isinstance(_ia, dict):
                    _monitor_sender = str(
                        _ia.get("lark_sender_name")
                        or _ia.get("lark_user_id")
                        or _ia.get("sender")
                        or ""
                    )
                asyncio.create_task(
                    mirror_conversation_async(
                        user_input or "",
                        out,
                        channel=_monitor_channel,
                        sender=_monitor_sender,
                        run_id=run_id,
                    ),
                    name=f"conv-monitor-{run_id[:8]}",
                )
        except Exception as _monitor_ex:
            logger.debug("[ConvMonitor] 挂钩失败（不影响主流程）: %s", _monitor_ex)
        # ─────────────────────────────────────────────────────────
        _turn_dbg["answer"] = out
        return _apply_hr_recruitment_final_answer_table_sync(out, ctx)
    finally:
        if _delegate_depth == 0:
            try:
                from l3_node.terminal_turn_debug_log import finalize_top_level_turn

                _dbg_ans = _turn_dbg.get("answer")
                if _dbg_ans is not None:
                    finalize_top_level_turn(
                        str(_dbg_ans),
                        delegate_depth=_delegate_depth,
                        run_id=run_id,
                        channel=_bg_channel,
                        extra=_lark_turn_dbg_extra or None,
                    )
            except Exception:
                pass
        unregister_cancel_event(run_id)
        if _delegate_depth == 0 and _lark_cid and _bg_channel == "lark_im_dispatcher":
            try:
                from l3_node.foreground_run_registry import unregister_foreground_run

                unregister_foreground_run(_lark_cid, run_id)
            except Exception:
                logger.debug("[L3 Agent] foreground_run_registry.unregister 跳过", exc_info=True)
        if _delegate_depth == 0 and _bg_channel != "background_task":
            try:
                from l3_node.task_runtime_registry import unregister_foreground_task

                unregister_foreground_task(run_id)
            except Exception:
                logger.debug("[L3 Agent] task_runtime_registry.unregister 跳过", exc_info=True)
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
# L5 本地记忆梦境合并（后台调度）— **已停用**，见 Memory Nexus / memory_compactor。
# ---------------------------------------------------------------------------


def _schedule_local_memory_compaction_background(user_input: str) -> None:
    """[已停用] 原：显式口令或 JACHIN_MEMORY_COMPACT_ON_SESSION 触发 JSON 梦境合并；现由 Memory Nexus 取代。"""
    logger.debug(
        "[MemoryCompact] 后台调度已禁用（忽略本轮）chars=%d",
        len(user_input or ""),
    )
    return


# ---------------------------------------------------------------------------
# 记忆：已移除 L2 /memory/sync 守护进程；跨会话 SSOT 为 Memory Nexus（SQLite + FastEmbed），见 memory_nexus_bridge。
# ---------------------------------------------------------------------------


# 注册 L3 神盾 Compaction（阶段 A：锚点/审计与 L3 共用）
try:
    import l3_node.l3_compaction_bridge  # noqa: F401
except Exception:
    pass
