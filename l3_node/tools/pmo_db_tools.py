"""
PMO-Copilot v6 — SQLite 原生工具 ``core:db_query`` / ``core:db_write``。

库文件默认 ``~/.jachin/workspace/pmo_db.sqlite``（可用 ``JACHIN_PMO_DB_PATH`` 覆盖）。
Schema SSOT：``docs/architecture/PMO_DB_REFACTOR_DESIGN.md`` §4。
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from l3_node.tools.pmo_import_json_loose import (
    extract_balanced_json_objects,
    loads_json_loose,
    salvage_bundle_tables,
)

logger = logging.getLogger(__name__)

PMO_NATIVE_TOOLS_LIST: list[dict[str, Any]] = [
    {
        "id": "core:db_query",
        "label": "core:db_query",
        "desc": (
            "PMO SQLite 只读查询（``~/.jachin/workspace/pmo_db.sqlite``）。"
            "**仅允许 SELECT**；写操作请用 core:db_write。"
            "JSON：sql（必填，可用 :name 占位符）；可选 params（对象）、max_rows（默认 200，上限 1000）。"
            "返回 status/rows/row_count/truncated。"
        ),
        "params": ["sql"],
    },
    {
        "id": "core:db_write",
        "label": "core:db_write",
        "desc": (
            "PMO SQLite 结构化写入（**SYNC/增量**；INIT 全量入库请用 core:pmo_import_json）。"
            "JSON：table、operation（insert|update|upsert）、records（对象数组）。"
        ),
        "params": ["table", "operation", "records"],
    },
    {
        "id": "core:pmo_import_json",
        "label": "core:pmo_import_json",
        "desc": (
            "PMO INIT 批量入库：从 JSON/NDJSON 文件 **Python 侧** upsert 到 SQLite（毫秒级，勿用 db_write 逐条）。"
            "支持 **宽容解析**：坏 JSON 会尝试 json_repair / 逐条拯救，返回 partial 而非全盘失败。"
            "JSON：file_path（必填，建议 ~/.jachin/workspace/pmo_staging/{view_id}_partN.ndjson）；"
            "可选 operation（默认 upsert）。"
            "文件格式 A（推荐 bundle）："
            '{"source_file":"01_….md","source_view":"vew…","tables":{"pmo_people":[],"pmo_dev_requirements":[]}}；'
            "格式 B（NDJSON）：每行 {\"table\":\"pmo_people\",\"records\":[…]}。"
            "写入顺序自动：people → 部门表 → personnel。"
            "personnel 写入时自动按 person_name 解析/补全 pmo_people.id（避免 FOREIGN KEY 失败）。"
        ),
        "params": ["file_path", "operation"],
    },
    {
        "id": "core:pmo_init_gap_report",
        "label": "core:pmo_init_gap_report",
        "desc": (
            "PMO INIT 缺口报告：对照 manifest 中各 md 的 source_file 统计四张业务表行数，"
            "列出 row_count=0 的未入库文件及各表总量。"
            "JSON：可选 manifest_path（默认 ~/.jachin/workspace/pmo_lark_pull/00_SYNC_MANIFEST.json）。"
        ),
        "params": ["manifest_path"],
    },
]

_TABLE_IMPORT_ORDER: tuple[str, ...] = (
    "pmo_people",
    "pmo_product_requirements",
    "pmo_dev_requirements",
    "pmo_design_requirements",
    "pmo_personnel_task_progress",
    "pmo_sync_state",
    "pmo_extraction_log",
)

_DEPT_TABLES = frozenset(
    {
        "pmo_product_requirements",
        "pmo_dev_requirements",
        "pmo_design_requirements",
    }
)

_WRITABLE_TABLES = _DEPT_TABLES | frozenset(
    {
        "pmo_personnel_task_progress",
        "pmo_people",
        "pmo_sync_state",
        "pmo_extraction_log",
    }
)

_TABLE_COLUMNS: dict[str, frozenset[str]] = {
    "pmo_product_requirements": frozenset(
        {
            "id",
            "requirement_name",
            "assigned_people",
            "work_cycle",
            "start_date",
            "end_date",
            "execution_stage",
            "planned_schedule",
            "priority",
            "flow_progress_note",
            "parent_id",
            "root_id",
            "hierarchy_depth",
            "node_kind",
            "source_view",
            "source_file",
            "extracted_at",
            "updated_at",
            "confidence",
            "raw_text",
        }
    ),
    "pmo_dev_requirements": frozenset(
        {
            "id",
            "requirement_name",
            "assigned_people",
            "work_cycle",
            "start_date",
            "end_date",
            "execution_stage",
            "planned_schedule",
            "priority",
            "flow_progress_note",
            "parent_id",
            "root_id",
            "hierarchy_depth",
            "node_kind",
            "source_view",
            "source_file",
            "extracted_at",
            "updated_at",
            "confidence",
            "raw_text",
        }
    ),
    "pmo_design_requirements": frozenset(
        {
            "id",
            "requirement_name",
            "assigned_people",
            "work_cycle",
            "start_date",
            "end_date",
            "execution_stage",
            "planned_schedule",
            "priority",
            "flow_progress_note",
            "parent_id",
            "root_id",
            "hierarchy_depth",
            "node_kind",
            "source_view",
            "source_file",
            "extracted_at",
            "updated_at",
            "confidence",
            "raw_text",
        }
    ),
    "pmo_people": frozenset({"id", "name", "dept", "role", "is_active"}),
    "pmo_personnel_task_progress": frozenset(
        {
            "id",
            "person_id",
            "person_name",
            "task_name",
            "planned_time",
            "completed_time",
            "execution_stage",
            "flow_progress_note",
            "priority",
            "work_cycle",
            "dept",
            "parent_task_id",
            "dept_requirement_id",
            "dept_table",
            "root_id",
            "hierarchy_depth",
            "source_view",
            "source_file",
            "extracted_at",
            "updated_at",
            "confidence",
            "raw_text",
        }
    ),
    "pmo_sync_state": frozenset(
        {"view_id", "view_name", "target_table", "last_synced", "record_count", "sync_status"}
    ),
    "pmo_extraction_log": frozenset(
        {
            "id",
            "run_id",
            "target_table",
            "source_view",
            "record_id",
            "action",
            "confidence",
            "notes",
            "created_at",
        }
    ),
}

_FORBIDDEN_SQL_RE = re.compile(
    r"\b("
    r"insert|update|delete|drop|alter|create|replace|truncate|attach|detach|"
    r"pragma\s+(?!table_info|index_list|foreign_key_list)|grant|revoke|vacuum|reindex"
    r")\b",
    re.IGNORECASE,
)

_SCHEMA_VERSION = 1

_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS pmo_schema_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pmo_people (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL UNIQUE,
  dept       TEXT,
  role       TEXT,
  is_active  INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS pmo_product_requirements (
  id                  TEXT PRIMARY KEY,
  requirement_name    TEXT NOT NULL,
  assigned_people     TEXT,
  work_cycle          TEXT,
  start_date          TEXT,
  end_date            TEXT,
  execution_stage     TEXT,
  planned_schedule    TEXT,
  priority            TEXT,
  flow_progress_note  TEXT,
  parent_id           TEXT,
  root_id             TEXT,
  hierarchy_depth     INTEGER,
  node_kind           TEXT,
  source_view         TEXT,
  source_file         TEXT,
  extracted_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
  confidence          REAL DEFAULT 1.0,
  raw_text            TEXT
);

CREATE TABLE IF NOT EXISTS pmo_dev_requirements (
  id                  TEXT PRIMARY KEY,
  requirement_name    TEXT NOT NULL,
  assigned_people     TEXT,
  work_cycle          TEXT,
  start_date          TEXT,
  end_date            TEXT,
  execution_stage     TEXT,
  planned_schedule    TEXT,
  priority            TEXT,
  flow_progress_note  TEXT,
  parent_id           TEXT,
  root_id             TEXT,
  hierarchy_depth     INTEGER,
  node_kind           TEXT,
  source_view         TEXT,
  source_file         TEXT,
  extracted_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
  confidence          REAL DEFAULT 1.0,
  raw_text            TEXT
);

CREATE TABLE IF NOT EXISTS pmo_design_requirements (
  id                  TEXT PRIMARY KEY,
  requirement_name    TEXT NOT NULL,
  assigned_people     TEXT,
  work_cycle          TEXT,
  start_date          TEXT,
  end_date            TEXT,
  execution_stage     TEXT,
  planned_schedule    TEXT,
  priority            TEXT,
  flow_progress_note  TEXT,
  parent_id           TEXT,
  root_id             TEXT,
  hierarchy_depth     INTEGER,
  node_kind           TEXT,
  source_view         TEXT,
  source_file         TEXT,
  extracted_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
  confidence          REAL DEFAULT 1.0,
  raw_text            TEXT
);

CREATE TABLE IF NOT EXISTS pmo_personnel_task_progress (
  id                  TEXT PRIMARY KEY,
  person_id           TEXT NOT NULL,
  person_name         TEXT NOT NULL,
  task_name           TEXT NOT NULL,
  planned_time        TEXT,
  completed_time      TEXT,
  execution_stage     TEXT,
  flow_progress_note  TEXT,
  priority            TEXT,
  work_cycle          TEXT,
  dept                TEXT,
  parent_task_id      TEXT,
  dept_requirement_id TEXT,
  dept_table          TEXT,
  root_id             TEXT,
  hierarchy_depth     INTEGER,
  source_view         TEXT,
  source_file         TEXT,
  extracted_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
  confidence          REAL DEFAULT 1.0,
  raw_text            TEXT,
  FOREIGN KEY (person_id) REFERENCES pmo_people(id)
);

CREATE TABLE IF NOT EXISTS pmo_sync_state (
  view_id       TEXT PRIMARY KEY,
  view_name     TEXT,
  target_table  TEXT,
  last_synced   DATETIME,
  record_count  INTEGER,
  sync_status   TEXT
);

CREATE TABLE IF NOT EXISTS pmo_change_queue (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  received_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
  table_id       TEXT NOT NULL,
  view_id        TEXT,
  record_id      TEXT,
  change_type    TEXT,
  changed_fields TEXT,
  raw_payload    TEXT,
  status         TEXT DEFAULT 'pending',
  processed_at   DATETIME,
  error_msg      TEXT
);

CREATE TABLE IF NOT EXISTS pmo_extraction_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id        TEXT,
  target_table  TEXT,
  source_view   TEXT,
  record_id     TEXT,
  action        TEXT,
  confidence    REAL,
  notes         TEXT,
  created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_product_parent     ON pmo_product_requirements(parent_id);
CREATE INDEX IF NOT EXISTS idx_product_root       ON pmo_product_requirements(root_id);
CREATE INDEX IF NOT EXISTS idx_product_work_cycle ON pmo_product_requirements(work_cycle);
CREATE INDEX IF NOT EXISTS idx_dev_parent         ON pmo_dev_requirements(parent_id);
CREATE INDEX IF NOT EXISTS idx_dev_root           ON pmo_dev_requirements(root_id);
CREATE INDEX IF NOT EXISTS idx_dev_work_cycle     ON pmo_dev_requirements(work_cycle);
CREATE INDEX IF NOT EXISTS idx_design_parent      ON pmo_design_requirements(parent_id);
CREATE INDEX IF NOT EXISTS idx_design_root        ON pmo_design_requirements(root_id);
CREATE INDEX IF NOT EXISTS idx_design_work_cycle  ON pmo_design_requirements(work_cycle);
CREATE INDEX IF NOT EXISTS idx_people_name        ON pmo_people(name);
CREATE INDEX IF NOT EXISTS idx_personnel_person   ON pmo_personnel_task_progress(person_id);
CREATE INDEX IF NOT EXISTS idx_personnel_parent   ON pmo_personnel_task_progress(parent_task_id);
CREATE INDEX IF NOT EXISTS idx_personnel_dept_req ON pmo_personnel_task_progress(dept_requirement_id);
CREATE INDEX IF NOT EXISTS idx_personnel_root     ON pmo_personnel_task_progress(root_id);
CREATE INDEX IF NOT EXISTS idx_personnel_cycle    ON pmo_personnel_task_progress(work_cycle);
CREATE INDEX IF NOT EXISTS idx_queue_status       ON pmo_change_queue(status);
CREATE INDEX IF NOT EXISTS idx_extraction_table   ON pmo_extraction_log(target_table);
"""


def get_pmo_db_path() -> Path:
    raw = (os.environ.get("JACHIN_PMO_DB_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    ws = Path(os.environ.get("JACHIN_HOME") or Path.home() / ".jachin").expanduser() / "workspace"
    return (ws / "pmo_db.sqlite").resolve()


def get_pmo_staging_dir() -> Path:
    """INIT 提取 JSON 落盘目录（与 core:fs_write workspace 一致）。"""
    ws = Path(os.environ.get("JACHIN_HOME") or Path.home() / ".jachin").expanduser() / "workspace"
    return (ws / "pmo_staging").resolve()


def get_default_pmo_manifest_path() -> Path:
    ws = Path(os.environ.get("JACHIN_HOME") or Path.home() / ".jachin").expanduser() / "workspace"
    return (ws / "pmo_lark_pull" / "00_SYNC_MANIFEST.json").resolve()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect() -> sqlite3.Connection:
    path = get_pmo_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_pmo_schema(conn: sqlite3.Connection | None = None) -> Path:
    """创建 PMO 库与表（幂等）。返回 db 路径。"""
    own = conn is None
    if own:
        conn = _connect()
    try:
        conn.executescript(_SCHEMA_DDL)
        conn.execute(
            "INSERT OR REPLACE INTO pmo_schema_meta(key, value) VALUES (?, ?)",
            ("schema_version", str(_SCHEMA_VERSION)),
        )
        conn.commit()
    finally:
        if own and conn is not None:
            conn.close()
    return get_pmo_db_path()


def init_pmo_database(*, force: bool = False) -> dict[str, Any]:
    """初始化或强制重建 PMO 数据库。"""
    path = get_pmo_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if force and path.exists():
        path.unlink()
    conn = _connect()
    try:
        ensure_pmo_schema(conn)
        counts: dict[str, int] = {}
        for table in sorted(_WRITABLE_TABLES):
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
            counts[table] = int(row["n"]) if row else 0
        return {
            "status": "ok",
            "db_path": str(path),
            "force": force,
            "schema_version": _SCHEMA_VERSION,
            "row_counts": counts,
        }
    finally:
        conn.close()


def _strip_sql_comments(sql: str) -> str:
    s = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    s = re.sub(r"--[^\n\r]*", " ", s)
    return s.strip()


def _validate_select_sql(sql: str) -> None:
    cleaned = _strip_sql_comments(sql)
    if not cleaned:
        raise ValueError("sql 不能为空")
    parts = [p.strip() for p in cleaned.split(";") if p.strip()]
    if len(parts) != 1:
        raise ValueError("仅允许单条 SELECT 语句")
    stmt = parts[0]
    if not re.match(r"^select\b", stmt, re.IGNORECASE):
        raise ValueError("core:db_query 仅允许 SELECT")
    if _FORBIDDEN_SQL_RE.search(stmt):
        raise ValueError("SQL 含禁止关键字或非只读 PRAGMA")


def _coerce_params(params: Any) -> dict[str, Any]:
    if params is None:
        return {}
    if isinstance(params, str):
        params = params.strip()
        if not params:
            return {}
        try:
            params = json.loads(params)
        except json.JSONDecodeError as e:
            raise ValueError(f"params 须为 JSON 对象: {e}") from e
    if not isinstance(params, dict):
        raise ValueError("params 须为 JSON 对象")
    return dict(params)


def _coerce_records(records: Any) -> list[dict[str, Any]]:
    if records is None:
        return []
    if isinstance(records, str):
        records = records.strip()
        if not records:
            return []
        try:
            records = json.loads(records)
        except json.JSONDecodeError as e:
            raise ValueError(f"records 须为 JSON 数组: {e}") from e
    if isinstance(records, dict):
        records = [records]
    if not isinstance(records, list):
        raise ValueError("records 须为对象数组")
    out: list[dict[str, Any]] = []
    for i, row in enumerate(records):
        if not isinstance(row, dict):
            raise ValueError(f"records[{i}] 须为对象")
        out.append(dict(row))
    return out


def _normalize_write_record(table: str, record: dict[str, Any], *, is_insert: bool) -> dict[str, Any]:
    allowed = _TABLE_COLUMNS[table]
    out: dict[str, Any] = {}
    for k, v in record.items():
        key = str(k).strip()
        if key not in allowed:
            raise ValueError(f"表 {table} 不支持字段 {key!r}")
        out[key] = v

    if table == "pmo_extraction_log":
        if is_insert and not out.get("id"):
            out["id"] = None  # autoincrement
        return out

    if table == "pmo_sync_state":
        if not out.get("view_id"):
            raise ValueError("pmo_sync_state 须含 view_id")
        return out

    if table == "pmo_people":
        if not out.get("id"):
            name = str(out.get("name") or "").strip()
            if not name:
                raise ValueError("pmo_people 须含 id 或 name")
            out["id"] = name
        if not str(out.get("name") or "").strip():
            out["name"] = str(out["id"])
        if out.get("is_active") is None:
            out["is_active"] = 1
        return out

    rid = str(out.get("id") or "").strip()
    if not rid:
        rid = f"pmo_{uuid.uuid4().hex[:16]}"
        out["id"] = rid

    if table in _DEPT_TABLES:
        if not str(out.get("requirement_name") or "").strip():
            raise ValueError(f"{table} 须含 requirement_name")
    elif table == "pmo_personnel_task_progress":
        if not str(out.get("task_name") or "").strip():
            raise ValueError("pmo_personnel_task_progress 须含 task_name")
        if not str(out.get("person_name") or "").strip() and not str(out.get("person_id") or "").strip():
            raise ValueError("pmo_personnel_task_progress 须含 person_id 或 person_name")
        if not str(out.get("person_id") or "").strip():
            out["person_id"] = str(out.get("person_name") or "").strip()

    now = _utc_now_iso()
    if is_insert:
        out.setdefault("extracted_at", now)
    out["updated_at"] = now
    if out.get("confidence") is None:
        out["confidence"] = 1.0
    return out


def _row_exists(conn: sqlite3.Connection, table: str, pk_col: str, pk_val: str) -> bool:
    row = conn.execute(f"SELECT 1 FROM {table} WHERE {pk_col} = ? LIMIT 1", (pk_val,)).fetchone()
    return row is not None


def _slug_person_id_base(name: str) -> str:
    base = re.sub(r"[^\w.]+", "_", (name or "").strip().lower()).strip("_").replace(".", "_")
    return base or "person"


def _suggest_person_id(conn: sqlite3.Connection, display_name: str) -> str:
    """由显示名生成 pmo_people.id（与 INIT 常见 ethan_001 / koi_liu_001 风格对齐）。"""
    base = _slug_person_id_base(display_name)
    for candidate in (f"{base}_001", base):
        if not _row_exists(conn, "pmo_people", "id", candidate):
            return candidate
    for n in range(2, 1000):
        candidate = f"{base}_{n:03d}"
        if not _row_exists(conn, "pmo_people", "id", candidate):
            return candidate
    return f"pmo_{uuid.uuid4().hex[:12]}"


def _lookup_people_id(conn: sqlite3.Connection, name_or_id: str) -> tuple[str, str] | None:
    """按 id 或 name（大小写不敏感）查找 pmo_people，返回 (id, name)。"""
    key = (name_or_id or "").strip()
    if not key:
        return None
    row = conn.execute(
        """
        SELECT id, name FROM pmo_people
        WHERE id = ? OR lower(trim(name)) = lower(trim(?))
        LIMIT 1
        """,
        (key, key),
    ).fetchone()
    if not row:
        return None
    return str(row[0]), str(row[1])


def _upsert_people_minimal(
    conn: sqlite3.Connection,
    *,
    person_id: str,
    person_name: str,
    dept: str | None = None,
) -> None:
    """INSERT 或按 id 更新 name（personnel 导入时自动补 people 锚点）。"""
    conn.execute(
        """
        INSERT INTO pmo_people (id, name, dept, is_active)
        VALUES (?, ?, ?, 1)
        ON CONFLICT(id) DO UPDATE SET
          name = excluded.name,
          dept = COALESCE(excluded.dept, pmo_people.dept)
        """,
        (person_id, person_name, dept),
    )


def _resolve_personnel_person_fk(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    """
    personnel.person_id 须引用 pmo_people.id。
    LLM 常把人名（Ethan）写入 person_id；此处按 name/id 解析，必要时自动 upsert people。
    """
    pid = str(record.get("person_id") or "").strip()
    pname = str(record.get("person_name") or "").strip()

    if pid and _row_exists(conn, "pmo_people", "id", pid):
        if not pname:
            row = conn.execute("SELECT name FROM pmo_people WHERE id = ? LIMIT 1", (pid,)).fetchone()
            if row:
                record["person_name"] = str(row[0])
        return

    for candidate in (pname, pid):
        if not candidate:
            continue
        found = _lookup_people_id(conn, candidate)
        if found:
            record["person_id"], record["person_name"] = found[0], found[1]
            return

    display = pname or pid
    if not display:
        raise ValueError("pmo_personnel_task_progress 无法解析 person_id/person_name")

    new_id = _suggest_person_id(conn, display)
    dept = str(record.get("dept") or "").strip() or None
    _upsert_people_minimal(conn, person_id=new_id, person_name=display, dept=dept)
    record["person_id"] = new_id
    record["person_name"] = display


def _prepare_write_record(
    conn: sqlite3.Connection,
    *,
    table: str,
    raw: dict[str, Any],
    is_insert: bool,
) -> dict[str, Any]:
    norm = _normalize_write_record(table, raw, is_insert=is_insert)
    if table == "pmo_personnel_task_progress":
        _resolve_personnel_person_fk(conn, norm)
    return norm


def _apply_write(
    conn: sqlite3.Connection,
    *,
    table: str,
    operation: str,
    record: dict[str, Any],
) -> tuple[str, float | None]:
    op = operation.lower().strip()
    if op not in ("insert", "update", "upsert"):
        raise ValueError("operation 须为 insert | update | upsert")

    if table == "pmo_extraction_log":
        cols = [k for k, v in record.items() if k != "id" or v is not None]
        vals = [record[k] for k in cols]
        placeholders = ", ".join("?" for _ in cols)
        conn.execute(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
            vals,
        )
        return "inserted", record.get("confidence")  # type: ignore[return-value]

    if table == "pmo_sync_state":
        pk_col, pk_val = "view_id", str(record["view_id"])
        exists = _row_exists(conn, table, pk_col, pk_val)
        if op == "insert" and exists:
            raise ValueError(f"记录已存在: {table}.{pk_val}")
        if op == "update" and not exists:
            raise ValueError(f"记录不存在: {table}.{pk_val}")
        cols = [k for k in record.keys()]
        if op == "update":
            set_clause = ", ".join(f"{c}=?" for c in cols if c != pk_col)
            vals = [record[c] for c in cols if c != pk_col] + [pk_val]
            conn.execute(f"UPDATE {table} SET {set_clause} WHERE {pk_col}=?", vals)
            return "updated", None
        if exists and op == "upsert":
            set_clause = ", ".join(f"{c}=?" for c in cols if c != pk_col)
            vals = [record[c] for c in cols if c != pk_col] + [pk_val]
            conn.execute(f"UPDATE {table} SET {set_clause} WHERE {pk_col}=?", vals)
            return "updated", None
        placeholders = ", ".join("?" for _ in cols)
        conn.execute(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
            [record[c] for c in cols],
        )
        return "inserted", None

    pk_col, pk_val = "id", str(record["id"])
    exists = _row_exists(conn, table, pk_col, pk_val)

    if op == "insert":
        if exists:
            raise ValueError(f"记录已存在: {table}.{pk_val}")
        cols = list(record.keys())
        conn.execute(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            [record[c] for c in cols],
        )
        return "inserted", float(record.get("confidence") or 1.0)

    if op == "update":
        if not exists:
            raise ValueError(f"记录不存在: {table}.{pk_val}")
        cols = [c for c in record.keys() if c != "extracted_at"]
        set_clause = ", ".join(f"{c}=?" for c in cols if c != pk_col)
        vals = [record[c] for c in cols if c != pk_col] + [pk_val]
        conn.execute(f"UPDATE {table} SET {set_clause} WHERE {pk_col}=?", vals)
        return "updated", float(record.get("confidence") or 1.0)

    # upsert
    cols = list(record.keys())
    if exists:
        upd_cols = [c for c in cols if c not in ("id", "extracted_at")]
        set_clause = ", ".join(f"{c}=?" for c in upd_cols)
        vals = [record[c] for c in upd_cols] + [pk_val]
        conn.execute(f"UPDATE {table} SET {set_clause} WHERE {pk_col}=?", vals)
        return "updated", float(record.get("confidence") or 1.0)

    conn.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
        [record[c] for c in cols],
    )
    return "inserted", float(record.get("confidence") or 1.0)


def run_db_query(
    *,
    sql: str = "",
    params: Any = None,
    max_rows: Any = 200,
) -> dict[str, Any]:
    """执行只读 SELECT。"""
    sql_s = str(sql or "").strip()
    if not sql_s:
        return {"status": "error", "error": "missing_sql", "message": "sql 不能为空"}
    try:
        _validate_select_sql(sql_s)
        bound = _coerce_params(params)
        try:
            limit = int(max_rows)
        except (TypeError, ValueError):
            limit = 200
        limit = max(1, min(limit, 1000))

        conn = _connect()
        try:
            ensure_pmo_schema(conn)
            cur = conn.execute(sql_s, bound)
            rows_raw = cur.fetchmany(limit + 1)
            truncated = len(rows_raw) > limit
            rows_raw = rows_raw[:limit]
            rows = [dict(r) for r in rows_raw]
            return {
                "status": "ok",
                "rows": rows,
                "row_count": len(rows),
                "truncated": truncated,
                "db_path": str(get_pmo_db_path()),
            }
        finally:
            conn.close()
    except Exception as e:
        logger.warning("[pmo_db_query] %s", e)
        return {"status": "error", "error": type(e).__name__, "message": str(e)}


def run_db_write(
    *,
    table: str = "",
    operation: str = "upsert",
    records: Any = None,
) -> dict[str, Any]:
    """结构化写入 PMO 业务表。"""
    table_s = str(table or "").strip()
    if not table_s:
        return {"status": "error", "error": "missing_table", "message": "table 不能为空"}
    if table_s not in _WRITABLE_TABLES:
        return {
            "status": "error",
            "error": "forbidden_table",
            "message": f"不允许写入表 {table_s!r}；允许: {', '.join(sorted(_WRITABLE_TABLES))}",
        }
    try:
        recs = _coerce_records(records)
        if not recs:
            return {"status": "error", "error": "empty_records", "message": "records 不能为空"}

        op = str(operation or "upsert").strip().lower()
        inserted = updated = skipped = 0
        low_confidence_warnings: list[dict[str, Any]] = []
        errors: list[str] = []

        conn = _connect()
        try:
            ensure_pmo_schema(conn)
            for i, raw in enumerate(recs):
                try:
                    is_insert = op == "insert" or (op == "upsert" and not _record_exists_for_table(conn, table_s, raw))
                    norm = _prepare_write_record(conn, table=table_s, raw=raw, is_insert=is_insert)
                    action, conf = _apply_write(conn, table=table_s, operation=op, record=norm)
                    if action == "inserted":
                        inserted += 1
                    elif action == "updated":
                        updated += 1
                    if conf is not None and conf < 0.7:
                        low_confidence_warnings.append(
                            {
                                "table": table_s,
                                "id": norm.get("id") or norm.get("view_id"),
                                "confidence": conf,
                                "index": i,
                            }
                        )
                except Exception as row_e:
                    skipped += 1
                    errors.append(f"records[{i}]: {row_e}")
            conn.commit()
        finally:
            conn.close()

        out: dict[str, Any] = {
            "status": "ok" if not errors or (inserted + updated) > 0 else "error",
            "inserted": inserted,
            "updated": updated,
            "skipped": skipped,
            "low_confidence_warnings": low_confidence_warnings,
            "db_path": str(get_pmo_db_path()),
        }
        if errors:
            out["errors"] = errors
            if out["status"] == "error":
                out["message"] = "; ".join(errors[:5])
        return out
    except Exception as e:
        logger.warning("[pmo_db_write] %s", e, exc_info=True)
        return {"status": "error", "error": type(e).__name__, "message": str(e)}


def _record_exists_for_table(conn: sqlite3.Connection, table: str, raw: dict[str, Any]) -> bool:
    if table == "pmo_sync_state":
        vid = str(raw.get("view_id") or "").strip()
        return bool(vid) and _row_exists(conn, table, "view_id", vid)
    rid = str(raw.get("id") or "").strip()
    if not rid:
        return False
    return _row_exists(conn, table, "id", rid)


def _resolve_import_json_path(file_path: str) -> Path:
    raw = str(file_path or "").strip()
    if not raw:
        raise ValueError("file_path 不能为空")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        ws = Path(os.environ.get("JACHIN_HOME") or Path.home() / ".jachin").expanduser() / "workspace"
        p = (ws / p).resolve()
    else:
        p = p.resolve()
    if not p.is_file():
        raise FileNotFoundError(f"导入文件不存在: {p}")
    if p.suffix.lower() not in (".json", ".ndjson", ".jsonl"):
        raise ValueError("file_path 须为 .json / .ndjson / .jsonl")
    return p


_TABLES_WITH_SOURCE_META = _DEPT_TABLES | frozenset({"pmo_personnel_task_progress"})


def _apply_bundle_meta(record: dict[str, Any], *, table: str, source_file: str, source_view: str) -> dict[str, Any]:
    out = dict(record)
    if table not in _TABLES_WITH_SOURCE_META:
        return out
    if source_file and not str(out.get("source_file") or "").strip():
        out["source_file"] = source_file
    if source_view and not str(out.get("source_view") or "").strip():
        out["source_view"] = source_view
    return out


def _parse_import_batches(
    text: str, *, default_operation: str
) -> tuple[str, str, list[tuple[str, str, list[dict[str, Any]]]], list[str]]:
    """
    解析导入文件为 (source_file, source_view, batches, parse_warnings)。
    batches: [(table, operation, records), ...]
    """
    parse_warnings: list[str] = []
    stripped = text.strip()
    if not stripped:
        raise ValueError("导入文件为空")

    # 多行：优先整文件 bundle；否则按 NDJSON 逐行（首行坏也走 NDJSON）
    lines = [ln.strip() for ln in stripped.splitlines() if ln.strip()]
    if len(lines) > 1:
        bundle_try, bundle_mode = loads_json_loose(stripped)
        if isinstance(bundle_try, dict) and "tables" in bundle_try:
            if bundle_mode == "repaired":
                parse_warnings.append("bundle:json_repair")
            data = bundle_try
            src_file = str(data.get("source_file") or "")
            src_view = str(data.get("source_view") or "")
            batches = []
            for table, recs_raw in data["tables"].items():
                table_s = str(table).strip()
                if table_s not in _WRITABLE_TABLES:
                    raise ValueError(f"不允许写入表 {table_s!r}")
                recs = _coerce_records(recs_raw)
                if not recs and isinstance(recs_raw, str):
                    recs = extract_balanced_json_objects(recs_raw)
                    if recs:
                        parse_warnings.append(f"bundle:{table_s}:salvaged_array")
                recs = [_apply_bundle_meta(r, table=table_s, source_file=src_file, source_view=src_view) for r in recs]
                if recs:
                    op = str(data.get("operation") or default_operation).strip().lower()
                    batches.append((table_s, op, recs))
            return src_file, src_view, batches, parse_warnings

        # NDJSON 逐行
        batches = []
        src_file = ""
        src_view = ""
        for line_no, ln in enumerate(lines, start=1):
            obj, mode = loads_json_loose(ln)
            if obj is None:
                salvaged = extract_balanced_json_objects(ln)
                if salvaged:
                    for sobj in salvaged:
                        if "table" in sobj:
                            obj = sobj
                            parse_warnings.append(f"ndjson:line{line_no}:salvaged_object")
                            break
                if obj is None:
                    parse_warnings.append(f"ndjson:line{line_no}:skip_invalid")
                    continue
            elif mode == "repaired":
                parse_warnings.append(f"ndjson:line{line_no}:json_repair")
            if not isinstance(obj, dict):
                parse_warnings.append(f"ndjson:line{line_no}:not_object")
                continue
            if obj.get("source_file"):
                src_file = str(obj["source_file"])
            if obj.get("source_view"):
                src_view = str(obj["source_view"])
            table = str(obj.get("table") or "").strip()
            if not table:
                parse_warnings.append(f"ndjson:line{line_no}:missing_table")
                continue
            if table not in _WRITABLE_TABLES:
                parse_warnings.append(f"ndjson:line{line_no}:bad_table:{table}")
                continue
            op = str(obj.get("operation") or default_operation).strip().lower()
            recs = _coerce_records(obj.get("records") if "records" in obj else obj.get("record"))
            if not recs and "records" in ln:
                inner = _extract_balanced_array_inner(ln, ln.find("["))
                recs = extract_balanced_json_objects(inner)
                if recs:
                    parse_warnings.append(f"ndjson:line{line_no}:salvaged_records")
            meta_sf = str(obj.get("source_file") or src_file)
            meta_sv = str(obj.get("source_view") or src_view)
            recs = [_apply_bundle_meta(r, table=table, source_file=meta_sf, source_view=meta_sv) for r in recs]
            if recs:
                batches.append((table, op, recs))
        return src_file, src_view, batches, parse_warnings

    data, parse_mode = loads_json_loose(stripped)
    if parse_mode == "repaired":
        parse_warnings.append("bundle:json_repair")
    if data is None:
        src_file, src_view, salvaged_batches, salvage_warn = salvage_bundle_tables(
            stripped,
            writable_tables=_WRITABLE_TABLES,
            coerce_records=_coerce_records,
        )
        parse_warnings.extend(salvage_warn)
        if salvaged_batches:
            parse_warnings.append("bundle:salvage_fallback")
            return src_file, src_view, salvaged_batches, parse_warnings
        raise ValueError("JSON 无法解析且逐条拯救未得到任何 records")

    if not isinstance(data, dict):
        raise ValueError("JSON 根须为对象（bundle 或单表 batch）")

    src_file = str(data.get("source_file") or "")
    src_view = str(data.get("source_view") or "")

    if "tables" in data and isinstance(data["tables"], dict):
        batches = []
        for table, recs_raw in data["tables"].items():
            table_s = str(table).strip()
            if table_s not in _WRITABLE_TABLES:
                raise ValueError(f"不允许写入表 {table_s!r}")
            recs = _coerce_records(recs_raw)
            if not recs and isinstance(recs_raw, str):
                recs = extract_balanced_json_objects(recs_raw)
                if recs:
                    parse_warnings.append(f"bundle:{table_s}:salvaged_array")
            recs = [_apply_bundle_meta(r, table=table_s, source_file=src_file, source_view=src_view) for r in recs]
            if recs:
                op = str(data.get("operation") or default_operation).strip().lower()
                batches.append((table_s, op, recs))
        return src_file, src_view, batches, parse_warnings

    table = str(data.get("table") or "").strip()
    if not table:
        raise ValueError("JSON 须含 tables{} 或 table+records")
    if table not in _WRITABLE_TABLES:
        raise ValueError(f"不允许写入表 {table!r}")
    op = str(data.get("operation") or default_operation).strip().lower()
    recs = _coerce_records(data.get("records"))
    recs = [_apply_bundle_meta(r, table=table, source_file=src_file, source_view=src_view) for r in recs]
    return src_file, src_view, [(table, op, recs)] if recs else [], parse_warnings


def _extract_balanced_array_inner(text: str, start: int) -> str:
    """从 '[' 位置提取数组内部文本（供 NDJSON records 拯救）。"""
    n = len(text)
    i = start if start >= 0 else 0
    if i < n and text[i] == "[":
        i += 1
    depth = 0
    in_string = False
    escape = False
    inner_start = i
    for j in range(i, n):
        c = text[j]
        if escape:
            escape = False
            continue
        if c == "\\" and in_string:
            escape = True
            continue
        if c == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            if depth == 0:
                return text[inner_start:j]
            depth -= 1
    return text[inner_start:]


def _sort_import_batches(
    batches: list[tuple[str, str, list[dict[str, Any]]]],
) -> list[tuple[str, str, list[dict[str, Any]]]]:
    order = {t: i for i, t in enumerate(_TABLE_IMPORT_ORDER)}

    def _key(item: tuple[str, str, list[dict[str, Any]]]) -> tuple[int, str]:
        return (order.get(item[0], 999), item[0])

    return sorted(batches, key=_key)


def _write_records_on_conn(
    conn: sqlite3.Connection,
    *,
    table: str,
    operation: str,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    op = str(operation or "upsert").strip().lower()
    inserted = updated = skipped = 0
    low_confidence_warnings: list[dict[str, Any]] = []
    errors: list[str] = []
    for i, raw in enumerate(records):
        try:
            is_insert = op == "insert" or (op == "upsert" and not _record_exists_for_table(conn, table, raw))
            norm = _prepare_write_record(conn, table=table, raw=raw, is_insert=is_insert)
            action, conf = _apply_write(conn, table=table, operation=op, record=norm)
            if action == "inserted":
                inserted += 1
            elif action == "updated":
                updated += 1
            if conf is not None and conf < 0.7:
                low_confidence_warnings.append(
                    {
                        "table": table,
                        "id": norm.get("id") or norm.get("view_id"),
                        "confidence": conf,
                        "index": i,
                    }
                )
        except Exception as row_e:
            skipped += 1
            errors.append(f"{table}[{i}]: {row_e}")
    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "low_confidence_warnings": low_confidence_warnings,
        "errors": errors,
    }


def run_import_json(*, file_path: str, operation: str = "upsert") -> dict[str, Any]:
    """从 JSON/NDJSON 文件批量 upsert PMO 表（INIT 专用，单事务）。"""
    try:
        path = _resolve_import_json_path(file_path)
        text = path.read_text(encoding="utf-8")
        src_file, src_view, batches, parse_warnings = _parse_import_batches(text, default_operation=operation)
        if not batches:
            return {
                "status": "error",
                "error": "empty_batches",
                "message": "导入文件未解析到任何 records",
                "file_path": str(path),
            }

        totals = {
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
            "low_confidence_warnings": [],
            "errors": [],
            "by_table": {},
        }
        conn = _connect()
        try:
            ensure_pmo_schema(conn)
            for table, op, recs in _sort_import_batches(batches):
                part = _write_records_on_conn(conn, table=table, operation=op, records=recs)
                totals["inserted"] += part["inserted"]
                totals["updated"] += part["updated"]
                totals["skipped"] += part["skipped"]
                totals["low_confidence_warnings"].extend(part["low_confidence_warnings"])
                totals["errors"].extend(part["errors"])
                totals["by_table"][table] = {
                    "inserted": part["inserted"],
                    "updated": part["updated"],
                    "skipped": part["skipped"],
                    "record_count": len(recs),
                }
            conn.commit()
        finally:
            conn.close()

        status = "ok" if (totals["inserted"] + totals["updated"]) > 0 else "error"
        if totals["errors"] and status == "ok":
            status = "partial"
        if parse_warnings and status == "ok":
            status = "partial"
        out: dict[str, Any] = {
            "status": status,
            "file_path": str(path),
            "source_file": src_file,
            "source_view": src_view,
            "inserted": totals["inserted"],
            "updated": totals["updated"],
            "skipped": totals["skipped"],
            "by_table": totals["by_table"],
            "low_confidence_warnings": totals["low_confidence_warnings"],
            "db_path": str(get_pmo_db_path()),
        }
        if parse_warnings:
            out["parse_warnings"] = parse_warnings[:30]
        if totals["errors"]:
            out["errors"] = totals["errors"][:50]
            if status == "error":
                out["message"] = "; ".join(totals["errors"][:5])
        return out
    except Exception as e:
        logger.warning("[pmo_import_json] %s", e, exc_info=True)
        return {"status": "error", "error": type(e).__name__, "message": str(e)}


def _load_manifest_files(manifest_path: Path) -> tuple[str, list[str]]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest 须为 JSON 对象")
    output_dir = str(data.get("output_dir") or manifest_path.parent)
    files_raw = data.get("files") or []
    names: list[str] = []
    for f in files_raw:
        if isinstance(f, str) and f.strip() and not f.startswith("00_SYNC"):
            names.append(Path(f.strip()).name)
    return output_dir, names


def run_init_gap_report(*, manifest_path: str = "") -> dict[str, Any]:
    """对照 manifest 统计各 source_file 在四张业务表中的行数，找出未入库文件。"""
    try:
        mp = Path(manifest_path.strip()).expanduser() if manifest_path.strip() else get_default_pmo_manifest_path()
        if not mp.is_absolute():
            ws = Path(os.environ.get("JACHIN_HOME") or Path.home() / ".jachin").expanduser() / "workspace"
            mp = (ws / mp).resolve()
        else:
            mp = mp.resolve()
        if not mp.is_file():
            return {
                "status": "error",
                "error": "manifest_not_found",
                "message": f"manifest 不存在: {mp}",
            }

        output_dir, md_files = _load_manifest_files(mp)
        business_tables = sorted(_DEPT_TABLES | {"pmo_personnel_task_progress"})

        conn = _connect()
        try:
            ensure_pmo_schema(conn)
            table_totals: dict[str, int] = {}
            for t in business_tables:
                row = conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()
                table_totals[t] = int(row["n"]) if row else 0

            per_file: list[dict[str, Any]] = []
            missing_files: list[str] = []
            for basename in md_files:
                counts: dict[str, int] = {}
                total = 0
                for t in business_tables:
                    row = conn.execute(
                        f"SELECT COUNT(*) AS n FROM {t} WHERE source_file = ?",
                        (basename,),
                    ).fetchone()
                    n = int(row["n"]) if row else 0
                    if n:
                        counts[t] = n
                        total += n
                entry = {
                    "source_file": basename,
                    "row_count_total": total,
                    "by_table": counts,
                    "status": "ok" if total > 0 else "missing",
                }
                per_file.append(entry)
                if total == 0:
                    missing_files.append(basename)
        finally:
            conn.close()

        return {
            "status": "ok",
            "manifest_path": str(mp),
            "output_dir": output_dir,
            "business_md_count": len(md_files),
            "table_totals": table_totals,
            "files": per_file,
            "missing_files": missing_files,
            "missing_count": len(missing_files),
            "init_complete": len(missing_files) == 0 and all(v > 0 for v in table_totals.values()),
            "db_path": str(get_pmo_db_path()),
        }
    except Exception as e:
        logger.warning("[pmo_init_gap_report] %s", e, exc_info=True)
        return {"status": "error", "error": type(e).__name__, "message": str(e)}


def dispatch_pmo_db_tool(tool_id: str, **kwargs: Any) -> dict[str, Any]:
    """供 ``core.native_tools.dispatch_native_tool`` 转发。"""
    payload = kwargs
    if isinstance(kwargs.get("input"), dict):
        payload = {**kwargs, **kwargs["input"]}

    if tool_id == "core:db_query":
        return run_db_query(
            sql=str(payload.get("sql") or payload.get("query") or ""),
            params=payload.get("params"),
            max_rows=payload.get("max_rows", 200),
        )
    if tool_id == "core:db_write":
        return run_db_write(
            table=str(payload.get("table") or ""),
            operation=str(payload.get("operation") or "upsert"),
            records=payload.get("records"),
        )
    if tool_id == "core:pmo_import_json":
        return run_import_json(
            file_path=str(payload.get("file_path") or payload.get("path") or ""),
            operation=str(payload.get("operation") or "upsert"),
        )
    if tool_id == "core:pmo_init_gap_report":
        return run_init_gap_report(
            manifest_path=str(payload.get("manifest_path") or payload.get("path") or ""),
        )
    raise ValueError(f"Unknown PMO db tool: {tool_id}")
