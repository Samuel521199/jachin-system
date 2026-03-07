"""
L3 HTTP API - 技能列表与执行

供 Skill Matrix 等前端调用。技能执行在 L3 本地进行（~/.jachin/l3_skill_cache/）。
端口 18990 系列，与 L2(18888)、WebSocket(18981) 分离。
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("l3_node")

L3_HTTP_PORT = 18990


def _tools_to_skill_infos(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将 Wasm 技能转为 SkillInfo 格式（供 Skill Matrix 展示），同名同版本去重"""
    result = []
    seen_key: set[tuple[str, str]] = set()
    for t in tools:
        tid = t.get("id", "")
        params = t.get("params", ["input"])
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
    input_data = body.get("input_data", {})
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
        return _json_response({"success": True, "result": {"text": result}, "error": None})
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

    @aiohttp.web.middleware
    async def cors_middleware(request, handler):
        if request.method == "OPTIONS":
            r = aiohttp.web.Response()
        else:
            r = await handler(request)
        r.headers["Access-Control-Allow-Origin"] = "*"
        r.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        r.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return r

    app = aiohttp.web.Application(middlewares=[cors_middleware])
    app.router.add_get("/api/v3/skills", _handle_skills_list)
    app.router.add_post("/api/v3/skills/{skill_id}/execute", _handle_skills_execute)

    def _is_port_in_use(e: BaseException) -> bool:
        if isinstance(e, OSError):
            return getattr(e, "errno", None) in (10048, 98)
        return False

    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    last_err: BaseException | None = None
    ports_to_try = [port, 18991, 18992, 18993, 18994, 18995, 18996, 18997, 18998, 18999]
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
