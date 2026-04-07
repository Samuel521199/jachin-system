"""LiteLLM 流式 acompletion 的供应商侧参数补丁。

DashScope（通义）在部分 HTTP/适配链路下，若未声明增量语义，每帧 ``delta.content`` 可能为「从开头到当前的完整文本」，
客户端若按 OpenAI 习惯做 ``buffer += delta`` 会出现 ``{`` + ``{ "key"`` 套娃复读。
与 ``StreamDeltaNormalizer`` 双保险：此处尽量让上游发真增量，归一化兜底累积帧。
"""
from __future__ import annotations

import os
from typing import Any


def merge_dashscope_stream_incremental_hint(model: str | None, kwargs_chat: dict[str, Any]) -> None:
    """
    对 ``model`` 为 ``dashscope/...`` 的流式请求，向 ``extra_body`` 写入 ``incremental_output: True``（已有键不覆盖）。
    关闭：环境变量 ``JACHIN_DASHSCOPE_STREAM_INCREMENTAL_OUTPUT=0|false|off``。
    """
    if os.environ.get("JACHIN_DASHSCOPE_STREAM_INCREMENTAL_OUTPUT", "").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    ):
        return
    m = (model or "").strip().lower()
    if not m.startswith("dashscope/"):
        return
    raw = kwargs_chat.get("extra_body")
    eb: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    eb.setdefault("incremental_output", True)
    kwargs_chat["extra_body"] = eb
