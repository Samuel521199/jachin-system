"""Jachin CLI 配置 - 读取全局配置或环境变量"""

from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_DIR = Path.home() / ".jachin-cli"
CONFIG_FILE = CONFIG_DIR / "config.json"


def get_token() -> str | None:
    """从环境变量或配置文件获取 JACHIN_DEV_TOKEN"""
    token = os.environ.get("JACHIN_DEV_TOKEN", "").strip()
    if token:
        return token
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
            return (data.get("token") or "").strip() or None
        except Exception:
            pass
    return None


def get_nexus_url() -> str | None:
    """获取 Nexus 商城 base URL。未配置时返回 None，必须由调用方显式指定。"""
    url = os.environ.get("JACHIN_NEXUS_URL", "").strip()
    if url:
        return url.rstrip("/")
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
            u = (data.get("nexus_url") or "").strip()
            if u:
                return u.rstrip("/")
        except Exception:
            pass
    return None


def save_config(token: str | None = None, nexus_url: str | None = None) -> None:
    """保存配置"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
        except Exception:
            pass
    if token is not None:
        data["token"] = token
    if nexus_url is not None:
        data["nexus_url"] = nexus_url
    CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
