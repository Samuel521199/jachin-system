"""PMO 战报飞书推送 chat_id 守卫：监控群写死，主群仅 .env/触发会话，拦截 dev 遗留群。"""
from __future__ import annotations

import json
from typing import Any

# 战报监控群（代码写死；勿改 .env）
PMO_WAR_REPORT_MONITOR_CHAT_ID = "oc_0e321f92d758ecb44aea5b499c90510b"

# 历史 dev 主群：禁止作为推送目标（SKILL 示例 / 旧 sidecar 硬编码残留）
PMO_LEGACY_BLOCKED_PUSH_CHAT_IDS = frozenset({
    "oc_437c98d11106295fb10751a5481ee465",
})


def pmo_is_legacy_blocked_chat_id(chat_id: str) -> bool:
    return (chat_id or "").strip() in PMO_LEGACY_BLOCKED_PUSH_CHAT_IDS


def pmo_war_report_allowed_chat_ids(session_chat_id: str = "") -> frozenset[str]:
    """当前轮允许推送的战报 chat_id 集合（主群 + 固定监控群）。"""
    from l3_node.pmo_lark_env import pmo_effective_primary_chat_id, pmo_push_monitor_enabled

    allowed: set[str] = set()
    primary = pmo_effective_primary_chat_id(session_chat_id).strip()
    if primary:
        allowed.add(primary)
    if pmo_push_monitor_enabled():
        allowed.add(PMO_WAR_REPORT_MONITOR_CHAT_ID)
    return frozenset(allowed)


def pmo_guard_blocked_push_chat_payload(
    chat_id: str,
    *,
    session_chat_id: str = "",
    tool: str = "",
    configured_primary: str = "",
) -> dict[str, Any] | None:
    """
    若 chat_id 不允许推送，返回结构化拦截载荷；否则 None。
    """
    cid = (chat_id or "").strip()
    if not cid:
        return None

    tool_name = (tool or "pmo_push").strip()
    primary_hint = (configured_primary or "").strip()
    if not primary_hint:
        from l3_node.pmo_lark_env import pmo_effective_primary_chat_id

        primary_hint = pmo_effective_primary_chat_id(session_chat_id).strip()

    if pmo_is_legacy_blocked_chat_id(cid):
        return {
            "status": "error",
            "error": "pmo_legacy_dev_chat_blocked",
            "blocked_chat_id": cid,
            "msg": (
                f"【宿主拦截 · 推送守卫】禁止向历史 dev 群 `{cid}` 推送战报。"
                "该 chat_id 仅存在于旧 SKILL 示例/旧 sidecar 硬编码，**不是**本机主群。"
                f"主群请用 `.env` 的 `PMO_PRIMARY_CHAT_ID`"
                f"{f'（当前生效: `{primary_hint}`）' if primary_hint else '（未配置则用飞书触发群）'}；"
                f"监控群固定为 `{PMO_WAR_REPORT_MONITOR_CHAT_ID}`（代码内置）。"
                f"请 `{tool_name}` 传 `{{}}` 或省略 chat_id，勿手写 oc_437。"
            ),
        }

    allowed = pmo_war_report_allowed_chat_ids(session_chat_id)
    if allowed and cid not in allowed:
        allowed_txt = ", ".join(sorted(allowed))
        return {
            "status": "error",
            "error": "pmo_push_chat_id_not_allowed",
            "blocked_chat_id": cid,
            "allowed_chat_ids": sorted(allowed),
            "msg": (
                f"【宿主拦截 · 推送守卫】chat_id `{cid}` 不在本机战报投递白名单。"
                f"允许目标：{allowed_txt}。"
                "主群由 `PMO_PRIMARY_CHAT_ID` 或飞书触发会话决定；监控群为代码固定值。"
                f"请 `{tool_name}` 传 `{{}}` 让宿主注入正确主群，或显式使用白名单内 chat_id。"
            ),
        }
    return None


def pmo_guard_observation_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def pmo_reject_legacy_primary_chat_id(chat_id: str | None) -> str | None:
    """工具层：若显式主群为 dev 遗留 ID，视为未指定，回退 env/会话。"""
    cid = (chat_id or "").strip()
    if cid and pmo_is_legacy_blocked_chat_id(cid):
        return None
    return cid or None
