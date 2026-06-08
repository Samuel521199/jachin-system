"""Lark drive.file.bitable_record_changed_v1 解析（无网络）。"""
from __future__ import annotations

from l3_node.tools.pmo_bitable_watch import parse_lark_bitable_record_changed


def test_parse_lark_bitable_record_changed_filters_table() -> None:
    body = {
        "header": {"event_type": "drive.file.bitable_record_changed_v1"},
        "event": {
            "table_id": "tblfK9gk6vTQpJtB",
            "file_token": "KHOebTjbpaeSy3sC4BxlzPelg7b",
            "action_list": [
                {
                    "action": "record_edited",
                    "record_id": "recTEST",
                    "before_value": [{"field_id": "fld1", "field_value": "旧"}],
                    "after_value": [{"field_id": "fld1", "field_value": "新"}],
                }
            ],
        },
    }
    events = parse_lark_bitable_record_changed(body)
    assert len(events) == 1
    assert events[0]["change_type"] == "updated"
    assert events[0]["record_id"] == "recTEST"
    assert events[0]["changed_fields"]["fld1"]["after"] == "新"


def test_parse_skips_wrong_table() -> None:
    body = {
        "header": {"event_type": "drive.file.bitable_record_changed_v1"},
        "event": {
            "table_id": "tblOTHER",
            "file_token": "KHOebTjbpaeSy3sC4BxlzPelg7b",
            "action_list": [{"action": "record_added", "record_id": "r1", "after_value": []}],
        },
    }
    assert parse_lark_bitable_record_changed(body) == []
