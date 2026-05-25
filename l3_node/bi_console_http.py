"""
BI 分析控制台 HTTP：手动 SSE 跑 ``scripts/run_bi_daily_report.py`` + 定时配置（``bi_console_scheduler``）。

与 ``l3_node/primitives/skills/bi/scheduler.py``（YAML 配置）独立。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_bi_stream_active: bool = False
_bi_start_lock: asyncio.Lock = asyncio.Lock()
_bi_proc: asyncio.subprocess.Process | None = None
_bi_user_abort: bool = False


def _json_response(data: Any, status: int = 200):
    import aiohttp.web

    return aiohttp.web.json_response(data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False))


def _stream_response():
    import aiohttp.web

    r = aiohttp.web.StreamResponse()
    r.headers["Content-Type"] = "text/event-stream"
    r.headers["Cache-Control"] = "no-cache"
    r.headers["Connection"] = "keep-alive"
    r.headers["Access-Control-Allow-Origin"] = "*"
    return r


async def _bi_subprocess_sse_stream(request: Any) -> Any:
    """GET /api/v1/bi-daily-report/stream — 子进程 SSE（等价 python scripts/run_bi_daily_report.py）。"""
    global _bi_stream_active, _bi_proc, _bi_user_abort

    from l3_node.paths import bi_daily_report_script_path, get_app_root

    root = get_app_root()
    script = bi_daily_report_script_path()
    if not script.is_file():
        return _json_response(
            {"ok": False, "error": f"脚本不存在: {script}"},
            status=404,
        )

    cmd = [sys.executable, str(script)]

    async with _bi_start_lock:
        if _bi_stream_active:
            response0 = _stream_response()
            await response0.prepare(request)
            err = json.dumps(
                {
                    "type": "error",
                    "message": "BI 战报任务已在执行中，请待当前完成后再试。",
                },
                ensure_ascii=False,
            )
            await response0.write(f"data: {err}\n\n".encode("utf-8"))
            return response0
        _bi_stream_active = True
    _bi_user_abort = False

    try:
        try:
            _ja = str(Path(root).resolve())
        except Exception:
            _ja = str(root)
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "JACHIN_APP_ROOT": _ja}
        response = _stream_response()
        keepalive_sec = 15.0
        last_keepalive = 0.0

        async def _write_line_obj(line: str) -> None:
            payload = {"line": line}
            await response.write(
                f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
            )
            if hasattr(response, "drain"):
                await response.drain()

        async def _write_event(obj: dict[str, Any]) -> None:
            await response.write(
                f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")
            )
            if hasattr(response, "drain"):
                await response.drain()

        await response.prepare(request)
        await _write_line_obj(f"> 启动: {' '.join(cmd)}")
        await _write_line_obj(f"> 工作目录: {_ja}")

        line_q: asyncio.Queue[str] = asyncio.Queue()

        async def _pump() -> int:
            global _bi_proc
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=str(root),
                env=env,
            )
            _bi_proc = proc
            code = 1
            try:
                if proc.stdout is None:
                    if proc.returncode is None:
                        proc.kill()
                    with contextlib.suppress(Exception):
                        await proc.wait()
                    return 2
                while True:
                    try:
                        line = await asyncio.wait_for(proc.stdout.readline(), timeout=1.25)
                    except asyncio.TimeoutError:
                        if _bi_user_abort and proc.returncode is None:
                            with contextlib.suppress(Exception):
                                proc.terminate()
                            with contextlib.suppress(asyncio.TimeoutError, Exception):
                                await asyncio.wait_for(proc.wait(), timeout=2.0)
                            if proc.returncode is None:
                                with contextlib.suppress(Exception):
                                    proc.kill()
                                with contextlib.suppress(Exception):
                                    await proc.wait()
                        continue
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace").rstrip("\n\r")
                    with contextlib.suppress(Exception):
                        line_q.put_nowait(text)
                code = await proc.wait()
            except asyncio.CancelledError:
                if proc.returncode is None:
                    with contextlib.suppress(Exception):
                        proc.terminate()
                        await asyncio.wait_for(proc.wait(), timeout=6.0)
                raise
            finally:
                _bi_proc = None
            return int(code)

        pump_task = asyncio.create_task(_pump())
        ok = False
        cancelled = False
        try:
            while not pump_task.done():
                try:
                    text = await asyncio.wait_for(line_q.get(), timeout=0.5)
                    await _write_line_obj(text)
                except asyncio.TimeoutError:
                    now = time.monotonic()
                    if now - last_keepalive >= keepalive_sec:
                        await response.write(b": keepalive\n\n")
                        if hasattr(response, "drain"):
                            await response.drain()
                        last_keepalive = now
            code = await pump_task
            while True:
                try:
                    text = line_q.get_nowait()
                    await _write_line_obj(text)
                except asyncio.QueueEmpty:
                    break
            cancelled = _bi_user_abort
            ok = code == 0 and not cancelled
        except asyncio.CancelledError:
            pump_task.cancel()
            with contextlib.suppress(Exception):
                await pump_task
            cancelled = True
            raise
        finally:
            if not pump_task.done():
                pump_task.cancel()
                with contextlib.suppress(Exception):
                    await pump_task

        await _write_event({"type": "done", "ok": ok, "cancelled": cancelled, "exit_code": code})
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    except Exception as e:
        logger.exception("[bi_console_http] stream failed: %s", e)
        try:
            await _write_event({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        _bi_stream_active = False
        _bi_proc = None
    return response


async def handle_bi_daily_report_stop(request: Any) -> Any:
    """POST /api/v1/bi-daily-report/stop"""
    global _bi_proc, _bi_user_abort, _bi_stream_active
    p = _bi_proc
    had_active = p is not None and p.returncode is None
    _bi_user_abort = True
    if not had_active:
        if _bi_stream_active:
            _bi_stream_active = False
        return _json_response(
            {
                "ok": True,
                "active_child": False,
                "message": "无运行中的 BI 子进程；已记录停止。",
            }
        )
    try:
        p.terminate()
        try:
            await asyncio.wait_for(p.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            p.kill()
            await p.wait()
    except Exception as e:
        logger.warning("[bi_console_http] stop failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)
    return _json_response({"ok": True, "active_child": True, "message": "已发送停止信号"})


async def handle_bi_schedule_toggle(request: Any) -> Any:
    """POST /api/v1/bi-daily-report/schedule/toggle"""
    try:
        body = await request.json() if request.body_exists else {}
    except Exception as e:
        return _json_response({"ok": False, "error": f"JSON 解析失败: {e}"}, status=400)
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        return _json_response({"ok": False, "error": "缺少布尔字段 enabled"}, status=400)
    try:
        from l3_node.jobs.bi_console_scheduler import apply_bi_console_schedule, scheduler_status

        hb = body.get("hour_beijing")
        mb = body.get("minute_beijing")
        hr = body.get("hourly_recurring")
        hourly_recurring: bool | None = None
        if isinstance(hr, bool):
            hourly_recurring = hr
        elif hr is not None and str(hr).strip() != "":
            hourly_recurring = str(hr).lower() in ("1", "true", "yes", "on")
        r = apply_bi_console_schedule(
            enabled=enabled,
            hour_beijing=int(hb) if hb is not None else None,
            minute_beijing=int(mb) if mb is not None else None,
            hourly_recurring=hourly_recurring,
        )
        st = scheduler_status()
        return _json_response({**r, "ok": True, **st})
    except Exception as e:
        logger.warning("[bi_console_http] schedule toggle failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def handle_bi_schedule_status(request: Any) -> Any:
    """GET /api/v1/bi-daily-report/schedule/status"""
    try:
        from l3_node.jobs.bi_console_scheduler import scheduler_status

        return _json_response({"ok": True, **scheduler_status()})
    except Exception as e:
        return _json_response({"ok": False, "active": False, "error": str(e)}, status=500)


async def handle_bi_schedule_log_stream(request: Any) -> Any:
    """GET /api/v1/bi-daily-report/schedule/log-stream"""
    try:
        from l3_node.jobs.bi_console_scheduler import (
            bi_scheduled_log_ring_snapshot,
            subscribe_bi_scheduled_log,
            unsubscribe_bi_scheduled_log,
        )
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)

    q = subscribe_bi_scheduled_log()
    response = _stream_response()
    try:
        await response.prepare(request)
        for obj in bi_scheduled_log_ring_snapshot():
            await response.write(
                f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")
            )
        while True:
            try:
                obj = await asyncio.wait_for(q.get(), timeout=20.0)
            except asyncio.TimeoutError:
                await response.write(b": keepalive\n\n")
                if hasattr(response, "drain"):
                    await response.drain()
                continue
            await response.write(
                f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")
            )
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    except Exception as e:
        logger.warning("[bi_console_http] schedule log stream: %s", e)
    finally:
        unsubscribe_bi_scheduled_log(q)
    return response


def register_bi_console_routes(app: Any) -> None:
    """注册 BI 控制台路由到 L3 HTTP app。"""
    app.router.add_get("/api/v1/bi-daily-report/stream", _bi_subprocess_sse_stream)
    app.router.add_post("/api/v1/bi-daily-report/stop", handle_bi_daily_report_stop)
    app.router.add_get("/api/v1/bi-daily-report/schedule/status", handle_bi_schedule_status)
    app.router.add_get("/api/v1/bi-daily-report/schedule/log-stream", handle_bi_schedule_log_stream)
    app.router.add_post("/api/v1/bi-daily-report/schedule/toggle", handle_bi_schedule_toggle)
