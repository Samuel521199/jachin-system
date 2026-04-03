#!/usr/bin/env python3
"""
官方 MCP（modelcontextprotocol/servers）stdio 集成测试。

前置：
  pip install -r tools/mcp-official/requirements-official-mcp.txt
  （仓库根目录已含 mcp SDK：core/requirements.txt 中的 mcp>=1.0.0）

用法（在仓库根目录）：
  python scripts/test_official_mcp_stdio.py
  python scripts/test_official_mcp_stdio.py --only fetch
  python scripts/test_official_mcp_stdio.py --only time
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.types import TextContent
except ImportError:
    print("缺少 mcp SDK，请安装: pip install mcp>=1.0.0")
    sys.exit(1)


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _text_from_result(result) -> str:
    parts: list[str] = []
    for block in result.content or []:
        if isinstance(block, TextContent):
            parts.append(block.text or "")
        elif hasattr(block, "text"):
            parts.append(str(getattr(block, "text", "") or ""))
    return "\n".join(parts).strip()


async def _run_server(
    module: str,
    *,
    tool_name: str,
    arguments: dict,
) -> tuple[bool, str]:
    """启动 `python -m <module>`，调用单个工具，返回 (ok, message)。"""
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", module],
        env=env,
    )
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                names = {t.name for t in listed.tools}
                if tool_name not in names:
                    return False, f"未找到工具 {tool_name!r}，当前: {sorted(names)}"
                out = await session.call_tool(tool_name, arguments)
                text = _text_from_result(out)
                return True, text
    except Exception as e:
        return False, str(e)


async def test_fetch() -> int:
    print("\n=== 官方 mcp-server-fetch（python -m mcp_server_fetch）===")
    if not _module_available("mcp_server_fetch"):
        print("[SKIP] 未安装 mcp-server-fetch，请: pip install mcp-server-fetch")
        return 0
    ok, msg = await _run_server(
        "mcp_server_fetch",
        tool_name="fetch",
        arguments={
            "url": "https://example.com/",
            "max_length": 4000,
            "start_index": 0,
            "raw": False,
        },
    )
    if not ok:
        print(f"[FAIL] {msg}")
        return 1
    print(f"[OK] fetch 已返回（长度 {len(msg)} 字符）")
    preview = msg[:400].replace("\n", " ")
    print(f"     预览: {preview}{'…' if len(msg) > 400 else ''}")
    if "example" not in msg.lower() and "failed" in msg.lower():
        print("     （提示：若网络或 robots.txt 受限，官方实现可能返回错误说明文本，仍视为协议通路正常）")
    return 0


async def test_time() -> int:
    print("\n=== 官方 mcp-server-time（python -m mcp_server_time）===")
    if not _module_available("mcp_server_time"):
        print("[SKIP] 未安装 mcp-server-time，请: pip install mcp-server-time")
        return 0
    ok, msg = await _run_server(
        "mcp_server_time",
        tool_name="get_current_time",
        arguments={"timezone": "Asia/Shanghai"},
    )
    if not ok:
        print(f"[FAIL] {msg}")
        return 1
    print(f"[OK] get_current_time: {msg[:500]}")
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(description="官方 MCP stdio 测试")
    parser.add_argument(
        "--only",
        choices=("fetch", "time", "all"),
        default="all",
        help="只跑指定服务器测试",
    )
    args = parser.parse_args()
    print("官方 MCP stdio 测试（Anthropic modelcontextprotocol/servers PyPI 包）")
    print(f"Python: {sys.executable}")

    rc = 0
    if args.only in ("fetch", "all"):
        rc |= await test_fetch()
    if args.only in ("time", "all"):
        rc |= await test_time()
    if args.only == "all" and rc == 0:
        print("\n全部执行完毕（无失败返回码）。")
    return rc


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
