"""Governance-mode history index for Memory Growth.

Daily and weekly reviews produce point-in-time recommendations. This module
turns those snapshots into a compact time-series index so later governance can
answer whether auto mode is becoming safer, noisier, or stuck.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import date as Date
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .memory_growth import memory_growth_dir


INDEX_RELATIVE_PATH = Path("indexes") / "memory_governance_auto_mode_history.json"


def append_auto_governance_mode_history(
    *,
    source: str,
    date: str,
    recommendation: dict[str, Any] | None,
    auto_policy: dict[str, Any] | None = None,
    auto_result: dict[str, Any] | None = None,
    report_path: str | None = None,
) -> dict[str, Any]:
    """Append or replace a governance-mode recommendation snapshot."""

    root = memory_growth_dir()
    index_path = root / INDEX_RELATIVE_PATH
    payload = _read_json(index_path)
    history = [row for row in payload.get("history", []) if isinstance(row, dict)]
    row = _make_row(
        source=source,
        date=date,
        recommendation=recommendation or {},
        auto_policy=auto_policy or {},
        auto_result=auto_result or {},
        report_path=report_path or "",
    )
    history = [item for item in history if item.get("entry_id") != row["entry_id"]]
    history.append(row)
    history.sort(key=lambda item: (str(item.get("date") or ""), str(item.get("created_at") or ""), str(item.get("source") or "")))
    history = history[-500:]
    index = _build_index(history)
    _write_json(index_path, index)
    return {
        "index_path": str(index_path),
        "entry_id": row["entry_id"],
        "latest": index.get("latest") or row,
        "summary": index.get("summary") or {},
        "trends": index.get("trends") or {},
    }


def read_auto_governance_mode_history(root: Path | None = None) -> dict[str, Any]:
    """Read the persisted mode-history index."""

    index_path = (root or memory_growth_dir()) / INDEX_RELATIVE_PATH
    payload = _read_json(index_path)
    if not isinstance(payload, dict) or int(payload.get("schema_version") or 0) != 1:
        return _build_index([])
    payload.setdefault("history", [])
    payload.setdefault("trends", {"days_7": [], "days_14": [], "days_30": []})
    payload.setdefault("summary", {})
    return payload


def _make_row(
    *,
    source: str,
    date: str,
    recommendation: dict[str, Any],
    auto_policy: dict[str, Any],
    auto_result: dict[str, Any],
    report_path: str,
) -> dict[str, Any]:
    metrics = recommendation.get("metrics") if isinstance(recommendation.get("metrics"), dict) else {}
    skipped = auto_result.get("skipped") if isinstance(auto_result.get("skipped"), list) else []
    retry_limited = sum(1 for item in skipped if isinstance(item, dict) and item.get("reason") == "auto_retry_limit_reached")
    raw_id = "|".join(
        [
            str(source or "unknown"),
            str(date or ""),
            str(report_path or ""),
            str(recommendation.get("current_mode") or ""),
            str(recommendation.get("recommended_mode") or ""),
        ]
    )
    row = {
        "entry_id": hashlib.sha1(raw_id.encode("utf-8", errors="ignore")).hexdigest()[:16],
        "source": str(source or "unknown"),
        "date": _normalize_date(date),
        "created_at": _iso_now(),
        "current_mode": str(recommendation.get("current_mode") or auto_policy.get("mode") or ""),
        "recommended_mode": str(recommendation.get("recommended_mode") or ""),
        "should_change": bool(recommendation.get("should_change")),
        "severity": str(recommendation.get("severity") or ""),
        "reasons": [str(item) for item in (recommendation.get("reasons") or []) if item is not None][:12],
        "auto_executed_count": int(auto_result.get("executed_count") or 0),
        "auto_failed_count": int(auto_result.get("failed_count") or 0),
        "auto_skipped_count": len(skipped),
        "auto_retry_limited_count": retry_limited,
        "trust_pending_count": int(metrics.get("trust_pending_count") or 0),
        "trust_failed_count": int(metrics.get("trust_failed_count") or 0),
        "trust_next_action_count": int(metrics.get("trust_next_action_count") or 0),
        "trust_conversion_rate": float(metrics.get("trust_conversion_rate") or 0.0),
        "recent_failure_rate_14d": float(metrics.get("recent_failure_rate_14d") or 0.0),
        "governance_effectiveness_score": int(metrics.get("governance_effectiveness_score") or 0),
        "report_path": report_path,
    }
    return row


def _build_index(history: list[dict[str, Any]]) -> dict[str, Any]:
    trends = {
        "days_7": _trend_rows(history, 7),
        "days_14": _trend_rows(history, 14),
        "days_30": _trend_rows(history, 30),
    }
    summary = _summary(history)
    return {
        "schema_version": 1,
        "updated_at": _iso_now(),
        "history": history,
        "latest": history[-1] if history else {},
        "trends": trends,
        "summary": summary,
    }


def _summary(history: list[dict[str, Any]]) -> dict[str, Any]:
    recent = _recent(history, 30)
    change_count = sum(1 for row in recent if row.get("should_change"))
    safe_auto_count = sum(1 for row in recent if row.get("recommended_mode") == "safe_auto")
    manual_count = sum(1 for row in recent if row.get("recommended_mode") == "manual")
    off_count = sum(1 for row in recent if row.get("recommended_mode") == "off")
    failed = sum(int(row.get("auto_failed_count") or 0) for row in recent)
    retry_limited = sum(int(row.get("auto_retry_limited_count") or 0) for row in recent)
    trust_next_actions = sum(int(row.get("trust_next_action_count") or 0) for row in recent)
    if failed or retry_limited:
        direction = "noisy"
    elif change_count >= 3 or manual_count > safe_auto_count:
        direction = "watch"
    elif safe_auto_count and change_count == 0:
        direction = "stable"
    else:
        direction = "unknown"
    return {
        "total_records": len(history),
        "last_30_records": len(recent),
        "last_30_change_recommended": change_count,
        "last_30_safe_auto_recommended": safe_auto_count,
        "last_30_manual_recommended": manual_count,
        "last_30_off_recommended": off_count,
        "last_30_auto_failed": failed,
        "last_30_retry_limited": retry_limited,
        "last_30_trust_next_actions": trust_next_actions,
        "risk_direction": direction,
    }


def _trend_rows(history: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
    by_date: dict[str, Counter[str]] = defaultdict(Counter)
    for row in history:
        key = _normalize_date(str(row.get("date") or ""))
        by_date[key]["records"] += 1
        by_date[key]["change_recommended"] += 1 if row.get("should_change") else 0
        mode = str(row.get("recommended_mode") or "")
        if mode == "safe_auto":
            by_date[key]["safe_auto_recommended"] += 1
        elif mode == "manual":
            by_date[key]["manual_recommended"] += 1
        elif mode == "off":
            by_date[key]["off_recommended"] += 1
        by_date[key]["auto_failed"] += int(row.get("auto_failed_count") or 0)
        by_date[key]["retry_limited"] += int(row.get("auto_retry_limited_count") or 0)
        by_date[key]["trust_next_actions"] += int(row.get("trust_next_action_count") or 0)
    today = Date.today()
    rows: list[dict[str, Any]] = []
    for offset in range(days - 1, -1, -1):
        key = (today - timedelta(days=offset)).isoformat()
        counts = by_date.get(key, Counter())
        rows.append(
            {
                "date": key,
                "records": int(counts.get("records", 0)),
                "change_recommended": int(counts.get("change_recommended", 0)),
                "safe_auto_recommended": int(counts.get("safe_auto_recommended", 0)),
                "manual_recommended": int(counts.get("manual_recommended", 0)),
                "off_recommended": int(counts.get("off_recommended", 0)),
                "auto_failed": int(counts.get("auto_failed", 0)),
                "retry_limited": int(counts.get("retry_limited", 0)),
                "trust_next_actions": int(counts.get("trust_next_actions", 0)),
            }
        )
    return rows


def _recent(history: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
    since = Date.today() - timedelta(days=days - 1)
    out: list[dict[str, Any]] = []
    for row in history:
        try:
            row_date = Date.fromisoformat(str(row.get("date") or "")[:10])
        except Exception:
            continue
        if row_date >= since:
            out.append(row)
    return out


def _normalize_date(value: str | None) -> str:
    text = str(value or "").strip()
    if len(text) >= 10:
        text = text[:10]
    try:
        return Date.fromisoformat(text).isoformat()
    except Exception:
        return Date.today().isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")
