"""
BI 每日战报 — 路径常量与数据目录

基准: ~/.jachin/client_volumes/bi_data/
设计规范: docs/bi_daily_report/03_SKILL_DESIGN.md
"""
from __future__ import annotations

from pathlib import Path


def get_jachin_root() -> Path:
    """用户 Jachin 根目录 (~/.jachin)"""
    return Path.home() / ".jachin"


def get_bi_data_root() -> Path:
    """BI 数据根目录 (~/.jachin/client_volumes/bi_data)"""
    return get_jachin_root() / "client_volumes" / "bi_data"


def get_bi_raw_dir() -> Path:
    """原始抓取数据目录 (~/.jachin/client_volumes/bi_data/raw)"""
    return get_bi_data_root() / "raw"


def get_bi_metrics_dir() -> Path:
    """计算后指标目录 (~/.jachin/client_volumes/bi_data/metrics)"""
    return get_bi_data_root() / "metrics"


def ensure_bi_dirs() -> None:
    """确保 BI 数据目录存在"""
    get_bi_raw_dir().mkdir(parents=True, exist_ok=True)
    get_bi_metrics_dir().mkdir(parents=True, exist_ok=True)
