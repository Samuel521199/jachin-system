"""Durable entity-correction memory for the Cognitive Kernel.

When the user confirms an ASR/entity correction such as ``lock -> Lark``, the
kernel records it here so future turns can resolve the same or very similar
surface form without asking again.
"""

from __future__ import annotations

import json
import re
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .contracts import MemoryWriteRequest
from .ledger import append_event
from .memory_lifecycle import record_lifecycle_memory_feedback, write_lifecycle_memory
from .paths import state_dir


_APP_CORRECTION_SIMILARITY_THRESHOLD = 0.88
_APP_CORRECTION_LEARNED_SIMILARITY_THRESHOLD = 0.84


def normalize_entity_surface(value: str) -> str:
    text = str(value or "").strip().lower()
    return "".join(ch for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def get_learned_app_correction(surface: str) -> dict[str, Any]:
    normalized = normalize_entity_surface(surface)
    if len(normalized) < 3:
        return {}
    records = _read_store().get("app_corrections") or []
    best: dict[str, Any] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        record_surface = str(record.get("surface_norm") or "").strip()
        app_name = str(record.get("app_name") or "").strip()
        if not record_surface or not app_name:
            continue
        score = 1.0 if normalized == record_surface else SequenceMatcher(None, normalized, record_surface).ratio()
        if score > float(best.get("score") or 0.0):
            best = {
                "name": app_name,
                "alias": record.get("candidate_alias") or app_name,
                "heard_as": surface,
                "surface_norm": record_surface,
                "score": score,
                "source": "learned_entity_correction",
                "requires_confirmation": bool(record.get("review_required") or False),
                "learned_from": record.get("surface") or record_surface,
                "confirmation_count": int(record.get("confirmation_count") or 1),
                "memory_id": record.get("memory_id") or "",
                "success_count": int(record.get("success_count") or 0),
                "failure_count": int(record.get("failure_count") or 0),
                "review_required": bool(record.get("review_required") or False),
            }
    threshold = _APP_CORRECTION_SIMILARITY_THRESHOLD
    if best and best.get("review_required") is False and int(best.get("success_count") or 0) >= 1:
        threshold = _APP_CORRECTION_LEARNED_SIMILARITY_THRESHOLD
    if best and float(best.get("score") or 0.0) >= threshold:
        return best
    return {}


def record_confirmed_entity_correction_from_work_order(*, work_order: Any, turn_id: str = "") -> bool:
    target = {}
    try:
        target = dict((getattr(work_order, "inputs", {}) or {}).get("target") or {})
    except Exception:
        target = {}
    if not target or not target.get("requires_entity_confirmation"):
        return False
    heard_as = str(target.get("heard_as") or "").strip()
    app_name = str(target.get("name") or target.get("app") or "").strip()
    if not heard_as or not app_name:
        return False
    surface_norm = normalize_entity_surface(heard_as)
    if len(surface_norm) < 3:
        return False
    store = _read_store()
    records = [x for x in store.get("app_corrections") or [] if isinstance(x, dict)]
    now_ms = int(time.time() * 1000)
    updated = False
    memory_content = entity_correction_memory_content(surface_norm, app_name)
    for record in records:
        if record.get("surface_norm") == surface_norm and record.get("app_name") == app_name:
            record["confirmation_count"] = int(record.get("confirmation_count") or 0) + 1
            record["last_confirmed_at_ms"] = now_ms
            record["candidate_alias"] = str(target.get("candidate_alias") or record.get("candidate_alias") or app_name)
            record["success_count"] = int(record.get("success_count") or 0) + 1
            record["last_verified_at_ms"] = now_ms
            record["confidence"] = min(0.99, max(float(record.get("confidence") or 0.0), 0.84) + 0.02)
            record["review_required"] = False
            record["review_reason"] = ""
            updated = True
            break
    if not updated:
        records.append(
            {
                "surface": heard_as,
                "surface_norm": surface_norm,
                "app_name": app_name,
                "candidate_alias": str(target.get("candidate_alias") or app_name),
                "entity_score": float(target.get("entity_score") or 0.0),
                "confirmation_count": 1,
                "hit_count": 0,
                "success_count": 1,
                "failure_count": 0,
                "confidence": 0.86,
                "review_required": False,
                "review_reason": "",
                "first_confirmed_at_ms": now_ms,
                "last_confirmed_at_ms": now_ms,
                "last_verified_at_ms": now_ms,
            }
        )
    store["app_corrections"] = records
    _write_store(store)
    try:
        from l3_node.voice_entity_correction import teach_alias

        teach_alias("app", app_name, heard_as, source="entity_correction_confirmed")
    except Exception:
        pass
    memory_record = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id=turn_id or "entity-correction",
            source_event="entity_correction_confirmed",
            memory_type="correction",
            content=memory_content,
            confidence=0.86,
            ttl="permanent",
            evidence=[
                {
                    "ok": True,
                    "entity_type": "app",
                    "surface": heard_as,
                    "surface_norm": surface_norm,
                    "target": app_name,
                    "source": "entity_correction_confirmed",
                    "turn_id": turn_id,
                }
            ],
        )
    )
    _attach_lifecycle_memory_id(surface_norm=surface_norm, app_name=app_name, memory_id=memory_record.memory_id)
    append_event(
        "entity_correction_learned",
        turn_id,
        {
            "entity_type": "app",
            "surface": heard_as,
            "surface_norm": surface_norm,
            "target": app_name,
            "candidate_alias": str(target.get("candidate_alias") or app_name),
        },
    )
    return True


def record_confirmed_entity_correction_from_input_context(
    *,
    work_order: Any,
    turn_id: str = "",
    source: str = "voice_low_confidence_confirmation",
) -> bool:
    """Learn a voice app alias when the user confirmed raw->normalized text.

    Low-confidence voice clarification can be triggered by the generic voice
    confidence gate instead of an entity-correction WorkOrder. In that case the
    target app is already resolved, but the WorkOrder target does not carry the
    explicit ``requires_entity_confirmation`` marker. This helper learns the
    raw spoken surface anyway after the confirmed action verifies successfully.
    """

    target = _work_order_target(work_order)
    input_context = {}
    try:
        input_context = dict((getattr(work_order, "inputs", {}) or {}).get("input_context") or {})
    except Exception:
        input_context = {}
    raw_text = str(input_context.get("raw_text") or "").strip()
    normalized_text = str(input_context.get("normalized_text") or "").strip()
    if not raw_text or not normalized_text or raw_text == normalized_text:
        return False
    app_name = str(target.get("name") or target.get("app") or "").strip()
    if not app_name:
        return False
    heard_as = _extract_spoken_app_surface(raw_text)
    if not heard_as:
        return False
    surface_norm = normalize_entity_surface(heard_as)
    app_norm = normalize_entity_surface(app_name)
    if len(surface_norm) < 3 or surface_norm == app_norm:
        return False
    fake_work_order = _WorkOrderLike(
        {
            "target": {
                "requires_entity_confirmation": True,
                "heard_as": heard_as,
                "name": app_name,
                "candidate_alias": app_name,
                "entity_score": 0.82,
                "source": source,
            }
        }
    )
    return record_confirmed_entity_correction_from_work_order(work_order=fake_work_order, turn_id=turn_id)


def record_entity_correction_usage_from_work_order(
    *,
    work_order: Any,
    turn_id: str = "",
    ok: bool,
    failure_reason: str = "",
) -> bool:
    target = _work_order_target(work_order)
    source = str(target.get("source") or "").strip()
    if source not in {"learned_entity_correction", "entity_correction_candidate"}:
        return False
    heard_as = str(target.get("heard_as") or target.get("learned_from") or "").strip()
    app_name = str(target.get("name") or target.get("app") or "").strip()
    surface_norm = str(target.get("surface_norm") or normalize_entity_surface(heard_as)).strip()
    if not surface_norm or not app_name:
        return False
    store = _read_store()
    records = [x for x in store.get("app_corrections") or [] if isinstance(x, dict)]
    now_ms = int(time.time() * 1000)
    changed = False
    for record in records:
        if record.get("surface_norm") == surface_norm and record.get("app_name") == app_name:
            record["hit_count"] = int(record.get("hit_count") or 0) + 1
            record["last_used_at_ms"] = now_ms
            if ok:
                record["success_count"] = int(record.get("success_count") or 0) + 1
                record["last_verified_at_ms"] = now_ms
                record["confidence"] = min(0.99, max(float(record.get("confidence") or 0.0), 0.84) + 0.02)
                if int(record.get("failure_count") or 0) <= 0:
                    record["review_required"] = False
                    record["review_reason"] = ""
            else:
                record["failure_count"] = int(record.get("failure_count") or 0) + 1
                record["confidence"] = max(0.1, float(record.get("confidence") or 0.8) - 0.15)
                if int(record.get("failure_count") or 0) >= 2 or int(record.get("failure_count") or 0) > int(record.get("success_count") or 0):
                    record["review_required"] = True
                    record["review_reason"] = failure_reason or "entity_correction_execution_failed"
            changed = True
            break
    if changed:
        store["app_corrections"] = records
        _write_store(store)
    record_lifecycle_memory_feedback(
        memory_type="correction",
        content=entity_correction_memory_content(surface_norm, app_name),
        ok=ok,
        turn_id=turn_id or "entity-correction",
        failure_reason=failure_reason or "entity_correction_execution_failed",
    )
    append_event(
        "entity_correction_usage_feedback",
        turn_id,
        {
            "entity_type": "app",
            "surface_norm": surface_norm,
            "target": app_name,
            "ok": bool(ok),
            "failure_reason": failure_reason,
        },
    )
    return changed


class _WorkOrderLike:
    def __init__(self, inputs: dict[str, Any]) -> None:
        self.inputs = inputs


def _extract_spoken_app_surface(text: str) -> str:
    raw = str(text or "").strip()
    patterns = [
        r"(?:打开|启动|运行|切换到|关闭|退出)\s*([A-Za-z][A-Za-z\s._-]{1,32}|[\u4e00-\u9fffA-Za-z\s._-]{1,32})(?=\s*(?:$|给|向|发送|发|说|消息|内容))",
        r"\b(?:open|launch|start|switch\s+to|close|quit)\s+([A-Za-z][A-Za-z\s._-]{1,32})(?=\s*(?:$|to|send|message|content))",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw, re.I)
        if match:
            return str(match.group(1) or "").strip(" ,，。._-")
    return ""


def entity_correction_memory_content(surface_norm: str, app_name: str) -> str:
    return json.dumps(
        {
            "type": "app_entity_correction",
            "surface_norm": normalize_entity_surface(surface_norm),
            "target_app": str(app_name or "").strip(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _work_order_target(work_order: Any) -> dict[str, Any]:
    try:
        return dict((getattr(work_order, "inputs", {}) or {}).get("target") or {})
    except Exception:
        return {}


def _attach_lifecycle_memory_id(*, surface_norm: str, app_name: str, memory_id: str) -> None:
    if not memory_id:
        return
    store = _read_store()
    records = [x for x in store.get("app_corrections") or [] if isinstance(x, dict)]
    changed = False
    for record in records:
        if record.get("surface_norm") == surface_norm and record.get("app_name") == app_name:
            record["memory_id"] = memory_id
            changed = True
            break
    if changed:
        store["app_corrections"] = records
        _write_store(store)


def _store_path() -> Path:
    return state_dir() / "entity_corrections.json"


def _read_store() -> dict[str, Any]:
    path = _store_path()
    if not path.exists():
        return {"schema_version": 1, "app_corrections": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"schema_version": 1, "app_corrections": []}
    except Exception:
        return {"schema_version": 1, "app_corrections": []}


def _write_store(data: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data["schema_version"] = 1
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
