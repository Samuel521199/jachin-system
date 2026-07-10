"""发版邮件拉取韧性：单封失败跳过、瞬态错误重试。"""
from __future__ import annotations

from unittest.mock import patch

from l3_node.tools import pmo_release_epic_mapping as mod


def _release_detail(mid: str, maint: str = "2026-06-05") -> dict:
    return {
        "message_id": mid,
        "subject": "生产环境维护公告",
        "body": f"维护日 {maint}",
        "internal_date": "1717488300000",
        "internal_dt": mod._ms_to_dt("1717488300000"),
    }


def test_fetch_continues_when_one_message_detail_fails():
    ids = ["bad1", "good1", "noise1"]

    def fake_get(*, message_id: str, **kwargs):
        if message_id == "bad1":
            return None
        if message_id == "good1":
            return _release_detail("good1")
        return {"message_id": message_id, "subject": "无关邮件", "body": "hello", "internal_date": "1"}

    with patch.object(mod, "_mail_list_message_ids_with_retry", return_value=(ids, None)):
        with patch.object(mod, "_mail_fetch_token_and_base", return_value=("tok", "https://x")):
            with patch.object(mod, "_mail_get_message_full", side_effect=fake_get):
                with patch.object(mod, "parse_release_maintenance_date", return_value=mod._parse_iso_date("2026-06-05")):
                    res = mod._fetch_release_mails_resilient()
    assert len(res["mails"]) == 1
    assert res["stats"]["detail_failures"] == 1


def test_mail_get_message_full_retries_transient_then_succeeds():
    calls = {"n": 0}

    def fake_once(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("获取邮件详情失败: Gateway timeout")
        return _release_detail("m1")

    with patch.object(mod, "_mail_get_message_once", side_effect=fake_once):
        with patch.object(mod, "_mail_detail_retry_count", return_value=3):
            with patch("time.sleep"):
                out = mod._mail_get_message_full(
                    token="t",
                    api_base="https://x",
                    mailbox="box",
                    message_id="m1",
                )
    assert out is not None
    assert out["message_id"] == "m1"
    assert calls["n"] == 2


def test_run_release_epic_mapping_partial_when_some_details_fail():
    fetch_stats = {"ids_scanned": 3, "detail_failures": 1, "list_failed": False}
    mails = [_release_detail("good1")]
    window = {"ok": True, "since": mails[0]["internal_dt"], "since_mail": mails[0]}

    with patch.object(mod, "pmo_mirror_db_ready", return_value=True):
        with patch.object(
            mod,
            "_fetch_release_mails_resilient",
            return_value={"mails": mails, "stats": fetch_stats},
        ):
            with patch.object(mod, "resolve_release_window", return_value=window):
                with patch.object(mod, "find_completed_epics_in_window", return_value=[]):
                    with patch.object(mod, "build_release_mapping_markdown", return_value="### 📦"):
                        rep = mod.run_release_epic_mapping()
    assert rep["status"] == "ok"
    assert rep.get("degraded") is True
    assert rep["release_mails_found"] == 1
