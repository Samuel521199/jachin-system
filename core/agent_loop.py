"""
Jachin Nexus Layer 2 - 自主代理循环 (ReAct)

Reason + Act：边缘智能体拥有「人设」与「Wasm 技能武器」，
通过 LLM 思考 -> 执行技能 -> 观察结果 -> 循环，直至任务完成。
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Callable

from core.agent_memory import add_memory, get_context

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FUEL_LIMIT = 100_000
MAX_REACT_ITERATIONS = 5  # 战役 1：防死锁，最大 5 次

# Mock 工具（无 Wasm 技能时）：get_weather, read_local_file
MOCK_TOOLS = [
    {"label": "get_weather", "type": "mock", "desc": "查询天气"},
    {"label": "read_local_file", "type": "mock", "desc": "读取本地文件"},
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
            # 尝试默认路径
            for default in ["plugins/dummy.wasm", "plugins/hello.wasm"]:
                candidate = _PROJECT_ROOT / default
                if candidate.exists():
                    skills.append({
                        "label": label,
                        "wasm_path": str(candidate),
                        "fuel_limit": fuel,
                    })
                    break
    return skills


def _build_system_prompt(skills: list[dict[str, Any]], use_mock: bool = False) -> str:
    """动态组装系统 Prompt：人设 + 技能武器列表（无技能时用 Mock 工具）"""
    if use_mock or not skills:
        lines = []
        for i, t in enumerate(MOCK_TOOLS, 1):
            lines.append(f"  {i}. {t['label']} - {t['desc']}")
        skills_desc = "你可以使用以下工具：\n" + "\n".join(lines)
        skills_desc += "\n\n当需要执行工具时，请输出：Action: <工具名> <参数（可选）>"
    else:
        lines = []
        for i, s in enumerate(skills, 1):
            lines.append(f"  {i}. {s['label']} (wasm_path: {s['wasm_path']})")
        skills_desc = "你可以使用以下 Wasm 技能：\n" + "\n".join(lines)
        skills_desc += "\n\n当需要执行技能时，请输出：Action: run <技能名称或序号>"
    skills_desc += "\n任务完成时，请输出：Final Answer: <最终回复>"

    return f"""你是一个高智商的 Jachin 边缘智能体。你可以自主思考。{skills_desc}

请严格使用 Thought, Action, Action Input, Observation 的格式进行思考和调用：
1. Thought: 分析当前情况，决定下一步
2. 如需执行工具：Action: <工具名> <参数>
3. 收到 Observation 后继续思考，或给出：Final Answer: <回复>

保持简洁，完成任务后务必输出 Final Answer:。"""


def _run_mock_tool(tool_name: str, action_input: str = "") -> str:
    """Mock 工具执行（战役 1：无 Wasm 时的模拟）"""
    tool_name = (tool_name or "").strip().lower()
    if tool_name == "get_weather":
        return "北京 晴 18°C，湿度 45%，适宜出行。"
    if tool_name == "read_local_file":
        path = (action_input or "").strip() or "~/.jachin/nexus_config.json"
        p = Path(path).expanduser()
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
            if tool in ("get_weather", "read_local_file"):
                return {"type": "mock", "tool": tool, "input": inp}

    # Action: run <技能名或序号>（Wasm 技能）
    m = re.search(r"Action:\s*run\s+(.+?)(?:\n|$)", text, re.IGNORECASE)
    if m:
        key = m.group(1).strip()
        if key.isdigit():
            idx = int(key)
            if 1 <= idx <= len(skills):
                return {"type": "action", "skill": skills[idx - 1]}
        for s in skills:
            if key in s.get("label", "") or s.get("label", "").startswith(key):
                return {"type": "action", "skill": s}
        for s in skills:
            if key in s.get("wasm_path", ""):
                return {"type": "action", "skill": s}

    return None


async def _get_llm_response(messages: list[dict[str, str]], system_prompt: str) -> str:
    """调用 LLM（本地 Qwen 或 API），返回文本"""
    try:
        from core.brain.llm.factory import LLMProviderFactory

        router = LLMProviderFactory.create_router()
        # 优先本地，其次 Qwen
        provider = router.local_adapter or router.qwen_adapter
        if not provider:
            try:
                provider = LLMProviderFactory.create_provider("local")
            except Exception:
                try:
                    provider = LLMProviderFactory.create_provider("qwen")
                except Exception:
                    pass
        if not provider:
            return "[LLM 未配置，请设置 LOCAL_LLM_URL 或 QWEN_API_KEY]"

        full_messages = [{"role": "system", "content": system_prompt}] + messages
        result = await provider.chat(full_messages, temperature=0.7, max_tokens=1024)
        return (result or "").strip()
    except Exception as e:
        logger.warning("LLM 调用失败: %s", e)
        return f"[LLM 调用失败: {e}]"


def _extract_thought(text: str) -> str | None:
    """从 LLM 输出中提取 Thought 内容"""
    m = re.search(r"Thought:\s*(.+?)(?=Action:|Final Answer:|Answer:|\n\n|$)", text, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else None


async def run(
    user_input: str,
    ast_json: dict | None = None,
    *,
    max_iterations: int = MAX_REACT_ITERATIONS,
    on_step: Callable[[str, str], None] | None = None,
) -> str:
    """
    运行 ReAct 代理循环，直至任务完成。

    Args:
        user_input: 用户自然语言输入（或任务指令）
        ast_json: 蓝图 AST，用于提取 Wasm 技能；若为 None 则无技能
        max_iterations: 最大循环次数，防止死循环（默认 5）
        on_step: 可选回调 (step_type, content)，用于打印 Thought/Action/Observation/Final Answer

    Returns:
        最终回复文本
    """
    def _emit(step_type: str, content: str) -> None:
        if on_step:
            on_step(step_type, content)

    ast_json = ast_json or {}
    skills = _extract_skills_from_blueprint(ast_json)
    use_mock = len(skills) == 0
    system_prompt = _build_system_prompt(skills, use_mock=use_mock)

    add_memory("user", user_input)
    messages = get_context(limit=20)
    if not messages:
        messages = [{"role": "user", "content": user_input}]

    for iteration in range(max_iterations):
        response = await _get_llm_response(messages, system_prompt)
        add_memory("assistant", response)

        # 打印 Thought（若有）
        thought = _extract_thought(response)
        if thought:
            _emit("thought", thought)

        parsed = _parse_action(response, skills, use_mock=use_mock)
        if parsed is None:
            # 未解析到 Action/Answer，将回复当作最终答案（或继续）
            if "Final Answer:" in response or "Answer:" in response or "answer:" in response.lower():
                for prefix in ("Final Answer:", "Answer:", "answer:"):
                    for line in response.split("\n"):
                        if line.strip().lower().startswith(prefix.lower()):
                            ans = line.split(":", 1)[1].strip()
                            _emit("answer", ans)
                            return ans
            messages.append({"role": "assistant", "content": response})
            messages.append({
                "role": "user",
                "content": "请给出最终回复，以 Final Answer: 开头。",
            })
            continue

        if parsed["type"] == "answer":
            ans = parsed.get("content", response)
            _emit("answer", ans)
            add_memory("assistant", ans)
            return ans

        # Mock 工具执行
        if parsed["type"] == "mock":
            tool = parsed.get("tool", "")
            inp = parsed.get("input", "")
            _emit("action", f"Mock 工具 {tool} {inp}".strip())
            observation = _run_mock_tool(tool, inp)
            _emit("observation", observation)
            add_memory("system", f"Observation: {observation}")
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
            _emit("action", f"Wasm 沙箱: {label}")
            # 同步执行，避免阻塞事件循环
            observation = await asyncio.to_thread(
                _run_wasm_skill, wasm_path, fuel
            )
            _emit("observation", observation)
            add_memory("system", f"Observation: {observation}")
            messages.append({"role": "assistant", "content": response})
            messages.append({
                "role": "user",
                "content": f"Observation: {observation}\n\n请根据观察结果继续思考，或给出最终 Answer:",
            })

    # 超限，取最后一条 assistant 作为回复
    for m in reversed(messages):
        if m.get("role") == "assistant" and m.get("content"):
            return m["content"]
    return "[ReAct 循环达到上限，任务未完成]"
