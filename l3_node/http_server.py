"""
L3 HTTP API - 技能列表与执行

供 Skill Matrix 等前端调用。技能执行在 L3 本地进行（~/.jachin/l3_skill_cache/）。
端口 18991 系列，与 L2(18888)、WebSocket(18981) 分离。
HR 透析镜执行成功后，分析报告写入 data/hr_analysis/ 及 ~/.jachin/volumes/ 对应数据卷。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import queue
import threading
from pathlib import Path
from typing import Any

from l3_node.paths import kalaroko_default_e2e_script_path

logger = logging.getLogger("l3_node")

L3_HTTP_PORT = 18991

# K11 统合冒烟：单路 SSE 子进程（与 /api/v1/monitor/stream 同形态）
_k11_unified_smoke_stream_active: bool = False
_k11_unified_smoke_start_lock: asyncio.Lock = asyncio.Lock()
_k11_unified_smoke_proc: "asyncio.subprocess.Process | None" = None


_HR_SKILL_IDS = (
    "jpp:com.jachin.hr.analyzer4",
)


def _tools_to_skill_infos(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将 Wasm 技能转为 SkillInfo 格式（供 Skill Matrix 展示），同名同版本去重"""
    result = []
    seen_key: set[tuple[str, str]] = set()
    for t in tools:
        tid = t.get("id", "")
        params = t.get("params", ["input"])
        # HR 透析镜：参数为 target_role/resume_filename/target_dir，统一为单一 execute 能力
        if tid in _HR_SKILL_IDS:
            caps = [{"name": "execute", "description": t.get("desc", "根据岗位要求分析简历，输出 Markdown 报告")}]
        else:
            caps = [{"name": p if isinstance(p, str) else p.get("name", ""), "description": ""} for p in params]
        name = t.get("_name") or t.get("label") or tid
        version = "1.0.0"
        dedup_key = (name, version)
        if dedup_key in seen_key:
            continue
        seen_key.add(dedup_key)
        result.append({
            "skill_id": tid,
            "name": name,
            "version": version,
            "description": t.get("desc"),
            "status": "installed",
            "capabilities": caps if caps else [{"name": "execute", "description": t.get("desc", "")}],
            "permissions": [],
            "item_id": t.get("_item_id"),
        })
    return result


async def _handle_skills_list(request) -> "aiohttp.web.Response":
    """GET /api/v3/skills - 仅返回 Wasm 技能（L2 同步），不含 Native Core"""
    import sys
    try:
        from l3_node.primitives import load_skills_for_ui
        tools = load_skills_for_ui(allowed_skills=None)
        skills = _tools_to_skill_infos(tools)
        print(f"[L3 HTTP] GET /api/v3/skills 返回 {len(skills)} 项技能", file=sys.stderr, flush=True)
        return _json_response(skills)
    except Exception as e:
        logger.warning("[L3 HTTP] list skills failed: %s", e)
        return _json_response([], status=500)


async def _handle_skills_uninstall(request) -> "aiohttp.web.Response":
    """DELETE /api/v3/skills/{item_id} - 卸载技能（代理到 L2，供浏览器控制台在无 Tauri 时使用）"""
    import sys
    from pathlib import Path

    item_id = request.match_info.get("item_id", "").strip()
    if not item_id:
        return _json_response({"ok": False, "error": "item_id required"}, status=400)

    purge_data = "true" in (request.query.get("purge_data") or "").lower()

    # 读取 L2 网关配置获取 sub_account_id
    cfg_path = Path.home() / ".jachin" / "l2_gateway_config.json"
    sub_account_id = ""
    l2_url = "http://localhost:18888"
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            sub_account_id = (data.get("sub_account_id") or "").strip()
            l2_url = (data.get("l2_base_url") or l2_url).rstrip("/")
        except Exception:
            pass

    if not sub_account_id:
        return _json_response(
            {"ok": False, "error": "未找到 sub_account_id，请先完成 L2 网关配对"},
            status=401,
        )

    print(f"[L3 HTTP] DELETE /api/v3/skills/{item_id} purge_data={purge_data} -> L2", file=sys.stderr, flush=True)
    try:
        import httpx
        delete_url = f"{l2_url}/api/v2/inventory/skills/{item_id}?purge_data={purge_data}"
        with httpx.Client(timeout=30.0) as client:
            r = client.delete(
                delete_url,
                headers={"X-Sub-Account-Id": sub_account_id},
            )
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if not body:
            body = {"ok": r.is_success, "error": r.text or "未知错误"}

        if not r.is_success:
            return _json_response(
                {"ok": False, "error": body.get("error") or body.get("detail") or r.text or "卸载失败"},
                status=r.status_code,
            )

        # L2 已将技能移入回收站（含 inventory/cache/builtin），无需再删 cache
        if body.get("ok") is False:
            return _json_response(body, status=400)
        return _json_response(body)
    except Exception as e:
        logger.warning("[L3 HTTP] uninstall %s failed: %s", item_id, e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_recycle_bin_list(request) -> "aiohttp.web.Response":
    """GET /api/v3/recycle-bin/skills - 列出回收站中的技能"""
    try:
        from core.recycle_bin import list_recycle_bin
        items = list_recycle_bin()
        return _json_response({"items": items, "count": len(items)})
    except Exception as e:
        logger.warning("[L3 HTTP] recycle bin list failed: %s", e)
        return _json_response({"items": [], "count": 0}, status=500)


async def _handle_recycle_bin_restore(request) -> "aiohttp.web.Response":
    """POST /api/v3/recycle-bin/skills/{recycle_id}/restore - 从回收站恢复"""
    recycle_id = request.match_info.get("recycle_id", "").strip()
    if not recycle_id:
        return _json_response({"ok": False, "error": "recycle_id required"}, status=400)
    try:
        from core.recycle_bin import restore_from_recycle_bin
        result = restore_from_recycle_bin(recycle_id)
        if not result.get("ok"):
            return _json_response(result, status=400)
        # 触发 L2 热重载
        cfg_path = Path.home() / ".jachin" / "l2_gateway_config.json"
        l2_url = "http://localhost:18888"
        if cfg_path.exists():
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
                l2_url = (data.get("l2_base_url") or l2_url).rstrip("/")
            except Exception:
                pass
            try:
                import httpx
                with httpx.Client(timeout=5.0) as client:
                    client.post(f"{l2_url}/api/v2/inventory/reload")
            except Exception:
                pass  # L2 可能未启动
            try:
                from l3_node.skill_sync import sync_skills_from_l2
                sync_skills_from_l2()  # 拉取恢复的技能到 L3 缓存
            except Exception:
                pass
        return _json_response(result)
    except Exception as e:
        logger.warning("[L3 HTTP] recycle bin restore failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_recycle_bin_delete(request) -> "aiohttp.web.Response":
    """DELETE /api/v3/recycle-bin/skills/{recycle_id} - 彻底删除"""
    recycle_id = request.match_info.get("recycle_id", "").strip()
    if not recycle_id:
        return _json_response({"ok": False, "error": "recycle_id required"}, status=400)
    try:
        from core.recycle_bin import permanent_delete_from_recycle_bin
        result = permanent_delete_from_recycle_bin(recycle_id)
        if not result.get("ok"):
            return _json_response(result, status=400)
        return _json_response(result)
    except Exception as e:
        logger.warning("[L3 HTTP] recycle bin delete failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_skills_execute(request) -> "aiohttp.web.Response":
    """POST /api/v3/skills/{skill_id}/execute"""
    import sys
    from core.wasm_runner import WasmExecutionError
    skill_id = request.match_info.get("skill_id", "")
    print(f"[L3 HTTP] 收到执行请求 skill_id={skill_id}", file=sys.stderr, flush=True)
    if not skill_id:
        return _json_response({"success": False, "error": "skill_id required"}, status=400)
    try:
        body = await request.json() if request.body_exists else {}
    except Exception as e:
        body = {}
        print(f"[Skill Execute] 解析请求体失败: {e}", file=sys.stderr, flush=True)
    capability_name = body.get("capability_name", "execute")
    input_data = body.get("input_data", {}) or {}
    # 控制面板直接执行 HR 透析镜时，若未传参则注入默认值
    # 默认批量模式：分析 target_dir 下所有简历（张三李四王五等），而非仅单份
    if skill_id.strip() in _HR_SKILL_IDS and not input_data.get("target_role"):
        input_data = {**input_data, "target_role": "backend_engineer"}
    if skill_id.strip() in _HR_SKILL_IDS and not input_data.get("resume_filename") and not input_data.get("target_dir"):
        input_data = {**input_data, "target_dir": "data/hr_resumes"}
    print(f"[Skill Execute] 开始 skill_id={skill_id} capability={capability_name} input={json.dumps(input_data, ensure_ascii=False)[:200]}", file=sys.stderr, flush=True)
    try:
        from l3_node.primitives import run_tool
        inp = json.dumps({**input_data, "capability": capability_name}, ensure_ascii=False)
        result = run_tool(skill_id, inp, allowed_skills=None)
        print(f"[Skill Execute] 完成 skill_id={skill_id} result_len={len(str(result))} result_preview={str(result)[:300]}", file=sys.stderr, flush=True)
        if isinstance(result, str) and result.startswith("[") and "]" in result:
            if "权限拒绝" in result or "未知" in result or "失败" in result:
                print(f"[Skill Execute] 失败 skill_id={skill_id} error={result}", file=sys.stderr, flush=True)
                return _json_response({"success": False, "result": None, "error": result})
        if isinstance(result, str) and not result.strip():
            result = "[执行完成但无输出，请检查 Wasm 插件或 execute ABI 返回值]"
        # HR 透析镜：loader 已写入 data/hr_analysis/ 及 volume，取路径供响应
        resp = {"success": True, "result": {"text": result}, "error": None}
        if skill_id.strip() in _HR_SKILL_IDS:
            persist_mod = __import__("l3_node.hr_loader", fromlist=["get_hr_analysis_persist"]).get_hr_analysis_persist()
            saved_path = persist_mod.get_last_saved_path() if persist_mod else None
            if saved_path:
                resp["result"]["saved_path"] = saved_path
        return _json_response(resp)
    except WasmExecutionError as e:
        print(f"[Skill Execute] WASM 异常 skill_id={skill_id} error={e}", file=sys.stderr, flush=True)
        logger.warning("[L3 HTTP] execute %s WASM failed: %s", skill_id, e)
        return _json_response({
            "success": False,
            "result": None,
            "error": str(e),
            "wasm_details": getattr(e, "wasm_details", ""),
        }, status=500)
    except Exception as e:
        print(f"[Skill Execute] 异常 skill_id={skill_id} error={e}", file=sys.stderr, flush=True)
        logger.warning("[L3 HTTP] execute %s failed: %s", skill_id, e)
        return _json_response({"success": False, "result": None, "error": str(e)}, status=500)


async def _handle_skills_execute_stream(request) -> "aiohttp.web.Response":
    """POST /api/v3/skills/{skill_id}/execute/stream - SSE 流式进度，供 HR 透析镜批量模式实时展示"""
    import sys
    from core.wasm_runner import WasmExecutionError

    skill_id = request.match_info.get("skill_id", "")
    if not skill_id:
        return _json_response({"success": False, "error": "skill_id required"}, status=400)
    try:
        body = await request.json() if request.body_exists else {}
    except Exception as e:
        body = {}
    capability_name = body.get("capability_name", "execute")
    input_data = body.get("input_data", {}) or {}
    if skill_id.strip() in _HR_SKILL_IDS and not input_data.get("target_role"):
        input_data = {**input_data, "target_role": "backend_engineer"}
    if skill_id.strip() in _HR_SKILL_IDS and not input_data.get("resume_filename") and not input_data.get("target_dir"):
        input_data = {**input_data, "target_dir": "data/hr_resumes"}

    if skill_id.strip() not in _HR_SKILL_IDS:
        return _json_response({"success": False, "error": "流式接口仅支持 HR 透析镜技能"}, status=400)

    ndjson_queue: queue.Queue[str] = queue.Queue()
    thread_result: dict[str, Any] = {"done": False, "error": None, "result": None}

    def _run_in_thread() -> None:
        try:
            from l3_node.primitives import run_tool
            inp = json.dumps({**input_data, "capability": capability_name}, ensure_ascii=False)
            r = run_tool(skill_id, inp, allowed_skills=None, ndjson_queue=ndjson_queue)
            thread_result["result"] = r
        except Exception as e:
            thread_result["error"] = str(e)
        finally:
            thread_result["done"] = True
            ndjson_queue.put(json.dumps({"status": "thread_done"}))

    thread = threading.Thread(target=_run_in_thread, daemon=True)
    thread.start()

    from l3_node.primitives.tools.loader import _extract_stem_from_hr_report, _fetch_skill_config, _get_hr_plugin_config_defaults
    persist_mod = __import__("l3_node.hr_loader", fromlist=["get_hr_analysis_persist"]).get_hr_analysis_persist()
    if not persist_mod:
        persist_hr_analysis_batch_item = lambda *a, **k: None
    else:
        persist_hr_analysis_batch_item = persist_mod.persist_hr_analysis_batch_item

    cfg = {}
    try:
        cfg = _fetch_skill_config(skill_id.replace("jpp:", ""))
        cfg = {**_get_hr_plugin_config_defaults(skill_id), **(cfg or {})}
    except Exception:
        pass

    response = _stream_response()
    await response.prepare(request)

    async def _sse_generator():
        seen_done = False
        while not seen_done:
            try:
                line = ndjson_queue.get(timeout=0.3)
            except queue.Empty:
                if thread_result["done"]:
                    break
                await asyncio.sleep(0.05)
                continue
            line = (line or "").strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            status = item.get("status")
            if status == "thread_done":
                break
            if status == "done":
                seen_done = True
                payload = {"status": "done"}
                await response.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
                break
            if status == "progress":
                report = item.get("report_content")
                fn = item.get("filename") or ""
                stem = (Path(fn).stem.replace("_resume", "").replace("_analysis", "").strip() or Path(fn).stem) if fn else ""
                import re
                if not stem or re.match(r"^resume_\d+$", stem):
                    stem = _extract_stem_from_hr_report(report or "") or stem or "unknown"
                if report and stem:
                    persist_hr_analysis_batch_item(skill_id, report, stem, config=cfg)
                payload = {"status": "progress", "filename": fn, "current": item.get("current"), "total": item.get("total")}
                await response.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
        thread.join(timeout=2.0)
        if thread_result["error"]:
            await response.write(f"data: {json.dumps({'status': 'error', 'error': thread_result['error']}, ensure_ascii=False)}\n\n".encode("utf-8"))

    await _sse_generator()
    return response


def _stream_response() -> "aiohttp.web.StreamResponse":
    """创建 SSE 流式响应（含 CORS，供 Tauri WebView 跨域 fetch）"""
    import aiohttp.web
    r = aiohttp.web.StreamResponse()
    r.headers["Content-Type"] = "text/event-stream"
    r.headers["Cache-Control"] = "no-cache"
    r.headers["Connection"] = "keep-alive"
    r.headers["Access-Control-Allow-Origin"] = "*"
    return r


async def _handle_scheduler_add_job(request) -> "aiohttp.web.Response":
    """POST /api/scheduler/add_job - 添加自动化招聘定时任务"""
    try:
        body = await request.json() if request.body_exists else {}
    except Exception as e:
        return _json_response({"ok": False, "error": f"请求体解析失败: {e}"}, status=400)
    job_name = (body.get("job_name") or "").strip()
    if not job_name:
        return _json_response({"ok": False, "error": "job_name 不能为空"}, status=400)
    job_config = {
        "job_name": job_name,
        "jd_config_path": (body.get("jd_config_path") or "").strip(),
        "jd_content": (body.get("jd_content") or "").strip(),
        "cdp_url": body.get("cdp_url", "http://127.0.0.1:9222"),
        "max_count": int(body.get("max_count", 50)),
        "filter_tab": (body.get("filter_tab") or "全部").strip(),
        "request_resume": body.get("request_resume", True),
        "analyze_threshold": int(body.get("analyze_threshold", 2)),
        "output_dir": (body.get("output_dir") or "").strip(),
        "focus_keywords": (body.get("focus_keywords") or "").strip(),
        "strictness": (body.get("strictness") or "standard").strip(),
    }
    try:
        from l3_node.hr_loader import get_recruitment_scheduler
        sched = get_recruitment_scheduler()
        if not sched:
            return _json_response({"ok": False, "error": "HR 招聘 MCP 包未找到，请从 L1 订阅 com.jachin.hr.recruitment"}, status=503)
        result = sched.add_scheduled_job(job_config)
        return _json_response(result)
    except Exception as e:
        logger.warning("[L3 HTTP] scheduler add_job failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_scheduler_remove_job(request) -> "aiohttp.web.Response":
    """POST /api/scheduler/remove_job - 移除自动化招聘定时任务。job_name 为空则停止所有岗位。"""
    try:
        body = await request.json() if request.body_exists else {}
    except Exception:
        body = {}
    job_name = (body.get("job_name") or "").strip()
    try:
        from l3_node.hr_loader import get_recruitment_scheduler
        sched = get_recruitment_scheduler()
        if not sched:
            return _json_response({"ok": False, "error": "HR 招聘 MCP 包未找到，请从 L1 订阅 com.jachin.hr.recruitment"}, status=503)
        remove_scheduled_job, list_scheduled_jobs, set_recruitment_stopped = sched.remove_scheduled_job, sched.list_scheduled_jobs, sched.set_recruitment_stopped
        if job_name:
            result = remove_scheduled_job(job_name)
        else:
            set_recruitment_stopped(True)
            jobs = list_scheduled_jobs()
            removed = []
            for j in jobs:
                folder = (j.get("job_folder") or "").strip()
                if folder:
                    r = remove_scheduled_job(folder)
                    if r.get("ok"):
                        removed.extend(r.get("removed", []))
            result = {"ok": True, "message": "已停止所有无人值守招聘任务", "removed": removed}
        return _json_response(result)
    except Exception as e:
        logger.warning("[L3 HTTP] scheduler remove_job failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_system_logs_stream(request) -> "aiohttp.web.StreamResponse":
    """GET /api/system/logs/stream - SSE 实时日志流，供前端控制台订阅"""
    from l3_node.log_broadcaster import consume_logs, format_sse_event
    import time
    response = _stream_response()
    await response.prepare(request)
    last_keepalive = time.monotonic()
    KEEPALIVE_INTERVAL = 15  # 秒，避免 Node undici BodyTimeoutError（静默流被判定超时）
    try:
        welcome = format_sse_event("[L3 全息监控] 已连接，等待日志流…", "INFO", time.time())
        await response.write(welcome.encode("utf-8"))
        if hasattr(response, "drain"):
            await response.drain()
        while True:
            item = await asyncio.to_thread(consume_logs)
            if item:
                msg, level, ts = item
                await response.write(format_sse_event(msg, level, ts).encode("utf-8"))
                last_keepalive = time.monotonic()
            elif time.monotonic() - last_keepalive >= KEEPALIVE_INTERVAL:
                await response.write(b": keepalive\n\n")
                last_keepalive = time.monotonic()
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    except Exception as e:
        logger.debug("[L3 HTTP] logs stream closed: %s", e)
    return response


async def _handle_monitor_kalaroko_stream(request) -> "aiohttp.web.StreamResponse":
    """GET /api/v1/monitor/stream — Kalaroko 默认场景多轮 E2E + 末尾 AI 综合分析，SSE 实时行日志。"""
    import importlib.util
    import sys
    import time

    try:
        runs = int(request.query.get("runs", "4"))
    except ValueError:
        runs = 4
    try:
        interval = int(request.query.get("interval", "30"))
    except ValueError:
        interval = 30
    skip_pw = str(request.query.get("skip_playwright", "")).lower() in ("1", "true", "yes", "on")

    response = _stream_response()
    await response.prepare(request)

    root = Path(__file__).resolve().parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    line_q: asyncio.Queue = asyncio.Queue()

    def line_sink(line: str) -> None:
        try:
            line_q.put_nowait(line)
        except Exception:
            pass

    async def _load_and_run() -> dict[str, Any]:
        script = kalaroko_default_e2e_script_path()
        if not script.is_file():
            return {
                "ok": False,
                "exit_code": 2,
                "error": f"缺少巡检脚本: {script}（frozen 请重新执行 python scripts/build_l3_sidecar.py）",
                "markdown_report": None,
                "llm_analysis": None,
            }
        spec = importlib.util.spec_from_file_location("_kalaroko_e2e_sse", script)
        if spec is None or spec.loader is None:
            return {
                "ok": False,
                "exit_code": 2,
                "error": "无法加载 test_kalaroko_default_scenarios_e2e.py",
                "markdown_report": None,
                "llm_analysis": None,
            }
        # 避免 sys.modules 命中旧模块：否则飞书仍推送历史「表格版」render_report_md
        sys.modules.pop(spec.name, None)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        run_fn = getattr(mod, "run_kalaroko_batch_test", None)
        if run_fn is None:
            return {
                "ok": False,
                "exit_code": 2,
                "error": "脚本缺少 run_kalaroko_batch_test",
                "markdown_report": None,
                "llm_analysis": None,
            }
        return await run_fn(runs, interval, skip_playwright=skip_pw, line_sink=line_sink)

    task = asyncio.create_task(_load_and_run())
    last_keepalive = time.monotonic()
    keepalive_sec = 15.0

    async def _write_line_obj(line: str) -> None:
        payload = {"line": line}
        await response.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
        if hasattr(response, "drain"):
            await response.drain()

    try:
        await _write_line_obj("[E2E] Kalaroko 全链路巡检任务已排队执行…")
        while True:
            try:
                line = await asyncio.wait_for(line_q.get(), timeout=0.35)
                await _write_line_obj(line)
                last_keepalive = time.monotonic()
            except asyncio.TimeoutError:
                if task.done():
                    break
                if time.monotonic() - last_keepalive >= keepalive_sec:
                    await response.write(b": keepalive\n\n")
                    last_keepalive = time.monotonic()

        while True:
            try:
                line = line_q.get_nowait()
                await _write_line_obj(line)
            except asyncio.QueueEmpty:
                break

        exc = task.exception()
        if exc is not None:
            payload = {"type": "error", "message": str(exc)}
            await response.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
        else:
            result = task.result()
            payload = {"type": "done", **result}
            await response.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
    except (ConnectionResetError, asyncio.CancelledError):
        task.cancel()
    except Exception as e:
        logger.warning("[L3 HTTP] monitor stream failed: %s", e)
        try:
            await response.write(f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n".encode("utf-8"))
        except Exception:
            pass
    return response


async def _handle_monitor_stop(request) -> "aiohttp.web.Response":
    """POST /api/v1/monitor/stop — 中断当前手动巡检主循环。"""
    try:
        from l3_node.kalaroko_e2e_control import stop_manual_run

        stop_manual_run()
    except Exception as e:
        logger.warning("[L3 HTTP] monitor stop failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)
    return _json_response({"ok": True, "message": "已发送停止信号"})


async def _handle_monitor_schedule_toggle(request) -> "aiohttp.web.Response":
    """POST /api/v1/monitor/schedule/toggle — JSON body: {\"enabled\": true|false}"""
    try:
        body = await request.json() if request.body_exists else {}
    except Exception as e:
        return _json_response({"ok": False, "error": f"JSON 解析失败: {e}"}, status=400)
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        return _json_response({"ok": False, "error": "缺少布尔字段 enabled"}, status=400)
    try:
        from l3_node.jobs.kalaroko_scheduler import start_scheduler, stop_scheduler, scheduler_status

        r = start_scheduler() if enabled else stop_scheduler()
        st = scheduler_status()
        return _json_response({"ok": True, "enabled": st.get("active", False), **r})
    except Exception as e:
        logger.warning("[L3 HTTP] monitor schedule toggle failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_monitor_schedule_status(request) -> "aiohttp.web.Response":
    """GET /api/v1/monitor/schedule/status"""
    try:
        from l3_node.jobs.kalaroko_scheduler import scheduler_status

        return _json_response({"ok": True, **scheduler_status()})
    except Exception as e:
        logger.warning("[L3 HTTP] monitor schedule status failed: %s", e)
        return _json_response({"ok": False, "active": False, "error": str(e)}, status=500)


def _k11_smoke_stream_parse_query(request) -> (
    tuple[tuple[str, str, bool, bool, bool] | None, "aiohttp.web.Response | None"]
):
    """返回 ((target_url, cdp_http, verbose, no_lark_report, headless), None) 或 (None, error_response)。"""
    from urllib.parse import urlparse

    target_url = (request.query.get("target_url") or "").strip()
    if target_url:
        p0 = urlparse(target_url)
        if p0.scheme not in ("http", "https") or not p0.netloc:
            r = _json_response(
                {"ok": False, "error": "target_url 须为有效 http(s) URL"},
                status=400,
            )
            return (None, r)

    cdp_http = (request.query.get("cdp_http") or "").strip()
    if cdp_http:
        p1 = urlparse(cdp_http)
        if p1.scheme not in ("http", "https") or not p1.netloc:
            r = _json_response(
                {"ok": False, "error": "cdp_http 须为有效 http(s) URL"},
                status=400,
            )
            return (None, r)

    verbose = str(request.query.get("verbose", "")).lower() in ("1", "true", "yes", "on")
    no_lark = str(request.query.get("no_lark_report", "")).lower() in ("1", "true", "yes", "on")
    headless = str(request.query.get("headless", "")).lower() in ("1", "true", "yes", "on")
    return ((target_url, cdp_http, verbose, no_lark, headless), None)


async def _k11_smoke_subprocess_sse_stream(
    request: Any,
    root: Path,
    cmd: list[str],
    start_line: str,
    log_event: str,
) -> "aiohttp.web.StreamResponse | aiohttp.web.Response":
    """K11 Playwright 脚本通用 SSE 子进程（与统合冒烟共用锁与停止通道）。"""
    import time

    global _k11_unified_smoke_stream_active, _k11_unified_smoke_proc

    async with _k11_unified_smoke_start_lock:
        if _k11_unified_smoke_stream_active:
            response0 = _stream_response()
            await response0.prepare(request)
            err = json.dumps(
                {
                    "type": "error",
                    "message": "K11 冒烟相关任务已在执行中，请待当前完成后再试。",
                },
                ensure_ascii=False,
            )
            await response0.write(f"data: {err}\n\n".encode("utf-8"))
            return response0
        _k11_unified_smoke_stream_active = True

    line_q: asyncio.Queue[str] = asyncio.Queue()
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    sub_task: asyncio.Task[int] | None = None
    response = _stream_response()

    async def _pump() -> int:
        global _k11_unified_smoke_proc
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(root),
            env=env,
        )
        _k11_unified_smoke_proc = proc
        code = 1
        try:
            if proc.stdout is None:
                if proc.returncode is None:
                    proc.kill()
                with contextlib.suppress(Exception):
                    await proc.wait()
                return 2
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\n\r")
                try:
                    line_q.put_nowait(text)
                except Exception:
                    pass
            code = await proc.wait()
        except asyncio.CancelledError:
            if proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=6.0)
                except (asyncio.TimeoutError, Exception):
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    with contextlib.suppress(Exception):
                        await proc.wait()
            code = 130
            raise
        finally:
            _k11_unified_smoke_proc = None
        return code

    last_keepalive = time.monotonic()
    keepalive_sec = 15.0

    async def _write_line_obj(line: str) -> None:
        payload = {"line": line}
        await response.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
        if hasattr(response, "drain"):
            await response.drain()

    try:
        await response.prepare(request)
        sub_task = asyncio.create_task(_pump())
        await _write_line_obj(start_line)
        while True:
            try:
                line = await asyncio.wait_for(line_q.get(), timeout=0.35)
                await _write_line_obj(line)
                last_keepalive = time.monotonic()
            except asyncio.TimeoutError:
                assert sub_task is not None
                if sub_task.done():
                    break
                if time.monotonic() - last_keepalive >= keepalive_sec:
                    await response.write(b": keepalive\n\n")
                    last_keepalive = time.monotonic()

        while True:
            try:
                l2 = line_q.get_nowait()
                await _write_line_obj(l2)
            except asyncio.QueueEmpty:
                break

        assert sub_task is not None
        if sub_task.cancelled():
            payload = {
                "type": "done",
                "ok": False,
                "exit_code": 130,
                "cancelled": True,
                "markdown_report": None,
                "llm_analysis": None,
            }
            await response.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
        else:
            exc = sub_task.exception()
            if exc is not None:
                if isinstance(exc, asyncio.CancelledError):
                    payload = {
                        "type": "done",
                        "ok": False,
                        "exit_code": 130,
                        "cancelled": True,
                        "markdown_report": None,
                        "llm_analysis": None,
                    }
                    await response.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
                else:
                    payload = {"type": "error", "message": str(exc)}
                    await response.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
            else:
                code = sub_task.result()
                payload = {
                    "type": "done",
                    "ok": code == 0,
                    "exit_code": code,
                    "markdown_report": None,
                    "llm_analysis": None,
                }
                await response.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
    except (ConnectionResetError, asyncio.CancelledError):
        if sub_task is not None and not sub_task.done():
            sub_task.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await sub_task
    except Exception as e:
        logger.warning("[L3 HTTP] %s stream failed: %s", log_event, e)
        try:
            await response.write(
                f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n".encode("utf-8")
            )
        except Exception:
            pass
    finally:
        if sub_task is not None and not sub_task.done():
            sub_task.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await sub_task
        _k11_unified_smoke_stream_active = False
    return response


async def _handle_k11_unified_smoke_stream(request) -> "aiohttp.web.StreamResponse | aiohttp.web.Response":
    """GET /api/v1/k11-unified-smoke/stream — 执行 ``scripts/test_k11_unified_platform_smoke_playwright.py``，SSE 行日志。"""
    import sys

    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / "test_k11_unified_platform_smoke_playwright.py"
    if not script.is_file():
        return _json_response(
            {"ok": False, "error": f"缺少 K11 统合冒烟脚本: {script}（需仓库根下 scripts/）"},
            status=500,
        )

    params, err = _k11_smoke_stream_parse_query(request)
    if err is not None:
        return err
    assert params is not None
    target_url, cdp_http, verbose, no_lark, _head = params

    cmd: list[str] = [sys.executable, str(script)]
    if target_url:
        cmd.extend(["--target-url", target_url])
    if cdp_http:
        cmd.extend(["--cdp-http", cdp_http])
    if verbose:
        cmd.append("-v")
    if no_lark:
        cmd.append("--no-lark-report")

    return await _k11_smoke_subprocess_sse_stream(
        request,
        root,
        cmd,
        f"[K11] 统合冒烟已启动: {script.name}",
        "k11 unified smoke",
    )


async def _handle_k11_p2_compat_only_stream(request) -> "aiohttp.web.StreamResponse | aiohttp.web.Response":
    """GET /api/v1/k11-p2-compat-only/stream — ``--only-compat`` 浏览器兼容段，SSE 行日志。"""
    import sys

    root = Path(__file__).resolve().parent.parent
    script = root / "scripts" / "test_k11_p2_compat_weaknet_playwright.py"
    if not script.is_file():
        return _json_response(
            {"ok": False, "error": f"缺少脚本: {script}（需仓库根下 scripts/）"},
            status=500,
        )

    params, err = _k11_smoke_stream_parse_query(request)
    if err is not None:
        return err
    assert params is not None
    target_url, cdp_http, verbose, no_lark, headless = params

    cmd: list[str] = [sys.executable, str(script), "--only-compat"]
    if headless:
        cmd.append("--headless")
    if target_url:
        cmd.extend(["--target-url", target_url])
    if cdp_http:
        cmd.extend(["--cdp-http", cdp_http])
    if verbose:
        cmd.append("-v")
    if no_lark:
        cmd.append("--no-lark-report")

    return await _k11_smoke_subprocess_sse_stream(
        request,
        root,
        cmd,
        f"[K11] P2 浏览器兼容已启动: {script.name} --only-compat",
        "k11 p2 compat",
    )


async def _handle_k11_unified_smoke_stop(request) -> "aiohttp.web.Response":
    """POST /api/v1/k11-unified-smoke/stop — 终止统合冒烟子进程（Playwright 链）。"""
    global _k11_unified_smoke_proc
    p = _k11_unified_smoke_proc
    if p is None or p.returncode is not None:
        return _json_response({"ok": True, "message": "无运行中的 K11 冒烟/Playwright 子进程"})
    try:
        p.terminate()
        try:
            await asyncio.wait_for(p.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            p.kill()
            await p.wait()
    except Exception as e:
        logger.warning("[L3 HTTP] k11 unified smoke stop failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)
    return _json_response({"ok": True, "message": "已发送停止信号"})


async def _handle_health(request) -> "aiohttp.web.Response":
    """GET /api/health - 健康检查，含 L2 连接状态（供 run_l3.ps1 等轮询）"""
    import os
    cfg_path = Path.home() / ".jachin" / "l2_gateway_config.json"
    l2_paired = False
    l2_base_url = os.environ.get("L2_BASE_URL", "http://localhost:18888")
    node_id = ""
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            l2_paired = data.get("paired") is True
            l2_base_url = (data.get("l2_base_url") or l2_base_url).rstrip("/")
            node_id = (data.get("node_id") or "").strip()
        except Exception:
            pass
    l2_reachable = False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(f"{l2_base_url}/health")
            l2_reachable = r.status_code == 200
    except Exception:
        pass
    return _json_response({
        "ok": True,
        "l2_paired": l2_paired,
        "l2_base_url": l2_base_url,
        "l2_reachable": l2_reachable,
        "node_id": node_id or None,
    })


async def _handle_scheduler_list_jobs(request) -> "aiohttp.web.Response":
    """GET /api/scheduler/list_jobs - 返回当前后台运行的自动化招聘任务列表"""
    try:
        from l3_node.hr_loader import get_recruitment_scheduler
        sched = get_recruitment_scheduler()
        if not sched:
            return _json_response({"jobs": [], "count": 0})
        jobs = sched.list_scheduled_jobs()
        return _json_response({"jobs": jobs, "count": len(jobs)})
    except Exception as e:
        logger.warning("[L3 HTTP] scheduler list_jobs failed: %s", e)
        return _json_response({"jobs": [], "count": 0, "error": str(e)}, status=500)


async def _handle_recruitment_start_task(request) -> "aiohttp.web.StreamResponse":
    """POST /api/recruitment/start_task - 一键式全链路招聘，SSE 流式进度"""
    try:
        body = await request.json() if request.body_exists else {}
    except Exception as e:
        return _json_response({"error": f"请求体解析失败: {e}"}, status=400)
    job_name = (body.get("job_name") or "").strip()
    if not job_name:
        return _json_response({"error": "job_name 不能为空"}, status=400)
    max_count = int(body.get("max_count") or 20)
    filter_tab = (body.get("filter_tab") or "全部").strip()
    request_resume = body.get("request_resume", True)
    if isinstance(request_resume, str):
        request_resume = request_resume.lower() in ("true", "1", "yes", "on")
    elif request_resume is None:
        request_resume = True
    logger.info("[L3 HTTP] recruitment start_task request_resume=%s filter_tab=%s", request_resume, filter_tab)
    jd_content = (body.get("jd_content") or "").strip()
    if not jd_content:
        logger.warning(
            "[L3 HTTP] recruitment 未提供岗位 JD（jd_content 为空），将使用岗位名/数据库/中性兜底；"
            "请务必在招聘大盘填写正式 JD 以获得最准评估"
        )
        print("\n[岗位 JD] 为空，将使用兜底\n", flush=True)
    else:
        logger.info("[L3 HTTP] recruitment 收到岗位 JD len=%d preview=%s", len(jd_content), (jd_content[:80] + "…") if len(jd_content) > 80 else jd_content)
        print(f"\n{'='*60}\n[岗位 JD] 已收到 (len={len(jd_content)})\n{jd_content}\n{'='*60}\n", flush=True)
    focus_keywords = (body.get("focus_keywords") or "").strip()
    strictness = (body.get("strictness") or "standard").strip()
    output_dir = (body.get("output_dir") or "").strip()
    force_reanalyze = body.get("force_reanalyze", False)
    if isinstance(force_reanalyze, str):
        force_reanalyze = force_reanalyze.lower() in ("true", "1", "yes", "on")

    response = _stream_response()
    await response.prepare(request)

    async def _sse_gen():
        try:
            from l3_node.hr_loader import get_recruitment_task
            task_mod = get_recruitment_task()
            if not task_mod:
                await response.write(
                    f"data: {json.dumps({'step': 0, 'msg': 'HR 招聘 MCP 包未找到，请从 L1 订阅 com.jachin.hr.recruitment', 'status': 'error'}, ensure_ascii=False)}\n\n".encode("utf-8")
                )
                return
            async for ev in task_mod.run_recruitment_task_stream(
                job_name=job_name,
                max_count=max_count,
                filter_tab=filter_tab,
                request_resume=request_resume,
                output_dir=output_dir,
                force_reanalyze=force_reanalyze,
                jd_content=jd_content,
                focus_keywords=focus_keywords,
                strictness=strictness,
            ):
                await response.write(
                    f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode("utf-8")
                )
        except Exception as e:
            logger.warning("[L3 HTTP] recruitment start_task failed: %s", e)
            await response.write(
                f"data: {json.dumps({'step': 0, 'msg': f'⚠️ 任务异常: {e}', 'status': 'error'}, ensure_ascii=False)}\n\n".encode("utf-8")
            )

    await _sse_gen()
    return response


async def _handle_mcp_execute(request) -> "aiohttp.web.Response":
    """POST /api/v3/mcp/execute — 兼容路径：供 L2 在可达 peer URL 时代为触发本机 MCP。

    目标态跨节点投递见 docs/ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md（L2 下行队列 + L3 Pull，非依赖入站 HTTP）。
    入站 HTTP 为 **NAT 降级路径**；须携带 L2 签发的 ``task_id`` + ``task_token``（与 Pull 队列一致）。
    开发排障可设 ``JACHIN_L3_MCP_EXECUTE_ALLOW_LEGACY=1`` 跳过令牌（不安全）。
    """
    import os

    try:
        body = await request.json() if request.body_exists else {}
    except Exception as e:
        return _json_response({"ok": False, "error": f"请求体解析失败: {e}"}, status=400)
    tool_name = (body.get("tool_name") or "").strip()
    arguments = body.get("arguments") or {}
    if not tool_name:
        return _json_response({"ok": False, "error": "tool_name 不能为空"}, status=400)

    allow_legacy = os.environ.get("JACHIN_L3_MCP_EXECUTE_ALLOW_LEGACY", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    task_id = str(body.get("task_id") or "").strip()
    task_token = str(body.get("task_token") or "").strip()
    if not allow_legacy:
        if not task_token or not task_id:
            return _json_response(
                {
                    "ok": False,
                    "error": "缺少 task_id/task_token；L2 委托须带 Task Token。NAT 场景请优先使用 Redis Pull。"
                    " 开发可设 JACHIN_L3_MCP_EXECUTE_ALLOW_LEGACY=1（不安全）。",
                },
                status=401,
            )
        try:
            from core.mcp_task_token import verify_mcp_delegate_task_token
        except ImportError:
            return _json_response({"ok": False, "error": "L3 无法加载 core.mcp_task_token"}, status=500)
        gw = Path.home() / ".jachin" / "l2_gateway_config.json"
        node_id = ""
        sub_account_id = ""
        if gw.exists():
            try:
                gc = json.loads(gw.read_text(encoding="utf-8"))
                if isinstance(gc, dict):
                    node_id = str(gc.get("node_id") or "").strip()
                    sub_account_id = str(gc.get("sub_account_id") or "").strip()
            except Exception:
                pass
        if not node_id or not sub_account_id:
            return _json_response(
                {"ok": False, "error": "本机缺少 ~/.jachin/l2_gateway_config.json 中的 node_id/sub_account_id，无法校验 Task Token"},
                status=401,
            )
        vok, vwhy = verify_mcp_delegate_task_token(
            task_token,
            task_id=task_id,
            tool_name=tool_name,
            executor_node_id=node_id,
            sub_account_id=sub_account_id,
        )
        if not vok:
            return _json_response({"ok": False, "error": f"task_token 无效: {vwhy}"}, status=403)

    try:
        from l3_node.primitives.mcp.registry import get_mcp_registry
        registry = get_mcp_registry()
        action_input = json.dumps(arguments, ensure_ascii=False) if isinstance(arguments, dict) else str(arguments)
        result = await registry.invoke(f"mcp:{tool_name}" if not tool_name.startswith("mcp:") else tool_name, action_input)
        return _json_response({"ok": True, "tool_name": tool_name, "result": result})
    except Exception as e:
        logger.warning("[L3 HTTP] mcp/execute 失败 tool=%s: %s", tool_name, e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_agent_run(request) -> "aiohttp.web.Response":
    """POST /api/v3/agent/run - 同步执行 L3 Agent，供控制台自然语言 404 回退使用。会触发 run_tool 持久化（如 HR 透析镜）"""
    try:
        body = await request.json() if request.body_exists else {}
    except Exception as e:
        return _json_response({"error": f"请求体解析失败: {e}"}, status=400)
    user_input = (body.get("user_input") or body.get("user_query") or "").strip()
    if not user_input:
        return _json_response({"error": "user_input 或 user_query 不能为空"}, status=400)
    try:
        from l3_node.agent_ref import engine_ref
        engine = engine_ref.get("engine")
    except ImportError:
        engine = None
    if not engine:
        return _json_response(
            {"error": "Agent 尚未就绪，请确保 L3 已启动且 WebSocket 已连接"},
            status=503,
        )
    try:
        from l3_node.agent_core import run_agent

        _isig = body.get("implicit_signals")
        _isig = _isig if isinstance(_isig, dict) else None
        _iatt = body.get("implicit_attribution")
        _iatt = _iatt if isinstance(_iatt, dict) else None
        if _iatt is None:
            _iatt = {"channel": "http_agent_run"}
        _ch = body.get("chat_id") or body.get("session_id") or ""
        _ch_s = str(_ch).strip() if _ch else ""
        if _ch_s:
            _iatt = {**_iatt, "lark_chat_id": _ch_s}
        try:
            from l3_node.lark_workflow_command_interceptor import try_lark_workflow_command_intercept

            _cmd = try_lark_workflow_command_intercept(user_input, channel_id=_ch_s)
        except Exception:
            _cmd = None
        if _cmd:
            return _json_response({"answer": _cmd, "command_intercepted": True})
        _att_meta = body.get("attachments_metadata")
        _att_meta = _att_meta if isinstance(_att_meta, list) else None
        _gw_st = body.get("gateway_system_state")
        _gw_st = str(_gw_st).strip() if _gw_st else None
        _gw_ch = str(body.get("gateway_clarification_handle") or "").strip()
        try:
            _gw_dl = float(body.get("gateway_clarification_deadline_ts") or 0.0)
        except (TypeError, ValueError):
            _gw_dl = 0.0
        _sniff_ws = body.get("gateway_workspace_dir") or body.get("git_workspace_dir")
        _sniff_ws = str(_sniff_ws).strip() if _sniff_ws else None
        try:
            _mi = int(body.get("max_iterations") or 8)
        except (TypeError, ValueError):
            _mi = 8
        _mi = max(1, min(_mi, 48))
        answer = await run_agent(
            user_input,
            engine,
            max_iterations=_mi,
            implicit_signals=_isig,
            implicit_attribution=_iatt,
            attachments_metadata=_att_meta,
            gateway_system_state=_gw_st,
            gateway_clarification_handle=_gw_ch,
            gateway_clarification_deadline_ts=_gw_dl,
            gateway_workspace_dir=_sniff_ws,
        )
        resp = {"answer": answer or ""}
        try:
            persist_mod = __import__("l3_node.hr_loader", fromlist=["get_hr_analysis_persist"]).get_hr_analysis_persist()
            if persist_mod:
                saved = persist_mod.get_last_saved_path()
                if saved:
                    resp["saved_path"] = saved
        except Exception:
            pass
        return _json_response(resp)
    except Exception as e:
        logger.warning("[L3 HTTP] agent/run 失败: %s", e)
        return _json_response({"error": str(e)}, status=500)


async def _handle_l3_setup_page(request) -> "aiohttp.web.Response":
    """GET /l3/setup — 浏览器内选择 L1 工作区并写入 l2_gateway_config（需 edge token）。"""
    import aiohttp.web

    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>L3 工作区向导</title>
<style>
body{font-family:system-ui,sans-serif;max-width:520px;margin:2rem auto;padding:0 1rem;background:#0f172a;color:#e2e8f0}
label{display:block;margin-top:.75rem;font-size:.85rem;color:#94a3b8}
input,select,button{width:100%;box-sizing:border-box;margin-top:.25rem;padding:.5rem .6rem;border-radius:6px;border:1px solid #334155;background:#1e293b;color:#f8fafc}
button{cursor:pointer;background:#0d9488;border-color:#0f766e;font-weight:600;margin-top:1rem}
button.secondary{background:#4f46e5;border-color:#4338ca}
.err{color:#f87171;font-size:.85rem;margin-top:.5rem}
.ok{color:#34d399;font-size:.85rem;margin-top:.5rem}
h1{font-size:1.25rem}
.note{font-size:.8rem;color:#64748b}
</style></head>
<body>
<h1>L3 工作区配置向导</h1>
<p class="note">使用 L1 边缘 <strong>access_token</strong>（常见于 L2 上 <code>~/.jachin/nexus_config.json</code>）。非边缘会话请先在 L1 完成配对。</p>
<label>L1 根地址</label>
<input id="base" type="text" placeholder="http://localhost:3000"/>
<label>Edge Bearer（access_token）</label>
<input id="token" type="password" autocomplete="off" placeholder="token"/>
<button type="button" id="btnLoad">拉取工作区列表</button>
<div id="err" class="err"></div>
<label id="lblWs" style="display:none">选择工作区</label>
<select id="ws" style="display:none"></select>
<label style="margin-top:1rem">算力节点区域 (JACHIN_ACTIVE_REGION)</label>
<select id="region">
  <option value="CN" selected>中国大陆 (CN)</option>
  <option value="SEA">东南亚 / 国际 (SEA)</option>
</select>
<button type="button" class="secondary" id="btnSave" style="display:none">写入 ~/.jachin/l2_gateway_config.json</button>
<div id="ok" class="ok"></div>
<script>
const $ = (id) => document.getElementById(id);
$('btnLoad').onclick = async () => {
  $('err').textContent = ''; $('ok').textContent = '';
  let nexus_base_url = $('base').value.trim();
  while (nexus_base_url.endsWith('/')) nexus_base_url = nexus_base_url.slice(0, -1);
  const access_token = $('token').value.trim();
  if (!nexus_base_url || !access_token) { $('err').textContent = '请填写 L1 地址与 token'; return; }
  const r = await fetch('/api/v3/setup/workspaces', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ nexus_base_url, access_token })
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || !j.success) {
    $('err').textContent = (j && j.message) || j.error || '拉取失败';
    return;
  }
  const ws = (j.data && j.data.workspaces) || [];
  const sel = $('ws');
  sel.innerHTML = '';
  for (const w of ws) {
    const o = document.createElement('option');
    o.value = w.id;
    o.textContent = (w.name || w.id) + (w.slug ? ' (' + w.slug + ')' : '');
    sel.appendChild(o);
  }
  sel.style.display = ws.length ? 'block' : 'none';
  $('lblWs').style.display = ws.length ? 'block' : 'none';
  $('btnSave').style.display = ws.length ? 'block' : 'none';
  if (!ws.length) $('err').textContent = '该账号下无工作区，请先在 L1 创建/加入工作区';
};
$('btnSave').onclick = async () => {
  $('err').textContent = ''; $('ok').textContent = '';
  const organization_id = $('ws').value;
  const workspace_name = ($('ws').selectedOptions[0] && $('ws').selectedOptions[0].textContent) || '';
  const region = ($('region').value || 'CN').trim().toUpperCase();
  const r = await fetch('/api/v3/setup/save-gateway-org', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ organization_id, workspace_name, region })
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || !j.ok) {
    $('err').textContent = (j && j.error) || '保存失败';
    return;
  }
  const jr = (j && j.jachin_active_region) ? j.jachin_active_region : region;
  $('ok').textContent = '已写入（算力区域：' + jr + '）。请重启 L3 或重新执行配对流程。';
};
</script>
</body></html>"""
    return aiohttp.web.Response(text=html, content_type="text/html; charset=utf-8")


async def _handle_setup_workspaces(request) -> "aiohttp.web.Response":
    try:
        body = await request.json()
    except Exception:
        return _json_response({"success": False, "error": "Invalid JSON"}, status=400)
    base = (body.get("nexus_base_url") or "").strip().rstrip("/")
    token = (body.get("access_token") or "").strip()
    if not base or not token:
        return _json_response(
            {
                "success": False,
                "error": "BAD_REQUEST",
                "message": "nexus_base_url 与 access_token 必填",
            },
            status=400,
        )
    try:
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{base}/api/v1/edge/me/workspaces",
                headers={"Authorization": f"Bearer {token}"},
            )
        data = resp.json()
    except Exception as e:
        logger.warning("[L3 setup] workspaces fetch failed: %s", e)
        return _json_response(
            {"success": False, "error": "UPSTREAM", "message": str(e)},
            status=502,
        )
    if resp.status_code != 200 or not isinstance(data, dict) or not data.get("success"):
        msg = "拉取失败"
        if isinstance(data, dict):
            msg = str(data.get("message") or data.get("error") or msg)
        return _json_response(
            {"success": False, "error": "UPSTREAM", "message": msg},
            status=502,
        )
    return _json_response({"success": True, "data": data.get("data")})


async def _handle_setup_save_gateway_org(request) -> "aiohttp.web.Response":
    try:
        body = await request.json()
    except Exception:
        return _json_response({"ok": False, "error": "Invalid JSON"}, status=400)
    organization_id = (body.get("organization_id") or "").strip()
    workspace_name = (body.get("workspace_name") or "").strip()[:128]
    region_raw = (body.get("region") or body.get("jachin_active_region") or "CN").strip().upper()
    if not organization_id:
        return _json_response({"ok": False, "error": "organization_id required"}, status=400)
    cfg_path = Path.home() / ".jachin" / "l2_gateway_config.json"
    prev: dict[str, Any] = {}
    if cfg_path.exists():
        try:
            prev = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            prev = {}
    prev["organization_id"] = organization_id
    if workspace_name:
        prev["workspace_name"] = workspace_name
    if region_raw in ("CN", "SEA"):
        prev["jachin_active_region"] = region_raw
        try:
            from core.env_persist import persist_jachin_active_region

            ok, err = persist_jachin_active_region(region_raw)
            if not ok:
                return _json_response(
                    {"ok": False, "error": "persist_region_failed", "message": err or "unknown"},
                    status=500,
                )
        except Exception as e:
            logger.warning("[L3 setup] persist JACHIN_ACTIVE_REGION failed: %s", e)
            return _json_response(
                {"ok": False, "error": "persist_region_failed", "message": str(e)},
                status=500,
            )
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(prev, indent=2, ensure_ascii=False), encoding="utf-8")
    return _json_response({"ok": True, "jachin_active_region": prev.get("jachin_active_region", "CN")})


def _safety_lock_admin_token_ok(request: Any) -> tuple[bool, str | None]:
    """校验请求头中的管理员密钥与 L3 进程环境变量一致。返回 (通过, 错误码)。"""
    exp = os.environ.get("JACHIN_SAFETY_LOCK_ADMIN_TOKEN", "").strip()
    if not exp:
        return False, "admin_token_not_configured"
    tok = (request.headers.get("X-Jachin-Safety-Lock-Token") or "").strip()
    if tok != exp:
        return False, "forbidden"
    return True, None


async def _handle_safety_lock_pending(request: Any) -> "aiohttp.web.Response":
    """GET /api/v3/safety-lock/pending — 列出待审批条目（需管理员密钥）。"""
    ok, err = _safety_lock_admin_token_ok(request)
    if not ok:
        status = 503 if err == "admin_token_not_configured" else 401
        return _json_response(
            {
                "ok": False,
                "error": err,
                "message": (
                    "请在本机为 L3 进程设置 JACHIN_SAFETY_LOCK_ADMIN_TOKEN。"
                    if err == "admin_token_not_configured"
                    else "管理员密钥不匹配。"
                ),
            },
            status=status,
        )
    try:
        from l3_node.jachin_safety_lock import list_pending_entries

        data = list_pending_entries()
        return _json_response(data)
    except Exception as e:
        logger.warning("[L3 HTTP] safety-lock pending list failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_safety_lock_approve(request: Any) -> "aiohttp.web.Response":
    """POST /api/v3/safety-lock/approve — 审批通过，写入 JACHIN_SAFETY_LOCK.md 并删除 pending。"""
    ok, err = _safety_lock_admin_token_ok(request)
    if not ok:
        status = 503 if err == "admin_token_not_configured" else 401
        return _json_response({"ok": False, "error": err}, status=status)
    try:
        body = await request.json()
    except Exception:
        return _json_response({"ok": False, "error": "invalid_json"}, status=400)
    pending_id = (body.get("pending_id") or body.get("pendingId") or "").strip()
    if not pending_id:
        return _json_response({"ok": False, "error": "pending_id required"}, status=400)
    from l3_node.jachin_safety_lock import approve_pending

    tok = (request.headers.get("X-Jachin-Safety-Lock-Token") or "").strip()
    result = approve_pending(pending_id, tok)
    if result.get("ok"):
        return _json_response(result)
    ec = result.get("error") or "error"
    status = 401 if ec == "forbidden" else 404 if ec == "not_found" else 400
    if ec == "admin_token_not_configured":
        status = 503
    return _json_response(result, status=status)


async def _handle_safety_lock_reject(request: Any) -> "aiohttp.web.Response":
    """POST /api/v3/safety-lock/reject — 拒绝并删除 pending 文件。"""
    ok, err = _safety_lock_admin_token_ok(request)
    if not ok:
        status = 503 if err == "admin_token_not_configured" else 401
        return _json_response({"ok": False, "error": err}, status=status)
    try:
        body = await request.json()
    except Exception:
        return _json_response({"ok": False, "error": "invalid_json"}, status=400)
    pending_id = (body.get("pending_id") or body.get("pendingId") or "").strip()
    if not pending_id:
        return _json_response({"ok": False, "error": "pending_id required"}, status=400)
    from l3_node.jachin_safety_lock import reject_pending

    tok = (request.headers.get("X-Jachin-Safety-Lock-Token") or "").strip()
    result = reject_pending(pending_id, tok)
    if result.get("ok"):
        return _json_response(result)
    ec = result.get("error") or "error"
    status = 401 if ec == "forbidden" else 404 if ec == "not_found" else 400
    if ec == "admin_token_not_configured":
        status = 503
    return _json_response(result, status=status)


async def _handle_native_fs_policy_get(request: Any) -> "aiohttp.web.Response":
    """GET /api/v3/config/native-fs-policy — 内置与用户扩展的读写策略展示（与桌面设置页对齐）。"""
    try:
        from l3_node.primitives.fs_path_blacklist import READ_BLACKLIST_BUILTIN_LINES
        from l3_node.primitives.native_fs_policy_store import (
            get_read_blacklist_extra_roots,
            get_write_allowlist_extra_roots,
            policy_path,
        )
        from l3_node.primitives.native_write_allowlist import get_builtin_native_write_roots

        custom_w = [str(p) for p in get_write_allowlist_extra_roots()]
        custom_r = [str(p) for p in get_read_blacklist_extra_roots()]
        return _json_response(
            {
                "ok": True,
                "policy_file": str(policy_path()),
                "builtin_write_roots": [str(p) for p in get_builtin_native_write_roots()],
                "custom_write_roots": custom_w,
                "builtin_read_blacklist_lines": list(READ_BLACKLIST_BUILTIN_LINES),
                "custom_read_blacklist_roots": custom_r,
            }
        )
    except Exception as e:
        logger.warning("[L3 HTTP] native-fs-policy get failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_native_fs_policy_post(request: Any) -> "aiohttp.web.Response":
    """POST /api/v3/config/native-fs-policy — 保存用户扩展路径（JSON body）。"""
    try:
        body = await request.json()
    except Exception:
        return _json_response({"ok": False, "error": "invalid_json"}, status=400)
    w = body.get("write_allowlist_extra") or body.get("writeAllowlistExtra")
    r = body.get("read_blacklist_extra") or body.get("readBlacklistExtra")
    if w is not None and not isinstance(w, list):
        return _json_response({"ok": False, "error": "write_allowlist_extra must be a list"}, status=400)
    if r is not None and not isinstance(r, list):
        return _json_response({"ok": False, "error": "read_blacklist_extra must be a list"}, status=400)
    ws = [str(x) for x in (w or []) if isinstance(x, str)]
    rs = [str(x) for x in (r or []) if isinstance(x, str)]
    try:
        from l3_node.primitives.native_fs_policy_store import save_policy

        ok, msg = save_policy(ws, rs)
        if not ok:
            return _json_response({"ok": False, "error": msg}, status=400)
        return _json_response({"ok": True, "message": msg})
    except Exception as e:
        logger.warning("[L3 HTTP] native-fs-policy post failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


def _json_response(data: Any, status: int = 200) -> "aiohttp.web.Response":
    import aiohttp.web
    return aiohttp.web.json_response(data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False))


async def run_http_server(port: int = L3_HTTP_PORT, host: str = "127.0.0.1") -> "aiohttp.web.Application":
    """启动 L3 HTTP API 服务（与 WebSocket 并行）"""
    try:
        import aiohttp.web
    except ImportError:
        logger.warning("[L3 HTTP] aiohttp 未安装，技能 HTTP API 不可用。pip install aiohttp")
        return None

    # 预加载招聘 APScheduler（HR 包存在时）；与 BI 定时独立（v0.8.50 系统基线 + BI 侧独立注册）
    try:
        try:
            from l3_node.early_log import trace
            trace("http_server: importing recruitment_scheduler...")
        except ImportError:
            pass
        from l3_node.hr_loader import get_recruitment_scheduler

        sched = get_recruitment_scheduler()
        if sched:
            try:
                from l3_node.early_log import trace
                trace("http_server: recruitment_scheduler loaded")
            except ImportError:
                pass
    except Exception as e:
        logger.debug("[L3 HTTP] recruitment_scheduler 预加载跳过: %s", e)

    try:
        from l3_node.primitives.skills.bi.scheduler import register_bi_daily_report_job

        register_bi_daily_report_job()
    except Exception as e:
        logger.debug("[L3 HTTP] bi.scheduler 注册跳过: %s", e)

    # stdio MCP 不得在「主 await 链」上同步拉起：Windows + frozen + mcp/anyio 子进程创建时可能抛出
    # asyncio.CancelledError（非 Exception 子类），会穿透 except Exception 并终止 asyncio.run(main)。
    # 在 HTTP 监听成功后再 create_task 后台引导，取消隔离在子任务内；详见 mcp_stdio_bootstrap。

    @aiohttp.web.middleware
    async def cors_middleware(request, handler):
        if request.method == "OPTIONS":
            r = aiohttp.web.Response()
        else:
            r = await handler(request)
        r.headers["Access-Control-Allow-Origin"] = "*"
        r.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
        r.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Jachin-Safety-Lock-Token"
        return r

    app = aiohttp.web.Application(middlewares=[cors_middleware])
    app.router.add_get("/api/v3/skills", _handle_skills_list)
    app.router.add_delete("/api/v3/skills/{item_id}", _handle_skills_uninstall)
    app.router.add_post("/api/v3/skills/{skill_id}/execute", _handle_skills_execute)
    app.router.add_post("/api/recruitment/start_task", _handle_recruitment_start_task)
    app.router.add_post("/api/scheduler/add_job", _handle_scheduler_add_job)
    app.router.add_post("/api/scheduler/remove_job", _handle_scheduler_remove_job)
    app.router.add_get("/api/scheduler/list_jobs", _handle_scheduler_list_jobs)
    app.router.add_get("/api/health", _handle_health)
    app.router.add_get("/api/system/logs/stream", _handle_system_logs_stream)
    app.router.add_get("/api/v1/monitor/stream", _handle_monitor_kalaroko_stream)
    app.router.add_post("/api/v1/monitor/stop", _handle_monitor_stop)
    app.router.add_post("/api/v1/monitor/schedule/toggle", _handle_monitor_schedule_toggle)
    app.router.add_get("/api/v1/monitor/schedule/status", _handle_monitor_schedule_status)
    app.router.add_get("/api/v1/k11-unified-smoke/stream", _handle_k11_unified_smoke_stream)
    app.router.add_get("/api/v1/k11-p2-compat-only/stream", _handle_k11_p2_compat_only_stream)
    app.router.add_post("/api/v1/k11-unified-smoke/stop", _handle_k11_unified_smoke_stop)
    app.router.add_post("/api/v3/skills/{skill_id}/execute/stream", _handle_skills_execute_stream)
    app.router.add_post("/api/v3/mcp/execute", _handle_mcp_execute)
    app.router.add_post("/api/v3/agent/run", _handle_agent_run)
    app.router.add_get("/l3/setup", _handle_l3_setup_page)
    app.router.add_post("/api/v3/setup/workspaces", _handle_setup_workspaces)
    app.router.add_post("/api/v3/setup/save-gateway-org", _handle_setup_save_gateway_org)
    app.router.add_get("/api/v3/recycle-bin/skills", _handle_recycle_bin_list)
    app.router.add_post("/api/v3/recycle-bin/skills/{recycle_id}/restore", _handle_recycle_bin_restore)
    app.router.add_delete("/api/v3/recycle-bin/skills/{recycle_id}", _handle_recycle_bin_delete)
    app.router.add_get("/api/v3/safety-lock/pending", _handle_safety_lock_pending)
    app.router.add_post("/api/v3/safety-lock/approve", _handle_safety_lock_approve)
    app.router.add_post("/api/v3/safety-lock/reject", _handle_safety_lock_reject)
    app.router.add_get("/api/v3/config/native-fs-policy", _handle_native_fs_policy_get)
    app.router.add_post("/api/v3/config/native-fs-policy", _handle_native_fs_policy_post)

    async def _on_startup_kalaroko_scheduler(app):
        """L3 重启后恢复 Kalaroko 定时巡检（状态见 ~/.jachin/data/kalaroko_scheduler_state.json）。"""
        try:
            from l3_node.jobs.kalaroko_scheduler import init_auto_start_scheduler

            init_auto_start_scheduler()
        except Exception as e:
            logger.warning("[L3 HTTP] Kalaroko scheduler auto-start skipped: %s", e)

    async def _on_startup_healthchecks_watchdog(_app):
        """Healthchecks.io 周期 ping（独立线程，不阻塞 asyncio）。"""
        try:
            from l3_node.jobs.healthchecks_watchdog import start_healthchecks_watchdog

            start_healthchecks_watchdog()
        except Exception as e:
            logger.warning("[L3 HTTP] Healthchecks watchdog skipped: %s", e)

    async def _on_startup_skill_matrix_sync(_app):
        """启动时将全量工具描述写入 Memory Nexus Skill_Matrix（后台任务，勿阻塞 runner.setup）。

        若在 on_startup 内 await assemble_tool_pool，会与 MCP 全量合并同链路易阻塞十余秒，
        导致 HTTP 尚未 listen、gateway 主流程无法进入 run_ws_server，桌面 WebSocket 18981 一直不可用。
        """
        v = (os.environ.get("JACHIN_SKILL_MATRIX_SYNC_ON_STARTUP") or "1").strip().lower()
        if v in ("0", "false", "no", "off"):
            return

        async def _skill_matrix_sync_bg() -> None:
            try:
                from l3_node.memory_nexus_bridge import sync_all_tools_to_nexus
                from l3_node.primitives.tools.tool_pool import assemble_tool_pool

                _tools = await assemble_tool_pool(
                    allowed_skills=None,
                    gateway_bundle=None,
                    bg_channel=None,
                    logger=logger,
                )
                r = await asyncio.to_thread(sync_all_tools_to_nexus, _tools)
                if r.get("ok"):
                    logger.info("[L3 HTTP] Skill matrix sync ok: count=%s", r.get("count"))
                else:
                    logger.warning("[L3 HTTP] Skill matrix sync: %s", r)
            except Exception as e:
                logger.warning("[L3 HTTP] Skill matrix sync skipped: %s", e)

        asyncio.create_task(_skill_matrix_sync_bg(), name="jachin-skill-matrix-sync")

    app.on_startup.append(_on_startup_kalaroko_scheduler)
    app.on_startup.append(_on_startup_healthchecks_watchdog)
    app.on_startup.append(_on_startup_skill_matrix_sync)

    def _is_port_in_use(e: BaseException) -> bool:
        if isinstance(e, OSError):
            return getattr(e, "errno", None) in (10048, 98)
        return False

    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    last_err: BaseException | None = None
    ports_to_try = [port, 18990, 18992, 18993, 18994, 18995, 18996, 18997, 18998, 18999]
    for try_port in ports_to_try:
        try:
            site = aiohttp.web.TCPSite(runner, host, try_port)
            await site.start()
            import sys
            if try_port != port:
                logger.warning("[L3 HTTP] 端口 %d 被占用，已改用 %d", port, try_port)
            logger.info("[L3 HTTP] 技能 API 已启动 http://%s:%d/api/v3/skills", host, try_port)
            print(f"[L3 HTTP] 已启动 http://{host}:{try_port}/api/v3/skills", file=sys.stderr, flush=True)

            async def _stdio_mcp_bootstrap_bg() -> None:
                try:
                    from l3_node.mcp_stdio_bootstrap import start_l3_stdio_mcp_host

                    await start_l3_stdio_mcp_host()
                except asyncio.CancelledError:
                    logger.debug("[L3 HTTP] stdio MCP 后台引导任务被取消")
                    raise
                except Exception as e:
                    logger.warning("[L3 HTTP] stdio MCP 宿主启动失败: %s", e, exc_info=True)

            def _stdio_mcp_bootstrap_done(t: asyncio.Task) -> None:
                if t.cancelled():
                    logger.debug("[L3 HTTP] stdio MCP bootstrap task 已取消")
                    return
                try:
                    exc = t.exception()
                except asyncio.CancelledError:
                    return
                if exc is not None:
                    logger.warning("[L3 HTTP] stdio MCP bootstrap task 异常: %s", exc, exc_info=exc)

            _mcp_bg = asyncio.create_task(_stdio_mcp_bootstrap_bg(), name="jachin-l3-stdio-mcp")
            _mcp_bg.add_done_callback(_stdio_mcp_bootstrap_done)
            app["jachin_stdio_mcp_bootstrap_task"] = _mcp_bg

            return app
        except OSError as e:
            last_err = e
            if _is_port_in_use(e):
                logger.warning("[L3 HTTP] 端口 %d 已被占用，尝试下一端口...", try_port)
                continue
            raise
    raise RuntimeError(
        f"端口 {ports_to_try[0]}~{ports_to_try[-1]} 均被占用。请执行: .\\scripts\\kill_l3_ports.ps1"
    ) from last_err
