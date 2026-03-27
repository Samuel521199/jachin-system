#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 MCP 工具 atom_greet_recommend_boss（推荐牛人自动打招呼）

前置条件：
  1. 用 scripts\\launch_chrome_debug.ps1 启动 Chrome
  2. 在 Chrome 中登录 Boss 直聘
  3. 编辑 data/jd_to_publish.json 填写 JD（用于筛选条件）
  4. 运行本脚本（建议先在浏览器打开「推荐牛人」页面）

用法：
  python scripts/test_mcp_greet_recommend_boss.py
  python scripts/test_mcp_greet_recommend_boss.py --config "d:/path/to/jd_to_publish.json"
  python scripts/test_mcp_greet_recommend_boss.py --direct   # 直接调用工具，不经过 MCP
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = ROOT.parent
sys.path.insert(0, str(PLUGIN_ROOT))


def _run_direct(config_path: str) -> dict:
    """直接调用工具（不经过 MCP）"""
    sys.path.insert(0, str(ROOT / "com.jachin.hr.recruitment"))
    from tools.atom_greet_recommend_boss import atom_greet_recommend_boss, load_jd_config

    jd = load_jd_config(config_path)
    if not jd:
        print("警告: 未加载到 JD 配置，将使用默认筛选条件")
    return atom_greet_recommend_boss(cdp_url="http://127.0.0.1:9222", jd_config_path=config_path)


async def _run_via_mcp(config_path: str) -> dict:
    """通过 MCP 协议调用工具"""
    sys.path.insert(0, str(PLUGIN_ROOT.parent))  # 项目根，用于 import core.mcp_client
    from core.mcp_client import MCPServerInstance

    server_script = ROOT / "com.jachin.hr.recruitment" / "server.py"
    if not server_script.exists():
        return {"success": False, "greeted_count": 0, "error": f"MCP server 不存在: {server_script}"}

    instance = MCPServerInstance(
        server_id="hr-atomic-tools",
        command="python",
        args=[str(server_script)],
    )
    try:
        await instance.connect()
        tools = await instance.list_tools()
        if "atom_greet_recommend_boss" not in [t.get("name") for t in tools]:
            return {"success": False, "greeted_count": 0, "error": "MCP 未暴露 atom_greet_recommend_boss"}

        args = {"cdp_url": "http://127.0.0.1:9222"}
        if config_path:
            args["jd_config_path"] = config_path
        result_str = await instance.call_tool("atom_greet_recommend_boss", args)
        try:
            return json.loads(result_str)
        except json.JSONDecodeError:
            return {"success": False, "greeted_count": 0, "error": result_str, "raw": result_str}
    finally:
        await instance.close()


def main() -> int:
    p = argparse.ArgumentParser(description="测试 MCP 工具 atom_greet_recommend_boss")
    p.add_argument("--config", default="", help="JD 配置文件路径，默认 data/jd_to_publish.json")
    p.add_argument("--direct", action="store_true", help="直接调用工具，不经过 MCP")
    args = p.parse_args()

    config_path = args.config or str(ROOT / "data" / "jd_to_publish.json")
    if config_path and Path(config_path).exists():
        print(f"配置: {config_path}")
    else:
        print("警告: 配置文件不存在，将使用默认筛选条件")

    print("=" * 60)
    print("测试 MCP 工具: atom_greet_recommend_boss（推荐牛人打招呼）")
    print("=" * 60)
    print(f"模式: {'直接调用' if args.direct else 'MCP 协议'}")
    print()

    if args.direct:
        result = _run_direct(config_path)
    else:
        result = asyncio.run(_run_via_mcp(config_path))

    print("结果:", json.dumps(result, ensure_ascii=False, indent=2))
    print()
    print("成功:", result.get("success"))
    print("打招呼人数:", result.get("greeted_count", 0))
    print("跳过(已沟通):", result.get("skipped_chat_history", 0))
    print("跳过(初筛不通过):", result.get("skipped_low_score", 0))
    if result.get("error"):
        print("错误:", result["error"])
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
