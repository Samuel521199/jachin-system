#!/usr/bin/env python3
"""
BI MCP 工具契约验收测试

验证 tool_web_scraper、tool_lark_notifier、tool_email_sender 的返回值符合《最高接口契约》。
开发者 A、B 完成实现后运行此脚本，确保通过后再提交 PR。

用法: python scripts/test_bi_mcp_contract.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 确保项目根在 path 中
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))


def test_atom_web_scraper_contract() -> tuple[bool, str]:
    """验证 atom_web_scraper 返回值包含 status、file_path/error"""
    from l3_node.mcp_tools.bi.tool_web_scraper import harvest_table_data
    from l3_node.mcp_tools.bi.paths import get_bi_raw_dir

    result = harvest_table_data("https://example.com", str(get_bi_raw_dir() / "test.csv"), {})
    if not isinstance(result, dict):
        return False, f"返回值应为 dict，实际: {type(result)}"
    if "status" not in result:
        return False, "返回值缺少 status 字段"
    if result.get("status") == "success":
        if "file_path" not in result:
            return False, "status=success 时必须有 file_path"
    else:
        if "error" not in result:
            return False, "status=error 时必须有 error"
    return True, "OK"


def test_atom_lark_notifier_contract() -> tuple[bool, str]:
    """验证 atom_lark_notifier 返回值包含 status、msg/error"""
    from l3_node.mcp_tools.bi.tool_lark_notifier import send_lark_markdown

    result = send_lark_markdown("https://open.feishu.cn/xxx", "# test", "title")
    if not isinstance(result, dict):
        return False, f"返回值应为 dict，实际: {type(result)}"
    if "status" not in result:
        return False, "返回值缺少 status 字段"
    if result.get("status") == "success":
        if "msg" not in result:
            return False, "status=success 时必须有 msg"
    else:
        if "error" not in result:
            return False, "status=error 时必须有 error"
    return True, "OK"


def test_atom_email_sender_contract() -> tuple[bool, str]:
    """验证 atom_email_sender 返回值包含 status、msg/error"""
    from l3_node.mcp_tools.bi.tool_email_sender import send_email_with_attachment

    result = send_email_with_attachment(
        {"host": "smtp.example.com", "user": "x", "password": "x"},
        ["a@b.com"],
        "test",
        "body",
        [],
    )
    if not isinstance(result, dict):
        return False, f"返回值应为 dict，实际: {type(result)}"
    if "status" not in result:
        return False, "返回值缺少 status 字段"
    if result.get("status") == "success":
        if "msg" not in result:
            return False, "status=success 时必须有 msg"
    else:
        if "error" not in result:
            return False, "status=error 时必须有 error"
    return True, "OK"


def main() -> int:
    print("=== BI MCP 契约验收测试 ===\n")
    ok_count = 0
    for name, fn in [
        ("atom_web_scraper", test_atom_web_scraper_contract),
        ("atom_lark_notifier", test_atom_lark_notifier_contract),
        ("atom_email_sender", test_atom_email_sender_contract),
    ]:
        ok, msg = fn()
        status = "PASS" if ok else "FAIL"
        print(f"  {name}: {status} - {msg}")
        if ok:
            ok_count += 1
    print()
    if ok_count == 3:
        print("全部通过")
        return 0
    print(f"失败 {3 - ok_count} 项，请检查契约: docs/bi_daily_report/01_PARALLEL_DEVELOPMENT_GUIDE.md")
    return 1


if __name__ == "__main__":
    sys.exit(main())
