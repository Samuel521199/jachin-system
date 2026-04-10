"""MCP 配置 env 中 ${VAR} 展开。"""

import os

from core.mcp_embedded_runtime import inject_os_env_tokens, resolve_mcp_cfg_placeholders


def test_inject_os_env_tokens() -> None:
    os.environ["__JACHIN_TEST_MCP_VAR__"] = "hello"
    try:
        assert inject_os_env_tokens("x${__JACHIN_TEST_MCP_VAR__}y") == "xhelloy"
        assert inject_os_env_tokens("${__JACHIN_TEST_MCP_VAR__}") == "hello"
    finally:
        os.environ.pop("__JACHIN_TEST_MCP_VAR__", None)


def test_resolve_mcp_cfg_placeholders_env() -> None:
    os.environ["__JACHIN_TEST_MCP_VAR2__"] = "secret"
    try:
        out = resolve_mcp_cfg_placeholders(
            {
                "id": "t",
                "command": "npx",
                "args": ["-y", "pkg"],
                "env": {"API": "${__JACHIN_TEST_MCP_VAR2__}"},
            }
        )
        assert out["env"] == {"API": "secret"}
    finally:
        os.environ.pop("__JACHIN_TEST_MCP_VAR2__", None)


def test_resolve_mcp_cfg_placeholders_env_backfill_empty_from_os() -> None:
    """配置里值为空串时，若 os.environ 已有同名变量则回填（占位符未写或曾为空）。"""
    os.environ["TAVILY_API_KEY"] = "tvly-from-os"
    try:
        out = resolve_mcp_cfg_placeholders(
            {
                "id": "x",
                "command": "npx",
                "args": ["-y", "pkg"],
                "env": {"TAVILY_API_KEY": ""},
            }
        )
        assert out["env"]["TAVILY_API_KEY"] == "tvly-from-os"
    finally:
        os.environ.pop("TAVILY_API_KEY", None)


def test_resolve_mcp_cfg_placeholders_tavily_injects_key_without_env_block() -> None:
    """Tavily stdio：未配置 env 时仍从 os.environ 注入 TAVILY_API_KEY（MCP SDK 不继承全环境）。"""
    os.environ["TAVILY_API_KEY"] = "tvly-direct"
    try:
        out = resolve_mcp_cfg_placeholders(
            {
                "id": "tavily-search",
                "command": "npx",
                "args": ["-y", "tavily-mcp@latest"],
            }
        )
        assert out["env"] == {"TAVILY_API_KEY": "tvly-direct"}
    finally:
        os.environ.pop("TAVILY_API_KEY", None)
