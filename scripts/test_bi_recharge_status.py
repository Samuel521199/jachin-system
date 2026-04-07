#!/usr/bin/env python3
"""
单页测试：平台充值情况 — 验证树形展开抓取（日期 -> 所有 -> (50,300] 等统计范围）

Prereq: 1) .\scripts\launch_chrome_debug_bi.ps1  2) 在 Chrome 中登录 BI 后台
Usage:
  python scripts/test_bi_recharge_status.py          # 通过菜单导航
  python scripts/test_bi_recharge_status.py --direct # 直接打开目标页（推荐）
Output: ~/.jachin/client_volumes/bi_data/raw/recharge_status.csv
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("test_recharge_status")

# 平台充值情况直接 URL（biRechargeTierDailySummary）
RECHARGE_STATUS_DIRECT_URL = "https://bi-admin-web.heronpro.xin/#/layout/BIManager/PlatformData/PlatformRecharge/biRechargeTierDailySummary"


def _check_chrome_cdp(cdp_url: str = "http://127.0.0.1:9222") -> bool:
    try:
        import urllib.request
        req = urllib.request.urlopen(f"{cdp_url.rstrip('/')}/json/version", timeout=3)
        return req.getcode() == 200
    except Exception:
        return False


def main() -> int:
    from l3_node.primitives.mcp.mcp_tools.bi.tool_web_scraper import harvest_table_data
    from l3_node.primitives.mcp.mcp_tools.bi.paths import get_bi_raw_dir, ensure_bi_dirs

    if not _check_chrome_cdp():
        print("Chrome 调试模式未启动，请先运行: .\\scripts\\launch_chrome_debug_bi.ps1")
        return 1

    ensure_bi_dirs()
    raw_dir = get_bi_raw_dir()
    out_path = raw_dir / "recharge_status.csv"

    use_direct = "--direct" in sys.argv

    if use_direct:
        from l3_node.primitives.mcp.mcp_tools.bi.spa_collector import get_automation_for_direct_url

        logger.info("使用 --direct 模式，直接打开: %s", RECHARGE_STATUS_DIRECT_URL)
        automation = get_automation_for_direct_url("recharge_status", RECHARGE_STATUS_DIRECT_URL)
        r = harvest_table_data(
            url=RECHARGE_STATUS_DIRECT_URL,
            output_path=str(out_path),
            config={
                "cdp_url": "http://127.0.0.1:9222",
                "output_format": "csv",
                "timeout": 60,
                "extract_rules": "table:not(.el-date-table), .el-table__body-wrapper table",
                "automation": automation,
            },
        )
        ok, fail = (1, 0) if r.get("status") == "success" else (0, 1)
        if r.get("status") == "success":
            logger.info("[1/1] recharge_status -> %s", r)
        else:
            logger.warning("[1/1] recharge_status -> %s", r)
    else:
        from l3_node.primitives.mcp.mcp_tools.bi.spa_collector import run_full_spa_collect

        ok, fail, _ = run_full_spa_collect(
            slugs=["recharge_status"],
            use_discover=False,
            auto_ingest=False,
            raw_dir=raw_dir,
            progress_cb=lambda i, t, s, r: logger.info("[%d/%d] %s -> %s", i, t, s, r),
        )

    if out_path.exists():
        lines = out_path.read_text(encoding="utf-8").strip().splitlines()
        print()
        print(f"抓取完成: {len(lines)-1} 行数据（含表头）")
        print(f"输出: {out_path}")
        print()
        print("前 25 行预览:")
        print("-" * 80)
        for line in lines[:25]:
            print(line[:200] + ("..." if len(line) > 200 else ""))
        print("-" * 80)
        content = out_path.read_text(encoding="utf-8")
        for kw in ["所有", "(50,300]", "日期", "统计范围"]:
            if kw in content:
                print(f"  [OK] 含: {kw}")
            else:
                print(f"  [--] 未抓到: {kw}")
    else:
        print(f"FAIL: 未生成 {out_path}")

    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
