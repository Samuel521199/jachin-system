"""
Jachin Nexus V2 - L3 单体 Agent 与记忆同步

单机闭环：Thought -> Action -> Observation。
MemorySyncDaemon：定期将本地记忆同步至 L2，拉取梦境优化结果。
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

logger = logging.getLogger(__name__)

MAX_REACT_ITERATIONS = 5
MOCK_TOOLS = [
    {"label": "get_weather", "type": "mock", "desc": "查询天气"},
    {"label": "read_local_file", "type": "mock", "desc": "读取本地文件"},
]


def _run_mock_tool(tool_name: str, action_input: str) -> str:
    if tool_name == "get_weather":
        return "北京 晴 18°C，湿度 45%，适宜出行。"
    if tool_name == "read_local_file":
        raw = (action_input or "").strip() or "~/.jachin/workspace/target.txt"
        path = raw
        if "target.txt" in raw:
            path = str(Path.home() / ".jachin" / "workspace" / "target.txt")
        p = Path(path).expanduser()
        if not p.exists():
            p = Path.home() / ".jachin" / "workspace" / "target.txt"
        if p.exists():
            try:
                return p.read_text(encoding="utf-8", errors="replace")[:500]
            except Exception as e:
                return f"[读取失败: {e}]"
        return f"[文件不存在: {path}]"
    return f"[未知 Mock 工具: {tool_name}]"


def _parse_action(
    llm_output: str,
    skills: list[dict[str, Any]],
    use_mock: bool = True,
) -> dict[str, Any] | None:
    text = (llm_output or "").strip()
    for pattern in (r"Final\s+Answer:\s*(.+?)(?:\n|$)", r"Answer:\s*(.+?)(?:\n|$)"):
        m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if m:
            return {"type": "answer", "content": m.group(1).strip()}

    if use_mock:
        m = re.search(r"Action:\s*(\w+)(?:\s+(.+?))?(?:\n|$)", text, re.IGNORECASE)
        if m:
            tool, inp = m.group(1).strip().lower(), (m.group(2) or "").strip()
            if tool in ("get_weather", "read_local_file"):
                return {"type": "mock", "tool": tool, "input": inp}

    for tool_id in ("core:fs_read", "core:shell_exec"):
        if re.search(rf"Action:\s*{re.escape(tool_id)}\s*(?:\n|$)", text, re.IGNORECASE):
            inp = ""
            mi = re.search(
                r"Action\s+Input:\s*(.+?)(?:\n\n|\n(?:Thought|Action|Final|$))",
                text, re.DOTALL | re.IGNORECASE,
            )
            if mi:
                inp = mi.group(1).strip()
            return {"type": "native", "tool": tool_id, "input": inp}
    return None


def _build_system_prompt(use_mock: bool = True) -> str:
    tools_desc = "\n".join(
        f"- {t['label']}: {t['desc']}" for t in MOCK_TOOLS
    ) if use_mock else ""
    return f"""你是一个智能助手，使用 ReAct 格式思考。
可用工具：
{tools_desc}

输出格式：
Thought: <你的思考>
Action: <工具名>
Action Input: <参数>
Observation: <工具返回>
...（可多轮）
Final Answer: <最终回复>
"""


async def _run_react_core(
    ctx: PipelineContext,
    engine: LiteLLMEngine,
    on_step: Optional[Callable[[str, str, str], None]] = None,
) -> None:
    skills = ctx.metadata.get("_skills") or []
    use_mock = ctx.metadata.get("_use_mock", True)
    max_iterations = ctx.metadata.get("_max_iterations", MAX_REACT_ITERATIONS)
    on_chunk = ctx.metadata.get("_on_chunk")
    messages = ctx.messages

    def _emit(step_type: str, content: str) -> None:
        if on_step:
            on_step(step_type, content, ctx.run_id)

    for iteration in range(max_iterations):
        ctx.current_response = ""
        ctx.parsed_action = None
        ctx.observation = ""

        await global_hooks.run(HOOK_BEFORE_LLM_THINK, ctx)
        if ctx.aborted:
            return

        full_messages = [{"role": "system", "content": ctx.system_prompt}] + messages
        if on_chunk:
            response = await engine.generate_response_stream(
                full_messages, chunk_callback=on_chunk,
                temperature=0.7, max_tokens=1024,
            )
        else:
            result = await engine.generate_response(
                full_messages, temperature=0.7, max_tokens=1024,
            )
            response = result.get("content", result) if isinstance(result, dict) else str(result)

        ctx.current_response = response

        thought = re.search(
            r"Thought:\s*(.+?)(?=Action:|Final Answer:|Answer:|\n\n|$)",
            response, re.DOTALL | re.IGNORECASE,
        )
        if thought:
            _emit("thought", thought.group(1).strip())

        parsed = _parse_action(response, skills, use_mock=use_mock)
        ctx.parsed_action = parsed

        if parsed is None:
            if "Final Answer:" in response or "Answer:" in response:
                for prefix in ("Final Answer:", "Answer:"):
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
            ctx.final_answer = ans
            return

        if parsed["type"] == "mock":
            tool = parsed.get("tool", "")
            inp = parsed.get("input", "")
            _emit("action", f"Mock {tool} {inp}".strip())
            await global_hooks.run(HOOK_BEFORE_TOOL_EXEC, ctx)
            if ctx.aborted:
                return
            observation = _run_mock_tool(tool, inp)
            ctx.observation = observation
            await global_hooks.run(HOOK_AFTER_TOOL_EXEC, ctx)
            _emit("observation", observation)
            messages.append({"role": "assistant", "content": response})
            messages.append({
                "role": "user",
                "content": f"Observation: {observation}\n\n请根据观察继续思考，或给出 Final Answer:",
            })
            continue

        if parsed["type"] == "native":
            tool = parsed.get("tool", "")
            inp = parsed.get("input", "")
            _emit("action", f"{tool} {inp}".strip())
            await global_hooks.run(HOOK_BEFORE_TOOL_EXEC, ctx)
            if ctx.aborted:
                return
            observation = _run_mock_tool(
                "read_local_file" if "fs_read" in tool else "get_weather",
                inp,
            )
            ctx.observation = observation
            await global_hooks.run(HOOK_AFTER_TOOL_EXEC, ctx)
            _emit("observation", observation)
            messages.append({"role": "assistant", "content": response})
            messages.append({
                "role": "user",
                "content": f"Observation: {observation}\n\n请根据观察继续思考，或给出 Final Answer:",
            })
            continue

    ctx.final_answer = "[ReAct 循环达到上限]"


async def run_agent(
    user_input: str,
    engine: LiteLLMEngine,
    *,
    max_iterations: int = MAX_REACT_ITERATIONS,
    on_step: Optional[Callable[[str, str, str], None]] = None,
    on_chunk: Optional[Callable[[str], Awaitable[None]]] = None,
) -> str:
    """
    运行 L3 单体 ReAct 循环。
    """
    run_id = str(uuid.uuid4())
    system_prompt = _build_system_prompt(use_mock=True)
    messages = [{"role": "user", "content": user_input}]

    ctx = PipelineContext(
        intent=user_input,
        source="l3_agent",
        run_id=run_id,
        metadata={
            "_skills": [],
            "_use_mock": True,
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
