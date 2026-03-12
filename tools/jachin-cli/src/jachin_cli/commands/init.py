"""
jachin init - 交互式初始化插件项目脚手架
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

console = Console()

# Plugin ID 格式：反向域名，如 com.example.hello
PLUGIN_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(\.[a-z][a-z0-9]*)+$")


def _validate_plugin_id(plugin_id: str) -> bool:
    return bool(PLUGIN_ID_PATTERN.match(plugin_id))


def init_cmd() -> None:
    """交互式创建插件项目"""
    console.print()
    console.print(Panel.fit(
        "[bold cyan]Jachin Init[/] - 插件项目脚手架",
        border_style="cyan",
    ))
    console.print()

    plugin_id = Prompt.ask(
        "[bold]Plugin ID[/] (如 com.example.hello)",
        default="com.example.hello",
    ).strip()
    if not plugin_id:
        console.print("[red]Error:[/] Plugin ID 不能为空")
        raise typer.Exit(1)
    if not _validate_plugin_id(plugin_id):
        console.print("[red]Error:[/] Plugin ID 格式错误，应为反向域名，如 com.example.hello")
        raise typer.Exit(1)

    plugin_name = Prompt.ask("[bold]Plugin Name[/]", default=plugin_id.split(".")[-1].title()).strip()
    if not plugin_name:
        plugin_name = plugin_id.split(".")[-1].title()

    description = Prompt.ask("[bold]Description[/]", default="").strip() or "暂无描述"

    item_type = Prompt.ask(
        "[bold]Item Type[/]",
        choices=["SKILL", "MCP"],
        default="SKILL",
    )

    cwd = Path.cwd()

    if item_type == "SKILL":
        _create_skill_scaffold(cwd, plugin_id, plugin_name, description)
    else:
        _create_mcp_scaffold(cwd, plugin_id, plugin_name, description)

    _print_success(plugin_id, plugin_name, item_type)


def _create_skill_scaffold(cwd: Path, plugin_id: str, plugin_name: str, description: str) -> None:
    """创建 SKILL 类型脚手架"""
    plugin_json = {
        "id": plugin_id,
        "name": plugin_name,
        "version": "1.0.0",
        "description": description,
        "type": "skill",
        "entry": "main.wasm",
        "permissions": [],
        "schema": {
            "input": {
                "type": "object",
                "properties": {"input": {"type": "string", "description": "输入参数"}},
                "required": [],
            },
        },
        "jmp_version": "2.0",
    }

    (cwd / "plugin.json").write_text(
        json.dumps(plugin_json, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    src_dir = cwd / "src"
    src_dir.mkdir(exist_ok=True)

    # Rust Wasm 模板（编译提示）
    main_rs = '''// Jachin SKILL - Rust/Wasm 模板
// 编译为 Wasm: wasm-pack build --target web
// 输出: pkg/xxx_bg.wasm -> 重命名为 main.wasm 放入项目根目录

#[no_mangle]
pub extern "C" fn run(_input: *const u8, _len: usize) -> i32 {
    0
}
'''
    (src_dir / "main.rs").write_text(main_rs, encoding="utf-8")

    # C Wasm 模板
    main_c = '''/* Jachin SKILL - C/Wasm 模板
 * 使用 Emscripten 编译: emcc main.c -o main.wasm -s EXPORTED_FUNCTIONS='["_run"]'
 */
int run(const char* input, int len) {
    return 0;
}
'''
    (src_dir / "main.c").write_text(main_c, encoding="utf-8")

    # 空占位符 main.wasm（minimal wasm magic + version）
    wasm_placeholder = bytes([0x00, 0x61, 0x73, 0x6D, 0x01, 0x00, 0x00, 0x00])  # \0asm v1
    (cwd / "main.wasm").write_bytes(wasm_placeholder)


def _create_mcp_scaffold(cwd: Path, plugin_id: str, plugin_name: str, description: str) -> None:
    """创建 MCP 类型脚手架"""
    plugin_json = {
        "id": plugin_id,
        "name": plugin_name,
        "version": "1.0.0",
        "description": description,
        "type": "mcp",
        "mcp_servers": [
            {
                "id": plugin_id,
                "command": "npx",
                "args": ["-y", "mcp-server-example"],
            },
        ],
    }

    (cwd / "plugin.json").write_text(
        json.dumps(plugin_json, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # MCP 通常不需要 wasm，创建说明文件
    (cwd / "README.md").write_text(
        f"# {plugin_name}\n\n{description}\n\n## MCP 配置\n\n编辑 plugin.json 中的 mcp_servers.command 和 args。\n",
        encoding="utf-8",
    )


def _print_success(plugin_id: str, plugin_name: str, item_type: str) -> None:
    """输出成功提示与下一步指引"""
    console.print()
    console.print(Panel.fit(
        f"[bold green]✓ 脚手架创建成功[/]\n\n"
        f"[cyan]Plugin ID:[/] {plugin_id}\n"
        f"[cyan]Name:[/] {plugin_name}\n"
        f"[cyan]Type:[/] {item_type}",
        border_style="green",
        title="[bold green]Success[/]",
    ))

    table = Table(title="下一步", show_header=False)
    table.add_column("步骤", style="cyan")
    table.add_column("说明", style="white")
    if item_type == "SKILL":
        table.add_row("1", "编译 Wasm: wasm-pack build --target web (Rust) 或 emcc main.c -o main.wasm (C)")
        table.add_row("2", "将生成的 .wasm 文件放入项目根目录，命名为 main.wasm")
        table.add_row("3", "运行 [bold]jachin pack[/] 打包")
    else:
        table.add_row("1", "编辑 plugin.json 中的 mcp_servers，配置 command 与 args")
        table.add_row("2", "运行 [bold]jachin pack[/] 打包")
    table.add_row("3", "运行 [bold]jachin publish[/] 上云发布")

    console.print()
    console.print(table)
    console.print()
