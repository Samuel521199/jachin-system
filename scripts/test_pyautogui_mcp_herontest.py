#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 PyAutoGUI MCP（mcp-pyautogui-server）与真实浏览器 + 目标站点配合是否正常。

默认目标：https://www.kalaroko.com/

与 Puppeteer MCP 的区别：
  - PyAutoGUI 走 **操作系统物理层**（截屏、绝对坐标移动/点击），不读 DOM；
  - 适合 Canvas / 无可选中 DOM 的控件；需本机 **真实可见的浏览器窗口**（非无头）。

**如何测试 MCP（三种方式）**

1) **本脚本（推荐，等价 L3 起的 stdio 子进程）**
   前置：``pip install mcp>=1.0.0``（与仓库一致），且下列 **二选一**
   - ``pip install mcp-pyautogui-server``（脚本优先用 ``python -m mcp_pyautogui_server``）
   - 或安装 ``uv`` 后使用 ``uvx mcp-pyautogui-server``（脚本在未安装包时回退）
   Linux 桌面无 DISPLAY 时请在环境中设置 ``DISPLAY``（如 ``:0``）。

   用法（仓库根目录）::
     python scripts/test_pyautogui_mcp_herontest.py
     python scripts/test_pyautogui_mcp_herontest.py --target-url https://www.kalaroko.com/
     python scripts/test_pyautogui_mcp_herontest.py --no-open-browser
     python scripts/test_pyautogui_mcp_herontest.py --wait-seconds 12

2) **Jachin L3 已挂载该 MCP 时**
   合并 ``config/mcp_servers.json.example`` 中 ``mcp-pyautogui-server`` 到 ``~/.jachin/mcp_servers.json``，
   重启 L3，在对话里让助手调用 ``get_screen_size``、``screenshot``、``move_mouse``、``click_mouse`` 等（工具名以 ``tools/list`` 为准）。

3) **PyAutoGUI 安全角**
   鼠标移到屏幕左上角会触发 **failsafe**，工具可能返回错误；测试时不要刻意移角。

截图输出目录：``~/.jachin/jachin_debug/``（与桌面热键诊断日志同父目录）。
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import shutil
import sys
import time
import webbrowser
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
    print("缺少 mcp SDK，请安装: pip install mcp>=1.0.0", file=sys.stderr)
    sys.exit(1)

try:
    from mcp.types import ImageContent
except ImportError:
    ImageContent = None  # type: ignore[misc, assignment]


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


def _save_image_from_result(result, out_path: Path) -> bool:
    for block in result.content or []:
        if ImageContent is not None and isinstance(block, ImageContent):
            out_path.write_bytes(block.data)
            return True
        data = getattr(block, "data", None)
        if isinstance(data, (bytes, bytearray)) and data:
            mime = (getattr(block, "mimeType", None) or getattr(block, "mime_type", None) or "").lower()
            if "image" in mime or mime == "":
                out_path.write_bytes(bytes(data))
                return True
    return False


def _stdio_params_pyautogui() -> StdioServerParameters:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if _module_available("mcp_pyautogui_server"):
        return StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_pyautogui_server"],
            env=env,
        )
    uvx = shutil.which("uvx") or shutil.which("uvx.exe")
    if uvx:
        return StdioServerParameters(
            command=uvx,
            args=["mcp-pyautogui-server"],
            env=env,
        )
    raise RuntimeError(
        "未找到 mcp-pyautogui-server：请执行 pip install mcp-pyautogui-server "
        "或将 uv/uvx 加入 PATH 后使用 uvx mcp-pyautogui-server"
    )


def _out_screenshot_path() -> Path:
    base = Path.home() / ".jachin" / "jachin_debug"
    base.mkdir(parents=True, exist_ok=True)
    return base / "herontest_pyautogui_mcp_screenshot.jpg"


async def _run_stdio(
    *,
    target_url: str,
    open_browser: bool,
    wait_seconds: float,
) -> int:
    try:
        params = _stdio_params_pyautogui()
    except RuntimeError as e:
        print(f"[pyautogui-mcp-test] {e}", file=sys.stderr)
        return 2

    print(
        f"[pyautogui-mcp-test] 启动 MCP stdio: {params.command} {' '.join(params.args)}",
        flush=True,
    )

    if open_browser:
        print(f"[pyautogui-mcp-test] 打开系统默认浏览器: {target_url}", flush=True)
        webbrowser.open(target_url)
        print(
            f"[pyautogui-mcp-test] 等待 {wait_seconds}s — 请将浏览器窗口置于前台以便截到页面",
            flush=True,
        )
        time.sleep(wait_seconds)
    else:
        print(
            "[pyautogui-mcp-test] 已跳过打开浏览器；请确保目标页已在可见窗口中。",
            flush=True,
        )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            names = [t.name for t in listed.tools]
            print("[pyautogui-mcp-test] 可用工具:", names, flush=True)

            for tool, args in (
                ("get_screen_size", {}),
                ("get_mouse_position", {}),
            ):
                if tool not in names:
                    print(f"[pyautogui-mcp-test] 跳过缺失工具: {tool}", flush=True)
                    continue
                out = await session.call_tool(tool, args)
                print(f"[pyautogui-mcp-test] {tool}:\n{_text_from_result(out)}", flush=True)

            if "screenshot" not in names:
                print("[pyautogui-mcp-test] 未找到 screenshot 工具。", file=sys.stderr)
                return 2

            out = await session.call_tool("screenshot", {})
            out_path = _out_screenshot_path()
            if _save_image_from_result(out, out_path):
                print(f"[pyautogui-mcp-test] 截图已保存: {out_path}", flush=True)
            else:
                txt = _text_from_result(out)
                print(
                    f"[pyautogui-mcp-test] screenshot 未解析为图片，文本/其它返回:\n{txt[:4000]}",
                    flush=True,
                )

    print("[pyautogui-mcp-test] stdio 测试结束。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="mcp-pyautogui-server 与 www.kalaroko.com 联调测试")
    ap.add_argument(
        "--target-url",
        default="https://www.kalaroko.com/",
        help="用默认浏览器打开的 URL（仅当未使用 --no-open-browser）",
    )
    ap.add_argument(
        "--no-open-browser",
        action="store_true",
        help="不自动打开浏览器（你已手动打开目标页时使用）",
    )
    ap.add_argument(
        "--wait-seconds",
        type=float,
        default=8.0,
        help="打开浏览器后等待秒数，便于把窗口点到前台",
    )
    args = ap.parse_args()
    return asyncio.run(
        _run_stdio(
            target_url=args.target_url,
            open_browser=not args.no_open_browser,
            wait_seconds=args.wait_seconds,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
