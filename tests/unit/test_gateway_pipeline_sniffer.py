"""gateway_pipeline：嗅探开关与跳过路径。"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from l3_node.intent_gateway.bundle import build_gateway_bundle
from l3_node.intent_gateway.gateway_pipeline import apply_gateway_ingress_pipeline


@pytest.mark.asyncio
async def test_context_sniffer_disabled_skips_build() -> None:
    b = build_gateway_bundle(user_input="hello", correlation_id="corr-test-1")
    with (
        patch(
            "l3_node.intent_gateway.config.get_intent_gateway_config",
            return_value={
                "context_sniffer_enabled": False,
                "context_sniffer_tracker_enabled": False,
            },
        ),
        patch(
            "l3_node.intent_gateway.context_sniffer.build_environment_report",
            new=AsyncMock(),
        ) as mock_build,
    ):
        await apply_gateway_ingress_pipeline(b, "hello", [], run_id="run-1")
    mock_build.assert_not_called()
    er = b.extra.get("environment_report")
    assert isinstance(er, dict)
    assert er.get("skipped") is True


@pytest.mark.asyncio
async def test_context_sniffer_passes_budget_to_build() -> None:
    b = build_gateway_bundle(user_input="q", correlation_id="c2")
    async def _fake_build(ui: str, ws: str, **kw: object):
        return {"ok": True, "meta": {"from_test": kw.get("max_total_chars")}}

    with (
        patch(
            "l3_node.intent_gateway.config.get_intent_gateway_config",
            return_value={
                "context_sniffer_enabled": True,
                "context_sniffer_max_total_chars": 999,
                "context_sniffer_max_git_chars": 111,
                "context_sniffer_tracker_enabled": False,
            },
        ),
        patch(
            "l3_node.intent_gateway.context_sniffer.build_environment_report",
            side_effect=_fake_build,
        ) as mock_build,
    ):
        await apply_gateway_ingress_pipeline(b, "q", [], run_id="r2", workspace_dir="/tmp")
    mock_build.assert_awaited_once()
    _call = mock_build.await_args
    assert _call.kwargs.get("max_total_chars") == 999
    assert _call.kwargs.get("max_git_chars") == 111
    assert _call[0][1] == "/tmp"
