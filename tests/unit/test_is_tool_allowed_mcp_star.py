"""is_tool_allowed：mcp:* 通配（L2 白名单 + 本地 MCP 合并）。"""

from l3_node.primitives.tools.loader import is_tool_allowed


def test_mcp_star_allows_any_mcp_prefixed_id() -> None:
    allowed = ["com.jachin.example", "mcp:*"]
    assert is_tool_allowed("mcp:puppeteer_navigate", allowed) is True
    assert is_tool_allowed("mcp:browser_click", allowed) is True


def test_mcp_star_not_in_list_blocks_mcp() -> None:
    allowed = ["com.jachin.example"]
    assert is_tool_allowed("mcp:puppeteer_navigate", allowed) is False


def test_none_allowed_skills_allows_all() -> None:
    assert is_tool_allowed("mcp:anything", None) is True
