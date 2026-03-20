"""
BI 数据提纯 — 薄封装，重导出自 main_skill

逻辑已迁入 l3_node/skills/bi/bi_daily_report/main_skill.py（一个插件仅一个 skill）。
本模块保留以兼容 scripts/run_bi_report_refiner.py、query_bi_date_example.py 等外部引用。
"""
from __future__ import annotations

from l3_node.skills.bi.bi_daily_report.main_skill import (
    _find_col,
    _run_refiner as run_refiner,
    _sync_refiner_to_lark as sync_refiner_to_lark,
)

__all__ = ["run_refiner", "sync_refiner_to_lark", "_find_col"]
