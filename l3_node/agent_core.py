"""
Jachin Nexus V2 - L3 单体 Agent 与记忆同步

单机闭环：Thought -> Action -> Observation。
支持 Agent 分身（delegate）：主 Agent 可将复杂任务拆给子 Agent 并行执行。
真实技能：core:fs_read、core:shell_exec、core:fs_write（权限限于 ~/.jachin/workspace/）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
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
from l3_node.llm_client import LiteLLMEngine, SecurityContext
from l3_node.skills import build_tools_description, get_hr_invoke_defaults, get_mcp_registry, load_tools, run_tool

logger = logging.getLogger(__name__)

MAX_REACT_ITERATIONS = 8  # 多轮工具调用场景需更多迭代，5 易触发「循环达到上限」
NATIVE_TOOL_IDS = ("core:fs_read", "core:fs_write", "core:shell_exec")
RECALL_MEMORY_TOOL_ID = "recall_memory"
COORDINATE_TOOL_ID = "coordinate"

# 子 Agent 角色预设（分身时使用）
SUB_AGENT_PROMPTS: dict[str, str] = {
    "coder": "你是资深程序员，只负责编写代码。使用 core:fs_read 读取文件，core:fs_write 写入代码。",
    "writer": "你是技术文档工程师，只负责撰写文档。使用 core:fs_read 读取参考，core:fs_write 写入文档。",
    "researcher": "你是研究员，负责查阅和分析。使用 core:fs_read 读取文件，core:shell_exec 执行查询命令。",
    "default": "你是专业助手，完成指定子任务。可用工具：core:fs_read、core:fs_write、core:shell_exec。",
}

# 子 Agent 独立工具集（按角色裁剪，绝不给发邮件等敏感技能）
SUB_AGENT_ALLOWED_SKILLS: dict[str, list[str]] = {
    "coder": ["core:fs_read", "core:fs_write", "core:shell_exec"],
    "writer": ["core:fs_read", "core:fs_write"],
    "researcher": ["core:fs_read", "core:shell_exec"],
    "default": ["core:fs_read", "core:fs_write", "core:shell_exec"],
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
    # 优先解析 Action：若 LLM 同时输出 Action 与 Final Answer，必须先执行工具，避免误判为幻觉
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
        from l3_node.skills import load_tools
        tools_fallback = load_tools(allowed_skills=allowed_skills)
        tool_ids = [t.get("id", "") for t in tools_fallback if t.get("id")]

    def _extract_input_after_action(action_pattern: str) -> str:
        """提取紧跟在当前 Action 后的 Action Input，避免多 Action 时取错"""
        m = re.search(action_pattern, text, re.IGNORECASE)
        if not m:
            return ""
        search_start = m.end()
        rest = text[search_start:]
        mi = re.search(
            r"Action\s+Input:\s*(.+?)(?:\n\n|\n(?:Thought|Action|Final|$))",
            rest, re.DOTALL | re.IGNORECASE,
        )
        return (mi.group(1).strip() if mi else "")

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
    # 最后解析 Final Answer（无 Action 时）
    for pattern in (r"Final\s+Answer:\s*(.+)", r"Answer:\s*(.+)"):
        m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if m:
            return {"type": "answer", "content": m.group(1).strip()}
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
    import httpx
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
            return "[未找到相关记忆]"
        parts = [f"- {r.get('content', '')[:300]}..." for r in results[:5]]
        return "\n".join(parts)
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
) -> str:
    allowed = _get_allowed_skills()
    tools = tools or load_tools(allowed_skills=allowed)
    tools_desc = build_tools_description(tools)
    recall_hint = ""
    if allow_recall and _get_l2_config():
        recall_hint = "\n- recall_memory: 向 L2 检索历史记忆。参数: 查询关键词。当需要回忆过往对话或上下文时使用。"
    coordinate_hint = ""
    if allow_coordinate and _get_l2_config():
        coordinate_hint = """
- coordinate: 向 L2 请求多节点协同。当任务需拆分给多台 L3 并行执行时使用。
  Action Input: {"parent_node_id": "本节点ID", "intent": "主任务描述", "sub_tasks": [{"intent": "子任务1"}, {"intent": "子任务2"}]}"""
    delegate_hint = ""
    if allow_delegate:
        delegate_hint = """
若任务需要多种能力（如同时写代码和写文档），可输出：
Action: delegate
Action Input: {"sub_tasks": [{"role": "coder", "task": "编写 XXX"}, {"role": "writer", "task": "撰写文档"}]}
将子任务交给专业子 Agent 并行执行。"""
    hr_hint = ""
    hr_ids = [t.get("id", "") for t in tools if "hr.analyzer" in (t.get("id") or "")]
    hr_preferred = (next((x for x in hr_ids if "analyzer4" in x), None) or next((x for x in hr_ids if "analyzer3" in x), None) or next((x for x in hr_ids if "analyzer2" in x), None) or (hr_ids[0] if hr_ids else None)) if hr_ids else None
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

    hr_recruitment_hint = """
【HR 招聘总监 SOP】你是 Jachin OS 的首席 AI 招聘总监，拥有直接操作 Boss 直聘后台和开启无人值守招聘流的最高权限。
当用户要求发布职位或招聘某类人才时，你必须严格遵循以下 SOP：

第一步【智能草拟配置】：当用户表达招聘意图（如「帮我招个 Golang」「你帮我发布一个职位」「帮我发一个职位」「发布职位」「我要招聘」等）时，**绝对不要**生硬地追问每一个缺失字段！你必须发挥你的专业 HR 知识，根据用户的零星线索，直接为他/她【脑补并生成】一份极其专业、完整的 JSON 配置单草案。草案必须严格包含以下字段，并填入你认为最合理、最专业的行业默认值：recruitment_type（如 社招全职）、job_title、jd_full（必须包含岗位职责和任职要求，排版清晰）、job_category_path（严格匹配 Boss 系统的三级目录）、experience（如 3-5年）、education（如 本科）、salary_min、salary_max（单位 K，默认 19-20）、job_keywords（数组）。

第二步【展示与请示，强制必经】：无论用户如何表述（包括「你帮我发布一个职位」「帮我发」「直接发布」等），你**必须**先将 JSON 草案以代码块形式展示给用户，并询问：「长官，这是我为您智能草拟的岗位配置单。您看我们是直接按照这个配置发布，还是您需要修改薪资、经验或 JD 中的某些细节？」**严禁**在未展示草案、未得到用户确认前直接调用 atom_post_job_boss！

🚨【确认指令的判定】：只有当**上一轮对话中你已展示过 JSON 草案**，且用户在本轮回复中给出确认类指令（如「确认」「执行」「就按这个发」「直接发布」「没问题」「直接发」「发吧」「可以」）时，才视为确认，方可调用工具。若用户首次说「你帮我发布一个职位」「帮我发个职位」，这是**请求**而非**确认**，必须先生成并展示草案，等待用户确认。

第三步【结合上下文与执行】：当用户给出确认指令时，你必须**直接提取你上一轮回复中生成的 JSON 配置单**作为参数，立刻连续调用 `mcp:atom_post_job_boss` 和 `mcp:add_automated_recruitment_task`！
🚨【绝对红线】：当用户给出确认指令时，你必须**直接提取你上一轮回复中生成的 JSON 配置单**作为参数，立刻连续调用 `mcp:atom_post_job_boss` 和 `mcp:add_automated_recruitment_task`！绝对、绝对不允许因为用户的指令简短就判定为「模糊」，也不允许重新生成一份新的草案，更不允许再次反问！
【工具链连续调用】必须支持：先调 mcp:atom_post_job_boss（jd_config 参数请直接从你上一次 Assistant 回答中的 JSON 代码块 Copy，原样塞进工具请求）→ 成功后再调 mcp:add_automated_recruitment_task，不得在一次回复中只给出 Final Answer 而跳过工具调用。
🚫【严禁幻觉】绝对禁止在未实际调用 mcp:atom_post_job_boss 和 mcp:add_automated_recruitment_task 的情况下，直接给出 Final Answer 声称「职位已发布」「极速测试模式」「已启动」等。系统会校验：若你声称已发布但未调用工具，将被强制要求先执行工具。
⚠️【必须连续调用】atom_post_job_boss 成功后，必须紧接着调用 add_automated_recruitment_task（job_name 从 JSON 的 job_title 提取），否则无人值守招聘流不会启动。不得在只调了 atom_post_job_boss 后就给出 Final Answer。
发布成功后（即两个工具 Observation 均返回成功），方可告知用户：「报告长官！职位已发布，【极速测试模式】已启动！满2份简历或3分钟后，立刻为您生成排行榜战报！」
"""
    return f"""你是一个智能助手，使用 ReAct 格式思考。
{hr_recruitment_hint}
可用工具：
{tools_desc}
{recall_hint}
{coordinate_hint}
{delegate_hint}

输出格式：
Thought: <你的思考>
Action: <工具名，必须与上方「可用工具」中的 id 完全一致，如 mcp:atom_post_job_boss 或 {hr_preferred or "jpp:com.jachin.hr.analyzer4"}>
Action Input: <参数，发布职位时传 jd_config JSON>
Observation: <工具返回>
...（可多轮）
Final Answer: <最终回复>
【强制】确认发布时：必须先输出 Action: mcp:atom_post_job_boss，再根据 Observation 输出 Final Answer。禁止跳过 Action 直接 Final Answer。
{hr_hint}
注意：工具执行后务必给出 Final Answer。禁止对 Observation 进行总结、概括或改写；若 Observation 已是完整报告，必须原样完整输出。HR 透析镜执行后，Final Answer 必须以「✅ 执行成功，本次分析了 X 份简历」开头（X 从 Observation 提取），再输出完整报告。
"""


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
    from l3_node.skills.loader import _build_allowed_ids as _loader_ids
    return _loader_ids(allowed_skills)


async def _run_sub_agent(
    task_spec: dict[str, Any],
    engine: LiteLLMEngine,
) -> str:
    """运行子 Agent，完成指定子任务。内部调用 _spawn_sub_agent_async（一次性，不复用）。"""
    role = (task_spec.get("role") or "default").lower()
    task = task_spec.get("task", "")
    result, _ = await _spawn_sub_agent_async(role, task, engine)
    return result


async def _spawn_sub_agent_async(
    role: str,
    task: str,
    engine: LiteLLMEngine,
    sub_agent_id: Optional[str] = None,
) -> tuple[str, str]:
    """异步版 spawn_sub_agent，供 delegate 流程调用。"""
    switches = _get_service_switches()
    if switches is not None:
        if len(switches) == 0 or role.lower() not in switches:
            return "当前子账号未开启该项服务支持", ""
    role_lower = (role or "default").lower()
    prompt = SUB_AGENT_PROMPTS.get(role_lower, SUB_AGENT_PROMPTS["default"])
    allowed = SUB_AGENT_ALLOWED_SKILLS.get(role_lower, SUB_AGENT_ALLOWED_SKILLS["default"])
    global_allowed = _get_allowed_skills()
    if global_allowed is not None:
        allowed = [s for s in allowed if s in _build_allowed_ids(global_allowed)]

    if sub_agent_id and sub_agent_id in _sub_agent_registry:
        agent = _sub_agent_registry[sub_agent_id]
        result = await agent.run_once(task, engine)
        return result, sub_agent_id

    sid = sub_agent_id or f"sub-{uuid.uuid4().hex[:8]}"
    agent = SubAgent(sid, prompt, allowed)
    _sub_agent_registry[sid] = agent
    result = await agent.run_once(task, engine)
    return result, sid


async def _run_react_core(
    ctx: PipelineContext,
    engine: LiteLLMEngine,
    on_step: Optional[Callable[[str, str, str], None]] = None,
) -> None:
    skills = ctx.metadata.get("_skills") or []
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

    # 追踪本轮已执行的招聘相关工具，用于拒绝「未调用工具却声称已发布」的幻觉回复
    ctx._executed_tools_this_run = set()

    for iteration in range(max_iterations):
        ctx.current_response = ""
        ctx.parsed_action = None
        ctx.observation = ""

        await global_hooks.run(HOOK_BEFORE_LLM_THINK, ctx)
        if ctx.aborted:
            return

        full_messages = [{"role": "system", "content": ctx.system_prompt}] + messages
        logger.debug("[L3 Agent] ReAct iter=%d 调用 LLM stream=%s", iteration + 1, bool(on_chunk))
        if on_chunk:
            response = await engine.generate_response_stream(
                full_messages, chunk_callback=on_chunk,
                temperature=0.7, max_tokens=16384,
            )
        else:
            result = await engine.generate_response(
                full_messages, temperature=0.7, max_tokens=16384,
            )
            response = result.get("content", result) if isinstance(result, dict) else str(result)

        ctx.current_response = response

        thought = re.search(
            r"Thought:\s*(.+?)(?=Action:|Final Answer:|Answer:|\n\n|$)",
            response, re.DOTALL | re.IGNORECASE,
        )
        if thought:
            _emit("thought", thought.group(1).strip())

        parsed = _parse_action(response, skills, use_mock=use_mock, allowed_skills=allowed_skills)
        ctx.parsed_action = parsed

        if parsed is None:
            if "Final Answer:" in response or "Answer:" in response:
                for prefix in ("Final Answer:", "Answer:"):
                    idx = response.lower().find(prefix.lower())
                    if idx >= 0:
                        ans = response[idx + len(prefix):].strip()
                        if ans:
                            # 校验：招聘工具链必须完整调用
                            has_success = "职位已发布" in ans or "JOB_" in ans or "TASK_AUTO" in ans or "极速测试模式" in ans or "已启动" in ans
                            no_post = "atom_post_job_boss" not in ctx._executed_tools_this_run
                            no_task = "add_automated_recruitment_task" not in ctx._executed_tools_this_run
                            if has_success and no_post:
                                logger.warning(
                                    "[L3 Agent] 拒绝幻觉回复：声称职位已发布但未调用 atom_post_job_boss，强制要求先执行工具"
                                )
                                messages.append({"role": "assistant", "content": response})
                                messages.append({
                                    "role": "user",
                                    "content": "【系统校验】你声称职位已发布，但未实际调用 mcp:atom_post_job_boss。请立即输出 Action: mcp:atom_post_job_boss，Action Input 为上一轮 JSON 配置单（从你之前的 Assistant 回复中提取），不得直接给出 Final Answer。",
                                })
                                continue
                            if has_success and no_task:
                                logger.warning(
                                    "[L3 Agent] 招聘工具链不完整：已调用 atom_post_job_boss 但未调用 add_automated_recruitment_task，强制要求开启无人值守"
                                )
                                messages.append({"role": "assistant", "content": response})
                                messages.append({
                                    "role": "user",
                                    "content": "【系统校验】你已发布职位但未开启无人值守招聘流。请立即输出 Action: mcp:add_automated_recruitment_task，Action Input 为 {\"job_name\": \"岗位名称\"}（从上一轮 JSON 配置单的 job_title 提取），不得直接给出 Final Answer。",
                                })
                                continue
                            _emit("answer", ans)
                            ctx.final_answer = ans
                            messages.append({"role": "assistant", "content": response})
                            return
            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": "请给出最终回复，以 Final Answer: 开头。"})
            continue

        if parsed["type"] == "answer":
            ans = parsed.get("content", response)
            # 校验：招聘工具链必须完整调用
            has_success = any(
                k in (ans or "") for k in ("职位已发布", "JOB_", "TASK_AUTO", "极速测试模式", "已启动")
            )
            no_post = "atom_post_job_boss" not in ctx._executed_tools_this_run
            no_task = "add_automated_recruitment_task" not in ctx._executed_tools_this_run
            if has_success and no_post:
                logger.warning(
                    "[L3 Agent] 拒绝幻觉回复：声称职位已发布但未调用 atom_post_job_boss，强制要求先执行工具"
                )
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": "【系统校验】你声称职位已发布，但未实际调用 mcp:atom_post_job_boss。请立即输出 Action: mcp:atom_post_job_boss，Action Input 为上一轮 JSON 配置单（从你之前的 Assistant 回复中提取），不得直接给出 Final Answer。",
                })
                continue
            if has_success and no_task:
                logger.warning(
                    "[L3 Agent] 招聘工具链不完整：已调用 atom_post_job_boss 但未调用 add_automated_recruitment_task，强制要求开启无人值守"
                )
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": "【系统校验】你已发布职位但未开启无人值守招聘流。请立即输出 Action: mcp:add_automated_recruitment_task，Action Input 为 {\"job_name\": \"岗位名称\"}（从上一轮 JSON 配置单的 job_title 提取），不得直接给出 Final Answer。",
                })
                continue
            _emit("answer", ans)
            ctx.final_answer = ans
            messages.append({"role": "assistant", "content": response})
            return

        # delegate：分身子 Agent 并行执行
        if parsed["type"] == "delegate":
            sub_tasks = parsed.get("sub_tasks", [])
            _emit("action", f"delegate {len(sub_tasks)} 个子任务")
            await global_hooks.run(HOOK_BEFORE_TOOL_EXEC, ctx)
            if ctx.aborted:
                return
            results = await asyncio.gather(
                *[_run_sub_agent(t, engine) for t in sub_tasks],
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
            ctx.observation = observation
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
            _emit("action", f"{tool} {inp}".strip())
            await global_hooks.run(HOOK_BEFORE_TOOL_EXEC, ctx)
            if ctx.aborted:
                return
            # 记录已执行的招聘工具，用于校验幻觉回复
            base_tool = (tool or "").replace("mcp:", "").strip()
            if base_tool in ("atom_post_job_boss", "add_automated_recruitment_task"):
                ctx._executed_tools_this_run.add(base_tool)
            # 工具执行路由器：MCP 工具（L3 本地 read_file 或 L2 代理），本地工具走 run_tool
            mcp_registry = get_mcp_registry()
            if tool in mcp_registry.known_mcp_tools:
                observation = await mcp_registry.invoke(tool, inp)
            else:
                observation = run_tool(tool, inp, allowed_skills=allowed_skills)
            ctx.observation = observation
            await global_hooks.run(HOOK_AFTER_TOOL_EXEC, ctx)
            _emit("observation", observation)
            # 工具返回已是完整报告（如 HR 透析镜）时直接作为最终答案，禁止 LLM 二次总结导致截断
            obs = (observation or "").strip()
            if len(obs) > 500 and ("## " in obs or "**" in obs or "综合评分" in obs or "录用建议" in obs or "评估" in obs):
                ctx.final_answer = obs
                if on_step:
                    on_step("answer", ctx.final_answer, ctx.run_id)
                return
            messages.append({"role": "assistant", "content": response})
            messages.append({
                "role": "user",
                "content": f"Observation: {observation}\n\n请根据观察继续思考，或给出 Final Answer（若 Observation 已是完整报告，直接完整引用，禁止总结或截断）:",
            })
            continue

    # 循环结束仍未产出：最后一轮兜底
    if ctx.observation:
        obs = (ctx.observation or "").strip()
        # Observation 已是完整报告（如 HR 透析镜输出）时直接使用，避免 LLM 二次总结导致截断
        if len(obs) > 800 and ("## " in obs or "**" in obs or "综合评分" in obs or "录用建议" in obs):
            ctx.final_answer = obs
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
            result = await engine.generate_response(full_m, temperature=0.3, max_tokens=16384)
            resp = result.get("content", result) if isinstance(result, dict) else str(result)
            for pat in (r"Final\s+Answer:\s*(.+?)(?:\n\n|$)", r"Answer:\s*(.+?)(?:\n\n|$)"):
                m = re.search(pat, resp, re.DOTALL | re.IGNORECASE)
                if m:
                    ctx.final_answer = m.group(1).strip()
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
                ctx.final_answer = m.group(1).strip()
                return
    ctx.final_answer = "[ReAct 循环达到上限]"


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
) -> str:
    """
    运行 L3 单体 ReAct 循环。
    支持 _system_prompt_override 供子 Agent 使用。
    _session_messages: 若提供，将作为历史上下文并在调用结束后被更新为完整对话（含本轮），供多轮对话复用。
    """
    run_id = str(uuid.uuid4())
    logger.debug("[L3 Agent] run_agent 开始 input_len=%d history=%d", len(user_input or ""), len(_session_messages or []) + len(_initial_messages or []))
    allowed = _get_allowed_skills()
    tools = load_tools(allowed_skills=allowed)
    # 神经桥接：从 L2 拉取 MCP 工具并合并（强容错，L2 不可用时仅用本地工具）
    try:
        mcp_registry = get_mcp_registry()
        mcp_tools = await mcp_registry.fetch_tools_from_l2()
        if mcp_tools:
            tools = list(tools) + mcp_tools
            logger.info("[L3 Agent] 已合并 %d 个 MCP 工具，总计 %d", len(mcp_tools), len(tools))
    except Exception as e:
        logger.debug("[L3 Agent] MCP 工具拉取跳过（L2 可能未启动）: %s", e)
    system_prompt = _system_prompt_override or _build_system_prompt(tools=tools, allow_delegate=True)
    # 优先使用 _session_messages（多轮对话），否则用 _initial_messages
    if _session_messages is not None:
        messages = list(_session_messages)
    elif _initial_messages:
        messages = list(_initial_messages)
    else:
        messages = []
    messages.append({"role": "user", "content": user_input})

    ctx = PipelineContext(
        intent=user_input,
        source="l3_agent",
        run_id=run_id,
        metadata={
            "_skills": tools,
            "_use_mock": False,
            "_max_iterations": max_iterations,
            "_on_step": on_step,
            "_on_chunk": on_chunk,
        },
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
    await pipeline.execute(ctx)

    # 多轮对话：将完整对话写回 _session_messages，供下一轮复用（含上一轮 Assistant 的 JSON 草案等）
    if _session_messages is not None:
        _session_messages.clear()
        # 保留最近 30 条消息，避免 token 溢出，同时确保「确认」等上下文可追溯
        recent = ctx.messages[-30:] if len(ctx.messages) > 30 else ctx.messages
        _session_messages.extend(recent)

    return ctx.final_answer or "[未产出回复]"


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
            await asyncio.wait([self._stop.wait(), asyncio.sleep(self.interval)], return_when=asyncio.FIRST_COMPLETED)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._loop())
            logger.info("[MemorySyncDaemon] 已启动，间隔 %.0fs", self.interval)

    def stop(self) -> None:
        self._stop.set()
        if self._task and not self._task.done():
            self._task.cancel()
