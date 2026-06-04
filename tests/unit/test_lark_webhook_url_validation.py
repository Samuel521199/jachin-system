"""Lark Webhook URL 校验与 PMO notifier 入参纠偏。"""
from __future__ import annotations

import json

from l3_node.agent_core import _pmo_fixup_atom_lark_notifier_inp
from l3_node.channels.lark.webhook_url import (
    is_valid_lark_incoming_webhook_url,
    looks_like_lark_chat_id,
)
from l3_node.primitives.mcp.mcp_tools.bi.tool_lark_notifier import send_lark_markdown


def test_chat_id_is_not_webhook():
    assert looks_like_lark_chat_id("oc_b1b9cff6804517c79b7f5a617ab30483")
    assert not is_valid_lark_incoming_webhook_url("oc_b1b9cff6804517c79b7f5a617ab30483")


def test_valid_hook_url():
    u = "https://open.larksuite.com/open-apis/bot/v2/hook/33ff8360-15f4-47ba-9ce3-153baf15c442"
    assert is_valid_lark_incoming_webhook_url(u)


def test_pmo_fixup_moves_oc_from_webhook_to_chat_id():
    inp = json.dumps(
        {
            "webhook_url": "oc_437c98d11106295fb10751a5481ee465",
            "markdown_content": "| a | b |\n|---|---|\n| 1 | 2 |",
            "title": "test",
        }
    )
    fixed = json.loads(_pmo_fixup_atom_lark_notifier_inp(inp))
    assert "webhook_url" not in fixed
    assert fixed.get("chat_id") == "oc_437c98d11106295fb10751a5481ee465"


def test_send_uses_im_when_webhook_is_chat_id():
    out = send_lark_markdown(
        webhook_url="oc_b1b9cff6804517c79b7f5a617ab30483",
        markdown_content="# test",
        title="t",
        chat_id="",
    )
    err = str(out.get("error") or "")
    assert "incoming webhook access token invalid" not in err.lower()
