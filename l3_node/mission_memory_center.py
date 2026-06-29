"""Unified memory helpers for OS missions.

Project path memory already exists for the Windows MCP layer.  This module
wraps it with a broader mission memory model: projects, recipient aliases,
known recipients, template usage, and lightweight preferences.  It is still a
local JSON store and remains independent from any concrete app automation.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from l3_node.mission_intent_schema import MissionIntent, MissionTaskType
from l3_node.project_memory import project_key, project_memory_path, remember_project, resolve_project


def mission_memory_path() -> Path:
    explicit = os.environ.get("JACHIN_OS_MISSION_MEMORY_PATH")
    if explicit:
        return Path(explicit).expanduser()
    project_path = project_memory_path()
    return project_path.with_name("os_mission_memory.json")


def _empty_memory() -> dict[str, Any]:
    return {
        "version": 1,
        "projects": {},
        "recipients": {},
        "recipient_aliases": {},
        "templates": {},
        "preferences": {
            "default_since_days": 3,
            "project_briefing_format": "bullet_points",
            "preview_before_high_risk": True,
        },
    }


def load_mission_memory() -> dict[str, Any]:
    path = mission_memory_path()
    if not path.exists():
        return _empty_memory()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_memory()
    if not isinstance(data, dict):
        return _empty_memory()
    base = _empty_memory()
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key].update(value)
        else:
            base[key] = value
    return base


def save_mission_memory(data: dict[str, Any]) -> Path:
    path = mission_memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data["version"] = 1
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def remember_recipient_alias(alias: str, canonical: str, kind: str = "unknown") -> dict[str, Any]:
    data = load_mission_memory()
    key = str(alias or "").strip().lower()
    target = str(canonical or "").strip()
    if not key or not target:
        raise ValueError("alias and canonical recipient are required")
    data.setdefault("recipient_aliases", {})[key] = {
        "alias": alias,
        "canonical": target,
        "kind": kind,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    path = save_mission_memory(data)
    return {"alias": alias, "canonical": target, "kind": kind, "memory_path": str(path)}


def _remember_recipients(recipients: list[str]) -> None:
    if not recipients:
        return
    data = load_mission_memory()
    rows = data.setdefault("recipients", {})
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    changed = False
    for recipient in recipients:
        name = str(recipient or "").strip()
        if not name:
            continue
        key = name.lower()
        row = rows.get(key) if isinstance(rows.get(key), dict) else {}
        rows[key] = {
            "name": name,
            "kind": row.get("kind") or "unknown",
            "last_used_at": now,
            "use_count": int(row.get("use_count") or 0) + 1,
        }
        changed = True
    if changed:
        save_mission_memory(data)


def resolve_recipient_aliases(recipients: list[str]) -> tuple[list[str], list[dict[str, Any]]]:
    data = load_mission_memory()
    aliases = data.get("recipient_aliases") if isinstance(data.get("recipient_aliases"), dict) else {}
    resolved: list[str] = []
    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for recipient in recipients:
        raw = str(recipient or "").strip()
        if not raw:
            continue
        row = aliases.get(raw.lower())
        target = str(row.get("canonical") or raw).strip() if isinstance(row, dict) else raw
        key = target.lower()
        if key in seen:
            continue
        seen.add(key)
        resolved.append(target)
        if isinstance(row, dict) and target != raw:
            evidence.append({"alias": raw, "canonical": target, "kind": row.get("kind") or "unknown"})
    return resolved, evidence


def apply_memory_to_intent(intent: MissionIntent) -> dict[str, Any]:
    """Mutate intent with remembered slots and return evidence.

    The function is intentionally conservative: it only fills slots that are
    missing or resolves recipient aliases.  It does not override explicit user
    values.
    """
    evidence: dict[str, Any] = {
        "memory_path": str(mission_memory_path()),
        "project_memory_path": str(project_memory_path()),
        "project_hit": False,
        "recipient_alias_hits": [],
        "preferences_applied": [],
    }

    if intent.task_type == MissionTaskType.PROJECT_BRIEFING_DELIVERY and not intent.slots.project_path:
        root, project_evidence = resolve_project(intent.slots.project_name)
        evidence["project_resolution"] = project_evidence
        if root:
            intent.slots.project_path = str(root)
            if not intent.slots.project_name:
                entry = project_evidence.get("memory_entry") if isinstance(project_evidence.get("memory_entry"), dict) else {}
                intent.slots.project_name = str(entry.get("name") or root.name)
            intent.missing_slots = [slot for slot in intent.missing_slots if slot != "project"]
            intent.confidence = min(0.98, intent.confidence + 0.08)
            intent.reasoning.append(f"project_memory_hit:{project_evidence.get('memory_key') or intent.slots.project_name}")
            evidence["project_hit"] = True
        else:
            intent.reasoning.append(f"project_memory_miss:{project_evidence.get('error') or 'not_found'}")

    if intent.slots.recipients:
        recipients, alias_hits = resolve_recipient_aliases(intent.slots.recipients)
        if recipients != intent.slots.recipients:
            intent.reasoning.append("recipient_alias_resolved")
        intent.slots.recipients = recipients
        evidence["recipient_alias_hits"] = alias_hits

    data = load_mission_memory()
    prefs = data.get("preferences") if isinstance(data.get("preferences"), dict) else {}
    if intent.task_type == MissionTaskType.PROJECT_BRIEFING_DELIVERY:
        if not intent.slots.output_format and prefs.get("project_briefing_format"):
            intent.slots.output_format = str(prefs.get("project_briefing_format"))
            evidence["preferences_applied"].append("project_briefing_format")
        if not intent.slots.since_days and prefs.get("default_since_days"):
            intent.slots.since_days = int(prefs.get("default_since_days") or 3)
            evidence["preferences_applied"].append("default_since_days")

    return evidence


def record_successful_mission(intent: MissionIntent, template_id: str = "") -> dict[str, Any]:
    data = load_mission_memory()
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    if intent.slots.project_path and intent.slots.project_name:
        try:
            project_row = remember_project(intent.slots.project_name, intent.slots.project_path)
            data.setdefault("projects", {})[project_key(intent.slots.project_name)] = {
                "name": project_row["name"],
                "path": project_row["path"],
                "updated_at": now,
            }
        except Exception:
            pass

    if intent.slots.recipients:
        _remember_recipients(intent.slots.recipients)
        fresh = load_mission_memory()
        data["recipients"] = fresh.get("recipients", data.get("recipients", {}))

    if template_id:
        rows = data.setdefault("templates", {})
        row = rows.get(template_id) if isinstance(rows.get(template_id), dict) else {}
        rows[template_id] = {
            "id": template_id,
            "last_used_at": now,
            "use_count": int(row.get("use_count") or 0) + 1,
        }
    path = save_mission_memory(data)
    return {"memory_path": str(path), "template_id": template_id, "updated_at": now}
