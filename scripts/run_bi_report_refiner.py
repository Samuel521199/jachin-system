#!/usr/bin/env python3
"""
BI 日报提纯脚本 — 从 DuckDB 提炼为 Lark 可导入的 CSV，并可选同步到 Lark 多维表格

用法:
  python scripts/run_bi_report_refiner.py [--date YYYY-MM-DD] [--output-dir PATH] [--sync-lark]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 确保项目根在 sys.path
root = Path(__file__).resolve().parents[1]
if str(root) not in sys.path:
    sys.path.insert(0, str(root))

from l3_node.primitives.mcp.mcp_tools.bi.report_refiner import run_refiner, sync_refiner_to_lark
from l3_node.primitives.mcp.mcp_tools.bi.paths import get_bi_output_dir, get_bi_raw_dir


def _load_config() -> dict:
    """从 bi_daily_report.yaml 读取配置"""
    from l3_node.paths import get_app_root
    jachin_root = Path.home() / ".jachin"
    for base in (jachin_root, get_app_root()):
        path = base / "config" / "skills" / "com.jachin.bi.daily_report" / "bi_daily_report.yaml"
        if path.exists():
            try:
                import yaml
                with open(path, encoding="utf-8") as f:
                    return yaml.safe_load(f) or {}
            except Exception:
                pass
    return {}


def main():
    ap = argparse.ArgumentParser(description="BI 日报提纯 — 输出 Lark 多维表格可导入 CSV")
    ap.add_argument(
        "--date",
        default=None,
        help="报表数据日 YYYY-MM-DD（= 提纯基准 t1，通常为昨日）；默认由程序按本机日期推算昨日",
    )
    ap.add_argument("--output-dir", default=None, help="输出目录；未指定时从配置读取，空则用默认")
    ap.add_argument("--sync-lark", action="store_true", help="提纯后同步到 Lark 多维表格（需配置 lark_bitable.enabled 或 tables）")
    args = ap.parse_args()

    cfg = _load_config()
    storage = cfg.get("storage") or {}
    output_override = storage.get("refiner_output_path") or ""

    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = get_bi_output_dir(output_override)

    raw_cfg = str(storage.get("analysis_raw_dir") or "").strip()
    if raw_cfg:
        _rawp = Path(raw_cfg).expanduser().resolve()
        raw_dir = _rawp if _rawp.exists() else get_bi_raw_dir()
    else:
        raw_dir = get_bi_raw_dir()

    written, errors = run_refiner(date_str=args.date, output_dir=output_dir, raw_dir=raw_dir)

    if errors:
        for e in errors:
            print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[OK] 已生成 {len(written)} 个 CSV 到 {output_dir}")
    for p in written:
        print(f"  - {p.name}")
    if len(written) < 11:
        print(file=sys.stderr)
        print("[提示] 仅生成部分 CSV，多数表无数据。请先执行：", file=sys.stderr)
        print("  1) .\\scripts\\launch_chrome_debug_bi.ps1  （Chrome 调试模式）", file=sys.stderr)
        print("  2) 在 Chrome 中登录 BI 后台", file=sys.stderr)
        print("  3) python scripts/run_bi_scraper_spa.py     （抓取到 raw/*.csv，提纯优先用 raw）", file=sys.stderr)
        print("  4) 可选: python scripts/import_raw_to_duckdb.py （导入 DuckDB 作兜底）", file=sys.stderr)
        print("  5) 再次运行本脚本", file=sys.stderr)

    # 同步到 Lark 多维表格
    lark_bitable = cfg.get("lark_bitable") or {}
    tables_map = lark_bitable.get("tables") or {}
    has_mapped = any((v or "").strip() for v in tables_map.values())
    if args.sync_lark or lark_bitable.get("enabled"):
        if not tables_map or not has_mapped:
            print("[WARN] 未配置 lark_bitable.tables（或全部 table_id 为空），跳过 Lark 同步", file=sys.stderr)
            print("      请在 bi_daily_report.yaml 的 lark_bitable.tables 中填入各 CSV 对应的 table_id", file=sys.stderr)
        else:
            sync_ok, sync_errs, sync_skipped = sync_refiner_to_lark(written, lark_bitable)
            if sync_skipped:
                for n, r in sync_skipped:
                    print(f"[Lark] 跳过: {n} — {r}", file=sys.stderr)
            if sync_errs:
                for e in sync_errs:
                    print(f"[Lark] 同步失败: {e}", file=sys.stderr)
            if sync_ok:
                print(f"[OK] 已同步 {sync_ok} 个表到 Lark 多维表格")
            # 提示未配置 table_id 的表
            tables_map = lark_bitable.get("tables") or {}
            no_id = [p.name for p in written if not (tables_map.get(p.name) or "").strip()]
            if no_id:
                print(file=sys.stderr)
                print(f"[提示] {len(no_id)} 个 CSV 未配置 table_id，已跳过同步：", file=sys.stderr)
                for n in no_id[:5]:
                    print(f"  - {n}", file=sys.stderr)
                if len(no_id) > 5:
                    print(f"  - ... 等共 {len(no_id)} 个", file=sys.stderr)
                print("      请在 bi_daily_report.yaml 的 lark_bitable.tables 中填入各 CSV 对应的 table_id", file=sys.stderr)


if __name__ == "__main__":
    main()
