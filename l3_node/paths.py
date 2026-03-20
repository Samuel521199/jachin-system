"""
L3 路径解析：支持 PyInstaller 打包与开发模式

PyInstaller 时 __file__ 指向 _MEIPASS 临时目录，skills_repo 不在其中。
frozen 模式：exe 仅含 agent+im，基于 exe 路径、cwd 或 JACHIN_APP_ROOT 推导，不再依赖 skills_repo/plugin。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_PLUGIN_HR = Path("skills_repo") / "plugin" / "com.jachin.hr.recruitment"


def _has_plugin_root(p: Path) -> bool:
    """检查路径下是否存在有效插件（开发模式用）。"""
    return (p / _PLUGIN_HR).exists()


def get_app_root() -> Path:
    """
    获取应用根目录。
    frozen 时：基于 exe 路径、cwd 或 JACHIN_APP_ROOT，不依赖 skills_repo/plugin。
    开发时：返回项目根（含 skills_repo/plugin/）。
    """
    if getattr(sys, "frozen", False):
        env_root = os.environ.get("JACHIN_APP_ROOT")
        if env_root:
            return Path(env_root).resolve()
        exe_dir = Path(sys.executable).resolve().parent
        parent = exe_dir.parent
        cwd = Path.cwd()
        # 便携包：exe 在 bin/，父级为 dist 根
        for p in (exe_dir, parent, cwd, cwd.parent):
            if p.exists():
                return p
        return parent
    return Path(__file__).resolve().parent.parent
