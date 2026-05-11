#!/usr/bin/env python3
"""
GameQA MCP Server — 本地物理网关（stdio / FastMCP）。

**L3 侧**：同名的 ``mcp:tool_*`` 已在 ``l3_node/primitives/mcp/registry.py`` 注册为 **进程内** 调用，
与 HTTP ``/api/v1/gameqa``、``session_service`` 单例一致；本条目的 stdio 仍可供 Cursor / 外部 MCP 宿主使用。

与同目录其他 L3 本地 MCP 一致（``l3_client/local_mcps/``）。

运行（仓库根目录）::

  python -m l3_client.local_mcps.gameqa_mcp.server

若需短入口亦可::

  python -m l3_client.local_mcps.gameqa_mcp

环境变量::
  GAMEQA_DATA_DIR   日志目录，默认 ~/.gameqa_mcp
  GAMEQA_KNOWLEDGE_ROOT  若设置，tool_read_knowledge 仅允许该目录下的 .md
  GAMEQA_REMOTE_DEBUG_PORT / GAMEQA_REMOTE_DEBUG_HOST  launch 时开放 CDP（默认 127.0.0.1:9222，与 scripts/launch_chrome_debug.ps1 一致）
  GAMEQA_CDP_URL    跳过 launch，优先 connect_over_cdp
  KALAROKO_CDP_ENDPOINT  未设 GAMEQA_CDP_URL 时亦可作附着地址（与 .env / K11 冒烟共用）
  GAMEQA_FORCE_NEW_BROWSER=1   丢弃共享端点文件并强制新起 Chromium（调试冲突时用）
  GAMEQA_YOLO_MODEL   （可选）Ultralytics/YOLO 权重路径或 hub 名（如 ``yolo11n.pt``）；未设则视觉为 Mock
  GAMEQA_YOLO_CONF / GAMEQA_YOLO_DEVICE / GAMEQA_YOLO_IMG_SIZE   参见 ``vision_engine.py``
"""
from __future__ import annotations

import json
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("gameqa_mcp")

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    logger.error("请安装 mcp: pip install mcp")
    sys.exit(1)

from .session_service import get_gameqa_service

try:
    mcp = FastMCP(
        "gameqa-physical-gateway",
        description="网页游戏 QA 物理网关：语义状态、点击执行、影子示教 JSONL。",
    )
except TypeError:
    mcp = FastMCP("gameqa-physical-gateway")


def _svc():
    return get_gameqa_service()


@mcp.tool(name="tool_read_knowledge")
def tool_read_knowledge(file_path: str) -> str:
    """读取本地规则 Markdown，供 Agent 注入知识层。"""
    raw = _svc().read_knowledge(file_path)
    return json.dumps(raw, ensure_ascii=False)


@mcp.tool(name="tool_launch_test_mode")
async def tool_launch_test_mode(url: str) -> str:
    """启动无头浏览器，进入自治 QA 模式。"""
    raw = await _svc().launch_test(url)
    return json.dumps(raw, ensure_ascii=False)


@mcp.tool(name="tool_launch_shadow_mode")
async def tool_launch_shadow_mode(url: str) -> str:
    """启动有头浏览器并注入点击监听，无感写入 training_data.jsonl。"""
    raw = await _svc().launch_shadow(url)
    return json.dumps(raw, ensure_ascii=False)


@mcp.tool(name="tool_refresh_view")
async def tool_refresh_view(url: str = "") -> str:
    """当前标签 K11 式刷新（稳健 goto / 冷导航）；url 空=硬刷新当前页，非空=goto；不经 launch / 文件锁。"""
    raw = await _svc().refresh_view(url)
    return json.dumps(raw, ensure_ascii=False)


@mcp.tool(name="tool_get_semantic_state")
async def tool_get_semantic_state() -> str:
    """截图 → 视觉解析 → 更新语义映射 → 返回精简 JSON。"""
    raw = await _svc().get_semantic_state()
    return json.dumps(raw, ensure_ascii=False)


@mcp.tool(name="tool_execute_action")
async def tool_execute_action(element_name: str) -> str:
    """按语义名查表点击视口坐标，并追加 audit_trail.jsonl。"""
    raw = await _svc().execute_action(element_name)
    return json.dumps(raw, ensure_ascii=False)


@mcp.tool(name="tool_get_audit_log")
def tool_get_audit_log() -> str:
    """返回本轮写入的 audit_trail.jsonl 全文（脚手架；大规模时可改为 run_id 分文件）。"""
    raw = _svc().get_audit_log()
    return json.dumps(raw, ensure_ascii=False)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
