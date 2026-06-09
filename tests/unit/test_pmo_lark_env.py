"""PMO 飞书 chat_id · 项目根 .env SSOT。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from l3_node import pmo_lark_env as ple


class TestPmoLarkEnv(unittest.TestCase):
    def setUp(self) -> None:
        ple._PMO_DOTENV_LOADED = False

    def test_primary_from_env(self) -> None:
        with mock.patch.dict(os.environ, {"PMO_PRIMARY_CHAT_ID": "oc_test_primary"}, clear=False):
            ple._PMO_DOTENV_LOADED = False
            self.assertEqual(ple.pmo_primary_chat_id(), "oc_test_primary")

    def test_change_alert_prefers_new_env_key(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "PMO_CHANGE_ALERT_CHAT_ID": "oc_alert_new",
                "PMO_BITABLE_WATCH_CHAT_ID": "oc_alert_old",
            },
            clear=False,
        ):
            ple._PMO_DOTENV_LOADED = False
            self.assertEqual(ple.pmo_change_alert_chat_id(), "oc_alert_new")

    def test_loads_project_root_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "app"
            root.mkdir()
            (root / ".env").write_text(
                "PMO_PRIMARY_CHAT_ID=oc_from_root_env\n",
                encoding="utf-8",
            )
            ple._PMO_DOTENV_LOADED = False
            with mock.patch("l3_node.paths.get_app_root", return_value=root):
                ple.ensure_pmo_dotenv_loaded()
            self.assertEqual(os.environ.get("PMO_PRIMARY_CHAT_ID"), "oc_from_root_env")

    def test_push_monitor_disabled_single_delivery(self) -> None:
        b_primary = "oc_367e7998b7dfe39c67d1598101defdfe"
        with mock.patch.dict(
            os.environ,
            {"PMO_PRIMARY_CHAT_ID": b_primary, "PMO_PUSH_MONITOR": "0"},
            clear=False,
        ):
            ple._PMO_DOTENV_LOADED = False
            self.assertFalse(ple.pmo_push_monitor_enabled())
            self.assertEqual(ple.pmo_required_delivery_chat_ids(), (b_primary,))

    def test_push_monitor_default_includes_monitor(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            ple._PMO_DOTENV_LOADED = False
            ids = ple.pmo_required_delivery_chat_ids()
            self.assertEqual(len(ids), 2)
            self.assertIn(ple.DEFAULT_PMO_PRIMARY_CHAT_ID, ids)
            self.assertIn(ple.DEFAULT_PMO_MONITOR_CHAT_ID, ids)


if __name__ == "__main__":
    unittest.main()
