"""
HR 招聘流程审计：追加 JSONL 事件，供对话 prompt 注入最近若干条 + 外部日志分析。
路径：~/.jachin/memory/hr_recruitment_audit.jsonl
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_JACHIN_ROOT = Path.home() / ".jachin"
_MEMORY_DIR = _JACHIN_ROOT / "memory"
_AUDIT_FILE = _MEMORY_DIR / "hr_recruitment_audit.jsonl"
_MAX_BYTES = 2_000_000
_MAX_TAIL_LINES = 200


def append_hr_recruitment_audit_event(
    event_type: str,
    detail: dict | None = None,
    *,
    job_folder: str = "",
    job_name: str = "",
) -> None:
    """写入一行 JSON 审计事件（失败静默）。"""
    try:
        _MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        if _AUDIT_FILE.exists() and _AUDIT_FILE.stat().st_size > _MAX_BYTES:
            bak = _AUDIT_FILE.with_suffix(".jsonl.bak")
            try:
                if bak.exists():
                    bak.unlink()
                _AUDIT_FILE.rename(bak)
            except OSError:
                pass
        rec = {
            "ts": time.time(),
            "event": (event_type or "unknown").strip(),
            "job_folder": (job_folder or "").strip(),
            "job_name": (job_name or "").strip(),
            "detail": detail if isinstance(detail, dict) else {},
        }
        with _AUDIT_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug("[HRAudit] append 失败: %s", e)


def read_hr_recruitment_audit_tail(n: int = 12) -> list[dict]:
    """读取文件末尾最多 n 条解析后的记录（用于 prompt）。"""
    n = max(1, min(40, int(n)))
    if not _AUDIT_FILE.exists():
        return []
    try:
        lines = _AUDIT_FILE.read_text(encoding="utf-8").splitlines()
        tail = lines[-_MAX_TAIL_LINES:]
        out: list[dict] = []
        for line in tail[-n:]:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    out.append(obj)
            except json.JSONDecodeError:
                continue
        return out
    except Exception as e:
        logger.debug("[HRAudit] read_tail 失败: %s", e)
        return []


def format_hr_recruitment_audit_for_prompt(n: int = 12) -> str:
    rows = read_hr_recruitment_audit_tail(n)
    if not rows:
        return ""
    lines = ["【招聘流程最近事件（审计 JSONL 摘要）】"]
    for r in rows:
        ev = str(r.get("event") or "")
        jf = str(r.get("job_folder") or "")
        jn = str(r.get("job_name") or "")
        detail = r.get("detail") or {}
        brief = json.dumps(detail, ensure_ascii=False)[:180] if detail else ""
        who = jn or jf or "—"
        lines.append(f"- {ev} | {who} | {brief}")
    return "\n".join(lines) + "\n"
