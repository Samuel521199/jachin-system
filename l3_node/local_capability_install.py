from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from l3_node.cognitive_kernel.capability_contract_validator import (
    contract_error_messages,
    contract_warning_messages,
    validate_capability_contract,
)
from l3_node.cognitive_kernel.recovery_playbook_schema import validate_recovery_playbook_manifest


DEFAULT_L1_BASE_URL = "http://47.86.39.173:3000"


def install_capability_from_l1(
    plugin_id: str,
    l1_base_url: str | None = None,
    repair: bool = False,
) -> dict[str, Any]:
    """Download a Skill/MCP/Model package from L1 and install all dependencies first."""

    clean_id = _normalize_dependency_id(plugin_id)
    if not clean_id:
        raise ValueError("plugin_id is required")

    base_url = _normalize_base_url(l1_base_url or os.environ.get("JACHIN_L1_BASE_URL") or DEFAULT_L1_BASE_URL)
    catalog = _fetch_catalog_map(base_url)
    installed_ids: list[str] = []
    result = _install_recursive(base_url, catalog, clean_id, repair, set(), installed_ids)
    result["installed_ids"] = installed_ids
    result["installed_count"] = len(installed_ids)
    return result


def _install_recursive(
    base_url: str,
    catalog: dict[str, dict[str, Any]],
    item_id: str,
    repair: bool,
    visiting: set[str],
    installed_ids: list[str],
) -> dict[str, Any]:
    target_id = _normalize_dependency_id(item_id)
    item = catalog.get(target_id)
    if not item:
        raise RuntimeError(f"package not found on L1: {target_id}")

    if _item_is_ready(target_id) and not repair:
        rec = _read_installed_registry().get("packages", {}).get(target_id) or {}
        return {
            "ok": True,
            "id": rec.get("id") or target_id,
            "version": rec.get("version") or item.get("version") or "0.0.0",
            "kind": rec.get("kind") or _normalize_kind(item.get("item_type") or item.get("kind") or "skill"),
            "installed_path": rec.get("installed_path"),
            "message": "already installed",
        }

    if target_id in visiting:
        raise RuntimeError(f"dependency cycle detected while installing {target_id}")
    visiting.add(target_id)
    for dep in _remote_dependencies(item):
        dep_id = _normalize_dependency_id(dep)
        if not dep_id or dep_id == target_id:
            continue
        if not _item_is_ready(dep_id):
            _install_recursive(base_url, catalog, dep_id, False, visiting, installed_ids)
    visiting.remove(target_id)

    result = _install_single(base_url, item, target_id)
    installed_ids.append(result["id"])
    return result


def _install_single(base_url: str, item: dict[str, Any], item_id: str) -> dict[str, Any]:
    package_url = str(item.get("package_url") or "").strip()
    if not package_url and _normalize_kind(item.get("item_type") or item.get("kind") or "") == "model":
        package_url = f"/api/v1/store/model-download?model_plugin_id={urllib.parse.quote(item_id)}"
    if not package_url:
        raise RuntimeError(f"package_url is missing for {item_id}")

    full_url = _resolve_url(base_url, package_url)
    downloaded = _download_package(full_url, item_id)
    actual_sha = _sha256_file(downloaded)
    expected_sha = str(item.get("package_sha256") or "").strip()
    if expected_sha and expected_sha.lower() != actual_sha.lower():
        raise RuntimeError(f"sha256 mismatch for {item_id}: expected {expected_sha}, got {actual_sha}")

    staging = _staging_dir(item_id)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    package_root = staging
    meta: dict[str, Any] = {}
    final_dir: Path | None = None
    try:
        _extract_zip(downloaded, staging)
        package_root = _detect_package_root(staging)
        meta = _read_package_meta(package_root)
        manifest = _read_capability_manifest(package_root, meta)
        playbook_errors = validate_recovery_playbook_manifest(manifest)
        if playbook_errors:
            preview = "; ".join(playbook_errors[:8])
            raise RuntimeError(f"invalid recovery_playbook in package {item_id}: {preview}")
        contract = validate_capability_contract(manifest)
        if contract.errors:
            preview = "; ".join(contract_error_messages(contract)[:8])
            raise RuntimeError(f"invalid capability contract in package {item_id}: {preview}")
        contract_warnings = contract_warning_messages(contract)
        installed_id = str(meta.get("id") or item.get("plugin_id") or item.get("id") or item_id).strip()
        kind = _normalize_kind(meta.get("kind") or item.get("item_type") or item.get("kind") or "skill")
        version = str(meta.get("version") or item.get("version") or "0.0.0")
        final_dir = _install_dir_for(kind, installed_id)
        backup_dir = _replace_existing_preserving_user_data(final_dir, _string_list(meta.get("preserve_user_data")))
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            package_root.rename(final_dir)
            if package_root != staging:
                shutil.rmtree(staging, ignore_errors=True)
            _restore_preserved_user_data(final_dir, _string_list(meta.get("preserve_user_data")), backup_dir)
            if backup_dir:
                shutil.rmtree(backup_dir, ignore_errors=True)
        except Exception:
            if final_dir.exists():
                shutil.rmtree(final_dir, ignore_errors=True)
            if backup_dir and backup_dir.exists():
                backup_dir.rename(final_dir)
            raise
    except Exception:
        if staging.exists() and final_dir != staging:
            shutil.rmtree(staging, ignore_errors=True)
        raise

    installed_id = str(meta.get("id") or item_id)
    kind = _normalize_kind(meta.get("kind") or item.get("item_type") or item.get("kind") or "skill")
    version = str(meta.get("version") or item.get("version") or "0.0.0")
    dependencies = _merge_dependencies(_remote_dependencies(item), meta)
    registry = _read_installed_registry()
    packages = registry.setdefault("packages", {})
    packages[installed_id] = {
        "id": installed_id,
        "name": item.get("name") or meta.get("name") or installed_id,
        "version": version,
        "kind": kind,
        "source": "l1",
        "package_url": full_url,
        "package_sha256": actual_sha,
        "installed_path": str(final_dir),
        "installed_at": _timestamp(),
        "enabled": True,
        "package_assets": meta.get("package_assets") or [],
        "preserve_user_data": meta.get("preserve_user_data") or [],
        "dependencies": dependencies,
        "capability_quality_score": contract.quality_score,
        "capability_contract_warnings": contract_warnings,
    }
    _write_installed_registry(registry)

    return {
        "ok": True,
        "id": installed_id,
        "version": version,
        "kind": kind,
        "installed_path": str(final_dir),
        "downloaded_path": str(downloaded),
        "package_sha256": actual_sha,
        "message": "installed from L1 package",
    }


def _fetch_catalog_map(base_url: str) -> dict[str, dict[str, Any]]:
    payload = _fetch_json(f"{base_url}/api/v1/store/catalog?limit=500")
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise RuntimeError("L1 catalog response missing data list")
    out: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_id = str(item.get("plugin_id") or item.get("id") or "").strip()
        if raw_id:
            out[_normalize_dependency_id(raw_id)] = item
    return out


def _remote_dependencies(item: dict[str, Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for key in ("required_mcps", "dependencies"):
        raw = item.get(key)
        if not isinstance(raw, list):
            continue
        for dep in raw:
            if isinstance(dep, str):
                value = dep.strip()
            elif isinstance(dep, dict):
                value = str(dep.get("plugin_id") or dep.get("model_id") or dep.get("id") or "").strip()
            else:
                value = ""
            if not value:
                continue
            norm = _normalize_dependency_id(value)
            if norm not in seen:
                seen.add(norm)
                out.append(value)
    raw_models = item.get("required_models")
    if isinstance(raw_models, list):
        for dep in raw_models:
            if isinstance(dep, str):
                value = dep.strip()
            elif isinstance(dep, dict):
                value = str(dep.get("plugin_id") or dep.get("model_id") or dep.get("id") or "").strip()
            else:
                value = ""
            if not value:
                continue
            raw = value if value.lower().startswith("model:") else f"model:{value}"
            norm = _normalize_dependency_id(raw)
            if norm not in seen:
                seen.add(norm)
                out.append(raw)
    return out


def _merge_dependencies(remote: list[str], meta: dict[str, Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in remote + _string_list(meta.get("required_mcps")):
        norm = _normalize_dependency_id(value)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(str(value))
    for value in _string_list(meta.get("required_models")):
        raw = value if value.lower().startswith("model:") else f"model:{value}"
        norm = _normalize_dependency_id(raw)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(raw)
    return out


def _item_is_ready(item_id: str) -> bool:
    rec = _read_installed_registry().get("packages", {}).get(_normalize_dependency_id(item_id))
    if not isinstance(rec, dict) or not rec.get("enabled", True):
        return False
    path = rec.get("installed_path")
    return bool(path and Path(str(path)).is_dir())


def _fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "Jachin-L3-CapabilityInstaller"})
    with urllib.request.urlopen(request, timeout=30) as resp:
        raw = resp.read()
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("JSON response must be an object")
    return data


def _download_package(url: str, item_id: str) -> Path:
    target_dir = _download_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{_safe_id(item_id)}-{_timestamp()}.zip"
    request = urllib.request.Request(url, headers={"User-Agent": "Jachin-L3-CapabilityInstaller"})
    with urllib.request.urlopen(request, timeout=300) as resp, target.open("wb") as f:
        shutil.copyfileobj(resp, f, length=1024 * 1024)
    return target


def _extract_zip(zip_path: Path, dest: Path) -> None:
    dest_resolved = dest.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            target = (dest / info.filename).resolve()
            try:
                target.relative_to(dest_resolved)
            except ValueError:
                raise RuntimeError(f"unsafe zip entry: {info.filename}")
        for info in zf.infolist():
            extracted = Path(zf.extract(info, dest))
            _restore_zip_permissions(info, extracted)


def _restore_zip_permissions(info: zipfile.ZipInfo, path: Path) -> None:
    if os.name == "nt" or not path.exists():
        return
    mode = (info.external_attr >> 16) & 0o777
    if mode:
        try:
            os.chmod(path, mode)
        except OSError:
            pass


def _detect_package_root(staging: Path) -> Path:
    if (staging / ".jachin-package.json").is_file() or (staging / "plugin.json").is_file():
        return staging
    candidates = [
        child
        for child in staging.iterdir()
        if child.is_dir() and ((child / ".jachin-package.json").is_file() or (child / "plugin.json").is_file())
    ]
    if len(candidates) == 1:
        return candidates[0]
    return staging


def _read_package_meta(package_root: Path) -> dict[str, Any]:
    for name in (".jachin-package.json", "plugin.json"):
        path = package_root / name
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}
    return {}


def _read_capability_manifest(package_root: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    path = package_root / "plugin.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                return data
        except Exception:
            return fallback
    return fallback


def _replace_existing_preserving_user_data(final_dir: Path, preserve: list[str]) -> Path | None:
    if not final_dir.exists():
        return None
    backup_dir = final_dir.with_name(f"{final_dir.name}.backup-{_timestamp()}")
    final_dir.rename(backup_dir)
    return backup_dir


def _restore_preserved_user_data(final_dir: Path, preserve: list[str], backup_dir: Path | None) -> None:
    if not backup_dir:
        return
    for rel in preserve:
        rel_path = Path(rel.replace("/", os.sep))
        if rel_path.is_absolute() or ".." in rel_path.parts:
            continue
        src = backup_dir / rel_path
        dst = final_dir / rel_path
        if not src.exists():
            continue
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalize_dependency_id(raw: str) -> str:
    value = str(raw or "").strip()
    lower = value.lower()
    if lower.startswith("model:"):
        return value[6:].strip()
    if lower.startswith("mcp:"):
        return value[4:].strip()
    return value


def _normalize_kind(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value == "model":
        return "model"
    if value == "mcp":
        return "mcp"
    if value == "tool":
        return "tool"
    return "skill"


def _install_dir_for(kind: str, item_id: str) -> Path:
    safe = _safe_id(item_id)
    if kind == "mcp":
        return _jachin_home_dir() / "l3_mcp_cache" / safe
    if kind == "model":
        return _jachin_home_dir() / "models" / safe
    return _jachin_home_dir() / "l3_skill_cache" / safe


def _read_installed_registry() -> dict[str, Any]:
    path = _installed_registry_path()
    if not path.is_file():
        return {"packages": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {"packages": {}}
    except Exception:
        return {"packages": {}}


def _write_installed_registry(registry: dict[str, Any]) -> None:
    path = _installed_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _resolve_url(base_url: str, raw: str) -> str:
    value = raw.strip()
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return f"{base_url}/{value.lstrip('/')}"


def _normalize_base_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    return value or DEFAULT_L1_BASE_URL


def _safe_id(value: str) -> str:
    return "".join("_" if c in '\\/:*?"<>|' else c for c in value)


def _string_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if str(x).strip()]


def _timestamp() -> str:
    return str(time.time_ns())


def _jachin_home_dir() -> Path:
    raw = os.environ.get("JACHIN_HOME")
    if raw:
        return Path(raw)
    if os.name == "nt":
        return Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".jachin"
    return Path(os.environ.get("HOME", str(Path.home()))) / ".jachin"


def _download_dir() -> Path:
    return _jachin_home_dir() / "capabilities" / "downloads"


def _staging_dir(item_id: str) -> Path:
    return _jachin_home_dir() / "capabilities" / "staging" / f"{_safe_id(item_id)}-{_timestamp()}"


def _installed_registry_path() -> Path:
    return _jachin_home_dir() / "capabilities" / "installed.json"
