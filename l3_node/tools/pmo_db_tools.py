"""
PMO-Copilot v6 — SQLite 原生工具 ``core:db_query`` / ``core:db_write``。

库文件默认 ``~/.jachin/workspace/pmo_db.sqlite``（可用 ``JACHIN_PMO_DB_PATH`` 覆盖）。
Schema SSOT：PMO skill package config and runtime database schema。
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
            "**仅允许 SELECT**（及只读 PRAGMA table_info/table_list）；写操作请 core:db_write。"
            "**pmo_raw_records 列**：id, **source_view**, source_file, row_index, raw_text, **fields(JSON)**, synced_at。"
            "**pmo_views_meta 列**：view_id, view_name, record_count, columns_json。"
            "业务字段在 fields JSON 内；**父记录** 为链接数组（几乎 never IS NULL）。"
            "tool input：**裸 SELECT SQL**（推荐）；或 JSON {\"sql\":\"...\"}。"
            "长 SQL 勿用 JSON 包装（内嵌引号易 missing_sql）。可选 max_rows（默认 200）。"
            "返回 status/rows/row_count/truncated；0 行或报错时可能含 hints。"
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
    {
        "id": "core:pmo_mirror_import",
        "label": "core:pmo_mirror_import",
        "desc": (
            "PMO v7 INIT 镜像入库：纯 Python 解析 ``pmo_lark_pull/*.md``（bullet + 平面表），"
            "写入 ``pmo_raw_records`` / ``pmo_views_meta``；**零 LLM**。"
            "JSON：可选 manifest_path（默认 ~/.jachin/workspace/pmo_lark_pull/00_SYNC_MANIFEST.json）；"
            "可选 pull_dir（覆盖 manifest output_dir）；可选 view_ids（字符串数组，仅导入指定视图）。"
            "返回 ok/total_records/views[]/files[]。"
        ),
        "params": ["manifest_path", "pull_dir", "view_ids"],
    },
    {
        "id": "core:pmo_personnel_report",
        "label": "core:pmo_personnel_report",
        "desc": (
            "PMO 人员任务矩阵（Python 解析 pmo_raw_records；SSOT vewCz1FFJi + vewpI8lyYw 合并）。"
            "JSON：可选 sprint；或 recent_window:true（近 21 天最多 3 Sprint，等同 Worker B 宿主预取）。"
            "返回 current_sprint（sd≤today）、recent_sprints[]、personnel_tasks[]（含 sprint/priority/"
            "start_date_iso/review_date_iso/acceptance_date_iso/expected_delivery_date_iso/"
            "actual_delivery_date_iso/department/task_no/progress/status）、requirement_context[]、"
            "unassigned_tasks[]、cross_week_tasks[]、by_person{}、summary、formatted_text（纯文本表，"
            "人员【姓名】+ GFM 任务表（序/任务/编号/日期 MM-DD），勿用 #### 或等宽空格表）；日期含 *_iso。"
        ),
        "params": ["sprint", "recent_window", "person_view", "cross_view"],
    },
    {
        "id": "core:pmo_sprint_epic_report",
        "label": "core:pmo_sprint_epic_report",
        "desc": (
            "PMO 单 Sprint 大需求 + 开发/产品/美术子任务（Python 解析 pmo_raw_records，SSOT vewpI8lyYw）。"
            "JSON 必填 sprint（如 2026/05/11-Sprint）；可选 department（默认 all=三者全采；可 development/product/art）、source_view。"
            "返回 epics[]（含 workflow_status、workflow_completion_pct 泳道进度）、dev_tasks[]、product_tasks[]、art_tasks[]、"
            "epic_children[]（含 department 字段）、summary{epic_count, dev/product/art_task_count, epics_with_*}；"
            "日期 ISO YYYY-MM-DD。workflow_status 见 pmo_workflow_stage（禁止待开始/进行中/已完成粗词）。"
            "近三周战报采集请用 recent_window:true（合并 C-1 窗内各 Sprint）。"
        ),
        "params": ["sprint", "department", "source_view", "recent_window"],
    },
    {
        "id": "core:pmo_resolve_sprint",
        "label": "core:pmo_resolve_sprint",
        "desc": (
            "自然语言/日期解析 Sprint 名（只读 pmo_raw_records DISTINCT Sprint）。"
            "JSON：可选 sprint（精确串）、sprint_date（YYYY-MM-DD）、label（如 5月11）、year。"
            "返回 resolved_sprint、candidates[]、ambiguous；禁止猜测唯一候选。"
        ),
        "params": ["sprint", "sprint_date", "label", "year"],
    },
    {
        "id": "core:pmo_release_epic_mapping",
        "label": "core:pmo_release_epic_mapping",
        "desc": (
            "PMO 战报 📦 版本发布需求映射（Worker D · D-TOOL）。"
            "读 Vivian 邮箱生产发版维护公告确定时间窗，在 vewpI8lyYw 镜像中筛选完成度 100% 的顶层 Epic。"
            "JSON：可选 mailbox、app_id、app_secret、page_size。"
            "返回 status、window、completed_epics[]、completed_count、markdown_section（📦 GFM 段）、"
            "release_mails_found、mailbox。"
            "禁止用 Version Goal 填写率代替本工具输出。"
        ),
        "params": ["mailbox", "app_id", "app_secret", "page_size"],
    },
    {
        "id": "core:pmo_macro_dashboard_push",
        "label": "core:pmo_macro_dashboard_push",
        "desc": (
            "PMO 宏观看板一键推送（Work 总 · 确定性路径）。"
            "内部自动 core:pmo_personnel_report + core:pmo_sprint_epic_report 宿主预取，"
            "默认 core:pmo_release_epic_mapping（Worker D）生成 📦 发版 Epic 清单，"
            "组装 Executive Summary + 📊 五列需求表 + 👥 人员表 + 📦 版本发布需求映射，"
            "polish_pmo_war_report_markdown 后以飞书 native_table 卡片发送。"
            "JSON：可选 chat_id（主群，默认 PMO_PRIMARY_CHAT_ID）；"
            "monitor_chat_id（监控群，默认 oc_0e321f92d758ecb44aea5b499c90510b）；"
            "push_monitor（默认 true，双群推送）；app_id/app_secret（空则用 atom_lark_notifier 配置）；"
            "dry_run（true 仅返回 markdown）；title（卡片标题）；"
            "use_release_epic_mapping（默认 true，启用 Worker D 📦）；"
            "release_mapping_section（可选，直接注入 Worker D markdown 段，跳过重复 D-TOOL）。"
            "返回 status、message_id(s)、current_sprint、epic_count、person_count、markdown_preview、pushes[]。"
            "Publisher 推宏观看板时 **优先** 本工具，禁止再手写 GFM 后重复 notifier。"
        ),
        "params": [
            "chat_id",
            "monitor_chat_id",
            "push_monitor",
            "app_id",
            "app_secret",
            "dry_run",
            "title",
            "use_release_epic_mapping",
            "release_mapping_section",
        ],
    },
    {
        "id": "core:pmo_macro_dashboard_preview",
        "label": "core:pmo_macro_dashboard_preview",
        "desc": (
            "PMO 宏观看板 Markdown 预览（不推送飞书）。"
            "与 push 同源 B/C 预取与 polish；返回 markdown 全文与 epic_count/person_count。"
            "JSON：可选 title。"
        ),
        "params": ["title"],
    },
    {
        "id": "core:pmo_bitable_watch_tick",
        "label": "core:pmo_bitable_watch_tick",
        "desc": (
            "PMO 多维表变更监控单次 tick（拉表 → 防抖会话 → 空闲满 idle_seconds 后汇总推送）。"
            "默认监控 tblfK9gk6vTQpJtB / vewCz1FFJi（人员任务看板），回调群见 pmo_bitable_watch.yaml。"
            "配置：~/.jachin/config/skills/pmo-copilot/pmo_bitable_watch.yaml 或 PMO_BITABLE_WATCH_* 环境变量。"
            "JSON：可选 force_finalize（true 立即结束会话并推送）；dry_run；app_id/app_secret。"
            "返回 action=baseline_initialized|session_active|waiting_debounce|session_finalized_notify。"
        ),
        "params": ["force_finalize", "dry_run", "app_id", "app_secret"],
    },
    {
        "id": "core:pmo_bitable_watch_status",
        "label": "core:pmo_bitable_watch_status",
        "desc": (
            "PMO 多维表变更监控状态（会话是否活跃、距上次变更秒数、基线记录数、最近推送时间）。"
            "无参数；只读。"
        ),
        "params": [],
    },
    {
        "id": "core:pmo_change_diff",
        "label": "core:pmo_change_diff",
        "desc": (
            "PMO 记录级 diff（created/updated/deleted + 字段 before/after）。"
            "JSON 模式 A：before_records + after_records（record_id→fields 或数组）。"
            "模式 B：webhook_payload（飞书 bitable.record.* 事件）。"
            "返回 events[]、summary{created,updated,deleted}。"
        ),
        "params": ["before_records", "after_records", "webhook_payload"],
    },
    {
        "id": "core:pmo_change_alert_analyze",
        "label": "core:pmo_change_alert_analyze",
        "desc": (
            "PMO 变更预警三轴分析（排期/人员/项目）+ 决策门 + 可选推送。"
            "参数 events[]（ChangeEvent dict）或 webhook_payload；"
            "push=true 时有问题才推 Lark；返回 change_alert_result 与 fact_pack。"
            "查数由宿主 Python 完成，禁止 Agent 自由 SQL。"
        ),
        "params": [
            "events",
            "webhook_payload",
            "view_id",
            "table_id",
            "push",
            "dry_run",
            "chat_id",
        ],
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
    r"insert|update|delete|drop|alter|create|truncate|attach|detach|"
    r"replace\s+into|"
    r"pragma\s+(?!table_info|table_list|index_list|foreign_key_list)|grant|revoke|vacuum|reindex"
    r")\b",
    re.IGNORECASE,
)

_ALLOWED_READONLY_PRAGMA_RE = re.compile(
    r"^pragma\s+(table_info|table_list|index_list|foreign_key_list)\s*\(",
    re.IGNORECASE,
)

_PMO_RAW_RECORDS_SCHEMA_HINT = (
    "pmo_raw_records: id, source_view, source_file, row_index, raw_text, fields(JSON), synced_at。"
    "过滤视图用 source_view=...（不是 view_id）。业务列在 fields 内用 json_extract。"
)

_SCHEMA_VERSION = 2

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

CREATE TABLE IF NOT EXISTS pmo_raw_records (
  id          TEXT PRIMARY KEY,
  source_view TEXT NOT NULL,
  source_file TEXT NOT NULL,
  row_index   INTEGER NOT NULL,
  raw_text    TEXT NOT NULL,
  fields      TEXT NOT NULL,
  synced_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pmo_views_meta (
  view_id      TEXT PRIMARY KEY,
  view_name    TEXT,
  file_name    TEXT,
  record_count INTEGER,
  columns_json TEXT,
  synced_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_raw_source_view ON pmo_raw_records(source_view);
CREATE INDEX IF NOT EXISTS idx_raw_synced_at   ON pmo_raw_records(synced_at);
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
    from l3_node.primitives.mcp.mcp_tools.bi.tool_bi_project_context import get_pmo_lark_pull_dir

    return (get_pmo_lark_pull_dir() / "00_SYNC_MANIFEST.json").resolve()


def pmo_mirror_db_ready() -> bool:
    """v7：镜像库是否已有 ``pmo_raw_records`` 数据。"""
    path = get_pmo_db_path()
    if not path.is_file():
        return False
    try:
        conn = _connect()
        try:
            ensure_pmo_schema(conn)
            row = conn.execute("SELECT COUNT(*) AS n FROM pmo_raw_records").fetchone()
            return bool(row and int(row["n"]) > 0)
        finally:
            conn.close()
    except Exception:
        return False


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect() -> sqlite3.Connection:
    path = get_pmo_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
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


_PRODUCT_STATUS_NESTED_RE = re.compile(
    r"json_extract\s*\(\s*json_extract\s*\([^)]*(?:需求状态|开发状态)",
    re.IGNORECASE,
)

_PRODUCT_STATUS_WRONG_HINT = (
    "⛔ 产品视图 vew8TxMcSh/vewL9Mofgd：「需求状态」「开发状态」在镜像库中是 **plain string**，"
    "须 **一次** json_extract，例如 "
    "json_extract(fields, '$.\"需求状态\"') AS demand_status。"
    "**禁止** json_extract(json_extract(fields,'$.\"需求状态\"'),'$[0].text')（整句会 malformed JSON）。"
    "开发表 vewpI8lyYw 的「状态」才用 [0].text；B-1/B-2/C-4 请 **原样复制** pmo_multi_agent_queries 任务体 SQL。"
)


def pmo_sql_has_product_status_nested_extract(sql: str) -> bool:
    """产品表「需求状态/开发状态」误用开发表 [0].text 嵌套 extract。"""
    return bool(_PRODUCT_STATUS_NESTED_RE.search(sql or ""))


_DEV_SSOT_VIEW_IDS = ("vewpI8lyYw", "vewjSEz5Xr", "vewCz1FFJi")

_DEV_VIEW_PRODUCT_FIELD_MARKERS = (
    "任务简述",
    "需求状态",
    "开发状态",
    '$."优先级"',
    "$.'优先级'",
    '$.\"优先级\"',
    '$."责任人"',
    '$.\"责任人\"',
)

_INVENTED_TASK_FIELD_MARKERS = (
    "任务标题",
    "任务ID",
    "任务类型",
    '$.\"负责人\"',
    "$.'负责人'",
    '$.\"关联需求\"',
    "预估工时",
    "实际工时",
    "开始时间",
    "截止时间",
)

_WORKER_B_SUP_INVENTED_FIELD_HINT = (
    "⛔ Worker B·B-SUP：禁止自编中文任务字段（任务标题/任务ID/负责人/关联需求等）。"
    "镜像键名为 Requirement / priority / Sprint / Person / 状态 / Progress。"
    "请 **逐字复制** WORKER_B_TASK **B-SUP**（非 Worker C C-2）。"
)

_DEV_VIEW_PRODUCT_FIELD_HINT = (
    "⛔ 开发/人员表（vewpI8lyYw / vewjSEz5Xr / vewCz1FFJi）禁止使用产品表字段名："
    "任务简述、优先级、责任人、需求状态、开发状态、Sprint[0]。"
    "须用 Requirement / priority / Person（B-3 plain；B-4 UNION；B-5 json_each）"
    "与 状态 plain string；请 **逐字复制** WORKER_B_TASK 对应 B-x SQL。"
)

_C2_SPRINT_IN_RE = re.compile(
    r"json_extract\s*\(\s*fields\s*,\s*['\"]\$\.Sprint['\"]\s*\)\s+in\s*\(",
    re.IGNORECASE,
)
_C2_REQUIREMENT_JSON_RE = re.compile(
    r"json_extract\s*\(\s*fields\s*,\s*['\"]\$\.Requirement['\"]",
    re.IGNORECASE,
)


def pmo_sql_has_product_fields_on_dev_view(sql: str) -> bool:
    """开发/人员表误用产品视图 JSON 键名（Verification evidence 全 null 或字段找错根因）。"""
    s = sql or ""
    low = s.lower()
    if not any(v.lower() in low for v in _DEV_SSOT_VIEW_IDS):
        return False
    if any(m in s for m in _DEV_VIEW_PRODUCT_FIELD_MARKERS):
        return True
    if any(m in s for m in _INVENTED_TASK_FIELD_MARKERS):
        return True
    if re.search(r'\$\.\"Sprint\"\s*\[\s*0\s*\]', s, re.I):
        return True
    if re.search(r"json_extract\s*\(\s*json_extract\s*\([^)]*责任人", s, re.I):
        return True
    return False


def pmo_sql_has_invented_chinese_task_fields(sql: str) -> bool:
    """Worker B 自编 Jira 式字段（任务标题/任务ID 等），镜像中不存在。"""
    s = sql or ""
    sl = s.lower()
    if "pmo_raw_records" not in sl and not any(v.lower() in sl for v in _DEV_SSOT_VIEW_IDS):
        return False
    return any(m in s for m in _INVENTED_TASK_FIELD_MARKERS)


def pmo_sql_is_worker_b_sup_vewp_context(sql: str) -> bool:
    """Worker B B-SUP：vewp 辅表（Sprint IN · 无 C-2 Epic WHERE · 非 json_each）。"""
    s = sql or ""
    sl = s.lower()
    if "vewpi8lyyw" not in sl:
        return False
    if "union" in sl or "json_each" in sl:
        return False
    if re.search(r"parent_|父记录|epic_name|task_level", s, re.I):
        return False
    if not _C2_SPRINT_IN_RE.search(s):
        return False
    if pmo_sql_has_c2_epic_filters(s):
        return False
    return bool(_C2_REQUIREMENT_JSON_RE.search(s)) and "limit" in sl


def pmo_sql_is_worker_b3_vewp_cross(sql: str) -> bool:
    """兼容旧 B-3（无 Sprint IN 的 vewp LIMIT 查询）。"""
    s = sql or ""
    sl = s.lower()
    if "vewpi8lyyw" not in sl:
        return False
    if "union" in sl or "json_each" in sl:
        return False
    if _C2_SPRINT_IN_RE.search(s):
        return False
    return bool(_C2_REQUIREMENT_JSON_RE.search(s)) and "limit" in sl


def pmo_sql_is_worker_b4_personnel_union(sql: str) -> bool:
    """Worker B B-4：vewCz1FFJi 人员 SSOT（UNION + plain string 分支）。"""
    sl = (sql or "").lower()
    return (
        "vewcz1ffji" in sl
        and "union" in sl
        and "typeof" in sl
        and "glob" in sl
        and "json_each" in sl
    )


def pmo_sql_has_vewcz1_personnel_without_json_each(sql: str) -> bool:
    """vewCz1FFJi 人员 SSOT 未使用 B-4 合法写法（单独 json_each 或裸 Person）。"""
    if "vewcz1ffji" not in (sql or "").lower():
        return False
    if pmo_sql_is_worker_b4_personnel_union(sql):
        return False
    sl = (sql or "").lower()
    if "typeof" in sl and "glob" in sl and "vewcz1ffji" in sl:
        return False
    if "person in charge" in (sql or "").lower() or "participant" in (sql or "").lower():
        return True
    if re.search(r'\$\.\"责任人\"', sql or ""):
        return True
    return False


def pmo_sql_is_worker_bs1_sprint_window(sql: str) -> bool:
    """Worker B B-S1 近三周 Sprint（vewCz1FFJi）。"""
    sl = (sql or "").lower()
    return (
        "vewcz1ffji" in sl
        and "sprint_date" in sl
        and "-21 days" in sl
        and "group by" in sl
    )


_VEWCZ1_JSON_EACH_HINT = (
    "⛔ vewCz1FFJi 人员 SSOT（B-4）：Person 常为 plain string（Buck/Seth），单独 json_each 会 malformed JSON。"
    "须 **逐字复制** WORKER_B_TASK **B-4**（UNION ALL：typeof+NOT GLOB 字符串分支 + json_each 数组分支），"
    "且 Sprint IN recent_sprints、任务编号 IS NOT NULL。先做 **B-S1** 取近三周 Sprint。"
)

_SPRINT_DATE_WRONG_HINT = (
    "⛔ Sprint 时间窗（C-1 / B-S1）：`YYYY/MM/DD-Sprint` 须 "
    "date(replace(substr(json_extract(fields,'$.Sprint'),1,10),'/','-'))；"
    "请逐字复制 WORKER_C_TASK **C-1** 或 WORKER_B_TASK **B-S1** SQL。"
)

_C1_SPRINT_DATE_BAD_RE = re.compile(
    r"date\s*\(\s*substr\s*\(\s*json_extract\s*\([^)]*['\"]\$\.Sprint['\"]",
    re.IGNORECASE,
)


_VEWP_NESTED_PERSON_STATUS_HINT = (
    "⛔ vewpI8lyYw（Worker C·C-2）：Person / 状态 在镜像中常为 **plain string**（含空串），"
    "禁止 json_extract(json_extract(...), '$[0].text')（整表扫描会 malformed JSON）。"
    "请 **整段逐字复制** WORKER_C_TASK **C-2**（含父记录双形态 WHERE + 任务编号 + Sprint IN）。"
)

_WORKER_C2_INCOMPLETE_HINT = (
    "⛔ Worker C·C-2：禁止用「Sprint IN + SELECT 全字段」捞全表（会把开发/产品/子任务当 Epic）。"
    "须 **整段逐字复制** WORKER_C_TASK **C-2**（父记录双形态 + 任务编号 IS NOT NULL + 排除部门占位）。"
    "子任务明细用 **C-3**；epics[] **仅** 来自 C-2，禁止把 C-3/自编查询结果写入 epics[]。"
)

_C2_EPIC_PARENT_NULL_RE = re.compile(
    r"父记录[\"']?\)\s+is\s+null"
    r"|父记录[\"']?\)\s*=\s*''"
    r"|父记录[\"']?\[0\]\.text[\"']?\)\s+is\s+null",
    re.IGNORECASE,
)
_C2_TASK_NO_WHERE_RE = re.compile(
    r"任务编号[\"'\\]*\)\s+is\s+not\s+null",
    re.IGNORECASE,
)
_DEPT_PLACEHOLDER_ROW_NAMES = frozenset({
    "开发", "美术", "产品", "测试", "平台前端", "平台后端",
    "游戏", "中台", "后台", "游戏客户端",
    # 飞书表内部门/泳道分组行（非真实子任务；无 Progress 时不应进入战报 📊 状态推断）
    "前端开发", "后端开发", "程序开发", "客户端", "服务端",
    "技术评估", "需求评审", "UI设计", "交互设计",
})


def pmo_sql_has_vewp_person_or_status_nested_extract(sql: str) -> bool:
    """vewpI8lyYw 上对 Person/状态 做 nested [0].text（易 malformed JSON）。"""
    s = sql or ""
    sl = s.lower()
    if "vewpi8lyyw" not in sl:
        return False
    if (
        pmo_sql_is_worker_b_sup_vewp_context(s)
        or pmo_sql_is_worker_b3_vewp_cross(s)
    ):
        return False
    if re.search(
        r"json_extract\s*\(\s*json_extract\s*\([^)]*person in charge/participant",
        sl,
        re.I,
    ):
        return True
    if re.search(
        r"json_extract\s*\(\s*json_extract\s*\([^)]*状态",
        s,
        re.I,
    ):
        return True
    return False


def pmo_sql_has_c2_epic_filters(sql: str) -> bool:
    """是否含 C-2 大需求三层 WHERE（父记录双形态 + 任务编号 + 部门占位排除）。"""
    s = sql or ""
    sl = s.lower()
    if "vewpi8lyyw" not in sl:
        return False
    return (
        bool(_C2_EPIC_PARENT_NULL_RE.search(s))
        and bool(_C2_TASK_NO_WHERE_RE.search(s))
        and "not in" in sl
        and re.search(r"开发|美术|产品", s) is not None
    )


def pmo_sql_missing_worker_c2_epic_filters(sql: str) -> bool:
    """近三周 Sprint IN 但缺少 C-2 大需求 WHERE（常误把全表当 Epic）。"""
    sl = (sql or "").lower()
    s = sql or ""
    if "vewpi8lyyw" not in sl:
        return False
    if (
        pmo_sql_is_worker_b_sup_vewp_context(s)
        or pmo_sql_is_worker_b3_vewp_cross(s)
    ):
        return False
    if pmo_sql_has_invented_chinese_task_fields(s):
        return False
    if "group by" in sl and "sprint_date" in sl:
        return False
    if "json_each" in sl:
        return False
    if "row_index" in sl and "order by row_index" in sl:
        return False
    if not _C2_SPRINT_IN_RE.search(s):
        return False
    if pmo_sql_has_c2_epic_filters(s):
        return False
    if _C2_REQUIREMENT_JSON_RE.search(s) or re.search(r"\bepic_name\b", sl):
        return True
    return False


def pmo_sql_is_worker_c1_sprint_window(sql: str) -> bool:
    """是否为 Worker C C-1 近三周 Sprint 聚合查询。"""
    sl = (sql or "").lower()
    return (
        "vewpi8lyyw" in sl
        and "sprint_date" in sl
        and "-21 days" in sl
        and "group by" in sl
    )


def pmo_sql_is_sprint_window_aggregate(sql: str) -> bool:
    """C-1 或 B-S1 近三周 Sprint 聚合。"""
    return pmo_sql_is_worker_c1_sprint_window(sql) or pmo_sql_is_worker_bs1_sprint_window(sql)


def pmo_sql_has_sprint_date_without_replace(sql: str) -> bool:
    """C-1/B-S1 误用 date(substr(Sprint)) 未 replace 斜杠为横杠。"""
    if not pmo_sql_is_sprint_window_aggregate(sql):
        return False
    s = sql or ""
    if "replace(" in s.lower() and "substr" in s.lower():
        return False
    return bool(_C1_SPRINT_DATE_BAD_RE.search(s))


_DB_QUERY_JSON_SQL_RE = re.compile(
    r'"sql"\s*:\s*"(.*)',
    re.DOTALL | re.IGNORECASE,
)


def parse_db_query_work_order_input(inp: str) -> dict[str, Any]:
    """
    解析 core:db_query 的 tool input。

    模型常输出 ``{"sql": "SELECT ..."}``；SQL 内未转义 ``"`` 会导致 ``json.loads`` 失败，
    进而 ``params={}`` → missing_sql。本函数在 JSON 失败时从正文回收 SELECT 语句。
    """
    s = (inp or "").strip()
    if not s:
        return {}
    if s.startswith("{"):
        try:
            o = json.loads(s)
            if isinstance(o, dict):
                sql = str(o.get("sql") or o.get("query") or "").strip()
                if sql:
                    return dict(o)
        except json.JSONDecodeError:
            pass
        m = _DB_QUERY_JSON_SQL_RE.search(s)
        if m:
            body = m.group(1)
            body = re.sub(r'"\s*\}\s*$', "", body)
            body = body.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')
            sql = body.strip()
            if sql:
                return {"sql": sql}
        sel = re.search(r"(?is)\bselect\b", s)
        if sel:
            chunk = s[sel.start() :].strip()
            chunk = re.sub(r'"\s*\}\s*$', "", chunk).strip().rstrip('"')
            if chunk:
                return {"sql": chunk}
    if re.match(r"^(?:select|pragma)\b", s, re.IGNORECASE):
        return {"sql": s}
    return {"sql": s}


def _validate_select_sql(sql: str) -> None:
    cleaned = _strip_sql_comments(sql)
    if not cleaned:
        raise ValueError("sql 不能为空")
    parts = [p.strip() for p in cleaned.split(";") if p.strip()]
    if len(parts) != 1:
        raise ValueError("仅允许单条 SELECT 语句")
    stmt = parts[0]
    if _ALLOWED_READONLY_PRAGMA_RE.match(stmt):
        if _FORBIDDEN_SQL_RE.search(stmt):
            raise ValueError("SQL 含禁止关键字或非只读 PRAGMA")
        return
    if not re.match(r"^select\b", stmt, re.IGNORECASE):
        raise ValueError("core:db_query 仅允许 SELECT 或只读 PRAGMA(table_info/table_list)")
    if _FORBIDDEN_SQL_RE.search(stmt):
        raise ValueError("SQL 含禁止关键字或非只读 PRAGMA")


def _db_query_hints(sql: str, *, message: str = "", row_count: int | None = None) -> list[str]:
    """常见 PMO 查询误区 → 可操作建议（Verification evidence hints）。"""
    hints: list[str] = []
    sl = (sql or "").lower()
    msg = (message or "").lower()
    if "no such column: view_id" in msg:
        hints.append(
            "pmo_raw_records 无 view_id 列；请用 source_view='vew…'。"
            "pmo_views_meta 才用 view_id。"
        )
    if pmo_sql_has_product_status_nested_extract(sql):
        hints.append(_PRODUCT_STATUS_WRONG_HINT)
    if pmo_sql_has_product_fields_on_dev_view(sql):
        hints.append(_DEV_VIEW_PRODUCT_FIELD_HINT)
    if pmo_sql_has_vewcz1_personnel_without_json_each(sql):
        hints.append(_VEWCZ1_JSON_EACH_HINT)
    if "latest_row" in sl and "group by" in sl and "sprint" in sl and "vewpi8lyyw" in sl:
        hints.append(
            "⛔ Worker C·C-1：禁止用 ORDER BY latest_row / MAX(row_index) 选「最近 Sprint」，"
            "会误选 2025 等历史周期。须用 date(replace(substr(Sprint,1,10),'/','-')) 降序 + "
            "sprint_date >= date('now','-21 days') LIMIT 3；见 WORKER_C_TASK C-1。"
        )
    if row_count == 0 and pmo_sql_is_sprint_window_aggregate(sql):
        if pmo_sql_has_sprint_date_without_replace(sql):
            hints.append(_SPRINT_DATE_WRONG_HINT)
        elif "replace(" not in sl:
            hints.append(_SPRINT_DATE_WRONG_HINT)
    if pmo_sql_has_sprint_date_without_replace(sql) and row_count != 0:
        hints.append(_SPRINT_DATE_WRONG_HINT)
    if "malformed json" in msg and "vewcz1ffji" in sl and "json_each" in sl:
        hints.append(_VEWCZ1_JSON_EACH_HINT)
    if "malformed json" in msg and "vewpi8lyyw" in sl:
        hints.append(_VEWP_NESTED_PERSON_STATUS_HINT)
    if "malformed json" in msg:
        hints.append(
            "⛔ malformed JSON：常见原因是对**纯字符串字段**做了二次 json_extract。"
            "产品视图 vew8TxMcSh/vewL9Mofgd 的「需求状态」「开发状态」是 plain string，"
            "须用 json_extract(fields, '$.\"需求状态\"') **直接提取**（plain string），"
            "**禁止** json_extract(json_extract(...), '$[0].text')；"
            "SQLite CASE 的 ELSE 分支仍会触发 malformed JSON，勿用 CASE 嵌套。"
        )
        if not pmo_sql_has_product_status_nested_extract(sql):
            hints.append(_PRODUCT_STATUS_WRONG_HINT)
    if row_count == 0 and "pmo_raw_records" in sl and re.search(r"\bview_id\b", sl):
        hints.append("pmo_raw_records 请改用 source_view 列过滤视图。")
    if row_count == 0 and "父记录" in sql:
        if re.search(r"\bis null\b", sl) and "[0].text" not in sql and "coalesce" not in sl:
            hints.append(
                "父记录在镜像库中常为 plain string（如「开发」），json_extract(fields, '$.$\"父记录\"') IS NULL 几乎恒为 0 行。"
                "Epic（C-2）请用 WORKER_C_TASK：父记录 NULL/空/或 [0].text NULL + 任务编号 IS NOT NULL + 排除部门占位 Requirement。"
            )
        elif "[0].text" in sql and "is null" in sl and "任务编号" not in sl and "vewpi8lyyw" in sl:
            hints.append(
                "仅用 父记录[0].text IS NULL 会漏 Epic 或误匹配。C-2 须父记录双形态（NULL/空/[0].text NULL）"
                "且 json_extract(fields, '$.$\"任务编号\"') IS NOT NULL；逐字复制 pmo_multi_agent_queries C-2 SQL。"
            )
        elif "= '[]'" in sql or "='[]'" in sql.replace(" ", ""):
            hints.append(
                "父记录字段可能是 string 或 JSON 数组；`父记录 = '[]'` 通常匹配不到行。"
                "子任务（C-3）请 COALESCE(trim(父记录), 父记录[0].text)；大需求见 C-2。"
            )
        elif "[0].text" in sql and "vewpi8lyyw" in sl and sl.count(" and ") >= 3:
            hints.append(
                "Epic/子任务查询 0 行：请逐字复制 WORKER_C_TASK 的 C-2/C-3（含 Sprint IN 与任务编号）。"
                "C-3 仍 0 且 C-2 有 Epic → 执行 C-6 按 row_index 归并 parent_epic=开发。"
            )
        elif "[0].text" in sql and "vewcz1ffji" in sl:
            hints.append(
                "❌ 视图混用：Epic 顶层筛选（父记录[0].text IS NULL）只能在 vewpI8lyYw 使用。"
                "vewCz1FFJi 是人员任务看板，任务行几乎都有父记录 text，此条件在此表恒 0 行。"
                "Step 4 请改 source_view='vewpI8lyYw'；Step 3 人员请用 json_each 明细 SQL。"
            )
    if row_count == 0 and "责任人" in sql and "vewcz1ffji" in sl:
        hints.append(
            "vewCz1FFJi 人员字段为 Person in charge/Participant（非「责任人」）；"
            "请 json_each(json_extract(fields, '$.$\"Person in charge/Participant\"')) 取 en_name。"
        )
    if (
        "person in charge" in sl or "participant" in sl or "json_each" in sl
    ) and "vewcz1ffji" in sl:
        select_part = sl.split("from", 1)[0] if "from" in sl else sl
        has_task_fields = any(
            k in select_part
            for k in ("requirement", "status", "sprint", "progress", "due", "start_date")
        )
        if "json_each" in sl and not has_task_fields and "count(" not in sl:
            hints.append(
                "Step 3 人员查询不完整：只查了 en_name，缺少 task/status/sprint/due 等列。"
                "须用 SKILL §1.2「Step 3 明细 SQL」一次 SELECT 全部字段，供节奏判定。"
            )
    if (
        "person in charge" in sl or "participant" in sl
    ) and re.search(r"\[0\]\.en_name|\$\[0\]\.en_name", sql or "") and "json_each" not in sl:
        hints.append(
            "Person in charge/Participant 须用 json_each 展开 **所有** 参与者；"
            "[0].en_name 只取第一人，多人任务会漏计。"
            "见 SKILL §1.2 Step 3 明细 SQL。"
        )
    if re.search(r'sprint"\]\[0\]|sprint"\[0\]', sql or "", re.I) or re.search(
        r'\$\.\"Sprint\"\[0\]\.text', sql or ""
    ):
        hints.append(
            "Sprint 是纯字符串字段，路径为 json_extract(fields, '$.Sprint')；"
            "禁止用 $.'Sprint'[0].text（那是对象数组写法，仅适用于「状态」字段）。"
        )
    if re.search(r"\b\w+\.json_extract\s*\(", sql or "", re.I):
        hints.append(
            "SQLite 中 json_extract 是独立函数，不能用 r1.json_extract(...) 写法。"
            "正确：json_extract(r1.fields, '$.xxx')。"
        )
    if "syntax error" in msg and re.search(r"\b\w+\.json_extract\s*\(", sql or "", re.I):
        hints.append(
            "跨视图检验建议拆成 Step 6a + 6b 两步简单查询，而非一条 JOIN。"
        )
    if re.search(r"\bjoin\b", sl) and sl.count("pmo_raw_records") >= 2:
        hints.append(
            "跨视图检验禁止一条 JOIN 完成："
            "Step 6a 从 vewpI8lyYw 取 TOP 5 延期 Requirement；"
            "Step 6b 在 vewCz1FFJi 用 fields LIKE '%需求名%' 逐条核对。"
        )
    if "vewpi8lyyw" in sl and ("负责人" in sql or "需求名称" in sql):
        hints.append(
            "vewpI8lyYw 开发表字段为 Person in charge/Participant 和 Requirement"
            "（非「负责人」「需求名称」）；请核对 Step1 的 columns_json。"
        )
    if pmo_sql_has_product_fields_on_dev_view(sql):
        hints.append(_DEV_VIEW_PRODUCT_FIELD_HINT)
    if "version goal" in sl and "limit 1" in sl and "count(" not in sl:
        hints.append(
            "Step7 未完成：Version Goal 须用 COUNT(*) 聚合统计填写率，"
            "禁止 LIMIT 1 单行样本；见 SKILL §1.2.1 Step7 SQL 模板。"
        )
    if ("状态" in sql or re.search(r"\bstatus\b", sl)) and "group by" not in sl and "count(" not in sl:
        if "pmo_raw_records" in sl and row_count and row_count > 3:
            hints.append(
                "Step5 状态分布须用 GROUP BY status_text, COUNT(*) 聚合，"
                "禁止仅返回明细行；产出须含「🔴 延期 N 条 / 🔵 按时完成 M 条」等量化结论。"
            )
    if (
        "person in charge" in sl or "participant" in sl or "en_name" in sl
    ) and "vewcz1ffji" in sl and "json_each" not in sl:
        hints.append(
            "⛔ Step3 **绝对禁忌**：未用 json_each 展开 Person in charge/Participant。"
            "直接 json_extract 返回的是 JSON 数组乱码（如 [{\"en_name\":\"…\"}]），"
            "无法做人员负荷分析；personnel_kanban 探针 **不计为完成**，推送前 PMO 审核 **不通过**。"
            "须改用 SKILL §1.2「Step 3 明细 SQL」（json_each + person/task/status/sprint 同查）。"
        )
    if not hints and row_count == 0 and "pmo_raw_records" in sl:
        hints.append(
            "0 行：请先 SELECT columns_json FROM pmo_views_meta WHERE view_id=... 核对 JSON 键名；"
            "或 SELECT fields FROM pmo_raw_records WHERE source_view=... LIMIT 1 看样本。"
        )
    hints.extend(_db_query_wide_date_range_hints(sql))
    return hints


def _db_query_wide_date_range_hints(sql: str) -> list[str]:
    """检测 Start Date / Expected Delivery Date 过滤范围是否过宽（>30 天）。"""
    from datetime import timedelta

    hints: list[str] = []
    if not sql:
        return hints
    sl = sql.lower()
    if "start date" not in sl and "expected delivery date" not in sl:
        return hints
    if re.search(r"json_extract\s*\(\s*fields\s*,\s*'\$\.sprint'\s*\)\s*=", sl):
        return hints
    for date_str in re.findall(r"'([0-9]{4}-\d{2}-\d{2})'", sql):
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            continue
        if datetime.now() - dt > timedelta(days=30):
            hints.append(
                "⚠️ 日期过滤范围过宽（>30 天），可能把历史 Sprint 数据全部纳入。"
                "建议从 Step2 样本提取当前 Sprint 名称，"
                "用 json_extract(fields,'$.Sprint') = '<当前Sprint>' 等值过滤聚焦本迭代。"
            )
            break
    return hints


def _db_query_row_quality_hints(rows: list[dict[str, Any]], sql: str) -> list[str]:
    """根据返回行检测字段名错误、json_each 未展开等质量问题。"""
    hints: list[str] = []
    if not rows:
        return hints
    sl = (sql or "").lower()
    skip_cols = frozenset({
        "source_view", "fields", "row_index", "id", "synced_at", "raw_text", "source_file",
    })
    for col in rows[0]:
        if col in skip_cols:
            continue
        non_null = 0
        json_array_like = 0
        for row in rows:
            val = row.get(col)
            if val is None:
                continue
            sval = str(val).strip()
            if not sval or sval.lower() in ("null", "none"):
                continue
            non_null += 1
            if sval.startswith("[{") and ("en_name" in sval or "avatar_url" in sval or '"text"' in sval):
                json_array_like += 1
        total = len(rows)
        if total >= 5 and non_null / total < 0.1:
            hints.append(
                f"⚠️ 字段 [{col}] 非空率极低（{non_null}/{total}），可能是字段名错误；"
                "请核对 Step1 的 columns_json 后重写 SQL。"
            )
        elif col in ("person", "owner", "person_name") and json_array_like >= max(1, total // 3):
            hints.append(
                "⛔ Step3 数据质量失败：person 列仍为 JSON 数组乱码（以 [{ 开头），"
                "说明未用 json_each 展开 en_name；personnel_kanban 探针 **不计为完成**。"
                "须重写为 SKILL §1.2 Step 3 明细 SQL（json_each）。"
            )
        elif col in ("status", "status_text") and json_array_like >= max(1, total // 3):
            hints.append(
                "状态列返回 JSON 数组（以 [{ 开头），须用 "
                "json_extract(json_extract(fields,'$.\"状态\"'),'$[0].text') 取 text。"
            )
    if "vewpi8lyyw" in sl and ("负责人" in sql or "需求名称" in sql):
        hints.append(
            "vewpI8lyYw 字段为 Person in charge/Participant / Requirement，"
            "非「负责人」「需求名称」。"
        )
    if pmo_sql_has_product_fields_on_dev_view(sql):
        hints.append(_DEV_VIEW_PRODUCT_FIELD_HINT)
    hints.extend(_db_query_vewp_epic_row_quality_hints(rows, sql))
    return hints[:5]


def _db_query_vewp_epic_row_quality_hints(
    rows: list[dict[str, Any]], sql: str
) -> list[str]:
    """vewpI8lyYw：检出「Sprint 全量捞取」误当 C-2 大需求。"""
    hints: list[str] = []
    if not rows or "vewpi8lyyw" not in (sql or "").lower():
        return hints
    s = sql or ""
    if pmo_sql_has_c2_epic_filters(s):
        req_col = None
        for c in ("epic_name", "requirement", "task"):
            if rows and c in rows[0]:
                req_col = c
                break
        if req_col:
            dept_rows = sum(
                1
                for r in rows
                if str(r.get(req_col) or "").strip() in _DEPT_PLACEHOLDER_ROW_NAMES
            )
            if dept_rows >= 2:
                hints.append(
                    "⛔ 返回行仍含部门占位词（开发/产品/美术…），不是 C-2 大需求结果。"
                    "请确认已逐字复制 WORKER_C_TASK C-2 且 WHERE 未删改。"
                )
        return hints[:2]
    if len(rows) < 10:
        return hints
    req_col = None
    for c in ("epic_name", "requirement", "task"):
        if c in rows[0]:
            req_col = c
            break
    if not req_col:
        return hints
    dept_rows = sum(
        1 for r in rows if str(r.get(req_col) or "").strip() in _DEPT_PLACEHOLDER_ROW_NAMES
    )
    if dept_rows >= 2 or len(rows) > 40:
        hints.append(_WORKER_C2_INCOMPLETE_HINT)
        hints.append(
            f"当前 {len(rows)} 行且含部门占位/子任务特征 → 这是 **C-3 级全量**，不是 epics[]。"
            "请执行 **C-2**（仅大需求，通常每 Sprint 十余条）；子任务写入 epic_children[]。"
        )
    return hints[:2]


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


def _prepend_worker_b_field_align_hints(hints: list[str] | None, sql: str) -> list[str]:
    """Worker B 相关 source_view：在 Verification evidence 顶部附带字段对齐摘要。"""
    try:
        from l3_node.pmo_worker_b_field_align import field_align_hint_for_sql

        align = field_align_hint_for_sql(sql)
        if not align:
            return list(hints or [])
        out = list(hints or [])
        if align not in out:
            out.insert(0, align)
        return out
    except Exception:
        return list(hints or [])


def run_db_query(
    *,
    sql: str = "",
    params: Any = None,
    max_rows: Any = 200,
) -> dict[str, Any]:
    """执行只读 SELECT。"""
    sql_s = str(sql or "").strip()
    if not sql_s:
        return {
            "status": "error",
            "error": "missing_sql",
            "message": (
                "sql 不能为空。tool input 请 **直接写 SELECT…; 裸 SQL**，"
                "禁止 {\"sql\":\"...\"} JSON 包装（SQL 内双引号会使 JSON 解析失败）。"
            ),
            "hints": [
                "✅ 正确：tool input 第一行即以 SELECT 开头，直至分号结束。"
                "❌ 错误：{\"sql\": \"SELECT ...\"}（易导致 missing_sql）",
            ],
        }
    try:
        _validate_select_sql(sql_s)
        if pmo_sql_has_product_status_nested_extract(sql_s):
            return {
                "status": "error",
                "error": "pmo_sql_antipattern",
                "message": (
                    "产品视图「需求状态」「开发状态」禁止 nested json_extract + [0].text；"
                    "请改用任务体 B-1/B-2/C-4 中的直接 json_extract（未执行 SQL，避免 malformed JSON）"
                ),
                "schema_hint": _PMO_RAW_RECORDS_SCHEMA_HINT,
                "hints": _prepend_worker_b_field_align_hints(
                    [_PRODUCT_STATUS_WRONG_HINT], sql_s
                ),
            }
        if pmo_sql_has_invented_chinese_task_fields(sql_s):
            return {
                "status": "error",
                "error": "pmo_sql_antipattern",
                "message": (
                    "Worker B·B-SUP：禁止自编任务标题/任务ID/负责人等字段；"
                    "请逐字复制 WORKER_B_TASK B-SUP（未执行 SQL）"
                ),
                "schema_hint": _PMO_RAW_RECORDS_SCHEMA_HINT,
                "hints": _prepend_worker_b_field_align_hints(
                    [_WORKER_B_SUP_INVENTED_FIELD_HINT], sql_s
                ),
            }
        if pmo_sql_has_product_fields_on_dev_view(sql_s):
            return {
                "status": "error",
                "error": "pmo_sql_antipattern",
                "message": (
                    "开发/人员表禁止使用产品字段名（任务简述/优先级/责任人等）；"
                    "请改用 WORKER_B_TASK B-S1/B-4/B-SUP SQL（未执行 SQL）"
                ),
                "schema_hint": _PMO_RAW_RECORDS_SCHEMA_HINT,
                "hints": _prepend_worker_b_field_align_hints(
                    [_DEV_VIEW_PRODUCT_FIELD_HINT], sql_s
                ),
            }
        if pmo_sql_has_vewcz1_personnel_without_json_each(sql_s):
            return {
                "status": "error",
                "error": "pmo_sql_antipattern",
                "message": (
                    "vewCz1FFJi 人员 SSOT 须 WORKER_B_TASK B-4（UNION：typeof+NOT GLOB + json_each 数组分支）；"
                    "请先 B-S1（未执行 SQL）"
                ),
                "schema_hint": _PMO_RAW_RECORDS_SCHEMA_HINT,
                "hints": _prepend_worker_b_field_align_hints(
                    [_VEWCZ1_JSON_EACH_HINT], sql_s
                ),
            }
        if pmo_sql_has_vewp_person_or_status_nested_extract(sql_s):
            return {
                "status": "error",
                "error": "pmo_sql_antipattern",
                "message": (
                    "vewpI8lyYw C-2：Person/状态 禁止 nested [0].text；"
                    "请整段复制 WORKER_C_TASK C-2（未执行 SQL，避免 malformed JSON）"
                ),
                "schema_hint": _PMO_RAW_RECORDS_SCHEMA_HINT,
                "hints": [_VEWP_NESTED_PERSON_STATUS_HINT],
            }
        if pmo_sql_missing_worker_c2_epic_filters(sql_s):
            return {
                "status": "error",
                "error": "pmo_sql_antipattern",
                "message": (
                    "Worker C·C-2 缺少任务编号/父记录双形态/Sprint IN；"
                    "请整段复制 WORKER_C_TASK C-2（未执行 SQL）"
                ),
                "schema_hint": _PMO_RAW_RECORDS_SCHEMA_HINT,
                "hints": [_WORKER_C2_INCOMPLETE_HINT],
            }
        if pmo_sql_has_sprint_date_without_replace(sql_s):
            return {
                "status": "error",
                "error": "pmo_sql_antipattern",
                "message": (
                    "Worker C·C-1：Sprint 日期须 replace('/','-') 再 date()；"
                    "请复制 WORKER_C_TASK C-1 SQL（未执行 SQL，避免恒 0 行）"
                ),
                "schema_hint": _PMO_RAW_RECORDS_SCHEMA_HINT,
                "hints": [_SPRINT_DATE_WRONG_HINT],
            }
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
            row_count = len(rows)
            out: dict[str, Any] = {
                "status": "ok",
                "rows": rows,
                "row_count": row_count,
                "truncated": truncated,
                "db_path": str(get_pmo_db_path()),
            }
            hints = _db_query_hints(sql_s, row_count=row_count)
            row_hints = _db_query_row_quality_hints(rows, sql_s)
            if row_hints:
                hints = (hints or []) + row_hints
            hints = _prepend_worker_b_field_align_hints(hints, sql_s)
            if hints:
                out["hints"] = hints
            return out
        finally:
            conn.close()
    except Exception as e:
        logger.warning("[pmo_db_query] %s", e)
        msg = str(e)
        err: dict[str, Any] = {
            "status": "error",
            "error": type(e).__name__,
            "message": msg,
            "schema_hint": _PMO_RAW_RECORDS_SCHEMA_HINT,
        }
        hints = _prepend_worker_b_field_align_hints(
            _db_query_hints(sql_s, message=msg), sql_s
        )
        if hints:
            err["hints"] = hints
        return err


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
    if tool_id == "core:pmo_mirror_import":
        from l3_node.tools.pmo_mirror_import import run_mirror_import

        raw_views = payload.get("view_ids")
        view_ids: list[str] | None = None
        if isinstance(raw_views, list):
            view_ids = [str(v) for v in raw_views]
        return run_mirror_import(
            manifest_path=str(payload.get("manifest_path") or payload.get("path") or ""),
            pull_dir=str(payload.get("pull_dir") or payload.get("output_dir") or ""),
            view_ids=view_ids,
        )
    if tool_id == "core:pmo_personnel_report":
        from l3_node.tools.pmo_personnel_query import run_personnel_report

        return run_personnel_report(
            sprint=str(payload.get("sprint") or "").strip() or None,
            recent_window=payload.get("recent_window") in (True, "true", "1", 1),
            person_view=str(payload.get("person_view") or "vewCz1FFJi"),
            cross_view=str(payload.get("cross_view") or "vewpI8lyYw"),
        )
    if tool_id == "core:pmo_sprint_epic_report":
        from l3_node.tools.pmo_sprint_query import (
            run_sprint_epic_report,
            run_sprint_epic_report_for_recent,
        )

        if payload.get("recent_window") in (True, "true", "1", 1):
            return run_sprint_epic_report_for_recent(
                source_view=str(payload.get("source_view") or "vewpI8lyYw"),
                department=str(payload.get("department") or "all"),
            )
        return run_sprint_epic_report(
            sprint=str(payload.get("sprint") or ""),
            source_view=str(payload.get("source_view") or "vewpI8lyYw"),
            department=str(payload.get("department") or "all"),
        )
    if tool_id == "core:pmo_resolve_sprint":
        from l3_node.tools.pmo_sprint_query import run_resolve_sprint

        year_raw = payload.get("year")
        year_i = int(year_raw) if year_raw not in (None, "") else None
        return run_resolve_sprint(
            sprint=str(payload.get("sprint") or "").strip() or None,
            sprint_date=str(payload.get("sprint_date") or "").strip() or None,
            label=str(payload.get("label") or "").strip() or None,
            year=year_i,
            source_view=str(payload.get("source_view") or "vewpI8lyYw"),
        )
    if tool_id == "core:pmo_release_epic_mapping":
        from l3_node.tools.pmo_release_epic_mapping import run_release_epic_mapping

        page_raw = payload.get("page_size")
        page_size = int(page_raw) if page_raw not in (None, "") else 20
        return run_release_epic_mapping(
            app_id=str(payload.get("app_id") or "").strip() or None,
            app_secret=str(payload.get("app_secret") or "").strip() or None,
            mailbox=str(payload.get("mailbox") or "").strip() or None,
            page_size=page_size,
        )
    if tool_id == "core:pmo_macro_dashboard_push":
        from l3_node.tools.pmo_macro_dashboard import run_macro_dashboard_push

        root_raw = payload.get("project_root")
        project_root = Path(str(root_raw)) if root_raw else None
        push_mon = payload.get("push_monitor")
        if push_mon is None:
            push_monitor = True
        else:
            push_monitor = push_mon in (True, "true", "1", 1)
        use_rel = payload.get("use_release_epic_mapping")
        if use_rel is None:
            use_release_epic_mapping = True
        else:
            use_release_epic_mapping = use_rel in (True, "true", "1", 1)
        return run_macro_dashboard_push(
            chat_id=str(payload.get("chat_id") or "").strip() or None,
            monitor_chat_id=str(payload.get("monitor_chat_id") or "").strip() or None,
            push_monitor=push_monitor,
            app_id=str(payload.get("app_id") or "").strip() or None,
            app_secret=str(payload.get("app_secret") or "").strip() or None,
            dry_run=payload.get("dry_run") in (True, "true", "1", 1),
            title=str(payload.get("title") or "").strip() or None,
            project_root=project_root,
            use_release_epic_mapping=use_release_epic_mapping,
            release_mapping_section=str(payload.get("release_mapping_section") or "").strip() or None,
        )
    if tool_id == "core:pmo_macro_dashboard_preview":
        from l3_node.tools.pmo_macro_dashboard import run_macro_dashboard_preview

        return run_macro_dashboard_preview(
            title=str(payload.get("title") or "").strip() or None,
        )
    if tool_id == "core:pmo_bitable_watch_tick":
        from l3_node.tools.pmo_bitable_watch import run_bitable_watch_tick

        ff = payload.get("force_finalize")
        return run_bitable_watch_tick(
            force_finalize=ff in (True, "true", "1", 1),
            dry_run=payload.get("dry_run") if "dry_run" in payload else None,
            app_id=str(payload.get("app_id") or "").strip() or None,
            app_secret=str(payload.get("app_secret") or "").strip() or None,
        )
    if tool_id == "core:pmo_bitable_watch_status":
        from l3_node.tools.pmo_bitable_watch import run_bitable_watch_status

        return run_bitable_watch_status()
    if tool_id == "core:pmo_change_diff":
        from l3_node.tools.pmo_bitable_watch import run_change_diff

        return run_change_diff(
            before_records=payload.get("before_records"),
            after_records=payload.get("after_records"),
            webhook_payload=payload.get("webhook_payload"),
        )
    if tool_id == "core:pmo_change_alert_analyze":
        from l3_node.tools.pmo_change_alert import run_change_alert_analyze

        ev = payload.get("events")
        events_list = ev if isinstance(ev, list) else None
        return run_change_alert_analyze(
            events=events_list,
            webhook_payload=payload.get("webhook_payload"),
            view_id=str(payload.get("view_id") or "").strip(),
            table_id=str(payload.get("table_id") or "").strip(),
            push=payload.get("push") in (True, "true", "1", 1),
            dry_run=payload.get("dry_run") in (True, "true", "1", 1),
            chat_id=str(payload.get("chat_id") or "").strip() or None,
        )
    raise ValueError(f"Unknown PMO db tool: {tool_id}")
