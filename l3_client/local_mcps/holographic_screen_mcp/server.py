#!/usr/bin/env python3
"""
Holographic Screen MCP — OmniParser 全息屏幕 + PyAutoGUI（stdio / FastMCP）。

**L3 侧**：``mcp:get_holographic_screen`` / ``mcp:physical_click`` 在
``l3_node/primitives/mcp/registry.py`` 进程内调用，与本 stdio 服务共用 ``session_service``。

运行（仓库根目录，须使用 OmniParser venv）::

  .\\.venv-omniparser\\Scripts\\python.exe -m l3_client.local_mcps.holographic_screen_mcp.server

环境变量::
  OMNIPARSER_MODEL_DIR     OmniParser-v2.0 根目录
  OMNIPARSER_PYTHON        推理用 Python（默认 .venv-omniparser）
  OMNIPARSER_BBOX_THRESHOLD / OMNIPARSER_IOU_THRESHOLD
  HOLOGRAPHIC_SCREEN_DATA_DIR  解析落盘目录
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
logger = logging.getLogger("holographic_mcp")

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    logger.error("请安装 mcp: pip install mcp")
    sys.exit(1)

from .session_service import get_holographic_screen_service

try:
    mcp = FastMCP(
        "holographic-screen",
        description="OmniParser 全息屏幕：截屏编号图 + 物理点击闭环。",
    )
except TypeError:
    mcp = FastMCP("holographic-screen")


def _svc():
    return get_holographic_screen_service()


@mcp.tool(name="get_holographic_screen")
def get_holographic_screen(
    bbox_threshold: float = 0.05,
    iou_threshold: float = 0.7,
) -> str:
    """
    截取全桌面 → OmniParser 解析 → 返回带红框编号的标注图（多模态）+
    精简 elements JSON（id / center_x / center_y）。
    """
    return _svc().get_holographic_screen(
        bbox_threshold=bbox_threshold,
        iou_threshold=iou_threshold,
    )


@mcp.tool(name="physical_click")
def physical_click(
    element_id: int,
    double_click: bool = False,
    button: str = "left",
) -> str:
    """按 get_holographic_screen 返回的 element id 在屏幕中心点击。"""
    return _svc().physical_click(
        element_id=element_id,
        double_click=double_click,
        button=button,
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
