"""DashScope LiteLLM：可选 OpenAI image_url → 原生 image/text（默认关，兼容 LiteLLM 校验）。"""

import pytest

from l3_node.dashscope_multimodal_normalize import (
    maybe_normalize_messages_for_dashscope_litellm,
)


def test_non_dashscope_unchanged():
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}},
            ],
        }
    ]
    out = maybe_normalize_messages_for_dashscope_litellm(msgs, model="gpt-4o")
    assert out[0]["content"][0]["type"] == "text"


def test_dashscope_default_keeps_openai_mm_for_litellm():
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "看图"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,abcd"}},
            ],
        }
    ]
    out = maybe_normalize_messages_for_dashscope_litellm(msgs, model="dashscope/qwen-vl-max")
    assert out[0]["content"][0]["type"] == "text"
    assert out[0]["content"][1]["type"] == "image_url"


def test_dashscope_converts_when_env_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JACHIN_LITELLM_DASHSCOPE_NATIVE_MULTIMODAL", "1")
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "看图"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,abcd"}},
            ],
        }
    ]
    out = maybe_normalize_messages_for_dashscope_litellm(msgs, model="dashscope/qwen-vl-max")
    c = out[0]["content"]
    assert c[0] == {"text": "看图"}
    assert c[1]["image"].startswith("data:image/jpeg;base64,")


def test_plain_string_user_unchanged():
    msgs = [{"role": "user", "content": "hello"}]
    out = maybe_normalize_messages_for_dashscope_litellm(msgs, model="dashscope/qwen3.5-plus")
    assert out[0]["content"] == "hello"
