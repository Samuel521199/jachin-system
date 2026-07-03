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


DEFAULT_L1_BASE_URL = "http://47.86.39.173:3000"


def install_model_from_l1(plugin_id: str, l1_base_url: str | None = None) -> dict[str, Any]:
    """Download a MODEL package from L1 and install it into ~/.jachin/models."""

    clean_id = (plugin_id or "").strip()
    if not clean_id:
        raise ValueError("plugin_id is required")

    base_url = _normalize_base_url(l1_base_url or os.environ.get("JACHIN_L1_BASE_URL") or DEFAULT_L1_BASE_URL)
    item = _find_model_item(base_url, clean_id)
    package_url = (item.get("package_url") or "").strip()
    if not package_url:
        package_url = f"/api/v1/store/model-download?model_plugin_id={urllib.parse.quote(clean_id)}"
    full_url = _resolve_url(base_url, package_url)

    downloaded = _download_package(full_url, clean_id)
    actual_sha = _sha256_file(downloaded)
    expected_sha = str(item.get("package_sha256") or "").strip()
    if expected_sha and expected_sha.lower() != actual_sha.lower():
        raise RuntimeError(f"sha256 mismatch for {clean_id}: expected {expected_sha}, got {actual_sha}")

    staging = _staging_dir(clean_id)
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        _extract_zip(downloaded, staging)
        package_root = _detect_package_root(staging)
        meta = _read_package_meta(package_root)
        installed_id = str(meta.get("id") or clean_id)
        version = str(meta.get("version") or item.get("version") or "0.0.0")
        final_dir = _model_cache_dir() / _safe_id(installed_id)
        backup = None
        if final_dir.exists():
            backup = final_dir.with_name(f"{final_dir.name}.backup-{_timestamp()}")
            final_dir.rename(backup)
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        try:
            if package_root == staging:
                staging.rename(final_dir)
                staging = final_dir
            else:
                package_root.rename(final_dir)
                shutil.rmtree(staging, ignore_errors=True)
            if backup:
                shutil.rmtree(backup, ignore_errors=True)
        except Exception:
            if final_dir.exists():
                shutil.rmtree(final_dir, ignore_errors=True)
            if backup and backup.exists():
                backup.rename(final_dir)
            raise
    except Exception:
        if staging.exists() and staging.name != _safe_id(clean_id):
            shutil.rmtree(staging, ignore_errors=True)
        raise

    registry = _read_installed_registry()
    packages = registry.setdefault("packages", {})
    packages[installed_id] = {
        "id": installed_id,
        "name": item.get("name") or meta.get("name") or installed_id,
        "version": version,
        "kind": "model",
        "source": "l1",
        "package_url": full_url,
        "package_sha256": actual_sha,
        "installed_path": str(final_dir),
        "installed_at": _timestamp(),
        "enabled": True,
        "package_assets": meta.get("package_assets") or [],
        "preserve_user_data": meta.get("preserve_user_data") or [],
    }
    _write_installed_registry(registry)

    return {
        "ok": True,
        "id": installed_id,
        "version": version,
        "kind": "model",
        "installed_path": str(final_dir),
        "downloaded_path": str(downloaded),
        "package_sha256": actual_sha,
        "message": "model installed into ~/.jachin/models",
    }


def _find_model_item(base_url: str, plugin_id: str) -> dict[str, Any]:
    url = f"{base_url}/api/v1/store/catalog?item_type=MODEL&limit=500"
    payload = _fetch_json(url)
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise RuntimeError("L1 catalog response missing data list")
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("plugin_id") == plugin_id or item.get("id") == plugin_id:
            return item
    raise RuntimeError(f"model package not found on L1: {plugin_id}")


def _fetch_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "Jachin-L3-ModelInstaller"})
    with urllib.request.urlopen(request, timeout=30) as resp:
        raw = resp.read()
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("JSON response must be an object")
    return data


def _download_package(url: str, plugin_id: str) -> Path:
    target_dir = _download_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{_safe_id(plugin_id)}-{_timestamp()}.zip"
    request = urllib.request.Request(url, headers={"User-Agent": "Jachin-L3-ModelInstaller"})
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
        zf.extractall(dest)


def _detect_package_root(staging: Path) -> Path:
    if (staging / ".jachin-package.json").is_file() or (staging / "plugin.json").is_file():
        return staging
    candidates = []
    for child in staging.iterdir():
        if child.is_dir() and ((child / ".jachin-package.json").is_file() or (child / "plugin.json").is_file()):
            candidates.append(child)
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


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


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


def _model_cache_dir() -> Path:
    return _jachin_home_dir() / "models"


def _staging_dir(plugin_id: str) -> Path:
    return _jachin_home_dir() / "capabilities" / "staging" / f"{_safe_id(plugin_id)}-{_timestamp()}"


def _installed_registry_path() -> Path:
    return _jachin_home_dir() / "capabilities" / "installed.json"
