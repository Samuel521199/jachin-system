"""server-filesystem：启动前须存在可访问根目录（与上游 Node 契约一致）。"""
from __future__ import annotations

import tempfile
from pathlib import Path

from core.inventory_scanner import _prune_mcp_filesystem_roots
from core.mcp_client import ensure_mcp_server_filesystem_root_directories


def test_prune_mcp_filesystem_roots_creates_missing_directory() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        missing = root / "nested" / "new_workspace"
        assert not missing.is_dir()
        args = [
            "npx",
            "-y",
            "@modelcontextprotocol/server-filesystem@0.6.3",
            str(missing),
        ]
        out = _prune_mcp_filesystem_roots(args)
        assert out is not None
        assert missing.is_dir()
        assert str(missing) in [str(x) for x in out]


def test_ensure_mcp_server_filesystem_root_directories() -> None:
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "fs_root"
        assert not d.exists()
        ensure_mcp_server_filesystem_root_directories(
            ["-y", "@modelcontextprotocol/server-filesystem@0.6.3", str(d)]
        )
        assert d.is_dir()
