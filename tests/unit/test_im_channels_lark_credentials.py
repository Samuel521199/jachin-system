"""Tests for im_channels lark credential resolution."""
from __future__ import annotations

from unittest.mock import patch

from l3_node.im_channels.lark_credentials import (
    resolve_lark_im_credentials,
    resolve_pmo_bitable_credentials,
)


def test_resolve_lark_im_from_yaml() -> None:
    aid, sec = resolve_lark_im_credentials(
        {"app_id": "cli_yaml", "app_secret": "sec_yaml"},
        "lark",
    )
    assert aid == "cli_yaml"
    assert sec == "sec_yaml"


def test_resolve_lark_hr_uses_yaml_first() -> None:
    aid, sec = resolve_lark_im_credentials(
        {"app_id": "cli_hr_yaml", "app_secret": "sec_hr_yaml"},
        "lark_hr",
    )
    assert aid == "cli_hr_yaml"
    assert sec == "sec_hr_yaml"


def test_resolve_pmo_bitable_from_watch_config() -> None:
    with patch(
        "l3_node.tools.pmo_bitable_watch._load_watch_config",
        return_value={"app_id": "cli_pmo", "app_secret": "sec_pmo"},
    ):
        aid, sec = resolve_pmo_bitable_credentials({})
    assert aid == "cli_pmo"
    assert sec == "sec_pmo"
