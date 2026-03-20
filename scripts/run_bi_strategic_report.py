#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BI 战略分析 — 单独测试入口

仅执行「战略深度分析」步骤，不跑抓取、提纯、Lark 同步。
用于验证 LLM 引擎、DuckDB 指标、CSV 摘要是否正常。

用法:
  python scripts/run_bi_strategic_report.py           # 默认昨日，输出到终端
  python scripts/run_bi_strategic_report.py --save    # 保存到文件
  python scripts/run_bi_strategic_report.py --date 2026-03-09  # 指定日期
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 项目根
root = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv
    load_dotenv(root / ".env", encoding="utf-8")
except ImportError:
    pass
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

# Windows 控制台 UTF-8
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _load_config() -> dict:
    """加载 bi_daily_report 配置"""
    import yaml
    candidates = [
        Path.home() / ".jachin" / "config" / "skills" / "com.jachin.bi.daily_report" / "bi_daily_report.yaml",
        root / "config" / "skills" / "com.jachin.bi.daily_report" / "bi_daily_report.yaml",
    ]
    for p in candidates:
        if p.exists():
            try:
                with open(p, encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            except Exception as e:
                print(f"[WARN] 配置加载失败 {p}: {e}", file=sys.stderr)
    return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="BI 战略分析单独测试")
    parser.add_argument("--date", default=None, help="报告日期 YYYY-MM-DD，默认昨日")
    parser.add_argument("--save", action="store_true", help="保存到文件 ~/.jachin/client_volumes/bi_data/output/strategic_report_YYYYMMDD.md")
    parser.add_argument("--no-save", dest="save", action="store_false")
    args = parser.parse_args()

    if args.date:
        try:
            dt = datetime.strptime(args.date[:10], "%Y-%m-%d")
        except ValueError:
            print(f"[ERROR] 无效日期: {args.date}")
            return 1
    else:
        dt = datetime.now() - timedelta(days=1)
    date_str = dt.strftime("%Y-%m-%d")

    cfg = _load_config()
    storage = cfg.get("storage") or {}
    refiner_path = (storage.get("refiner_output_path") or "").strip()
    from l3_node.mcp_tools.bi.paths import get_bi_output_dir
    output_dir = get_bi_output_dir(refiner_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"[战略分析] 日期: {date_str}")
    print(f"[战略分析] CSV 目录: {output_dir}")
    print("[战略分析] 正在生成战略战报（调用 LLM）...")
    print("-" * 50)

    async def _run() -> str:
        from l3_node.skills.bi.bi_daily_report.strategic_report import generate_bi_strategic_report_async
        return await generate_bi_strategic_report_async(
            metrics=None,
            output_dir=output_dir,
            config=cfg,
        )

    md = asyncio.run(_run())

    print(md)
    print("-" * 50)

    if args.save:
        out_file = output_dir / f"strategic_report_{date_str.replace('-', '')}.md"
        out_file.write_text(md, encoding="utf-8")
        print(f"[战略分析] 已保存: {out_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
