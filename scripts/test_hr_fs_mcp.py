#!/usr/bin/env python3
"""
测试 hr-filesystem MCP 功能

前置：L2 已启动 (python -m core.main)
"""
import sys
from pathlib import Path

# Windows 下强制 stdout/stderr 使用 UTF-8，避免中文乱码
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import httpx
except ImportError:
    print("请安装 httpx: pip install httpx")
    sys.exit(1)

BASE = "http://localhost:18888"


def main() -> int:
    print("=" * 60)
    print("hr-filesystem MCP 功能测试")
    print("=" * 60)

    with httpx.Client(timeout=30.0) as client:
        # 1. 获取工具列表
        print("\n[1] GET /api/v2/mcp/tools ...")
        try:
            r = client.get(f"{BASE}/api/v2/mcp/tools")
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"   [FAIL] L2 未启动或不可达: {e}")
            print("   请先运行: python -m core.main")
            return 1

        tools = data.get("tools") or []
        count = data.get("count", 0)
        print(f"   [OK] 工具数: {count}")
        for t in tools[:8]:
            print(f"        - {t.get('name', '?')}: {t.get('description', '')[:50]}...")

        if not tools:
            print("   [FAIL] 无 MCP 工具，请检查 local-hr-fs 是否已挂载")
            return 1

        # 2. list_directory - 列出 data/hr_resumes（需传绝对路径，MCP 校验 allowed dir）
        hr_dir = str((ROOT / "data" / "hr_resumes").resolve())
        print(f"\n[2] POST /api/v2/mcp/invoke list_directory path={hr_dir} ...")
        try:
            r = client.post(
                f"{BASE}/api/v2/mcp/invoke",
                json={"tool_name": "list_directory", "arguments": {"path": hr_dir}},
            )
            r.raise_for_status()
            out = r.json()
            result = out.get("result", "")
            print(f"   [OK] 返回:\n{result[:500]}")
        except Exception as e:
            print(f"   [FAIL] {e}")
            if hasattr(e, "response") and e.response is not None:
                print(f"   响应: {e.response.text[:300]}")
            return 1

        # 3. read_file - 读取张三简历（绝对路径）
        resume_path = str((ROOT / "data" / "hr_resumes" / "zhangsan_resume.md").resolve())
        print(f"\n[3] POST /api/v2/mcp/invoke read_file ...")
        try:
            r = client.post(
                f"{BASE}/api/v2/mcp/invoke",
                json={
                    "tool_name": "read_file",
                    "arguments": {"path": resume_path},
                },
            )
            r.raise_for_status()
            out = r.json()
            result = out.get("result", "")
            print(f"   [OK] 简历内容预览:\n{result[:800]}...")
            if "张三" in result and "后端架构师" in result:
                print("\n   [PASS] 绝密简历读取成功！")
            else:
                print("\n   [WARN] 内容可能不完整")
        except Exception as e:
            print(f"   [FAIL] {e}")
            if hasattr(e, "response") and e.response is not None:
                print(f"   响应: {e.response.text[:300]}")
            return 1

    print("\n" + "=" * 60)
    print("全部测试通过")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
