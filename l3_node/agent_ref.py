"""
L3 Agent 引擎引用，供 HTTP API 的 agent/run 端点使用。

run_http_server 与 run_ws_server 可能不同步启动，此处提供共享引用，
使 agent/run 能在 engine 就绪后调用 run_agent。
"""
from __future__ import annotations

engine_ref: dict = {}  # {"engine": LiteLLMEngine | None}
