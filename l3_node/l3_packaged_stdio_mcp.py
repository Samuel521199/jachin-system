"""
L3_LOCAL MCP 制品中的 **stdio 声明式** 包：仅 ``plugin.json`` + ``stdio_server{command,args,env}``，
无 Python ``tools[]`` 模块。L3 启动时注入 ``MCPManager.add_server``，与 ``mcp_servers.json`` 行为一致。

占位符（command / args / env 字符串中）：
- ``__PROJECT_ROOT__`` / ``__PROJECT_ROOT__/sub`` → ``l3_node.paths.get_app_root()``
- ``__JACHIN_HOME__`` → ``~/.jachin``（尊重 ``JACHIN_HOME`` 环境变量）
- ``__JACHIN_WORKSPACE__`` → ``~/.jachin/workspace``（不存在则创建）
- ``__JACHIN_MCP_PYTHON__`` / ``__JACHIN_MCP_NODE__`` → 见 ``core.mcp_embedded_runtime``

见 docs/SKILL_MCP_UPLOAD_SPEC.md §2.x。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

def _l3_mcp_cache_root() -> Path:
    h = os.environ.get("JACHIN_HOME")
    base = Path(h).expanduser().resolve() if h else Path.home() / ".jachin"
    return base / "l3_mcp_cache"


def _resolve_placeholders_l3(s: str) -> str:
    if not isinstance(s, str) or not s:
        return s
    from l3_node.paths import get_app_root

    root = get_app_root().resolve()
    _home = os.environ.get("JACHIN_HOME")
    jh = Path(_home).expanduser().resolve() if _home else Path.home() / ".jachin"
    ws = jh / "workspace"
    try:
        ws.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    out = s
    if "__JACHIN_WORKSPACE__" in out:
        out = out.replace("__JACHIN_WORKSPACE__", str(ws.resolve()))
    if "__JACHIN_HOME__" in out:
        out = out.replace("__JACHIN_HOME__", str(jh.resolve()))
    if "__PROJECT_ROOT__" in out:
        if "__PROJECT_ROOT__/" in out or out.strip() == "__PROJECT_ROOT__":
            sub = out.replace("__PROJECT_ROOT__/", "").replace("__PROJECT_ROOT__", "").lstrip("/\\")
            out = str((root / sub).resolve()) if sub else str(root)
        else:
            out = out.replace("__PROJECT_ROOT__", str(root))
    try:
        from core.mcp_embedded_runtime import inject_embedded_tokens

        out = inject_embedded_tokens(out)
    except Exception:
        pass
    return out


def _resolve_stdio_args(args: list[Any]) -> list[Any]:
    from core.inventory_scanner import _prune_mcp_filesystem_roots

    resolved: list[Any] = []
    for a in args or []:
        if isinstance(a, str):
            resolved.append(_resolve_placeholders_l3(a))
        else:
            resolved.append(a)
    pruned = _prune_mcp_filesystem_roots(resolved)
    return pruned if pruned is not None else resolved


def _resolve_stdio_env(env: Optional[dict[str, Any]]) -> Optional[dict[str, str]]:
    if not env or not isinstance(env, dict):
        return None
    out: dict[str, str] = {}
    for k, v in env.items():
        if isinstance(v, str):
            out[str(k)] = _resolve_placeholders_l3(v)
        else:
            out[str(k)] = str(v)
    merged = {**os.environ, **out}
    return merged


def _iter_package_dirs() -> list[Path]:
    import sys

    dirs: list[Path] = []
    cache = _l3_mcp_cache_root()
    if cache.exists():
        for d in cache.iterdir():
            if d.is_dir():
                dirs.append(d)
    if not getattr(sys, "frozen", False):
        try:
            from l3_node.paths import get_app_root

            plugin_root = get_app_root() / "skills_repo" / "plugin"
            if plugin_root.exists():
                for p in plugin_root.iterdir():
                    if p.is_dir() and (p / "plugin.json").exists():
                        try:
                            pl = json.loads((p / "plugin.json").read_text(encoding="utf-8"))
                            if str(pl.get("runtime_tier", "")).upper() == "L3_LOCAL" and (
                                pl.get("item_type", "").lower() == "mcp" or pl.get("type", "").lower() == "mcp"
                            ):
                                dirs.append(p)
                        except Exception:
                            pass
        except Exception:
            pass
    return dirs


def _stdio_block_from_plugin(plugin: dict[str, Any]) -> Optional[dict[str, Any]]:
    """支持 stdio_server 或 mcp_execution_mode=stdio_server。"""
    mode = (plugin.get("mcp_execution_mode") or "").strip().lower()
    blk = plugin.get("stdio_server")
    if isinstance(blk, dict) and (blk.get("command") or "").strip():
        return blk
    if mode == "stdio_server" and isinstance(blk, dict):
        return blk
    return None


async def register_l3_packaged_stdio_mcps() -> int:
    """
    扫描 l3_mcp_cache（及开发态 skills_repo/plugin 下 L3_LOCAL MCP），
    对含 ``stdio_server`` 的 ``plugin.json`` 调用 ``MCPManager.add_server``。
    Returns:
        成功注册的服务器数量（跳过已存在 server_id、连接失败不计入）。
    """
    try:
        from core.mcp_client import get_mcp_manager
    except ImportError:
        return 0

    mgr = get_mcp_manager()
    ok = 0
    for subdir in _iter_package_dirs():
        pj = subdir / "plugin.json"
        if not pj.exists():
            continue
        try:
            plugin = json.loads(pj.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug("[L3PackagedStdio] 跳过 %s: %s", subdir.name, e)
            continue
        it = (plugin.get("item_type") or plugin.get("type") or "").lower()
        if it != "mcp":
            continue
        if str(plugin.get("runtime_tier", "L3_LOCAL")).upper() != "L3_LOCAL":
            continue
        blk = _stdio_block_from_plugin(plugin)
        if not blk:
            continue
        command_raw = (blk.get("command") or "").strip()
        if not command_raw:
            continue
        plugin_id = (plugin.get("id") or subdir.name or "packaged-mcp").strip()
        server_id = (blk.get("id") or blk.get("name") or plugin_id).strip()
        if not server_id:
            server_id = plugin_id
        command = _resolve_placeholders_l3(command_raw)
        try:
            from core.mcp_embedded_runtime import preflight_mcp_stdio_command, resolve_mcp_stdio_command

            command = resolve_mcp_stdio_command(command)
            ok_pf, pf_msg = preflight_mcp_stdio_command(command, server_id)
            if not ok_pf:
                logger.error("[L3PackagedStdio] %s", pf_msg)
                continue
        except Exception as e:
            logger.warning("[L3PackagedStdio] 运行时解析/预检异常 server_id=%s err=%s，跳过", server_id, e)
            continue
        args_raw = blk.get("args") or []
        if not isinstance(args_raw, list):
            args_raw = []
        resolved = _resolve_stdio_args(args_raw)
        from core.inventory_scanner import _prune_mcp_filesystem_roots

        pruned = _prune_mcp_filesystem_roots(list(resolved))
        if pruned is None:
            logger.warning(
                "[L3PackagedStdio] 跳过 %s：server-filesystem 无有效根目录",
                server_id,
            )
            continue
        args_resolved = pruned

        env_merged = _resolve_stdio_env(blk.get("env") if isinstance(blk.get("env"), dict) else None)
        cfg: dict[str, Any] = {
            "id": server_id,
            "command": command,
            "args": args_resolved,
        }
        if env_merged is not None:
            cfg["env"] = env_merged
        try:
            cache_root = _l3_mcp_cache_root().resolve()
            try:
                subdir.resolve().relative_to(cache_root)
                reg_source = "l3_mcp_cache"
            except ValueError:
                reg_source = "skills_repo_dev"
            if await mgr.add_server(cfg):
                ok += 1
                logger.info(
                    "[L3PackagedStdio] 已注册 server_id=%s plugin=%s source=%s package_root=%s",
                    server_id,
                    plugin_id,
                    reg_source,
                    str(subdir.resolve()),
                )
        except Exception as e:
            logger.warning("[L3PackagedStdio] add_server 失败 server_id=%s err=%s", server_id, e)
    return ok
