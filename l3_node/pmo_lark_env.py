"""
PMO 飞书会话 chat_id 与项目根 ``.env`` 加载 SSOT。

战报（分支 A）与变更预警（change-alert）推送目标均优先读 **项目根** ``.env``，
其次 ``~/.jachin/.env``；未配置时使用内置默认值。

环境变量：
- ``PMO_PRIMARY_CHAT_ID`` — 主线战报主群
- ``PMO_MONITOR_CHAT_ID`` — 主线战报监控群（亦作预警监控群回退）
- ``PMO_PUSH_MONITOR`` — 设为 ``0``/``false`` 时战报只推主群（打包机 B 等单群场景）
- ``PMO_CHANGE_ALERT_CHAT_ID`` — 变更预警主推送群（兼容 ``PMO_BITABLE_WATCH_CHAT_ID``）
- ``PMO_CHANGE_ALERT_MONITOR_CHAT_ID`` — 变更预警监控群（兼容 ``PMO_BITABLE_WATCH_MONITOR_CHAT_ID``）
"""
from __future__ import annotations

import os
from pathlib import Path

# 内置默认（仅 .env / YAML 均未配置时）
DEFAULT_PMO_PRIMARY_CHAT_ID = "oc_437c98d11106295fb10751a5481ee465"
DEFAULT_PMO_MONITOR_CHAT_ID = "oc_0e321f92d758ecb44aea5b499c90510b"
DEFAULT_PMO_CHANGE_ALERT_CHAT_ID = "oc_b1b9cff6804517c79b7f5a617ab30483"

_PMO_DOTENV_LOADED = False


def ensure_pmo_dotenv_loaded() -> None:
    """加载项目根 ``.env`` 与 ``~/.jachin/.env``（不覆盖进程已有变量）。"""
    global _PMO_DOTENV_LOADED
    if _PMO_DOTENV_LOADED:
        return
    _PMO_DOTENV_LOADED = True
    try:
        from dotenv import load_dotenv

        from l3_node.paths import get_app_root

        root = get_app_root()
        jachin = Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin"))).expanduser()
        for p in (root / ".env", jachin / ".env"):
            if p.is_file():
                load_dotenv(p, override=False, encoding="utf-8")
    except ImportError:
        pass
    except Exception:
        pass


def _first_env(*names: str) -> str:
    ensure_pmo_dotenv_loaded()
    for name in names:
        v = (os.environ.get(name) or "").strip()
        if v:
            return v
    return ""


def pmo_primary_chat_id() -> str:
    """主线战报 · 主群 chat_id。"""
    return _first_env("PMO_PRIMARY_CHAT_ID") or DEFAULT_PMO_PRIMARY_CHAT_ID


def pmo_monitor_chat_id() -> str:
    """主线战报 · 监控群 chat_id。"""
    return _first_env("PMO_MONITOR_CHAT_ID") or DEFAULT_PMO_MONITOR_CHAT_ID


def pmo_push_monitor_enabled() -> bool:
    """是否要求监控群双推（``PMO_PUSH_MONITOR=0`` 时仅主群）。"""
    ensure_pmo_dotenv_loaded()
    v = (os.environ.get("PMO_PUSH_MONITOR") or "").strip().lower()
    return v not in ("0", "false", "no", "off")


def pmo_required_delivery_chat_ids() -> tuple[str, ...]:
    """
    宏观看板「投递完成」须 success 的 chat_id 列表（与 macro_dashboard_push / 宿主校验一致）。
    单群部署：``PMO_PRIMARY_CHAT_ID=oc_…`` + ``PMO_PUSH_MONITOR=0``。
    """
    primary = pmo_primary_chat_id()
    ids = [primary]
    if pmo_push_monitor_enabled():
        mon = pmo_monitor_chat_id()
        if mon and mon != primary:
            ids.append(mon)
    return tuple(ids)


def pmo_change_alert_chat_id() -> str:
    """变更预警 · 主推送群 chat_id。"""
    return (
        _first_env("PMO_CHANGE_ALERT_CHAT_ID", "PMO_BITABLE_WATCH_CHAT_ID")
        or DEFAULT_PMO_CHANGE_ALERT_CHAT_ID
    )


def pmo_change_alert_monitor_chat_id() -> str:
    """变更预警 · 监控群 chat_id（双群推送时用）。"""
    return (
        _first_env(
            "PMO_CHANGE_ALERT_MONITOR_CHAT_ID",
            "PMO_BITABLE_WATCH_MONITOR_CHAT_ID",
            "PMO_MONITOR_CHAT_ID",
        )
        or DEFAULT_PMO_MONITOR_CHAT_ID
    )


def pmo_lark_chat_env_summary() -> dict[str, str]:
    """当前生效的 chat_id 与对应 .env 键（供日志 / 调试）。"""
    ensure_pmo_dotenv_loaded()
    return {
        "PMO_PRIMARY_CHAT_ID": pmo_primary_chat_id(),
        "PMO_MONITOR_CHAT_ID": pmo_monitor_chat_id(),
        "PMO_PUSH_MONITOR": "1" if pmo_push_monitor_enabled() else "0",
        "PMO_REQUIRED_DELIVERY_CHAT_IDS": ",".join(pmo_required_delivery_chat_ids()),
        "PMO_CHANGE_ALERT_CHAT_ID": pmo_change_alert_chat_id(),
        "PMO_CHANGE_ALERT_MONITOR_CHAT_ID": pmo_change_alert_monitor_chat_id(),
    }
