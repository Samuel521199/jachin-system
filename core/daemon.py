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
            "dim": "dim",
        }
    )
)


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

                    # 若返回蓝图，执行
                    blueprint = data.get("blueprint")
                    if blueprint and isinstance(blueprint, dict):
                        ast_json = blueprint.get("ast_json")
                        name = blueprint.get("name", "未命名蓝图")
                        if ast_json:
                            console.print(
                                f"[cyan][Blueprint] 📥 收到新蓝图: {name}[/cyan]"
                            )
                            await execute_blueprint_fn(ast_json)
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
# AST 蓝图解析与执行引擎 (含 WASM 物理沙箱)
# -----------------------------------------------------------------------------

DEFAULT_FUEL_LIMIT = 100_000
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WASM_PATHS = [
    _PROJECT_ROOT / "plugins" / "dummy.wasm",
    _PROJECT_ROOT / "plugins" / "hello.wasm",
    Path.cwd() / "plugins" / "dummy.wasm",
    Path.cwd() / "plugins" / "hello.wasm",
]


def _resolve_wasm_path(node_data: dict) -> Path | None:
    """解析 Processor 节点的 wasm_path，支持 data.wasm_path 或默认路径"""
    data = node_data or {}
    explicit = data.get("wasm_path")
    if explicit:
        p = Path(explicit)
        if p.exists():
            return p
    for p in DEFAULT_WASM_PATHS:
        if p.exists():
            return p
    return None


async def execute_blueprint(ast_json: dict) -> None:
    """
    解析 AST 并执行：Trigger -> Processor (WASM 沙箱) -> Action
    """
    nodes = ast_json.get("nodes") or []
    if not nodes:
        console.print("[dim]  [AST] 空蓝图，无节点可执行[/dim]")
        return

    triggers = [n for n in nodes if (n.get("type") or "").lower() == "trigger"]
    processors = [n for n in nodes if (n.get("type") or "").lower() == "processor"]
    actions = [n for n in nodes if (n.get("type") or "").lower() == "action"]

    for n in triggers:
        label = (n.get("data") or {}).get("label", "触发器")
        console.print(f"[cyan]🔵 [Trigger] {label} 已就绪，正在监听唤醒词...[/cyan]")

    for n in processors:
        label = (n.get("data") or {}).get("label", "处理器")
        data = n.get("data") or {}
        wasm_path = _resolve_wasm_path(data)
        fuel = data.get("fuel_limit", DEFAULT_FUEL_LIMIT)

        if wasm_path:
            try:
                from core.wasm_runner import JachinWasmSandbox

                console.print(
                    f"[purple]🟣 [Processor] 正在拉起受限沙箱 ({label})，注入燃料: {fuel}...[/purple]"
                )
                sandbox = JachinWasmSandbox()
                result = sandbox.run_plugin(str(wasm_path), fuel_limit=fuel)
                console.print(
                    f"[purple]🟣 [Processor] 沙箱执行完成，运行结果: {result}[/purple]"
                )
            except ImportError:
                console.print(
                    "[purple]🟣 [Processor] wasmtime 未安装，跳过沙箱执行 (pip install wasmtime)[/purple]"
                )
                await asyncio.sleep(0.5)
            except FileNotFoundError:
                console.print(
                    f"[purple]🟣 [Processor] 找不到插件，跳过沙箱执行 (可运行 python scripts/gen_dummy_wasm.py 生成)[/purple]"
                )
                await asyncio.sleep(0.5)
            except Exception as e:
                console.print(
                    f"[red]🟣 [Processor] 沙箱执行异常: {e}[/red]"
                )
        else:
            console.print(
                f"[purple]🟣 [Processor] 加载本地大模型 / WASM 沙箱 ({label}) (模拟延时 1s)...[/purple]"
            )
            await asyncio.sleep(1.0)

    for n in actions:
        label = (n.get("data") or {}).get("label", "执行器")
        console.print(
            f"[green]🟢 [Action] 继电器已触发 / 扬声器正在播报 ({label})[/green]"
        )

    console.print("[green]  ✅ 蓝图执行完成[/green]")


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
