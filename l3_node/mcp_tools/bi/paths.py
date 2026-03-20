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


def get_bi_output_dir(override: str | Path | None = None) -> Path:
    """
    提纯输出目录，供 Lark 多维表格导入。

    默认: ~/.jachin/client_volumes/bi_data/output
    override: 配置项 storage.refiner_output_path；空则用默认
    """
    if override and str(override).strip():
        p = Path(str(override).strip())
        if p.is_absolute():
            return p
        return get_jachin_root() / p
    return get_bi_data_root() / "output"


def get_bi_duckdb_dir() -> Path:
    """DuckDB 文件所在目录 (~/.jachin/client_volumes/bi_data/duckdb)"""
    return get_bi_data_root() / "duckdb"


def get_bi_duckdb_path() -> Path:
    """DuckDB 文件路径 (~/.jachin/client_volumes/bi_data/duckdb/bi.duckdb)"""
    return get_bi_duckdb_dir() / "bi.duckdb"


def ensure_bi_dirs() -> None:
    """确保 BI 数据目录存在"""
    get_bi_raw_dir().mkdir(parents=True, exist_ok=True)
    get_bi_metrics_dir().mkdir(parents=True, exist_ok=True)
    get_bi_duckdb_dir().mkdir(parents=True, exist_ok=True)
    get_bi_output_dir().mkdir(parents=True, exist_ok=True)
