"""
PMO v7 — 飞书 Markdown 原文镜像入库（纯 Python，零 LLM）。

解析 ``atom_bi_project_context`` 落盘的 md：
- 层级 bullet 行（``· key: value``）
- GFM 平面表格（``| col | ... |``）
- 无法解析的行：``raw_text`` 保留，``fields={}``
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_VIEW_ID_IN_NAME_RE = re.compile(r"_(vew[a-zA-Z0-9]+)\.md$", re.I)
_BULLET_LINE_RE = re.compile(
    r"^\s*-\s+\*\*`(?P<rid>rec[a-zA-Z0-9]+(?:…|\.\.\.)?)`\*\*\s*(?P<rest>.*)$"
)
_KV_CHUNK_RE = re.compile(r"(?P<key>[^:·]+?)\s*:\s*(?P<val>.*?)(?=\s*·\s*|$)")
_TABLE_SEP_RE = re.compile(r"^\|\s*:?-+")


@dataclass
class ParsedRow:
    row_index: int
    raw_text: str
    fields: dict[str, str]
    record_id: str | None = None


def extract_view_id_from_filename(file_name: str) -> str:
    m = _VIEW_ID_IN_NAME_RE.search(file_name.replace("\\", "/"))
    return m.group(1) if m else ""


def extract_view_id_from_md(text: str) -> str:
    m = re.search(r"view_id[_\s]*(?:hint)?[`:\s]*[`']?(vew[a-zA-Z0-9]+)", text, re.I)
    if m:
        return m.group(1)
    m2 = re.search(r'"view_id_hint"\s*:\s*"(vew[a-zA-Z0-9]+)"', text)
    return m2.group(1) if m2 else ""


def _split_table_cells(line: str) -> list[str]:
    parts = [p.strip() for p in line.strip().strip("|").split("|")]
    return parts


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.count("|") >= 2


def _parse_bullet_fields(rest: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    chunks = [c.strip() for c in rest.split("·") if c.strip()]
    for chunk in chunks:
        if ":" not in chunk:
            continue
        key, _, val = chunk.partition(":")
        key = key.strip()
        val = val.strip()
        if key:
            fields[key] = val
    return fields


def parse_md_content(text: str) -> list[ParsedRow]:
    """按文件顺序提取可入库行（bullet + 平面表 + 兜底）。"""
    rows: list[ParsedRow] = []
    row_index = 0
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # GFM 表格块
        if _is_table_row(line) and i + 1 < len(lines) and _TABLE_SEP_RE.match(lines[i + 1].strip()):
            headers = _split_table_cells(line)
            i += 2
            while i < len(lines) and _is_table_row(lines[i]):
                cells = _split_table_cells(lines[i])
                if len(cells) < len(headers):
                    cells.extend([""] * (len(headers) - len(cells)))
                fields = {
                    headers[j]: cells[j] if j < len(cells) else ""
                    for j in range(len(headers))
                    if headers[j]
                }
                rows.append(
                    ParsedRow(
                        row_index=row_index,
                        raw_text=lines[i],
                        fields=fields,
                    )
                )
                row_index += 1
                i += 1
            continue

        # 层级 bullet（含无法解析的 bullet 兜底）
        if stripped.startswith("- "):
            bm = _BULLET_LINE_RE.match(line)
            if bm:
                rid = bm.group("rid").replace("…", "")
                rest = bm.group("rest") or ""
                fields = _parse_bullet_fields(rest)
                if rid and "record_id" not in fields:
                    fields["record_id"] = rid
                rows.append(
                    ParsedRow(
                        row_index=row_index,
                        raw_text=line,
                        fields=fields,
                        record_id=rid or None,
                    )
                )
            else:
                rows.append(
                    ParsedRow(
                        row_index=row_index,
                        raw_text=line,
                        fields={},
                    )
                )
            row_index += 1
            i += 1
            continue

        i += 1

    return rows


def _stable_row_id(source_view: str, source_file: str, row_index: int) -> str:
    ns = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")
    key = f"{source_view}|{source_file}|{row_index}"
    return str(uuid.uuid5(ns, key))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def resolve_md_files(
    *,
    manifest_path: Path | None = None,
    pull_dir: Path | None = None,
) -> tuple[Path, list[Path]]:
    """从 manifest 或 pull_dir 解析待入库 md 列表。"""
    mp = manifest_path
    if mp is None:
        from l3_node.tools.pmo_db_tools import get_default_pmo_manifest_path

        mp = get_default_pmo_manifest_path()

    md_files: list[Path] = []
    base_dir: Path

    if mp.is_file():
        data = json.loads(mp.read_text(encoding="utf-8"))
        out_dir = str(data.get("output_dir") or "").strip()
        base_dir = Path(out_dir).expanduser().resolve() if out_dir else mp.parent.resolve()
        if pull_dir is not None:
            base_dir = pull_dir.expanduser().resolve()
        for rel in data.get("files") or []:
            if not isinstance(rel, str):
                continue
            bn = Path(rel.replace("\\", "/")).name
            if not bn.endswith(".md"):
                continue
            cand = (base_dir / bn).resolve()
            if cand.is_file():
                md_files.append(cand)
            else:
                alt = (mp.parent / bn).resolve()
                if alt.is_file():
                    md_files.append(alt)
    elif pull_dir and pull_dir.is_dir():
        base_dir = pull_dir.expanduser().resolve()
        md_files = sorted(
            p for p in base_dir.glob("*.md") if p.name != "README.md"
        )
    else:
        raise FileNotFoundError(f"manifest 不存在且未指定 pull_dir: {mp}")

    if not md_files and pull_dir and pull_dir.is_dir():
        base_dir = pull_dir.expanduser().resolve()
        md_files = sorted(p for p in base_dir.glob("*_vew*.md"))

    return base_dir, md_files


def import_mirror_from_files(
    conn: Any,
    md_files: list[Path],
    *,
    synced_at: str | None = None,
) -> dict[str, Any]:
    """将 md 文件镜像写入 ``pmo_raw_records`` / ``pmo_views_meta``。"""
    synced_at = synced_at or _utc_now_iso()
    per_view: dict[str, dict[str, Any]] = {}
    total_records = 0
    file_reports: list[dict[str, Any]] = []

    for md_path in md_files:
        text = md_path.read_text(encoding="utf-8", errors="replace")
        file_name = md_path.name
        view_id = extract_view_id_from_filename(file_name) or extract_view_id_from_md(text)
        if not view_id:
            file_reports.append(
                {
                    "file": file_name,
                    "status": "skipped",
                    "reason": "no_view_id",
                }
            )
            continue

        parsed = parse_md_content(text)
        conn.execute("DELETE FROM pmo_raw_records WHERE source_view = ?", (view_id,))

        columns: set[str] = set()
        inserted = 0
        for pr in parsed:
            columns.update(pr.fields.keys())
            row_id = _stable_row_id(view_id, file_name, pr.row_index)
            conn.execute(
                """
                INSERT OR REPLACE INTO pmo_raw_records
                  (id, source_view, source_file, row_index, raw_text, fields, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id,
                    view_id,
                    file_name,
                    pr.row_index,
                    pr.raw_text,
                    json.dumps(pr.fields, ensure_ascii=False),
                    synced_at,
                ),
            )
            inserted += 1

        view_name = ""
        m_title = re.search(r'"title"\s*:\s*"([^"]+)"', text[:4000])
        if m_title:
            view_name = m_title.group(1)

        conn.execute(
            """
            INSERT OR REPLACE INTO pmo_views_meta
              (view_id, view_name, file_name, record_count, columns_json, synced_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                view_id,
                view_name,
                file_name,
                inserted,
                json.dumps(sorted(columns), ensure_ascii=False),
                synced_at,
            ),
        )

        per_view[view_id] = {
            "view_id": view_id,
            "file_name": file_name,
            "record_count": inserted,
            "columns": sorted(columns),
        }
        total_records += inserted
        file_reports.append(
            {
                "file": file_name,
                "view_id": view_id,
                "record_count": inserted,
                "status": "ok",
            }
        )

    try:
        from l3_node.pmo_worker_b_field_align import load_worker_b_field_alignment

        load_worker_b_field_alignment(force=True)
    except Exception:
        pass

    return {
        "total_records": total_records,
        "views": list(per_view.values()),
        "files": file_reports,
        "synced_at": synced_at,
    }


def run_mirror_import(
    *,
    manifest_path: str = "",
    pull_dir: str = "",
    view_ids: list[str] | None = None,
) -> dict[str, Any]:
    """``core:pmo_mirror_import`` 入口。"""
    from l3_node.tools.pmo_db_tools import ensure_pmo_schema, get_pmo_db_path

    try:
        mp: Path | None = None
        if manifest_path.strip():
            mp = Path(manifest_path).expanduser().resolve()
        pd: Path | None = None
        if pull_dir.strip():
            pd = Path(pull_dir).expanduser().resolve()

        conn_path = ensure_pmo_schema()
        base_dir, md_files = resolve_md_files(manifest_path=mp, pull_dir=pd)

        if view_ids:
            allow = {v.strip() for v in view_ids if v and str(v).strip()}
            md_files = [
                p
                for p in md_files
                if extract_view_id_from_filename(p.name) in allow
                or extract_view_id_from_md(p.read_text(encoding="utf-8", errors="replace")[:8000])
                in allow
            ]

        if not md_files:
            return {
                "status": "error",
                "error": "no_md_files",
                "message": f"未找到可入库 md（base={base_dir}）",
                "db_path": str(get_pmo_db_path()),
            }

        from l3_node.tools.pmo_db_tools import _connect

        conn = _connect()
        try:
            report = import_mirror_from_files(conn, md_files)
            conn.commit()
        finally:
            conn.close()

        return {
            "status": "ok",
            "ok": True,
            "db_path": str(get_pmo_db_path()),
            "base_dir": str(base_dir),
            "md_file_count": len(md_files),
            **report,
        }
    except Exception as e:
        return {
            "status": "error",
            "ok": False,
            "error": type(e).__name__,
            "message": str(e),
        }
