"""持久化关键环境变量到 ~/.jachin/.env（及可选项目根 .env）。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple


def _project_root_env_path() -> Optional[Path]:
    """推断项目根 .env（与 l3_node 同仓运行时）。"""
    try:
        here = Path(__file__).resolve()
        root = here.parent.parent
        p = root / ".env"
        if p.parent.is_dir():
            return p
    except Exception:
        pass
    return None


def persist_jachin_active_region(region: str) -> Tuple[bool, Optional[str]]:
    """
    将 JACHIN_ACTIVE_REGION 写入 dotenv 并更新当前进程 os.environ。
    region: CN | SEA（大小写不敏感）
    """
    r = (region or "CN").strip().upper()
    if r not in ("CN", "SEA"):
        return False, "region must be CN or SEA"
    try:
        from dotenv import set_key
    except ImportError:
        return False, "python-dotenv not installed"

    os.environ["JACHIN_ACTIVE_REGION"] = r

    home_env = Path.home() / ".jachin" / ".env"
    try:
        home_env.parent.mkdir(parents=True, exist_ok=True)
        set_key(str(home_env), "JACHIN_ACTIVE_REGION", r)
    except OSError as e:
        return False, f"write ~/.jachin/.env: {e}"

    proj = _project_root_env_path()
    if proj is not None:
        try:
            proj.parent.mkdir(parents=True, exist_ok=True)
            set_key(str(proj), "JACHIN_ACTIVE_REGION", r)
        except OSError as e:
            return False, f"write project .env: {e}"

    return True, None
