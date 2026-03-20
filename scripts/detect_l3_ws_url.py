#!/usr/bin/env python3
"""
检测本机 L3 WebSocket 实际监听端口

用法: python scripts/detect_l3_ws_url.py

先启动 L3（python -m l3_node --ws-only 或 桌面端），再运行本脚本。
"""
from __future__ import annotations

import asyncio
import sys

PORTS = [18981, 18982, 18983, 18984, 18985]
PATH = "/sensory"


async def try_connect(port: int) -> bool:
    try:
        import websockets
    except ImportError:
        print("请安装: pip install websockets")
        sys.exit(1)

    url = f"ws://127.0.0.1:{port}{PATH}"
    try:
        async with websockets.connect(url, open_timeout=2, close_timeout=1) as ws:
            return True
    except Exception:
        return False


async def main() -> None:
    print("正在检测本机 L3 WebSocket 端口 (需先启动 L3)...\n")
    for port in PORTS:
        url = f"ws://127.0.0.1:{port}{PATH}"
        ok = await try_connect(port)
        status = "✓ 可用" if ok else "✗"
        print(f"  {url}  {status}")
        if ok:
            print(f"\n→ 请将 L3_WS_URL 设为: {url}")
            print("  写入 config/mcps/atom_lark_notifier/config.yaml 的 l3_ws_url")
            return
    print("\n未检测到 L3。请先启动 L3:")
    print("  python -m l3_node --ws-only")
    print("  或打开 Jachin 桌面端（由桌面端拉起 L3）")
    print("\n若 L3 已启动，可检查端口: netstat -ano | findstr 18981")


if __name__ == "__main__":
    asyncio.run(main())
