"""npx server-filesystem 握手重试辅助（initialize 阶段 Connection closed）。"""
from __future__ import annotations

from core.mcp_client import (
    _npx_filesystem_connect_max_attempts,
    _npx_filesystem_initialize_failure_retriable,
)


def test_initialize_failure_retriable_detects_connection_closed() -> None:
    class McpErr(Exception):
        pass

    assert _npx_filesystem_initialize_failure_retriable(McpErr("Connection closed"))
    assert not _npx_filesystem_initialize_failure_retriable(ValueError("bad args"))


def test_max_attempts_is_sane() -> None:
    n = _npx_filesystem_connect_max_attempts()
    assert 1 <= n <= 10
