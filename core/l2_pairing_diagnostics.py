"""
L2 控制台配对诊断：汇总 nexus_config、与 core.settings 中 L1 基址对照、心跳/同步前置条件。
access_token 等敏感字段仅输出掩码与长度，便于排障且不泄露密钥。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

PAIRING_LOG_PREFIX = "[L1↔L2 Pairing]"

NEXUS_CONFIG_PATH = Path.home() / ".jachin" / "nexus_config.json"


def mask_secret(value: str | None, *, prefix_keep: int = 10, suffix_keep: int = 4) -> str:
    """脱敏：仅保留头尾字符与长度。"""
    if value is None:
        return "(null)"
    s = str(value).strip()
    if not s:
        return "(empty)"
    if len(s) <= prefix_keep + suffix_keep + 1:
        return f"*** (len={len(s)})"
    return f"{s[:prefix_keep]}…{s[-suffix_keep:]} (len={len(s)})"


def read_nexus_config_silent() -> dict[str, Any]:
    """读取 ~/.jachin/nexus_config.json，失败返回 {}（不打日志）。"""
    cfg, _err = read_nexus_config_from_disk()
    return cfg


def read_nexus_config_from_disk() -> tuple[dict[str, Any], str | None]:
    """
    读取配置文件。(data, error_message)。
    error_message 仅在存在文件但读失败或 JSON 非法时非空。
    """
    if not NEXUS_CONFIG_PATH.exists():
        return {}, None
    try:
        raw = NEXUS_CONFIG_PATH.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            raw = NEXUS_CONFIG_PATH.read_text(encoding="utf-16")
        except OSError as e:
            return {}, f"read_error: {e}"
    except OSError as e:
        return {}, f"read_error: {e}"
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return {}, "root_must_be_object"
        return parsed, None
    except json.JSONDecodeError as e:
        return {}, f"json_error: {e}"


def _effective_tenant_id(cfg: dict[str, Any]) -> str:
    return (
        (cfg.get("tenant_id") or "").strip()
        or (os.environ.get("JACHIN_TENANT_ID") or "").strip()
        or (cfg.get("l1_user_id") or "").strip()
        or (cfg.get("instance_id") or "").strip()
    )


def _sub_accounts_sqlite_lines(cfg: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    try:
        from core.db import get_connection

        conn = get_connection()
        try:
            n = conn.execute("SELECT COUNT(*) FROM sub_accounts").fetchone()
            cnt = int(n[0]) if n else 0
            lines.append(f"SQLite sub_accounts 行数: {cnt}")
            iid = (cfg.get("instance_id") or "").strip()
            if iid:
                did = f"default-{iid}"
                one = conn.execute(
                    "SELECT id, main_user_id, name FROM sub_accounts WHERE id = ?",
                    (did,),
                ).fetchone()
                if one:
                    lines.append(
                        f"默认子账号 {did}: main_user_id={one[1]} name={one[2]!r}"
                    )
                else:
                    lines.append(
                        f"默认子账号 {did}: 尚未创建（登录后应执行 ensure_default_sub_account）"
                    )
        finally:
            conn.close()
    except Exception as e:
        lines.append(f"SQLite sub_accounts: 读取失败 ({type(e).__name__}: {e})")
    return lines


def _gateway_admin_sqlite_lines() -> list[str]:
    lines: list[str] = []
    try:
        from core.db import get_connection

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT id, username, main_user_id, role FROM gateway_admins LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        if row:
            lines.append(
                f"SQLite gateway_admins(首行): id={row[0]} username={row[1]} "
                f"main_user_id={row[2]} role={row[3]}"
            )
        else:
            lines.append("SQLite gateway_admins: (表中无记录)")
    except Exception as e:
        lines.append(f"SQLite gateway_admins: 读取失败 ({type(e).__name__}: {e})")
    return lines


def build_pairing_diagnostic_lines(
    *,
    phase: str,
    cfg: dict[str, Any] | None = None,
    extra_lines: list[str] | None = None,
) -> list[str]:
    """
    构造多行诊断文本（不含前缀），由调用方用统一 logger 输出。
    """
    disk_err: str | None = None
    if cfg is None:
        cfg, disk_err = read_nexus_config_from_disk()

    lines: list[str] = [
        "────────────────────────────────────────",
        f"阶段: {phase}",
        f"配置文件: {NEXUS_CONFIG_PATH} exists={NEXUS_CONFIG_PATH.exists()}",
    ]

    if NEXUS_CONFIG_PATH.exists():
        try:
            st = NEXUS_CONFIG_PATH.stat()
            lines.append(f"配置文件大小: {st.st_size} bytes mtime_ns={getattr(st, 'st_mtime_ns', 0)}")
        except OSError as e:
            lines.append(f"配置文件 stat 失败: {e}")

    if disk_err:
        lines.append(f"警告: nexus_config 读取/解析失败 — {disk_err}")

    if cfg:
        nb = (cfg.get("nexus_base_url") or "").strip().rstrip("/")
        iid = (cfg.get("instance_id") or "").strip()
        uid = (cfg.get("l1_user_id") or "").strip()
        tid = (cfg.get("tenant_id") or "").strip()
        pcode = (cfg.get("pairing_code") or "").strip()
        tok = cfg.get("access_token")
        hb_iv = cfg.get("heartbeat_interval_sec")

        lines.append(f"nexus_base_url(文件): {nb or '(missing)'}")
        lines.append(f"instance_id(edge_agents): {iid or '(missing)'}")
        lines.append(f"l1_user_id: {uid or '(missing)'}")
        lines.append(f"tenant_id(文件): {tid or '(missing)'}")
        lines.append(f"pairing_code(来源标记): {pcode or '(missing)'}")
        lines.append(f"access_token: {mask_secret(tok if isinstance(tok, str) else None)}")
        if hb_iv is not None:
            lines.append(f"heartbeat_interval_sec: {hb_iv}")

        eff_tenant = _effective_tenant_id(cfg)
        lines.append(f"CloudSync 有效 tenant_id(含 env 回退): {eff_tenant or '(missing)'}")

        h_ok = bool(iid and isinstance(tok, str) and tok.strip() and nb)
        lines.append(
            f"L1 心跳前置: {'OK' if h_ok else 'NO'} "
            f"(需要 instance_id + access_token + nexus_base_url)"
        )

        s_ok = bool(nb and isinstance(tok, str) and tok.strip() and eff_tenant)
        lines.append(
            f"CloudSync 前置: {'OK' if s_ok else 'NO'} "
            f"(需要 nexus_base_url + access_token + tenant 解析非空)"
        )

        if nb:
            lines.append(f"心跳 URL: {nb}/api/v1/edge/heartbeat")
            lines.append(f"manifest URL: {nb}/api/v1/sync/manifest")
    elif not cfg:
        if not NEXUS_CONFIG_PATH.exists():
            lines.append(
                "当前无 nexus_config 文件：尚未与 L1 配对（网关 L1 邮箱登录 / Web Bridge / CLI pair）。"
            )
        elif not disk_err:
            lines.append(
                "nexus_config 存在但无有效字段（空对象或 instance_id/access_token/base_url 等未写入）。"
            )
        lines.append("L1 心跳前置: NO   CloudSync 前置: NO")

    # L2 进程侧「指向 L1」的环境（与文件中的 nexus_base_url 应对齐）
    try:
        from core.config import settings

        s_nexus = (getattr(settings, "NEXUS_BASE_URL", None) or "").strip().rstrip("/")
        s_brain = (getattr(settings, "BRAIN_BASE_URL", None) or "").strip().rstrip("/")
        lines.append(f"settings.NEXUS_BASE_URL(L2→L1 API): {s_nexus or '(empty)'}")
        lines.append(f"settings.BRAIN_BASE_URL(L2 对外): {s_brain or '(empty)'}")

        file_nb = (cfg.get("nexus_base_url") or "").strip().rstrip("/") if cfg else ""
        if file_nb and s_nexus and file_nb.rstrip("/") != s_nexus.rstrip("/"):
            lines.append(
                "警告: 文件内 nexus_base_url 与 settings.NEXUS_BASE_URL 不一致，"
                "邮箱登录/redeem 以 settings 为准写盘后应已覆盖；若仍不一致请检查环境变量与旧配置。"
            )
    except Exception as e:
        lines.append(f"读取 settings 失败: {type(e).__name__}: {e}")

    lines.append(f"NEXUS_L2_LOGIN_SECRET 已设置: {bool((os.environ.get('NEXUS_L2_LOGIN_SECRET') or '').strip())}")
    lines.append(f"JACHIN_TENANT_ID 环境变量: {(os.environ.get('JACHIN_TENANT_ID') or '').strip() or '(unset)'}")

    try:
        from core.redis_manager import get_redis_client

        if get_redis_client():
            lines.append("Redis: 已连接（L1 心跳 Leader 选举模式）")
        else:
            lines.append("Redis: 未使用（心跳单节点退化）")
    except Exception:
        lines.append("Redis: 检测跳过")

    lines.extend(_gateway_admin_sqlite_lines())
    lines.extend(_sub_accounts_sqlite_lines(cfg if cfg else {}))

    if extra_lines:
        lines.extend(extra_lines)

    lines.append("────────────────────────────────────────")
    return lines


def log_pairing_diagnostics(
    log: logging.Logger,
    *,
    phase: str,
    cfg: dict[str, Any] | None = None,
    extra_lines: list[str] | None = None,
) -> None:
    for line in build_pairing_diagnostic_lines(
        phase=phase, cfg=cfg, extra_lines=extra_lines
    ):
        log.info("%s %s", PAIRING_LOG_PREFIX, line)
