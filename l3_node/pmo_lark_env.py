"""
PMO 飞书会话 chat_id 与项目根 ``.env`` 加载 SSOT。

**打包机配置（与 Jachin Desktop.exe 同级 ``.env``）** 建议填写：
``LARK_APP_ID`` / ``LARK_APP_SECRET``、``PMO_PRIMARY_CHAT_ID``、``PMO_MONITOR_CHAT_ID``、
``PMO_CHANGE_ALERT_CHAT_ID`` / ``PMO_CHANGE_ALERT_MONITOR_CHAT_ID``。

首次运行会把安装目录 ``.env`` 中缺失的 PMO/Lark 键补种到 ``~/.jachin/.env``。
之后用户本机配置以 ``~/.jachin/.env`` 为准，能力包升级不会覆盖用户配置。

**读取优先级**（同名键）：``~/.jachin/.env`` 覆盖安装目录 ``.env``，再覆盖进程环境变量。
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# 战报/预警监控群内置默认（未配 PMO_MONITOR_CHAT_ID / PMO_CHANGE_ALERT_MONITOR_CHAT_ID 时）
DEFAULT_PMO_WAR_REPORT_MONITOR_CHAT_ID = "oc_0e321f92d758ecb44aea5b499c90510b"

# 变更预警内置默认（战报主群无内置默认）
DEFAULT_PMO_CHANGE_ALERT_CHAT_ID = "oc_b1b9cff6804517c79b7f5a617ab30483"

_PMO_DOTENV_LOADED = False
_PMO_USER_DOTENV_SEEDED = False
_PMO_ENV_KEY_NAMES = (
    "LARK_APP_ID",
    "LARK_APP_SECRET",
    "PMO_PRIMARY_CHAT_ID",
    "PMO_MONITOR_CHAT_ID",
    "PMO_PUSH_MONITOR",
    "PMO_CHANGE_ALERT_CHAT_ID",
    "PMO_CHANGE_ALERT_MONITOR_CHAT_ID",
    "PMO_BITABLE_WATCH_CHAT_ID",
    "PMO_BITABLE_WATCH_MONITOR_CHAT_ID",
)


def _jachin_home_dir() -> Path:
    jh = (os.environ.get("JACHIN_HOME") or "").strip()
    if jh:
        return Path(jh).expanduser()
    try:
        return Path.home() / ".jachin"
    except RuntimeError:
        return Path(os.environ.get("TEMP", os.environ.get("TMP", "/tmp"))) / ".jachin"


def _pmo_dotenv_paths() -> tuple[Path, Path]:
    from l3_node.paths import get_app_root

    return get_app_root() / ".env", _jachin_home_dir() / ".env"


def _read_dotenv_key(path: Path, key: str) -> str:
    """从单个 .env 文件读取键（不依赖 load_dotenv 顺序）。"""
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    pat = re.compile(rf"^\s*{re.escape(key)}\s*=\s*(.*?)\s*$", re.MULTILINE)
    m = pat.search(text)
    if not m:
        return ""
    raw = m.group(1).strip().strip('"').strip("'")
    if raw.startswith("#"):
        return ""
    return raw


def _read_dotenv_keys(path: Path, keys: tuple[str, ...]) -> dict[str, str]:
    return {key: _read_dotenv_key(path, key) for key in keys}


def ensure_pmo_user_dotenv_seeded() -> None:
    """
    把安装/能力包里的 PMO 配置种到 ``~/.jachin/.env``。

    这一步只补缺失键，不覆盖用户已经在 ``~/.jachin`` 里配置的值。
    这样开发模式、打包模式、L1 下载后的能力包都走同一套用户配置位置。
    """
    global _PMO_USER_DOTENV_SEEDED
    if _PMO_USER_DOTENV_SEEDED:
        return
    _PMO_USER_DOTENV_SEEDED = True

    install_path, jachin_path = _pmo_dotenv_paths()
    if not install_path.is_file() or install_path == jachin_path:
        return

    install_values = _read_dotenv_keys(install_path, _PMO_ENV_KEY_NAMES)
    missing_values = {k: v for k, v in install_values.items() if v}
    if not missing_values:
        return

    try:
        jachin_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.debug("[PMO env] skip seeding ~/.jachin/.env: mkdir failed: %s", exc)
        return

    existing_values = _read_dotenv_keys(jachin_path, _PMO_ENV_KEY_NAMES)
    to_append = [
        (key, value)
        for key, value in missing_values.items()
        if not existing_values.get(key)
    ]
    if not to_append:
        return

    try:
        existed = jachin_path.is_file()
        with jachin_path.open("a", encoding="utf-8", newline="\n") as f:
            if existed and jachin_path.stat().st_size > 0:
                f.write("\n")
            f.write("# PMO Copilot local config, seeded from installed package .env.\n")
            f.write("# Edit values here; package upgrades will not overwrite them.\n")
            for key, value in to_append:
                f.write(f"{key}={value}\n")
        logger.info(
            "[PMO env] seeded %s PMO/Lark config key(s) into %s",
            len(to_append),
            jachin_path,
        )
    except OSError as exc:
        logger.debug("[PMO env] skip seeding ~/.jachin/.env: write failed: %s", exc)


def _resolve_pmo_env_key(key: str) -> tuple[str, str]:
    """
    返回 (value, source_hint)。
    安装目录与 ~/.jachin 均有时：**统帅目录优先**（便于打包机只改 ~/.jachin/.env）。
    """
    ensure_pmo_dotenv_loaded()
    install_path, jachin_path = _pmo_dotenv_paths()
    install_val = _read_dotenv_key(install_path, key)
    jachin_val = _read_dotenv_key(jachin_path, key)
    if jachin_val:
        return jachin_val, str(jachin_path)
    if install_val:
        return install_val, str(install_path)
    env_val = (os.environ.get(key) or "").strip()
    if env_val:
        return env_val, "os.environ"
    return "", ""


def ensure_pmo_dotenv_loaded() -> None:
    """加载安装目录与 ``~/.jachin/.env`` 到 os.environ（统帅目录键 override 安装目录）。"""
    global _PMO_DOTENV_LOADED
    if _PMO_DOTENV_LOADED:
        return
    _PMO_DOTENV_LOADED = True
    ensure_pmo_user_dotenv_seeded()
    try:
        from dotenv import load_dotenv

        install_path, jachin_path = _pmo_dotenv_paths()
        if install_path.is_file():
            load_dotenv(install_path, override=False, encoding="utf-8")
        if jachin_path.is_file():
            load_dotenv(jachin_path, override=True, encoding="utf-8")
    except ImportError:
        pass
    except Exception:
        pass


def _first_env(*names: str) -> str:
    for name in names:
        if name in _PMO_ENV_KEY_NAMES:
            val, _ = _resolve_pmo_env_key(name)
            if val:
                return val
            continue
        ensure_pmo_dotenv_loaded()
        v = (os.environ.get(name) or "").strip()
        if v:
            return v
    return ""


def pmo_primary_chat_id() -> str:
    """配置的主群 chat_id（未配置则空）。"""
    val, src = _resolve_pmo_env_key("PMO_PRIMARY_CHAT_ID")
    if val:
        logger.debug("[PMO env] PMO_PRIMARY_CHAT_ID=%s from %s", val, src)
    return val


def pmo_effective_primary_chat_id(session_chat_id: str = "") -> str:
    """运行时主群：配置优先，否则飞书触发会话。"""
    configured = pmo_primary_chat_id()
    if configured:
        return configured
    return (session_chat_id or "").strip()


def pmo_monitor_chat_id() -> str:
    """战报监控群：优先 ``PMO_MONITOR_CHAT_ID``（安装根 ``.env``），否则内置默认。"""
    val, src = _resolve_pmo_env_key("PMO_MONITOR_CHAT_ID")
    if val:
        logger.debug("[PMO env] PMO_MONITOR_CHAT_ID=%s from %s", val, src)
        return val
    return DEFAULT_PMO_WAR_REPORT_MONITOR_CHAT_ID


def pmo_push_monitor_enabled() -> bool:
    """是否双群推送；监控群 ID 固定，仅 ``PMO_PUSH_MONITOR=0`` 时关闭。"""
    val, _ = _resolve_pmo_env_key("PMO_PUSH_MONITOR")
    if val.lower() in ("0", "false", "no", "off"):
        return False
    return True


def pmo_required_delivery_chat_ids(session_chat_id: str = "") -> tuple[str, ...]:
    primary = pmo_effective_primary_chat_id(session_chat_id)
    if not primary:
        return ()
    ids = [primary]
    if pmo_push_monitor_enabled():
        mon = pmo_monitor_chat_id()
        if mon and mon != primary:
            ids.append(mon)
    return tuple(ids)


def pmo_delivery_targets_debug(session_chat_id: str = "") -> dict[str, str]:
    """推送前诊断：生效目标与配置来源。"""
    primary, primary_src = _resolve_pmo_env_key("PMO_PRIMARY_CHAT_ID")
    effective = pmo_effective_primary_chat_id(session_chat_id)
    eff_src = primary_src if primary else (
        f"session:{session_chat_id}" if session_chat_id else "unset"
    )
    targets = pmo_required_delivery_chat_ids(session_chat_id)
    mon = pmo_monitor_chat_id()
    _, mon_src = _resolve_pmo_env_key("PMO_MONITOR_CHAT_ID")
    return {
        "PMO_PRIMARY_CONFIGURED": primary or "(empty)",
        "PMO_PRIMARY_SOURCE": primary_src or "(none)",
        "PMO_EFFECTIVE_PRIMARY": effective or "(empty)",
        "PMO_EFFECTIVE_SOURCE": eff_src,
        "PMO_MONITOR_CHAT_ID": mon,
        "PMO_MONITOR_SOURCE": mon_src or "code:default_pmo_war_report_monitor",
        "PMO_PUSH_MONITOR": "1" if pmo_push_monitor_enabled() else "0",
        "PMO_DELIVERY_TARGETS": ",".join(targets) if targets else "(none)",
    }


def pmo_change_alert_chat_id() -> str:
    return (
        _first_env("PMO_CHANGE_ALERT_CHAT_ID", "PMO_BITABLE_WATCH_CHAT_ID")
        or DEFAULT_PMO_CHANGE_ALERT_CHAT_ID
    )


def pmo_change_alert_monitor_chat_id() -> str:
    return (
        _first_env(
            "PMO_CHANGE_ALERT_MONITOR_CHAT_ID",
            "PMO_BITABLE_WATCH_MONITOR_CHAT_ID",
        )
        or DEFAULT_PMO_WAR_REPORT_MONITOR_CHAT_ID
    )


def pmo_lark_chat_env_summary() -> dict[str, str]:
    ensure_pmo_dotenv_loaded()
    return {
        "PMO_PRIMARY_CHAT_ID": pmo_primary_chat_id(),
        "PMO_MONITOR_CHAT_ID": pmo_monitor_chat_id(),
        "PMO_PUSH_MONITOR": "1" if pmo_push_monitor_enabled() else "0",
        "PMO_CHANGE_ALERT_CHAT_ID": pmo_change_alert_chat_id(),
        "PMO_CHANGE_ALERT_MONITOR_CHAT_ID": pmo_change_alert_monitor_chat_id(),
    }
