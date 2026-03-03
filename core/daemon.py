"""
Jachin Nexus Layer 2 - 核心守护进程

边缘智能体中枢神经：规律心跳、拉取蓝图、降维执行。
入口: python -m core.daemon
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx
from rich.console import Console
from rich.theme import Theme

# -----------------------------------------------------------------------------
# 配置
# -----------------------------------------------------------------------------

CONFIG_PATH = Path.home() / ".jachin" / "nexus_config.json"
HEARTBEAT_INTERVAL_SEC = 10
HEARTBEAT_URL_SUFFIX = "/api/v1/agents/heartbeat"

console = Console(
    theme=Theme(
        {
            "cyan": "#22d3ee",
            "purple": "#a78bfa",
            "green": "#22c55e",
            "red": "#ef4444",
            "yellow": "#eab308",
            "magenta": "#d946ef",
            "dim": "dim",
        }
    )
)


def _react_step_printer(step_type: str, content: str) -> None:
    """赛博朋克风格打印 ReAct 每一步"""
    content = (content or "").strip()
    if not content:
        return
    # 截断过长内容
    if len(content) > 200:
        content = content[:197] + "..."
    if step_type == "thought":
        console.print(f"  [magenta][Thought][/magenta] {content}")
    elif step_type == "action":
        console.print(f"  [purple]🟣 [Action][/purple] 正在执行: {content}")
    elif step_type == "observation":
        console.print(f"  [cyan][Observation][/cyan] {content}")
    elif step_type == "answer":
        console.print(f"  [green][Final Answer][/green] {content}")


def load_config() -> dict:
    """读取 ~/.jachin/nexus_config.json"""
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def validate_config() -> tuple[str, str, str]:
    """
    校验配置，返回 (access_token, layer1_url, instance_id)。
    若无效则打印红色警告并退出。
    """
    cfg = load_config()
    token = cfg.get("access_token") or ""
    base = (cfg.get("nexus_base_url") or "http://localhost:3000").rstrip("/")
    instance_id = cfg.get("instance_id") or ""

    if not token:
        console.print(
            "[red]❌ 边缘智能体尚未配对，请先运行 [bold]python -m core.cli pair[/bold][/red]"
        )
        raise SystemExit(1)

    return token, base, instance_id


# -----------------------------------------------------------------------------
# 心跳循环
# -----------------------------------------------------------------------------


async def heartbeat_loop(
    access_token: str,
    layer1_url: str,
    instance_id: str,
    execute_blueprint_fn,
) -> None:
    """每隔 10 秒向 Layer 1 发送心跳，并处理返回的蓝图"""
    url = f"{layer1_url}{HEARTBEAT_URL_SUFFIX}"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    payload = {
        "instance_id": instance_id,
        "core_version": "1.0.0",
        "metrics": {},
        "active_plugins": {},
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        while True:
            start = time.perf_counter()
            try:
                resp = await client.post(url, json=payload, headers=headers)
                elapsed_ms = int((time.perf_counter() - start) * 1000)

                if resp.status_code == 200:
                    data = resp.json()
                    console.print(
                        f"[green][Heartbeat] 💓 脉搏正常 | 延迟: {elapsed_ms}ms[/green]"
                    )

                    # 若返回蓝图或 IM 任务，喂给 Agent Loop 自主执行
                    blueprint = data.get("blueprint")
                    task = data.get("task") or data.get("message")
                    pending_message_ids = data.get("pending_message_ids") or []

                    if blueprint and isinstance(blueprint, dict):
                        ast_json = blueprint.get("ast_json")
                        name = blueprint.get("name", "未命名蓝图")
                        if ast_json:
                            console.print(
                                f"[cyan][Blueprint] 📥 收到新蓝图: {name}[/cyan]"
                            )
                            await execute_blueprint_fn(
                                ast_json,
                                user_input=task,
                                pending_message_ids=pending_message_ids,
                                access_token=access_token,
                                layer1_url=layer1_url,
                            )
                    elif task and pending_message_ids:
                        # 仅有 IM 消息，无新蓝图：用空技能集执行（或可扩展为缓存上次蓝图）
                        console.print(f"[cyan][IM] 📩 收到用户消息，启动 Agent...[/cyan]")
                        await execute_blueprint_fn(
                            {},
                            user_input=task,
                            pending_message_ids=pending_message_ids,
                            access_token=access_token,
                            layer1_url=layer1_url,
                        )
                else:
                    console.print(
                        f"[red][Heartbeat] ❌ 心跳失败 HTTP {resp.status_code}[/red]"
                    )
            except Exception as e:
                elapsed_ms = int((time.perf_counter() - start) * 1000)
                console.print(
                    f"[red][Heartbeat] ❌ 连接异常 | 延迟: {elapsed_ms}ms | {e}[/red]"
                )

            await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)


# -----------------------------------------------------------------------------
# Agent Loop 驱动：蓝图作为岗位说明书，ReAct 自主执行
# -----------------------------------------------------------------------------


async def execute_blueprint(
    ast_json: dict,
    user_input: str | None = None,
    *,
    pending_message_ids: list | None = None,
    access_token: str = "",
    layer1_url: str = "",
) -> None:
    """
    将蓝图与任务指令喂给 AgentLoop，由 ReAct 代理自主决定执行策略。

    不再机械执行 Trigger->Processor->Action，而是：
    - 蓝图 = 岗位说明书（人设 + Wasm 技能武器）
    - 任务/聊天消息 = User Input
    - Agent 通过 Thought -> Action -> Observation 循环自主完成
    - 若有 pending_message_ids，执行完成后调用 Layer 1 的 result API 回传用户（TG/飞书）
    """
    from core.agent_loop import run as agent_run

    nodes = ast_json.get("nodes") or []
    if not nodes:
        console.print("[dim]  [AST] 空蓝图，无节点可执行[/dim]")

    # 默认任务：新蓝图已加载，请自主待命
    task = user_input or "新蓝图已下发，请基于当前技能自主待命。若有待办任务请执行，否则保持就绪。"
    console.print(f"[cyan]🧠 [Agent] 收到任务，启动 ReAct 代理循环...[/cyan]")
    console.print(f"[dim]  输入: {task[:80]}{'...' if len(task) > 80 else ''}[/dim]")

    try:
        result = await agent_run(
            task,
            ast_json=ast_json,
            on_step=_react_step_printer,
        )
        console.print(f"[green]  ✅ Agent 完成[/green]")

        # 若有 IM 消息 ID，将结果回传 Layer 1（推送到用户手机）
        if pending_message_ids and access_token and layer1_url:
            await _report_result(
                result=str(result)[:4096],
                message_ids=pending_message_ids,
                access_token=access_token,
                layer1_url=layer1_url,
            )
    except Exception as e:
        console.print(f"[red]  ❌ Agent 异常: {e}[/red]")
        if pending_message_ids and access_token and layer1_url:
            await _report_result(
                result=f"[执行异常] {e}",
                message_ids=pending_message_ids,
                access_token=access_token,
                layer1_url=layer1_url,
            )


async def _report_result(
    result: str,
    message_ids: list,
    access_token: str,
    layer1_url: str,
) -> None:
    """调用 Layer 1 result API，将执行结果推回用户（TG/飞书）"""
    url = f"{layer1_url.rstrip('/')}/api/v1/agents/result"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    payload = {"result": result, "message_ids": message_ids}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                console.print("[green]  📤 结果已回传至用户手机[/green]")
            else:
                console.print(f"[dim]  [Result] 回传失败 HTTP {resp.status_code}[/dim]")
    except Exception as e:
        console.print(f"[dim]  [Result] 回传异常: {e}[/dim]")


# -----------------------------------------------------------------------------
# 入口
# -----------------------------------------------------------------------------


def start_daemon() -> None:
    """启动守护进程"""
    console.print("[cyan]⚙️ Jachin Nexus Layer 2 - 边缘觉醒[/cyan]")
    console.print("[dim]正在读取配置...[/dim]")

    access_token, layer1_url, instance_id = validate_config()

    console.print(f"[dim]  Layer 1: {layer1_url}[/dim]")
    console.print(f"[dim]  Instance: {instance_id or '(未设置)'}[/dim]")
    console.print()
    console.print("[green]🚀 守护进程已启动，按 Ctrl+C 优雅退出[/green]")
    console.print()

    async def _main() -> None:
        await heartbeat_loop(
            access_token=access_token,
            layer1_url=layer1_url,
            instance_id=instance_id,
            execute_blueprint_fn=execute_blueprint,
        )

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        console.print()
        console.print(
            "[yellow]🛑 收到中断信号，边缘智能体正在优雅休眠... 神经连接已断开。[/yellow]"
        )


if __name__ == "__main__":
    start_daemon()
