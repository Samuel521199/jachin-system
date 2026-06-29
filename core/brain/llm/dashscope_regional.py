"""
DashScope 区域化凭证（中国区 CN vs 东南亚/国际 SEA）。

由 JACHIN_ACTIVE_REGION 选择环境变量族，并与旧变量 DASHSCOPE_API_KEY / DASHSCOPE_API_BASE 兼容。
"""

from __future__ import annotations

import os
from typing import Any, Optional

# LiteLLM / OpenAI 兼容模式默认入口（可被 DASHSCOPE_API_BASE_* 覆盖）
_DEFAULT_BASE_CN = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_DEFAULT_BASE_SEA = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


def _active_region_str() -> str:
    """JACHIN_ACTIVE_REGION：环境变量优先，否则 core.config.settings。"""
    r = os.getenv("JACHIN_ACTIVE_REGION", "").strip().upper()
    if r == "SEA" and not (os.getenv("DASHSCOPE_API_KEY_SEA") or "").strip():
        if (os.getenv("DASHSCOPE_API_KEY_CN") or "").strip():
            return "CN"
    if r:
        return r
    try:
        from core.config import settings

        return (getattr(settings, "JACHIN_ACTIVE_REGION", None) or "CN").strip().upper() or "CN"
    except Exception:
        return "CN"


def get_jachin_active_region() -> str:
    """当前活跃区域标识：CN | SEA（大写）。"""
    return _active_region_str()


def user_configured_regional_dashscope_key_env() -> bool:
    """
    用户是否为当前区域单独配置了 DASHSCOPE_API_KEY_SEA / DASHSCOPE_API_KEY_CN。
    若为真，则不得让 L2 SecurityContext 下发的国内 Key 覆盖（否则 SEA + intl base + 国服 sk → 401）。
    """
    r = _active_region_str()
    if r == "SEA":
        return bool((os.getenv("DASHSCOPE_API_KEY_SEA") or "").strip())
    if r == "CN":
        return bool((os.getenv("DASHSCOPE_API_KEY_CN") or "").strip())
    return False


def get_dashscope_regional_credentials() -> tuple[Optional[str], Optional[str]]:
    """
    根据 JACHIN_ACTIVE_REGION 返回 (api_key, api_base)。

    - CN：DASHSCOPE_API_KEY_CN / DASHSCOPE_API_BASE_CN
    - SEA：DASHSCOPE_API_KEY_SEA / DASHSCOPE_API_BASE_SEA
    - 若区域专用 Key 未配置，回退 DASHSCOPE_API_KEY、QWEN_*、DASHSCOPE_API_BASE
    """
    active_region = _active_region_str()

    if active_region == "SEA":
        api_key = (os.getenv("DASHSCOPE_API_KEY_SEA") or "").strip() or None
        api_base = (os.getenv("DASHSCOPE_API_BASE_SEA") or "").strip() or None
        default_base = _DEFAULT_BASE_SEA
    else:
        api_key = (os.getenv("DASHSCOPE_API_KEY_CN") or "").strip() or None
        api_base = (os.getenv("DASHSCOPE_API_BASE_CN") or "").strip() or None
        default_base = _DEFAULT_BASE_CN

    if not api_key:
        api_key = (
            (os.getenv("DASHSCOPE_API_KEY") or "").strip()
            or (os.getenv("QWEN_API_KEY") or "").strip()
            or (os.getenv("QWEN_AI_API_KEY") or "").strip()
        ) or None

    if not api_base:
        api_base = (os.getenv("DASHSCOPE_API_BASE") or "").strip() or None
    if not api_base:
        api_base = default_base

    return (api_key, api_base)


def get_dashscope_regional_api_base() -> str:
    """当前区域对应的默认/显式 api base（必有非空字符串）。"""
    _, b = get_dashscope_regional_credentials()
    return b or _DEFAULT_BASE_CN


def _litellm_model_uses_dashscope(model: str) -> bool:
    m = (model or "").lower()
    if "dashscope" in m or "qwen" in m:
        return True
    return m.startswith("qwen/") or m.startswith("qwen-")


def litellm_apply_dashscope_credentials(
    model: str,
    kwargs_chat: dict[str, Any],
    *,
    explicit_api_key: Optional[str] = None,
) -> None:
    """
    对 LiteLLM 调用参数字典注入 api_key / api_base。
    explicit_api_key：例如 L3 SecurityContext 解密后的 Key（多为 L2 同步的国内 sk）。
    若用户已为当前区域配置 DASHSCOPE_API_KEY_SEA / DASHSCOPE_API_KEY_CN，**不得**用 explicit 覆盖，
    否则会出现国际 endpoint + 国内 Key → 401 Incorrect API key。
    """
    if not _litellm_model_uses_dashscope(model):
        return
    key, base = get_dashscope_regional_credentials()
    if explicit_api_key and str(explicit_api_key).strip():
        if not user_configured_regional_dashscope_key_env():
            key = str(explicit_api_key).strip()
    elif not key:
        key = (os.getenv("DASHSCOPE_API_KEY") or "").strip() or None
        if not key:
            try:
                from core.brain.llm.credential_loader import get_dashscope_key

                _k = get_dashscope_key()
                if _k:
                    key = str(_k).strip()
            except ImportError:
                pass
    if key:
        kwargs_chat["api_key"] = key
    if base:
        kwargs_chat["api_base"] = base
