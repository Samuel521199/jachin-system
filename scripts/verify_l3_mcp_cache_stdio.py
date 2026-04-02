#!/usr/bin/env python3
"""
仅从 ~/.jachin/l3_mcp_cache/<item_id>/plugin.json 拉起 stdio MCP，验证「拉取包」可用性。
不读取 mcp_servers.json、不扫描 inventory、不合并 skills_repo/plugin —— 与线上「只带缓存目录」一致。

用法（Windows 示例）:
  python scripts/verify_l3_mcp_cache_stdio.py
  python scripts/verify_l3_mcp_cache_stdio.py --cache-dir "C:\\Users\\YOU\\.jachin\\l3_mcp_cache\\<uuid>"

日志关键字: [MCP_VERIFY] —— 含 PACKAGE_ROOT、PLUGIN_ID、SERVER_ID、COMMAND。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(message)s",
)
log = logging.getLogger("mcp_verify")


def _default_cache_dir() -> Path:
    return (
        Path.home()
        / ".jachin"
        / "l3_mcp_cache"
        / "dddcbaab-e230-4603-b245-9023420b2dd6"
    )


def _under_l3_mcp_cache(package_root: Path) -> bool:
    cache = (Path.home() / ".jachin" / "l3_mcp_cache").resolve()
    try:
        package_root.resolve().relative_to(cache)
        return True
    except ValueError:
        return False


async def main() -> int:
    ap = argparse.ArgumentParser(description="验证 l3_mcp_cache 内 stdio MCP 可连接、可列工具")
    ap.add_argument(
        "--cache-dir",
        type=Path,
        default=_default_cache_dir(),
        help="拉取包目录（须含 plugin.json，且位于 ~/.jachin/l3_mcp_cache 下）",
    )
    ap.add_argument(
        "--skip-cache-root-check",
        action="store_true",
        help="不校验目录是否在 l3_mcp_cache 下（仅调试）",
    )
    args = ap.parse_args()
    root = args.cache_dir.resolve()
    pj = root / "plugin.json"
    if not pj.is_file():
        log.error("[MCP_VERIFY] 缺少 plugin.json path=%s", pj)
        return 2
    if not args.skip_cache_root_check and not _under_l3_mcp_cache(root):
        log.error(
            "[MCP_VERIFY] 拒绝：PACKAGE_ROOT 不在 ~/.jachin/l3_mcp_cache 下，无法保证是「拉取包」而非本机其它路径: %s",
            root,
        )
        return 3

    try:
        plugin = json.loads(pj.read_text(encoding="utf-8"))
    except Exception as e:
        log.error("[MCP_VERIFY] plugin.json 解析失败: %s", e)
        return 4

    from l3_node.l3_packaged_stdio_mcp import (
        _resolve_stdio_args,
        _resolve_stdio_env,
        _stdio_block_from_plugin,
    )
    from core.inventory_scanner import _prune_mcp_filesystem_roots
    from core.mcp_client import MCPServerInstance

    it = (plugin.get("item_type") or plugin.get("type") or "").lower()
    if it != "mcp":
        log.error("[MCP_VERIFY] 非 MCP 条目 item_type/type=%s", it)
        return 5
    if str(plugin.get("runtime_tier", "L3_LOCAL")).upper() != "L3_LOCAL":
        log.error("[MCP_VERIFY] runtime_tier 非 L3_LOCAL")
        return 6
    blk = _stdio_block_from_plugin(plugin)
    if not blk or not (blk.get("command") or "").strip():
        log.error("[MCP_VERIFY] 缺少 stdio_server.command")
        return 7

    plugin_id = (plugin.get("id") or root.name or "unknown").strip()
    server_id = (blk.get("id") or blk.get("name") or plugin_id).strip() or plugin_id
    command = blk.get("command", "").strip()
    args_raw = blk.get("args") or []
    if not isinstance(args_raw, list):
        args_raw = []
    resolved = _resolve_stdio_args(args_raw)
    pruned = _prune_mcp_filesystem_roots(list(resolved))
    if pruned is None:
        log.error("[MCP_VERIFY] server-filesystem 无有效根目录，已跳过（与 L3 注册逻辑一致）")
        return 8
    env_merged = _resolve_stdio_env(blk.get("env") if isinstance(blk.get("env"), dict) else None)

    log.info("[MCP_VERIFY] ========== 仅使用拉取缓存目录（非 mcp_servers.json / 非 inventory 扫描） ==========")
    log.info("[MCP_VERIFY] PACKAGE_ROOT=%s", root)
    log.info("[MCP_VERIFY] PLUGIN_JSON=%s", pj)
    log.info("[MCP_VERIFY] CACHE_ITEM_DIR=%s", root.name)
    log.info("[MCP_VERIFY] PLUGIN_ID=%s SERVER_ID(from stdio_server.id)=%s", plugin_id, server_id)
    log.info("[MCP_VERIFY] COMMAND=%s ARGS=%s", command, pruned)
    if env_merged:
        # 不打印完整环境，避免泄露密钥；仅说明合并了 env
        extra_keys = sorted(set(env_merged.keys()) - set(__import__("os").environ.keys()))
        log.info("[MCP_VERIFY] ENV 额外键(相对进程环境): %s", extra_keys)

    inst = None
    try:
        inst = MCPServerInstance(
            server_id=server_id,
            command=command,
            args=[str(x) for x in pruned],
            env=env_merged,
        )
        await inst.connect()
        tools = await inst.list_tools()
        names = [t.get("name", "") for t in tools]
        log.info("[MCP_VERIFY] list_tools OK count=%d names=%s", len(names), names)
        # 官方 mcp-server-fetch 常见工具名 fetch
        probe = None
        for cand in ("fetch", "mcp_fetch", "http_fetch"):
            if cand in names:
                probe = cand
                break
        if probe:
            log.info("[MCP_VERIFY] 试调工具 name=%s（最小请求）", probe)
            out = await inst.call_tool(
                probe,
                {"url": "https://httpbin.org/get", "max_length": 500},
            )
            log.info("[MCP_VERIFY] call_tool 返回摘要 len=%d preview=%s", len(out), out[:400].replace("\n", " "))
        else:
            log.warning(
                "[MCP_VERIFY] 未找到常见 fetch 工具名，仅完成连接与 list_tools；请人工核对 tools 列表",
            )
        log.info("[MCP_VERIFY] ========== 验证结束：使用的 MCP 来自上述 PACKAGE_ROOT ==========")
        return 0
    except Exception as e:
        log.exception("[MCP_VERIFY] 失败: %s", e)
        return 9
    finally:
        if inst is not None:
            await inst.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
