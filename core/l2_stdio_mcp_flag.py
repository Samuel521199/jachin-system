"""
L2 是否在**本机**拉起 stdio MCP（MCPManager + inventory 侧载）。

长期架构：默认 **关闭**（L3 内嵌 stdio MCP，L2 仅 TaskManager + 委托）。
兼容回滚：设置环境变量 ``JACHIN_L2_STDIO_MCP=1``。
"""
from __future__ import annotations

import os


def l2_stdio_mcp_enabled() -> bool:
    return os.environ.get("JACHIN_L2_STDIO_MCP", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
