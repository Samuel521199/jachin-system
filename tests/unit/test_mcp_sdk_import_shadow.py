from __future__ import annotations

import importlib
import sys
from pathlib import Path


def test_stdio_noise_filter_imports_official_mcp_when_primitives_shadows(monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    primitives = str((repo_root / "l3_node" / "primitives").resolve())

    monkeypatch.syspath_prepend(primitives)
    for name in list(sys.modules):
        if name == "mcp" or name.startswith("mcp."):
            sys.modules.pop(name, None)
    sys.modules.pop("core.mcp_stdio_noise_filter", None)

    mod = importlib.import_module("core.mcp_stdio_noise_filter")
    loaded_from = Path(mod.types.__file__).resolve()
    local_mcp = (repo_root / "l3_node" / "primitives" / "mcp").resolve()

    assert hasattr(mod.types, "JSONRPCMessage")
    assert not loaded_from.is_relative_to(local_mcp)
