"""
Jachin Nexus V2 - L3 MCP 工具桥接器

从 L2 拉取 MCP 工具列表，维护 known_mcp_tools 避免与本地 Wasm 重名冲突，
提供 OpenAI/Anthropic 标准 tools 格式，供大模型使用。

read_file（含 PDF 提取）已下放 L3 本地执行，不依赖 L2。
"""
from __future__ import annotations

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
]


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
        local_names = {"read_file"}

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
        logger.info("[MCP Registry] 已合并 %d 个 MCP 工具（含 L3 本地 read_file）", len(tools))
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

    async def invoke(self, tool_id: str, action_input: str, *, timeout: float = 30.0) -> str:
        """
        执行 MCP 工具。L3 本地工具（如 read_file）直接执行，其余走 L2。
        """
        if tool_id in self._local_mcp_tools:
            raw_name = self._raw_name(tool_id)
            if raw_name == "read_file":
                arguments = {}
                inp = (action_input or "").strip()
                if inp:
                    if inp.strip().startswith("{") and "}" in inp:
                        try:
                            arguments = json.loads(inp)
                            if not isinstance(arguments, dict):
                                arguments = {"path": inp}
                        except json.JSONDecodeError:
                            arguments = {"path": inp}
                    else:
                        arguments = {"path": inp}
                path_val = arguments.get("path", arguments.get("input", inp))
                return _invoke_read_file_local(str(path_val) if path_val else "")
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
