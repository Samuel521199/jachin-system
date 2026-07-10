"""PMO 对外回复脱敏：监控群 / chat_id 不得泄漏。"""
from __future__ import annotations

from l3_node.pmo_user_visible_sanitize import sanitize_pmo_confidential_wording


def test_strips_monitor_group_and_chat_ids() -> None:
    raw = (
        "✅ K11 · PMO 宏观战报 已成功推送至主群（oc_437c98d11106295fb10751a5481ee465）"
        "与监控群（oc_0e321f92d758ecb44aea5b499c90510b）。"
        "当前 Sprint 为 2026/06/08-Sprint，共 13 个大需求。"
    )
    out = sanitize_pmo_confidential_wording(raw)
    assert "监控群" not in out
    assert "oc_" not in out
    assert "13 个大需求" in out
    assert "飞书" in out
