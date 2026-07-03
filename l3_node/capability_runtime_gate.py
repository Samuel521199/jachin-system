from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable


READY_STATUSES = {"installed", "local_only", "update_available"}


def capability_available(
    *,
    ids: Iterable[str] = (),
    prefixes: Iterable[str] = (),
    name_includes: Iterable[str] = (),
    dev_env: str | None = None,
) -> bool:
    """Return whether an optional capability is installed and enabled locally.

    This is the Python-side counterpart of the desktop sidebar/install-center
    gate. Packaged L3 must only expose business capabilities that are present in
    ``~/.jachin/capabilities/installed.json`` or the cache directories merged
    into that registry. Development can opt in explicitly with ``dev_env``.
    """

    if dev_env and _truthy(os.environ.get(dev_env)):
        return True

    wanted_ids = {str(x).strip().lower() for x in ids if str(x).strip()}
    wanted_prefixes = [str(x).strip().lower() for x in prefixes if str(x).strip()]
    wanted_names = [str(x).strip().lower() for x in name_includes if str(x).strip()]
    if not wanted_ids and not wanted_prefixes and not wanted_names:
        return False

    for item in _installed_items():
        if not bool(item.get("enabled", True)):
            continue
        status = str(item.get("status") or "installed").strip().lower()
        if status and status not in READY_STATUSES:
            continue
        item_id = str(item.get("id") or "").strip().lower()
        item_name = str(item.get("name") or "").strip().lower()
        if item_id in wanted_ids:
            return True
        if any(item_id.startswith(prefix) for prefix in wanted_prefixes):
            return True
        if any(needle in item_name for needle in wanted_names):
            return True
    return False


def _installed_items() -> list[dict[str, Any]]:
    registry = _read_registry()
    packages = registry.get("packages")
    if not isinstance(packages, dict):
        packages = {}
    out: list[dict[str, Any]] = []
    for raw in packages.values():
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        path = Path(str(item.get("installed_path") or ""))
        if path and not path.is_dir():
            item["status"] = "repair_needed"
        else:
            item["status"] = "installed"
        out.append(item)
    seen = {str(item.get("id") or "").strip() for item in out}
    for item in _disk_cache_items():
        item_id = str(item.get("id") or "").strip()
        if item_id and item_id not in seen:
            out.append(item)
            seen.add(item_id)
    return out


def _disk_cache_items() -> list[dict[str, Any]]:
    home = _jachin_home_dir()
    roots = (
        (home / "l3_skill_cache", "skill"),
        (home / "l3_mcp_cache", "mcp"),
        (home / "models", "model"),
        (home / "skills", "skill"),
    )
    out: list[dict[str, Any]] = []
    for base, kind in roots:
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if not child.is_dir():
                continue
            package_id = _package_id_from_dir(child) or child.name
            out.append(
                {
                    "id": package_id,
                    "name": package_id,
                    "kind": kind,
                    "source": "local_cache",
                    "installed_path": str(child),
                    "enabled": True,
                    "status": "installed",
                }
            )
    return out


def _package_id_from_dir(path: Path) -> str | None:
    for name in (".jachin-package.json", "plugin.json"):
        p = path / name
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for key in ("id", "plugin_id", "name"):
            value = str(data.get(key) or "").strip()
            if value:
                return value
    return None


def _read_registry() -> dict[str, Any]:
    path = _installed_registry_path()
    if not path.is_file():
        return {"packages": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {"packages": {}}
    except Exception:
        return {"packages": {}}


def _installed_registry_path() -> Path:
    return _jachin_home_dir() / "capabilities" / "installed.json"


def _jachin_home_dir() -> Path:
    raw = os.environ.get("JACHIN_HOME")
    if raw:
        return Path(raw)
    if os.name == "nt":
        return Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".jachin"
    return Path(os.environ.get("HOME", str(Path.home()))) / ".jachin"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}
