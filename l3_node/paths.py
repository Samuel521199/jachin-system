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
    开发时：默认以本文件所在仓库根为准；若设置了 JACHIN_APP_ROOT 且目录下含 l3_node 或 skills_repo，则优先采用（避免
    多套 Python / 错误 cwd 导致 l3_node 包不在本仓库时找不到 skills_repo/plugin）。
    """
    env_root = (os.environ.get("JACHIN_APP_ROOT") or "").strip()
    if env_root:
        p = Path(env_root).expanduser().resolve()
        if p.is_dir() and ((p / "l3_node").is_dir() or (p / "skills_repo").is_dir()):
            return p

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        # 侧车在 bin/ 时应用根为上一级（含 .env、config、workspace 占位），勿把 bin 当根
        if exe_dir.name.lower() == "bin" and exe_dir.parent.is_dir():
            return exe_dir.parent
        parent = exe_dir.parent
        cwd = Path.cwd()
        for cand in (exe_dir, parent, cwd, cwd.parent):
            if cand.exists():
                return cand
        return parent
    return Path(__file__).resolve().parent.parent


def k11_unified_smoke_script_path() -> Path:
    """
    ``scripts/test_k11_unified_platform_smoke_playwright.py``（统合冒烟 Playwright）。

    规则同 ``kalaroko_default_e2e_script_path``：frozen 优先 ``_MEIPASS/scripts``，
    否则便携目录 ``get_app_root()/scripts``，最后仓库根。
    """
    fname = "test_k11_unified_platform_smoke_playwright.py"
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        p = Path(sys._MEIPASS) / "scripts" / fname
        if p.is_file():
            return p
    portable = get_app_root() / "scripts" / fname
    if portable.is_file():
        return portable
    return Path(__file__).resolve().parent.parent / "scripts" / fname


def k11_p2_compat_weaknet_script_path() -> Path:
    """``scripts/test_k11_p2_compat_weaknet_playwright.py``（P2 浏览器兼容段）。"""
    fname = "test_k11_p2_compat_weaknet_playwright.py"
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        p = Path(sys._MEIPASS) / "scripts" / fname
        if p.is_file():
            return p
    portable = get_app_root() / "scripts" / fname
    if portable.is_file():
        return portable
    return Path(__file__).resolve().parent.parent / "scripts" / fname


def k11_game_open_smoke_script_path() -> Path:
    """``scripts/test_k11_game_open_smoke.py``（游戏模块开门冒烟）。"""
    fname = "test_k11_game_open_smoke.py"
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        p = Path(sys._MEIPASS) / "scripts" / fname
        if p.is_file():
            return p
    portable = get_app_root() / "scripts" / fname
    if portable.is_file():
        return portable
    return Path(__file__).resolve().parent.parent / "scripts" / fname


def kalaroko_default_e2e_script_path() -> Path:
    """
    ``scripts/test_kalaroko_default_scenarios_e2e.py`` 的绝对路径。

    - **frozen**：PyInstaller 将脚本 ``--add-data`` 到 ``sys._MEIPASS/scripts/``（见 ``build_l3_sidecar.py``）。
    - **便携目录**：若 ``get_app_root()/scripts/`` 下存在同名文件（如手工拷贝），优先使用。
    - **开发**：仓库根 ``<repo>/scripts/``。
    """
    fname = "test_kalaroko_default_scenarios_e2e.py"
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        p = Path(sys._MEIPASS) / "scripts" / fname
        if p.is_file():
            return p
    portable = get_app_root() / "scripts" / fname
    if portable.is_file():
        return portable
    return Path(__file__).resolve().parent.parent / "scripts" / fname
