"""Vision UI 屏幕解析（编号与 JSON 映射）。"""
from __future__ import annotations

import json

from core.mcp_multimodal_result import parse_multimodal_observation_payload
from l3_client.local_mcps.vision_ui_mcp.screen_parser import _merge_and_number
from l3_client.local_mcps.vision_ui_mcp.service import VisionUIService, _needs_clipboard_paste


def test_merge_and_number_sorts_stable():
    ocr = [("记事本", 100.0, 200.0, 0.9), ("开始", 50.0, 800.0, 0.85)]
    els = _merge_and_number(ocr, [])
    assert len(els) == 2
    assert els[0].element_id == "1"
    assert els[1].text == "开始"


def test_needs_clipboard_paste_cjk():
    assert _needs_clipboard_paste("测试成功")
    assert not _needs_clipboard_paste("hello")


def test_get_parsed_screen_multimodal_envelope():
    from unittest.mock import patch

    svc = VisionUIService()

    class _FakeParsed:
        ok = True
        elements = {"1": {"text": "Notepad", "x": 10, "y": 20, "score": 0.9, "source": "ocr"}}
        element_list = []
        annotated_png = b"\x89PNG\r\n\x1a\n" + b"x" * 80
        annotated_path = "/tmp/x.png"
        raw_png_path = "/tmp/r.png"
        screen_width = 1920
        screen_height = 1080
        notes = "test"
        error = ""

    with patch(
        "l3_client.local_mcps.vision_ui_mcp.service.capture_screen_png",
        return_value=(b"\x89PNG\r\n\x1a\n" + b"y" * 80, 100, 100, ""),
    ), patch(
        "l3_client.local_mcps.vision_ui_mcp.service.parse_screen_from_png",
        return_value=_FakeParsed(),
    ):
        raw = svc.get_parsed_screen()
    text, urls = parse_multimodal_observation_payload(raw)
    assert urls
    obj = json.loads(text)
    assert obj["ok"] is True
    assert "1" in obj["elements"]
