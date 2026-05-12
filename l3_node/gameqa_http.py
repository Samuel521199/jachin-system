"""
GameQA HTTP：桌面控制台 **点火器**（run-skill）+ SSE 日志 + 停止 / 训练抽样。

业务闭环由 **L3 Agent** 读取 ``l3_node/skills/gameqa/*.md`` 并仅调用 **进程内** ``mcp:tool_*``（见 ``registry.L3_LOCAL_MCP_TOOLS``），
与 ``session_service`` 单例对齐；禁止在此模块直接编排 Playwright 细粒度步骤。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

GAMEQA_MCP_ALLOWLIST_AUTO: list[str] = [
    "mcp:tool_read_knowledge",
    "mcp:tool_launch_test_mode",
    "mcp:tool_refresh_view",
    "mcp:tool_get_semantic_state",
    "mcp:tool_execute_action",
    "mcp:tool_heuristic_dismiss_once",
    "mcp:tool_get_audit_log",
]

GAMEQA_MCP_ALLOWLIST_SHADOW: list[str] = [
    "mcp:tool_read_knowledge",
    "mcp:tool_launch_shadow_mode",
    "mcp:tool_get_semantic_state",
    "mcp:tool_get_audit_log",
]

_gameqa_skill_task: asyncio.Task | None = None


def _resp(data: dict, status: int = 200):
    import aiohttp.web

    return aiohttp.web.json_response(data, status=status)


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


def _resolve_skill_path(skill_name: str) -> Path | None:
    sn = (skill_name or "").strip()
    if not sn:
        return None
    p = Path(sn)
    if p.is_file():
        return p.resolve()
    base = Path(__file__).resolve().parent / "skills" / "gameqa"
    fn = sn if sn.endswith(".md") else f"{sn}.md"
    cand = (base / fn).resolve()
    if cand.is_file():
        return cand
    return None


def _allowlist_for_skill(skill_path: Path) -> list[str]:
    if "shadow" in skill_path.name.lower():
        return list(GAMEQA_MCP_ALLOWLIST_SHADOW)
    return list(GAMEQA_MCP_ALLOWLIST_AUTO)


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


async def handle_run_skill(request):
    """
    POST /api/v1/gameqa/run-skill
    JSON: { "skill_name", "url", "rules_path?": "", "max_iterations?": 32 }
    异步启动 Agent；立即返回 ``started``（独占：同时仅允许一条 GameQA skill 跑）。
    """
    global _gameqa_skill_task
    if _gameqa_skill_task and not _gameqa_skill_task.done():
        return _resp({"ok": False, "error": "GameQA Agent 任务尚在运行"}, status=409)
    try:
        body = await request.json() if request.body_exists else {}
    except Exception:
        body = {}
    skill_name = (body.get("skill_name") or "").strip()
    url = (body.get("url") or "").strip()
    rules_path = (body.get("rules_path") or "").strip()
    if not skill_name:
        return _resp({"ok": False, "error": "skill_name required"}, status=400)
    if not url:
        return _resp({"ok": False, "error": "url required"}, status=400)
    sp = _resolve_skill_path(skill_name)
    if not sp:
        return _resp({"ok": False, "error": f"skill file not found: {skill_name!r}"}, status=404)
    try:
        mi = int(body.get("max_iterations") or 42)
    except (TypeError, ValueError):
        mi = 42
    mi = max(1, min(mi, 64))
    allow = _allowlist_for_skill(sp)

    try:
        skill_text = sp.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return _resp({"ok": False, "error": f"read skill: {e!r}"}, status=500)

    user_input = (
        f"[GameQA · run-skill 宿主上下文]\n"
        f"target_url: {url}\n"
        f"rules_path: {rules_path}\n"
        f"skill_file: {sp}\n\n"
        f"--- SKILL BEGIN ---\n{skill_text}\n--- SKILL END ---\n\n"
        f"请严格按 SKILL 中的 Persona、工具白名单与 SOP 执行（ReAct）。\n"
    )

    async def _body() -> None:
        svc = _svc()
        await svc.emit_log(f"[gameqa][run-skill] 启动 Agent skill={sp.name!r} allowlist={allow}")

        async def _on_chunk(s: str) -> None:
            frag = (s or "").replace("\r", " ").replace("\n", " ")
            if not frag.strip():
                return
            if len(frag) > 3600:
                frag = frag[:3600] + "…"
            await svc.emit_log(f"[gameqa][agent] {frag}")

        try:
            from l3_node.agent_ref import engine_ref

            engine = engine_ref.get("engine")
        except Exception:
            engine = None
        if not engine:
            await svc.emit_log("[gameqa][run-skill] 失败：L3 engine 未就绪（请先连接 WebSocket 或完成 bootstrap）")
            return
        try:
            from l3_node.agent_core import run_agent

            ans = await run_agent(
                user_input,
                engine,
                max_iterations=mi,
                _allowed_skills_override=allow,
                on_chunk=_on_chunk,
                implicit_attribution={"channel": "gameqa_run_skill", "source": "gameqa_console"},
            )
            tail = (ans or "").strip()
            if len(tail) > 4000:
                tail = tail[:4000] + "…"
            await svc.emit_log(f"[gameqa][agent] Final Answer 摘要: {tail or '(empty)'}")
        except asyncio.CancelledError:
            await svc.emit_log("[gameqa][run-skill] Agent 任务已取消")
            raise
        except Exception as e:
            logger.exception("[gameqa] run-skill Agent")
            await svc.emit_log(f"[gameqa][run-skill] Agent 异常: {e!r}")

    t = asyncio.create_task(_body())

    def _clear(_: asyncio.Task) -> None:
        global _gameqa_skill_task
        if _gameqa_skill_task is t:
            _gameqa_skill_task = None

    _gameqa_skill_task = t
    t.add_done_callback(_clear)
    return _resp(
        {
            "ok": True,
            "started": True,
            "skill_name": skill_name,
            "skill_path": str(sp),
            "max_iterations": mi,
        }
    )


async def handle_stop(request):
    """POST /api/v1/gameqa/stop — 关闭浏览器会话；不取消后台 Agent 任务（如需可后续增加 cancel）。"""
    out = await _svc().stop()
    return _resp(out)


async def handle_training_tail(request):
    """GET /api/v1/gameqa/training-tail?lines=30"""
    try:
        n = int(request.query.get("lines", "30"))
    except ValueError:
        n = 30
    n = max(1, min(500, n))
    raw = _svc().get_training_tail(max_lines=n)
    return _resp(raw)


def register_gameqa_routes(app) -> None:
    """GameQA：SSE + run-skill + stop + training-tail。"""
    app.router.add_get("/api/v1/gameqa/log-stream", handle_log_stream)
    app.router.add_post("/api/v1/gameqa/run-skill", handle_run_skill)
    app.router.add_post("/api/v1/gameqa/stop", handle_stop)
    app.router.add_get("/api/v1/gameqa/training-tail", handle_training_tail)
    logger.info("[L3 HTTP] GameQA routes: log-stream, run-skill, stop, training-tail")
