"""
PMO 子进程复用本机常驻 L3 的 MCP（避免第二套 stdio 子进程拖死主 L3）。

适用：``start-layer3.ps1`` 已起 ``python -m l3_node`` + 后台 ``run_pmo_copilot_skill.py``。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

PMO_DELEGATE_HEADER = "X-Jachin-Pmo-Mcp-Delegate"


def should_delegate_mcp_to_local_l3() -> bool:
    try:
        from l3_node.pmo_copilot_env import is_pmo_copilot_run

        if not is_pmo_copilot_run():
            return False
    except Exception:
        if "--run-pmo-copilot" not in __import__("sys").argv:
            return False
    v = (os.environ.get("JACHIN_PMO_REUSE_L3_MCP") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def local_l3_http_base() -> str:
    port = (os.environ.get("JACHIN_L3_HTTP_PORT") or "18991").strip() or "18991"
    host = (os.environ.get("JACHIN_L3_HTTP_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    return f"http://{host}:{port}"


async def local_l3_http_reachable() -> bool:
    try:
        import httpx
    except ImportError:
        return False
    url = f"{local_l3_http_base()}/api/v3/skills"
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            resp = await client.get(url)
            return resp.status_code == 200
    except Exception:
        return False


async def fetch_mcp_tools_via_local_l3_http() -> list[dict[str, Any]]:
    import httpx

    url = f"{local_l3_http_base()}/api/v3/mcp/tools"
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url, headers={PMO_DELEGATE_HEADER: "1"})
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, dict) or not data.get("ok"):
        raise RuntimeError(f"本机 L3 MCP 工具列表异常: {data!r}")
    tools = data.get("tools")
    if not isinstance(tools, list):
        raise RuntimeError("本机 L3 MCP 工具列表格式错误")
    return tools


async def invoke_mcp_via_local_l3_http(
    raw_name: str,
    arguments: dict[str, Any],
    *,
    timeout: float = 120.0,
) -> str:
    import httpx

    url = f"{local_l3_http_base()}/api/v3/mcp/execute"
    body = {"tool_name": raw_name, "arguments": arguments}
    async with httpx.AsyncClient(timeout=timeout + 5.0) as client:
        resp = await client.post(
            url,
            json=body,
            headers={PMO_DELEGATE_HEADER: "1"},
        )
        data = resp.json()
    if not isinstance(data, dict):
        return json.dumps({"status": "error", "error": "invalid_response"}, ensure_ascii=False)
    if not data.get("ok"):
        return json.dumps(
            {"status": "error", "error": data.get("error") or "mcp_execute_failed"},
            ensure_ascii=False,
        )
    result = data.get("result")
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False)
