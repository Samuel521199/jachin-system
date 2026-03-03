"""
Jachin Nexus Layer 2 - 极客终端 CLI
入口: python -m core.cli pair
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import click
import httpx
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

console = Console(
    theme=Theme(
        {
            "cyan": "#22d3ee",
            "purple": "#a78bfa",
            "green": "#22c55e",
            "dim": "dim",
            "code": "bold cyan",
        }
    )
)

ASCII_ART = r"""
[purple]  ██╗ █████╗  ██████╗██╗  ██╗██╗███╗   ██╗[/purple]
[purple]  ██║██╔══██╗██╔════╝██║  ██║██║████╗  ██║[/purple]
[purple]  ██║███████║██║     ███████║██║██╔██╗ ██║[/purple]
[cyan]  ██║██╔══██║██║     ██╔══██║██║██║╚██╗██║[/cyan]
[cyan]  ██║██║  ██║╚██████╗██║  ██║██║██║ ╚████║[/cyan]
[cyan]  ╚═╝╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝╚═╝╚═╝  ╚═══╝[/cyan]
[dim]     N E X U S  ·  Edge AI OS  ·  Layer 2[/dim]
"""

CONFIG_PATH = Path.home() / ".jachin" / "nexus_config.json"


def _format_short_code(code: str) -> str:
    """格式化为 X7A-9K2 样式"""
    if len(code) >= 6:
        return f"{code[:3]}-{code[3:6]}"
    return code


@click.group()
@click.version_option(version="1.0.0", prog_name="Jachin Nexus CLI")
def cli() -> None:
    """Jachin Nexus Layer 2 极客终端工具"""
    pass


@cli.command()
@click.option(
    "--base-url",
    default="http://localhost:3000",
    envvar="NEXUS_BASE_URL",
    help="Layer 1 Nexus Console 地址",
)
def pair(base_url: str) -> None:
    """边缘智能体配对 - 6 位码连接指挥部"""
    base_url = base_url.rstrip("/")

    # 1. 赛博朋克 Logo
    console.print(ASCII_ART)
    console.print()

    # 2. POST pairing/request
    try:
        resp = httpx.post(
            f"{base_url}/api/v1/pairing/request",
            json={
                "device_fingerprint": None,
                "environment_type": "bare_metal",
                "core_version": "1.0.0",
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.RequestError as e:
        console.print(f"[red][ERROR][/red] 无法连接 Layer 1: {e}")
        console.print(f"        请确认 [cyan]{base_url}[/cyan] 可访问，且 Nexus 服务已启动。")
        raise SystemExit(1)
    except httpx.HTTPStatusError as e:
        console.print(f"[red][ERROR][/red] 配对请求失败: HTTP {e.response.status_code}")
        raise SystemExit(1)

    session_id = data.get("session_id")
    short_code = data.get("short_code", "")
    pair_url = data.get("pair_url", f"{base_url}/pair")
    expires_in = data.get("expires_in", 300)

    if not session_id or not short_code:
        console.print("[red][ERROR][/red] Layer 1 返回数据异常，缺少 session_id 或 short_code")
        raise SystemExit(1)

    # 3. 高亮显示配对码
    code_display = _format_short_code(short_code)
    code_panel = Panel(
        Text.from_markup(f"[bold cyan]{code_display}[/bold cyan]"),
        title="[purple]配对短码[/purple]",
        border_style="cyan",
        padding=(1, 4),
    )
    console.print(code_panel)
    console.print()
    console.print(
        f"[dim]请在 5 分钟内前往 Nexus Console 输入此配对码：[/dim]"
    )
    console.print(f"[cyan]{pair_url}[/cyan]")
    console.print()
    console.print("[dim]静默轮询中，每 3 秒检查一次...[/dim]")

    # 4. 轮询 pairing/status
    poll_interval = 3
    deadline = time.time() + expires_in

    with Progress(
        SpinnerColumn(),
        TextColumn("[dim]{task.description}[/dim]"),
        console=console,
    ) as progress:
        task = progress.add_task("等待指挥官授权...", total=None)
        while time.time() < deadline:
            time.sleep(poll_interval)
            try:
                r = httpx.get(
                    f"{base_url}/api/v1/pairing/status",
                    params={"session_id": session_id},
                    timeout=5.0,
                )
                r.raise_for_status()
                status_data = r.json()
            except Exception:
                continue

            st = status_data.get("status")
            if st == "success":
                progress.update(task, description="[green]授权成功！[/green]")
                break
            if st == "expired":
                console.print("\n[red][ERROR][/red] 配对码已过期，请重新执行 [cyan]python -m core.cli pair[/cyan]")
                raise SystemExit(1)
        else:
            console.print("\n[red][ERROR][/red] 配对超时，请重新执行 [cyan]python -m core.cli pair[/cyan]")
            raise SystemExit(1)

    # 5. 成功 - 绿色提示
    access_token = status_data.get("access_token")
    instance_id = status_data.get("instance_id", "dev-layer2-001")
    nexus_base_url = status_data.get("nexus_base_url", base_url)

    console.print()
    console.print(Panel(
        "[green]✅ 授权成功！正在下发中枢公钥...[/green]",
        border_style="green",
    ))

    # 6. 写入配置
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    cfg = {
        "instance_id": instance_id,
        "access_token": access_token,
        "nexus_base_url": nexus_base_url.rstrip("/"),
    }
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")

    # 7. 炫酷退出
    table = Table(show_header=False)
    table.add_column(style="dim")
    table.add_column()
    table.add_row("配置路径", str(CONFIG_PATH))
    table.add_row("instance_id", instance_id)
    console.print(table)
    console.print()
    console.print(
        Panel(
            "[bold cyan]🚀 边缘智能体已激活，神经元接入星图！[/bold cyan]",
            border_style="cyan",
            padding=(1, 2),
        )
    )


@cli.command()
@click.option(
    "--base-url",
    default=None,
    envvar="NEXUS_BASE_URL",
    help="Layer 1 地址（覆盖 nexus_config.json 中的 nexus_base_url）",
)
def daemon(base_url: str | None) -> None:
    """启动边缘智能体守护进程（心跳 + 蓝图执行）"""
    from core.daemon import start_daemon

    if base_url:
        cfg = {}
        if CONFIG_PATH.exists():
            try:
                cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        cfg["nexus_base_url"] = base_url.rstrip("/")
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    start_daemon()


if __name__ == "__main__":
    cli()
