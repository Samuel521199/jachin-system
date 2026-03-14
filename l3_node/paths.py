"""
L3 路径解析：支持 PyInstaller 打包与开发模式

PyInstaller 时 __file__ 指向 _MEIPASS 临时目录，skills_repo 不在其中。
需用 sys.executable、cwd 或 JACHIN_APP_ROOT 推导实际应用根目录。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_PLUGIN_MCP = Path("skills_repo") / "plugin" / "2-track-a-atomic-mcp"


def get_app_root() -> Path:
    """
    获取应用根目录（含 skills_repo/plugin/2-track-a-atomic-mcp）。
    frozen 时不能用 __file__，需用 exe 路径或 cwd 推导。
    """
    if getattr(sys, "frozen", False):
        env_root = os.environ.get("JACHIN_APP_ROOT")
        if env_root:
            p = Path(env_root).resolve()
            if (p / _PLUGIN_MCP).exists():
                return p
        exe_dir = Path(sys.executable).resolve().parent
        if (exe_dir / _PLUGIN_MCP).exists():
            return exe_dir
        parent = exe_dir.parent
        if (parent / _PLUGIN_MCP).exists():
            return parent
        cwd = Path.cwd()
        if (cwd / _PLUGIN_MCP).exists():
            return cwd
        if (cwd.parent / _PLUGIN_MCP).exists():
            return cwd.parent
        return parent
    return Path(__file__).resolve().parent.parent
