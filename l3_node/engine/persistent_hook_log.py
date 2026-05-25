"""
可选：将 L3 HookRegistry 事件追加写入 SQLite，便于跨轮诊断与后续 DAG 续跑（AGI 路线图 §3.2.4 轻量落地）。

开启：环境变量 JACHIN_PERSIST_HOOKS=1|true|yes|on
路径：$JACHIN_HOME/workspace/hook_events.sqlite3（默认同 ~/.jachin/workspace）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from l3_node.engine.hooks_pipeline import PipelineContext

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_INSTALLED = False

_META_KEYS_KEEP = (
    "_execution_brief_reason",
    "_retry_reason",
    "_implicit_channel",
    "delegate_sub_task_index",
    "delegate_sub_task_role",
    "path",
    "node_id",
    "executed_tool",
    "task_node_error",
    "task_node_result_preview",
    "_task_decompose_sub_count",
    "_task_decompose_roles_preview",
    "_resilience_strategy",
    "_resilience_strategy_count",
    "_resilience_strategy_hint",
)


def _hooks_db_path() -> Path:
    root = Path(os.environ.get("JACHIN_HOME") or Path.home() / ".jachin").expanduser()
    d = root / "workspace"
    d.mkdir(parents=True, exist_ok=True)
    return d / "hook_events.sqlite3"


def _persist_hooks_enabled() -> bool:
    return (os.environ.get("JACHIN_PERSIST_HOOKS") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _compact_metadata(md: dict[str, Any] | None) -> dict[str, Any]:
    if not md:
        return {}
    out: dict[str, Any] = {}
    for k in _META_KEYS_KEEP:
        if k in md:
            v = md[k]
            if isinstance(v, (str, int, float, bool)) or v is None:
                out[k] = v
            else:
                out[k] = str(v)[:400]
    return out


def _sync_append_row(path: Path, hook: str, run_id: str, intent_preview: str, meta: dict[str, Any]) -> None:
    row_meta = json.dumps(meta, ensure_ascii=False)
    if len(row_meta) > 12000:
        row_meta = row_meta[:11997] + "…"
    with _LOCK:
        conn = sqlite3.connect(str(path), timeout=8.0)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS hook_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    hook TEXT NOT NULL,
                    run_id TEXT,
                    intent_preview TEXT,
                    meta_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_hook_events_run_id ON hook_events(run_id)"
            )
            conn.execute(
                "INSERT INTO hook_events (ts, hook, run_id, intent_preview, meta_json) VALUES (?,?,?,?,?)",
                (time.time(), hook, run_id or "", intent_preview[:500], row_meta),
            )
            conn.commit()
        finally:
            conn.close()


def ensure_persistent_hook_log_registered() -> None:
    """幂等：在 run_agent 入口调用一次即可；未设 JACHIN_PERSIST_HOOKS 则不注册。"""
    global _INSTALLED
    if _INSTALLED:
        return
    if not _persist_hooks_enabled():
        return
    from l3_node.engine.hooks_pipeline import (
        HOOK_AFTER_TOOL_EXEC,
        HOOK_BEFORE_LLM_THINK,
        HOOK_BEFORE_RESPONSE,
        HOOK_BEFORE_TOOL_EXEC,
        HOOK_ON_AGENT_TEAM_ASSEMBLED,
        HOOK_ON_CONSENSUS_REACHED,
        HOOK_ON_DISCUSSION_ROUND_END,
        HOOK_ON_DISCUSSION_ROUND_START,
        HOOK_ON_EXECUTION_BRIEF,
        HOOK_ON_EXPERIENCE_LEARNED,
        HOOK_ON_INTENT_RECEIVED,
        HOOK_ON_MEMORY_COMMIT,
        HOOK_ON_RETRY,
        HOOK_ON_STRATEGY_SHIFT,
        HOOK_ON_TASK_DAG_COMPLETE,
        HOOK_ON_TASK_DECOMPOSE,
        HOOK_ON_TASK_NODE_DONE,
        HOOK_ON_TASK_NODE_START,
        global_hooks,
    )

    _names = (
        HOOK_ON_INTENT_RECEIVED,
        HOOK_BEFORE_LLM_THINK,
        HOOK_BEFORE_TOOL_EXEC,
        HOOK_AFTER_TOOL_EXEC,
        HOOK_BEFORE_RESPONSE,
        HOOK_ON_TASK_DECOMPOSE,
        HOOK_ON_TASK_NODE_START,
        HOOK_ON_TASK_NODE_DONE,
        HOOK_ON_TASK_DAG_COMPLETE,
        HOOK_ON_AGENT_TEAM_ASSEMBLED,
        HOOK_ON_DISCUSSION_ROUND_START,
        HOOK_ON_DISCUSSION_ROUND_END,
        HOOK_ON_CONSENSUS_REACHED,
        HOOK_ON_RETRY,
        HOOK_ON_STRATEGY_SHIFT,
        HOOK_ON_EXECUTION_BRIEF,
        HOOK_ON_MEMORY_COMMIT,
        HOOK_ON_EXPERIENCE_LEARNED,
    )
    db = _hooks_db_path()

    def _factory(hook_name: str):
        async def _handler(ctx: PipelineContext) -> None:
            try:
                meta = _compact_metadata(getattr(ctx, "metadata", None))
                intent = str(getattr(ctx, "intent", "") or "")
                rid = str(getattr(ctx, "run_id", "") or "")
                await asyncio.to_thread(
                    _sync_append_row,
                    db,
                    hook_name,
                    rid,
                    intent,
                    meta,
                )
            except Exception as e:
                logger.debug("[PersistentHookLog] 追加失败 hook=%s: %s", hook_name, e)

        return _handler

    for n in _names:
        global_hooks.register(n, _factory(n))
    _INSTALLED = True
    logger.info("[PersistentHookLog] 已注册 %d 条 Hook → %s", len(_names), db)


def read_recent_hook_events(
    *,
    limit: int = 50,
    hook: str | None = None,
    run_id: str | None = None,
    run_id_exact: bool = False,
) -> list[dict[str, Any]]:
    """
    读取 hook_events 表最近若干条（诊断 / 轻量「回放」预览）；不持有写锁。
    需磁盘上已有 `JACHIN_PERSIST_HOOKS` 运行产生的库。

    run_id + run_id_exact=True：精确匹配单次 run（DAG 续跑探针）；否则 run_id 为 LIKE %...% 子串。
    """
    cap = max(1, min(500, int(limit)))
    path = _hooks_db_path()
    if not path.is_file():
        return []
    hook_f = (hook or "").strip() or None
    rid_f = (run_id or "").strip() or None
    exact = bool(run_id_exact) and bool(rid_f)
    rows: list[dict[str, Any]] = []
    conn = sqlite3.connect(str(path), timeout=8.0)
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='hook_events'"
        )
        if cur.fetchone() is None:
            return []
        q = "SELECT id, ts, hook, run_id, intent_preview, meta_json FROM hook_events WHERE 1=1"
        params: list[Any] = []
        if hook_f:
            q += " AND hook = ?"
            params.append(hook_f)
        if rid_f:
            if exact:
                q += " AND run_id = ?"
                params.append(rid_f)
            else:
                q += " AND run_id LIKE ?"
                params.append(f"%{rid_f}%")
        q += " ORDER BY id DESC LIMIT ?"
        params.append(cap)
        for r in conn.execute(q, params):
            meta_raw = r["meta_json"]
            try:
                meta_p = json.loads(meta_raw) if meta_raw else {}
            except json.JSONDecodeError:
                meta_p = {}
            rows.append({
                "id": r["id"],
                "ts": r["ts"],
                "hook": r["hook"],
                "run_id": r["run_id"],
                "intent_preview": r["intent_preview"],
                "meta": meta_p if isinstance(meta_p, dict) else {},
            })
    finally:
        conn.close()
    return rows


def read_hook_events_chronological(
    run_id: str,
    *,
    limit: int = 300,
    hook: str | None = None,
) -> list[dict[str, Any]]:
    """按时间正序读取单次 run 的 Hook 事件链（回放执行器用）。"""
    rid = (run_id or "").strip()
    if not rid:
        return []
    cap = max(1, min(1000, int(limit)))
    path = _hooks_db_path()
    if not path.is_file():
        return []
    hook_f = (hook or "").strip() or None
    rows: list[dict[str, Any]] = []
    conn = sqlite3.connect(str(path), timeout=8.0)
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='hook_events'"
        )
        if cur.fetchone() is None:
            return []
        q = (
            "SELECT id, ts, hook, run_id, intent_preview, meta_json "
            "FROM hook_events WHERE run_id = ?"
        )
        params: list[Any] = [rid]
        if hook_f:
            q += " AND hook = ?"
            params.append(hook_f)
        q += " ORDER BY id ASC LIMIT ?"
        params.append(cap)
        for r in conn.execute(q, params):
            meta_raw = r["meta_json"]
            try:
                meta_p = json.loads(meta_raw) if meta_raw else {}
            except json.JSONDecodeError:
                meta_p = {}
            rows.append({
                "id": r["id"],
                "ts": r["ts"],
                "hook": r["hook"],
                "run_id": r["run_id"],
                "intent_preview": r["intent_preview"],
                "meta": meta_p if isinstance(meta_p, dict) else {},
            })
    finally:
        conn.close()
    return rows


def hooks_db_available() -> bool:
    return _hooks_db_path().is_file()
