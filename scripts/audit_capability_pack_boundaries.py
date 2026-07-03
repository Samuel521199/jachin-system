"""Audit MCP/Skill package boundaries for L3 portability.

Usage:
  python scripts/audit_capability_pack_boundaries.py
  python scripts/audit_capability_pack_boundaries.py --json output/capability_pack_audit.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.capability_pack_policy import (  # noqa: E402
    CORE_BUNDLED_SKILL_IDS,
    CORE_MCP_PACKAGE_IDS,
    BUSINESS_PACKAGE_IDS,
    is_l1_portable_stdio_value,
    iter_repo_package_dirs,
    manifest_package_id,
    package_tier,
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _read_manifest_yaml_id(path: Path) -> str:
    # Minimal, dependency-free id/name extraction for audit purposes.
    for line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        s = line.strip()
        if s.startswith("id:") or s.startswith("name:"):
            return s.split(":", 1)[1].strip().strip("\"'")
    return path.parent.name


def _stdio_values(plugin: dict[str, Any]) -> list[Any]:
    block = plugin.get("stdio_server") if isinstance(plugin.get("stdio_server"), dict) else {}
    values: list[Any] = []
    values.append(block.get("command"))
    values.extend(block.get("args") or [])
    env = block.get("env")
    if isinstance(env, dict):
        values.extend(env.values())
    return values


def _audit_plugin_package(path: Path) -> dict[str, Any]:
    plugin = _read_json(path / "plugin.json")
    pid = manifest_package_id(plugin, path.name)
    tier = package_tier(pid)
    item_type = str(plugin.get("item_type") or plugin.get("type") or "").lower()
    runtime_tier = str(plugin.get("runtime_tier") or "").upper()
    problems: list[str] = []

    if not plugin.get("version"):
        problems.append("missing version")
    if item_type == "mcp" and runtime_tier == "L3_LOCAL":
        has_stdio = isinstance(plugin.get("stdio_server"), dict)
        has_tools = bool(plugin.get("tools"))
        if not has_stdio and not has_tools:
            problems.append("L3_LOCAL MCP needs stdio_server or tools")
        bad_values = [v for v in _stdio_values(plugin) if not is_l1_portable_stdio_value(v)]
        if bad_values:
            problems.append("stdio_server contains __PROJECT_ROOT__; use __MCP_PACKAGE_ROOT__ or __JACHIN_HOME__")
    if tier == "business" and "skills_repo/_bundled" in path.as_posix():
        problems.append("business package must not live under skills_repo/_bundled")

    return {
        "path": str(path.relative_to(ROOT)),
        "id": pid,
        "kind": "mcp" if item_type == "mcp" else "plugin",
        "tier": tier,
        "portable": not problems,
        "problems": problems,
    }


def _audit_skill_package(path: Path) -> dict[str, Any]:
    if (path / "manifest.yaml").exists():
        pid = _read_manifest_yaml_id(path / "manifest.yaml")
    else:
        pid = path.name
    tier = package_tier(pid)
    problems: list[str] = []
    if "skills_repo/_bundled" in str(path.as_posix()) and pid not in CORE_BUNDLED_SKILL_IDS:
        problems.append("only core bundled skills may live under skills_repo/_bundled")
    if tier == "business" and "skills_repo/_bundled" in str(path.as_posix()):
        problems.append("business skill must be published as an L1 package, not bundled")
    return {
        "path": str(path.relative_to(ROOT)),
        "id": pid,
        "kind": "skill",
        "tier": tier,
        "portable": not problems,
        "problems": problems,
    }


def run_audit() -> dict[str, Any]:
    packages: list[dict[str, Any]] = []
    for path in iter_repo_package_dirs(ROOT):
        try:
            if (path / "plugin.json").exists():
                packages.append(_audit_plugin_package(path))
            else:
                packages.append(_audit_skill_package(path))
        except Exception as exc:
            packages.append(
                {
                    "path": str(path.relative_to(ROOT)),
                    "id": path.name,
                    "kind": "unknown",
                    "tier": "unknown",
                    "portable": False,
                    "problems": [str(exc)],
                }
            )

    problems = [p for p in packages if p["problems"]]
    return {
        "ok": not problems,
        "policy": {
            "core_bundled_skills": sorted(CORE_BUNDLED_SKILL_IDS),
            "core_mcp_packages": sorted(CORE_MCP_PACKAGE_IDS),
            "known_business_packages": sorted(BUSINESS_PACKAGE_IDS),
        },
        "counts": {
            "packages": len(packages),
            "core": sum(1 for p in packages if p["tier"] == "core"),
            "business": sum(1 for p in packages if p["tier"] == "business"),
            "extension": sum(1 for p in packages if p["tier"] == "extension"),
            "problems": len(problems),
        },
        "packages": packages,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", dest="json_path", help="Write full audit report to this path")
    args = parser.parse_args()

    report = run_audit()
    print(
        "Capability pack audit: "
        f"packages={report['counts']['packages']} "
        f"core={report['counts']['core']} "
        f"business={report['counts']['business']} "
        f"extension={report['counts']['extension']} "
        f"problems={report['counts']['problems']}"
    )
    for pkg in report["packages"]:
        if pkg["problems"]:
            print(f"[WARN] {pkg['id']} ({pkg['path']}): {'; '.join(pkg['problems'])}")

    if args.json_path:
        out = Path(args.json_path)
        if not out.is_absolute():
            out = ROOT / out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote audit report: {out}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
