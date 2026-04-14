#!/usr/bin/env python3
"""
生成 L1 商城可 jachin pack 的「官方 MCP 参考」占位包（仅 plugin.json，无 config/）。
与 config/mcp_servers.json.example、tools/mcp-official 中的 id 对应，便于一键上架。

用法（仓库根）: python scripts/gen_l1_mcp_stub_packages.py
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "skills_repo" / "l1_upload_stubs"

STUBS: list[dict[str, object]] = [
    {
        "dir": "com.jachin.mcp.stub.official.fetch",
        "plugin": {
            "id": "com.jachin.mcp.stub.official.fetch",
            "name": "MCP Fetch（stub）",
            "version": "1.0.0",
            "description": "PyPI mcp-server-fetch；与本机 official-fetch / official-mcp-fetch 配置对应。",
            "type": "mcp",
            "runtime_tier": "L3_LOCAL",
            "mcp_execution_mode": "stdio_server",
            "stdio_server": {
                "id": "official-fetch",
                "command": "python",
                "args": ["-m", "mcp_server_fetch"],
                "env": {"PYTHONIOENCODING": "utf-8"},
            },
        },
    },
    {
        "dir": "com.jachin.mcp.stub.official.time",
        "plugin": {
            "id": "com.jachin.mcp.stub.official.time",
            "name": "MCP Time（stub）",
            "version": "1.0.0",
            "description": "PyPI mcp_server_time；与本机 official-time 对应。",
            "type": "mcp",
            "runtime_tier": "L3_LOCAL",
            "mcp_execution_mode": "stdio_server",
            "stdio_server": {
                "id": "official-time",
                "command": "python",
                "args": ["-m", "mcp_server_time"],
                "env": {"PYTHONIOENCODING": "utf-8"},
            },
        },
    },
    {
        "dir": "com.jachin.mcp.stub.official.git",
        "plugin": {
            "id": "com.jachin.mcp.stub.official.git",
            "name": "MCP Git（stub）",
            "version": "1.0.0",
            "description": "PyPI mcp_server_git；仓库根请在本机 args 中替换为实际路径。",
            "type": "mcp",
            "runtime_tier": "L3_LOCAL",
            "mcp_execution_mode": "stdio_server",
            "stdio_server": {
                "id": "official-git",
                "command": "python",
                "args": ["-m", "mcp_server_git", "--repository", "__JACHIN_WORKSPACE__"],
                "env": {"PYTHONIOENCODING": "utf-8"},
            },
        },
    },
    {
        "dir": "com.jachin.mcp.stub.official.filesystem.dirs",
        "plugin": {
            "id": "com.jachin.mcp.stub.official.filesystem.dirs",
            "name": "MCP Filesystem 多目录（stub）",
            "version": "1.0.0",
            "description": "npm @modelcontextprotocol/server-filesystem；与本机 official-filesystem 多目录示例对应。",
            "type": "mcp",
            "runtime_tier": "L3_LOCAL",
            "mcp_execution_mode": "stdio_server",
            "stdio_server": {
                "id": "official-filesystem",
                "command": "npx",
                "args": [
                    "-y",
                    "@modelcontextprotocol/server-filesystem",
                    "__JACHIN_WORKSPACE__",
                ],
            },
        },
    },
    {
        "dir": "com.jachin.mcp.stub.official.sqlite.npx",
        "plugin": {
            "id": "com.jachin.mcp.stub.official.sqlite.npx",
            "name": "MCP SQLite npm（stub）",
            "version": "1.0.0",
            "description": "npm mcp-sqlite；数据库路径使用工作区内 sqlite。",
            "type": "mcp",
            "runtime_tier": "L3_LOCAL",
            "mcp_execution_mode": "stdio_server",
            "stdio_server": {
                "id": "official-sqlite-npx",
                "command": "npx",
                "args": ["-y", "mcp-sqlite", "__JACHIN_WORKSPACE__/test_db.sqlite"],
            },
        },
    },
    {
        "dir": "com.jachin.mcp.stub.official.memory.npx",
        "plugin": {
            "id": "com.jachin.mcp.stub.official.memory.npx",
            "name": "MCP Memory（stub）",
            "version": "1.0.0",
            "description": "npm @modelcontextprotocol/server-memory。",
            "type": "mcp",
            "runtime_tier": "L3_LOCAL",
            "mcp_execution_mode": "stdio_server",
            "stdio_server": {
                "id": "official-memory-npx",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-memory"],
            },
        },
    },
    {
        "dir": "com.jachin.mcp.stub.tavily.search",
        "plugin": {
            "id": "com.jachin.mcp.stub.tavily.search",
            "name": "Tavily Search（stub）",
            "version": "1.0.0",
            "description": "npm tavily-mcp；需本机配置 TAVILY_API_KEY。",
            "type": "mcp",
            "runtime_tier": "L3_LOCAL",
            "mcp_execution_mode": "stdio_server",
            "stdio_server": {
                "id": "tavily-search",
                "command": "npx",
                "args": ["-y", "tavily-mcp@latest"],
                "env": {"TAVILY_API_KEY": "${TAVILY_API_KEY}"},
            },
        },
    },
    {
        "dir": "com.jachin.mcp.stub.playwright.browser",
        "plugin": {
            "id": "com.jachin.mcp.stub.playwright.browser",
            "name": "MCP Playwright（stub）",
            "version": "1.0.0",
            "description": "npm @playwright/mcp 浏览器自动化。",
            "type": "mcp",
            "runtime_tier": "L3_LOCAL",
            "mcp_execution_mode": "stdio_server",
            "stdio_server": {
                "id": "playwright-browser-npx",
                "command": "npx",
                "args": ["-y", "@playwright/mcp@latest"],
            },
        },
    },
    {
        "dir": "com.jachin.mcp.stub.sqlite.life.ledger",
        "plugin": {
            "id": "com.jachin.mcp.stub.sqlite.life.ledger",
            "name": "SQLite 生活库 / 记账（stub）",
            "version": "1.0.0",
            "description": "PyPI mcp-server-sqlite（uvx）；与本机 sqlite_manager 对应，默认库 ~/.jachin/workspace/my_life_data.db。",
            "type": "mcp",
            "runtime_tier": "L3_LOCAL",
            "mcp_execution_mode": "stdio_server",
            "stdio_server": {
                "id": "sqlite_manager",
                "command": "uvx",
                "args": [
                    "mcp-server-sqlite",
                    "--db-path",
                    "__JACHIN_WORKSPACE__/my_life_data.db",
                ],
            },
        },
    },
    {
        "dir": "com.jachin.mcp.stub.google.maps.travel",
        "plugin": {
            "id": "com.jachin.mcp.stub.google.maps.travel",
            "name": "Google Maps MCP（出行助手，stub）",
            "version": "1.0.0",
            "description": "npm @modelcontextprotocol/server-google-maps；与本机 maps_assistant 对应，需 GOOGLE_MAPS_API_KEY。",
            "type": "mcp",
            "runtime_tier": "L3_LOCAL",
            "mcp_execution_mode": "stdio_server",
            "stdio_server": {
                "id": "maps_assistant",
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-google-maps"],
                "env": {"GOOGLE_MAPS_API_KEY": "${GOOGLE_MAPS_API_KEY}"},
            },
        },
    },
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for item in STUBS:
        d = OUT / str(item["dir"])
        d.mkdir(parents=True, exist_ok=True)
        pj = d / "plugin.json"
        pj.write_text(
            json.dumps(item["plugin"], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {pj.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
