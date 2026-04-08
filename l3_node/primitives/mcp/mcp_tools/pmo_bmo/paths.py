"""
PMO 数据目录 — 与 BI 的 client_volumes/bi_data 并列，使用 ~/.jachin/client_volumes/PMO/
"""
from __future__ import annotations

from pathlib import Path


def get_pmo_client_root() -> Path:
    return Path.home() / ".jachin" / "client_volumes" / "PMO"


def get_pmo_raw_dir() -> Path:
    """API 原始 JSON：~/.jachin/client_volumes/PMO/raw"""
    return get_pmo_client_root() / "raw"


def get_pmo_output_client_dir() -> Path:
    """提纯表 CSV 与同步产物：~/.jachin/client_volumes/PMO/output（与仓库 docs/pmo_bmo_plugin/output 对应）"""
    return get_pmo_client_root() / "output"


def get_pmo_duckdb_dir() -> Path:
    return get_pmo_client_root() / "duckdb"


def get_pmo_duckdb_path() -> Path:
    """PMO 专用 DuckDB（与 bi.duckdb 分离）"""
    return get_pmo_duckdb_dir() / "pmo.duckdb"


def get_pmo_docs_raw_rel() -> str:
    """相对仓库根的 Markdown 落盘目录"""
    return "docs/pmo_bmo_plugin/raw"


def ensure_pmo_dirs() -> None:
    get_pmo_raw_dir().mkdir(parents=True, exist_ok=True)
    get_pmo_duckdb_dir().mkdir(parents=True, exist_ok=True)
    get_pmo_output_client_dir().mkdir(parents=True, exist_ok=True)
