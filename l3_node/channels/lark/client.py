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
            jachin = Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin")))
            env_candidates = [
                root / ".env",
                root / "skills_repo" / "plugin" / "com.jachin.hr.recruitment" / ".env",
                jachin / "l3_mcp_cache" / "com.jachin.hr.recruitment" / ".env",
                root / "skills_repo" / "plugin" / "2-track-a-atomic-mcp" / ".env",
            ]
            try:
                from l3_node.hr_loader import _get_hr_recruitment_plugin_root
                hr_root = _get_hr_recruitment_plugin_root()
                if hr_root:
                    env_candidates.insert(2, hr_root / ".env")  # 优先 l3_mcp_cache（含 UUID 目录）
            except Exception:
                pass
            for p in env_candidates:
                if p.exists():
                    load_dotenv(p)
                    return
        except ImportError:
            pass
        load_dotenv()
    except ImportError:
        pass


def _api_base_from_domain(domain: str | None) -> str:
    """从 domain 推导 API 基地址"""
    if not domain or not str(domain).strip():
        return LARK_API_BASE
    d = str(domain).strip().rstrip("/")
    return f"{d}/open-apis" if "/open-apis" not in d else d


def get_tenant_access_token(
    app_id: str | None = None,
    app_secret: str | None = None,
    api_base: str | None = None,
) -> str:
    """获取 Lark tenant_access_token。可选传入 app_id/secret/api_base，否则从环境变量读取"""
    _ensure_dotenv_loaded()
    aid = app_id or os.environ.get("LARK_APP_ID") or os.environ.get("FEISHU_APP_ID")
    sec = app_secret or os.environ.get("LARK_APP_SECRET") or os.environ.get("FEISHU_APP_SECRET")
    if not aid or not sec:
        raise ValueError(
            "请配置 LARK_APP_ID 和 LARK_APP_SECRET（环境变量或 im_channels 配置）；"
            "或设置系统环境变量。"
        )
    try:
        import requests
    except ImportError:
        raise RuntimeError("请安装 requests: pip install requests")

    base = api_base or LARK_API_BASE
    url = f"{base}/auth/v3/tenant_access_token/internal"
    resp = requests.post(
        url, json={"app_id": aid, "app_secret": sec}, timeout=10
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Lark token 失败: {data}")
    return data["tenant_access_token"]
