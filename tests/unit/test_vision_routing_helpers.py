"""core.llm_provider 多模态路由辅助（ReAct 含图时切 VL 模型）。"""
from __future__ import annotations

from core.llm_provider import (
    dashscope_vl_should_omit_openai_tools_for_multimodal,
    litellm_model_supports_openai_multimodal_chat,
    l3_react_full_messages_need_vision_model,
    user_message_content_has_openai_image,
    vision_safe_litellm_fallback_models,
)


def test_user_message_content_has_openai_image():
    assert not user_message_content_has_openai_image("")
    assert not user_message_content_has_openai_image([{"type": "text", "text": "仅文字"}])
    assert user_message_content_has_openai_image(
        [{"type": "text", "text": "看图"}, {"type": "image_url", "image_url": {"url": "https://x/y.png"}}]
    )


def test_litellm_model_supports_openai_multimodal_chat():
    assert litellm_model_supports_openai_multimodal_chat("dashscope/qwen-vl-max")
    assert litellm_model_supports_openai_multimodal_chat("dashscope/qwen2-vl-7b-instruct")
    assert litellm_model_supports_openai_multimodal_chat("dashscope/qwen3.5-plus")
    assert not litellm_model_supports_openai_multimodal_chat("dashscope/qwen3.5-flash-2026-02-23")


def test_dashscope_vl_should_omit_openai_tools_for_multimodal():
    msgs = [
        {"role": "system", "content": "s"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "看图"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,abc"}},
            ],
        },
    ]
    assert dashscope_vl_should_omit_openai_tools_for_multimodal(
        model="dashscope/qwen-vl-max",
        messages=msgs,
    )
    # qwen3.5-plus 在「无 tools」直连时可读图；ReAct 若仍传 tools，DashScope 侧会丢图，须同样省略 tools
    assert dashscope_vl_should_omit_openai_tools_for_multimodal(
        model="dashscope/qwen3.5-plus",
        messages=msgs,
    )
    assert not dashscope_vl_should_omit_openai_tools_for_multimodal(
        model="dashscope/qwen-vl-max",
        messages=[{"role": "user", "content": "仅文字"}],
    )


def test_vision_safe_litellm_fallback_models_strips_text_only_fallbacks():
    fb = vision_safe_litellm_fallback_models(
        primary="dashscope/qwen-vl-max",
        base_fallbacks=[
            "dashscope/qwen3.5-flash-2026-02-23",
            "dashscope/qwen-vl-plus",
        ],
    )
    assert "flash" not in "".join(fb)
    assert any("qwen-vl" in x for x in fb)


def test_l3_react_full_messages_need_vision_model():
    assert not l3_react_full_messages_need_vision_model(None)
    assert not l3_react_full_messages_need_vision_model([])
    assert not l3_react_full_messages_need_vision_model([{"role": "user", "content": "hello"}])
    assert l3_react_full_messages_need_vision_model(
        [
            {"role": "system", "content": "s"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "图片讲述了什么"},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,abc"}},
                ],
            },
        ]
    )
