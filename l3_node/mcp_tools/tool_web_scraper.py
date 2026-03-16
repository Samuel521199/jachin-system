"""
通用网页抓取器 — mcp:atom_web_scraper

契约: docs/bi_daily_report/01_PARALLEL_DEVELOPMENT_GUIDE.md
开发者 A 负责实现，本文件为占位 stub。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def harvest_table_data(
    url: str,
    output_path: str | Path | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    抓取网页/后台表格数据，保存为 CSV 或 JSON。

    Args:
        url: 目标 URL
        output_path: 输出路径，为空时使用 bi_paths.get_bi_raw_dir() 下 YYYYMMDD.csv
        config: 可选 {extract_rules, output_format, headers, timeout}

    Returns:
        {"status": "success", "file_path": "..."} 或 {"status": "error", "error": "..."}
    """
    # TODO: 开发者 A 实现 — 使用 requests/BeautifulSoup 或 Playwright
    return {
        "status": "error",
        "error": "[STUB] tool_web_scraper 占位实现，待开发者 A 完成",
    }


if __name__ == "__main__":
    # 开发者 A 本地测试入口
    r = harvest_table_data("https://example.com", config={"output_format": "csv"})
    print(r)
