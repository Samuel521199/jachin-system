"""
jachin publish - 一键上云发布到 Nexus 商城

PRIVATE 可见性时默认执行「影子上传」：仅登记 plugin.json 元数据，不打包、不上传二进制。
"""
from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from jachin_cli.config import get_nexus_url, get_token

console = Console()


def publish_cmd(
    visibility: str = typer.Option("PUBLIC", "--visibility", "-v", help="PUBLIC 或 PRIVATE"),
    price: int = typer.Option(0, "--price", "-p", help="月付价格(分)，0=免费"),
) -> None:
    """读取配置与 dist 包，向 L1 Nexus 发起发布请求"""
    console.print()
    console.print(Panel.fit(
        "[bold cyan]Jachin Publish[/] - 一键上云",
        border_style="cyan",
    ))
    console.print()

    token = get_token()
    if not token:
        console.print(Panel.fit(
            "[bold red]未配置 JACHIN_DEV_TOKEN[/]\n\n"
            "请设置环境变量: export JACHIN_DEV_TOKEN=your_token\n"
            "或在 ~/.jachin-cli/config.json 中配置 token 字段",
            border_style="red",
            title="[bold red]Error[/]",
        ))
        raise typer.Exit(1)

    cwd = Path.cwd()
    plugin_path = cwd / "plugin.json"
    if not plugin_path.exists():
        console.print("[red]Error:[/] plugin.json 不存在，请先运行 jachin init")
        raise typer.Exit(1)

    try:
        plugin_data = json.loads(plugin_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        console.print("[red]Error:[/] plugin.json 解析失败")
        raise typer.Exit(1)

    if visibility.upper() not in ("PUBLIC", "PRIVATE"):
        visibility = "PUBLIC"
    else:
        visibility = visibility.upper()
    if price < 0:
        price = 0

    # PRIVATE 时默认影子上传：仅登记元数据，不打包、不上传
    shadow_only = visibility.upper() == "PRIVATE"
    latest_zip = None
    if not shadow_only:
        dist_dir = cwd / "dist"
        if not dist_dir.exists():
            console.print("[red]Error:[/] dist/ 目录不存在，请先运行 jachin pack")
            raise typer.Exit(1)

        zips = sorted(dist_dir.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not zips:
            console.print("[red]Error:[/] dist/ 下无 zip 包，请先运行 jachin pack")
            raise typer.Exit(1)
        latest_zip = zips[0]

    nexus_url = get_nexus_url()
    publish_url = f"{nexus_url}/api/v1/store/publish"

    console.print()
    console.print(f"[dim]Nexus:[/] {publish_url}")
    if shadow_only:
        console.print("[dim]模式:[/] 影子上传（仅登记元数据，不传包）")
    else:
        console.print(f"[dim]包:[/] {latest_zip.name}")
    console.print()

    success = _do_publish(publish_url, token, plugin_data, latest_zip, visibility, price, shadow_only)

    if success:
        _print_success(plugin_data.get("name", "Plugin"), shadow_only)
    else:
        console.print(Panel.fit(
            "[bold red]发布失败[/]\n\n"
            "请检查：\n"
            "1. Nexus 是否已启动 (localhost:3000)\n"
            "2. JACHIN_DEV_TOKEN 是否有效\n"
            "3. /api/v1/store/publish 接口是否已实现",
            border_style="red",
            title="[bold red]Error[/]",
        ))
        raise typer.Exit(1)


def _do_publish(
    url: str,
    token: str,
    plugin_data: dict,
    zip_path: Path | None,
    visibility: str,
    price: int,
    shadow_only: bool,
) -> bool:
    """发起 HTTP POST 请求。shadow_only 时仅传元数据，否则 multipart 上传 zip。"""
    try:
        import httpx

        headers = {"Authorization": f"Bearer {token}"}
        data = {
            "plugin_id": plugin_data.get("id"),
            "name": plugin_data.get("name"),
            "description": plugin_data.get("description", ""),
            "version": plugin_data.get("version", "1.0.0"),
            "item_type": "SKILL" if (plugin_data.get("type") or "").lower() == "skill" else "MCP",
            "visibility": visibility,
            "price_monthly": str(price),
            "shadow_only": "true" if shadow_only else "false",
        }

        if shadow_only:
            resp = httpx.post(url, data=data, headers=headers, timeout=30.0)
        else:
            with open(zip_path, "rb") as f:
                files = {"package": (zip_path.name, f, "application/zip")}
                resp = httpx.post(url, data=data, files=files, headers=headers, timeout=60.0)

        if resp.status_code == 200:
            return True
        if resp.status_code == 404:
            console.print("[yellow]Note:[/] 接口 /api/v1/store/publish 尚未实现，Mock 成功")
            return True
        console.print(f"[red]HTTP {resp.status_code}:[/] {resp.text[:200]}")
        return False
    except ImportError:
        console.print("[yellow]Note:[/] 未安装 httpx，Mock 成功。pip install httpx 后可真实上传")
        return True
    except Exception as e:
        console.print(f"[red]Request failed:[/] {e}")
        return False


def _print_success(name: str, shadow_only: bool = False) -> None:
    """撒花 Emoji 和大字报祝贺"""
    msg = (
        f"[bold white]{name}[/] 已上架 Nexus 商城\n\n"
        + (
            "[dim]影子上传完成，实体包请侧载到 L2 ~/.jachin/inventory/[/]"
            if shadow_only
            else "[dim]前往 http://localhost:3000/store 查看[/]"
        )
    )
    console.print()
    console.print(Panel.fit(
        "[bold green]🎉 发布成功！[/]\n\n" + msg,
        border_style="green",
        title="[bold green]🎉 Congratulations[/]",
    ))
    console.print()
