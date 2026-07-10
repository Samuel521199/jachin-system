"""RoleExecutor Verification evidence 多模态注入（阶段一视神经）。"""
from __future__ import annotations

import json

from core.llm_provider import l3_role_execution_full_messages_need_vision_model
from core.mcp_multimodal_result import (
    JACHIN_MCP_MULTIMODAL_KEY,
    build_multimodal_observation_payload,
    encode_image_bytes_as_data_url,
    parse_multimodal_observation_payload,
)
from l3_node.observation_vision_adapter import (
    build_observation_user_content,
    extract_observation_image_data_urls,
    tool_may_return_screenshot,
)


def test_build_multimodal_observation_payload_roundtrip():
    du = encode_image_bytes_as_data_url(b"\x89PNG\r\n\x1a\n" + b"x" * 64, "image/png")
    raw = build_multimodal_observation_payload(text_parts=["saved screen"], image_data_urls=[du])
    text, urls = parse_multimodal_observation_payload(raw)
    assert urls and urls[0].startswith("data:image/")
    assert "saved screen" in text
    obj = json.loads(raw)
    assert obj[JACHIN_MCP_MULTIMODAL_KEY] == 1


def test_tool_may_return_screenshot():
    assert tool_may_return_screenshot("mcp:screenshot")
    assert tool_may_return_screenshot("mcp:puppeteer_screenshot")
    assert not tool_may_return_screenshot("mcp:fetch")


def test_build_observation_user_content_multimodal():
    du = encode_image_bytes_as_data_url(b"\xff\xd8\xff" + b"y" * 80, "image/jpeg")

    def _followup(obs: str, _tool: str) -> str:
        return f"Verification evidence: {obs}\n\n请继续:"

    obs = build_multimodal_observation_payload(text_parts=["ok"], image_data_urls=[du])
    content = build_observation_user_content(
        obs,
        "mcp:screenshot",
        followup_builder=_followup,
    )
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert any(p.get("type") == "image_url" for p in content)
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": content},
    ]
    assert l3_role_execution_full_messages_need_vision_model(msgs)


def test_extract_from_plain_base64_field():
    du = encode_image_bytes_as_data_url(b"\x89PNG" + b"z" * 100, "image/png")
    b64 = du.split(",", 1)[1]
    obs = json.dumps({"status": "ok", "base64": b64, "mime": "image/png"})
    urls = extract_observation_image_data_urls(obs, "mcp:screenshot")
    assert len(urls) == 1
    assert urls[0].startswith("data:image/")
