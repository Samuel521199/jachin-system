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

_PLUGIN_DOTENV_MERGED = False

# 供 403 / scope 后「强制换发」占位：当前每次请求均 POST 新 token；若将来增加短期内存缓存，在此 bump 后代际失效。
_lark_tenant_token_epoch: int = 0


def bump_lark_tenant_token_epoch() -> None:
    """使后续逻辑可感知「已要求丢弃旧代 token」（排查日志、未来缓存键）。"""
    global _lark_tenant_token_epoch
    _lark_tenant_token_epoch += 1


def invalidate_lark_tenant_token_cache() -> None:
    """
    飞书开放平台 tenant_access_token 在**本进程**若将来做短期缓存，在此清空。
    当前实现为每次 ``get_tenant_access_token`` 均重新 POST；403/权限刚发布时仍调用本函数
    并配合 ``time.sleep`` 重试，以满足「先失效再换发」的操作顺序与可观测性。
    """
    bump_lark_tenant_token_epoch()


def _ensure_dotenv_loaded() -> None:
    """合并 skills_repo/plugin/.env 等路径，补全 LARK_CHAT_ID 等。

    第一轮 ``override=False``：不覆盖进程里已有变量（兼容 L2 注入）。
    第二轮仅对 ``skills_repo/plugin/.env`` 使用 ``override=True``（可用 ``JACHIN_IGNORE_PLUGIN_LARK=1`` 跳过）：
    避免用户曾在**系统/用户级环境变量**里导出过旧的 ``LARK_APP_ID``（如 HR 应用），导致与
    ``ou_`` 不同源的 **open_id cross app**；仓库内 plugin/.env 通常为显式飞书配置，应优先生效。
    """
    global _PLUGIN_DOTENV_MERGED
    if _PLUGIN_DOTENV_MERGED:
        return
    _PLUGIN_DOTENV_MERGED = True
    try:
        from dotenv import load_dotenv

        from l3_node.paths import get_app_root

        root = get_app_root()
        jachin = Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin")))
        paths: list[Path] = [
            root / "skills_repo" / "plugin" / ".env",
            root / "skills_repo" / "plugin" / "com.jachin.hr.recruitment" / ".env",
            jachin / "l3_mcp_cache" / "com.jachin.hr.recruitment" / ".env",
        ]
        try:
            from l3_node.hr_loader import _get_hr_recruitment_plugin_root

            hr_root = _get_hr_recruitment_plugin_root()
            if hr_root:
                paths.insert(1, hr_root / ".env")
        except Exception:
            pass
        for p in paths:
            if p.is_file():
                load_dotenv(p, override=False)
        _pl = root / "skills_repo" / "plugin" / ".env"
        if _pl.is_file():
            _ign = (os.environ.get("JACHIN_IGNORE_PLUGIN_LARK") or "").strip().lower()
            if _ign not in ("1", "true", "yes", "on"):
                load_dotenv(_pl, override=True)
    except ImportError:
        pass
    except Exception:
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

    **约定**：`LARK_APP_ID` / `LARK_APP_SECRET` 表示 **通用/默认** 机器人（终端镜像、util:lark_send_text、与用户 open_id 同应用）；
    HR 招聘专用应用请用 `resolve_hr_lark_credentials()`，避免与通用 open_id 混用导致 ``open_id cross app``。
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


def resolve_hr_lark_credentials() -> tuple[str, str, str | None]:
    """
    HR 招聘插件专用应用（多维表、atom_lark_*、与招聘场景绑定的回调）。

    优先级：**HR_LARK_APP_ID** + **HR_LARK_APP_SECRET** → 回退 ``resolve_lark_credentials()``（兼容仅配置一套凭证的旧环境）。
    """
    _ensure_dotenv_loaded()
    aid = (os.environ.get("HR_LARK_APP_ID") or os.environ.get("FEISHU_HR_APP_ID") or "").strip()
    sec = (os.environ.get("HR_LARK_APP_SECRET") or os.environ.get("FEISHU_HR_APP_SECRET") or "").strip()
    if aid and sec:
        return aid, sec, None
    return resolve_lark_credentials()


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
    """获取 Lark tenant_access_token。显式传入 app_id+app_secret 时仅用二者；否则走 resolve_lark_credentials（含 YAML）。

    每次调用都会向开放平台请求新 token，**不在本地持久化缓存**；权限变更后无需「删缓存文件」，但租户侧可能需重新授权应用。
    """
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
