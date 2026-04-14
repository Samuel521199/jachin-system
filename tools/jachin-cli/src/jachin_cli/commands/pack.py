"""
jachin pack - 打包与极度严苛的校验

规范: .cursor/rules/076-skill-mcp-upload-spec.mdc, docs/SKILL_MCP_UPLOAD_SPEC.md
- 若存在 config/ 目录，必须包含 config/manifest.yaml
- manifest.yaml 的 writes 路径必须指向包内存在的文件/目录
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
    item_type = (data.get("type") or data.get("item_type") or "skill").lower()

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
    # version 可选：缺失时使用默认 "1.0.0"，兼容旧包

    # 入口文件
    item_type = (data.get("type") or data.get("item_type") or "skill").lower()
    if item_type in ("skill", "tool"):
        entry = data.get("entry") or "main.wasm"
        entry_path = cwd / entry
        if not entry_path.exists():
            hint = (
                "编译 Wasm 后将 main.wasm 放入项目根目录"
                if item_type == "skill"
                else "原子工具包请提供与 entry 一致的 Wasm 或宿主约定入口文件"
            )
            errors.append((f"入口文件不存在: {entry}", hint))
    elif item_type == "mcp":
        runtime_tier = (data.get("runtime_tier") or "L3_LOCAL").upper()
        if runtime_tier == "L2_GATEWAY":
            console.print(
                "[yellow]提示：长期默认请使用 runtime_tier=L3_LOCAL，"
                "并以 stdio_server（官方 MCP）或 tools[]（Python）在 L3 执行；"
                "L2_GATEWAY 仅保留兼容。[/]"
            )
            mcp = data.get("mcp_servers")
            if not mcp or not isinstance(mcp, list):
                errors.append(("L2_GATEWAY MCP 需配置 mcp_servers", "添加 mcp_servers 数组，含 command 与 args"))
            elif mcp and not (mcp[0].get("command") if isinstance(mcp[0], dict) else False):
                errors.append(("mcp_servers 需含 command", "如 \"command\": \"npx\", \"args\": [\"-y\", \"xxx\"]"))
        if runtime_tier == "L3_LOCAL":
            stdio_blk = data.get("stdio_server")
            has_stdio = (
                isinstance(stdio_blk, dict)
                and str(stdio_blk.get("command") or "").strip() != ""
            )
            tools = data.get("tools") or []
            if isinstance(tools, dict):
                tools = list(tools.values()) if tools else []
            has_python = False
            if isinstance(tools, list):
                for t in tools:
                    if isinstance(t, dict) and (t.get("module") or "").strip() and (t.get("function") or "").strip():
                        has_python = True
                        break
            if not has_stdio and not has_python:
                errors.append(
                    (
                        "L3_LOCAL MCP 需 stdio_server 或 tools[]",
                        "二选一：① \"stdio_server\": {\"id\":\"...\",\"command\":\"npx\",\"args\":[\"-y\",\"@pkg\"]} "
                        "② \"tools\": [{\"id\":\"x\",\"module\":\"tools.x\",\"function\":\"run\",\"params\":[\"input\"]}]",
                    ),
                )

    # 077: Skill 依赖 MCP — required_mcps 格式校验（TOOL 一般无 required_mcps）
    if item_type == "skill":
        errors.extend(_validate_required_mcps(data))

    # 076: config 随包规范 — 若 config/ 存在则必须含 manifest.yaml
    errors.extend(_validate_config(cwd))

    return errors


def _validate_required_mcps(data: dict) -> list[tuple[str, str]]:
    """077 规范：Skill 的 required_mcps 格式校验。规范: .cursor/rules/077-skill-mcp-dependency.mdc"""
    errors: list[tuple[str, str]] = []
    rm = data.get("required_mcps")
    if rm is None:
        return errors
    if not isinstance(rm, list):
        errors.append(("required_mcps 必须为数组", "如 \"required_mcps\": [\"mcp:com.jachin.boss.atom\"]"))
        return errors
    for i, x in enumerate(rm):
        if not isinstance(x, str) or not x.strip():
            errors.append((f"required_mcps[{i}] 必须为非空字符串", "如 \"mcp:com.jachin.xxx\""))
            continue
        raw = x.strip()
        pid = raw[4:].strip().lower() if raw.lower().startswith("mcp:") else raw.strip().lower()
        if not pid:
            errors.append((f"required_mcps[{i}] 格式错误", "应为 mcp:com.example.xxx 或 com.example.xxx"))
        elif not PLUGIN_ID_PATTERN.match(pid):
            errors.append((f"required_mcps[{i}] 的 pluginId 格式错误", "应为反向域名，如 com.jachin.xxx，仅小写字母数字与点"))
    return errors


def _validate_config(cwd: Path) -> list[tuple[str, str]]:
    """校验 config/ 与 manifest.yaml。规范 076、docs/SKILL_MCP_UPLOAD_SPEC.md"""
    errors: list[tuple[str, str]] = []
    config_dir = cwd / "config"
    manifest_path = config_dir / "manifest.yaml"

    if not config_dir.exists() or not config_dir.is_dir():
        return errors

    if not manifest_path.exists() or not manifest_path.is_file():
        errors.append(
            ("config/ 存在但缺少 manifest.yaml", "添加 config/manifest.yaml，或移除 config/ 目录。规范: docs/SKILL_MCP_UPLOAD_SPEC.md"),
        )
        return errors

    try:
        import yaml
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        errors.append((f"config/manifest.yaml 解析失败: {e}", "检查 YAML 语法"))
        return errors

    if not raw or not isinstance(raw, dict):
        errors.append(("config/manifest.yaml 必须为有效 YAML 对象", "参考 docs/SKILL_MCP_UPLOAD_SPEC.md"))
        return errors

    writes = raw.get("writes")
    if not writes or not isinstance(writes, list):
        errors.append(("config/manifest.yaml 需含 writes 数组", "参考 docs/SKILL_MCP_UPLOAD_SPEC.md"))
        return errors

    for i, entry in enumerate(writes):
        if not isinstance(entry, dict):
            continue
        path_str = entry.get("path")
        if not path_str or not isinstance(path_str, str):
            continue
        path_str = path_str.strip().lstrip("/")
        if path_str.startswith("config/"):
            rel_in_config = path_str[7:]
        else:
            rel_in_config = path_str
        src_path = config_dir / rel_in_config
        entry_type = (entry.get("type") or "file").lower()
        if entry_type == "directory":
            if not src_path.exists() or not src_path.is_dir():
                errors.append(
                    (f"writes[{i}].path={path_str} 指向的目录不存在", f"创建 {src_path} 或修正 path"),
                )
        else:
            if not src_path.exists() or not src_path.is_file():
                errors.append(
                    (f"writes[{i}].path={path_str} 指向的文件不存在", f"创建 {src_path} 或修正 path"),
                )

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
