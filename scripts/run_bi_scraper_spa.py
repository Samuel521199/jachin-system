#!/usr/bin/env python3
"""
BI SPA 批量抓取 — CLI 入口

核心逻辑已迁移至 l3_node/mcp_tools/bi/spa_collector.py，main_skill 可复用。
设计: docs/bi_daily_report/09_SPA_SCRAPER_PLACEMENT_ANALYSIS.md

Prereq: 1) .\scripts\launch_chrome_debug_bi.ps1  2) Login in Chrome
Usage: python scripts/run_bi_scraper_spa.py [--no-discover]
  --no-discover: 使用硬编码 MENU_ITEMS，跳过菜单自动发现
Output: ~/.jachin/client_volumes/bi_data/raw/{slug}.csv
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))


def _setup_logging(raw_dir: Path) -> logging.Logger:
    log_dir = raw_dir.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"bi_scraper_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    log = logging.getLogger("bi_scraper")
    log.info("Log file: %s", log_file)
    return log


def _check_chrome_cdp(cdp_url: str = "http://127.0.0.1:9222") -> bool:
    """检测 Chrome 调试端口是否可达"""
    try:
        import urllib.request
        req = urllib.request.urlopen(f"{cdp_url.rstrip('/')}/json/version", timeout=3)
        return req.getcode() == 200
    except Exception:
        return False


def main() -> int:
    from l3_node.mcp_tools.bi.spa_collector import run_full_spa_collect
    from l3_node.mcp_tools.bi.paths import get_bi_raw_dir, ensure_bi_dirs

    use_discover = "--no-discover" not in sys.argv
    ensure_bi_dirs()
    raw_dir = get_bi_raw_dir()
    _setup_logging(raw_dir)

    # 提前检测 Chrome 调试模式，避免 34 次无意义重试
    if not _check_chrome_cdp():
        print()
        print("=" * 60)
        print("Chrome 调试模式未启动，无法连接 127.0.0.1:9222")
        print()
        print("请先执行以下步骤：")
        print("  1. 在项目根目录运行: .\\scripts\\launch_chrome_debug_bi.ps1")
        print("  2. 等待 Chrome 打开并自动跳转到 BI 后台")
        print("  3. 在 Chrome 中登录 BI 后台")
        print("  4. 再次运行: python scripts/run_bi_scraper_spa.py")
        print()
        print("=" * 60)
        return 1

    def on_progress(idx: int, total: int, slug: str, result: dict) -> None:
        status = result.get("status", "")
        if status == "success":
            rows = result.get("rows_count", 0)
            print(f"[{idx}/{total}] {slug} ... OK ({rows} rows)")
        else:
            print(f"[{idx}/{total}] {slug} ... FAIL: {result.get('error', result)}")

    ok, fail, failed_slugs = run_full_spa_collect(
        slugs=None,
        use_discover=use_discover,
        auto_ingest=False,
        raw_dir=raw_dir,
        progress_cb=on_progress,
    )

    print()
    print(f"Done: {ok} OK, {fail} FAIL")
    if failed_slugs:
        print("Failed slugs:", ", ".join(failed_slugs[:10]), "..." if len(failed_slugs) > 10 else "")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
