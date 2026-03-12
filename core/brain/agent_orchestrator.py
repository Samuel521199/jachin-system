"""
AgentOrchestrator - ReAct 循环智能中枢
接收用户输入，调用 LLM，解析 Thought/Action，执行工具，循环直到得到最终答案
"""

import json
import logging
import re
from typing import Dict, Any, List, Optional

import ray

from core.config import settings

logger = logging.getLogger(__name__)

REACT_SYSTEM = """你是一个智能助手，可以使用以下工具完成任务。
输出格式必须为 JSON：
{"thought": "你的思考", "action": "tool_call" | "finish", "tool": {"skill_id": "xxx", "capability": "xxx", "params": {}}, "answer": "最终回答（仅当 action=finish 时）"}
- action=tool_call 时，必须提供 tool
- action=finish 时，必须提供 answer
"""


@ray.remote(num_cpus=0.2, num_gpus=0)
class AgentOrchestrator:
    """ReAct 循环编排器"""

    def __init__(self):
        self._max_turns = 10

    async def run(self, user_input: str) -> Dict[str, Any]:
        """
        ReAct 循环：接收用户输入，调用 LLM，执行工具，直到得到最终答案
        """
        plugin_mgr = _get_plugin_manager()
        caps = await plugin_mgr.list_capabilities()
        tools_desc = _format_tools(caps)
        messages = [
            {"role": "system", "content": REACT_SYSTEM + "\n可用工具：\n" + tools_desc},
            {"role": "user", "content": user_input},
        ]
        for turn in range(self._max_turns):
            llm = _get_llm(user_input)
            response = await llm.chat(messages=messages, temperature=0.3, max_tokens=1024)
            text = response if isinstance(response, str) else (getattr(response, "text", None) or str(response))
            parsed = _parse_react(text)
            if not parsed:
                messages.append({"role": "assistant", "content": text})
                messages.append({"role": "user", "content": "请以 JSON 格式输出，包含 thought、action，若 action=tool_call 则包含 tool。"})
                continue
            thought = parsed.get("thought", "")
            action = parsed.get("action", "finish")
            if action == "finish":
                answer = parsed.get("answer", text)
                return {"success": True, "answer": answer, "turns": turn + 1}
            if action == "tool_call":
                tool = parsed.get("tool", {})
                skill_id = tool.get("skill_id")
                capability = tool.get("capability")
                params = tool.get("params") or {}
                if not skill_id or not capability:
                    messages.append({"role": "assistant", "content": text})
                    messages.append({"role": "user", "content": "tool 必须包含 skill_id 和 capability。"})
                    continue
                actor = plugin_mgr.get_actor(skill_id)
                if not actor:
                    obs = f"Error: Skill {skill_id} not loaded."
                else:
                    try:
                        ref = actor.execute.remote(capability, params)
                        result = ray.get(ref)
                        obs = json.dumps(result, ensure_ascii=False)
                    except Exception as e:
                        obs = f"Error: {e}"
                messages.append({"role": "assistant", "content": text})
                messages.append({"role": "user", "content": f"Observation: {obs}\n请继续思考，或 action=finish 给出最终回答。"})
            else:
                messages.append({"role": "assistant", "content": text})
                messages.append({"role": "user", "content": "action 应为 tool_call 或 finish。"})
        return {"success": False, "error": "Max turns exceeded", "turns": self._max_turns}


def _get_plugin_manager():
    from core.system.plugin_manager import get_plugin_manager
    return get_plugin_manager()


def _get_llm(user_input: str = ""):
    """根据 user_input 复杂度路由到合适模型（大小脑协同）"""
    from core.brain.llm_engine import route_and_get_llm
    return route_and_get_llm(user_input)


def _format_tools(caps: List[Dict[str, Any]]) -> str:
    lines = []
    for c in caps:
        sid = c.get("skill_id", "")
        name = c.get("capability_name", "")
        desc = c.get("description", "")
        lines.append(f"- {sid}.{name}: {desc}")
    return "\n".join(lines) if lines else "(无可用工具)"


def _parse_react(text: str) -> Optional[Dict[str, Any]]:
    """解析 LLM 输出的 JSON"""
    text = text.strip()
    m = re.search(r"\{[^{}]*\"thought\"[^{}]*\}", text, re.DOTALL)
    if not m:
        m = re.search(r"\{[\s\S]*?\}", text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None
