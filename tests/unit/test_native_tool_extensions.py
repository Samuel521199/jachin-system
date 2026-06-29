from __future__ import annotations

import importlib


def test_pmo_native_tools_are_not_loaded_by_default(monkeypatch) -> None:
    monkeypatch.delenv("JACHIN_PMO_COPILOT_RUN", raising=False)
    monkeypatch.delenv("JACHIN_ENABLE_PMO_NATIVE_TOOLS", raising=False)
    monkeypatch.delenv("JACHIN_NATIVE_TOOL_EXTENSION_MODULES", raising=False)

    import l3_node.primitives.tools.native_extensions as ext

    importlib.reload(ext)
    ids = {t.get("id") for t in ext.load_native_extension_tools()}

    assert "core:pmo_macro_dashboard_push" not in ids
    assert "core:db_query" not in ids


def test_pmo_native_tools_load_only_when_pmo_runner_marks_process(monkeypatch) -> None:
    monkeypatch.setenv("JACHIN_PMO_COPILOT_RUN", "1")
    monkeypatch.delenv("JACHIN_ENABLE_PMO_NATIVE_TOOLS", raising=False)
    monkeypatch.delenv("JACHIN_NATIVE_TOOL_EXTENSION_MODULES", raising=False)

    import l3_node.primitives.tools.native_extensions as ext

    importlib.reload(ext)
    ids = {t.get("id") for t in ext.load_native_extension_tools()}

    assert "core:db_query" in ids
    assert "core:pmo_macro_dashboard_push" in ids
