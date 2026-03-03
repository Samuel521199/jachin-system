"""
Jachin Nexus Layer 2 - 生物学记忆管线 (Biological Memory Pipeline)

v5.0 划时代设计：像人一样睡觉、遗忘和成长。
三层记忆：海马体（短期）→ 梦境引擎（压缩提纯）→ 大脑皮层（核心标识）。
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

_JACHIN_DIR = Path.home() / ".jachin"
_DB_PATH = _JACHIN_DIR / "memory.db"
_SHORT_TERM_RETENTION_HOURS = 24


def _ensure_dir() -> None:
    _JACHIN_DIR.mkdir(parents=True, exist_ok=True)


def _init_schema(conn: sqlite3.Connection) -> None:
    """初始化生物学记忆表"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS short_term_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            meta TEXT,
            timestamp REAL DEFAULT (strftime('%s', 'now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_short_term_ts ON short_term_logs(timestamp DESC)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS core_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tag TEXT NOT NULL,
            content TEXT NOT NULL,
            source_summary TEXT,
            created_at REAL DEFAULT (strftime('%s', 'now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_core_memory_tag ON core_memory(tag)")
    conn.commit()


def _get_conn() -> sqlite3.Connection:
    _ensure_dir()
    conn = sqlite3.connect(str(_DB_PATH))
    _init_schema(conn)
    return conn


# -----------------------------------------------------------------------------
# 海马体 (Hippocampus) - 短期缓存
# -----------------------------------------------------------------------------


def add_short_term(role: str, content: str, meta: dict[str, Any] | None = None) -> None:
    """
    海马体：无损记录一次交互。
    存活周期 24 小时，供梦境引擎提纯。
    """
    import json
    content = (content or "").strip()
    if not content:
        return
    role = (role or "user").strip().lower()
    if role not in ("user", "assistant", "system"):
        role = "user"
    meta_str = json.dumps(meta or {}, ensure_ascii=False) if meta else None

    try:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO short_term_logs (role, content, meta) VALUES (?, ?, ?)",
            (role, content, meta_str),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_short_term_for_dream(limit: int = 500) -> list[dict[str, Any]]:
    """
    获取今日 short_term_logs，供梦境引擎消费。
    仅返回 24 小时内的记录。
    """
    import json
    cutoff = time.time() - (_SHORT_TERM_RETENTION_HOURS * 3600)
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT id, role, content, meta, timestamp FROM short_term_logs WHERE timestamp >= ? ORDER BY timestamp ASC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
        conn.close()
        return [
            {
                "id": r[0],
                "role": r[1],
                "content": r[2],
                "meta": json.loads(r[3]) if r[3] else {},
                "timestamp": r[4],
            }
            for r in rows
        ]
    except Exception:
        return []


def delete_short_term_after_dream(ids: list[int]) -> None:
    """梦境引擎提纯后，删除已处理的短期日志"""
    if not ids:
        return
    try:
        conn = _get_conn()
        placeholders = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM short_term_logs WHERE id IN ({placeholders})", ids)
        conn.commit()
        conn.close()
    except Exception:
        pass


def prune_short_term_older_than_24h() -> None:
    """清理超过 24 小时的短期日志"""
    cutoff = time.time() - (_SHORT_TERM_RETENTION_HOURS * 3600)
    try:
        conn = _get_conn()
        conn.execute("DELETE FROM short_term_logs WHERE timestamp < ?", (cutoff,))
        conn.commit()
        conn.close()
    except Exception:
        pass


# -----------------------------------------------------------------------------
# 大脑皮层 (Core Memory) - 核心标识
# -----------------------------------------------------------------------------


def add_core_memory(tag: str, content: str, source_summary: str = "") -> None:
    """
    大脑皮层：写入一条核心记忆。
    tag 如 preference、server_alert、user_habit 等。
    """
    content = (content or "").strip()
    tag = (tag or "general").strip()
    if not content or not tag:
        return
    try:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO core_memory (tag, content, source_summary) VALUES (?, ?, ?)",
            (tag, content, source_summary or ""),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_core_memory_for_prompt(limit: int = 20) -> str:
    """
    获取最近 N 条核心记忆，格式化为 System Prompt 片段。
    供 Agent Loop 在每次思考前注入。
    """
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT tag, content FROM core_memory ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
        if not rows:
            return ""
        lines = ["[核心记忆] 以下是你已记住的重要信息，请据此服务主人："]
        for tag, content in rows:
            lines.append(f"  - [{tag}] {content}")
        return "\n".join(lines)
    except Exception:
        return ""
