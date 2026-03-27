"""
L3 HTTP API - 技能列表与执行

供 Skill Matrix 等前端调用。技能执行在 L3 本地进行（~/.jachin/l3_skill_cache/）。
端口 18991 系列，与 L2(18888)、WebSocket(18981) 分离。
HR 透析镜执行成功后，分析报告写入 data/hr_analysis/ 及 ~/.jachin/volumes/ 对应数据卷。
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("l3_node")

L3_HTTP_PORT = 18991


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
        from l3_node.skills import load_skills_for_ui
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
        from l3_node.skills import run_tool
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
            from l3_node.skills import run_tool
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

    from l3_node.skills.loader import _extract_stem_from_hr_report, _fetch_skill_config, _get_hr_plugin_config_defaults
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
    """POST /api/v3/mcp/execute - L2 委托执行 MCP 工具，供本机无技能时由其他 L3 执行"""
    try:
        body = await request.json() if request.body_exists else {}
    except Exception as e:
        return _json_response({"ok": False, "error": f"请求体解析失败: {e}"}, status=400)
    tool_name = (body.get("tool_name") or "").strip()
    arguments = body.get("arguments") or {}
    if not tool_name:
        return _json_response({"ok": False, "error": "tool_name 不能为空"}, status=400)
    try:
        from l3_node.skills.mcp_registry import get_mcp_registry
        registry = get_mcp_registry()
        action_input = json.dumps(arguments, ensure_ascii=False) if isinstance(arguments, dict) else str(arguments)
        result = await registry.invoke(f"mcp:{tool_name}" if not tool_name.startswith("mcp:") else tool_name, action_input)
        return _json_response({"ok": True, "tool_name": tool_name, "result": result})
    except Exception as e:
        logger.warning("[L3 HTTP] mcp/execute 失败 tool=%s: %s", tool_name, e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_mcp_execute(request) -> "aiohttp.web.Response":
    """POST /api/v3/mcp/execute - L2 委托执行 MCP 工具，供本机无技能时由其他 L3 执行"""
    try:
        body = await request.json() if request.body_exists else {}
    except Exception as e:
        return _json_response({"ok": False, "error": f"请求体解析失败: {e}"}, status=400)
    tool_name = (body.get("tool_name") or "").strip()
    arguments = body.get("arguments") or {}
    if not tool_name:
        return _json_response({"ok": False, "error": "tool_name 不能为空"}, status=400)
    try:
        from l3_node.skills.mcp_registry import get_mcp_registry
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
        answer = await run_agent(
            user_input,
            engine,
            max_iterations=8,
            implicit_signals=_isig,
            implicit_attribution=_iatt,
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
        from l3_node.skills.bi.scheduler import register_bi_daily_report_job

        register_bi_daily_report_job()
    except Exception as e:
        logger.debug("[L3 HTTP] bi.scheduler 注册跳过: %s", e)

    @aiohttp.web.middleware
    async def cors_middleware(request, handler):
        if request.method == "OPTIONS":
            r = aiohttp.web.Response()
        else:
            r = await handler(request)
        r.headers["Access-Control-Allow-Origin"] = "*"
        r.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
        r.headers["Access-Control-Allow-Headers"] = "Content-Type"
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
    app.router.add_post("/api/v3/skills/{skill_id}/execute/stream", _handle_skills_execute_stream)
    app.router.add_post("/api/v3/mcp/execute", _handle_mcp_execute)
    app.router.add_post("/api/v3/agent/run", _handle_agent_run)
    app.router.add_post("/api/v3/mcp/execute", _handle_mcp_execute)
    app.router.add_get("/api/v3/recycle-bin/skills", _handle_recycle_bin_list)
    app.router.add_post("/api/v3/recycle-bin/skills/{recycle_id}/restore", _handle_recycle_bin_restore)
    app.router.add_delete("/api/v3/recycle-bin/skills/{recycle_id}", _handle_recycle_bin_delete)

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
