"""Holographic Screen MCP 服务（编号解析与点击查表）。"""
from __future__ import annotations

import json
from unittest.mock import patch

from core.mcp_multimodal_result import parse_multimodal_observation_payload
from l3_client.local_mcps.holographic_screen_mcp.omniparser_core import simplify_elements_for_llm
from l3_client.local_mcps.holographic_screen_mcp.session_service import HolographicScreenService


def test_simplify_elements_for_llm():
    raw = [
        {"id": 0, "center_xy_pixels": [100.2, 200.7], "type": "text", "content": "OK"},
        {"id": 12, "center_xy_pixels": [500.0, 300.0]},
    ]
    out = simplify_elements_for_llm(raw)
    assert out[0]["id"] == 0
    assert out[0]["center_x"] == 100
    assert out[0]["center_y"] == 201
    assert out[1]["id"] == 12
    assert out[1]["center_x"] == 500
    assert out[1]["center_y"] == 300


def test_get_holographic_screen_multimodal_envelope():
    svc = HolographicScreenService()
    fake_report = {
        "ok": True,
        "elements_llm": [{"id": 12, "center_x": 500, "center_y": 300, "content": "Save"}],
        "outputs": {"annotated_image": "/tmp/out.jpg"},
    }

    with patch(
        "l3_client.local_mcps.holographic_screen_mcp.session_service.capture_screen_png",
        return_value=(b"\x89PNG\r\n\x1a\n" + b"y" * 80, 1920, 1080, ""),
    ), patch(
        "l3_client.local_mcps.holographic_screen_mcp.session_service.run_omniparser",
        return_value=fake_report,
    ), patch("pathlib.Path.is_file", return_value=True), patch(
        "pathlib.Path.read_bytes",
        return_value=b"\xff\xd8\xff" + b"x" * 80,
    ):
        raw = svc.get_holographic_screen()

    text, urls = parse_multimodal_observation_payload(raw)
    assert urls
    obj = json.loads(text)
    assert obj["ok"] is True
    assert obj["elements"][0]["id"] == 12
    assert 12 in svc.last_elements
    assert svc.last_elements[12].center_x == 500


def test_physical_click_resolves_id():
    from l3_client.local_mcps.holographic_screen_mcp.session_service import HolographicElement

    svc = HolographicScreenService()
    svc.last_elements = {
        12: HolographicElement(element_id=12, center_x=500, center_y=300, content="btn"),
    }
    with patch("pyautogui.moveTo"), patch("pyautogui.click"):
        raw = svc.physical_click(element_id=12)
    res = json.loads(raw)
    assert res["ok"] is True
    assert res["element_id"] == 12
