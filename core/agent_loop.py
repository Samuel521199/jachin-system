"""
Jachin Nexus Layer 2 - 自主代理循环 (ReAct) v8.0

Reason + Act：四大原语路由（MCP / Skills / Tools）+ Fallback + HITL 安全红线。
v8.0 Nexus Hook Pipeline：ReAct 逻辑封装为中间件，pre_intent/pre_llm/post_tool/pre_response 可扩展。
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from rich.console import Console
from rich.logging import RichHandler

from core.agent_memory import add_memory, get_context
from core.biological_memory import add_short_term, get_core_memory_for_prompt
from core.hooks_pipeline import (
    HOOK_AFTER_TOOL_EXEC,
    HOOK_BEFORE_LLM_THINK,
    HOOK_BEFORE_RESPONSE,
    HOOK_BEFORE_TOOL_EXEC,
    HOOK_ON_INTENT_RECEIVED,
    Pipeline,
    PipelineContext,
    global_hooks,
)
import core.swarm_hook  # noqa: F401 — 注册 Edge Mesh Swarm Hook
import core.compaction_hook  # noqa: F401 — 注册神盾 Compaction Hook

logger = logging.getLogger(__name__)
console = Console()


class SecurityException(Exception):
    """用户拒绝授权时抛出"""
    pass

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FUEL_LIMIT = 100_000
MAX_REACT_ITERATIONS = 5  # 战役 1：防死锁，最大 5 次

# Mock 工具（无 Wasm 技能时）：get_weather, read_local_file, video_encode（Swarm 外包）
MOCK_TOOLS = [
    {"label": "get_weather", "type": "mock", "desc": "查询天气"},
    {"label": "read_local_file", "type": "mock", "desc": "读取本地文件"},
    {"label": "video_encode", "type": "mock", "desc": "视频转码（重载任务，由 Edge Mesh 虫群节点执行）"},
    {"label": "core:handoff", "type": "handoff", "desc": "当遇到超出当前人设专业领域的问题时，移交控制权给更专业的专家。可选: architect, researcher, default"},
]


def _extract_skills_from_blueprint(ast_json: dict) -> list[dict[str, Any]]:
    """
    从蓝图 AST 提取 Processor 节点作为 Wasm 技能列表。

    Returns:
        [{"label": "天气查询", "wasm_path": "/path/to/plugin.wasm", "fuel_limit": 100000}, ...]
    """
    nodes = ast_json.get("nodes") or []
    skills = []
    for n in nodes:
        if (n.get("type") or "").lower() != "processor":
            continue
        data = n.get("data") or {}
        label = data.get("label") or "未命名技能"
        wasm_path = data.get("wasm_path")
        fuel = data.get("fuel_limit", DEFAULT_FUEL_LIMIT)

        # 解析 wasm_path
        if wasm_path:
            p = Path(wasm_path)
            if not p.is_absolute():
                p = _PROJECT_ROOT / wasm_path
            if p.exists():
                skills.append({
                    "label": label,
                    "wasm_path": str(p),
                    "fuel_limit": fuel,
                })
        else:
            # v8.0: vector_router.match_local_skill() 可接管意图→技能匹配，此处保留 fallback
            # 已移除死板的默认路径遍历 (plugins/dummy.wasm, plugins/hello.wasm)
            pass
    return skills


def _build_system_prompt(
    skills: list[dict[str, Any]],
    use_mock: bool = False,
    persona_name: str = "default",
) -> str:
    """动态组装系统 Prompt：核心记忆 + 人设 + 技能武器列表。persona_name 供 Handoff 切换。"""
    core_mem = get_core_memory_for_prompt(limit=20)
    core_prefix = f"{core_mem}\n\n" if core_mem else ""

    from core.personas import get_persona
    persona_text = get_persona(persona_name)

    skill_md_list = [s for s in skills if s.get("type") == "skill_md"]
    wasm_list = [s for s in skills if s.get("type") != "skill_md"]

    if skill_md_list:
        # Skills：SKILL.md 技能，注入完整内容 + Native Core 工具
        skill_content = skill_md_list[0].get("skill_content", "")
        skills_desc = f"""## 当前激活技能（SKILL.md）

{skill_content}

## 可用工具（Native Core，权限限于 ~/.jachin/workspace/）
- core:fs_read(file_path) — 读取文件，路径需在 workspace 内
- core:shell_exec(command) — 执行 Shell 命令，工作目录为 workspace

输出格式：
Action: core:fs_read
Action Input: target.txt

或
Action: core:shell_exec
Action Input: ls -la

或（当遇到硬核代码/架构问题超出能力时）
Action: core:handoff
Action Input: architect
"""
    elif use_mock or not skills:
        lines = []
        for i, t in enumerate(MOCK_TOOLS, 1):
            lines.append(f"  {i}. {t['label']} - {t['desc']}")
        # 无技能命中时仍提供 Native Core 兜底，支持 ls/目录扫描等基础操作
        skills_desc = "你可以使用以下工具：\n" + "\n".join(lines)
        skills_desc += """

## Native Core 兜底（执行命令、读文件时优先使用）
- core:fs_read(file_path) — 读取文件，路径需在 ~/.jachin/workspace/ 内
- core:shell_exec(command) — 执行 Shell 命令，工作目录为 workspace

当需要列出目录、执行 ls 等命令时，使用：Action: core:shell_exec   Action Input: ls -la
当需要读取文件时，使用：Action: core:fs_read   Action Input: <文件路径>"""
        skills_desc += """

## Cognitive Swarm 接力（Handoff）
- core:handoff(expert_name) — 当遇到超出当前专业领域的问题时，移交控制权。可选: architect, researcher, default
  示例：Action: core:handoff   Action Input: architect"""
        skills_desc += "\n\n当需要执行工具时，请输出：Action: <工具名>   Action Input: <参数>"
    else:
        lines = []
        for i, s in enumerate(wasm_list, 1):
            lines.append(f"  {i}. {s['label']} (wasm_path: {s.get('wasm_path', '')})")
        skills_desc = "你可以使用以下 Wasm 技能：\n" + "\n".join(lines)
        skills_desc += "\n\n当需要执行技能时，请输出：Action: run <技能名称或序号>"
    skills_desc += "\n任务完成时，请输出：Final Answer: <最终回复>"

    return f"""{core_prefix}{persona_text}{skills_desc}

请严格使用 Thought, Action, Action Input, Observation 的格式进行思考和调用：
1. Thought: 分析当前情况，决定下一步
2. 如需执行工具：Action: <工具名>   Action Input: <参数>
3. 收到 Observation 后继续思考，或给出：Final Answer: <回复>

保持简洁，完成任务后务必输出 Final Answer:。"""


def _run_mock_tool(tool_name: str, action_input: str = "") -> str:
    """Mock 工具执行（战役 1：无 Wasm 时的模拟）"""
    tool_name = (tool_name or "").strip().lower()
    if tool_name == "get_weather":
        return "北京 晴 18°C，湿度 45%，适宜出行。"
    if tool_name == "video_encode":
        return "[Swarm] 视频转码应由虫群节点执行，若看到此消息说明未接单"
    if tool_name == "read_local_file":
        raw = (action_input or "").strip() or "~/.jachin/nexus_config.json"
        path = raw
        # 兼容 LLM 输出：提取 target.txt 或 workspace 路径
        if "target.txt" in raw:
            path = str(Path.home() / ".jachin" / "workspace" / "target.txt")
        p = Path(path).expanduser()
        if not p.exists() and ("target" in raw or "workspace" in raw.lower()):
            p = Path.home() / ".jachin" / "workspace" / "target.txt"
        if p.exists():
            try:
                return p.read_text(encoding="utf-8", errors="replace")[:500]
            except Exception as e:
                return f"[读取失败: {e}]"
        return f"[文件不存在: {path}]"
    return f"[未知 Mock 工具: {tool_name}]"


def _run_wasm_skill(wasm_path: str, fuel_limit: int = DEFAULT_FUEL_LIMIT) -> str:
    """同步执行 Wasm 插件，返回结果字符串"""
    try:
        from core.wasm_runner import JachinWasmSandbox
        sandbox = JachinWasmSandbox()
        result = sandbox.run_plugin(wasm_path, fuel_limit=fuel_limit)
        return str(result) if result is not None else "(无返回值)"
    except ImportError:
        return "[Wasm 沙箱未安装，跳过执行]"
    except FileNotFoundError:
        return f"[找不到插件: {wasm_path}]"
    except Exception as e:
        return f"[执行异常: {e}]"


def _parse_action(
    llm_output: str,
    skills: list[dict[str, Any]],
    use_mock: bool = False,
) -> dict[str, Any] | None:
    """
    解析 LLM 输出中的 Action 或 Final Answer。

    Returns:
        {"type": "answer", "content": "..."} 或
        {"type": "action", "skill": {...}} 或
        {"type": "mock", "tool": "get_weather", "input": "..."} 或 None
    """
    text = (llm_output or "").strip()
    # Final Answer: / Answer: 最终回复
    for pattern in (r"Final\s+Answer:\s*(.+?)(?:\n|$)", r"Answer:\s*(.+?)(?:\n|$)"):
        m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if m:
            return {"type": "answer", "content": m.group(1).strip()}

    # Action: <mock 工具名> <参数>（无 Wasm 时）
    if use_mock:
        m = re.search(r"Action:\s*(\w+)(?:\s+(.+?))?(?:\n|$)", text, re.IGNORECASE)
        if m:
            tool, inp = m.group(1).strip().lower(), (m.group(2) or "").strip()
            if tool in ("get_weather", "read_local_file", "video_encode"):
                return {"type": "mock", "tool": tool, "input": inp}

    # Action: core:handoff（Cognitive Swarm 接力）
    if re.search(r"Action:\s*core:handoff\s*(?:\n|$)", text, re.IGNORECASE):
        inp = ""
        mi = re.search(r"Action\s+Input:\s*(.+?)(?:\n\n|\n(?:Thought|Action|Final|$))", text, re.DOTALL | re.IGNORECASE)
        if mi:
            inp = mi.group(1).strip()
        return {"type": "handoff", "tool": "core:handoff", "input": inp or "architect"}

    # Action: core:fs_read / core:shell_exec（SKILL.md Native Core）
    for tool_id in ("core:fs_read", "core:shell_exec"):
        if re.search(rf"Action:\s*{re.escape(tool_id)}\s*(?:\n|$)", text, re.IGNORECASE):
            inp = ""
            mi = re.search(r"Action\s+Input:\s*(.+?)(?:\n\n|\n(?:Thought|Action|Final|$))", text, re.DOTALL | re.IGNORECASE)
            if mi:
                inp = mi.group(1).strip()
            return {"type": "native", "tool": tool_id, "input": inp}

    # Action: run <技能名或序号>（Wasm 技能）
    m = re.search(r"Action:\s*run\s+(.+?)(?:\n|$)", text, re.IGNORECASE)
    if m:
        key = m.group(1).strip()
        wasm_skills = [s for s in skills if s.get("type") != "skill_md"]
        if key.isdigit() and 1 <= int(key) <= len(wasm_skills):
            return {"type": "action", "skill": wasm_skills[int(key) - 1]}
    return None


async def _get_llm_response(
    messages: list[dict[str, str]],
    system_prompt: str,
    chunk_callback: Callable[[str], Awaitable[None]] | None = None,
) -> str:
    """调用认知引擎池。v8.0 流式神经：chunk_callback 非空时使用 generate_response_stream"""
    try:
        from core.llm_provider import CognitiveEngineFactory

        engine = CognitiveEngineFactory.get_engine()
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        if chunk_callback:
            result = await engine.generate_response_stream(
                full_messages,
                chunk_callback=chunk_callback,
                temperature=0.7,
                max_tokens=1024,
                call_purpose="layer2_agent_loop_stream",
            )
        else:
            result = await engine.generate_response(
                full_messages,
                temperature=0.7,
                max_tokens=1024,
                call_purpose="layer2_agent_loop",
            )
        if isinstance(result, dict):
            result = result.get("content", "") or ""
        return (result or "").strip()
    except ValueError as e:
        logger.warning("认知引擎配置错误: %s", e)
        return f"[认知引擎未配置: {e}]"
    except Exception as e:
        logger.warning("LLM 调用失败: %s", e)
        return f"[LLM 调用失败: {e}]"


def _extract_thought(text: str) -> str | None:
    """从 LLM 输出中提取 Thought 内容"""
    m = re.search(r"Thought:\s*(.+?)(?=Action:|Final Answer:|Answer:|\n\n|$)", text, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else None


def _emit_step(
    step_type: str,
    content: str,
    on_step: Callable[[str, str, str], None] | None,
    run_id: str = "",
) -> None:
    """统一 ReAct 步骤输出：回调 + 控制台。v8.0 全链路追踪：run_id 贯穿染色"""
    prefix = f"[RunID:{run_id[:8]}] " if run_id else ""
    if on_step:
        on_step(step_type, content, run_id)
    if step_type == "thought":
        console.print(f"{prefix}[dim cyan]Thought:[/dim cyan] {content[:200]}...")
    elif step_type == "action":
        console.print(f"{prefix}[bold green]Action:[/bold green] {content}")
    elif step_type == "observation":
        console.print(f"{prefix}[yellow]Observation:[/yellow] {content[:300]}...")
    elif step_type == "answer":
        console.print(f"{prefix}[green]Final Answer:[/green] {content[:300]}...")


async def _run_react_core(ctx: PipelineContext) -> None:
    """
    v8.0 ReAct 核心中间件：封装 Thought->Action->Obs 循环。
    在各阶段调用 global_hooks，支持 pre_llm/post_tool 等扩展。
    """
    skills: list[dict[str, Any]] = ctx.metadata.get("_skills") or []
    use_mock: bool = ctx.metadata.get("_use_mock", False)
    max_iterations: int = ctx.metadata.get("_max_iterations", MAX_REACT_ITERATIONS)
    on_step: Callable[[str, str, str], None] | None = ctx.metadata.get("_on_step")
    on_hitl_request: Callable[[str, str], None] | None = ctx.metadata.get("_on_hitl_request")
    messages = ctx.messages

    def _emit(step_type: str, content: str) -> None:
        _emit_step(step_type, content, on_step, ctx.run_id)

    for iteration in range(max_iterations):
        ctx.current_response = ""
        ctx.parsed_action = None
        ctx.observation = ""

        await global_hooks.run(HOOK_BEFORE_LLM_THINK, ctx)
        if ctx.aborted:
            return

        on_chunk = ctx.metadata.get("_on_chunk")
        response = await _get_llm_response(messages, ctx.system_prompt, chunk_callback=on_chunk)
        ctx.current_response = response
        add_memory("assistant", response)
        add_short_term("assistant", response, meta={"iteration": iteration})

        thought = _extract_thought(response)
        if thought:
            _emit("thought", thought)

        parsed = _parse_action(response, skills, use_mock=use_mock)
        ctx.parsed_action = parsed

        if parsed is None:
            if "Final Answer:" in response or "Answer:" in response or "answer:" in response.lower():
                for prefix in ("Final Answer:", "Answer:", "answer:"):
                    for line in response.split("\n"):
                        if line.strip().lower().startswith(prefix.lower()):
                            ans = line.split(":", 1)[1].strip()
                            _emit("answer", ans)
                            ctx.final_answer = ans
                            return
            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": "请给出最终回复，以 Final Answer: 开头。"})
            continue

        if parsed["type"] == "answer":
            ans = parsed.get("content", response)
            _emit("answer", ans)
            add_memory("assistant", ans)
            ctx.final_answer = ans
            return

        if parsed["type"] == "handoff":
            expert_name = (parsed.get("input") or "architect").strip().lower() or "architect"
            _emit("action", f"core:handoff {expert_name}")

            from core.personas import PERSONA_REGISTRY, get_persona

            if expert_name not in PERSONA_REGISTRY:
                expert_name = "architect"
            ctx.system_prompt = _build_system_prompt(skills, use_mock, persona_name=expert_name)
            ctx.metadata["_current_persona"] = expert_name

            display_name = {"architect": "资深架构师", "researcher": "情报分析师", "default": "全能助理"}.get(expert_name, expert_name)
            observation = f"[System] 灵魂传输完成。你现在是 {display_name}。请以新身份继续完成用户的原始请求。"
            console.print(
                f"[bold magenta][🔄 Handoff][/bold magenta] 虫群接力触发！"
                f"当前人格已剥离，【{display_name}】已接管大脑控制权。"
            )
            ctx.observation = observation
            _emit("observation", observation)
            add_memory("system", f"Observation: {observation}")
            add_short_term("system", f"Observation: {observation}", meta={"handoff": expert_name})
            messages.append({"role": "assistant", "content": response})
            messages.append({
                "role": "user",
                "content": f"Observation: {observation}\n\n请以【{display_name}】的身份继续思考，或给出 Final Answer:",
            })
            continue

        if parsed["type"] == "native":
            tool = parsed.get("tool", "")
            inp = parsed.get("input", "")
            _emit("action", f"{tool} {inp}".strip())

            await global_hooks.run(HOOK_BEFORE_TOOL_EXEC, ctx)
            if ctx.aborted:
                return
            if getattr(ctx, "swarm_resolved", False):
                observation = ctx.observation
            else:
                if tool == "core:shell_exec" and on_hitl_request:
                    from core.hitl_registry import register, await_response
                    task_id = str(uuid.uuid4())
                    register(task_id)
                    content = f"core:shell_exec {inp.strip() or 'ls -la'}"
                    on_hitl_request(task_id, content)
                    approved = await await_response(task_id, timeout=300.0)
                    if not approved:
                        raise SecurityException("User Rejected: 指挥官拒绝执行 Shell 命令")

                try:
                    from core.native_tools import dispatch_native_tool
                    if tool == "core:fs_read":
                        path = inp.strip() or "target.txt"
                        result = dispatch_native_tool(tool, file_path=path)
                    elif tool == "core:shell_exec":
                        result = dispatch_native_tool(tool, command=inp.strip() or "ls -la")
                    else:
                        result = "[未知 Native 工具]"
                    observation = str(result) if not isinstance(result, str) else result
                except SecurityException:
                    raise
                except Exception as e:
                    observation = f"[Native 执行异常: {e}]"
            ctx.observation = observation

            await global_hooks.run(HOOK_AFTER_TOOL_EXEC, ctx)

            _emit("observation", observation)
            add_memory("system", f"Observation: {observation}")
            add_short_term("system", f"Observation: {observation}", meta={"tool": tool})
            messages.append({"role": "assistant", "content": response})
            messages.append({
                "role": "user",
                "content": f"Observation: {observation}\n\n请根据观察结果继续思考，或给出 Final Answer:",
            })
            continue

        if parsed["type"] == "mock":
            tool = parsed.get("tool", "")
            inp = parsed.get("input", "")
            _emit("action", f"Mock 工具 {tool} {inp}".strip())

            await global_hooks.run(HOOK_BEFORE_TOOL_EXEC, ctx)
            if ctx.aborted:
                return
            if getattr(ctx, "swarm_resolved", False):
                observation = ctx.observation
            else:
                observation = _run_mock_tool(tool, inp)
            ctx.observation = observation
            await global_hooks.run(HOOK_AFTER_TOOL_EXEC, ctx)

            _emit("observation", observation)
            add_memory("system", f"Observation: {observation}")
            add_short_term("system", f"Observation: {observation}", meta={"tool": tool})
            messages.append({"role": "assistant", "content": response})
            messages.append({
                "role": "user",
                "content": f"Observation: {observation}\n\n请根据观察结果继续思考，或给出 Final Answer:",
            })
            continue

        if parsed["type"] == "action":
            skill = parsed.get("skill")
            if not skill:
                continue
            wasm_path = skill.get("wasm_path", "")
            fuel = skill.get("fuel_limit", DEFAULT_FUEL_LIMIT)
            label = skill.get("label", wasm_path)
            tools_fallback = skill.get("tools")
            _emit("action", f"Wasm 沙箱: {label}")

            await global_hooks.run(HOOK_BEFORE_TOOL_EXEC, ctx)
            if ctx.aborted:
                return
            if getattr(ctx, "swarm_resolved", False):
                observation = ctx.observation
            else:
                observation = await asyncio.to_thread(_run_wasm_skill, wasm_path, fuel)
                if ("[执行异常" in observation or "[找不到插件" in observation) and tools_fallback:
                    for t in tools_fallback:
                        fb = t.get("fallback", "")
                        if fb and fb.startswith("core:"):
                            console.print(f"[yellow]MCP/Wasm 失败，Fallback 至 {fb}[/yellow]")
                            break
            ctx.observation = observation
            await global_hooks.run(HOOK_AFTER_TOOL_EXEC, ctx)

            _emit("observation", observation)
            add_memory("system", f"Observation: {observation}")
            add_short_term("system", f"Observation: {observation}", meta={"skill": label})
            messages.append({"role": "assistant", "content": response})
            messages.append({
                "role": "user",
                "content": f"Observation: {observation}\n\n请根据观察结果继续思考，或给出最终 Answer:",
            })

    for m in reversed(messages):
        if m.get("role") == "assistant" and m.get("content"):
            ctx.final_answer = m["content"]
            return
    ctx.final_answer = "[ReAct 循环达到上限，任务未完成]"


async def run(
    user_input: str,
    ast_json: dict | None = None,
    *,
    max_iterations: int = MAX_REACT_ITERATIONS,
    run_id: str = "",
    on_step: Callable[[str, str, str], None] | None = None,
    on_hitl_request: Callable[[str, str], None] | None = None,
    on_chunk: Callable[[str], Awaitable[None]] | None = None,
) -> str | dict[str, Any]:
    """
    运行 ReAct 代理循环（v8.0 Nexus Hook Pipeline）。

    Args:
        user_input: 用户自然语言输入（或任务指令）
        ast_json: 蓝图 AST，用于提取 Wasm 技能；若为 None 则无技能
        max_iterations: 最大循环次数，防止死循环（默认 5）
        on_step: 可选回调 (step_type, content)，用于打印 Thought/Action/Observation/Final Answer
        on_hitl_request: 可选回调 (task_id, content)，用于广播 HITL_REQUIRED 至 Layer 3

    Returns:
        最终回复文本，或 HITL_REQUIRED 状态字典（需云端技能时）
    """
    ast_json = ast_json or {}
    skills = _extract_skills_from_blueprint(ast_json)

    if len(skills) == 0:
        try:
            from core.vector_router import SemanticRouter
            router = SemanticRouter()
            match = await router.match_local_skill_async(user_input, threshold=0.50)
            if match and match.get("path"):
                p = Path(match["path"])
                skill_id = match.get("skill_id", "vector_matched")
                if p.suffix == ".wasm":
                    skills.append({
                        "label": skill_id,
                        "wasm_path": match["path"],
                        "fuel_limit": DEFAULT_FUEL_LIMIT,
                    })
                    console.print("[green]✓ 向量路由命中本地技能[/green]")
                elif p.name == "SKILL.md":
                    skill_content = p.read_text(encoding="utf-8", errors="replace")
                    skills.append({
                        "label": skill_id,
                        "type": "skill_md",
                        "skill_path": match["path"],
                        "skill_content": skill_content,
                        "tools": [{"prefer": "mcp:local_os_toolkit", "fallback": "core:fs_read"}, {"prefer": "mcp:bash_env", "fallback": "core:shell_exec"}],
                    })
                    console.print(f"[green]✓ 向量路由命中 SKILL.md: {skill_id}[/green]")
        except ImportError:
            pass

    use_mock = len(skills) == 0
    system_prompt = _build_system_prompt(skills, use_mock=use_mock)

    add_memory("user", user_input)
    add_short_term("user", user_input, meta={"source": "agent_loop"})
    messages = get_context(limit=20)
    if not messages:
        messages = [{"role": "user", "content": user_input}]

    ctx = PipelineContext(
        intent=user_input,
        source="agent_loop",
        session_id="",
        run_id=run_id,
        metadata={
            "ast_json": ast_json,
            "_skills": skills,
            "_use_mock": use_mock,
            "_max_iterations": max_iterations,
            "_on_step": on_step,
            "_on_hitl_request": on_hitl_request,
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

    async def react_core_mw(c: PipelineContext, next_fn) -> None:
        await _run_react_core(c)
        if not c.aborted:
            await next_fn()

    async def pre_response_mw(c: PipelineContext, next_fn) -> None:
        await global_hooks.run(HOOK_BEFORE_RESPONSE, c)
        await next_fn()

    pipeline.use(on_intent_mw).use(react_core_mw).use(pre_response_mw)
    await pipeline.execute(ctx)

    return ctx.final_answer or "[ReAct 未产出回复]"
