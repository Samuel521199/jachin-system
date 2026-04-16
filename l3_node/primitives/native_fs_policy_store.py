"""
Native 文件系统策略（用户扩展）：写入白名单额外根目录、读取黑名单额外根目录。

持久化：~/.jachin/config/native_fs_policy.json（与桌面控制台共用）。
内置规则仍分别在 native_write_allowlist / fs_path_blacklist 中；本模块仅合并「用户扩展」。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("l3_node")

_POLICY_PATH = Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin"))) / "config" / "native_fs_policy.json"

_mtime_cache: float | None = None
_raw_cache: dict[str, Any] | None = None


def policy_path() -> Path:
    return _POLICY_PATH


def _load_raw_uncached() -> dict[str, Any]:
    p = _POLICY_PATH
    if not p.is_file():
        return {"version": 1, "write_allowlist_extra": [], "read_blacklist_extra": []}
    try:
        text = p.read_text(encoding="utf-8")
        data = json.loads(text)
        if not isinstance(data, dict):
            return {"version": 1, "write_allowlist_extra": [], "read_blacklist_extra": []}
        return data
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("[native_fs_policy] 读取失败 %s: %s", p, e)
        return {"version": 1, "write_allowlist_extra": [], "read_blacklist_extra": []}


def load_raw() -> dict[str, Any]:
    """带 mtime 缓存的原始配置。"""
    global _mtime_cache, _raw_cache
    try:
        m = _POLICY_PATH.stat().st_mtime
    except OSError:
        m = -1.0
    if _raw_cache is not None and _mtime_cache == m:
        return _raw_cache
    _mtime_cache = m
    _raw_cache = _load_raw_uncached()
    return _raw_cache


def invalidate_cache() -> None:
    global _mtime_cache, _raw_cache
    _mtime_cache = None
    _raw_cache = None


def _validate_extra_root(s: str) -> Path | None:
    raw = (s or "").strip()
    if not raw:
        return None
    try:
        p = Path(raw).expanduser().resolve()
    except (OSError, RuntimeError, ValueError) as e:
        logger.debug("[native_fs_policy] 忽略无效路径 %r: %s", s, e)
        return None
    if not p.is_absolute():
        return None
    return p


def get_write_allowlist_extra_roots() -> list[Path]:
    """用户配置的、允许 Native 写入的额外根目录（已 resolve、去重）。"""
    data = load_raw()
    raw_list = data.get("write_allowlist_extra") or []
    if not isinstance(raw_list, list):
        return []
    roots: list[Path] = []
    seen: set[str] = set()
    for item in raw_list:
        if isinstance(item, str):
            p = _validate_extra_root(item)
        elif isinstance(item, dict) and "path" in item:
            p = _validate_extra_root(str(item.get("path") or ""))
        else:
            continue
        if p is None:
            continue
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            roots.append(p)
    return roots


def get_read_blacklist_extra_roots() -> list[Path]:
    """用户配置的、禁止读取的路径根（其下任意路径均拒绝读取）。"""
    data = load_raw()
    raw_list = data.get("read_blacklist_extra") or []
    if not isinstance(raw_list, list):
        return []
    roots: list[Path] = []
    seen: set[str] = set()
    for item in raw_list:
        if isinstance(item, str):
            p = _validate_extra_root(item)
        elif isinstance(item, dict) and "path" in item:
            p = _validate_extra_root(str(item.get("path") or ""))
        else:
            continue
        if p is None:
            continue
        key = str(p).lower()
        if key not in seen:
            seen.add(key)
            roots.append(p)
    return roots


def save_policy(
    write_allowlist_extra: list[str],
    read_blacklist_extra: list[str],
) -> tuple[bool, str]:
    """
    校验并写入策略文件。成功时 invalidate 缓存。
    返回 (ok, message)。
    """
    w_clean: list[str] = []
    errors: list[str] = []
    for i, s in enumerate(write_allowlist_extra):
        p = _validate_extra_root(s)
        if p is None:
            errors.append(f"写入白名单第 {i + 1} 项无效: {s!r}")
        else:
            w_clean.append(str(p))
    r_clean: list[str] = []
    for i, s in enumerate(read_blacklist_extra):
        p = _validate_extra_root(s)
        if p is None:
            errors.append(f"读取黑名单第 {i + 1} 项无效: {s!r}")
        else:
            r_clean.append(str(p))
    if errors:
        return False, "；".join(errors)

    payload = {
        "version": 1,
        "write_allowlist_extra": w_clean,
        "read_blacklist_extra": r_clean,
    }
    path = _POLICY_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as e:
        return False, f"写入失败: {e}"
    invalidate_cache()
    return True, "ok"
