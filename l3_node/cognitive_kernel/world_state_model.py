"""World-state model distilled from StateFabric snapshots."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any

from .contracts import StateSnapshot
from .ledger import append_event


@dataclass(slots=True)
class WorldStateModel:
    model_id: str
    snapshot_id: str
    active_app: str = ""
    active_window_title: str = ""
    running_app_names: list[str] = field(default_factory=list)
    recent_apps: list[str] = field(default_factory=list)
    last_opened_app: str = ""
    last_user_facing_app: str = ""
    open_app_count: int = 0
    task_channel: str = ""
    voice_summary: dict[str, Any] = field(default_factory=dict)
    resource_summary: dict[str, Any] = field(default_factory=dict)
    risk_flags: list[str] = field(default_factory=list)
    freshness_ms: int = 0
    confidence: float = 0.0
    gaps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _snapshot_dict(snapshot: StateSnapshot | dict[str, Any]) -> dict[str, Any]:
    if isinstance(snapshot, StateSnapshot):
        return snapshot.to_dict()
    return dict(snapshot)


def _pick_app_name(value: dict[str, Any]) -> str:
    for key in ("app", "app_name", "name", "process", "process_name", "exe"):
        text = str(value.get(key) or "").strip()
        if text:
            return text
    return ""


def _title(value: dict[str, Any]) -> str:
    for key in ("title", "window_title", "name"):
        text = str(value.get(key) or "").strip()
        if text:
            return text
    return ""


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def build_world_state_model(snapshot: StateSnapshot | dict[str, Any], *, turn_id: str = "world_state") -> WorldStateModel:
    data = _snapshot_dict(snapshot)
    active_window = dict(data.get("active_window") or {})
    running_apps = [dict(item) for item in data.get("running_apps") or [] if isinstance(item, dict)]
    recent_events = [dict(item) for item in data.get("recent_app_events") or [] if isinstance(item, dict)]
    active_app = _pick_app_name(active_window)
    running_names = _unique([_pick_app_name(item) for item in running_apps])
    recent_names = _unique([_pick_app_name(item) for item in reversed(recent_events)])
    last_opened_app = ""
    for event in reversed(recent_events):
        event_name = str(event.get("event") or event.get("action") or "").lower()
        if event_name in {"open", "opened", "launch", "launched", "focus", "focused", "switch"}:
            last_opened_app = _pick_app_name(event)
            if last_opened_app:
                break
    last_user_facing = active_app or last_opened_app or (recent_names[0] if recent_names else "")
    risk_state = dict(data.get("risk_state") or {})
    risk_flags = [
        key
        for key, value in risk_state.items()
        if value not in (None, False, "", "ok", "low", "none", "unknown")
    ]
    freshness = int(data.get("freshness_ms") or 0)
    gaps: list[str] = []
    if not active_app:
        gaps.append("active_app_unknown")
    if not running_names:
        gaps.append("running_apps_empty")
    if freshness > 30_000:
        gaps.append("stale_snapshot")
    confidence = 0.94
    confidence -= 0.16 * len(gaps)
    confidence = max(0.1, min(0.99, confidence))
    model_id = "world_" + hashlib.sha1(str(data.get("snapshot_id", "")).encode("utf-8")).hexdigest()[:12]
    model = WorldStateModel(
        model_id=model_id,
        snapshot_id=str(data.get("snapshot_id") or ""),
        active_app=active_app,
        active_window_title=_title(active_window),
        running_app_names=running_names,
        recent_apps=recent_names[:12],
        last_opened_app=last_opened_app,
        last_user_facing_app=last_user_facing,
        open_app_count=len(running_names),
        task_channel=str(dict(data.get("task_state") or {}).get("channel") or ""),
        voice_summary=dict(data.get("voice_state") or {}),
        resource_summary=dict(data.get("resource_state") or {}),
        risk_flags=risk_flags,
        freshness_ms=freshness,
        confidence=round(confidence, 3),
        gaps=gaps,
    )
    append_event("world_state_model_built", turn_id, model.to_dict())
    return model

