"""
Jachin Nexus V2 - Layer 2 控制面数据库 Schema

子账号、API Key 保险箱、L3 节点注册（含公钥）。
"""
from __future__ import annotations

import sqlite3


def _migrate_sub_accounts_pairing_code(conn: sqlite3.Connection) -> None:
    """迁移：为已有 sub_accounts 表添加 l1_pairing_code 列（若不存在）"""
    cur = conn.execute("PRAGMA table_info(sub_accounts)")
    cols = [row[1] for row in cur.fetchall()]
    if "l1_pairing_code" not in cols:
        conn.execute("ALTER TABLE sub_accounts ADD COLUMN l1_pairing_code TEXT")
        conn.commit()


def init_all(conn: sqlite3.Connection) -> None:
    """初始化所有 L2 控制面表"""
    conn.executescript(_SCHEMA_SQL)
    _migrate_sub_accounts_pairing_code(conn)
    conn.commit()


_SCHEMA_SQL = """
-- =============================================================================
-- L2 控制面 - 子账号
-- 由用户主账号在 L2 创建，permissions_json 定义允许的 L3 节点和 Skill 白名单
-- =============================================================================
CREATE TABLE IF NOT EXISTS sub_accounts (
    id TEXT PRIMARY KEY,
    main_user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    permissions_json TEXT NOT NULL DEFAULT '{}',
    l1_pairing_code TEXT,
    created_at REAL DEFAULT (strftime('%s', 'now')),
    updated_at REAL DEFAULT (strftime('%s', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_sub_accounts_main_user ON sub_accounts(main_user_id);

-- =============================================================================
-- L2 控制面 - L3 节点注册
-- L3 注册时上报设备指纹和公钥，用于 API Key 加密下发
-- =============================================================================
CREATE TABLE IF NOT EXISTS l3_nodes (
    id TEXT PRIMARY KEY,
    device_fingerprint TEXT,
    public_key_pem TEXT NOT NULL,
    sub_account_id TEXT,
    capabilities_json TEXT DEFAULT '{}',
    last_seen_at REAL DEFAULT (strftime('%s', 'now')),
    created_at REAL DEFAULT (strftime('%s', 'now')),
    FOREIGN KEY (sub_account_id) REFERENCES sub_accounts(id)
);

CREATE INDEX IF NOT EXISTS idx_l3_nodes_sub_account ON l3_nodes(sub_account_id);
CREATE INDEX IF NOT EXISTS idx_l3_nodes_fingerprint ON l3_nodes(device_fingerprint);

-- =============================================================================
-- L2 控制面 - API Key 保险箱
-- L2 用 Master Key 对称加密存储，下发给 L3 时用 L3 公钥加密
-- =============================================================================
CREATE TABLE IF NOT EXISTS api_keys_vault (
    id TEXT PRIMARY KEY,
    sub_account_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    encrypted_key TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    created_at REAL DEFAULT (strftime('%s', 'now')),
    FOREIGN KEY (sub_account_id) REFERENCES sub_accounts(id)
);

CREATE INDEX IF NOT EXISTS idx_api_keys_sub_account ON api_keys_vault(sub_account_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_provider ON api_keys_vault(provider);
"""
