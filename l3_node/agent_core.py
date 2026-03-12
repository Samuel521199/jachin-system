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
    return None


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


LAST_JD_PENDING_PATH = Path.home() / ".jachin" / "l3_last_jd_pending.json"
_JD_PENDING_TTL_SEC = 7200  # 2 小时内有效


def _save_last_jd_pending(jd_config: dict) -> None:
    """当 Agent 输出待确认的 JD 时保存，供「同意」无会话时兜底"""
    if not jd_config or not isinstance(jd_config, dict):
        return
    if not (jd_config.get("job_title") or jd_config.get("jd_full")):
        return
    try:
        LAST_JD_PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
        LAST_JD_PENDING_PATH.write_text(
            json.dumps({"jd_config": jd_config, "updated_at": __import__("time").time()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("[Agent] 已保存待确认 JD 至 fallback，job_title=%s", jd_config.get("job_title", ""))
    except Exception as e:
        logger.debug("[Agent] 保存 last_jd_pending 失败: %s", e)


def _load_last_jd_pending() -> dict | None:
    """加载最近待确认的 JD（chat_id 会话失效时兜底），超时返回 None"""
    if not LAST_JD_PENDING_PATH.exists():
        return None
    try:
        data = json.loads(LAST_JD_PENDING_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        jd = data.get("jd_config")
        ts = data.get("updated_at", 0)
        if isinstance(jd, dict) and (jd.get("job_title") or jd.get("jd_full")):
            age = __import__("time").time() - ts if ts else 999999
            if age < _JD_PENDING_TTL_SEC:
                return jd
    except Exception as e:
        logger.debug("[Agent] 加载 last_jd_pending 失败: %s", e)
    return None


def _clear_last_jd_pending() -> None:
    """发布成功后清除，避免误用"""
    try:
        if LAST_JD_PENDING_PATH.exists():
            LAST_JD_PENDING_PATH.unlink()
    except Exception:
        pass


async def _execute_publish_bypass(jd_config: dict) -> str | None:
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
        mcp_registry = get_mcp_registry()
        inp = json.dumps({"jd_config_path": path, "cdp_url": "http://127.0.0.1:9222"}, ensure_ascii=False)
        obs = await mcp_registry.invoke("mcp:atom_post_job_boss", inp)
        result = json.loads(obs) if (obs or "").strip().startswith("{") else {}
        if not result.get("posted", False) and "需要登录" in str(result.get("error", "")):
            return "已为您打开 Boss 直聘登录页，请扫码登录。登录完成后请回复「已登录」或「继续发布」。"
        if not result.get("posted", False):
            logger.warning("[Agent] 直接发布未成功: %s", result.get("error", obs)[:200])
            return None
        _clear_last_jd_pending()
        task_inp = json.dumps({"job_name": job_title}, ensure_ascii=False)
        await mcp_registry.invoke("mcp:add_automated_recruitment_task", task_inp)
        return "职位发布成功！【无人值守流程】已启动：推荐牛人每15分钟（满3人打招呼即停）→20秒后自动抓简历→满4份简历触发 Agent 讨论简历，输出前2名排行榜和 Lark 多维表，达标后停止该岗位招聘。"
    except Exception as e:
        logger.warning("[Agent] 直接发布异常: %s", e)
        return None


def _persist_jd_config_before_publish(jd_config: dict) -> str | None:
    """
    HR 同意后、打开 Chrome 发布前【必须自动先执行】：创建 data/{岗位名}/、复制模板填 jd.json、
    创建 pending/processed/result、排行榜_Summary.md。完成后返回 jd_config_path 供后续发布使用。
    """
    if not jd_config or not isinstance(jd_config, dict):
        return None
    job_title = (jd_config.get("job_title") or "").strip()
    if not job_title:
        return None
    try:
        _proj = Path(__file__).resolve().parent.parent
        plugin_root = _proj / "skills_repo" / "plugin" / "2-track-a-atomic-mcp"
        if not plugin_root.exists():
            logger.warning("[Agent] plugin 路径不存在，无法持久化 JD 配置")
            return None
        import sys
        if str(plugin_root) not in sys.path:
            sys.path.insert(0, str(plugin_root))
        from tools.hr_data_paths import init_job_jd_from_template
        jd_path = init_job_jd_from_template(job_title, overrides=jd_config)
        logger.info("[Agent] HR 已确认，已自动创建 data/%s/、复制模板填 jd.json、创建 pending/processed/result", job_title)
        return str(jd_path)
    except Exception as e:
        logger.warning("[Agent] 持久化 JD 配置失败: %s", e)
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
【HR 招聘总监 SOP】你是 Jachin OS 的首席 AI 招聘总监。**触发**：当用户说「我要发布职位」「招聘」「我要招」「发布一个XXX工程师职位」等时，一律走本 SOP。

🚨【绝对红线·严禁违反】禁止臆想、禁止杜撰、禁止在未从 HR 处获取到明确回复前自行填充任何配置。所有硬性字段必须由 HR 明确告知，你不得凭空填写。**在 HR 明确回复「同意」或点击确认之前，绝对禁止调用 atom_post_job_boss 与 add_automated_recruitment_task。**

【第一步：首次综合询问】当 HR 只说「我要招聘」「发布职位」「我要招人」等模糊指令时，**第一轮必须纯询问，禁止调用任何发布工具**。统一询问：
1. **岗位名称**是什么？
2. **招聘类型**：社招全职 / 应届生校园招聘 / 实习生招聘 / 兼职招聘？
3. **薪资待遇**大概多少？（例如：20-35K/月）
4. **学历要求**？（本科/硕士等）
5. **经验要求**？（不限/1年以内/1-3年/3-5年等）
若 HR 第一轮未给出某项，**必须单独再发一条**追问该项，例如：「您是要社招、校招、实习还是兼职呀？」「薪资范围大概多少？」直到收集齐全部硬性字段。

【第二步：硬性字段与选项映射】HR 可用模糊自然语言，你需认真解析为合规配置值。**若解析不确定，单独再问 HR**。
- **recruitment_type**：只能选其一填入 `社招全职` | `应届生校园招聘` | `实习生招聘` | `兼职招聘`。映射：正式工/全职/社招→社招全职；校招/应届生→应届生校园招聘；实习→实习生招聘；兼职→兼职招聘。
- **job_title**：必须询问 HR 后如实填入。
- **jd_full**：根据 job_title 与已收集信息用 AI 生成完整 JD（岗位职责+任职要求+薪资待遇），**发给 HR 检查**，问是否可行；如有修改，**按 HR 说的改**。
- **experience**：只能选其一填入 `不限` | `1年以内` | `1-3年` | `3-5年` | `5-10年` | `10年以上`。映射：应届/无经验→1年以内；1到3年/1-3年→1-3年。
- **education**：只能选其一填入 `高中` | `大专` | `本科` | `硕士` | `博士`。映射：本科及以上→本科；研究生→硕士。
- **salary_min、salary_max**：询问 HR 薪资范围，解析为数字（单位 K）。若未给，**单独追问**：「薪资范围大概多少？」
- **job_keywords**：你可根据 job_title 与 jd_full 自行填写关键词数组。
- **job_category_path**：根据 job_title 解析为 Boss 三级目录，如 `["互联网/AI", "后端开发", "Java开发工程师"]`。

【第三步：统一输出与确认】收集齐所有硬性信息并完成 jd_full、job_keywords、job_category_path 的 AI 补充后，**将完整 JD 配置以 ```json ... ``` 代码块形式统一输出**给 HR，附上「请您确认以上配置无误。确认后请回复「同意」或点击确认，我将立即为您发布。」**在 HR 明确同意前，禁止调用发布工具。**

【第四步：同意后自动执行】当 HR 回复「同意」「确认」「确认发布」「就按这个发」「直接发布」时，**立即**输出 Action: mcp:atom_post_job_boss，Action Input 填 {\"jd_config\": {...}}。系统将**自动**执行：① 在 data/ 下新建以岗位名为名的文件夹；② 复制 jd_to_publish.example.json 为 jd.json 并填入 HR 确认内容；③ 创建 pending、processed、result 子目录；④ 打开 Chrome 发布职位。**无需 HR 额外操作，你不得等待、不得再询问。**

【Chrome 与登录】若 Observation 返回「需要登录」「请扫码登录」，原样告知 HR：「已为您打开 Boss 直聘登录页，请扫码登录。登录完成后请回复「已登录」或「继续发布」。」当 HR 回复「已登录」「继续发布」后，**再次调用** atom_post_job_boss，传入上一轮展示的 JSON。

【发布成功提醒】职位发布成功后，给 HR 发送：「职位发布成功！【无人值守流程】已启动：推荐牛人每15分钟（满3人打招呼即停）→20秒后自动抓简历→满4份简历触发 Agent 讨论简历，输出前2名排行榜和 Lark 多维表，达标后停止该岗位招聘。」

【关闭流程】当 HR 说「关闭」「停止」「取消」招聘、无人值守、自动化流程时，**必须立即**输出 Action: mcp:stop_automated_recruitment，Action Input 为 {\"job_name\": \"\"}。**禁止**仅回复「已关闭」却不实际调用工具。
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
Action: <工具名，必须与上方「可用工具」中的 id 完全一致，如 {hr_preferred or "jpp:com.jachin.hr.analyzer4"}>
Action Input: <参数>
Observation: <工具返回>
...（可多轮）
Final Answer: <最终回复>
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

        # 一旦 LLM 输出含 JD 配置，立即写入 fallback，供 Lark 会话丢失时「同意」兜底
        _jd_raw = _extract_jd_config_from_conversation(messages, response)
        if _jd_raw:
            try:
                _jd_obj = json.loads(_jd_raw)
                _jd = _jd_obj.get("jd_config") if isinstance(_jd_obj, dict) else None
                if isinstance(_jd, dict):
                    _save_last_jd_pending(_jd)
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

        if parsed is None:
            # 兜底：用户回复「同意」但 LLM 误判为「没有配置」时，从对话中提取 JD 并强制要求调用发布工具
            last_user_content = ""
            for m in reversed(messages or []):
                if isinstance(m, dict) and m.get("role") == "user":
                    last_user_content = (m.get("content") or "").strip()
                    break
            if re.search(r"同意|确认|确认发布|就按这个发|直接发布", last_user_content):
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
            base_tool = (tool or "").replace("mcp:", "").strip()
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
            _emit("action", f"{tool} {inp[:200]}{'...' if len(inp or '') > 200 else ''}".strip())
            await global_hooks.run(HOOK_BEFORE_TOOL_EXEC, ctx)
            if ctx.aborted:
                return
            # 记录已执行的招聘工具，用于校验幻觉回复
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
            # atom_post_job_boss 发布成功后清除 fallback，避免下次误用
            if base_tool == "atom_post_job_boss":
                try:
                    raw = (observation or "").strip()
                    if raw.startswith("{"):
                        _obs_obj = json.loads(raw)
                        if _obs_obj.get("posted", False):
                            _clear_last_jd_pending()
                except Exception:
                    pass
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

    # 预检1：用户说「我要招聘」等模糊指令且对话中尚无 JD 配置时，强制依次询问所有硬性字段
    _vague_recruitment = re.search(
        r"我要(?:招聘|发布|招人?)|发布(?:一个)?职位|招聘",
        (user_input or "").strip(),
    )
    _has_jd_in_history = bool(_extract_jd_config_from_conversation(messages, ""))
    if _vague_recruitment and not _has_jd_in_history:
        prefix = "【系统】用户要发布职位，但尚未提供完整配置。你必须**仅做询问**，禁止臆想、禁止杜撰、禁止调用 atom_post_job_boss。请用 Final Answer 向 HR 依次询问：1.岗位名称是什么？2.社招、校招、实习还是兼职？3.薪资待遇大概多少？4.学历要求？5.经验要求？若 HR 第一轮未给某项，下一轮**单独追问**该项，直到收集齐再输出完整 JD 配置供确认。\n\n"
        messages[-1]["content"] = prefix + (user_input or "")

    # 预检2：用户说「关闭」「停止」招聘流程时，强制要求调用 stop_automated_recruitment，禁止仅回复文字
    if re.search(r"关闭|停止|取消", (user_input or "").strip()) and re.search(r"招聘|无人值守|自动化", (user_input or "").strip()):
        prefix = "【系统】用户要求关闭招聘流程。你必须输出 Action: mcp:stop_automated_recruitment，Action Input: {\"job_name\": \"\"}，以真正停止后台任务。禁止仅回复「已关闭」而不调用工具。\n\n"
        messages[-1]["content"] = prefix + (messages[-1].get("content") or "")

    # 预检3：用户回复「同意」时，若有 JD 配置则直接执行发布，不经过 LLM（彻底避免循环）
    _agree_match = re.search(r"同意|确认|确认发布|就按这个发|直接发布", (user_input or "").strip())
    _agree_jd_cfg = None
    if _agree_match:
        fallback = _extract_jd_config_from_conversation(messages, "")
        if fallback:
            try:
                obj = json.loads(fallback)
                _agree_jd_cfg = obj.get("jd_config") if isinstance(obj, dict) else obj
                if not isinstance(_agree_jd_cfg, dict):
                    _agree_jd_cfg = None
            except Exception:
                pass
        if not _agree_jd_cfg:
            _agree_jd_cfg = _load_last_jd_pending()
            if _agree_jd_cfg:
                logger.info("[Agent] 同意但无会话，从 fallback 恢复 JD job_title=%s，直接执行发布", _agree_jd_cfg.get("job_title"))
        # 只要拿到 JD，一律直接发布，不再交给 LLM（防止误判「新对话」导致循环）
        if _agree_jd_cfg:
            _direct_publish = await _execute_publish_bypass(_agree_jd_cfg)
            if _direct_publish:
                return _direct_publish
            # 直接发布失败时，注入 JD 让 LLM 兜底调用 atom_post_job_boss
            _jd_str = json.dumps({"jd_config": _agree_jd_cfg}, ensure_ascii=False)
            prefix = "【系统】用户已确认以下 JD，请直接调用 mcp:atom_post_job_boss，Action Input 填：{}\n勿输出「没有配置」或「新对话」类提示。\n\n".format(_jd_str)
            messages[-1]["content"] = prefix + (user_input or "")

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
        # 若本轮输出含待确认 JD，写入 fallback，供 Lark 会话丢失时「同意」兜底
        _pending = _extract_jd_config_from_conversation(recent, ctx.current_response or "")
        if _pending:
            try:
                obj = json.loads(_pending)
                jd = obj.get("jd_config") if isinstance(obj, dict) else None
                if isinstance(jd, dict):
                    _save_last_jd_pending(jd)
            except Exception:
                pass

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
