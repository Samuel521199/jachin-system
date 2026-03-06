"""
Jachin Nexus V2 - L2 创世自举

L1 配对成功后，确保 L2 存在默认子账号，并将配对码（pairing_code）写入子账号作为溯源印记。
单机模式：从环境变量自动同步 API Key 到默认子账号，免去 Admin 手动添加。
"""
from __future__ import annotations

import json
import logging
import os
import secrets
from pathlib import Path

logger = logging.getLogger(__name__)

# Provider -> 环境变量映射（与 l3_node/llm_client 一致）
_ENV_TO_PROVIDER = [
    ("dashscope", ["DASHSCOPE_API_KEY", "QWEN_API_KEY", "QWEN_AI_API_KEY"]),
    ("openai", ["OPENAI_API_KEY"]),
]

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
    - main_user_id 强制使用 l1_user_id（L1 配对透传的真实用户主账号），防呆：无则回退 instance_id
    - 默认子账号 id = default-{instance_id}
    - 所有 sub_accounts 归属权严格继承 gateway_admins.main_user_id
    """
    cfg = _load_nexus_config()
    instance_id = cfg.get("instance_id") or ""
    access_token = cfg.get("access_token") or ""
    pairing_code = cfg.get("pairing_code") or None
    # 防呆：l1_user_id 优先，无则回退 instance_id（旧版兼容）
    l1_user_id = cfg.get("l1_user_id") or instance_id

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
        upd = conn.execute(
            "UPDATE sub_accounts SET main_user_id = ? WHERE id = ? AND main_user_id != ?",
            (l1_user_id, default_id, l1_user_id),
        )
        if upd.rowcount and upd.rowcount > 0:
            conn.commit()
            logger.info("默认子账号 main_user_id 已更新为 l1_user_id: %s", l1_user_id)
        return

    name = f"边缘网关 (配对码: {pairing_code})" if pairing_code else "边缘网关 (L1 已配对)"
    conn.execute(
        """
        INSERT INTO sub_accounts (id, main_user_id, name, role, permissions_json, l1_pairing_code, created_at, updated_at)
        VALUES (?, ?, ?, 'admin', '{}', ?, strftime('%s', 'now'), strftime('%s', 'now'))
        """,
        (default_id, l1_user_id, name, pairing_code),
    )
    conn.commit()
    logger.info("创世默认子账号已创建: %s (l1_pairing_code=%s)", default_id, pairing_code or "(无)")


def _get_default_sub_account_ids() -> list[str]:
    """
    获取应同步 API Key 的子账号 ID 列表。
    优先：已配对的 default-{instance_id}；否则首个 sub_account（本地 Gateway 无 L1 时）。
    """
    cfg = _load_nexus_config()
    instance_id = cfg.get("instance_id") or ""
    access_token = cfg.get("access_token") or ""

    from core.db import get_connection

    conn = get_connection()
    try:
        if instance_id and access_token:
            default_id = f"default-{instance_id}"
            row = conn.execute(
                "SELECT 1 FROM sub_accounts WHERE id = ?",
                (default_id,),
            ).fetchone()
            if row:
                return [default_id]
        # 无配对：取首个 sub_account（本地 Gateway 场景）
        row = conn.execute(
            "SELECT id FROM sub_accounts ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
        if row:
            return [row[0]]
        return []
    finally:
        conn.close()


def sync_api_keys_from_env() -> int:
    """
    单机模式优化：从环境变量自动同步 API Key 到默认子账号的 api_keys_vault。
    仅当 vault 中该 provider 尚无 Key 时插入，不覆盖 Admin 已配置的 Key。
    支持多 Key：Admin 可继续通过界面添加更多 Key。

    环境变量：DASHSCOPE_API_KEY/QWEN_API_KEY、OPENAI_API_KEY
    可通过 JACHIN_SYNC_API_KEYS_FROM_ENV=0 禁用。
    """
    if os.environ.get("JACHIN_SYNC_API_KEYS_FROM_ENV", "1").lower() in ("0", "false", "off"):
        logger.debug("JACHIN_SYNC_API_KEYS_FROM_ENV=0，跳过 env 同步")
        return 0

    sub_ids = _get_default_sub_account_ids()
    if not sub_ids:
        logger.debug("无默认子账号，跳过 env 同步")
        return 0

    from core.db import get_connection
    from core.security.crypto_manager import encrypt_for_storage, hash_key_for_audit

    conn = get_connection()
    added = 0
    try:
        for sub_id in sub_ids:
            for provider, env_names in _ENV_TO_PROVIDER:
                api_key = None
                for name in env_names:
                    api_key = os.environ.get(name)
                    if api_key and str(api_key).strip():
                        api_key = str(api_key).strip()
                        break
                if not api_key:
                    continue

                # 仅当 vault 中该 provider 尚无 Key 时插入
                existing = conn.execute(
                    "SELECT 1 FROM api_keys_vault WHERE sub_account_id = ? AND provider = ?",
                    (sub_id, provider),
                ).fetchone()
                if existing:
                    continue

                key_id = f"env-{provider}-{secrets.token_hex(4)}"
                key_hash = hash_key_for_audit(api_key)
                encrypted = encrypt_for_storage(api_key)
                conn.execute(
                    """
                    INSERT INTO api_keys_vault (id, sub_account_id, provider, encrypted_key, key_hash)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (key_id, sub_id, provider, encrypted, key_hash),
                )
                added += 1
                logger.info(
                    "从环境变量同步 API Key 到子账号 %s (provider=%s)",
                    sub_id,
                    provider,
                )
        if added:
            conn.commit()
    except Exception as e:
        logger.warning("sync_api_keys_from_env 失败: %s", e)
    finally:
        conn.close()
    return added
