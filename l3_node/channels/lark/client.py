"""
Lark 通道 — 客户端与 Token 管理
"""
from __future__ import annotations

import os
from pathlib import Path

LARK_API_BASE_DEFAULT = "https://open.larksuite.com/open-apis"
FEISHU_API_BASE = "https://open.feishu.cn/open-apis"


def get_lark_api_base() -> str:
    """根据 LARK_USE_FEISHU 返回飞书中国版或 Lark 国际版 API 地址"""
    if os.environ.get("LARK_USE_FEISHU", "").lower() in ("1", "true", "yes"):
        return FEISHU_API_BASE
    return LARK_API_BASE_DEFAULT


LARK_API_BASE = "https://open.larksuite.com/open-apis"  # 兼容旧代码，新逻辑用 get_lark_api_base()


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


def _lark_creds_from_im_yaml_dict(ch: dict) -> tuple[str, str, str | None]:
    """从 im_channels.lark 字典解析 app_id、app_secret、api_base（domain）。"""
    if not isinstance(ch, dict):
        return "", "", None
    y_aid = (ch.get("app_id") or "").strip()
    y_sec = (ch.get("app_secret") or "").strip()
    dom = (ch.get("domain") or "").strip()
    api_base = _api_base_from_domain(dom) if dom else None
    return y_aid, y_sec, api_base


def resolve_lark_credentials() -> tuple[str, str, str | None]:
    """
    解析飞书应用凭证与 API 根地址。

    优先级：环境变量（含 .env）→ ~/.jachin/config/im_channels.yaml（`im_channels.lark`，与长连接同源）。

    返回 (app_id, app_secret, api_base)；api_base 为 None 时调用方应使用 get_lark_api_base()。
    """
    _ensure_dotenv_loaded()
    aid = (os.environ.get("LARK_APP_ID") or os.environ.get("FEISHU_APP_ID") or "").strip()
    sec = (os.environ.get("LARK_APP_SECRET") or os.environ.get("FEISHU_APP_SECRET") or "").strip()
    if aid and sec:
        return aid, sec, None

    try:
        from l3_node.im_channels.config import load_config

        ch = (load_config().get("im_channels") or {}).get("lark") or {}
        y_aid, y_sec, y_base = _lark_creds_from_im_yaml_dict(ch)
        if y_aid and y_sec:
            return y_aid, y_sec, y_base
    except Exception:
        pass

    return "", "", None


def is_lark_api_configured(
    app_id: str | None = None,
    app_secret: str | None = None,
) -> bool:
    """
    是否已配置飞书/Lark 应用凭证（环境变量、.env、~/.jachin/config/im_channels.yaml）。
    用于在未配置时跳过多维表同步，避免抛错与 ERROR 级堆栈。
    """
    _ensure_dotenv_loaded()
    aid = (app_id or os.environ.get("LARK_APP_ID") or os.environ.get("FEISHU_APP_ID") or "").strip()
    sec = (app_secret or os.environ.get("LARK_APP_SECRET") or os.environ.get("FEISHU_APP_SECRET") or "").strip()
    if aid and sec:
        return True
    r_aid, r_sec, _ = resolve_lark_credentials()
    return bool(r_aid and r_sec)


def get_tenant_access_token(
    app_id: str | None = None,
    app_secret: str | None = None,
    api_base: str | None = None,
) -> str:
    """获取 Lark tenant_access_token。显式传入 app_id+app_secret 时仅用二者；否则走 resolve_lark_credentials（含 YAML）。"""
    _ensure_dotenv_loaded()
    if app_id and app_secret:
        aid, sec = str(app_id).strip(), str(app_secret).strip()
        base = api_base or get_lark_api_base()
    else:
        aid, sec, yb = resolve_lark_credentials()
        if not aid or not sec:
            raise ValueError(
                "请配置 LARK_APP_ID 和 LARK_APP_SECRET（环境变量、.env，"
                "或 ~/.jachin/config/im_channels.yaml 的 im_channels.lark.app_id / app_secret）。"
            )
        base = api_base or yb or get_lark_api_base()
    try:
        import requests
    except ImportError:
        raise RuntimeError("请安装 requests: pip install requests")

    url = f"{base}/auth/v3/tenant_access_token/internal"
    resp = requests.post(
        url, json={"app_id": aid, "app_secret": sec}, timeout=10
    )
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Lark token 失败: {data}")
    return data["tenant_access_token"]
