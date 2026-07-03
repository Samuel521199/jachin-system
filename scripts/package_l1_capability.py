"""Package one MCP/Skill directory as an L1-uploadable zip.

The package keeps source-relative files and excludes secrets, caches, logs, and
local runtime output. A small ``.jachin-package.json`` is injected so L1/L2/L3
can inspect package id, tier, type, and sha256 without executing the package.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.capability_pack_policy import manifest_package_id, package_tier  # noqa: E402


EXCLUDE_DIRS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
    "target",
    "output",
    "data",
    "logs",
}

EXCLUDE_FILE_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".log",
    ".tmp",
    ".bak",
    ".zip",
    ".tar",
    ".gz",
    ".7z",
}

EXCLUDE_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".DS_Store",
    "Thumbs.db",
}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_yaml_like_id(path: Path) -> str:
    for line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        s = line.strip()
        if s.startswith("id:") or s.startswith("name:"):
            return s.split(":", 1)[1].strip().strip("\"'")
    return path.parent.name


def _package_metadata(source: Path) -> dict[str, Any]:
    plugin_path = source / "plugin.json"
    manifest_path = source / "manifest.yaml"
    package_assets: list[dict[str, Any]] = []
    preserve_user_data: list[str] = []
    required_mcps: list[str] = []
    required_models: list[str] = []
    if plugin_path.exists():
        manifest = _read_json(plugin_path)
        package_id = manifest_package_id(manifest, source.name)
        kind = str(manifest.get("item_type") or manifest.get("type") or "plugin").lower()
        version = str(manifest.get("version") or "0.0.0")
        package_assets = _collect_package_assets(source, manifest)
        preserve_user_data = _collect_preserve_user_data(manifest)
        required_mcps = _collect_string_list(manifest.get("required_mcps") or manifest.get("dependencies") or [])
        required_models = _collect_string_list(manifest.get("required_models") or [])
    elif manifest_path.exists():
        package_id = _read_yaml_like_id(manifest_path)
        kind = "skill"
        version = "0.0.0"
    elif (source / "SKILL.md").exists():
        package_id = source.name
        kind = "skill"
        version = "0.0.0"
    else:
        raise SystemExit(f"{source} is not a capability package: missing plugin.json, manifest.yaml, or SKILL.md")
    return {
        "id": package_id,
        "kind": "mcp" if kind == "mcp" else ("model" if kind == "model" else kind),
        "version": version,
        "tier": package_tier(package_id),
        "packaged_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "format": "jachin.l1.capability.zip.v1",
        "package_assets": package_assets,
        "preserve_user_data": preserve_user_data,
        "required_mcps": required_mcps,
        "required_models": required_models,
    }


def _collect_string_list(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        value = item.strip()
        if not value or value in seen:
            continue
        out.append(value)
        seen.add(value)
    return out


def _collect_preserve_user_data(manifest: dict[str, Any]) -> list[str]:
    raw = manifest.get("preserve_user_data") or manifest.get("user_data_roots") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.append(item.strip().replace("\\", "/"))
    return out


def _collect_package_assets(source: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    raw = manifest.get("package_assets") or manifest.get("package_include") or []
    if isinstance(raw, (str, dict)):
        raw = [raw]
    if not isinstance(raw, list):
        return []

    assets: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            src_raw = item
            dst_raw = f"assets/{Path(item).name}"
        elif isinstance(item, dict):
            src_raw = str(item.get("from") or item.get("path") or "").strip()
            dst_raw = str(item.get("to") or "").strip()
            if not dst_raw:
                dst_raw = f"assets/{Path(src_raw).name}"
        else:
            continue
        if not src_raw:
            continue
        src = _resolve_asset_source(source, src_raw)
        dst = _safe_asset_destination(dst_raw)
        if src.is_file():
            assets.append(
                {
                    "from": str(src.relative_to(ROOT).as_posix()),
                    "to": dst,
                    "sha256": _sha256(src),
                    "size": src.stat().st_size,
                }
            )
        elif src.is_dir():
            files = [p for p in sorted(src.rglob("*")) if p.is_file() and _include_file(p.relative_to(src))]
            for path in files:
                rel = path.relative_to(src).as_posix()
                assets.append(
                    {
                        "from": str(path.relative_to(ROOT).as_posix()),
                        "to": f"{dst.rstrip('/')}/{rel}",
                        "sha256": _sha256(path),
                        "size": path.stat().st_size,
                    }
                )
        else:
            raise SystemExit(f"package asset not found: {src_raw} (resolved to {src})")
    return assets


def _resolve_asset_source(source: Path, raw: str) -> Path:
    p = Path(raw)
    candidates = []
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.extend([ROOT / p, source / p])
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.exists():
            if not resolved.is_relative_to(ROOT):
                raise SystemExit(f"package asset must stay inside repo root: {resolved}")
            return resolved
    return candidates[0].resolve()


def _safe_asset_destination(raw: str) -> str:
    dst = raw.strip().replace("\\", "/").lstrip("/")
    if not dst or dst.startswith("../") or "/../" in dst or dst == "..":
        raise SystemExit(f"unsafe package asset destination: {raw}")
    if dst in {".jachin-package.json", "plugin.json"}:
        raise SystemExit(f"package asset destination collides with package metadata: {raw}")
    return dst


def _include_file(path: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in path.parts):
        return False
    if path.name in EXCLUDE_FILENAMES:
        return False
    if path.suffix.lower() in EXCLUDE_FILE_SUFFIXES:
        return False
    return True


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def package_capability(source: Path, out_dir: Path) -> Path:
    source = source.resolve()
    if not source.is_dir():
        raise SystemExit(f"source directory not found: {source}")
    meta = _package_metadata(source)
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_id = meta["id"].replace(":", "_").replace("/", "_").replace("\\", "_")
    version = meta["version"].replace("/", "_").replace("\\", "_")
    out_path = out_dir / f"{safe_id}-{version}.zip"

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(".jachin-package.json", json.dumps(meta, ensure_ascii=False, indent=2))
        for path in sorted(source.rglob("*")):
            if path.is_dir():
                continue
            rel = path.relative_to(source)
            if not _include_file(rel):
                continue
            if rel.as_posix() == "plugin.json":
                content = path.read_text(encoding="utf-8-sig")
                zf.writestr(rel.as_posix(), content.encode("utf-8"))
            else:
                zf.write(path, rel.as_posix())
        written_assets: set[str] = set()
        for asset in meta.get("package_assets", []):
            if not isinstance(asset, dict):
                continue
            src_rel = str(asset.get("from") or "").strip()
            dst = _safe_asset_destination(str(asset.get("to") or "").strip())
            if not src_rel or not dst or dst in written_assets:
                continue
            src = (ROOT / src_rel).resolve()
            if not src.is_file():
                raise SystemExit(f"package asset file disappeared: {src}")
            zf.write(src, dst)
            written_assets.add(dst)

    sha = _sha256(out_path)
    meta_path = out_path.with_suffix(out_path.suffix + ".sha256")
    meta_path.write_text(f"{sha}  {out_path.name}\n", encoding="utf-8")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", help="Capability package directory")
    parser.add_argument("--out", default="dist_l1_capabilities", help="Output directory")
    args = parser.parse_args()

    source = Path(args.source)
    if not source.is_absolute():
        source = ROOT / source
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out = package_capability(source, out_dir)
    print(f"Packaged: {out}")
    print(f"SHA256:   {out.with_suffix(out.suffix + '.sha256')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
