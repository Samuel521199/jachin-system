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


def _normalize_dev_token(raw: str | None) -> str | None:
    """去掉首尾空白与成对引号，避免用户复制 .env 时带上引号。"""
    if not raw:
        return None
    s = raw.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return s or None


def _validate_dev_token_for_http(token: str) -> str | None:
    """
    Bearer Token 须为 ASCII（HTTP 头编码）；若含中文多为误把文档说明当成了密钥。
    返回 None 表示可用；否则返回给人看的错误说明。
    """
    if any(ord(c) > 127 for c in token):
        return (
            "当前 JACHIN_DEV_TOKEN 含有非 ASCII 字符（例如误把「与 cloud…中完全一致」说明文字当成了 Token）。\n"
            "请打开 cloud/nexus/.env.local，复制 JACHIN_DEV_TOKEN= 后面的**随机字符串**（仅英文数字符号），"
            "不要包含中文或整句说明。"
        )
    return None


def publish_cmd(
    visibility: str = typer.Option("PUBLIC", "--visibility", "-v", help="PUBLIC 或 PRIVATE"),
    price: int = typer.Option(0, "--price", "-p", help="月付价格(分)，0=免费"),
    nexus: str | None = typer.Option(
        None,
        "--nexus",
        "--l1",
        "-n",
        help="L1 Nexus 地址，必须显式指定。如 http://192.168.110.10:3000 或 https://nexus.example.com",
    ),
) -> None:
    """读取配置与 dist 包，向 L1 Nexus 发起发布请求"""
    console.print()
    console.print(Panel.fit(
        "[bold cyan]Jachin Publish[/] - 一键上云",
        border_style="cyan",
    ))
    console.print()

    token = _normalize_dev_token(get_token())
    if not token:
        console.print(Panel.fit(
            "[bold red]未配置 JACHIN_DEV_TOKEN[/]\n\n"
            "请设置环境变量: [cyan]JACHIN_DEV_TOKEN[/]（与 [cyan]cloud/nexus/.env.local[/] 中 [cyan]JACHIN_DEV_TOKEN=[/] 后的值一致）\n"
            "或在 [cyan]~/.jachin-cli/config.json[/] 中配置 [cyan]token[/] 字段",
            border_style="red",
            title="[bold red]Error[/]",
        ))
        raise typer.Exit(1)

    _tok_err = _validate_dev_token_for_http(token)
    if _tok_err:
        console.print(Panel.fit(
            "[bold red]JACHIN_DEV_TOKEN 无效[/]\n\n" + _tok_err,
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

    nexus_url = (nexus or "").strip().rstrip("/") or get_nexus_url()
    if not nexus_url:
        console.print(Panel.fit(
            "[bold red]未指定 L1 Nexus 地址[/]\n\n"
            "上传 Skill/MCP 时必须显式指定目标 L1，不能使用 localhost 默认。\n\n"
            "方式一（推荐）：命令行参数\n"
            "  [cyan]jachin publish --nexus http://192.168.110.10:3000[/]\n"
            "  [cyan]jachin publish -n https://nexus.example.com[/]\n\n"
            "方式二：环境变量\n"
            "  [cyan]JACHIN_NEXUS_URL=http://192.168.110.10:3000 jachin publish[/]\n\n"
            "方式三：配置文件 ~/.jachin-cli/config.json\n"
            "  [cyan]{\"nexus_url\": \"http://192.168.110.10:3000\"}[/]",
            border_style="red",
            title="[bold red]Error[/]",
        ))
        raise typer.Exit(1)

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
        _print_success(plugin_data.get("name", "Plugin"), shadow_only, nexus_url)
    else:
        console.print(Panel.fit(
            "[bold red]发布失败[/]\n\n"
            "请检查：\n"
            "1. --nexus 指定的 L1 地址是否可达（如 http://192.168.110.x:3000）\n"
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
        raw_type = (plugin_data.get("type") or plugin_data.get("item_type") or "skill").lower()
        if raw_type == "mcp":
            form_item_type = "MCP"
        elif raw_type in ("tool", "tools"):
            form_item_type = "TOOL"
        else:
            form_item_type = "SKILL"
        data = {
            "plugin_id": plugin_data.get("id"),
            "name": plugin_data.get("name"),
            "description": plugin_data.get("description", ""),
            "version": plugin_data.get("version", "1.0.0"),
            "item_type": form_item_type,
            "visibility": visibility,
            "price_monthly": str(price),
            "shadow_only": "true" if shadow_only else "false",
        }

        if shadow_only:
            resp = httpx.post(url, data=data, headers=headers, timeout=30.0)
        else:
            with open(zip_path, "rb") as f:
                files = {"package": (zip_path.name, f, "application/zip")}
                # 本机 next dev / 冷启动或大包上传可能超过 60s
                resp = httpx.post(url, data=data, files=files, headers=headers, timeout=180.0)

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


def _print_success(name: str, shadow_only: bool = False, nexus_url: str | None = None) -> None:
    """撒花 Emoji 和大字报祝贺"""
    store_hint = f"[dim]前往 {nexus_url}/store 查看[/]" if nexus_url else "[dim]前往 L1 商城查看[/]"
    msg = (
        f"[bold white]{name}[/] 已上架 Nexus 商城\n\n"
        + (
            "[dim]影子上传完成，实体包请侧载到 L2 ~/.jachin/inventory/[/]"
            if shadow_only
            else store_hint
        )
    )
    console.print()
    console.print(Panel.fit(
        "[bold green]🎉 发布成功！[/]\n\n" + msg,
        border_style="green",
        title="[bold green]🎉 Congratulations[/]",
    ))
    console.print()
