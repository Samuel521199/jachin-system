"""Project/session memory for Work Ledger.

This is intentionally small and local.  It remembers project aliases and the
latest related Work Ledger session so natural phrases such as "Jachin",
"this project", or "yesterday's task" can be resolved without asking for the
same path every day.
"""

from __future__ import annotations

import hashlib
import functools
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any


_MEMORY_LOCK = threading.RLock()


def _memory_locked(func):
    @functools.wraps(func)
    def wrapped(*args, **kwargs):
        with _MEMORY_LOCK:
            return func(*args, **kwargs)

    return wrapped


@_memory_locked
def remember_project_from_session(session: dict[str, Any]) -> dict[str, Any]:
    project_path = str(session.get("project_path") or "").strip()
    project_name = str(session.get("project_name") or "").strip()
    title = str(session.get("title") or "").strip()
    user_goal = str(session.get("user_goal") or "").strip()
    session_id = str(session.get("session_id") or "").strip()
    aliases = _infer_aliases(project_name=project_name, project_path=project_path, title=title, user_goal=user_goal)
    if not aliases and not project_path and not session_id:
        return _load_memory()
    data = _load_memory()
    projects = data.setdefault("projects", {})
    now_ms = _now_ms()
    for alias in aliases:
        key = _alias_key(alias)
        if not key:
            continue
        existing = projects.get(key) if isinstance(projects.get(key), dict) else {}
        projects[key] = {
            **existing,
            "alias": alias,
            "project_name": project_name or existing.get("project_name") or alias,
            "project_path": project_path or existing.get("project_path") or "",
            "last_session_id": session_id or existing.get("last_session_id") or "",
            "last_title": title or existing.get("last_title") or "",
            "last_user_goal": user_goal or existing.get("last_user_goal") or "",
            "updated_at_ms": now_ms,
            "confidence": 0.96 if project_path else 0.72,
            "source": "work_ledger_session",
        }
    data["recent"] = {
        "session_id": session_id,
        "title": title,
        "project_name": project_name,
        "project_path": project_path,
        "user_goal": user_goal,
        "updated_at_ms": now_ms,
    }
    _save_memory(data)
    return data


@_memory_locked
def resolve_project_reference(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    data = _load_memory()
    projects = data.get("projects") if isinstance(data.get("projects"), dict) else {}
    lowered = raw.lower()
    best: tuple[float, str, dict[str, Any]] | None = None
    for key, item in projects.items():
        if not isinstance(item, dict):
            continue
        aliases = {key, _alias_key(str(item.get("alias") or "")), _alias_key(str(item.get("project_name") or ""))}
        path_name = Path(str(item.get("project_path") or "")).name
        if path_name:
            aliases.add(_alias_key(path_name))
        score = 0.0
        for alias_key in aliases:
            if not alias_key:
                continue
            if alias_key in lowered.replace(" ", "").lower():
                score = max(score, 0.92)
            elif alias_key in lowered:
                score = max(score, 0.86)
        if score <= 0:
            continue
        updated = float(item.get("updated_at_ms") or 0)
        score += min(0.05, max(0.0, (_now_ms() - updated) / -604800000.0 + 0.05))
        if best is None or score > best[0]:
            best = (score, key, item)
    if best:
        score, key, item = best
        return {
            "ok": True,
            "alias": item.get("alias") or key,
            "project_name": item.get("project_name") or item.get("alias") or key,
            "project_path": item.get("project_path") or "",
            "session_id": item.get("last_session_id") or "",
            "confidence": round(min(1.0, score), 3),
            "reason": f"matched_project_alias:{item.get('alias') or key}",
        }
    if _looks_like_recent_project_reference(lowered):
        recent = data.get("recent") if isinstance(data.get("recent"), dict) else {}
        if recent.get("session_id") or recent.get("project_path"):
            return {
                "ok": True,
                "alias": "recent",
                "project_name": recent.get("project_name") or "",
                "project_path": recent.get("project_path") or "",
                "session_id": recent.get("session_id") or "",
                "confidence": 0.7,
                "reason": "matched_recent_project_reference",
            }
    return None


@_memory_locked
def project_memory_status() -> dict[str, Any]:
    data = _load_memory()
    projects = data.get("projects") if isinstance(data.get("projects"), dict) else {}
    source_profiles = data.get("source_profiles") if isinstance(data.get("source_profiles"), dict) else {}
    return {
        "path": str(_memory_path()),
        "project_count": len(projects),
        "source_profile_count": len(source_profiles),
        "recent": data.get("recent") if isinstance(data.get("recent"), dict) else {},
        "projects": sorted(projects.values(), key=lambda item: int(item.get("updated_at_ms") or 0), reverse=True)[:50],
    }


@_memory_locked
def get_project_source_profile(project_path: str) -> dict[str, Any] | None:
    canonical_path = _canonical_project_path(project_path)
    if not canonical_path:
        return None
    data = _load_memory()
    profiles = data.get("source_profiles") if isinstance(data.get("source_profiles"), dict) else {}
    profile = profiles.get(_project_key(canonical_path))
    if not isinstance(profile, dict):
        return None
    return {
        **profile,
        "roots": [str(item) for item in profile.get("roots") or [] if str(item).strip()],
        "source_cursors": dict(profile.get("source_cursors") or {}),
    }


@_memory_locked
def save_project_source_profile(
    project_path: str,
    roots: list[str],
    *,
    session_id: str = "",
) -> dict[str, Any]:
    canonical_path = _canonical_project_path(project_path)
    if not canonical_path:
        raise ValueError("project_path is required for project source authorization")
    normalized_roots = _normalize_roots(roots)
    data = _load_memory()
    profiles = data.setdefault("source_profiles", {})
    key = _project_key(canonical_path)
    existing = profiles.get(key) if isinstance(profiles.get(key), dict) else {}
    profile = {
        **existing,
        "project_key": key,
        "project_path": canonical_path,
        "roots": normalized_roots,
        "source_cursors": dict(existing.get("source_cursors") or {}),
        "last_session_id": str(session_id or existing.get("last_session_id") or ""),
        "authorized_at_ms": int(existing.get("authorized_at_ms") or _now_ms()),
        "updated_at_ms": _now_ms(),
        "authorization_source": "explicit_user_configuration",
    }
    profiles[key] = profile
    _save_memory(data)
    return dict(profile)


@_memory_locked
def update_project_source_profile_cursors(
    project_path: str,
    sources: dict[str, Any],
    *,
    session_id: str = "",
) -> dict[str, Any] | None:
    canonical_path = _canonical_project_path(project_path)
    if not canonical_path:
        return None
    data = _load_memory()
    profiles = data.get("source_profiles") if isinstance(data.get("source_profiles"), dict) else {}
    key = _project_key(canonical_path)
    profile = profiles.get(key)
    if not isinstance(profile, dict):
        return None
    authorized_roots = [Path(item) for item in profile.get("roots") or [] if str(item).strip()]
    checkpoints: dict[str, Any] = dict(profile.get("source_cursors") or {})
    for source_key, raw in sources.items():
        if not isinstance(raw, dict):
            continue
        source_uri = str(raw.get("source_uri") or "").strip()
        if not source_uri or source_uri.startswith("checkpoint://") or source_uri.startswith("inline://"):
            continue
        try:
            source_path = Path(source_uri).expanduser().resolve()
        except Exception:
            continue
        if not any(_path_is_within(source_path, root) for root in authorized_roots):
            continue
        checkpoints[str(source_key)] = {
            "source_key": str(source_key),
            "source_uri": str(source_path),
            "source_type": str(raw.get("source_type") or ""),
            "position": max(0, int(raw.get("position") or 0)),
            "position_chars": max(0, int(raw.get("position_chars") or 0)),
            "last_source_id": str(raw.get("last_source_id") or ""),
            "size": max(0, int(raw.get("size") or 0)),
            "mtime_ms": max(0, int(raw.get("mtime_ms") or 0)),
            "last_sync_at": str(raw.get("last_sync_at") or ""),
            "total_read_count": max(0, int(raw.get("total_read_count") or 0)),
            "total_line_count": max(0, int(raw.get("total_line_count") or 0)),
        }
    profile["source_cursors"] = checkpoints
    profile["last_session_id"] = str(session_id or profile.get("last_session_id") or "")
    profile["updated_at_ms"] = _now_ms()
    profiles[key] = profile
    data["source_profiles"] = profiles
    _save_memory(data)
    return dict(profile)


@_memory_locked
def revoke_project_source_profile(project_path: str, *, root: str = "") -> dict[str, Any]:
    canonical_path = _canonical_project_path(project_path)
    if not canonical_path:
        raise ValueError("project_path is required")
    data = _load_memory()
    profiles = data.get("source_profiles") if isinstance(data.get("source_profiles"), dict) else {}
    key = _project_key(canonical_path)
    profile = profiles.get(key)
    if not isinstance(profile, dict):
        return {"project_key": key, "project_path": canonical_path, "roots": [], "source_cursors": {}}
    if not str(root or "").strip():
        profiles.pop(key, None)
        _save_memory(data)
        return {"project_key": key, "project_path": canonical_path, "roots": [], "source_cursors": {}}
    revoked_root = Path(str(root)).expanduser().resolve()
    remaining_roots = [
        item
        for item in profile.get("roots") or []
        if Path(str(item)).expanduser().resolve() != revoked_root
    ]
    remaining_cursors = {
        source_key: cursor
        for source_key, cursor in (profile.get("source_cursors") or {}).items()
        if not _path_is_within(
            Path(str(cursor.get("source_uri") or "")).expanduser().resolve(),
            revoked_root,
        )
    }
    if remaining_roots:
        profile["roots"] = remaining_roots
        profile["source_cursors"] = remaining_cursors
        profile["updated_at_ms"] = _now_ms()
        profiles[key] = profile
    else:
        profiles.pop(key, None)
    _save_memory(data)
    return dict(profile) if remaining_roots else {
        "project_key": key,
        "project_path": canonical_path,
        "roots": [],
        "source_cursors": {},
    }


def _infer_aliases(*, project_name: str, project_path: str, title: str, user_goal: str) -> list[str]:
    aliases: list[str] = []
    for value in (project_name, Path(project_path).name if project_path else "", title, user_goal):
        aliases.extend(_alias_candidates(value))
    return _dedupe_aliases(aliases)


def _alias_candidates(value: str) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    candidates: list[str] = []
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,48}", text):
        if token.lower() in {"work", "ledger", "codex", "cursor", "todo", "fixme"}:
            continue
        candidates.append(token)
    chinese_chunks = re.findall(r"[\u4e00-\u9fffA-Za-z0-9_-]{2,32}", text)
    for chunk in chinese_chunks:
        if any(stop in chunk for stop in ("开始", "记录", "今天", "开发", "工作", "任务", "项目路径")):
            continue
        candidates.append(chunk)
    return candidates


def _dedupe_aliases(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        clean = str(value or "").strip(" ：:，。,.")
        key = _alias_key(clean)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return out[:12]


def _alias_key(value: str) -> str:
    return re.sub(r"[\s_\\/:.-]+", "", str(value or "").strip().lower())


def _looks_like_recent_project_reference(text: str) -> bool:
    return any(
        cue in text
        for cue in (
            "这个项目",
            "这项目",
            "上次任务",
            "昨天任务",
            "昨天那个",
            "上次那个",
            "继续这个",
            "接着这个",
        )
    )


def _load_memory() -> dict[str, Any]:
    path = _memory_path()
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                data.setdefault("schema_version", 1)
                data.setdefault("projects", {})
                data.setdefault("source_profiles", {})
                return data
    except Exception:
        pass
    return {"schema_version": 1, "projects": {}, "source_profiles": {}, "recent": {}}


def _canonical_project_path(project_path: str) -> str:
    raw = str(project_path or "").strip()
    if not raw:
        return ""
    try:
        return str(Path(raw).expanduser().resolve())
    except Exception:
        return raw


def _project_key(project_path: str) -> str:
    normalized = _canonical_project_path(project_path).lower()
    return f"project_{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:20]}"


def _normalize_roots(roots: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in roots:
        value = str(raw or "").strip()
        if not value:
            continue
        try:
            path = str(Path(value).expanduser().resolve())
        except Exception:
            path = value
        key = path.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(path)
    return normalized


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _save_memory(data: dict[str, Any]) -> None:
    path = _memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(temp_path, path)


def _memory_path() -> Path:
    from l3_node.work_ledger import work_ledger_home

    return work_ledger_home() / "project_memory.json"


def _now_ms() -> int:
    return int(time.time() * 1000)
