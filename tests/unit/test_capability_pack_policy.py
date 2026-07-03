from pathlib import Path

from core.capability_pack_policy import (
    DEV_BUSINESS_PACKAGE_ENV,
    DEV_REPO_PACKAGE_ENV,
    is_business_package_id,
    is_core_package_id,
    is_l1_portable_stdio_value,
    package_tier,
    should_load_repo_package,
    should_scan_repo_skill_roots,
)


def test_core_and_business_package_classification():
    assert is_core_package_id("com.jachin.mcp.filesystem_workspace")
    assert is_core_package_id("com.jachin.files")
    assert is_business_package_id("com.jachin.hr.recruitment")
    assert is_business_package_id("pmo-copilot")
    assert package_tier("com.jachin.mcp.filesystem_workspace") == "core"
    assert package_tier("com.jachin.hr.recruitment") == "business"
    assert package_tier("com.example.custom") == "extension"


def test_repo_packages_are_hidden_by_default(monkeypatch):
    monkeypatch.delenv(DEV_REPO_PACKAGE_ENV, raising=False)
    monkeypatch.delenv(DEV_BUSINESS_PACKAGE_ENV, raising=False)

    assert should_load_repo_package("com.jachin.mcp.filesystem_workspace", is_mcp=True)
    assert not should_load_repo_package("com.jachin.hr.recruitment", is_mcp=True)
    assert not should_load_repo_package("com.example.custom", is_mcp=True)
    assert not should_scan_repo_skill_roots()


def test_repo_business_packages_need_explicit_dev_flag(monkeypatch):
    monkeypatch.delenv(DEV_REPO_PACKAGE_ENV, raising=False)
    monkeypatch.setenv(DEV_BUSINESS_PACKAGE_ENV, "1")

    assert should_load_repo_package("com.jachin.hr.recruitment", is_mcp=True)
    assert should_scan_repo_skill_roots()


def test_repo_extension_packages_need_explicit_dev_flag(monkeypatch):
    monkeypatch.setenv(DEV_REPO_PACKAGE_ENV, "true")
    monkeypatch.delenv(DEV_BUSINESS_PACKAGE_ENV, raising=False)

    assert should_load_repo_package("com.example.custom", is_mcp=True)
    assert not should_load_repo_package("com.jachin.hr.recruitment", is_mcp=True)
    assert should_scan_repo_skill_roots()


def test_l1_portable_stdio_values_disallow_project_root():
    assert is_l1_portable_stdio_value("__MCP_PACKAGE_ROOT__/server.py")
    assert is_l1_portable_stdio_value(str(Path.home()))
    assert not is_l1_portable_stdio_value("__PROJECT_ROOT__/skills_repo/plugin/server.py")
