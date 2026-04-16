"""通义模型名：剥离 -YYYY-MM-DD 快照后缀（见 core.llm_provider._normalize_model_for_litellm）。"""
from __future__ import annotations

from core.llm_provider import _normalize_model_for_litellm


def test_normalize_strips_qwen3_max_snapshot_suffix():
    assert _normalize_model_for_litellm("qwen3-max-2026-01-23") == "dashscope/qwen3-max"
    assert _normalize_model_for_litellm("dashscope/qwen3-max-2026-01-23") == "dashscope/qwen3-max"


def test_normalize_strips_qwen35_flash_snapshot_suffix():
    assert _normalize_model_for_litellm("qwen3.5-flash-2026-02-23") == "dashscope/qwen3.5-flash"
    assert _normalize_model_for_litellm("dashscope/qwen3.5-flash-2026-02-23") == "dashscope/qwen3.5-flash"


def test_normalize_unchanged_non_qwen_tail():
    assert _normalize_model_for_litellm("dashscope/text-embedding-v1") == "dashscope/text-embedding-v1"


def test_normalize_ollama_untouched():
    assert _normalize_model_for_litellm("ollama/llama3") == "ollama/llama3"
