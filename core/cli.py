"""
Jachin Nexus Layer 2 CLI
python -m core.cli pair — L1↔L2 **辅助**配对（无头/SSH/恢复）；主路径见 L2 /gateway：
L1 邮箱+密码 或「Nexus 账号登录」。docs/L1_L2_PAIRING_AND_WEB_BRIDGE.md
refresh-tenant：租户字段修复
"""
from __future__ import annotations

import json
import time
from pathlib import Path

# 尽早将 .env 转为 UTF-8（Windows 可能产生 UTF-16），避免 pydantic-settings 等读取失败
def _ensure_env_utf8() -> None:
    for _p in [Path.cwd(), Path(__file__).resolve().parent.parent]:
        _e = _p / ".env"
        if _e.exists():
            try:
                _e.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                try:
                    _e.write_text(_e.read_text(encoding="utf-16"), encoding="utf-8")
                except Exception:
                    pass
            break

_ensure_env_utf8()

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
@click.version_option(version="0.8.104", prog_name="Jachin Nexus CLI")
def cli() -> None:
    """Jachin Nexus Layer 2 CLI（L1↔L2 辅助配对、refresh-tenant 等）"""
    pass


@cli.command()
@click.option(
    "--base-url",
    default="http://localhost:3000",
    envvar="NEXUS_BASE_URL",
    help="Layer 1 Nexus Console 地址",
)
@click.option(
    "--recover",
    is_flag=True,
    help="恢复模式：云端已配对成功但本地未保存时，用 6 位码取回凭证",
)
@click.option("--code", default="", help="恢复时使用的 6 位配对码（与 --recover 配合）")
def pair(base_url: str, recover: bool, code: str) -> None:
    """L1↔L2 辅助配对：6 位码 + L1 网页确认（有 Web 时优先用 L2 /gateway「Nexus 账号登录」）。"""
    base_url = base_url.rstrip("/")

    # 恢复模式：云端已确认但 CLI 未写入配置时，用码取回凭证
    if recover and code:
        code_clean = code.strip().upper().replace("-", "").replace(" ", "")[:6]
        if len(code_clean) != 6:
            console.print("[red][ERROR][/red] 恢复码必须为 6 位")
            raise SystemExit(1)
        console.print(ASCII_ART)
        console.print()
        console.print("[cyan]恢复模式：从云端取回配对凭证...[/cyan]")
        try:
            r = httpx.get(
                f"{base_url}/api/v1/pairing/status",
                params={"code": code_clean},
                timeout=10.0,
            )
            r.raise_for_status()
            data = r.json()
        except httpx.RequestError as e:
            console.print(f"[red][ERROR][/red] 无法连接 Layer 1: {e}")
            raise SystemExit(1)
        except httpx.HTTPStatusError as e:
            console.print(f"[red][ERROR][/red] 请求失败: HTTP {e.response.status_code}")
            raise SystemExit(1)

        st = data.get("status")
        if st != "success":
            console.print(f"[red][ERROR][/red] 恢复失败: {data.get('error', st)}")
            console.print("  可能原因：配对码已过期、未在云端确认、或码错误")
            raise SystemExit(1)

        access_token = data.get("access_token")
        instance_id = data.get("instance_id", "dev-layer2-001")
        l1_user_id = data.get("l1_user_id")
        nexus_base_url = (data.get("nexus_base_url") or base_url).rstrip("/")
        if not access_token:
            console.print("[red][ERROR][/red] 云端未返回 access_token")
            raise SystemExit(1)

        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        cfg = {
            "instance_id": instance_id,
            "access_token": access_token,
            "nexus_base_url": nexus_base_url,
            "pairing_code": code_clean,
        }
        if l1_user_id:
            cfg["l1_user_id"] = l1_user_id
        tid = data.get("tenant_id")
        if tid:
            cfg["tenant_id"] = tid
            cfg["sync_tenant_ids"] = [tid]
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        console.print(Panel("[green]✅ 凭证已恢复并写入本地配置[/green]", border_style="green"))
        console.print(f"  配置路径: [cyan]{CONFIG_PATH}[/cyan]")
        console.print(f"  instance_id: [dim]{instance_id}[/dim]")
        console.print()
        console.print("[bold cyan]🚀 可重新运行 start-layer2.ps1 启动 daemon[/bold cyan]")
        return

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
                "core_version": "0.8.104",
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
        body = (e.response.text or "").strip()[:500]
        console.print(f"[red][ERROR][/red] 配对请求失败: HTTP {e.response.status_code}")
        if body:
            console.print(f"[dim]{body}[/dim]")
        console.print(
            "[dim]常见原因：L1 已配置 DATABASE_URL 但未执行迁移（缺 edge_agents 表）、"
            "或 Postgres 连不上。请查 L1 容器日志 pairing/request、并在 cloud/nexus 执行 "
            "npm run db:migrate。[/dim]"
        )
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
    l1_user_id = status_data.get("l1_user_id")
    nexus_base_url = status_data.get("nexus_base_url", base_url)

    console.print()
    console.print(Panel(
        "[green]✅ 授权成功！正在下发中枢公钥...[/green]",
        border_style="green",
    ))

    # 6. 写入配置（含 pairing_code、l1_user_id 用于 L2 创世管理员绑定）
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    cfg = {
        "instance_id": instance_id,
        "access_token": access_token,
        "nexus_base_url": nexus_base_url.rstrip("/"),
        "pairing_code": short_code,
    }
    if l1_user_id:
        cfg["l1_user_id"] = l1_user_id
    tenant_id = status_data.get("tenant_id")
    if tenant_id:
        cfg["tenant_id"] = tenant_id
        cfg["sync_tenant_ids"] = [tenant_id]
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


@cli.command("refresh-tenant")
@click.option(
    "--base-url",
    default="",
    envvar="NEXUS_BASE_URL",
    help="Layer 1 地址；默认使用 nexus_config.json 中的 nexus_base_url",
)
def refresh_tenant(base_url: str) -> None:
    """已配对机器：从 L1 拉取 organizations.id 写入 tenant_id（修复 manifest 用错 l1_user_id）"""
    if not CONFIG_PATH.exists():
        console.print(f"[red][ERROR][/red] 未找到配置: {CONFIG_PATH}")
        raise SystemExit(1)
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        console.print(f"[red][ERROR][/red] 无法解析配置: {e}")
        raise SystemExit(1)
    instance_id = cfg.get("instance_id")
    if not instance_id:
        console.print("[red][ERROR][/red] 配置中缺少 instance_id")
        raise SystemExit(1)
    url = (base_url or cfg.get("nexus_base_url") or "http://localhost:3000").rstrip("/")
    try:
        r = httpx.get(
            f"{url}/api/v1/pairing/status",
            params={"session_id": instance_id},
            timeout=10.0,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        console.print(f"[red][ERROR][/red] 请求失败: {e}")
        raise SystemExit(1)
    if data.get("status") != "success":
        console.print(f"[red][ERROR][/red] 边缘未激活: {data.get('error', data.get('status'))}")
        raise SystemExit(1)
    tid = data.get("tenant_id")
    if not tid:
        console.print(
            "[yellow]云端未返回 tenant_id[/yellow]（可能为旧版 L1 或未写入 organization_id）。"
            "请在 Console 用同一账号再次确认配对或升级 Nexus 后重试。"
        )
        raise SystemExit(1)
    cfg["tenant_id"] = tid
    cfg["sync_tenant_ids"] = [tid]
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"[green]✅ 已写入 tenant_id=[/green][cyan]{tid}[/cyan] → {CONFIG_PATH}")


@cli.command()
@click.option("--password", default="admin123", help="新密码（默认 admin123）")
def reset_admin(password: str) -> None:
    """重置 L2 网关管理员密码为 admin/<password>（登录 401 时使用）"""
    import secrets
    import bcrypt
    from core.db import get_connection

    conn = get_connection()
    pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
    try:
        row = conn.execute(
            "SELECT id FROM gateway_admins WHERE username = 'admin' LIMIT 1"
        ).fetchone()
        if not row:
            # 加载 nexus_config 以获取 l1_user_id（若已配对）
            cfg = {}
            if CONFIG_PATH.exists():
                try:
                    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                except Exception:
                    pass
            main_user_id = cfg.get("l1_user_id") or cfg.get("instance_id") or f"gw-admin-{secrets.token_hex(4)}"
            conn.execute(
                """
                INSERT INTO gateway_admins (id, username, password_hash, main_user_id, role)
                VALUES (?, 'admin', ?, ?, 'admin')
                """,
                (f"gw-admin-{secrets.token_hex(4)}", pw_hash, main_user_id),
            )
            conn.commit()
            console.print("[green]✅ 已创建 admin 账号，密码: %s[/green]" % password)
        else:
            conn.execute(
                "UPDATE gateway_admins SET password_hash = ? WHERE username = 'admin'",
                (pw_hash,),
            )
            conn.commit()
            console.print("[green]✅ 已重置 admin 密码为: %s[/green]" % password)
        console.print("  登录: http://localhost:18888/admin")
    except Exception as e:
        console.print("[red][ERROR][/red] %s" % e)
        raise SystemExit(1)
    finally:
        conn.close()


@cli.command()
@click.argument("user_input", required=False, default="")
@click.option(
    "--daemon",
    "use_daemon",
    is_flag=True,
    help="使用已运行的 Daemon 处理（打通 HITL 沙箱：桌面精灵弹窗授权）",
)
def shell(user_input: str, use_daemon: bool) -> None:
    """极客模式：注入全息感官总线，brain_worker 处理。--daemon 时由 Daemon 处理并支持 HITL 弹窗"""
    import asyncio
    from core.event_bus import (
        get_bus,
        emit_omni_input,
        SensoryInputEvent,
        SensoryOutputEvent,
    )

    inp = user_input or "帮我瞅瞅 workspace 里的 target.txt 写了啥"
    console.print(f"[cyan][Ignition][/cyan] 输入: [yellow]{inp}[/yellow]")
    if use_daemon:
        console.print("[dim]  → 注入总线，由 Daemon 处理（Tauri 可弹 HITL 授权）...[/dim]")
    else:
        console.print("[dim]  → 注入全息感官总线，brain_worker 处理中...[/dim]")
    console.print()

    def _react_step_printer(step_type: str, content: str, run_id: str = "") -> None:
        c = (content or "")[:200] + ("..." if len(content or "") > 200 else "")
        prefix = f"[RunID:{run_id[:8]}] " if run_id else ""
        if step_type == "thought":
            console.print(f"  {prefix}[dim][Thought][/dim] {c}")
        elif step_type == "action":
            console.print(f"  {prefix}[purple][Action][/purple] {c}")
        elif step_type == "observation":
            console.print(f"  {prefix}[cyan][Observation][/cyan] {c}")

    async def _go_standalone() -> None:
        """ standalone：本进程启动 brain_worker（无 HITL 弹窗，core:shell_exec 会挂起）"""
        bus = get_bus()
        bus.set_step_callback(_react_step_printer)
        bus.start_brain_worker()

        done = asyncio.Event()

        async def output_handler(ev: SensoryOutputEvent) -> None:
            if ev.source_ref != "cli":
                return
            if ev.action_type == "text":
                console.print(Panel(ev.content, title="[green]Final Answer[/green]", border_style="green"))
            done.set()

        bus.subscribe("output.cli", output_handler)

        await bus.publish_input(SensoryInputEvent(source="cli", intent=inp, metadata={}))

        try:
            await asyncio.wait_for(done.wait(), timeout=120.0)
        except asyncio.TimeoutError:
            console.print("[red]⚠ 超时：brain_worker 未在 120 秒内返回[/red]")

    async def _go_daemon() -> None:
        """ --daemon：仅注入 SQLite，连接 ws://localhost:18881/sensory 等待 Daemon 广播结果 """
        emit_omni_input("cli", inp, {})
        try:
            import websockets
            async with websockets.connect(
                "ws://localhost:18881/sensory",
                open_timeout=5.0,
                close_timeout=2.0,
            ) as ws:
                console.print("[dim] 已连接 Daemon 全息通道，等待推理与 HITL...[/dim]")
                while True:
                    msg = await asyncio.wait_for(ws.recv(), timeout=180.0)
                    try:
                        data = json.loads(msg)
                        st = (data.get("step_type") or "").lower()
                        content = data.get("content", "")
                        if st in ("answer", "rejected", "error"):
                            title = "[green]Final Answer[/green]" if st == "answer" else f"[red]{st}[/red]"
                            console.print(Panel(content, title=title, border_style="green" if st == "answer" else "red"))
                            return
                        elif st == "hitl_required":
                            console.print("[yellow]  ⏳ 桌面精灵已弹出授权框，请点击【授权】或【拒绝】[/yellow]")
                    except json.JSONDecodeError:
                        pass
        except (ConnectionRefusedError, OSError) as e:
            console.print(f"[red]❌ 无法连接 Daemon (ws://localhost:18881/sensory): {e}[/red]")
            console.print("[dim]请先运行: python -m core.cli daemon[/dim]")
            raise SystemExit(1)
        except asyncio.TimeoutError:
            console.print("[red]⚠ 超时：Daemon 未在 180 秒内返回[/red]")

    if use_daemon:
        asyncio.run(_go_daemon())
    else:
        asyncio.run(_go_standalone())


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
