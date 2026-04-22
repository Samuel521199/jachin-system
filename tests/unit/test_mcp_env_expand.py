"""MCP 配置 env 中 ${VAR} 展开。"""

import os

from core.mcp_embedded_runtime import effective_stdio_env_for_sdk, inject_os_env_tokens, resolve_mcp_cfg_placeholders


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


def test_effective_stdio_env_browser_use_fills_config_path(tmp_path) -> None:
    cfg_file = tmp_path / "attach.json"
    cfg_file.write_text("{}", encoding="utf-8")
    os.environ["JACHIN_BROWSER_USE_ATTACH_CONFIG"] = str(cfg_file)
    try:
        eff = effective_stdio_env_for_sdk(
            "browser-use",
            ["--from", "browser-use[cli]", "browser-use", "--mcp"],
            {"BROWSER_USE_CONFIG_PATH": "${BROWSER_USE_CONFIG_PATH}"},
        )
        assert eff is not None
        assert eff.get("BROWSER_USE_CONFIG_PATH") == str(cfg_file.resolve())
    finally:
        os.environ.pop("JACHIN_BROWSER_USE_ATTACH_CONFIG", None)


def test_effective_stdio_env_browser_use_detects_args_without_dash_mcp(tmp_path) -> None:
    """与 K11 脚本一致：args 含 browser-use 即视为 browser-use MCP，不必同时写 --mcp。"""
    cfg_file = tmp_path / "attach.json"
    cfg_file.write_text("{}", encoding="utf-8")
    os.environ["JACHIN_BROWSER_USE_ATTACH_CONFIG"] = str(cfg_file)
    try:
        eff = effective_stdio_env_for_sdk(
            "k11-custom-id",
            ["uvx", "browser-use"],
            None,
            command="uvx",
        )
        assert eff is not None
        assert eff.get("BROWSER_USE_CONFIG_PATH") == str(cfg_file.resolve())
    finally:
        os.environ.pop("JACHIN_BROWSER_USE_ATTACH_CONFIG", None)


def test_resolve_mcp_cfg_placeholders_browser_use_injects_attach_when_file_exists(tmp_path) -> None:
    """browser-use：占位符未设时若本地附加配置文件存在则注入 BROWSER_USE_CONFIG_PATH。"""
    cfg_file = tmp_path / "browser-use-attach-cdp.json"
    cfg_file.write_text('{"browser_profile":{}}', encoding="utf-8")
    os.environ["JACHIN_BROWSER_USE_ATTACH_CONFIG"] = str(cfg_file)
    try:
        out = resolve_mcp_cfg_placeholders(
            {
                "id": "browser-use",
                "command": "uvx",
                "args": ["--from", "browser-use[cli]", "browser-use", "--mcp"],
                "env": {"BROWSER_USE_CONFIG_PATH": "${BROWSER_USE_CONFIG_PATH}"},
            }
        )
        assert out["env"]["BROWSER_USE_CONFIG_PATH"] == str(cfg_file.resolve())
    finally:
        os.environ.pop("JACHIN_BROWSER_USE_ATTACH_CONFIG", None)


def test_resolve_mcp_cfg_placeholders_jachin_puppeteer_injects_cdp_url(tmp_path) -> None:
    """jachin-puppeteer-cdp：附加 JSON 含 cdp_url 时补全 PUPPETEER_BROWSER_URL。"""
    cfg_file = tmp_path / "browser-use-attach-cdp.json"
    cfg_file.write_text(
        '{"browser_profile":{"p":{"cdp_url":"http://127.0.0.1:9222"}}}',
        encoding="utf-8",
    )
    os.environ["JACHIN_BROWSER_USE_ATTACH_CONFIG"] = str(cfg_file)
    try:
        out = resolve_mcp_cfg_placeholders(
            {
                "id": "jachin-puppeteer-cdp",
                "command": "node",
                "args": ["__JACHIN_REPO_ROOT__/tools/mcp-jachin-puppeteer-cdp/index.mjs"],
                "env": {"PUPPETEER_BROWSER_URL": "${PUPPETEER_BROWSER_URL}"},
            }
        )
        assert out["env"]["PUPPETEER_BROWSER_URL"] == "http://127.0.0.1:9222"
    finally:
        os.environ.pop("JACHIN_BROWSER_USE_ATTACH_CONFIG", None)
