"""
Jachin Nexus V2 - L3 技能加载器

扫描并加载 Native Core、JPP Wasm 插件与本地技能，转化为 LiteLLM 可用的 tools 格式。
权限死锁在 ~/.jachin/workspace/。
零信任：allowed_skills 白名单硬拦截，未在白名单内的 Skill 绝对禁止提交给 LLM。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# JPP Wasm 插件目录（相对 l3_node/skills）
_WASM_PLUGINS_DIR = Path(__file__).resolve().parent / "wasm_plugins"

# Native Core 工具定义（对标 core/native_tools.py，权限限于 ~/.jachin/workspace/）
NATIVE_TOOLS: list[dict[str, Any]] = [
    {
        "id": "core:fs_read",
        "label": "core:fs_read",
        "desc": "读取文件内容。路径必须位于 ~/.jachin/workspace/ 下。参数: file_path",
        "params": ["file_path"],
    },
    {
        "id": "core:fs_write",
        "label": "core:fs_write",
        "desc": "写入文件。路径必须位于 ~/.jachin/workspace/ 下。参数: file_path, content",
        "params": ["file_path", "content"],
    },
    {
        "id": "core:shell_exec",
        "label": "core:shell_exec",
        "desc": "执行 Shell 命令，工作目录死锁在 ~/.jachin/workspace/。参数: command, timeout(可选,默认30)",
        "params": ["command"],
    },
]


def _invoke_native(tool_id: str, **kwargs: Any) -> Any:
    """调用 Native Core 工具。"""
    try:
        from core.native_tools import dispatch_native_tool
        return dispatch_native_tool(tool_id, **kwargs)
    except ImportError:
        return _invoke_native_fallback(tool_id, **kwargs)
    except Exception as e:
        return f"[执行失败: {e}]"


def _invoke_native_fallback(tool_id: str, **kwargs: Any) -> Any:
    """L3 独立运行时的 Native 兜底实现。"""
    workspace = Path.home() / ".jachin" / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    def _assert_under(p: Path) -> None:
        if not str(p.resolve()).startswith(str(workspace.resolve())):
            raise ValueError(f"路径越界: {p} 必须在 ~/.jachin/workspace/ 下")

    if tool_id == "core:fs_read":
        fp = Path(kwargs.get("file_path", "")).expanduser()
        if not fp.is_absolute():
            fp = (workspace / fp).resolve()
        _assert_under(fp)
        return fp.read_text(encoding="utf-8", errors="replace")
    if tool_id == "core:fs_write":
        fp = Path(kwargs.get("file_path", "")).expanduser()
        if not fp.is_absolute():
            fp = (workspace / fp).resolve()
        _assert_under(fp)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(kwargs.get("content", ""), encoding="utf-8")
        return {"ok": True}
    if tool_id == "core:shell_exec":
        import subprocess
        cmd = kwargs.get("command", "")
        timeout = kwargs.get("timeout", 30)
        r = subprocess.run(
            cmd, shell=True, cwd=str(workspace),
            capture_output=True, text=True, timeout=timeout,
        )
        return {"returncode": r.returncode, "stdout": r.stdout or "", "stderr": r.stderr or ""}
    raise ValueError(f"未知工具: {tool_id}")


def _scan_wasm_plugins() -> list[dict[str, Any]]:
    """
    扫描 l3_node/skills/wasm_plugins/ 下的 JPP .wasm 插件。
    每个 .wasm 需配套 plugin.json 或 {id}.json，含 id, name, description, parameters。
    """
    tools: list[dict[str, Any]] = []
    if not _WASM_PLUGINS_DIR.exists():
        return tools
    for p in _WASM_PLUGINS_DIR.iterdir():
        if p.suffix != ".wasm":
            continue
        # 查找描述文件：plugin.json 或 {stem}.json
        desc_path = p.parent / "plugin.json"
        if not desc_path.exists():
            desc_path = p.parent / f"{p.stem}.json"
        if not desc_path.exists():
            logger.debug("[Skills] Wasm 插件 %s 无描述文件，跳过", p.name)
            continue
        try:
            desc = json.loads(desc_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("[Skills] 解析 %s 失败: %s", desc_path.name, e)
            continue
        plugin_id = desc.get("id") or p.stem
        tool_id = f"jpp:{plugin_id}"
        params = desc.get("parameters", desc.get("schema", {}).get("input", {}))
        if isinstance(params, dict):
            param_names = list(params.keys()) if params else []
        elif isinstance(params, list):
            param_names = [p.get("name", p) if isinstance(p, dict) else str(p) for p in params]
        else:
            param_names = []
        tools.append({
            "id": tool_id,
            "label": tool_id,
            "desc": desc.get("description", desc.get("name", tool_id)),
            "params": param_names or ["input"],
            "_wasm_path": str(p.resolve()),
            "_plugin_id": plugin_id,
        })
    return tools


def _invoke_wasm(tool_id: str, params: dict[str, Any]) -> str:
    """调用 JPP Wasm 插件，通过 core.wasm_runner 执行。"""
    tools = _scan_wasm_plugins()
    t = next((x for x in tools if x["id"] == tool_id), None)
    if not t:
        return f"[未知 Wasm 技能: {tool_id}]"
    wasm_path = t.get("_wasm_path", "")
    if not wasm_path or not Path(wasm_path).exists():
        return f"[Wasm 文件不存在: {tool_id}]"
    try:
        from core.wasm_runner import run_wasm_plugin
        stdin_json = params if params else {}
        result = run_wasm_plugin(
            wasm_path,
            function_name="run",
            fuel_limit=200_000,
            stdin_json=stdin_json,
        )
        if result is None:
            return "[Wasm 执行未返回结果]"
        if isinstance(result, str):
            return result
        if isinstance(result, int):
            return f"[exit {result}]"
        return str(result)
    except Exception as e:
        logger.warning("[Skills] Wasm 执行失败 %s: %s", tool_id, e)
        return f"[Wasm 执行失败: {e}]"


def run_tool(
    tool_id: str,
    action_input: str,
    allowed_skills: Optional[list[str]] = None,
) -> str:
    """
    根据工具 ID 和输入字符串执行工具，返回可读结果。
    硬拦截：若 allowed_skills 非 None 且 tool_id 不在白名单，直接拒绝执行。
    支持 Native Core 与 JPP Wasm 动态路由。
    """
    tool_id = (tool_id or "").strip().lower()
    inp = (action_input or "").strip()

    if not is_tool_allowed(tool_id, allowed_skills):
        return "[权限拒绝: 当前子账号未开启该技能]"

    # JPP Wasm 插件：JSON 参数序列化后传入 wasm_runner
    if tool_id.startswith("jpp:"):
        params: dict[str, Any] = {}
        if inp:
            try:
                params = json.loads(inp) if inp.strip().startswith("{") else {"input": inp}
            except json.JSONDecodeError:
                params = {"input": inp}
        return _invoke_wasm(tool_id, params)

    params = {}
    if tool_id == "core:fs_read":
        params["file_path"] = inp or "target.txt"
    elif tool_id == "core:shell_exec":
        params["command"] = inp
        params["timeout"] = 30
    elif tool_id == "core:fs_write":
        if "," in inp and "=" in inp:
            for part in inp.split(","):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[k.strip()] = v.strip()
        else:
            lines = inp.split("\n")
            params["file_path"] = lines[0].strip() if lines else ""
            params["content"] = "\n".join(lines[1:]) if len(lines) > 1 else ""
    else:
        return f"[未知工具: {tool_id}]"

    try:
        result = _invoke_native(tool_id, **params)
        if isinstance(result, dict):
            out = result.get("stdout", "")
            err = result.get("stderr", "")
            code = result.get("returncode", 0)
            if err:
                return f"[exit {code}]\nstdout: {out}\nstderr: {err}"
            return out or f"[exit {code}]"
        return str(result)
    except Exception as e:
        return f"[执行失败: {e}]"


def _build_allowed_ids(allowed_skills: list[str]) -> set[str]:
    """将白名单项展开为可匹配的 id 集合（支持 core:xxx、jpp:xxx 与 xxx）。"""
    out: set[str] = set()
    for s in allowed_skills:
        if not isinstance(s, str) or not s.strip():
            continue
        s = s.strip().lower()
        out.add(s)
        if s.startswith("jpp:"):
            continue  # jpp: 仅精确匹配
        if s.startswith("core:"):
            out.add(s[5:])
        else:
            out.add(f"core:{s}")
    return out


def load_tools(
    skill_ids: Optional[list[str]] = None,
    allowed_skills: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """
    加载可用工具列表，受 L2 白名单硬拦截。
    包含 Native Core 与 l3_node/skills/wasm_plugins/ 下的 JPP .wasm 插件。

    Args:
        skill_ids: 显式指定要加载的 ID 列表（可选，内部用）
        allowed_skills: L2 下发的白名单。None=全开，[]=无权限，非空=仅这些

    Returns:
        过滤后的 tools，未在白名单内的 Skill 被剔除，绝不提交给 LLM。
    """
    tools = list(NATIVE_TOOLS)
    wasm_tools = _scan_wasm_plugins()
    for w in wasm_tools:
        tools.append({
            "id": w["id"],
            "label": w["label"],
            "desc": w["desc"],
            "params": w.get("params", ["input"]),
        })
    if skill_ids:
        tools = [t for t in tools if t["id"] in skill_ids]

    if allowed_skills is not None:
        if not allowed_skills:
            tools = []
            logger.debug("[Skills] allowed_skills 为空，无可用工具")
        else:
            allowed_ids = _build_allowed_ids(allowed_skills)
            filtered = [t for t in tools if t["id"] in allowed_ids]
            for t in tools:
                if t not in filtered:
                    logger.debug("[Skills] 白名单拦截: %s 不在 allowed_skills 中", t["id"])
            tools = filtered
    return tools


def is_tool_allowed(tool_id: str, allowed_skills: Optional[list[str]]) -> bool:
    """
    硬拦截：校验 tool_id 是否在白名单内。
    用于 run_tool 执行前二次校验，防御本地篡改。
    """
    if allowed_skills is None:
        return True
    if not allowed_skills:
        return False
    allowed_ids = _build_allowed_ids(allowed_skills)
    return (tool_id or "").strip().lower() in allowed_ids


def build_tools_description(tools: list[dict[str, Any]]) -> str:
    """生成 Agent system prompt 中的工具描述段落。"""
    lines = [f"- {t['label']}: {t['desc']}" for t in tools]
    return "\n".join(lines) if lines else "（无可用工具）"
