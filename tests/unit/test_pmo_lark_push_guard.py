"""PMO 战报推送 chat_id 守卫。"""
from __future__ import annotations

import unittest

from l3_node.pmo_lark_push_guard import (
    PMO_ARCHIVED_BLOCKED_PUSH_CHAT_IDS,
    PMO_WAR_REPORT_MONITOR_CHAT_ID,
    pmo_guard_blocked_push_chat_payload,
    pmo_is_archived_blocked_chat_id,
    pmo_reject_archived_primary_chat_id,
    pmo_war_report_allowed_chat_ids,
)


class TestPmoLarkPushGuard(unittest.TestCase):
    def test_archived_blocked_chat_id(self) -> None:
        archived = "oc_437c98d11106295fb10751a5481ee465"
        self.assertIn(archived, PMO_ARCHIVED_BLOCKED_PUSH_CHAT_IDS)
        self.assertTrue(pmo_is_archived_blocked_chat_id(archived))
        self.assertFalse(pmo_is_archived_blocked_chat_id(PMO_WAR_REPORT_MONITOR_CHAT_ID))

    def test_reject_archived_primary(self) -> None:
        self.assertIsNone(
            pmo_reject_archived_primary_chat_id("oc_437c98d11106295fb10751a5481ee465")
        )
        self.assertEqual(
            pmo_reject_archived_primary_chat_id("oc_868fc82317a60ce89744ae51bb7bce91"),
            "oc_868fc82317a60ce89744ae51bb7bce91",
        )

    def test_guard_blocks_archived_dev_chat(self) -> None:
        payload = pmo_guard_blocked_push_chat_payload(
            "oc_437c98d11106295fb10751a5481ee465",
            session_chat_id="",
            tool="mcp:atom_lark_notifier",
            configured_primary="oc_868fc82317a60ce89744ae51bb7bce91",
        )
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload.get("error"), "pmo_archived_dev_chat_blocked")

    def test_allowed_includes_primary_and_fixed_monitor(self) -> None:
        import os
        import tempfile
        from pathlib import Path
        from unittest import mock

        from l3_node import pmo_lark_env as ple

        td = tempfile.mkdtemp()
        install = Path(td) / "install"
        jachin = Path(td) / "jachin"
        install.mkdir()
        jachin.mkdir()
        primary = "oc_868fc82317a60ce89744ae51bb7bce91"
        monitor = "oc_monitor_from_env"
        (install / ".env").write_text(
            f"PMO_PRIMARY_CHAT_ID={primary}\nPMO_MONITOR_CHAT_ID={monitor}\n",
            encoding="utf-8",
        )
        ple._PMO_DOTENV_LOADED = False
        with mock.patch.dict(os.environ, {"JACHIN_HOME": str(jachin)}, clear=True), mock.patch(
            "l3_node.paths.get_app_root", return_value=install
        ):
            ple._PMO_DOTENV_LOADED = False
            allowed = pmo_war_report_allowed_chat_ids()
            self.assertIn(primary, allowed)
            self.assertIn(monitor, allowed)


if __name__ == "__main__":
    unittest.main()
