"""Project path memory used by OS mission routing.

This file intentionally stays independent from the Windows UIA implementation.
The Windows MCP layer uses the same JSON shape/path, so both sides can share
project aliases without coupling the mission router to a specific app skill.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any


def _jachin_os_data_dir() -> Path:
    base = os.environ.get("JACHIN_OS_DATA_DIR") or os.environ.get("LOCALAPPDATA") or str(Path.home() / ".jachin")
    path = Path(base).expanduser()
    if path.name.lower() not in ("jachin", ".jachin"):
        path = path / "Jachin"
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_memory_path() -> Path:
    return Path(os.environ.get("JACHIN_OS_PROJECT_MEMORY_PATH") or (_jachin_os_data_dir() / "os_project_memory.json")).expanduser()


def project_key(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "").strip().lower())


def load_project_memory() -> dict[str, Any]:
    path = project_memory_path()
    if not path.exists():
        return {"projects": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"projects": {}}
    if not isinstance(data, dict):
        return {"projects": {}}
    if not isinstance(data.get("projects"), dict):
        data["projects"] = {}
    return data


def save_project_memory(data: dict[str, Any]) -> Path:
    path = project_memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def remember_project(project_name: str, project_path: str | Path) -> dict[str, Any]:
    root = Path(project_path).expanduser().resolve()
    name = str(project_name or "").strip() or root.name
    data = load_project_memory()
    key = project_key(name)
    data.setdefault("projects", {})[key] = {
        "name": name,
        "path": str(root),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    path = save_project_memory(data)
    return {"name": name, "key": key, "path": str(root), "memory_path": str(path)}


def resolve_project(project_name: str = "") -> tuple[Path | None, dict[str, Any]]:
    data = load_project_memory()
    projects = data.get("projects") or {}
    name = str(project_name or "").strip()
    key = project_key(name)
    row = projects.get(key) if key else None
    if not row and name:
        compact = key.replace(" ", "")
        for candidate_key, candidate in projects.items():
            if compact and compact in str(candidate_key).replace(" ", ""):
                row = candidate
                key = str(candidate_key)
                break
    if not row and not name and len(projects) == 1:
        key, row = next(iter(projects.items()))
    if isinstance(row, dict):
        root = Path(str(row.get("path") or "")).expanduser().resolve()
        evidence = {"project_name": name, "memory_key": key, "memory_entry": row, "memory_path": str(project_memory_path())}
        if root.exists() and root.is_dir():
            return root, evidence
        evidence["error"] = "remembered_project_path_not_found"
        return None, evidence
    return None, {
        "project_name": name,
        "memory_path": str(project_memory_path()),
        "error": "project_path_required_first_time",
        "known_projects": list(projects.keys())[:30],
    }
