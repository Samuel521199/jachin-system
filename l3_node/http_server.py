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
import re
import threading
from pathlib import Path
from typing import Any

from l3_node.k11_subprocess_cli import build_k11_l3_subprocess_cmd as _k11_smoke_subprocess_cmd
from l3_node.paths import (
    get_app_root,
    k11_game_open_smoke_script_path,
    k11_p2_compat_weaknet_script_path,
    k11_tongits_autoplay_smoke_script_path,
    k11_unified_smoke_script_path,
    kalaroko_default_e2e_script_path,
)

logger = logging.getLogger("l3_node")

L3_HTTP_PORT = 18991

# POST /api/v3/agent/run：同一 session_id / chat_id 串行化，避免并发 run_agent 撕裂共享 _session_messages
_http_agent_session_locks: dict[str, asyncio.Lock] = {}
_http_agent_session_locks_guard = asyncio.Lock()


async def _http_agent_session_lock(session_key: str) -> asyncio.Lock:
    sk = (session_key or "").strip()
    async with _http_agent_session_locks_guard:
        lock = _http_agent_session_locks.get(sk)
        if lock is None:
            lock = asyncio.Lock()
            _http_agent_session_locks[sk] = lock
        return lock


async def _http_agent_session_lock_held(session_key: str) -> bool:
    """同一 `chat_id`/`session_id` 是否已有 `/api/v3/agent/run` 持锁执行中（协程仍在 `async with lock` 内）。"""
    sk = (session_key or "").strip()
    if not sk:
        return False
    async with _http_agent_session_locks_guard:
        lk = _http_agent_session_locks.get(sk)
    if lk is None:
        return False
    return lk.locked()


_VOICE_EXPLICIT_MEMORY_RE = re.compile(r"^\s*(?:请|帮我|麻烦你)?记住[：:，,\s]*(?P<fact>.+?)\s*[。.!！]?\s*$")
_VOICE_SESSION_GUARD = (
    "【桌面语音会话规则】回答‘刚刚/前面/我告诉你’这类问题时，必须优先依据当前 chat_id "
    "的本轮会话历史；没有命中时再说明不确定，不要引用其它会话或全局旧记忆。"
)


def _extract_voice_explicit_memory(text: str) -> str:
    m = _VOICE_EXPLICIT_MEMORY_RE.match(text or "")
    if not m:
        return ""
    fact = (m.group("fact") or "").strip()
    return fact[:1000]


def _ensure_voice_session_guard(messages: list[dict[str, Any]]) -> None:
    for m in messages[:4]:
        if isinstance(m, dict) and m.get("role") == "system" and m.get("content") == _VOICE_SESSION_GUARD:
            return
    messages.insert(0, {"role": "system", "content": _VOICE_SESSION_GUARD})


async def _commit_voice_explicit_memory(*, fact: str, chat_id: str, source: str, original_text: str) -> None:
    fact = (fact or "").strip()
    if not fact:
        return
    try:
        from l3_client.local_mcps.jachin_memory_nexus.memory_backend import commit_drawer

        await asyncio.to_thread(
            commit_drawer,
            text=f"[voice_companion_explicit_memory] {fact}",
            wing="User_Persona",
            room="Companion_Explicit_Memory",
            extra_meta={
                "tag": "voice_companion_explicit_memory",
                "source": source or "desktop_voice_companion",
                "chat_id": chat_id,
                "original_text": original_text[:1000],
            },
        )
    except Exception as e:
        logger.warning("[L3 HTTP] voice explicit memory commit skipped: %s", e)


def _registry_diag_read_token() -> str:
    return (
        (os.environ.get("JACHIN_REGISTRY_DIAG_TOKEN") or "").strip()
        or (os.environ.get("JACHIN_HOOK_EVENTS_READ_TOKEN") or "").strip()
    )


def _registry_diag_auth_failure(request) -> Any:
    """未配置令牌 → 503；头不匹配 → 401；通过 → None。"""
    tok = _registry_diag_read_token()
    if not tok:
        return _json_response(
            {
                "ok": False,
                "error": "Set JACHIN_REGISTRY_DIAG_TOKEN or JACHIN_HOOK_EVENTS_READ_TOKEN",
            },
            status=503,
        )
    hdr = (
        (request.headers.get("X-Jachin-Registry-Diag-Token") or "").strip()
        or (request.headers.get("X-Jachin-Hook-Events-Token") or "").strip()
    )
    if hdr != tok:
        return _json_response({"ok": False, "error": "unauthorized"}, status=401)
    return None


# K11 统合冒烟：单路 SSE 子进程（与 /api/v1/monitor/stream 同形态）
_k11_unified_smoke_stream_active: bool = False
_k11_unified_smoke_start_lock: asyncio.Lock = asyncio.Lock()
_k11_unified_smoke_proc: "asyncio.subprocess.Process | None" = None
_k11_unified_smoke_user_abort: bool = False


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

    async def _write_line_obj(line: str) -> None:
        payload = {"line": line}
        await response.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
        if hasattr(response, "drain"):
            await response.drain()

    # 必须先写出首条 SSE，再 ``create_task``：否则首轮 ``await`` 会让循环先跑
    # ``_load_and_run`` 内的 ``exec_module``（同步重），长时间占满事件循环，客户端首连表现为卡死。
    task: asyncio.Task | None = None
    last_keepalive = time.monotonic()
    keepalive_sec = 15.0

    try:
        await _write_line_obj("[E2E] Kalaroko 全链路巡检任务已排队执行…")
        task = asyncio.create_task(_load_and_run())
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

        if task is None:
            raise RuntimeError("internal: monitor stream task not started")
        exc = task.exception()
        if exc is not None:
            payload = {"type": "error", "message": str(exc)}
            await response.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
        else:
            result = task.result()
            payload = {"type": "done", **result}
            await response.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
    except (ConnectionResetError, asyncio.CancelledError):
        if task is not None:
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
    *,
    run_count: int = 1,
    interval_between_runs_sec: int = 0,
) -> "aiohttp.web.StreamResponse | aiohttp.web.Response":
    """K11 Playwright 脚本通用 SSE 子进程（与统合冒烟共用锁与停止通道）；支持多轮次与轮间间隔。"""
    import time

    global _k11_unified_smoke_stream_active, _k11_unified_smoke_proc, _k11_unified_smoke_user_abort

    run_count = max(1, min(99, int(run_count)))
    interval_between_runs_sec = max(0, min(3600, int(interval_between_runs_sec)))

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
    _k11_unified_smoke_user_abort = False

    try:
        # 子进程会早退走 k11_subprocess_cli，须与 get_app_root() 一致并显式传入，避免仅继承错误 cwd/父 env
        try:
            _ja = str(Path(root).resolve())
        except Exception:
            _ja = str(root)
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "JACHIN_APP_ROOT": _ja}
        sub_task: asyncio.Task[int] | None = None
        response = _stream_response()
        keepalive_sec = 15.0
        ended_with_error_event: bool = False
    
        async def _write_line_obj(line: str) -> None:
            payload = {"line": line}
            await response.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
            if hasattr(response, "drain"):
                await response.drain()
    
        try:
            await response.prepare(request)
            all_runs_ok = True
            last_code = 0
            for run_idx in range(1, run_count + 1):
                if _k11_unified_smoke_user_abort:
                    await _write_line_obj("> 已按停止请求结束，不再执行后续轮次。")
                    break
                line_q: asyncio.Queue[str] = asyncio.Queue()
    
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
                        # 使用短超时 readline，使事件循环能穿插处理「停止」请求；否则子进程
                        # 长时间不输出时，某些环境下停止信号与进程终止的调度会更难及时生效。
                        while True:
                            try:
                                line = await asyncio.wait_for(
                                    proc.stdout.readline(), timeout=1.25
                                )
                            except asyncio.TimeoutError:
                                if (
                                    _k11_unified_smoke_user_abort
                                    and proc.returncode is None
                                ):
                                    with contextlib.suppress(Exception):
                                        proc.terminate()
                                    with contextlib.suppress(
                                        asyncio.TimeoutError, Exception
                                    ):
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
    
                if run_count > 1:
                    await _write_line_obj(
                        f"[K11] ========== 第 {run_idx} / {run_count} 轮 =========="
                    )
                sub_task = asyncio.create_task(_pump())
                if run_idx == 1:
                    await _write_line_obj(start_line)
                else:
                    await _write_line_obj(f"[K11] 子进程第 {run_idx} 轮已启动…")
                last_keepalive = time.monotonic()
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
                    last_code = 130
                    all_runs_ok = False
                    break
                exc = sub_task.exception()
                if exc is not None:
                    if isinstance(exc, asyncio.CancelledError):
                        last_code = 130
                        all_runs_ok = False
                    else:
                        err_pl = {"type": "error", "message": str(exc)}
                        await response.write(
                            f"data: {json.dumps(err_pl, ensure_ascii=False)}\n\n".encode("utf-8")
                        )
                        all_runs_ok = False
                        last_code = 1
                        ended_with_error_event = True
                        sub_task = None
                        break
                else:
                    last_code = int(sub_task.result())
                    if last_code != 0:
                        all_runs_ok = False
                sub_task = None
    
                if _k11_unified_smoke_user_abort:
                    await _write_line_obj("> 已按停止请求结束，不再执行后续轮次。")
                    break
                if run_idx < run_count and interval_between_runs_sec > 0:
                    await _write_line_obj(
                        f"> 第 {run_idx} 轮结束 (exit {last_code})，"
                        f"间隔 {interval_between_runs_sec} 秒后开始下一轮…"
                    )
                    await asyncio.sleep(float(interval_between_runs_sec))
                elif run_idx < run_count and interval_between_runs_sec == 0:
                    await _write_line_obj(
                        f"> 第 {run_idx} 轮结束 (exit {last_code})，立即开始下一轮…"
                    )
    
            cancelled = _k11_unified_smoke_user_abort
            if sub_task is not None and not sub_task.done():
                sub_task.cancel()
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await sub_task
            if not ended_with_error_event:
                if cancelled:
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
                    payload = {
                        "type": "done",
                        "ok": all_runs_ok and last_code == 0,
                        "exit_code": last_code,
                        "markdown_report": None,
                        "llm_analysis": None,
                    }
                    await response.write(
                        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
                    )
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
    finally:
        _k11_unified_smoke_user_abort = False
        _k11_unified_smoke_stream_active = False
    return response


async def _handle_k11_unified_smoke_stream(request) -> "aiohttp.web.StreamResponse | aiohttp.web.Response":
    """GET /api/v1/k11-unified-smoke/stream — 执行 ``scripts/test_k11_unified_platform_smoke_playwright.py``，SSE 行日志。"""
    root = get_app_root()
    script = k11_unified_smoke_script_path()
    if not script.is_file():
        return _json_response(
            {
                "ok": False,
                "error": (
                    f"缺少 K11 统合冒烟脚本: {script}（源码仓库需 scripts/；"
                    "打包侧车需含 PyInstaller --add-data；便携安装需 get_app_root()/scripts/）"
                ),
            },
            status=500,
        )

    params, err = _k11_smoke_stream_parse_query(request)
    if err is not None:
        return err
    assert params is not None
    target_url, cdp_http, verbose, no_lark, _head = params

    passthrough: list[str] = []
    if target_url:
        passthrough.extend(["--target-url", target_url])
    if cdp_http:
        passthrough.extend(["--cdp-http", cdp_http])
    if verbose:
        passthrough.append("-v")
    if no_lark:
        passthrough.append("--no-lark-report")

    cmd: list[str] = _k11_smoke_subprocess_cmd(
        "--jachin-k11-unified-smoke-subprocess", passthrough
    )

    try:
        runs = int(request.query.get("runs", "1"))
    except ValueError:
        runs = 1
    try:
        interval_sec = int(request.query.get("interval", "0"))
    except ValueError:
        interval_sec = 0

    start_msg = f"[K11] 统合冒烟已启动: {script.name}"
    if runs > 1:
        start_msg += f"（共 {runs} 轮，间隔 {interval_sec}s）"

    return await _k11_smoke_subprocess_sse_stream(
        request,
        root,
        cmd,
        start_msg,
        "k11 unified smoke",
        run_count=runs,
        interval_between_runs_sec=interval_sec,
    )


async def _handle_k11_p2_compat_only_stream(request) -> "aiohttp.web.StreamResponse | aiohttp.web.Response":
    """GET /api/v1/k11-p2-compat-only/stream — ``--only-compat`` 浏览器兼容段，SSE 行日志。"""
    root = get_app_root()
    script = k11_p2_compat_weaknet_script_path()
    if not script.is_file():
        return _json_response(
            {
                "ok": False,
                "error": f"缺少脚本: {script}（源码/打包/便携目录规则同 K11 统合脚本）",
            },
            status=500,
        )

    params, err = _k11_smoke_stream_parse_query(request)
    if err is not None:
        return err
    assert params is not None
    target_url, cdp_http, verbose, no_lark, headless = params

    passthrough: list[str] = ["--only-compat"]
    if headless:
        passthrough.append("--headless")
    if target_url:
        passthrough.extend(["--target-url", target_url])
    if cdp_http:
        passthrough.extend(["--cdp-http", cdp_http])
    if verbose:
        passthrough.append("-v")
    if no_lark:
        passthrough.append("--no-lark-report")

    cmd: list[str] = _k11_smoke_subprocess_cmd(
        "--jachin-k11-p2-compat-subprocess", passthrough
    )

    try:
        runs = int(request.query.get("runs", "1"))
    except ValueError:
        runs = 1
    try:
        interval_sec = int(request.query.get("interval", "0"))
    except ValueError:
        interval_sec = 0

    return await _k11_smoke_subprocess_sse_stream(
        request,
        root,
        cmd,
        f"[K11] P2 浏览器兼容已启动: {script.name} --only-compat",
        "k11 p2 compat",
        run_count=runs,
        interval_between_runs_sec=interval_sec,
    )


async def _handle_k11_game_open_smoke_stream(request) -> "aiohttp.web.StreamResponse | aiohttp.web.Response":
    """GET /api/v1/k11-game-open-smoke/stream — 执行 ``scripts/test_k11_game_open_smoke.py``，SSE 行日志。"""
    root = get_app_root()
    script = k11_game_open_smoke_script_path()
    if not script.is_file():
        # 须返回 SSE（勿仅 JSON 500）：否则控制台 EventSource 读不到 body，只表现为「SSE 已结束」。
        resp = _stream_response()
        await resp.prepare(request)
        payload = json.dumps(
            {
                "type": "error",
                "message": (
                    f"缺少脚本: {script}。frozen 侧车请重新执行 python scripts/build_l3_sidecar.py "
                    "（须将 test_k11_game_open_smoke.py 打入 _MEIPASS/scripts）；"
                    "或把该文件放到安装目录 scripts/ 下。"
                ),
            },
            ensure_ascii=False,
        )
        await resp.write(f"data: {payload}\n\n".encode("utf-8"))
        return resp

    params, err = _k11_smoke_stream_parse_query(request)
    if err is not None:
        return err
    assert params is not None
    target_url, _cdp_http, verbose, no_lark, _headless = params
    single_game = (request.query.get("single_game", "") or "").strip()

    passthrough: list[str] = []
    if target_url:
        passthrough.extend(["--target-url", target_url])
    if verbose:
        passthrough.append("-v")
    if no_lark:
        passthrough.append("--no-lark-report")
    if single_game:
        passthrough.extend(["--single-game", single_game])

    cmd: list[str] = _k11_smoke_subprocess_cmd(
        "--jachin-k11-game-open-smoke-subprocess", passthrough
    )
    return await _k11_smoke_subprocess_sse_stream(
        request,
        root,
        cmd,
        f"[K11] 游戏模块冒烟已启动: {script.name}",
        "k11 game open smoke",
        run_count=1,
        interval_between_runs_sec=0,
    )


async def _handle_k11_tongits_autoplay_smoke_stream(request) -> "aiohttp.web.StreamResponse | aiohttp.web.Response":
    """GET /api/v1/k11-tongits-autoplay-smoke/stream — 接管当前 Tongits 页打牌 + Lark 金币结算。"""
    root = get_app_root()
    script = k11_tongits_autoplay_smoke_script_path()
    if not script.is_file():
        resp = _stream_response()
        await resp.prepare(request)
        payload = json.dumps(
            {
                "type": "error",
                "message": (
                    f"缺少脚本: {script}。请放入 scripts/ 或重新 build_l3_sidecar "
                    "（test_k11_tongits_autoplay_smoke.py + k11_tongits_smoke_session.py）。"
                ),
            },
            ensure_ascii=False,
        )
        await resp.write(f"data: {payload}\n\n".encode("utf-8"))
        return resp

    params, err = _k11_smoke_stream_parse_query(request)
    if err is not None:
        return err
    assert params is not None
    target_url, cdp_http, verbose, no_lark, _headless = params
    round_wait = (request.query.get("round_wait_sec") or "").strip()
    launch_browser = str(request.query.get("launch_browser", "")).lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    passthrough: list[str] = []
    if target_url:
        passthrough.extend(["--target-url", target_url])
    if cdp_http:
        passthrough.extend(["--cdp-http", cdp_http])
    if verbose:
        passthrough.append("-v")
    if no_lark:
        passthrough.append("--no-lark-report")
    if launch_browser:
        passthrough.append("--launch-browser")
    if round_wait:
        passthrough.extend(["--round-wait-sec", round_wait])

    cmd: list[str] = _k11_smoke_subprocess_cmd(
        "--jachin-k11-tongits-autoplay-smoke-subprocess", passthrough
    )
    return await _k11_smoke_subprocess_sse_stream(
        request,
        root,
        cmd,
        f"[K11] Tongits 当前牌桌接管已启动: {script.name}",
        "k11 tongits autoplay smoke",
        run_count=1,
        interval_between_runs_sec=0,
    )


async def _handle_k11_unified_smoke_schedule_toggle(request) -> "aiohttp.web.Response":
    """POST /api/v1/k11-unified-smoke/schedule/toggle — JSON: enabled, hour_beijing?, minute_beijing?, runs?, interval_sec?, hourly_recurring?"""
    from l3_node.k11_smoke_debug_log import (
        k11_smoke_debug_init_once,
        k11_smoke_debug_exc,
        k11_smoke_debug_line,
        k11_smoke_debug_mapping,
    )

    k11_smoke_debug_init_once()
    k11_smoke_debug_line(
        "POST schedule/toggle from=%s",
        str(getattr(request, "remote", None) or request.headers.get("X-Real-IP") or "?"),
    )
    try:
        body = await request.json() if request.body_exists else {}
    except Exception as e:
        k11_smoke_debug_exc("schedule/toggle json", e)
        return _json_response({"ok": False, "error": f"JSON 解析失败: {e}"}, status=400)
    try:
        if isinstance(body, dict):
            k11_smoke_debug_mapping("toggle json", body)
        else:
            k11_smoke_debug_line("toggle body (non-dict): %r", body)
    except Exception:
        pass
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        k11_smoke_debug_line("toggle 拒绝: enabled 非 bool")
        return _json_response({"ok": False, "error": "缺少布尔字段 enabled"}, status=400)
    try:
        from l3_node.jobs.k11_unified_smoke_scheduler import (
            apply_k11_unified_smoke_schedule,
            scheduler_status,
        )
    except Exception as e:
        k11_smoke_debug_exc("import k11_unified_smoke_scheduler", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)

    hb = body.get("hour_beijing")
    mb = body.get("minute_beijing")
    runs = body.get("runs")
    iv = body.get("interval_sec")
    hr = body.get("hourly_recurring")
    hourly_recurring: bool | None = None
    if isinstance(hr, bool):
        hourly_recurring = hr
    elif hr is not None and str(hr).strip() != "":
        hourly_recurring = str(hr).lower() in ("1", "true", "yes", "on")
    try:
        r = apply_k11_unified_smoke_schedule(
            enabled=enabled,
            hour_beijing=int(hb) if hb is not None else None,
            minute_beijing=int(mb) if mb is not None else None,
            runs=int(runs) if runs is not None else None,
            interval_sec=int(iv) if iv is not None else None,
            hourly_recurring=hourly_recurring,
        )
        k11_smoke_debug_line("apply_k11 result: %r", r)
    except Exception as e:
        k11_smoke_debug_exc("apply_k11_unified_smoke_schedule", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)
    st = scheduler_status()
    k11_smoke_debug_line("scheduler_status after toggle: %r", st)
    return _json_response({**r, "ok": True, **st})


async def _handle_k11_unified_smoke_schedule_status(request) -> "aiohttp.web.Response":
    """GET /api/v1/k11-unified-smoke/schedule/status"""
    try:
        from l3_node.jobs.k11_unified_smoke_scheduler import scheduler_status
        from l3_node.k11_smoke_debug_log import k11_smoke_debug_init_once, k11_smoke_debug_line

        k11_smoke_debug_init_once()
        k11_smoke_debug_line(
            "GET schedule/status from=%s",
            str(getattr(request, "remote", None) or request.headers.get("X-Real-IP") or "?"),
        )
        st = {**{"ok": True}, **scheduler_status()}
        k11_smoke_debug_line("status payload: %r", st)
        return _json_response(st)
    except Exception as e:
        logger.warning("[L3 HTTP] k11 smoke schedule status failed: %s", e)
        try:
            from l3_node.k11_smoke_debug_log import k11_smoke_debug_exc

            k11_smoke_debug_exc("schedule_status", e)
        except Exception:
            pass
        return _json_response({"ok": False, "active": False, "error": str(e)}, status=500)


async def _handle_k11_unified_smoke_schedule_log_stream(request) -> "aiohttp.web.StreamResponse | aiohttp.web.Response":
    """GET /api/v1/k11-unified-smoke/schedule/log-stream — 定时批跑子进程行输出，供 Jachin 控制台 MIND STREAM 与桌面通知复用。"""
    try:
        from l3_node.jobs.k11_unified_smoke_scheduler import (
            k11_scheduled_log_ring_snapshot,
            subscribe_k11_scheduled_log,
            unsubscribe_k11_scheduled_log,
        )
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)

    try:
        from l3_node.k11_smoke_debug_log import k11_smoke_debug_init_once, k11_smoke_debug_line

        k11_smoke_debug_init_once()
        k11_smoke_debug_line(
            "GET schedule/log-stream (SSE) from=%s",
            str(getattr(request, "remote", None) or request.headers.get("X-Real-IP") or "?"),
        )
    except Exception:
        pass

    q = subscribe_k11_scheduled_log()
    response = _stream_response()
    try:
        await response.prepare(request)
        for obj in k11_scheduled_log_ring_snapshot():
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
        logger.warning("[L3 HTTP] k11 schedule log stream: %s", e)
    finally:
        unsubscribe_k11_scheduled_log(q)
    return response


async def _handle_k11_unified_smoke_stop(request) -> "aiohttp.web.Response":
    """POST /api/v1/k11-unified-smoke/stop — 终止统合冒烟子进程（Playwright 链）。"""
    global _k11_unified_smoke_proc, _k11_unified_smoke_user_abort, _k11_unified_smoke_stream_active
    p = _k11_unified_smoke_proc
    had_active = p is not None and p.returncode is None
    _k11_unified_smoke_user_abort = True
    if not had_active:
        if _k11_unified_smoke_stream_active:
            _k11_unified_smoke_stream_active = False
        return _json_response(
            {
                "ok": True,
                "active_child": False,
                "message": (
                    "无运行中的 K11 子进程；已记录停止。若曾出现"
                    "「已在执行中」但无子进程，已同时重置占用状态。"
                ),
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
        logger.warning("[L3 HTTP] k11 unified smoke stop failed: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)
    return _json_response(
        {
            "ok": True,
            "active_child": True,
            "message": "已发送停止信号",
        }
    )


async def _handle_cron_thinker_ingest_release(request) -> "aiohttp.web.Response":
    """POST /api/v1/cron-thinker/ingest-release-announcement — 邮件转发原文等，对齐飞书公告规则。"""
    tok = (os.environ.get("JACHIN_CRON_THINKER_INGEST_TOKEN") or "").strip()
    if tok:
        hdr = (request.headers.get("X-Jachin-Cron-Thinker-Token") or "").strip()
        if hdr != tok:
            return _json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        body = await request.json()
    except Exception:
        return _json_response({"ok": False, "error": "invalid json"}, status=400)
    if not isinstance(body, dict):
        return _json_response({"ok": False, "error": "body must be a json object"}, status=400)
    text = body.get("text")
    if text is None or not str(text).strip():
        return _json_response({"ok": False, "error": "text required"}, status=400)
    try:
        from core.cron_thinker import _audit_log, _audit_trunc, feed_release_announcement_text

        text_s = str(text).strip()
        hdr_s = (request.headers.get("X-Jachin-Cron-Thinker-Token") or "").strip()
        _audit_log(
            "http_ingest_release",
            remote=request.remote,
            path=str(request.path_qs),
            text_char_len=len(text_s),
            text_head=_audit_trunc(text_s, 6000),
            auth_configured=bool(tok),
            auth_ok=(not tok or hdr_s == tok),
        )
        r = feed_release_announcement_text(text_s, source="http")
    except Exception as e:
        logger.warning("[L3 HTTP] cron_thinker ingest: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)
    return _json_response(r, status=200)


async def _handle_cron_thinker_release_smoke_status(request) -> "aiohttp.web.Response":
    """GET /api/v1/cron-thinker/release-smoke-status — 发版次日冒烟调度摘要。"""
    try:
        from core.cron_thinker import cron_thinker_scheduler_status

        return _json_response(cron_thinker_scheduler_status())
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_cron_thinker_bios_settings_get(request) -> "aiohttp.web.Response":
    """GET /api/v1/cron-thinker/bios-settings — 发版公告生物钟（控制台）当前配置。"""
    try:
        from core.cron_thinker import _mail_poll_enabled, _release_smoke_enabled, load_bios_settings

        return _json_response(
            {
                "ok": True,
                "settings": load_bios_settings(),
                "env": {
                    "release_smoke": _release_smoke_enabled(),
                    "mail_poll": _mail_poll_enabled(),
                },
            }
        )
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_cron_thinker_bios_settings_post(request) -> "aiohttp.web.Response":
    """POST /api/v1/cron-thinker/bios-settings — 合并保存生物钟开关/第几日/时刻，并重挂轮询（可选）。"""
    try:
        body = await request.json()
    except Exception:
        return _json_response({"ok": False, "error": "invalid json"}, status=400)
    if not isinstance(body, dict):
        return _json_response({"ok": False, "error": "body must be a json object"}, status=400)
    try:
        from core.cron_thinker import _audit_log, apply_bios_runtime, save_bios_settings

        keys = sorted(str(k) for k in body.keys())
        apply_raw = str(body.get("apply_runtime", "1")).strip().lower()
        ar = apply_raw not in ("0", "false", "no", "off")
        _audit_log(
            "http_bios_settings_post",
            remote=request.remote,
            body_keys=keys,
            apply_runtime=ar,
        )
        saved = save_bios_settings(body)
        rt = apply_bios_runtime() if ar else {"ok": True, "skipped_runtime": True}
        return _json_response({"ok": True, "settings": saved, "runtime": rt})
    except Exception as e:
        logger.warning("[L3 HTTP] cron_thinker bios-settings POST: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_registry_external_sched_hint_post(request) -> "aiohttp.web.Response":
    """POST /api/v1/registry/external-sched-hint — 合并外部定时心跳（与 workspace/external_scheduled_hints.json 同源）。"""
    tok = (os.environ.get("JACHIN_REGISTRY_EXTERNAL_SCHED_TOKEN") or "").strip()
    if not tok:
        return _json_response(
            {
                "ok": False,
                "error": "Set JACHIN_REGISTRY_EXTERNAL_SCHED_TOKEN to enable this endpoint",
            },
            status=503,
        )
    hdr = (request.headers.get("X-Jachin-Registry-Token") or "").strip()
    if hdr != tok:
        return _json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        body = await request.json()
    except Exception:
        return _json_response({"ok": False, "error": "invalid json"}, status=400)
    if not isinstance(body, dict):
        return _json_response({"ok": False, "error": "body must be object"}, status=400)
    pk = str(body.get("process_key") or "").strip()
    if not pk or len(pk) > 120:
        return _json_response({"ok": False, "error": "process_key required (max 120)"}, status=400)
    title = str(body.get("title") or "").strip()
    if not title:
        return _json_response({"ok": False, "error": "title required"}, status=400)
    sched_sum = str(body.get("schedule_summary") or "").strip()
    pid_v: int | None = None
    if body.get("pid") is not None:
        try:
            pid_v = int(body["pid"])
        except (TypeError, ValueError):
            pid_v = None
    try:
        from l3_node.task_runtime_registry import merge_external_scheduled_process_hint

        merge_external_scheduled_process_hint(
            process_key=pk,
            title=title[:240],
            schedule_summary=(sched_sum or "（HTTP 登记）")[:480],
            pid=pid_v,
        )
    except Exception as e:
        logger.warning("[L3 HTTP] registry external-sched-hint: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)
    return _json_response({"ok": True, "process_key": pk}, status=200)


async def _handle_registry_external_sched_hint_delete(request) -> "aiohttp.web.Response":
    """DELETE /api/v1/registry/external-sched-hint — JSON body `{ \"process_key\": \"...\" }`。"""
    tok = (os.environ.get("JACHIN_REGISTRY_EXTERNAL_SCHED_TOKEN") or "").strip()
    if not tok:
        return _json_response(
            {"ok": False, "error": "Set JACHIN_REGISTRY_EXTERNAL_SCHED_TOKEN to enable this endpoint"},
            status=503,
        )
    hdr = (request.headers.get("X-Jachin-Registry-Token") or "").strip()
    if hdr != tok:
        return _json_response({"ok": False, "error": "unauthorized"}, status=401)
    try:
        body = await request.json()
    except Exception:
        return _json_response({"ok": False, "error": "invalid json"}, status=400)
    if not isinstance(body, dict):
        return _json_response({"ok": False, "error": "body must be object"}, status=400)
    pk = str(body.get("process_key") or "").strip()
    if not pk:
        return _json_response({"ok": False, "error": "process_key required"}, status=400)
    try:
        from l3_node.task_runtime_registry import remove_external_scheduled_process_hint

        removed = remove_external_scheduled_process_hint(pk)
    except Exception as e:
        logger.warning("[L3 HTTP] registry external-sched-hint DELETE: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)
    if not removed:
        return _json_response({"ok": False, "process_key": pk, "error": "not found"}, status=404)
    return _json_response({"ok": True, "process_key": pk}, status=200)


async def _handle_hook_events_recent_get(request) -> "aiohttp.web.Response":
    """GET /api/v1/registry/hook-events-recent?limit=50&hook=...&run_id=..."""
    bad = _registry_diag_auth_failure(request)
    if bad is not None:
        return bad
    try:
        lim = int(request.query.get("limit", "50"))
    except ValueError:
        lim = 50
    hook_q = (request.query.get("hook") or "").strip() or None
    run_q = (request.query.get("run_id") or "").strip() or None
    run_exact = (request.query.get("run_id_exact") or "").strip().lower() in ("1", "true", "yes")
    try:
        from l3_node.engine.persistent_hook_log import read_recent_hook_events

        rows = read_recent_hook_events(
            limit=lim, hook=hook_q, run_id=run_q, run_id_exact=run_exact
        )
    except Exception as e:
        logger.warning("[L3 HTTP] hook-events-recent: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)
    return _json_response({"ok": True, "count": len(rows), "events": rows}, status=200)


async def _handle_registry_runtime_snapshot_get(request) -> "aiohttp.web.Response":
    """GET /api/v1/registry/runtime-snapshot?session_key=&chat_id=（后两者可选，用于 HTTP 同会话锁探针）。"""
    bad = _registry_diag_auth_failure(request)
    if bad is not None:
        return bad
    try:
        from l3_node.task_runtime_registry import get_runtime_registry_snapshot_dict

        snap = get_runtime_registry_snapshot_dict()
    except Exception as e:
        logger.warning("[L3 HTTP] runtime-snapshot: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)
    sk = (request.query.get("session_key") or request.query.get("chat_id") or "").strip()
    if sk:
        try:
            from l3_node.session_hot_user_inject import peek_pending_session_user

            _hot = peek_pending_session_user(sk)
            if _hot:
                snap["session_hot_user_pending"] = _hot
        except Exception:
            pass
        try:
            busy = await _http_agent_session_lock_held(sk)
        except Exception as e:
            logger.warning("[L3 HTTP] runtime-snapshot lock probe: %s", e)
            busy = False
        snap = {**snap, "http_agent_session": {"session_key": sk, "lock_held": busy}}
    try:
        from l3_node.global_task_registry import get_global_registry_summary

        snap["global_task_registry"] = get_global_registry_summary()
    except Exception:
        pass
    try:
        from l3_node.session_instruction_queue import get_all_session_stats, siq_enabled

        snap["session_instruction_queue"] = {
            "enabled": siq_enabled(),
            "sessions": get_all_session_stats(),
        }
    except Exception:
        pass
    try:
        from l3_node.task_engine.dag_node_sync import dag_node_sync_enabled, get_next_pending_dag_node

        snap["dag_node_sync_enabled"] = dag_node_sync_enabled()
        snap["task_dag_next_pending"] = get_next_pending_dag_node()
    except Exception:
        pass
    try:
        from l3_node.engine.execution_resilience_chain import strategy_chain_enabled

        snap["resilience_strategy_chain"] = strategy_chain_enabled()
    except Exception:
        pass
    return _json_response({"ok": True, **snap}, status=200)


async def _handle_registry_global_tasks_get(request) -> "aiohttp.web.Response":
    """GET /api/v1/registry/global-tasks — GlobalTaskRegistry 集群 SSOT 摘要（SQLite / Redis）。"""
    bad = _registry_diag_auth_failure(request)
    if bad is not None:
        return bad
    try:
        from l3_node.global_task_registry import get_global_registry_summary, list_running_tasks

        include_done = (request.query.get("include_done") or "").strip().lower() in (
            "1", "true", "yes",
        )
        summary = get_global_registry_summary()
        tasks = [t.to_dict() for t in list_running_tasks(include_done=include_done)]
        return _json_response(
            {"ok": True, **summary, "tasks_full": tasks[:100]},
            status=200,
        )
    except Exception as e:
        logger.warning("[L3 HTTP] global-tasks GET: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_registry_siq_sessions_get(request) -> "aiohttp.web.Response":
    """GET /api/v1/registry/siq-sessions — SessionInstructionQueue 会话统计（AU）。"""
    bad = _registry_diag_auth_failure(request)
    if bad is not None:
        return bad
    try:
        from l3_node.session_instruction_queue import get_all_session_stats, siq_enabled, siq_mode

        return _json_response(
            {
                "ok": True,
                "enabled": siq_enabled(),
                "mode": siq_mode(),
                "sessions": get_all_session_stats(),
            },
            status=200,
        )
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_registry_external_scheduled_hints_get(request) -> "aiohttp.web.Response":
    """GET /api/v1/registry/external-scheduled-hints — 只读 **M** 心跳文件。"""
    bad = _registry_diag_auth_failure(request)
    if bad is not None:
        return bad
    try:
        from l3_node.task_runtime_registry import read_external_scheduled_hints_dict

        hint_body = read_external_scheduled_hints_dict()
    except Exception as e:
        logger.warning("[L3 HTTP] external-scheduled-hints GET: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)
    return _json_response({"ok": True, **hint_body}, status=200)


async def _handle_registry_task_dag_active_get(request) -> "aiohttp.web.Response":
    """GET /api/v1/registry/task-dag-active — 只读 `workspace/task_dags/active.json`（**H**）。"""
    bad = _registry_diag_auth_failure(request)
    if bad is not None:
        return bad
    try:
        from l3_node.task_engine.task_dag import load_task_dag_dict

        dag = load_task_dag_dict()
    except Exception as e:
        logger.warning("[L3 HTTP] task-dag-active GET: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)
    return _json_response({"ok": True, "dag": dag}, status=200)


async def _handle_registry_dag_guardrails_get(request) -> "aiohttp.web.Response":
    """
    GET /api/v1/registry/dag-guardrails?dag_id=&limit= — DAG 级 Guardrails 预算状态（AP）。
    dag_id 不传时列出最近活跃的所有 DAG 预算记录。
    """
    bad = _registry_diag_auth_failure(request)
    if bad is not None:
        return bad
    dag_id = (request.query.get("dag_id") or "").strip()
    try:
        limit = int(request.query.get("limit", "20"))
    except ValueError:
        limit = 20
    try:
        from l3_node.task_engine.dag_guardrails import (
            DagGuardrailsChecker,
            dag_guardrails_enabled,
            list_active_dag_budgets,
            load_dag_budget,
        )
        enabled = dag_guardrails_enabled()
        if dag_id:
            state = load_dag_budget(dag_id)
            checker = DagGuardrailsChecker(dag_id)
            violation = checker.check_dag_budget() if enabled else None
            return _json_response({
                "ok": True,
                "enabled": enabled,
                "budget": state.to_dict(),
                "violation": {"rule": violation.rule, "message": violation.message} if violation else None,
            }, status=200)
        else:
            budgets = list_active_dag_budgets(limit=limit)
            return _json_response({"ok": True, "enabled": enabled, "budgets": budgets}, status=200)
    except Exception as e:
        logger.warning("[L3 HTTP] dag-guardrails GET: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_registry_dag_handoff_export_post(request) -> "aiohttp.web.Response":
    """
    POST /api/v1/registry/dag-handoff/export — 导出当前 DAG Handoff Package（AR）。
    Body JSON:
      { "run_id": "...", "context_hint": "..." }
    返回可直接 POST 到另一节点 /import 的 JSON 包。
    """
    bad = _registry_diag_auth_failure(request)
    if bad is not None:
        return bad
    try:
        body: dict = await request.json()
    except Exception:
        body = {}
    run_id = (body.get("run_id") or request.query.get("run_id") or "").strip()
    context_hint = (body.get("context_hint") or "").strip()
    try:
        from l3_node.task_engine.dag_handoff import export_dag_handoff

        pkg = export_dag_handoff(run_id, context_hint=context_hint)
        if pkg is None:
            return _json_response({"ok": False, "error": "无法导出：active.json 不存在或 DAG 已完成"}, status=404)
        return _json_response({"ok": True, "package": pkg.to_dict()}, status=200)
    except Exception as e:
        logger.warning("[L3 HTTP] dag-handoff/export POST: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_registry_dag_handoff_import_post(request) -> "aiohttp.web.Response":
    """
    POST /api/v1/registry/dag-handoff/import — 导入 Handoff Package 并准备续跑（AR）。
    Body JSON: DagHandoffPackage（直接传 /export 返回的 package 字段）
    返回 HandoffImportResult + resume_intent 供调用方传给 run_agent。
    """
    bad = _registry_diag_auth_failure(request)
    if bad is not None:
        return bad
    try:
        body: dict = await request.json()
    except Exception:
        return _json_response({"ok": False, "error": "invalid JSON body"}, status=400)
    # 支持直接传整个 export 响应（含 package 嵌套）或裸包
    package_data = body.get("package") or body
    try:
        from l3_node.task_engine.dag_handoff import import_dag_handoff

        result = import_dag_handoff(package_data)
        return _json_response(result.to_dict(), status=200 if result.ok else 422)
    except Exception as e:
        logger.warning("[L3 HTTP] dag-handoff/import POST: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_registry_dag_handoff_list_get(request) -> "aiohttp.web.Response":
    """
    GET /api/v1/registry/dag-handoff/list — 列出 JACHIN_DAG_HANDOFF_DIR 中待导入的包（AR）。
    """
    bad = _registry_diag_auth_failure(request)
    if bad is not None:
        return bad
    try:
        limit = int(request.query.get("limit", "10"))
    except ValueError:
        limit = 10
    try:
        from l3_node.task_engine.dag_handoff import list_available_handoff_packages

        packages = list_available_handoff_packages(limit=limit)
        return _json_response({"ok": True, "packages": packages}, status=200)
    except Exception as e:
        logger.warning("[L3 HTTP] dag-handoff/list GET: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_dag_handoff_auto_transfer_post(request) -> "aiohttp.web.Response":
    """
    POST /api/v1/registry/dag-handoff/auto-transfer — 自动将 DAG 转交给空闲对等节点（AS）。
    Body JSON: { "run_id": "...", "context_hint": "...", "release_lock": true }
    """
    bad = _registry_diag_auth_failure(request)
    if bad is not None:
        return bad
    try:
        body: dict = await request.json()
    except Exception:
        body = {}
    run_id = (body.get("run_id") or "").strip()
    context_hint = (body.get("context_hint") or "").strip()
    release_lock = bool(body.get("release_lock", True))
    try:
        from l3_node.task_engine.dag_handoff import auto_handoff_to_peer

        result = await auto_handoff_to_peer(run_id, context_hint=context_hint, release_lock=release_lock)
        return _json_response(result, status=200 if result.get("ok") else 503)
    except Exception as e:
        logger.warning("[L3 HTTP] dag-handoff/auto-transfer POST: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


# ---------------------------------------------------------------------------
# AS — DAG Coordinator 端点
# ---------------------------------------------------------------------------

async def _handle_coordinator_info_get(request) -> "aiohttp.web.Response":
    """GET /api/v1/registry/coordinator/info — 本节点协调器状态摘要（AS）。"""
    try:
        from l3_node.task_engine.dag_coordinator import get_coordinator_info
        return _json_response(get_coordinator_info(), status=200)
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_coordinator_peers_get(request) -> "aiohttp.web.Response":
    """GET /api/v1/registry/coordinator/peers?include_http=0 — 列出活跃对等节点（AS）。"""
    bad = _registry_diag_auth_failure(request)
    if bad is not None:
        return bad
    include_http = request.query.get("include_http", "0") not in ("0", "false", "no")
    try:
        from l3_node.task_engine.dag_coordinator import discover_http_peers, list_alive_nodes

        nodes = [n.to_dict() for n in list_alive_nodes()]
        if include_http:
            http_peers = await discover_http_peers()
            existing_ids = {n["node_id"] for n in nodes}
            for hp in http_peers:
                if hp.node_id not in existing_ids:
                    nodes.append(hp.to_dict())
        return _json_response({"ok": True, "nodes": nodes, "count": len(nodes)}, status=200)
    except Exception as e:
        logger.warning("[L3 HTTP] coordinator/peers GET: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_coordinator_register_post(request) -> "aiohttp.web.Response":
    """
    POST /api/v1/registry/coordinator/register — 节点自注册心跳（AS）。
    Body JSON: { "node_id": "...", "http_url": "...", "load_score": 0.0 }
    """
    try:
        body: dict = await request.json()
    except Exception:
        body = {}
    node_id = (body.get("node_id") or "").strip()
    http_url = (body.get("http_url") or "").strip()
    load_score = float(body.get("load_score") or 0.0)
    if not node_id:
        return _json_response({"ok": False, "error": "node_id required"}, status=400)
    try:
        from l3_node.task_engine.dag_coordinator import register_node
        register_node(node_id, http_url, load_score=load_score)
        return _json_response({"ok": True, "node_id": node_id}, status=200)
    except Exception as e:
        logger.warning("[L3 HTTP] coordinator/register POST: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_coordinator_dag_claim_post(request) -> "aiohttp.web.Response":
    """
    POST /api/v1/registry/coordinator/dag-claim — 抢占 DAG 分布式锁（AS）。
    Body JSON: { "dag_id": "...", "node_id": "..." }
    Returns: { "ok": bool, "lock_token": "...", "message": "..." }
    """
    bad = _registry_diag_auth_failure(request)
    if bad is not None:
        return bad
    try:
        body: dict = await request.json()
    except Exception:
        body = {}
    dag_id = (body.get("dag_id") or "").strip()
    node_id = (body.get("node_id") or "").strip()
    if not dag_id or not node_id:
        return _json_response({"ok": False, "error": "dag_id and node_id required"}, status=400)
    try:
        from l3_node.task_engine.dag_coordinator import claim_dag
        success, token = claim_dag(dag_id, node_id)
        return _json_response({
            "ok": success,
            "lock_token": token if success else "",
            "message": "锁获取成功" if success else f"DAG「{dag_id}」已被其他节点持有",
        }, status=200 if success else 409)
    except Exception as e:
        logger.warning("[L3 HTTP] coordinator/dag-claim POST: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_coordinator_dag_release_delete(request) -> "aiohttp.web.Response":
    """
    DELETE /api/v1/registry/coordinator/dag-claim/{dag_id} — 释放 DAG 锁（AS）。
    Body JSON: { "node_id": "...", "lock_token": "..." }
    """
    bad = _registry_diag_auth_failure(request)
    if bad is not None:
        return bad
    dag_id = request.match_info.get("dag_id", "").strip()
    try:
        body: dict = await request.json()
    except Exception:
        body = {}
    node_id = (body.get("node_id") or "").strip()
    lock_token = (body.get("lock_token") or "").strip()
    if not dag_id or not node_id or not lock_token:
        return _json_response({"ok": False, "error": "dag_id, node_id and lock_token required"}, status=400)
    try:
        from l3_node.task_engine.dag_coordinator import release_dag
        success = release_dag(dag_id, node_id, lock_token)
        return _json_response({"ok": success}, status=200 if success else 403)
    except Exception as e:
        logger.warning("[L3 HTTP] coordinator/dag-claim DELETE: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_coordinator_dag_locks_get(request) -> "aiohttp.web.Response":
    """GET /api/v1/registry/coordinator/dag-locks — 列出当前有效的所有 DAG 锁（AS）。"""
    bad = _registry_diag_auth_failure(request)
    if bad is not None:
        return bad
    try:
        from l3_node.task_engine.dag_coordinator import list_dag_locks
        locks = [lk.to_dict() for lk in list_dag_locks()]
        return _json_response({"ok": True, "locks": locks, "count": len(locks)}, status=200)
    except Exception as e:
        logger.warning("[L3 HTTP] coordinator/dag-locks GET: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_registry_hook_replay_get(request) -> "aiohttp.web.Response":
    """
    GET /api/v1/registry/hook-replay?run_id=... — Hook 回放探针（BJ）。
    需 JACHIN_PERSIST_HOOKS=1 且 JACHIN_HOOK_REPLAY_ENABLE=1。
    """
    bad = _registry_diag_auth_failure(request)
    if bad is not None:
        return bad
    run_id = (request.query.get("run_id") or "").strip()
    if not run_id:
        return _json_response({"ok": False, "error": "run_id required"}, status=400)
    try:
        from l3_node.engine.hook_replay_executor import probe_hook_replay, replay_enabled

        if not replay_enabled():
            return _json_response(
                {"ok": False, "error": "JACHIN_HOOK_REPLAY_ENABLE not set"},
                status=503,
            )
        result = probe_hook_replay(run_id)
    except Exception as e:
        logger.warning("[L3 HTTP] hook-replay GET: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)
    body = result.to_dict()
    body["ok"] = result.ok
    return _json_response(body, status=200 if result.ok else 404)


async def _handle_registry_hook_replay_post(request) -> "aiohttp.web.Response":
    """
    POST /api/v1/registry/hook-replay — Hook 回放 + 可选 DAG 续跑应用 + 可选自动 run_agent（BJ）。
    Body: {
      "run_id": "...",
      "mode": "probe"|"apply",
      "apply_dag_resume": true,
      "auto_run_agent": false,
      "user_input": "",
      "final_answer": "",
      "implicit_attribution": {}
    }
    """
    bad = _registry_diag_auth_failure(request)
    if bad is not None:
        return bad
    try:
        body: dict = await request.json()
    except Exception:
        body = {}
    run_id = (body.get("run_id") or request.query.get("run_id") or "").strip()
    if not run_id:
        return _json_response({"ok": False, "error": "run_id required"}, status=400)
    mode = str(body.get("mode") or "probe").strip().lower()
    apply_dag = body.get("apply_dag_resume", mode == "apply")
    if isinstance(apply_dag, str):
        apply_dag = apply_dag.strip().lower() in ("1", "true", "yes")
    auto_run = body.get("auto_run_agent", False)
    if isinstance(auto_run, str):
        auto_run = auto_run.strip().lower() in ("1", "true", "yes")
    try:
        from l3_node.engine.hook_replay_executor import (
            HookReplayFollowupContext,
            apply_hook_replay,
            probe_hook_replay,
            replay_enabled,
            schedule_hook_replay_followup_run,
        )

        if not replay_enabled():
            return _json_response(
                {"ok": False, "error": "JACHIN_HOOK_REPLAY_ENABLE not set"},
                status=503,
            )
        if mode == "apply":
            result = apply_hook_replay(run_id, apply_dag_resume=bool(apply_dag))
        else:
            result = probe_hook_replay(run_id)
        if auto_run:
            try:
                from l3_node.agent_ref import engine_ref

                engine = engine_ref.get("engine")
            except ImportError:
                engine = None
            _iatt = body.get("implicit_attribution")
            _iatt = _iatt if isinstance(_iatt, dict) else None
            followup = HookReplayFollowupContext(
                parent_run_id=run_id,
                user_input=str(body.get("user_input") or ""),
                final_answer=str(body.get("final_answer") or ""),
                session_messages=None,
                implicit_attribution=_iatt,
            )
            if engine is not None:
                schedule_hook_replay_followup_run(result, engine, followup=followup)
            else:
                result.message += "；auto_run_agent 跳过（engine 未就绪）"
    except Exception as e:
        logger.warning("[L3 HTTP] hook-replay POST: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)
    payload = result.to_dict()
    payload["ok"] = result.ok
    return _json_response(payload, status=200 if result.ok else 404)


async def _handle_registry_preempt_cancel_post(request) -> "aiohttp.web.Response":
    """
    POST /api/v1/registry/preempt-cancel — 跨机/跨进程抢占取消（BG）。
    Body: { "run_id": "..." }
    """
    bad = _registry_diag_auth_failure(request)
    if bad is not None:
        return bad
    try:
        body: dict = await request.json()
    except Exception:
        body = {}
    run_id = (body.get("run_id") or "").strip()
    if not run_id:
        return _json_response({"ok": False, "error": "run_id required"}, status=400)
    cancelled = False
    try:
        from l3_node.foreground_run_registry import request_cancel_run

        cancelled = bool(request_cancel_run(run_id))
    except Exception as e:
        return _json_response({"ok": False, "error": str(e)}, status=500)
    return _json_response({"ok": True, "run_id": run_id, "cancelled": cancelled}, status=200)


async def _handle_registry_dag_resume_post(request) -> "aiohttp.web.Response":
    """
    POST /api/v1/registry/dag-resume — DAG 轻量续跑探测与应用（AO）。
    Body JSON:
      { "run_id": "<原始 run_id>", "dry_run": true }
    dry_run=true（默认）只探测，不修改 active.json；
    dry_run=false 时将待续跑节点重置为 pending 并更新 active.json。
    """
    bad = _registry_diag_auth_failure(request)
    if bad is not None:
        return bad
    try:
        body: dict = await request.json()
    except Exception:
        body = {}
    run_id_param = (body.get("run_id") or request.query.get("run_id") or "").strip()
    dry_run = bool(body.get("dry_run", True))
    try:
        from l3_node.task_engine.dag_resume import apply_dag_resume, probe_dag_resume

        if dry_run:
            result = probe_dag_resume(run_id_param)
        else:
            result = apply_dag_resume(run_id_param)
    except Exception as e:
        logger.warning("[L3 HTTP] dag-resume POST: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)
    return _json_response(result.to_dict(), status=200)


async def _handle_registry_im_channel_pending_get(request) -> "aiohttp.web.Response":
    """GET /api/v1/registry/im-channel-pending?chat_id=&limit= — 飞书 IM 线程池待处理深度（**W**）。"""
    bad = _registry_diag_auth_failure(request)
    if bad is not None:
        return bad
    try:
        lim = int(request.query.get("limit", "64"))
    except ValueError:
        lim = 64
    chat_q = (request.query.get("chat_id") or "").strip() or None
    try:
        from l3_node.im_channels.dispatcher import get_im_dispatcher_inflight_snapshot

        body = get_im_dispatcher_inflight_snapshot(chat_id=chat_q, limit=lim)
    except Exception as e:
        logger.warning("[L3 HTTP] im-channel-pending GET: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)
    return _json_response({"ok": True, **body}, status=200)


async def _handle_autonomy_intents_list(request) -> "aiohttp.web.Response":
    """GET /api/v1/autonomy/intents — 列出所有持久化意图（**Z**）。"""
    bad = _registry_diag_auth_failure(request)
    if bad is not None:
        return bad
    enabled_only = (request.query.get("enabled_only") or "").strip().lower() in ("1", "true")
    try:
        from l3_node.autonomy.intent_persister import get_intent_persister
        intents = get_intent_persister().list_all(enabled_only=enabled_only)
        return _json_response({"ok": True, "count": len(intents), "intents": [i.to_dict() for i in intents]})
    except Exception as e:
        logger.warning("[L3 HTTP] autonomy/intents GET: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_autonomy_intents_post(request) -> "aiohttp.web.Response":
    """POST /api/v1/autonomy/intents — 创建持久化意图（**Z**）。"""
    bad = _registry_diag_auth_failure(request)
    if bad is not None:
        return bad
    try:
        body = await request.json()
    except Exception:
        return _json_response({"ok": False, "error": "invalid JSON"}, status=400)
    required = ("description", "action", "trigger_type")
    for k in required:
        if not body.get(k):
            return _json_response({"ok": False, "error": f"missing field: {k}"}, status=400)
    try:
        from l3_node.autonomy.intent_persister import get_intent_persister
        intent = get_intent_persister().create(
            description=str(body["description"]),
            action=str(body["action"]),
            trigger_type=str(body["trigger_type"]),
            cron=body.get("cron"),
            event=body.get("event"),
            condition=body.get("condition"),
            interval_sec=body.get("interval_sec"),
            failure_notification_channel=body.get("failure_notification_channel"),
        )
        return _json_response({"ok": True, "intent": intent.to_dict()}, status=201)
    except Exception as e:
        logger.warning("[L3 HTTP] autonomy/intents POST: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_autonomy_intent_patch(request) -> "aiohttp.web.Response":
    """PATCH /api/v1/autonomy/intents/{intent_id} — 启用/禁用意图（**Z**）。"""
    bad = _registry_diag_auth_failure(request)
    if bad is not None:
        return bad
    intent_id = request.match_info.get("intent_id", "")
    try:
        body = await request.json()
    except Exception:
        return _json_response({"ok": False, "error": "invalid JSON"}, status=400)
    if "enabled" not in body:
        return _json_response({"ok": False, "error": "missing field: enabled"}, status=400)
    try:
        from l3_node.autonomy.intent_persister import get_intent_persister
        ok = get_intent_persister().set_enabled(intent_id, bool(body["enabled"]))
        if not ok:
            return _json_response({"ok": False, "error": "intent not found"}, status=404)
        return _json_response({"ok": True, "intent_id": intent_id, "enabled": bool(body["enabled"])})
    except Exception as e:
        logger.warning("[L3 HTTP] autonomy/intents PATCH: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_autonomy_intent_delete(request) -> "aiohttp.web.Response":
    """DELETE /api/v1/autonomy/intents/{intent_id} — 删除持久化意图（**Z**）。"""
    bad = _registry_diag_auth_failure(request)
    if bad is not None:
        return bad
    intent_id = request.match_info.get("intent_id", "")
    try:
        from l3_node.autonomy.intent_persister import get_intent_persister
        ok = get_intent_persister().delete(intent_id)
        if not ok:
            return _json_response({"ok": False, "error": "intent not found"}, status=404)
        return _json_response({"ok": True, "intent_id": intent_id, "deleted": True})
    except Exception as e:
        logger.warning("[L3 HTTP] autonomy/intents DELETE: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


async def _handle_autonomy_status_get(request) -> "aiohttp.web.Response":
    """GET /api/v1/autonomy/status — 可观测性面板 JSON（**AD**）。"""
    bad = _registry_diag_auth_failure(request)
    if bad is not None:
        return bad
    try:
        from l3_node.autonomy.dashboard import build_autonomy_status_dict

        body = build_autonomy_status_dict()
        return _json_response({"ok": True, **body}, status=200)
    except Exception as e:
        logger.warning("[L3 HTTP] autonomy/status GET: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


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


def _is_loopback_http_peer(request) -> bool:
    remote = (getattr(request, "remote", None) or "").strip()
    return remote in ("127.0.0.1", "::1", "localhost")


def _pmo_mcp_delegate_request(request) -> bool:
    try:
        from l3_node.pmo_mcp_delegate import PMO_DELEGATE_HEADER

        return (request.headers.get(PMO_DELEGATE_HEADER) or "").strip() == "1"
    except Exception:
        return False


async def _handle_mcp_tools_list(request) -> "aiohttp.web.Response":
    """GET /api/v3/mcp/tools — 供 PMO 子进程拉取本机常驻 L3 已挂载的 MCP 工具表（仅 loopback）。"""
    if not _is_loopback_http_peer(request) or not _pmo_mcp_delegate_request(request):
        return _json_response({"ok": False, "error": "forbidden"}, status=403)
    try:
        from l3_node.primitives.mcp.registry import get_mcp_registry

        reg = get_mcp_registry()
        tools = await reg.fetch_tools_from_l2()
        return _json_response({"ok": True, "tools": tools, "count": len(tools)})
    except Exception as e:
        logger.warning("[L3 HTTP] mcp/tools 失败: %s", e)
        return _json_response({"ok": False, "error": str(e)}, status=500)


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
    ) or (_is_loopback_http_peer(request) and _pmo_mcp_delegate_request(request))
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


async def _http_agent_run_via_siq(
    session_key: str,
    user_input: str,
    invoke_coro_factory,
) -> tuple[str, str]:
    """
    经 SessionInstructionQueue 执行 HTTP 会话指令。
    返回 (answer, siq_status)。
    """
    from l3_node.session_instruction_queue import (
        SIQInstruction,
        siq_enabled,
        submit_instruction,
        _instruction_timeout,
    )

    if not siq_enabled():
        return (await invoke_coro_factory(user_input), "disabled")

    done = asyncio.Event()
    holder: dict[str, str] = {"answer": ""}

    async def execute_fn(instr: SIQInstruction) -> str:
        ans = await invoke_coro_factory(instr.intent)
        holder["answer"] = ans or ""
        done.set()
        return holder["answer"]

    status = await submit_instruction(
        session_key,
        user_input,
        execute_fn,
        metadata={"channel": "http_agent_run"},
    )
    if status == "disabled":
        return (await invoke_coro_factory(user_input), "disabled")
    if status == "rejected":
        return ("当前会话指令队列已满，请稍后再试。", "rejected")
    try:
        await asyncio.wait_for(done.wait(), timeout=_instruction_timeout() + 5.0)
    except asyncio.TimeoutError:
        return ("处理超时，请稍后重试。", f"{status}_timeout")
    return (holder["answer"], status)


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
        _voice_companion = bool(_isig and _isig.get("desktop_companion"))
        if _voice_companion:
            _voice_fact = _extract_voice_explicit_memory(user_input)
            if _voice_fact:
                await _commit_voice_explicit_memory(
                    fact=_voice_fact,
                    chat_id=_ch_s,
                    source=str((_isig or {}).get("source") or "desktop_voice_companion"),
                    original_text=user_input,
                )
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
        _session_messages: list[dict[str, Any]] | None = None

        async def _load_session_for_run() -> None:
            nonlocal _session_messages
            if not _ch_s:
                _session_messages = None
                return
            try:
                from l3_node.lark_session import load_lark_session

                _session_messages = await asyncio.to_thread(load_lark_session, _ch_s)
            except Exception as e:
                logger.debug("[L3 HTTP] load session skipped chat_id=%s: %s", _ch_s[:20], e)
                _session_messages = []
            if _voice_companion and _session_messages is not None:
                _ensure_voice_session_guard(_session_messages)

        async def _save_session_after_run() -> None:
            if not _ch_s or _session_messages is None:
                return
            try:
                from l3_node.lark_session import save_lark_session

                await asyncio.to_thread(save_lark_session, _ch_s, _session_messages)
                logger.debug("[L3 HTTP] chat_id=%s saved session %d messages", _ch_s[:20], len(_session_messages))
            except Exception as e:
                logger.debug("[L3 HTTP] save session skipped chat_id=%s: %s", _ch_s[:20], e)

        async def _invoke_agent() -> str:
            return await run_agent(
                user_input,
                engine,
                max_iterations=_mi,
                _session_messages=_session_messages,
                implicit_signals=_isig,
                implicit_attribution=_iatt,
                attachments_metadata=_att_meta,
                gateway_system_state=_gw_st,
                gateway_clarification_handle=_gw_ch,
                gateway_clarification_deadline_ts=_gw_dl,
                gateway_workspace_dir=_sniff_ws,
            )

        _siq_status = ""
        _use_siq = False
        try:
            from l3_node.session_instruction_queue import siq_enabled as _siq_on

            _use_siq = bool(_ch_s and _siq_on())
        except ImportError:
            _use_siq = False

        if _use_siq:
            await _load_session_for_run()
            answer, _siq_status = await _http_agent_run_via_siq(_ch_s, user_input, _invoke_agent)
            await _save_session_after_run()
        elif _ch_s:
            try:
                if await _http_agent_session_lock_held(_ch_s):
                    from l3_node.session_hot_user_inject import record_pending_session_user_text

                    record_pending_session_user_text(_ch_s, user_input)
            except Exception:
                pass
            _lk = await _http_agent_session_lock(_ch_s)
            async with _lk:
                await _load_session_for_run()
                answer = await _invoke_agent()
                await _save_session_after_run()
        else:
            answer = await _invoke_agent()
        resp = {"answer": answer or ""}
        if _siq_status:
            resp["siq_status"] = _siq_status
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

    try:
        from l3_node.lark_test_schedule import ensure_test_schedule_scheduler_started

        ensure_test_schedule_scheduler_started()
    except Exception as e:
        logger.debug("[L3 HTTP] test-skill scheduler 启动跳过: %s", e)

    try:
        from l3_node.deferred_task_scheduler import ensure_deferred_scheduler_started

        ensure_deferred_scheduler_started()
    except Exception as e:
        logger.debug("[L3 HTTP] deferred-task scheduler 启动跳过: %s", e)

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
        r.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, X-Jachin-Safety-Lock-Token, X-Jachin-Cron-Thinker-Token, "
            "X-Jachin-Registry-Token, X-Jachin-Hook-Events-Token, X-Jachin-Registry-Diag-Token"
        )
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
    app.router.add_get("/api/v1/k11-game-open-smoke/stream", _handle_k11_game_open_smoke_stream)
    app.router.add_get(
        "/api/v1/k11-tongits-autoplay-smoke/stream", _handle_k11_tongits_autoplay_smoke_stream
    )
    app.router.add_get("/api/v1/k11-unified-smoke/schedule/status", _handle_k11_unified_smoke_schedule_status)
    app.router.add_get("/api/v1/k11-unified-smoke/schedule/log-stream", _handle_k11_unified_smoke_schedule_log_stream)
    app.router.add_post("/api/v1/k11-unified-smoke/schedule/toggle", _handle_k11_unified_smoke_schedule_toggle)
    app.router.add_post("/api/v1/k11-unified-smoke/stop", _handle_k11_unified_smoke_stop)
    try:
        from l3_node.gameqa_http import register_gameqa_routes

        register_gameqa_routes(app)
    except Exception as e:
        logger.warning("[L3 HTTP] GameQA routes skipped: %s", e)
    try:
        from l3_node.bi_console_http import register_bi_console_routes

        register_bi_console_routes(app)
    except Exception as e:
        logger.warning("[L3 HTTP] BI console routes skipped: %s", e)
    try:
        from l3_node.pmo_webhook_receiver import register_pmo_webhook_routes

        register_pmo_webhook_routes(app)
    except Exception as e:
        logger.warning("[L3 HTTP] PMO webhook routes skipped: %s", e)
    app.router.add_post(
        "/api/v1/cron-thinker/ingest-release-announcement", _handle_cron_thinker_ingest_release
    )
    app.router.add_get(
        "/api/v1/cron-thinker/release-smoke-status", _handle_cron_thinker_release_smoke_status
    )
    app.router.add_get("/api/v1/cron-thinker/bios-settings", _handle_cron_thinker_bios_settings_get)
    app.router.add_post("/api/v1/cron-thinker/bios-settings", _handle_cron_thinker_bios_settings_post)
    app.router.add_post("/api/v1/registry/external-sched-hint", _handle_registry_external_sched_hint_post)
    app.router.add_delete("/api/v1/registry/external-sched-hint", _handle_registry_external_sched_hint_delete)
    app.router.add_get("/api/v1/registry/hook-events-recent", _handle_hook_events_recent_get)
    app.router.add_get("/api/v1/registry/runtime-snapshot", _handle_registry_runtime_snapshot_get)
    app.router.add_get("/api/v1/registry/global-tasks", _handle_registry_global_tasks_get)
    app.router.add_get("/api/v1/registry/siq-sessions", _handle_registry_siq_sessions_get)
    app.router.add_get("/api/v1/registry/external-scheduled-hints", _handle_registry_external_scheduled_hints_get)
    app.router.add_get("/api/v1/registry/task-dag-active", _handle_registry_task_dag_active_get)
    app.router.add_get("/api/v1/registry/dag-guardrails", _handle_registry_dag_guardrails_get)
    app.router.add_get("/api/v1/registry/hook-replay", _handle_registry_hook_replay_get)
    app.router.add_post("/api/v1/registry/hook-replay", _handle_registry_hook_replay_post)
    app.router.add_post("/api/v1/registry/preempt-cancel", _handle_registry_preempt_cancel_post)
    app.router.add_post("/api/v1/registry/dag-resume", _handle_registry_dag_resume_post)
    app.router.add_post("/api/v1/registry/dag-handoff/export", _handle_registry_dag_handoff_export_post)
    app.router.add_post("/api/v1/registry/dag-handoff/import", _handle_registry_dag_handoff_import_post)
    app.router.add_get("/api/v1/registry/dag-handoff/list", _handle_registry_dag_handoff_list_get)
    app.router.add_post("/api/v1/registry/dag-handoff/auto-transfer", _handle_dag_handoff_auto_transfer_post)
    # AS — DAG Coordinator 端点
    app.router.add_get("/api/v1/registry/coordinator/info", _handle_coordinator_info_get)
    app.router.add_get("/api/v1/registry/coordinator/peers", _handle_coordinator_peers_get)
    app.router.add_post("/api/v1/registry/coordinator/register", _handle_coordinator_register_post)
    app.router.add_post("/api/v1/registry/coordinator/dag-claim", _handle_coordinator_dag_claim_post)
    app.router.add_delete("/api/v1/registry/coordinator/dag-claim/{dag_id}", _handle_coordinator_dag_release_delete)
    app.router.add_get("/api/v1/registry/coordinator/dag-locks", _handle_coordinator_dag_locks_get)
    app.router.add_get("/api/v1/registry/im-channel-pending", _handle_registry_im_channel_pending_get)
    app.router.add_get("/api/v1/autonomy/status", _handle_autonomy_status_get)
    app.router.add_post("/api/v1/autonomy/intents", _handle_autonomy_intents_post)
    app.router.add_patch("/api/v1/autonomy/intents/{intent_id}", _handle_autonomy_intent_patch)
    app.router.add_delete("/api/v1/autonomy/intents/{intent_id}", _handle_autonomy_intent_delete)
    app.router.add_post("/api/v3/skills/{skill_id}/execute/stream", _handle_skills_execute_stream)
    app.router.add_get("/api/v3/mcp/tools", _handle_mcp_tools_list)
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

    async def _on_startup_register_k11_schedule_sse_loop(_app):
        """绑定主 asyncio 循环，供 cron_thinker 等线程向 MIND STREAM / schedule SSE 推流。"""
        try:
            from l3_node.jobs.k11_unified_smoke_scheduler import register_k11_schedule_log_loop

            register_k11_schedule_log_loop(asyncio.get_running_loop())
        except Exception as e:
            logger.warning("[L3 HTTP] register_k11_schedule_log_loop skipped: %s", e)

    async def _on_startup_kalaroko_scheduler(app):
        """L3 重启后恢复 Kalaroko 定时巡检（状态见 ~/.jachin/data/kalaroko_scheduler_state.json）。"""
        try:
            from l3_node.jobs.kalaroko_scheduler import init_auto_start_scheduler

            init_auto_start_scheduler()
        except Exception as e:
            logger.warning("[L3 HTTP] Kalaroko scheduler auto-start skipped: %s", e)

    async def _on_startup_k11_unified_smoke_scheduler(_app):
        """L3 重启后恢复 K11 统合冒烟「每日北京固定点」调度。"""
        try:
            from l3_node.jobs.k11_unified_smoke_scheduler import init_k11_unified_smoke_auto_start

            init_k11_unified_smoke_auto_start()
        except Exception as e:
            logger.warning("[L3 HTTP] K11 unified smoke scheduler auto-start skipped: %s", e)

    async def _on_startup_healthchecks_watchdog(_app):
        """Healthchecks.io 周期 ping（独立线程，不阻塞 asyncio）。"""
        try:
            from l3_node.jobs.healthchecks_watchdog import start_healthchecks_watchdog

            start_healthchecks_watchdog()
        except Exception as e:
            logger.warning("[L3 HTTP] Healthchecks watchdog skipped: %s", e)

    async def _on_startup_cron_thinker(_app):
        """发版公告 → 次日统合冒烟：BackgroundScheduler + 持久化恢复。"""
        try:
            from core.cron_thinker import start_cron_thinker_daemon

            start_cron_thinker_daemon()
        except Exception as e:
            logger.warning("[L3 HTTP] cron_thinker daemon skipped: %s", e)

    async def _on_startup_dag_coordinator(_app):
        """AS — DAG Coordinator 心跳循环（JACHIN_COORDINATOR_ENABLE=1 时激活）。"""
        try:
            from l3_node.task_engine.dag_coordinator import ensure_coordinator_started
            ensure_coordinator_started(asyncio.get_running_loop())
        except Exception as e:
            logger.warning("[L3 HTTP] DAG Coordinator startup skipped: %s", e)

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

    async def _on_startup_pmo_bitable_watch(_app):
        """PMO 多维表变更监控：轮询 + 60s 防抖回调。"""
        try:
            from l3_node.jobs.pmo_bitable_watch_scheduler import init_pmo_bitable_watch_auto_start

            init_pmo_bitable_watch_auto_start()
        except Exception as e:
            logger.warning("[L3 HTTP] PMO bitable watch scheduler skipped: %s", e)

    async def _on_startup_bi_console_scheduler(_app):
        """L3 重启后恢复 BI 控制台定时（~/.jachin/data/bi_console_scheduler_state.json）。"""
        try:
            from l3_node.jobs.bi_console_scheduler import (
                init_bi_console_auto_start,
                register_bi_console_schedule_log_loop,
            )

            register_bi_console_schedule_log_loop(asyncio.get_running_loop())
            init_bi_console_auto_start()
        except Exception as e:
            logger.warning("[L3 HTTP] BI console scheduler auto-start skipped: %s", e)

    async def _on_startup_autonomy_services(_app):
        """启动 AutonomousAwarenessLoop（§5 Layer 2）。若禁用则静默跳过。"""
        try:
            from l3_node.bootstrap import start_autonomy_services
            start_autonomy_services()
        except Exception as e:
            logger.warning("[L3 HTTP] autonomy services startup skipped: %s", e)

    async def _on_startup_global_registry_redis(_app):
        """Redis GlobalTaskRegistry 抢占 Pub/Sub 订阅（BO 增强）。"""
        try:
            from l3_node.global_registry_redis import start_preempt_subscriber

            start_preempt_subscriber()
        except Exception as e:
            logger.warning("[L3 HTTP] global registry redis subscriber skipped: %s", e)

    app.on_startup.append(_on_startup_register_k11_schedule_sse_loop)
    app.on_startup.append(_on_startup_kalaroko_scheduler)
    app.on_startup.append(_on_startup_k11_unified_smoke_scheduler)
    app.on_startup.append(_on_startup_healthchecks_watchdog)
    app.on_startup.append(_on_startup_cron_thinker)
    app.on_startup.append(_on_startup_dag_coordinator)
    app.on_startup.append(_on_startup_skill_matrix_sync)
    app.on_startup.append(_on_startup_pmo_bitable_watch)
    app.on_startup.append(_on_startup_bi_console_scheduler)
    app.on_startup.append(_on_startup_autonomy_services)
    app.on_startup.append(_on_startup_global_registry_redis)

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

            try:
                from l3_node.runtime_diag_log import log_runtime_milestone, start_runtime_diag_loop

                log_runtime_milestone(f"HTTP API listening http://{host}:{try_port}")
                asyncio.create_task(start_runtime_diag_loop(), name="jachin-l3-runtime-diag")
            except Exception as e:
                logger.debug("[L3 HTTP] runtime_diag 启动跳过: %s", e)

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
