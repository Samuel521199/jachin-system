"""PMO 飞书 chat_id · 项目根 .env SSOT。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from l3_node import pmo_lark_env as ple


def _isolated_pmo_env(
    *,
    install_env: str = "",
    jachin_env: str = "",
    os_environ: dict[str, str] | None = None,
):
    td = tempfile.mkdtemp()
    install = Path(td) / "install"
    jachin = Path(td) / "jachin"
    install.mkdir()
    jachin.mkdir()
    if install_env:
        (install / ".env").write_text(install_env, encoding="utf-8")
    if jachin_env:
        (jachin / ".env").write_text(jachin_env, encoding="utf-8")
    env = {"JACHIN_HOME": str(jachin)}
    if os_environ:
        env.update(os_environ)
    return mock.patch.dict(os.environ, env, clear=True), mock.patch(
        "l3_node.paths.get_app_root", return_value=install
    )


class TestPmoLarkEnv(unittest.TestCase):
    def setUp(self) -> None:
        ple._PMO_DOTENV_LOADED = False

    def test_primary_from_env(self) -> None:
        ctx = _isolated_pmo_env(os_environ={"PMO_PRIMARY_CHAT_ID": "oc_test_primary"})
        with ctx[0], ctx[1]:
            ple._PMO_DOTENV_LOADED = False
            self.assertEqual(ple.pmo_primary_chat_id(), "oc_test_primary")

    def test_primary_empty_when_unconfigured(self) -> None:
        ctx = _isolated_pmo_env()
        with ctx[0], ctx[1]:
            ple._PMO_DOTENV_LOADED = False
            self.assertEqual(ple.pmo_primary_chat_id(), "")

    def test_effective_primary_uses_session_when_env_empty(self) -> None:
        b_chat = "oc_367e7998b7dfe39c67d1598101defdfe"
        ctx = _isolated_pmo_env()
        with ctx[0], ctx[1]:
            ple._PMO_DOTENV_LOADED = False
            self.assertEqual(ple.pmo_effective_primary_chat_id(b_chat), b_chat)

    def test_change_alert_prefers_new_env_key(self) -> None:
        ctx = _isolated_pmo_env(
            os_environ={
                "PMO_CHANGE_ALERT_CHAT_ID": "oc_alert_new",
                "PMO_BITABLE_WATCH_CHAT_ID": "oc_alert_old",
            }
        )
        with ctx[0], ctx[1]:
            ple._PMO_DOTENV_LOADED = False
            self.assertEqual(ple.pmo_change_alert_chat_id(), "oc_alert_new")

    def test_jachin_home_overrides_install_primary(self) -> None:
        ctx = _isolated_pmo_env(
            install_env="PMO_PRIMARY_CHAT_ID=oc_437c98d11106295fb10751a5481ee465\n",
            jachin_env="PMO_PRIMARY_CHAT_ID=oc_868fc82317a60ce89744ae51bb7bce91\n",
        )
        with ctx[0], ctx[1]:
            ple._PMO_DOTENV_LOADED = False
            val, src = ple._resolve_pmo_env_key("PMO_PRIMARY_CHAT_ID")
            self.assertEqual(val, "oc_868fc82317a60ce89744ae51bb7bce91")
            self.assertIn(".env", src)

    def test_push_monitor_disabled_single_delivery(self) -> None:
        b_primary = "oc_367e7998b7dfe39c67d1598101defdfe"
        ctx = _isolated_pmo_env(
            jachin_env=f"PMO_PRIMARY_CHAT_ID={b_primary}\nPMO_PUSH_MONITOR=0\n"
        )
        with ctx[0], ctx[1]:
            ple._PMO_DOTENV_LOADED = False
            self.assertFalse(ple.pmo_push_monitor_enabled())
            self.assertEqual(ple.pmo_required_delivery_chat_ids(), (b_primary,))

    def test_push_monitor_dual_delivery_uses_env_monitor(self) -> None:
        ctx = _isolated_pmo_env(
            install_env=(
                "PMO_PRIMARY_CHAT_ID=oc_primary\n"
                "PMO_MONITOR_CHAT_ID=oc_custom_monitor\n"
            ),
        )
        with ctx[0], ctx[1]:
            ple._PMO_DOTENV_LOADED = False
            self.assertEqual(ple.pmo_monitor_chat_id(), "oc_custom_monitor")
            ids = ple.pmo_required_delivery_chat_ids()
            self.assertEqual(ids, ("oc_primary", "oc_custom_monitor"))

    def test_fixed_monitor_chat_id_default_when_unset(self) -> None:
        ctx = _isolated_pmo_env()
        with ctx[0], ctx[1]:
            ple._PMO_DOTENV_LOADED = False
            self.assertEqual(
                ple.pmo_monitor_chat_id(),
                ple.DEFAULT_PMO_WAR_REPORT_MONITOR_CHAT_ID,
            )

    def test_session_only_single_delivery(self) -> None:
        b_chat = "oc_367e7998b7dfe39c67d1598101defdfe"
        ctx = _isolated_pmo_env(jachin_env="PMO_PUSH_MONITOR=0\n")
        with ctx[0], ctx[1]:
            ple._PMO_DOTENV_LOADED = False
            self.assertEqual(ple.pmo_required_delivery_chat_ids(b_chat), (b_chat,))


if __name__ == "__main__":
    unittest.main()
