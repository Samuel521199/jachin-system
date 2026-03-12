"""
Jachin Nexus Layer 2 - 核心守护进程

边缘智能体中枢神经：规律心跳、拉取蓝图、降维执行、梦境调度。
v8.0 Layer 3 Capability Negotiation：WebSocket 握手 manifest，按 caps 过滤广播。
入口: python -m core.daemon
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import uuid
from pathlib import Path

import httpx
import websockets
from rich.console import Console
from rich.theme import Theme

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# 配置
# -----------------------------------------------------------------------------

CONFIG_PATH = Path.home() / ".jachin" / "nexus_config.json"

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


def _react_step_printer(step_type: str, content: str, run_id: str = "") -> None:
    """赛博朋克风格打印 ReAct 每一步。v8.0 全链路追踪：run_id 染色前缀"""
    content = (content or "").strip()
    if not content:
        return
    # 截断过长内容
    if len(content) > 200:
        content = content[:197] + "..."
    prefix = f"[RunID:{run_id[:8]}] " if run_id else ""
    if step_type == "thought":
        console.print(f"  {prefix}[magenta][Thought][/magenta] {content}")
    elif step_type == "action":
        console.print(f"  {prefix}[purple]🟣 [Action][/purple] 正在执行: {content}")
    elif step_type == "observation":
        console.print(f"  {prefix}[cyan][Observation][/cyan] {content}")
    elif step_type == "answer":
        console.print(f"  {prefix}[green][Final Answer][/green] {content}")


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
# Jachin Mesh (v8.0) — 量子隧道贯通：WebSocket 优先，HTTP 心跳兜底
# -----------------------------------------------------------------------------

MESH_POLL_INTERVAL = 5  # HTTP 兜底轮询间隔（秒）
MESH_WS_INITIAL_BACKOFF = 2  # 指数退避初始秒数
MESH_WS_MAX_BACKOFF = 120  # 最大退避秒数
_emitted_message_ids: set = set()  # 已注入的 message_id，避免重复下发
_last_blueprint_hash: str = ""  # 上次下发的蓝图哈希，避免重复


def _layer1_ws_url(layer1_url: str) -> str:
    """将 http(s) 转为 wss"""
    base = layer1_url.rstrip("/").replace("http://", "ws://").replace("https://", "wss://")
    return f"{base}/api/v1/agents/stream"


async def _mesh_heartbeat_poll(
    access_token: str,
    layer1_url: str,
    instance_id: str,
) -> dict | None:
    """HTTP 心跳拉取：返回 blueprint/task/pending_message_ids"""
    url = f"{layer1_url.rstrip('/')}/api/v1/agents/heartbeat"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    payload = {"instance_id": instance_id or None}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.debug("[Mesh] 心跳请求异常: %s", e)
    return None


async def _process_mesh_payload(
    data: dict,
    access_token: str,
    layer1_url: str,
) -> None:
    """处理心跳/WebSocket 下发的 blueprint + task，注入总线并执行（去重）"""
    from core.event_bus import emit_omni_input

    global _emitted_message_ids

    blueprint = data.get("blueprint")
    task = data.get("task")
    pending_ids = data.get("pending_message_ids") or []
    pending_set = frozenset(str(x) for x in pending_ids)

    ast_json = {}
    if blueprint and isinstance(blueprint.get("ast_json"), dict):
        ast_json = blueprint["ast_json"]

    # 去重：已下发过的 message_ids 不再重复注入
    if pending_set and pending_set <= _emitted_message_ids:
        return
    if pending_set:
        _emitted_message_ids.update(pending_set)
        # 防止无限增长，仅保留最近 100 个
        if len(_emitted_message_ids) > 100:
            _emitted_message_ids = set(list(_emitted_message_ids)[-50:])

    if task and str(task).strip():
        metadata = {
            "ast_json": ast_json,
            "pending_message_ids": pending_ids,
            "access_token": access_token,
            "layer1_url": layer1_url,
        }
        emit_omni_input("telegram", str(task).strip(), metadata)
        console.print(f"[green][Mesh] ⚡ 指令已注入: {str(task)[:60]}...[/green]")
    elif ast_json and not task:
        # 仅蓝图更新，无新任务（节流：相同蓝图不重复下发）
        global _last_blueprint_hash
        bp_hash = str(hash(json.dumps(ast_json, sort_keys=True, default=str)))
        if bp_hash != _last_blueprint_hash:
            _last_blueprint_hash = bp_hash
            metadata = {"ast_json": ast_json}
            emit_omni_input("telegram", "新蓝图已下发，请基于当前技能自主待命。", metadata)
            console.print("[dim][Mesh] 蓝图已更新，无待办任务[/dim]")


async def connect_layer1_websocket(
    access_token: str,
    layer1_url: str,
    instance_id: str,
) -> None:
    """
    Jachin Mesh 量子隧道：WSS 长连 + 指数退避重连，失败则 HTTP 心跳兜底。
    实现云边毫秒级指令下发，断线自动重连。
    """
    ws_url = _layer1_ws_url(layer1_url)
    backoff = MESH_WS_INITIAL_BACKOFF
    use_ws = False
    ws_unavailable = False  # 若 WSS 端点不存在，切 HTTP 后不再重试 WSS

    while not ws_unavailable:
        try:
            async with websockets.connect(
                ws_url,
                extra_headers={"Authorization": f"Bearer {access_token}"},
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
            ) as ws:
                use_ws = True
                backoff = MESH_WS_INITIAL_BACKOFF  # 连接成功，重置退避
                console.print(f"[green][Mesh] 🔗 量子隧道已贯通 {ws_url}[/green]")
                while True:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=60.0)
                        data = json.loads(msg)
                        if data.get("task") is not None or data.get("blueprint") is not None:
                            await _process_mesh_payload(data, access_token, layer1_url)
                    except asyncio.TimeoutError:
                        continue
                    except websockets.exceptions.ConnectionClosed:
                        console.print("[bold red][Mesh] ⚠ 量子隧道断开！指数退避 %ds 后重连...[/bold red]", backoff)
                        break
        except Exception as e:
            err_str = str(e).lower()
            if "404" in err_str or "403" in err_str or "connection refused" in err_str:
                logger.info("[Mesh] WebSocket 端点不可用: %s", e)
                ws_unavailable = True
                break
            console.print(
                "[bold yellow][Mesh] ⚠ 连接失败，%ds 后重试: %s[/bold yellow]",
                backoff,
                str(e)[:80],
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MESH_WS_MAX_BACKOFF)

    # HTTP 心跳兜底
    if not use_ws or ws_unavailable:
        console.print("[yellow][Mesh] 使用 HTTP 心跳模式 (每 %ds)，等待 Layer 1 指令...[/yellow]", MESH_POLL_INTERVAL)

    while True:
        try:
            data = await _mesh_heartbeat_poll(access_token, layer1_url, instance_id)
            if data and data.get("success"):
                await _process_mesh_payload(data, access_token, layer1_url)
        except Exception as e:
            logger.debug("[Mesh] 心跳处理异常: %s", e)
        await asyncio.sleep(MESH_POLL_INTERVAL)


# -----------------------------------------------------------------------------
# 梦境引擎调度：凌晨 3 点 + 空闲 30min 触发 Dream Weaver
# -----------------------------------------------------------------------------

DREAM_HOUR = 3  # 凌晨 3 点
IDLE_THRESHOLD_SEC = 30 * 60  # 空闲 30 分钟触发梦境重塑
IDLE_CHECK_INTERVAL = 10 * 60  # 每 10 分钟检查一次空闲
_LAST_WEAVE_TIME: float = 0.0  # 上次 Dream Weaver 执行时间，避免频繁触发


def _seconds_until_3am() -> float:
    """计算距离下次凌晨 3 点的秒数"""
    now = datetime.datetime.now()
    next_3am = now.replace(hour=DREAM_HOUR, minute=0, second=0, microsecond=0)
    if now >= next_3am:
        next_3am += datetime.timedelta(days=1)
    return (next_3am - now).total_seconds()


async def _run_dream_weaver_if_idle() -> bool:
    """若距离上次交互超过 30 分钟，执行 Dream Weaver。返回是否执行"""
    import time
    from core.event_bus import get_last_interaction_time
    from core.dream_weaver import run_weave_dreams

    global _LAST_WEAVE_TIME
    now = time.time()
    if now - _LAST_WEAVE_TIME < 3600:  # 至少间隔 1 小时
        return False
    if now - get_last_interaction_time() < IDLE_THRESHOLD_SEC:
        return False
    try:
        _LAST_WEAVE_TIME = now
        await run_weave_dreams()
        return True
    except Exception as e:
        console.print(f"[red][Dream Weaver] 空闲触发异常: {e}[/red]")
        return False


async def dream_scheduler_loop() -> None:
    """v8.0 梦境调度：凌晨 3 点梦境回放 + Dream Weaver；空闲 30min 触发 Dream Weaver"""
    import time

    while True:
        wait_until_3am = _seconds_until_3am()
        sleep_sec = min(IDLE_CHECK_INTERVAL, max(60, wait_until_3am - 60))
        console.print(f"[dim][Dream] 下次梦境: {datetime.timedelta(seconds=int(wait_until_3am))} 后[/dim]")
        await asyncio.sleep(sleep_sec)

        # 空闲检测：每轮检查，若空闲 > 30min 则 Dream Weaver
        if await _run_dream_weaver_if_idle():
            console.print("[dim][Dream Weaver] 系统空闲，已完成潜意识重塑[/dim]")

        # 凌晨 3 点窗口（误差 2 分钟内）：梦境引擎 + Dream Weaver
        wait_until_3am = _seconds_until_3am()
        if wait_until_3am <= 120:
            try:
                from core.dreamer import run_dream_sequence
                from core.dream_weaver import run_weave_dreams

                console.print("[magenta][Dream] 🌙 梦境引擎启动，正在提纯今日记忆...[/magenta]")
                count = await run_dream_sequence(limit=500)
                console.print(f"[magenta][Dream] ✅ 梦境完成，写入 {count} 条核心记忆[/magenta]")

                weave_count = await run_weave_dreams()
                if weave_count > 0:
                    console.print(f"[magenta][Dream Weaver] ✨ LanceDB 记忆重塑完成，{weave_count} 条核心认知[/magenta]")
            except Exception as e:
                console.print(f"[red][Dream] ❌ 梦境异常: {e}[/red]")


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
    run_id = uuid.uuid4().hex
    console.print(f"[RunID:{run_id[:8]}] [cyan]🧠 [Agent] 收到任务，启动 ReAct 代理循环...[/cyan]")
    console.print(f"[RunID:{run_id[:8]}] [dim]  输入: {task[:80]}{'...' if len(task) > 80 else ''}[/dim]")

    try:
        result = await agent_run(
            task,
            ast_json=ast_json,
            run_id=run_id,
            on_step=_react_step_printer,
        )
        console.print(f"[RunID:{run_id[:8]}] [green]  ✅ Agent 完成[/green]")

        # 若有 IM 消息 ID，将结果回传 Layer 1（推送到用户手机）
        if pending_message_ids and access_token and layer1_url:
            await _report_result(
                result=str(result)[:4096],
                message_ids=pending_message_ids,
                access_token=access_token,
                layer1_url=layer1_url,
            )
    except Exception as e:
        console.print(f"[RunID:{run_id[:8]}] [red]  ❌ Agent 异常: {e}[/red]")
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
        from core.event_bus import (
            start_omni_consumer,
            subscribe_omni_output,
            OmniOutputEvent,
        )

        # 全息感官总线：IM 输出 -> 回传 Layer 1
        async def _im_output_handler(ev: OmniOutputEvent) -> None:
            if ev.source != "telegram":
                return
            pid = ev.payload.get("pending_message_ids") or []
            token = ev.payload.get("access_token") or ""
            url = ev.payload.get("layer1_url") or ""
            if pid and token and url:
                await _report_result(result=ev.result, message_ids=pid, access_token=token, layer1_url=url)

        subscribe_omni_output("telegram", _im_output_handler)
        from core.sensory_server import _broadcast_to_ui, start_sensory_server
        subscribe_omni_output("layer3_broadcast", _broadcast_to_ui)
        subscribe_omni_output("swarm_broadcast", _broadcast_to_ui)
        from core.event_bus import set_omni_step_callback

        set_omni_step_callback(_react_step_printer)
        start_omni_consumer()

        # Layer 3 视觉投射：启动本地 WS Server
        await start_sensory_server()

        # Jachin Mesh (WebSocket 占位) + 梦境调度 并行运行
        await asyncio.gather(
            connect_layer1_websocket(
                access_token=access_token,
                layer1_url=layer1_url,
                instance_id=instance_id,
            ),
            dream_scheduler_loop(),
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
