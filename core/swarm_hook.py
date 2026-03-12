"""
Jachin Nexus v8.0 — Edge Mesh Swarm Hook 拦截器

在 HOOK_BEFORE_TOOL_EXEC 拦截重计算型工具，向全网广播 task_offer，
挂起等待节点接单回传，跳过本地执行。
"""
from __future__ import annotations

import logging
from typing import Any

from rich.console import Console
from rich.theme import Theme

from core.hooks_pipeline import HOOK_BEFORE_TOOL_EXEC, PipelineContext, global_hooks

logger = logging.getLogger(__name__)
console = Console(theme=Theme({"swarm": "#d946ef", "dim": "dim"}))

# 重计算型工具：需外包至虫群节点。可配置扩展（~/.jachin/nexus_config.json → swarm.heavy_tools）
def _load_heavy_tools() -> set[str]:
    try:
        from pathlib import Path
        import json
        cfg_path = Path.home() / ".jachin" / "nexus_config.json"
        if cfg_path.exists():
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            tools = data.get("swarm", {}).get("heavy_tools")
            if isinstance(tools, list):
                return set(str(t) for t in tools)
    except Exception:
        pass
    return {"video_encode", "ffmpeg_encode", "heavy_render"}


HEAVY_TOOLS: set[str] = _load_heavy_tools()


def _get_tool_name_from_parsed(parsed: dict[str, Any] | None) -> str:
    """从 parsed_action 提取 tool 名称"""
    if not parsed:
        return ""
    tool = parsed.get("tool", "")
    if tool:
        return str(tool).strip()
    skill = parsed.get("skill") or {}
    return str(skill.get("label", "")).strip()


def _get_payload_from_parsed(parsed: dict[str, Any] | None) -> dict[str, Any]:
    """从 parsed_action 提取任务参数"""
    if not parsed:
        return {}
    payload: dict[str, Any] = {"tool": _get_tool_name_from_parsed(parsed)}
    if parsed.get("type") == "native":
        payload["input"] = parsed.get("input", "")
        payload["tool"] = parsed.get("tool", "")
    elif parsed.get("type") == "mock":
        payload["input"] = parsed.get("input", "")
    elif parsed.get("type") == "action":
        skill = parsed.get("skill") or {}
        payload["wasm_path"] = skill.get("wasm_path", "")
        payload["label"] = skill.get("label", "")
    return payload


async def _swarm_before_tool_handler(ctx: PipelineContext) -> None:
    """HOOK_BEFORE_TOOL_EXEC：若为 heavy_tool，广播 task_offer 并挂起"""
    parsed = ctx.parsed_action
    tool_name = _get_tool_name_from_parsed(parsed)
    if not tool_name or tool_name not in HEAVY_TOOLS:
        return

    from core.swarm_registry import register_task, await_task_result
    from core.event_bus import get_bus
    from core.event_bus import SensoryOutputEvent

    payload = _get_payload_from_parsed(parsed)
    task_id = register_task(tool_name, payload)

    bus = get_bus()
    run_id = getattr(ctx, "run_id", "") or ""
    ev = SensoryOutputEvent(
        source_ref="swarm_broadcast",
        content=task_id,
        action_type="task_offer",
        metadata={
            "step_type": "task_offer",
            "task_id": task_id,
            "tool": tool_name,
            "payload": payload,
            "run_id": run_id,
        },
    )
    await bus.publish_output(ev)

    prefix = f"[RunID:{run_id[:8]}] " if run_id else ""
    console.print(f"{prefix}[swarm][🐝 Swarm] 广播重载任务 {task_id} ({tool_name})...[/swarm]")

    result = await await_task_result(task_id, timeout=300.0)
    if result is not None:
        ctx.observation = str(result)
        ctx.swarm_resolved = True
        console.print(f"{prefix}[swarm][🐝 Swarm] 任务完成！耗时已计算。[/swarm]")
    else:
        ctx.observation = "[Swarm] 任务超时或无人接单，跳过"
        ctx.swarm_resolved = True


# 模块加载时自动注册
def _register_swarm_hook() -> None:
    global_hooks.register(HOOK_BEFORE_TOOL_EXEC, _swarm_before_tool_handler)


_register_swarm_hook()
