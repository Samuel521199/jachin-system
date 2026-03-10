#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键验证招聘全链路脚本是否可用。

用法：
  cd skills_repo/plugin
  python scripts/verify_all_recruitment_scripts.py           # 完整验证（需 Chrome + Boss 登录）
  python scripts/verify_all_recruitment_scripts.py --dry     # 仅验证不依赖 Chrome 的部分
  python scripts/verify_all_recruitment_scripts.py --l3      # 通过 L3 MCP 调用验证
"""
from __future__ import annotations

import argparse
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = ROOT / "2-track-a-atomic-mcp"
PROJ_ROOT = ROOT.resolve().parent.parent
CONFIG = ROOT / "data" / "jd_to_publish.json"


def _add_plugin_path():
    if str(PLUGIN_ROOT) not in sys.path:
        sys.path.insert(0, str(PLUGIN_ROOT))


def check_jd_config() -> tuple[bool, str]:
    """检查 jd_to_publish.json 是否存在且有效"""
    if not CONFIG.exists():
        return False, f"配置文件不存在: {CONFIG}\n请复制 jd_to_publish.example.json 并编辑"
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
        if not data.get("job_title") and not data.get("jd_full"):
            return False, "jd_to_publish.json 中 job_title 或 jd_full 为空"
        return True, f"已加载: {data.get('job_title', '')}"
    except Exception as e:
        return False, f"配置文件解析失败: {e}"


def check_chrome_cdp() -> tuple[bool, str]:
    """检查 Chrome 调试端口 9222 是否可达"""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=3000)
            contexts = browser.contexts
            browser.close()
        if not contexts:
            return False, "Chrome 已连接但无上下文，请确保已打开 Boss 直聘页面"
        return True, "Chrome 调试模式已就绪 (9222)"
    except Exception as e:
        err = str(e)
        if "connect" in err.lower() or "timeout" in err.lower():
            return False, (
                "Chrome 未以调试模式启动\n"
                "请运行: scripts\\launch_chrome_debug.ps1\n"
                "然后在 Chrome 中登录 Boss 直聘"
            )
        return False, f"Chrome 检查失败: {err}"


def test_atom_post_job_boss() -> tuple[bool, str]:
    """直接调用 atom_post_job_boss（需 Chrome + Boss 登录）"""
    _add_plugin_path()
    try:
        from tools.atom_post_job_boss import atom_post_job_boss
        result = atom_post_job_boss(
            cdp_url="http://127.0.0.1:9222",
            jd_config_path=str(CONFIG),
        )
        ok = result.get("success") and result.get("posted")
        msg = f"success={result.get('success')}, posted={result.get('posted')}"
        if result.get("error"):
            msg += f", error={result['error']}"
        return ok, msg
    except Exception as e:
        return False, str(e)


def test_add_automated_recruitment_task() -> tuple[bool, str]:
    """通过 L3 调度器添加岗位（不依赖 Chrome）"""
    sys.path.insert(0, str(PROJ_ROOT))
    try:
        from l3_node.recruitment_scheduler import add_scheduled_job, remove_scheduled_job
        job_name = "验证测试岗位_verify"
        remove_scheduled_job(job_name)
        result = add_scheduled_job({
            "job_name": job_name,
            "jd_config_path": str(CONFIG),
            "cdp_url": "http://127.0.0.1:9222",
            "max_count": 5,
            "filter_tab": "全部",
            "request_resume": True,
            "analyze_threshold": 2,
            "analyze_interval_hours": 0.05,
        })
        remove_scheduled_job(job_name)
        ok = result.get("ok") is True
        msg = json.dumps(result, ensure_ascii=False)
        return ok, msg
    except Exception as e:
        return False, str(e)


def test_l3_mcp_invoke() -> tuple[bool, str]:
    """通过 L3 MCP Registry 调用 atom_post_job_boss（需 Chrome）"""
    sys.path.insert(0, str(PROJ_ROOT))
    try:
        from l3_node.skills.mcp_registry import get_mcp_registry
        import asyncio

        async def _run():
            reg = get_mcp_registry()
            jd = json.loads(CONFIG.read_text(encoding="utf-8"))
            inp = json.dumps({"jd_config": jd, "cdp_url": "http://127.0.0.1:9222"}, ensure_ascii=False)
            return await reg.invoke("mcp:atom_post_job_boss", inp, timeout=60.0)

        result_str = asyncio.run(_run())
        data = json.loads(result_str) if result_str.strip().startswith("{") else {}
        ok = data.get("success") and data.get("posted")
        return ok, result_str[:200] + ("..." if len(result_str) > 200 else "")
    except Exception as e:
        return False, str(e)


def main():
    p = argparse.ArgumentParser(description="验证招聘全链路脚本")
    p.add_argument("--dry", action="store_true", help="仅验证不依赖 Chrome 的部分（调度器）")
    p.add_argument("--l3", action="store_true", help="通过 L3 MCP 调用验证 atom_post_job_boss")
    args = p.parse_args()

    print("=" * 60)
    print("招聘全链路脚本验证")
    print("=" * 60)

    all_ok = True

    # 1. JD 配置
    ok, msg = check_jd_config()
    print(f"\n[1] JD 配置 (jd_to_publish.json): {'OK' if ok else 'FAIL'}")
    print(f"    {msg}")
    if not ok:
        all_ok = False

    # 2. Chrome（非 dry 时检查）
    if not args.dry:
        ok, msg = check_chrome_cdp()
        print(f"\n[2] Chrome 调试模式 (9222): {'OK' if ok else 'FAIL'}")
        for line in msg.split("\n"):
            print(f"    {line}")
        if not ok:
            all_ok = False
            print("\n    提示: 使用 --dry 可跳过 Chrome 相关验证")

    # 3. 调度器 add_scheduled_job（不依赖 Chrome）
    ok, msg = test_add_automated_recruitment_task()
    print(f"\n[3] 调度器 add_automated_recruitment_task: {'OK' if ok else 'FAIL'}")
    print(f"    {msg[:150]}{'...' if len(msg) > 150 else ''}")
    if not ok:
        all_ok = False

    # 4. atom_post_job_boss 直接调用（需 Chrome）
    if not args.dry:
        ok, msg = test_atom_post_job_boss()
        print(f"\n[4] atom_post_job_boss 直接调用: {'OK' if ok else 'FAIL'}")
        print(f"    {msg}")
        if not ok:
            all_ok = False

    # 5. L3 MCP 调用（可选）
    if args.l3 and not args.dry:
        ok, msg = test_l3_mcp_invoke()
        print(f"\n[5] L3 MCP atom_post_job_boss: {'OK' if ok else 'FAIL'}")
        print(f"    {msg}")
        if not ok:
            all_ok = False

    print("\n" + "=" * 60)
    if all_ok:
        print("全部通过")
        sys.exit(0)
    print("存在失败项，请按提示修复")
    sys.exit(1)


if __name__ == "__main__":
    main()
