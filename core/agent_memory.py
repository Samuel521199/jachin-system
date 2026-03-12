"""
Jachin Nexus Layer 2 - Agent 持久化记忆

为边缘智能体提供短期对话上下文与长期关键事件记忆。
支持 sqlite3 或极简 JSON 存储。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

# 存储路径：~/.jachin/memory.db（战役 1 规范）
_JACHIN_DIR = Path.home() / ".jachin"
_DB_PATH = _JACHIN_DIR / "memory.db"
_JSON_PATH = _JACHIN_DIR / "agent_memory.json"  # fallback
_USE_SQLITE = True  # 优先 sqlite，fallback 到 JSON


def _ensure_dir() -> None:
    _JACHIN_DIR.mkdir(parents=True, exist_ok=True)


def _init_sqlite() -> sqlite3.Connection:
    _ensure_dir()
    conn = sqlite3.connect(str(_DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp REAL DEFAULT (strftime('%s', 'now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversations_ts ON conversations(timestamp DESC)")
    conn.commit()
    return conn


def _init_json() -> list[dict[str, Any]]:
    _ensure_dir()
    if _JSON_PATH.exists():
        try:
            return json.loads(_JSON_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def add_memory(role: str, content: str) -> None:
    """
    添加一条记忆（user/assistant/system）。

    Args:
        role: 角色，如 "user", "assistant", "system"
        content: 内容
    """
    role = (role or "user").strip().lower()
    if role not in ("user", "assistant", "system"):
        role = "user"
    content = (content or "").strip()
    if not content:
        return

    if _USE_SQLITE:
        try:
            conn = _init_sqlite()
            conn.execute(
                "INSERT INTO conversations (role, content) VALUES (?, ?)",
                (role, content),
            )
            conn.commit()
            conn.close()
        except Exception:
            _add_memory_json(role, content)
    else:
        _add_memory_json(role, content)


def _add_memory_json(role: str, content: str) -> None:
    data = _init_json()
    data.append({
        "role": role,
        "content": content,
        "created_at": __import__("time").time(),
    })
    # 只保留最近 500 条
    if len(data) > 500:
        data = data[-500:]
    _JSON_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")


def get_context(limit: int = 10) -> list[dict[str, str]]:
    """
    获取最近 N 条记忆，用于 Agent 上下文。

    Returns:
        [{"role": "user", "content": "..."}, ...]，按时间升序（旧->新）
    """
    if _USE_SQLITE:
        try:
            conn = _init_sqlite()
            rows = conn.execute(
                "SELECT role, content FROM conversations ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
            conn.close()
            out = [{"role": r[0], "content": r[1]} for r in reversed(rows)]
            return out
        except Exception:
            return _get_context_json(limit)
    return _get_context_json(limit)


def _get_context_json(limit: int) -> list[dict[str, str]]:
    data = _init_json()
    recent = data[-limit:] if len(data) > limit else data
    return [{"role": r.get("role", "user"), "content": r.get("content", "")} for r in recent]


def clear_memory() -> None:
    """清空所有记忆（慎用）"""
    if _USE_SQLITE and _DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(_DB_PATH))
            conn.execute("DELETE FROM conversations")
            conn.commit()
            conn.close()
        except Exception:
            pass
    if _JSON_PATH.exists():
        _JSON_PATH.write_text("[]", encoding="utf-8")
