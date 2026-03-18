"""
Lark 通道 — 客户端与 Token 管理
"""
from __future__ import annotations

import os
from pathlib import Path

LARK_API_BASE = "https://open.larksuite.com/open-apis"


def _ensure_dotenv_loaded() -> None:
    """若 LARK 相关变量未设置，尝试从项目根或插件 .env 加载"""
    if os.environ.get("LARK_APP_ID") or os.environ.get("FEISHU_APP_ID"):
        return
    try:
        from dotenv import load_dotenv

        try:
            from l3_node.paths import get_app_root

            root = get_app_root()
            for p in [
                root / ".env",
                root / "skills_repo" / "plugin" / "2-track-a-atomic-mcp" / ".env",
            ]:
                if p.exists():
                    load_dotenv(p)
                    return
        except ImportError:
            pass
        load_dotenv()
    except ImportError:
        pass


def get_tenant_access_token() -> str:
    """获取 Lark tenant_access_token"""
    _ensure_dotenv_loaded()
    app_id = os.environ.get("LARK_APP_ID") or os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("LARK_APP_SECRET") or os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        raise ValueError(
            "请配置环境变量 LARK_APP_ID 和 LARK_APP_SECRET，并在项目根 .env 中填写；"
            "或设置系统环境变量。"
        )
    try:
        import requests
    except ImportError:
        raise RuntimeError("请安装 requests: pip install requests")

    url = f"{LARK_API_BASE}/auth/v3/tenant_access_token/internal"
    resp = requests.post(
        url, json={"app_id": app_id, "app_secret": app_secret}, timeout=10
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Lark token 失败: {data}")
    return data["tenant_access_token"]
