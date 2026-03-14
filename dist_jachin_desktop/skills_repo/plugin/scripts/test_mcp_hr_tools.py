#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试 HR 原子 MCP 工具箱：验证 atom_post_job_boss、atom_greet_recommend_boss 等工具已正确暴露。

用法：
  python scripts/test_mcp_hr_tools.py                    # 仅列出工具，不执行
  python scripts/test_mcp_hr_tools.py --list             # 同上
  python scripts/test_mcp_hr_tools.py --test post_job     # 测试发布 JD（需 Chrome）
  python scripts/test_mcp_hr_tools.py --test greet        # 测试打招呼（需 Chrome）
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

ROOT = Path(__file__).resolve().parent.parent  # plugin/
PROJECT_ROOT = ROOT.parent.parent  # jachin-system-main
sys.path.insert(0, str(PROJECT_ROOT))


async def _connect_and_list_tools() -> list[dict]:
    """连接 MCP 并列出工具"""
    from core.mcp_client import MCPServerInstance

    server_script = ROOT / "2-track-a-atomic-mcp" / "server.py"
    if not server_script.exists():
        raise FileNotFoundError(f"MCP server 不存在: {server_script}")

    instance = MCPServerInstance(
        server_id="hr-atomic-tools",
        command="python",
        args=[str(server_script)],
    )
    await instance.connect()
    tools = await instance.list_tools()
    await instance.close()
    return tools


async def _call_tool(name: str, arguments: dict) -> str:
    """调用 MCP 工具"""
    from core.mcp_client import MCPServerInstance

    server_script = ROOT / "2-track-a-atomic-mcp" / "server.py"
    instance = MCPServerInstance(
        server_id="hr-atomic-tools",
        command="python",
        args=[str(server_script)],
    )
    await instance.connect()
    result = await instance.call_tool(name, arguments)
    await instance.close()
    return result


def main() -> int:
    p = argparse.ArgumentParser(description="测试 HR 原子 MCP 工具箱")
    p.add_argument("--list", action="store_true", help="仅列出 MCP 工具")
    p.add_argument("--test", choices=["post_job", "greet"], help="执行指定工具测试")
    p.add_argument("--config", default="", help="JD 配置文件路径")
    args = p.parse_args()

    config_path = args.config or str(ROOT / "data" / "jd_to_publish.json")

    print("=" * 60)
    print("HR 原子 MCP 工具箱测试")
    print("=" * 60)
    print(f"Server: {ROOT / '2-track-a-atomic-mcp' / 'server.py'}")
    print()

    try:
        tools = asyncio.run(_connect_and_list_tools())
    except Exception as e:
        print(f"错误: 无法连接 MCP Server - {e}")
        return 1

    print(f"已连接，共 {len(tools)} 个工具:")
    for t in tools:
        name = t.get("name", "")
        desc = (t.get("description", "") or "")[:60]
        print(f"  - {name}: {desc}…" if len(desc or "") >= 60 else f"  - {name}: {desc}")

    required = ["atom_post_job_boss", "atom_greet_recommend_boss"]
    missing = [r for r in required if r not in [t.get("name") for t in tools]]
    if missing:
        print(f"\n警告: 缺少工具 {missing}")
        return 1

    print("\n✓ 发布 JD 与打招呼工具已正确暴露")

    if args.test:
        print(f"\n执行测试: {args.test}")
        if args.test == "post_job":
            args_dict = {"cdp_url": "http://127.0.0.1:9222", "jd_config_path": config_path}
            result = asyncio.run(_call_tool("atom_post_job_boss", args_dict))
        else:  # greet
            args_dict = {"cdp_url": "http://127.0.0.1:9222", "jd_config_path": config_path}
            result = asyncio.run(_call_tool("atom_greet_recommend_boss", args_dict))
        print("返回:", result)
        try:
            r = json.loads(result)
            print("成功:", r.get("success"))
        except json.JSONDecodeError:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
