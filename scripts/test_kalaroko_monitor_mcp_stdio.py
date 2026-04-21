#!/usr/bin/env python3
"""
Kalaroko Monitor MCP — stdio 存活与 fetch_api_health 快速验证。

前置（仓库根）：
  pip install -r requirements_kalaroko.txt
  playwright install chromium

用法：
  python scripts/test_kalaroko_monitor_mcp_stdio.py
  python scripts/test_kalaroko_monitor_mcp_stdio.py --url https://gwp.heronpro.xin/some/health/path

说明：
  - 子进程需能 import ``l3_client``，故设置 PYTHONPATH=仓库根（本脚本自动注入）。
  - 目标 URL 主机须在 KALAROKO_MONITOR_ALLOWED_HOSTS 内（本脚本默认追加 gwp.heronpro.xin）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
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
    print("缺少 mcp SDK，请: pip install mcp>=1.0.0", file=sys.stderr)
    sys.exit(1)

MODULE = "l3_client.local_mcps.kalaroko_monitor.mcp_kalaroko_monitor"


def _text_from_result(result) -> str:
    parts: list[str] = []
    for block in result.content or []:
        if isinstance(block, TextContent):
            parts.append(block.text or "")
        elif hasattr(block, "text"):
            parts.append(str(getattr(block, "text", "") or ""))
    return "\n".join(parts).strip()


async def _main(url: str) -> int:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["KALAROKO_MONITOR_ALLOWED_HOSTS"] = (
        "kalaroko.com,www.kalaroko.com,gwp.heronpro.xin"
    )

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", MODULE],
        env=env,
        cwd=str(ROOT),
    )

    arguments = {
        "endpoints": [
            {
                "id": "sanity_health",
                "url": url,
                "method": "GET",
                "expected_status": 200,
                "timeout_ms": 20000,
            }
        ],
        "run_id": "sanity-check",
        "parallel": True,
    }

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = {t.name for t in listed.tools}
            tool_name = "fetch_api_health"
            if tool_name not in names:
                print(f"[FAIL] 未找到工具 {tool_name!r}，当前: {sorted(names)}", file=sys.stderr)
                return 2
            out = await session.call_tool(tool_name, arguments)
            text = _text_from_result(out)
            print(text)
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                print("[WARN] 返回非 JSON，但 stdio 会话已成功", file=sys.stderr)
                return 0
            if isinstance(data, dict) and data.get("ok") is False:
                print(f"[FAIL] 工具返回错误: {data.get('error_code')} {data.get('message')}", file=sys.stderr)
                return 1
            return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Kalaroko Monitor MCP stdio 快速检测")
    ap.add_argument(
        "--url",
        default="https://gwp.heronpro.xin/some/health/path",
        help="健康检查 URL（主机须在 KALAROKO_MONITOR_ALLOWED_HOSTS）",
    )
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_main(args.url)))
