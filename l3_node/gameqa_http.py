"""
GameQA HTTP API：供 Jachin 桌面控制台驱动「自治测试 / 影子训练」与实时日志 SSE。

与 MCP 工具共用 ``l3_client.local_mcps.gameqa_mcp.session_service`` 单例（L3 进程内）。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _resp(data: dict, status: int = 200):
    import aiohttp.web

    return aiohttp.web.json_response(data, status=status)


def _default_knowledge_path() -> str:
    try:
        from l3_node.paths import get_app_root

        p = get_app_root() / "l3_client" / "local_mcps" / "gameqa_mcp" / "knowledge" / "tongits_rules.md"
        if p.is_file():
            return str(p)
    except Exception as e:
        logger.debug("[gameqa_http] default knowledge path: %s", e)
    return ""


def _svc():
    from l3_client.local_mcps.gameqa_mcp.session_service import get_gameqa_service

    return get_gameqa_service()


def _stream_sse():
    import aiohttp.web

    r = aiohttp.web.StreamResponse()
    r.headers["Content-Type"] = "text/event-stream"
    r.headers["Cache-Control"] = "no-cache"
    r.headers["Connection"] = "keep-alive"
    r.headers["Access-Control-Allow-Origin"] = "*"
    return r


async def handle_log_stream(request):
    """GET /api/v1/gameqa/log-stream — SSE ``{line: string}``"""
    svc = _svc()
    q = svc.subscribe_logs()
    response = _stream_sse()
    await response.prepare(request)
    last_keepalive = time.monotonic()
    KEEPALIVE = 15.0
    dd = "(unknown)"
    try:
        from l3_client.local_mcps.gameqa_mcp.core.browser_engine import gameqa_data_dir

        dd = str(gameqa_data_dir())
    except Exception as e:
        logger.debug("[gameqa_http] gameqa_data_dir: %s", e)
    pid = os.getpid()
    ts = datetime.now(timezone.utc).isoformat()
    welcome_lines = (
        "[gameqa][sse] 已订阅日志流 · 与本 L3 进程共用 GameQA 会话单例",
        f"[gameqa][sse] l3_pid={pid} utc={ts}",
        f"[gameqa][sse] GAMEQA_DATA_DIR 解析路径={dd!r} （落盘 cdp_http.txt 等与此一致）",
        f"[gameqa][sse] 当前会话 run_id={svc.run_id!r} mode={svc.mode!r}",
    )
    try:
        for wl in welcome_lines:
            chunk = json.dumps({"line": wl}, ensure_ascii=False)
            await response.write(f"data: {chunk}\n\n".encode("utf-8"))
        if hasattr(response, "drain"):
            await response.drain()
        while True:
            try:
                line = await asyncio.wait_for(q.get(), timeout=1.0)
                payload = json.dumps({"line": line}, ensure_ascii=False)
                await response.write(f"data: {payload}\n\n".encode("utf-8"))
                if hasattr(response, "drain"):
                    await response.drain()
                last_keepalive = time.monotonic()
            except asyncio.TimeoutError:
                if time.monotonic() - last_keepalive >= KEEPALIVE:
                    await response.write(b": keepalive\n\n")
                    last_keepalive = time.monotonic()
    except (ConnectionResetError, asyncio.CancelledError, BrokenPipeError):
        pass
    except Exception as e:
        logger.warning("[gameqa_http] log-stream: %s", e)
    finally:
        svc.unsubscribe_logs(q)
    return response


async def handle_launch_test(request):
    """POST /api/v1/gameqa/launch-test JSON {url}"""
    try:
        body = await request.json() if request.body_exists else {}
    except Exception:
        body = {}
    url = (body.get("url") or "").strip()
    if not url:
        return _resp({"ok": False, "error": "url required"}, status=400)
    out = await _svc().launch_test(url)
    return _resp(out, status=200 if out.get("ok") else 500)


async def handle_launch_shadow(request):
    """POST /api/v1/gameqa/launch-shadow JSON {url}"""
    try:
        body = await request.json() if request.body_exists else {}
    except Exception:
        body = {}
    url = (body.get("url") or "").strip()
    if not url:
        return _resp({"ok": False, "error": "url required"}, status=400)
    out = await _svc().launch_shadow(url)
    return _resp(out, status=200 if out.get("ok") else 500)


async def handle_stop(request):
    """POST /api/v1/gameqa/stop"""
    out = await _svc().stop()
    return _resp(out)


async def handle_semantic_state(request):
    """POST /api/v1/gameqa/semantic-state"""
    out = await _svc().get_semantic_state()
    return _resp(out, status=200 if out.get("ok") else 400)


async def handle_execute(request):
    """POST /api/v1/gameqa/execute JSON {element_name}"""
    try:
        body = await request.json() if request.body_exists else {}
    except Exception:
        body = {}
    name = (body.get("element_name") or "").strip()
    if not name:
        return _resp({"ok": False, "error": "element_name required"}, status=400)
    out = await _svc().execute_action(name)
    return _resp(out, status=200 if out.get("ok") else 400)


async def handle_read_knowledge(request):
    """POST /api/v1/gameqa/read-knowledge JSON {file_path?: } 缺省时用仓库内 tongits_rules.md"""
    try:
        body = await request.json() if request.body_exists else {}
    except Exception:
        body = {}
    fp = (body.get("file_path") or "").strip()
    if not fp:
        fp = _default_knowledge_path()
    if not fp:
        return _resp(
            {"ok": False, "error": "file_path empty and default tongits_rules.md not found under app root"},
            status=400,
        )
    raw = _svc().read_knowledge(fp)
    # 避免巨大正文拖垮 UI：仅返回摘要长度，完整 content 仍给（Agent 场景需要）
    if raw.get("ok") and isinstance(raw.get("content"), str):
        c = raw["content"]
        if len(c) > 120_000:
            raw = {**raw, "content_truncated": True, "content": c[:120_000], "content_full_length": len(c)}
    if raw.get("ok"):
        await _svc().emit_log(f"[gameqa] 已读取知识文件 {fp}")
    return _resp(raw, status=200 if raw.get("ok") else 400)


async def handle_audit(request):
    """GET /api/v1/gameqa/audit"""
    raw = _svc().get_audit_log()
    return _resp(raw)


async def handle_training_tail(request):
    """GET /api/v1/gameqa/training-tail?lines=30"""
    try:
        n = int(request.query.get("lines", "30"))
    except ValueError:
        n = 30
    n = max(1, min(500, n))
    raw = _svc().get_training_tail(max_lines=n)
    return _resp(raw)


async def handle_status(request):
    """GET /api/v1/gameqa/status"""
    svc = _svc()
    return _resp(svc.status_payload())


def register_gameqa_routes(app) -> None:
    """注册 GameQA REST + SSE。"""
    app.router.add_get("/api/v1/gameqa/log-stream", handle_log_stream)
    app.router.add_post("/api/v1/gameqa/launch-test", handle_launch_test)
    app.router.add_post("/api/v1/gameqa/launch-shadow", handle_launch_shadow)
    app.router.add_post("/api/v1/gameqa/stop", handle_stop)
    app.router.add_post("/api/v1/gameqa/semantic-state", handle_semantic_state)
    app.router.add_post("/api/v1/gameqa/execute", handle_execute)
    app.router.add_post("/api/v1/gameqa/read-knowledge", handle_read_knowledge)
    app.router.add_get("/api/v1/gameqa/audit", handle_audit)
    app.router.add_get("/api/v1/gameqa/training-tail", handle_training_tail)
    app.router.add_get("/api/v1/gameqa/status", handle_status)
    logger.info("[L3 HTTP] GameQA routes registered (/api/v1/gameqa/*)")
