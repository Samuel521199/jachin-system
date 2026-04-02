"""
L2 本地 ~/.jachin/nexus_config.json 读写与 P3 多工作区（sync_tenant_ids）辅助。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

CONFIG_PATH = Path.home() / ".jachin" / "nexus_config.json"


def load_nexus_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        raw = CONFIG_PATH.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            raw = CONFIG_PATH.read_text(encoding="utf-16")
        except Exception:
            return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def save_nexus_config(cfg: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def normalize_sync_tenant_ids(cfg: dict[str, Any]) -> list[str]:
    """
    返回应用 manifest 同步的租户 UUID 列表（去重保序）。
    兼容旧文件：仅有 tenant_id 时视为单租户。
    """
    out: list[str] = []
    raw = cfg.get("sync_tenant_ids")
    if isinstance(raw, list):
        for x in raw:
            s = str(x).strip()
            if s and s not in out:
                out.append(s)
    primary = (cfg.get("tenant_id") or "").strip()
    if primary:
        if primary not in out:
            out.insert(0, primary)
    return out


def allowed_organization_ids_for_l3(cfg: dict[str, Any]) -> Optional[set[str]]:
    """
    若 L2 已与 L1 配对并带有租户信息，返回允许 L3 auth/sync 的 organization_id 集合；
    若列表为空（未配对/无 tenant），返回 None 表示不强制校验。
    """
    ids = normalize_sync_tenant_ids(cfg)
    if not ids:
        return None
    return set(ids)
