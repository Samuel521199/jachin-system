"""MCP 工具参数别名：file_path → path（对齐 server-filesystem schema）。"""
from __future__ import annotations

import asyncio

from core.mcp_client import (
    _SERVER_FILESYSTEM_NPM_PIN,
    _pin_server_filesystem_npm_version,
    normalize_mcp_schema_aliases,
    stdio_official_filesystem_workspace_cwd,
)


def test_pin_server_filesystem_upgrades_unversioned_and_old_pins() -> None:
    assert _pin_server_filesystem_npm_version(
        ["-y", "@modelcontextprotocol/server-filesystem", "/tmp/ws"]
    ) == ["-y", _SERVER_FILESYSTEM_NPM_PIN, "/tmp/ws"]
    assert _pin_server_filesystem_npm_version(
        ["-y", "@modelcontextprotocol/server-filesystem@0.6.2", "/tmp/ws"]
    ) == ["-y", _SERVER_FILESYSTEM_NPM_PIN, "/tmp/ws"]
    assert _pin_server_filesystem_npm_version(
        ["-y", _SERVER_FILESYSTEM_NPM_PIN, "/tmp/ws"]
    ) == ["-y", _SERVER_FILESYSTEM_NPM_PIN, "/tmp/ws"]


def test_stdio_filesystem_cwd_first_allowed_root(tmp_path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    args = ["-y", "@modelcontextprotocol/server-filesystem", str(root)]
    assert stdio_official_filesystem_workspace_cwd(args) == str(root.resolve())


def test_stdio_filesystem_cwd_none_without_package() -> None:
    assert stdio_official_filesystem_workspace_cwd(["-y", "other-pkg"]) is None


def test_write_file_maps_file_path_to_path() -> None:
    a = normalize_mcp_schema_aliases(
        "write_file",
        {"file_path": "/tmp/x.py", "content": "print(1)"},
    )
    assert a.get("path") == "/tmp/x.py"
    assert "file_path" not in a
    assert a.get("content") == "print(1)"


def test_read_file_maps_file_path() -> None:
    a = normalize_mcp_schema_aliases("read_file", {"file_path": "foo.txt"})
    assert a.get("path") == "foo.txt"


def test_unrelated_tool_unchanged() -> None:
    a = normalize_mcp_schema_aliases("fetch", {"url": "https://a.com"})
    assert a == {"url": "https://a.com"}


def test_should_prime_coder_for_python_script_request() -> None:
    from core.llm_provider import should_prime_l3_react_coder_mode

    msgs = [
        {"role": "user", "content": "请新建 scripts 并写 Python 脚本 system_monitor.py，每2秒打印 CPU 内存。"},
    ]
    assert should_prime_l3_react_coder_mode(react_iteration=0, full_messages=msgs) is True
    assert should_prime_l3_react_coder_mode(react_iteration=1, full_messages=msgs) is False


def test_to_openai_write_file_requires_path_and_content_even_if_params_order_content_first() -> None:
    from l3_node.primitives.mcp.registry import MCPToolRegistry

    reg = MCPToolRegistry(l2_base_url="http://127.0.0.1:9")
    tools = [
        {
            "id": "mcp:write_file",
            "label": "mcp:write_file",
            "desc": "写文件",
            "params": ["content", "path"],
        }
    ]
    out = reg.to_openai_tools_schema(tools)
    assert out and out[0]["function"]["name"] == "mcp_write_file"
    req = out[0]["function"]["parameters"].get("required") or []
    assert set(req) == {"path", "content"}


def test_bridge_write_file_missing_path_returns_json() -> None:
    from l3_node.primitives.mcp.registry import MCPToolRegistry

    reg = MCPToolRegistry(l2_base_url="http://127.0.0.1:9")
    out = asyncio.run(
        reg._bridge_atomic_file_mcp_to_native("mcp:write_file", '{"content":"print(1)"}')
    )
    assert out is not None
    assert "missing_path" in out


def test_bridge_non_file_tool_returns_none() -> None:
    from l3_node.primitives.mcp.registry import MCPToolRegistry

    reg = MCPToolRegistry(l2_base_url="http://127.0.0.1:9")
    assert (
        asyncio.run(reg._bridge_atomic_file_mcp_to_native("mcp:fetch", '{"url":"https://x.com"}'))
        is None
    )


def test_infer_mcp_write_path_from_user_messages_scripts_folder() -> None:
    from l3_node.agent_core import _infer_mcp_write_path_from_user_messages

    msgs = [
        {
            "role": "user",
            "content": "请在你的工作区目录下新建一个名为 scripts 的文件夹，并在里面写一个 Python 脚本 system_monitor.py。",
        },
    ]
    assert _infer_mcp_write_path_from_user_messages(msgs) == "scripts/system_monitor.py"


def test_format_mcp_tool_args_for_log_write_file() -> None:
    from core.mcp_client import format_mcp_tool_args_for_log

    s = format_mcp_tool_args_for_log(
        "write_file",
        {"content": "print(1)", "file_path": "/tmp/x.py"},
    )
    assert "keys=" in s
    assert "path=" in s
    assert "content=" in s


def test_build_tools_description_warns_path_for_mcp_write_file() -> None:
    from l3_node.primitives.tools.loader import build_tools_description

    s = build_tools_description(
        [
            {
                "id": "mcp:write_file",
                "label": "mcp:write_file",
                "desc": "写入工作区文件",
                "params": ["content", "path"],
            }
        ]
    )
    assert "mcp:write_file" in s
    assert "path" in s
    assert "file_path" in s
