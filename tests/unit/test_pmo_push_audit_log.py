"""PMO 推送审计日志。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from l3_node.pmo_copilot_debug_file import (
    append_pmo_lark_push_line,
    append_pmo_lark_push_plan_line,
    init_pmo_debug_session,
)
from l3_node.pmo_push_audit_log import log_pmo_lark_push, log_pmo_lark_push_plan


class TestPmoPushAuditLog(unittest.TestCase):
    def test_debug_file_records_chat_id(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log_path = Path(td) / "pmo_test.txt"
            init_pmo_debug_session(
                log_path=log_path,
                user_message="test",
                correlation_id="c1",
                max_iterations=8,
                mode_hint="multi-agent",
            )
            append_pmo_lark_push_plan_line(
                tool="core:pmo_macro_dashboard_push",
                chat_ids=["oc_868fc82317a60ce89744ae51bb7bce91"],
                debug={"PMO_EFFECTIVE_PRIMARY": "oc_868fc82317a60ce89744ae51bb7bce91"},
            )
            append_pmo_lark_push_line(
                tool="core:pmo_macro_dashboard_push",
                chat_id="oc_868fc82317a60ce89744ae51bb7bce91",
                status="success",
                message_id="om_abc123",
            )
            text = log_path.read_text(encoding="utf-8")
            self.assertIn("oc_868fc82317a60ce89744ae51bb7bce91", text)
            self.assertIn("message_id=om_abc123", text)
            self.assertIn("飞书推送计划", text)

    def test_log_pmo_lark_push_calls_logger(self) -> None:
        with mock.patch("l3_node.pmo_push_audit_log.logger") as mlog:
            with mock.patch("l3_node.pmo_copilot_debug_file.append_pmo_lark_push_line"):
                log_pmo_lark_push(
                    tool="mcp:atom_lark_notifier",
                    chat_id="oc_test_chat",
                    status="success",
                    message_id="om_x",
                )
        mlog.info.assert_called_once()
        self.assertIn("oc_test_chat", str(mlog.info.call_args))

    def test_log_pmo_lark_push_plan(self) -> None:
        with mock.patch("l3_node.pmo_push_audit_log.logger") as mlog:
            with mock.patch("l3_node.pmo_copilot_debug_file.append_pmo_lark_push_plan_line"):
                log_pmo_lark_push_plan(
                    tool="core:pmo_macro_dashboard_push",
                    chat_ids=["oc_a", "oc_b"],
                )
        self.assertTrue(any("oc_a" in str(c) for c in mlog.info.call_args_list))


if __name__ == "__main__":
    unittest.main()
