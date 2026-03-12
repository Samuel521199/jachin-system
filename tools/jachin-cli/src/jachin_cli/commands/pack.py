"""
jachin pack - 打包与极度严苛的校验
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

console = Console()

PLUGIN_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(\.[a-z][a-z0-9]*)+$")


def pack_cmd() -> None:
    """读取 plugin.json，校验后打包"""
    console.print()
    console.print(Panel.fit(
        "[bold cyan]Jachin Pack[/] - 打包与校验",
        border_style="cyan",
    ))
    console.print()

    cwd = Path.cwd()
    plugin_path = cwd / "plugin.json"

    if not plugin_path.exists():
        _error("plugin.json 不存在", "请在项目根目录运行 jachin pack，或先执行 jachin init")
        raise typer.Exit(1)

    try:
        data = json.loads(plugin_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        _error("plugin.json 解析失败", str(e))
        raise typer.Exit(1)

    errors = _validate(data, cwd)
    if errors:
        _print_validation_errors(errors)
        raise typer.Exit(1)

    plugin_id = data["id"]
    version = data.get("version", "1.0.0")
    item_type = (data.get("type") or "skill").lower()

    dist_dir = cwd / "dist"
    dist_dir.mkdir(exist_ok=True)
    zip_name = f"{plugin_id}_v{version}.zip"
    zip_path = dist_dir / zip_name

    # 排除的文件
    exclude = {"dist", ".git", "__pycache__", "node_modules", ".venv", "venv", "target", "Cargo.lock"}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("打包中...", total=None)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in cwd.rglob("*"):
                if f.is_file():
                    rel = f.relative_to(cwd)
                    if any(part in exclude for part in rel.parts):
                        continue
                    if rel.name == zip_name and "dist" in rel.parts:
                        continue
                    zf.write(f, rel)
        progress.update(task, completed=True)

    console.print()
    console.print(Panel.fit(
        f"[bold green]✓ 打包成功[/]\n\n"
        f"[cyan]输出:[/] {zip_path}\n"
        f"[dim]运行 jachin publish 一键上云[/]",
        border_style="green",
        title="[bold green]Success[/]",
    ))
    console.print()


def _validate(data: dict, cwd: Path) -> list[tuple[str, str]]:
    """严苛校验，返回 [(错误摘要, 修复建议), ...]"""
    errors: list[tuple[str, str]] = []

    # ID 格式
    plugin_id = data.get("id")
    if not plugin_id:
        errors.append(("缺少 id 字段", "在 plugin.json 中添加 \"id\": \"com.example.hello\""))
    elif not isinstance(plugin_id, str):
        errors.append(("id 必须为字符串", "如 \"id\": \"com.example.hello\""))
    elif not PLUGIN_ID_PATTERN.match(plugin_id):
        errors.append(("id 格式错误", "应为反向域名，如 com.example.hello，仅小写字母数字与点"))

    # 必填字段
    if not data.get("name"):
        errors.append(("缺少 name 字段", "添加 \"name\": \"插件名称\""))
    if not data.get("description"):
        errors.append(("缺少 description 字段", "添加 \"description\": \"插件描述\""))
    if not data.get("version"):
        errors.append(("缺少 version 字段", "添加 \"version\": \"1.0.0\""))

    # 入口文件
    item_type = (data.get("type") or "skill").lower()
    if item_type == "skill":
        entry = data.get("entry") or "main.wasm"
        entry_path = cwd / entry
        if not entry_path.exists():
            errors.append(
                (f"入口文件不存在: {entry}", "编译 Wasm 后将 main.wasm 放入项目根目录"),
            )
    elif item_type == "mcp":
        mcp = data.get("mcp_servers")
        if not mcp or not isinstance(mcp, list):
            errors.append(("MCP 需配置 mcp_servers", "添加 mcp_servers 数组，含 command 与 args"))
        elif mcp and not (mcp[0].get("command") if isinstance(mcp[0], dict) else False):
            errors.append(("mcp_servers 需含 command", "如 \"command\": \"npx\", \"args\": [\"-y\", \"xxx\"]"))

    return errors


def _error(title: str, detail: str) -> None:
    console.print(Panel.fit(
        f"[bold red]{title}[/]\n\n{detail}",
        border_style="red",
        title="[bold red]Error[/]",
    ))
    console.print()


def _print_validation_errors(errors: list[tuple[str, str]]) -> None:
    table = Table(title="[bold red]校验失败[/]", show_header=True, header_style="red")
    table.add_column("错误", style="red")
    table.add_column("修复建议", style="yellow")
    for err, fix in errors:
        table.add_row(err, fix)
    console.print(Panel(table, border_style="red", title="[bold red]Validation Error[/]"))
    console.print()
