#!/usr/bin/env python3
"""
jachin-system-pilot 本地测试脚本

验证：
1. wasm_runner 直接执行 execute ABI
2. L3 loader run_tool 路径
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 确保项目根在 path 中
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_wasm_runner_direct() -> bool:
    """直接通过 wasm_runner 执行 wasm"""
    wasm_path = ROOT / "l3_node" / "primitives" / "tools" / "wasm_bundled" / "main.wasm"
    if not wasm_path.exists():
        print(f"[FAIL] wasm 不存在: {wasm_path}")
        return False
    try:
        from core.wasm_runner import run_wasm_plugin
        result = run_wasm_plugin(
            str(wasm_path),
            function_name="run",
            fuel_limit=200_000,
            stdin_json={"input": "test"},
        )
        if result is None:
            print("[FAIL] wasm_runner 返回 None")
            return False
        result_str = result if isinstance(result, str) else str(result)
        if "status" not in result_str or "ok" not in result_str:
            print(f"[FAIL] 输出不符合预期: {result_str[:200]}")
            return False
        data = json.loads(result_str)
        if data.get("status") != "ok" or "系统状态" not in str(data.get("message", "")):
            print(f"[FAIL] JSON 内容不符合预期: {data}")
            return False
        print(f"[PASS] wasm_runner 直接执行: {result_str[:80]}...")
        return True
    except Exception as e:
        print(f"[FAIL] wasm_runner 异常: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_l3_loader_run_tool() -> bool:
    """通过 L3 loader run_tool 执行"""
    try:
        from l3_node.primitives import run_tool, load_skills_for_ui
        tools = load_skills_for_ui(allowed_skills=None)
        pilot = [t for t in tools if "pilot" in (t.get("id") or "").lower()]
        if not pilot:
            print(f"[FAIL] 未找到 jachin-system-pilot，当前技能: {[t['id'] for t in tools]}")
            return False
        tool_id = pilot[0]["id"]
        result = run_tool(tool_id, '{"input":"test"}', allowed_skills=None)
        if not result or result.startswith("[") and ("失败" in result or "未知" in result or "拒绝" in result):
            print(f"[FAIL] run_tool 返回: {result}")
            return False
        if "status" not in result or "ok" not in result:
            print(f"[FAIL] 输出不符合预期: {result[:200]}")
            return False
        data = json.loads(result)
        if data.get("status") != "ok":
            print(f"[FAIL] JSON 不符合预期: {data}")
            return False
        print(f"[PASS] L3 run_tool: {result[:80]}...")
        return True
    except Exception as e:
        print(f"[FAIL] L3 run_tool 异常: {e}")
        import traceback
        traceback.print_exc()
        return False


def main() -> int:
    print("=" * 60)
    print("jachin-system-pilot 本地测试")
    print("=" * 60)
    ok1 = test_wasm_runner_direct()
    ok2 = test_l3_loader_run_tool()
    print("=" * 60)
    if ok1 and ok2:
        print("[OK] 全部测试通过")
        return 0
    print("[FAIL] 部分测试失败")
    return 1


if __name__ == "__main__":
    sys.exit(main())
