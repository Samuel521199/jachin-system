"""
Jachin Nexus V2 - Layer 2 控制面数据库

L2 零信任控制面：子账号、API Key 保险箱、L3 节点注册。
使用 SQLite 存储，默认 ~/.jachin/l2_control.db。
K8s 部署：设置 JACHIN_L2_DB_PATH 指向共享卷，确保多 Pod 共享同一数据。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

def _get_db_path() -> Path:
    env_path = os.environ.get("JACHIN_L2_DB_PATH")
    if env_path:
        return Path(env_path).expanduser()
    return Path.home() / ".jachin" / "l2_control.db"

_JACHIN_DIR = Path.home() / ".jachin"
_DB_PATH = _get_db_path()


def _ensure_dir() -> None:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def get_db_path() -> Path:
    return _DB_PATH


def get_connection() -> sqlite3.Connection:
    """获取 L2 控制面数据库连接"""
    _ensure_dir()
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    from core.db.schema import init_all
    init_all(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """初始化 L2 控制面 Schema（V2 架构）"""
    from core.db.schema import init_all
    init_all(conn)
