"""Capability package boundary policy for MCP/Skill loading.

The desktop/L3 runtime must stay small and portable: core capabilities may be
bundled, while business capabilities must be delivered as independent L1/L2
packages and installed into the user's ``~/.jachin`` caches.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


CORE_BUNDLED_SKILL_IDS: frozenset[str] = frozenset(
    {
        "com.jachin.os-mate",
        "com.jachin.files",
        "com.jachin.calendar",
        "com.jachin.voip",
    }
)

CORE_MCP_PACKAGE_IDS: frozenset[str] = frozenset(
    {
        "com.jachin.mcp.filesystem_workspace",
        "com.jachin.mcp.git.workspace",
        "com.jachin.mcp.officialfetch",
        "com.jachin.mcp.sqlite.workspace",
        "com.jachin.mcp.playwright_browser",
        "com.jachin.mcp.office_powerpoint",
        "com.jachin.mcp.tavily.search",
        "com.jachin.mcp.smtp.sendmail",
    }
)

BUSINESS_PACKAGE_PREFIXES: tuple[str, ...] = (
    "com.jachin.hr.",
    "com.jachin.hr-",
    "com.jachin.bi.",
    "com.jachin.pmo",
    "pmo-",
    "hr-",
    "a-share-",
    "global-market-",
    "kalaroko-",
    "youtube-",
    "bilibili-",
    "test-",
)

BUSINESS_PACKAGE_IDS: frozenset[str] = frozenset(
    {
        "com.jachin.hr.recruitment",
        "com.jachin.bi.daily_report",
        "pmo-copilot",
        "hr-recruitment",
        "a-share-analyst",
        "global-market-analyst",
        "daily-nexus-commander",
    }
)

DEV_REPO_PACKAGE_ENV = "JACHIN_DEV_LOAD_REPO_CAPABILITY_PACKAGES"
DEV_BUSINESS_PACKAGE_ENV = "JACHIN_DEV_LOAD_BUSINESS_CAPABILITY_PACKAGES"


def truthy_env(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def normalize_package_id(package_id: str | None) -> str:
    return str(package_id or "").strip()


def manifest_package_id(manifest: dict[str, Any], fallback: str = "") -> str:
    return normalize_package_id(
        manifest.get("id")
        or manifest.get("plugin_id")
        or manifest.get("name")
        or fallback
    )


def is_core_package_id(package_id: str | None) -> bool:
    pid = normalize_package_id(package_id)
    return pid in CORE_BUNDLED_SKILL_IDS or pid in CORE_MCP_PACKAGE_IDS


def is_business_package_id(package_id: str | None) -> bool:
    pid = normalize_package_id(package_id)
    low = pid.lower()
    return pid in BUSINESS_PACKAGE_IDS or any(low.startswith(prefix) for prefix in BUSINESS_PACKAGE_PREFIXES)


def package_tier(package_id: str | None) -> str:
    """Return ``core``, ``business``, or ``extension`` for a package id."""
    if is_core_package_id(package_id):
        return "core"
    if is_business_package_id(package_id):
        return "business"
    return "extension"


def repo_capability_packages_enabled() -> bool:
    """Whether dev runtime may scan arbitrary repo packages outside caches."""
    return truthy_env(DEV_REPO_PACKAGE_ENV)


def repo_business_packages_enabled() -> bool:
    """Whether dev runtime may scan repo business packages directly."""
    return truthy_env(DEV_BUSINESS_PACKAGE_ENV)


def should_load_repo_package(package_id: str | None, *, is_mcp: bool = False) -> bool:
    """Return whether a package under the source repo may be auto-loaded.

    Installed packages in ``~/.jachin/l3_*_cache`` are not controlled by this
    function; they already came through subscription/sideload. This only guards
    source-tree scanning such as ``skills_repo/plugin`` during development.
    """
    pid = normalize_package_id(package_id)
    if not pid:
        return False
    if is_core_package_id(pid):
        return True
    if is_business_package_id(pid):
        return repo_business_packages_enabled()
    return repo_capability_packages_enabled()


def should_scan_repo_skill_roots() -> bool:
    """Whether PluginManager should auto-scan non-bundled source skills."""
    return repo_capability_packages_enabled() or repo_business_packages_enabled()


def is_l1_portable_stdio_value(value: Any) -> bool:
    """Check whether stdio command/arg/env values avoid repo-only placeholders."""
    if not isinstance(value, str):
        return True
    # __PROJECT_ROOT__ makes a downloaded L1 package depend on the development
    # repository. Package code should use __MCP_PACKAGE_ROOT__ or ~/.jachin.
    return "__PROJECT_ROOT__" not in value


def iter_repo_package_dirs(root: Path) -> list[Path]:
    """List likely source-tree capability package directories for audits."""
    out: list[Path] = []
    for rel in (
        "skills_repo/_bundled",
        "skills_repo/plugin",
        "skills_repo/apps",
        "skills_repo/drivers",
        "skills_repo",
    ):
        base = root / rel
        if not base.exists() or not base.is_dir():
            continue
        for child in base.iterdir():
            if not child.is_dir() or child.name.startswith(".") or child.name.startswith("_"):
                continue
            if (child / "plugin.json").exists() or (child / "manifest.yaml").exists() or (child / "SKILL.md").exists():
                out.append(child)
    return out
