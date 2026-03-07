"""
Jachin CLI - Jachin Nexus 开发者生态命令行工具

神兵利器：脚手架、打包、上云。
"""
from __future__ import annotations

import sys

# Windows 控制台 UTF-8：避免 Rich 输出中文时 UnicodeEncodeError (gbk)
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import typer
from rich.console import Console

from jachin_cli.commands.init import init_cmd
from jachin_cli.commands.pack import pack_cmd
from jachin_cli.commands.publish import publish_cmd

app = typer.Typer(
    name="jachin",
    help="[bold cyan]Jachin CLI[/] - Jachin Nexus 开发者生态命令行工具",
    add_completion=False,
)

console = Console()


@app.callback()
def main_callback() -> None:
    """Jachin CLI 主入口"""
    pass


app.command(name="init", help="初始化插件项目脚手架")(init_cmd)
app.command(name="pack", help="打包并校验插件")(pack_cmd)
app.command(name="publish", help="一键上云发布到 Nexus 商城")(publish_cmd)


if __name__ == "__main__":
    app()
