"""
Jachin Nexus V2 - L2 创世自举

L1 配对成功后，确保 L2 存在默认子账号，并将配对码（pairing_code）写入子账号作为溯源印记。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_NEXUS_CONFIG_PATH = Path.home() / ".jachin" / "nexus_config.json"


def _load_nexus_config() -> dict:
    """读取 nexus_config.json，兼容旧版无 pairing_code，容错不抛错"""
    if not _NEXUS_CONFIG_PATH.exists():
        return {}
    try:
        raw = _NEXUS_CONFIG_PATH.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            raw = _NEXUS_CONFIG_PATH.read_text(encoding="utf-16")
        except Exception:
            return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def ensure_default_sub_account() -> None:
    """
    L2 创世自举：若已与 L1 配对且尚无默认子账号，则创建并将 pairing_code 写入 l1_pairing_code。

    - 读取 ~/.jachin/nexus_config.json
    - 若 instance_id 与 access_token 存在（已配对），则确保存在默认子账号
    - 默认子账号 id = default-{instance_id}，main_user_id = instance_id
    - 将 pairing_code 写入 l1_pairing_code（旧版配置无此字段时为空，容错）
    """
    cfg = _load_nexus_config()
    instance_id = cfg.get("instance_id") or ""
    access_token = cfg.get("access_token") or ""
    pairing_code = cfg.get("pairing_code") or None  # 旧版无 pairing_code 时为空

    if not instance_id or not access_token:
        logger.debug("未配对 L1，跳过 ensure_default_sub_account")
        return

    default_id = f"default-{instance_id}"

    from core.db import get_connection

    conn = get_connection()
    cur = conn.execute(
        "SELECT 1 FROM sub_accounts WHERE id = ?",
        (default_id,),
    )
    if cur.fetchone():
        logger.debug("默认子账号已存在: %s", default_id)
        return

    name = f"边缘网关 (配对码: {pairing_code})" if pairing_code else "边缘网关 (L1 已配对)"
    conn.execute(
        """
        INSERT INTO sub_accounts (id, main_user_id, name, role, permissions_json, l1_pairing_code, created_at, updated_at)
        VALUES (?, ?, ?, 'admin', '{}', ?, strftime('%s', 'now'), strftime('%s', 'now'))
        """,
        (default_id, instance_id, name, pairing_code),
    )
    conn.commit()
    logger.info("创世默认子账号已创建: %s (l1_pairing_code=%s)", default_id, pairing_code or "(无)")
