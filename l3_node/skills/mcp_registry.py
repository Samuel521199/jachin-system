"""
Jachin Nexus V2 - L3 MCP 工具桥接器

从 L2 拉取 MCP 工具列表，维护 known_mcp_tools 避免与本地 Wasm 重名冲突，
提供 OpenAI/Anthropic 标准 tools 格式，供大模型使用。

read_file、atom_post_job_boss、atom_greet_recommend_boss 已下放 L3 本地执行，不依赖 L2。
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 默认 L2 地址（可从 l2_gateway_config.json 读取）
DEFAULT_L2_BASE_URL = "http://localhost:18888"
MCP_TOOLS_PREFIX = "mcp:"

# L3 本地 MCP 工具（不依赖 L2，下放至 L3 执行）
L3_LOCAL_MCP_TOOLS: list[dict[str, Any]] = [
    {
        "id": "mcp:read_file",
        "label": "mcp:read_file",
        "desc": "[L3 本地] 读取文件内容。支持 .md/.txt 及 .pdf 文本提取。路径需在 workspace、client_volumes、data/hr_resumes、config/hr_jds 下。",
        "params": ["path"],
    },
    {
        "id": "mcp:atom_post_job_boss",
        "label": "mcp:atom_post_job_boss",
        "desc": "[L3 本地] 发布职位(publish_job)。在 Boss 直聘自动填写并发布。可传 jd_config(JSON)或 jd_config_path。必含 job_title,jd_full,recruitment_type,experience,education,salary_min,salary_max。",
        "params": ["cdp_url", "jd_config_path", "jd_config"],
    },
    {
        "id": "mcp:atom_greet_recommend_boss",
        "label": "mcp:atom_greet_recommend_boss",
        "desc": "[L3 本地] 在推荐牛人页面自动筛选并打招呼：读 JD → 遍历卡片 → 跳过已沟通 → 初筛 → 打招呼，最多2人。需 Chrome 调试模式。",
        "params": ["cdp_url", "jd_config_path"],
    },
    {
        "id": "mcp:add_automated_recruitment_task",
        "label": "mcp:add_automated_recruitment_task",
        "desc": "[L3 本地] 将岗位加入无人值守招聘调度引擎。每1分钟抓取简历、每2分钟推荐牛人打招呼、每1分钟检查。analyze_threshold 默认1（抓取1份即分析），分析完成后自动结束调度。",
        "params": ["job_name", "analyze_threshold", "analyze_interval_hours", "jd_config_path"],
    },
]


def _invoke_atom_post_job_boss_local(
    cdp_url: str = "",
    jd_config_path: str = "",
    jd_config: str | dict | None = None,
) -> str:
    """L3 本地执行 atom_post_job_boss。若传 jd_config(JSON)，先写入 jd_to_publish.json 再发布。"""
    _proj = Path(__file__).resolve().parent.parent.parent
    plugin_root = _proj / "skills_repo" / "plugin" / "2-track-a-atomic-mcp"
    default_jd_path = plugin_root.parent / "data" / "jd_to_publish.json"
    if not plugin_root.exists():
        return json.dumps({"success": False, "posted": False, "error": f"plugin 路径不存在: {plugin_root}"}, ensure_ascii=False)
    # 若传入 jd_config，先写入文件
    if jd_config:
        cfg = jd_config
        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg)
            except json.JSONDecodeError:
                return json.dumps({"success": False, "posted": False, "error": "jd_config 不是有效 JSON"}, ensure_ascii=False)
        if isinstance(cfg, dict) and (cfg.get("job_title") or cfg.get("jd_full")):
            # 规范化薪资字段，确保 salary_min/salary_max 为整数（支持 salary_range、字符串等）
            _sal_min, _sal_max = cfg.get("salary_min"), cfg.get("salary_max")
            if _sal_min is None or _sal_max is None:
                import re
                _sr = cfg.get("salary_range", "")
                if _sr:
                    _m = re.search(r"(\d+)[^\d]*(\d+)?", str(_sr))
                    if _m:
                        _sal_min = int(_m.group(1))
                        _sal_max = int(_m.group(2)) if _m.group(2) else _sal_min
            if _sal_min is not None:
                cfg["salary_min"] = int(_sal_min)
            if _sal_max is not None:
                cfg["salary_max"] = int(_sal_max)
            default_jd_path.parent.mkdir(parents=True, exist_ok=True)
            default_jd_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            jd_config_path = str(default_jd_path)
    import sys
    if str(plugin_root) not in sys.path:
        sys.path.insert(0, str(plugin_root))
    try:
        from tools.atom_post_job_boss import atom_post_job_boss
        result = atom_post_job_boss(
            cdp_url=cdp_url or "http://127.0.0.1:9222",
            jd_config_path=jd_config_path or str(default_jd_path),
        )
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.warning("[MCP Registry] atom_post_job_boss 本地执行失败: %s", e)
        return json.dumps({"success": False, "posted": False, "error": str(e)}, ensure_ascii=False)


def _invoke_atom_greet_recommend_boss_local(cdp_url: str = "", jd_config_path: str = "") -> str:
    """L3 本地执行 atom_greet_recommend_boss，直接调用 plugin 工具。"""
    _proj = Path(__file__).resolve().parent.parent.parent
    plugin_root = _proj / "skills_repo" / "plugin" / "2-track-a-atomic-mcp"
    if not plugin_root.exists():
        return json.dumps({"success": False, "greeted_count": 0, "error": f"plugin 路径不存在: {plugin_root}"}, ensure_ascii=False)
    import sys
    if str(plugin_root) not in sys.path:
        sys.path.insert(0, str(plugin_root))
    try:
        from tools.atom_greet_recommend_boss import atom_greet_recommend_boss
        result = atom_greet_recommend_boss(
            cdp_url=cdp_url or "http://127.0.0.1:9222",
            jd_config_path=jd_config_path or "",
        )
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.warning("[MCP Registry] atom_greet_recommend_boss 本地执行失败: %s", e)
        return json.dumps({"success": False, "greeted_count": 0, "error": str(e)}, ensure_ascii=False)


def _invoke_add_automated_recruitment_task_local(
    job_name: str = "",
    analyze_threshold: int = 1,
    analyze_interval_hours: float = 0.05,
    jd_config_path: str = "",
) -> str:
    """L3 本地执行 add_automated_recruitment_task，向调度器添加岗位。抓取1份即分析，分析完成后结束调度。"""
    if not (job_name or "").strip():
        return json.dumps({"ok": False, "error": "job_name 不能为空"}, ensure_ascii=False)
    _proj = Path(__file__).resolve().parent.parent.parent
    default_jd_path = str(_proj / "skills_repo" / "plugin" / "data" / "jd_to_publish.json")
    job_config = {
        "job_name": (job_name or "").strip(),
        "analyze_threshold": int(analyze_threshold) if analyze_threshold is not None else 1,
        "analyze_interval_hours": float(analyze_interval_hours) if analyze_interval_hours is not None else 0.05,
        "jd_config_path": (jd_config_path or "").strip() or default_jd_path,
        "cdp_url": "http://127.0.0.1:9222",
        "max_count": 50,
        "filter_tab": "全部",
        "request_resume": True,
    }
    try:
        from l3_node.recruitment_scheduler import add_scheduled_job
        result = add_scheduled_job(job_config)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.warning("[MCP Registry] add_automated_recruitment_task 失败: %s", e)
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


def _invoke_read_file_local(path_raw: str) -> str:
    """L3 本地执行 read_file，使用 core.pdf_extractor。"""
    from core.pdf_extractor import extract_pdf_text, SCAN_PLACEHOLDER
    _proj = Path(__file__).resolve().parent.parent.parent
    _l3_vol = Path.home() / ".jachin" / "client_volumes"
    raw = (path_raw or "").strip().replace("\\", "/")
    if not raw or "\n" in raw or len(raw) > 1200:
        return "[read_file] 路径无效"
    p = Path(raw)
    if p.is_absolute() and not p.exists() and "/" in raw and "\\" not in raw:
        p_alt = Path(raw.replace("/", "\\"))
        if p_alt.exists():
            p = p_alt
    path_obj = None
    if p.is_absolute() and p.exists():
        path_obj = p.resolve()
    else:
        raw_norm = raw.lstrip("/")
        for base, sub in [
            (_l3_vol, raw_norm),
            (_proj / "data" / "hr_resumes", p.name or raw_norm),
            (_proj / "config" / "hr_jds", p.name or raw_norm),
        ]:
            cand = (base / sub).resolve()
            if cand.exists() and cand.is_file():
                path_obj = cand
                break
    if not path_obj or not path_obj.exists():
        return f"[read_file] 路径无效或越界: {path_raw[:100]}"
    try:
        if path_obj.suffix.lower() == ".pdf":
            content = extract_pdf_text(path_obj)
            if not content.strip():
                return SCAN_PLACEHOLDER
            return content
        return path_obj.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.warning("[MCP Registry] read_file 本地执行失败 path=%s err=%s", path_obj, e)
        return f"[read_file] 读取失败: {e}"


def _get_l2_base_url() -> str:
    """从 l2_gateway_config.json 读取 L2 地址。"""
    cfg_path = Path.home() / ".jachin" / "l2_gateway_config.json"
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            base = (data.get("l2_base_url") or "").rstrip("/")
            if base:
                return base
        except Exception:
            pass
    return DEFAULT_L2_BASE_URL


class MCPToolRegistry:
    """
    MCP 工具桥接器：从 L2 拉取工具、维护路由、格式化为 LLM 可用的 schema。
    """

    def __init__(self, l2_base_url: Optional[str] = None) -> None:
        self._l2_base_url = (l2_base_url or _get_l2_base_url()).rstrip("/")
        self._known_mcp_tools: set[str] = set()
        self._tools_cache: list[dict[str, Any]] = []
        self._local_mcp_tools: set[str] = {t["id"] for t in L3_LOCAL_MCP_TOOLS}

    @property
    def known_mcp_tools(self) -> set[str]:
        """MCP 工具名集合（含 L3 本地 + L2 拉取），避免与本地 Wasm 重名。"""
        return self._known_mcp_tools.copy()

    def _mcp_id(self, name: str) -> str:
        """为 MCP 工具添加前缀，避免与 core:、jpp: 冲突。"""
        name = (name or "").strip()
        if not name:
            return ""
        if name.startswith(MCP_TOOLS_PREFIX):
            return name
        return f"{MCP_TOOLS_PREFIX}{name}"

    def _raw_name(self, mcp_id: str) -> str:
        """去掉 mcp: 前缀，得到 L2 期望的原始工具名。"""
        s = (mcp_id or "").strip()
        if s.startswith(MCP_TOOLS_PREFIX):
            return s[len(MCP_TOOLS_PREFIX) :]
        return s

    async def fetch_tools_from_l2(self) -> list[dict[str, Any]]:
        """
        获取 MCP 工具列表。L3 本地 read_file 优先注入，L2 工具合并（read_file 已下放 L3，不重复）。
        Returns:
            合并后的工具列表，格式与 load_tools 一致：{id, label, desc, params}
        """
        import httpx

        tools: list[dict[str, Any]] = list(L3_LOCAL_MCP_TOOLS)
        self._known_mcp_tools = set(self._local_mcp_tools)
        local_names = {"read_file", "atom_post_job_boss", "atom_greet_recommend_boss", "add_automated_recruitment_task"}

        url = f"{self._l2_base_url}/api/v2/mcp/tools"
        logger.info("[MCP Registry] 从 L2 拉取工具 url=%s", url)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
        except httpx.TimeoutException as e:
            logger.warning("[MCP Registry] L2 请求超时 url=%s err=%s", url, e)
            self._tools_cache = tools
            logger.info("[MCP Registry] 使用 L3 本地工具 %d 个（L2 不可用）", len(tools))
            return tools
        except httpx.HTTPStatusError as e:
            logger.warning("[MCP Registry] L2 返回错误 url=%s status=%s", url, e.response.status_code)
            self._tools_cache = tools
            return tools
        except Exception as e:
            logger.warning("[MCP Registry] 拉取工具失败 url=%s err=%s", url, e)
            self._tools_cache = tools
            return tools

        raw_tools = data.get("tools", [])
        for t in raw_tools:
            name = t.get("name", "").strip()
            if not name or name in local_names:
                continue
            mcp_id = self._mcp_id(name)
            self._known_mcp_tools.add(mcp_id)
            params: list[str] = []
            schema = t.get("inputSchema") or {}
            if isinstance(schema, dict):
                props = schema.get("properties") or {}
                params = list(props.keys()) if props else ["input"]
            desc = t.get("description") or name
            tools.append({
                "id": mcp_id,
                "label": mcp_id,
                "desc": f"[L2 MCP] {desc}",
                "params": params,
            })
        self._tools_cache = tools
        logger.info("[MCP Registry] 已合并 %d 个 MCP 工具（含 L3 本地 read_file、atom_post_job_boss、atom_greet_recommend_boss、add_automated_recruitment_task）", len(tools))
        return tools

    def to_openai_tools_schema(self, tools: Optional[list[dict[str, Any]]] = None) -> list[dict[str, Any]]:
        """
        将工具列表格式化为 OpenAI/Anthropic 标准的 tools JSON Schema 数组。
        供 LiteLLM 等传递 function calling 使用。
        """
        lst = tools or self._tools_cache
        result = []
        for t in lst:
            name = t.get("id", t.get("label", ""))
            desc = t.get("desc", t.get("description", ""))
            params = t.get("params", ["input"])
            schema = {"type": "object", "properties": {}}
            for p in params:
                schema["properties"][p] = {"type": "string", "description": p}
            if params:
                schema["required"] = params[:1]
            result.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": schema,
                },
            })
        return result

    def get_tools_for_prompt(self) -> list[dict[str, Any]]:
        """
        返回当前缓存的 MCP 工具列表（与 load_tools 格式一致）。
        若未拉取过则返回空。
        """
        return list(self._tools_cache)

    def _parse_action_input(self, action_input: str) -> dict[str, Any]:
        """解析 action_input 为 arguments 字典。支持提取内嵌 JSON、去除前后噪音。"""
        arguments: dict[str, Any] = {}
        inp = (action_input or "").strip()
        if not inp:
            return arguments
        # 去除常见前缀（LLM 可能附带）
        for prefix in ("Action Input:", "Action Input：", "input:", "参数:"):
            if inp.lower().startswith(prefix.lower()):
                inp = inp[len(prefix):].strip()
        # 去除 ```json 等代码块包裹（LLM 可能输出 JSON 代码块）
        if "```" in inp:
            import re as _re
            _m = _re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", inp)
            if _m:
                inp = _m.group(1).strip()
        if inp.strip().startswith("{") and "}" in inp:
            try:
                arguments = json.loads(inp)
                if not isinstance(arguments, dict):
                    arguments = {"input": inp}
            except json.JSONDecodeError:
                # 尝试提取第一个完整 JSON 对象
                start = inp.find("{")
                if start >= 0:
                    depth, end = 0, start
                    for i, c in enumerate(inp[start:], start):
                        if c == "{":
                            depth += 1
                        elif c == "}":
                            depth -= 1
                            if depth == 0:
                                end = i
                                break
                    try:
                        arguments = json.loads(inp[start : end + 1])
                        if isinstance(arguments, dict):
                            pass
                        else:
                            arguments = {"input": inp}
                    except json.JSONDecodeError:
                        arguments = {"input": inp}
                else:
                    arguments = {"input": inp}
        else:
            arguments = {"input": inp}
        return arguments

    async def invoke(self, tool_id: str, action_input: str, *, timeout: float = 30.0) -> str:
        """
        执行 MCP 工具。L3 本地工具（read_file、atom_post_job_boss、atom_greet_recommend_boss）直接执行，其余走 L2。
        """
        if tool_id in self._local_mcp_tools:
            raw_name = self._raw_name(tool_id)
            arguments = self._parse_action_input(action_input)

            if raw_name == "read_file":
                path_val = arguments.get("path", arguments.get("input", ""))
                return _invoke_read_file_local(str(path_val) if path_val else "")

            if raw_name == "atom_post_job_boss":
                cdp_url = arguments.get("cdp_url", "http://127.0.0.1:9222")
                jd_config_path = arguments.get("jd_config_path", "")
                jd_config = arguments.get("jd_config")
                # 兼容：LLM 可能直接传顶层 JSON 即 JD 配置（含 job_title、jd_full 等）
                if jd_config is None and (arguments.get("job_title") or arguments.get("jd_full")):
                    jd_config = arguments
                # 兼容：input 字段为 JSON 字符串
                if jd_config is None:
                    raw_inp = arguments.get("input", "")
                    if isinstance(raw_inp, str) and raw_inp.strip().startswith("{"):
                        try:
                            parsed = json.loads(raw_inp)
                            if isinstance(parsed, dict) and (parsed.get("job_title") or parsed.get("jd_full")):
                                jd_config = parsed
                        except json.JSONDecodeError:
                            pass
                if isinstance(jd_config, dict):
                    jd_config = json.dumps(jd_config, ensure_ascii=False)
                logger.info("[MCP Registry] L3 本地执行 atom_post_job_boss cdp=%s jd_config=%s", cdp_url, "有" if jd_config else "无")
                return await asyncio.to_thread(
                    _invoke_atom_post_job_boss_local,
                    str(cdp_url) if cdp_url else "http://127.0.0.1:9222",
                    str(jd_config_path) if jd_config_path else "",
                    str(jd_config) if jd_config else None,
                )

            if raw_name == "atom_greet_recommend_boss":
                cdp_url = arguments.get("cdp_url", "http://127.0.0.1:9222")
                jd_config_path = arguments.get("jd_config_path", "")
                return await asyncio.to_thread(
                    _invoke_atom_greet_recommend_boss_local,
                    str(cdp_url) if cdp_url else "http://127.0.0.1:9222",
                    str(jd_config_path) if jd_config_path else "",
                )

            if raw_name == "add_automated_recruitment_task":
                job_name = arguments.get("job_name", "")
                if not (job_name or "").strip():
                    jd_cfg = arguments.get("jd_config", {})
                    if isinstance(jd_cfg, dict) and jd_cfg.get("job_title"):
                        job_name = str(jd_cfg["job_title"]).strip()
                # 兜底：职位发布后 LLM 可能未传 job_name（如强制校验触发的二次调用），从 jd_to_publish.json 读取
                if not (job_name or "").strip():
                    _proj = Path(__file__).resolve().parent.parent.parent
                    jd_path = _proj / "skills_repo" / "plugin" / "data" / "jd_to_publish.json"
                    if jd_path.exists():
                        try:
                            jd_data = json.loads(jd_path.read_text(encoding="utf-8"))
                            if isinstance(jd_data, dict) and jd_data.get("job_title"):
                                job_name = str(jd_data["job_title"]).strip()
                                logger.info("[MCP Registry] add_automated_recruitment_task job_name 从 jd_to_publish.json 兜底: %s", job_name)
                        except Exception as e:
                            logger.debug("[MCP Registry] 读取 jd_to_publish.json 兜底失败: %s", e)
                analyze_threshold = arguments.get("analyze_threshold", 1)
                analyze_interval_hours = arguments.get("analyze_interval_hours", 0.05)
                jd_config_path = arguments.get("jd_config_path", "")
                logger.info("[MCP Registry] L3 本地执行 add_automated_recruitment_task job_name=%s", job_name or "(空)")
                return _invoke_add_automated_recruitment_task_local(
                    job_name=str(job_name).strip() if job_name else "",
                    analyze_threshold=int(analyze_threshold) if analyze_threshold is not None else 1,
                    analyze_interval_hours=float(analyze_interval_hours) if analyze_interval_hours is not None else 0.05,
                    jd_config_path=str(jd_config_path) if jd_config_path else "",
                )

        logger.info("[MCP Registry] 工具 %s 不在 L3 本地，转发 L2", tool_id)
        return await self.invoke_via_l2(tool_id, action_input, timeout=timeout)

    async def invoke_via_l2(
        self,
        tool_id: str,
        action_input: str,
        *,
        timeout: float = 30.0,
    ) -> str:
        """
        通过 L2 POST /api/v2/mcp/invoke 执行 MCP 工具。
        强容错：L2 宕机或超时时返回拟人化系统提示，不抛异常。
        """
        import httpx

        raw_name = self._raw_name(tool_id)
        if not raw_name:
            return "[MCP] 工具名无效"

        # 解析 action_input 为 arguments
        arguments: dict[str, Any] = {}
        inp = (action_input or "").strip()
        if inp:
            if inp.strip().startswith("{") and "}" in inp:
                try:
                    arguments = json.loads(inp)
                    if not isinstance(arguments, dict):
                        arguments = {"input": inp}
                except json.JSONDecodeError:
                    arguments = {"input": inp}
            else:
                arguments = {"input": inp}

        url = f"{self._l2_base_url}/api/v2/mcp/invoke"
        payload = {"tool_name": raw_name, "arguments": arguments}
        logger.info("[MCP Registry] 调用 L2 invoke tool=%s url=%s", raw_name, url)

        # TODO(MVP): 不传 X-Sub-Account-Id / Bearer，L2 已放宽鉴权，直接 POST 即可
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.TimeoutException as e:
            err_msg = str(e)
            logger.warning("[MCP Registry] L2 调用超时 tool=%s err=%s", raw_name, err_msg)
            return f"【系统异常】调用本地底层网关(L2)失败，请提醒用户检查网关状态。错误信息: 请求超时({timeout}秒)"
        except httpx.ConnectError as e:
            err_msg = str(e)
            logger.warning("[MCP Registry] L2 连接失败 tool=%s err=%s", raw_name, err_msg)
            return f"【系统异常】调用本地底层网关(L2)失败，请提醒用户检查网关是否已启动。错误信息: {err_msg}"
        except httpx.HTTPStatusError as e:
            err_msg = str(e)
            logger.warning("[MCP Registry] L2 返回错误 tool=%s status=%s", raw_name, e.response.status_code)
            try:
                body = e.response.json()
                detail = body.get("detail", body)
                if isinstance(detail, dict):
                    detail = detail.get("message", detail.get("detail", str(detail)))
                err_msg = str(detail) if detail else err_msg
            except Exception:
                pass
            return f"【系统异常】调用本地底层网关(L2)失败，请提醒用户检查网关状态。错误信息: {err_msg}"
        except Exception as e:
            err_msg = str(e)
            logger.exception("[MCP Registry] L2 调用异常 tool=%s err=%s", raw_name, err_msg)
            return f"【系统异常】调用本地底层网关(L2)失败，请提醒用户检查网关状态。错误信息: {err_msg}"

        result = data.get("result", "")
        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=False)
        return str(result) if result else "[无输出]"


# 全局单例
_registry: Optional[MCPToolRegistry] = None


def get_mcp_registry() -> MCPToolRegistry:
    """获取 MCP 工具桥接器单例。"""
    global _registry
    if _registry is None:
        _registry = MCPToolRegistry()
    return _registry
