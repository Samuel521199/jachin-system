"""
Jachin Nexus V2 - L3 技能加载器

扫描并加载 Native Core、JPP Wasm 插件与本地技能，转化为 LiteLLM 可用的 tools 格式。
权限死锁在 ~/.jachin/workspace/。
零信任：allowed_skills 白名单硬拦截，未在白名单内的 Skill 绝对禁止提交给 LLM。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _extract_stem_from_hr_report(report: str) -> str:
    """从 HR 报告提取候选人姓名作为 stem。支持多种 LLM 输出格式。"""
    # 格式1: 候选人姓名：张三
    m = re.search(r"候选人姓名[：:]\s*([^\s\n\-—]+)", report)
    if m:
        return m.group(1).strip()
    # 格式2: 候选人评估报告：张三 - 后端架构师
    m = re.search(r"候选人评估报告[：:]\s*([^\s\-—]+)", report)
    if m:
        return m.group(1).strip()
    # 格式3: # 张三 - 后端架构师（附录原始简历标题）
    m = re.search(r"#\s*[📋🧾]?\s*([^\s\-—]+)\s*[-—]\s*后端", report)
    if m:
        return m.group(1).strip()
    return ""

# JPP Wasm 插件目录（相对 l3_node/skills）
_WASM_PLUGINS_DIR = Path(__file__).resolve().parent / "wasm_plugins"
# L3 冷启动同步缓存：~/.jachin/l3_skill_cache/（从 L2 拉取的技能）
_L3_SKILL_CACHE_DIR = Path.home() / ".jachin" / "l3_skill_cache"

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


def _fetch_skill_config(skill_id: str) -> dict[str, Any]:
    """
    从 L2 拉取技能配置（skill_registry）。
    优先直接调用 core.skill_registry（同进程），否则 HTTP GET。
    """
    try:
        from core.skill_registry import get_skill_config
        return get_skill_config(skill_id)
    except ImportError:
        pass
    try:
        import httpx
        cfg_path = Path.home() / ".jachin" / "l2_gateway_config.json"
        l2_url = "http://localhost:18888"
        sub_account_id = ""
        if cfg_path.exists():
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
                l2_url = (data.get("l2_base_url") or l2_url).rstrip("/")
                sub_account_id = data.get("sub_account_id") or ""
            except Exception:
                pass
        url = f"{l2_url}/api/v2/skills/{skill_id}/config"
        headers = {}
        if sub_account_id:
            headers["X-Sub-Account-Id"] = sub_account_id
        with httpx.Client(timeout=5.0, trust_env=False) as client:
            r = client.get(url, headers=headers or None)
            if r.is_success:
                data = r.json()
                return data.get("config") or {}
    except Exception as e:
        logger.debug("[Skills] HTTP 拉取配置失败: %s", e)
    return {}


def _get_hr_plugin_config_defaults(lookup_id: str) -> dict[str, Any]:
    """从 plugin.json 直接读取 HR 技能默认配置（L2 不可用时兜底，确保 output_dir 等可用）"""
    _HR_ITEM_MAP = {
        "jpp:com.jachin.hr.analyzer": "hr-analyzer",
        "jpp:com.jachin.hr.analyzer2": "hr-analyzer2",
        "jpp:com.jachin.hr.analyzer3": "hr-analyzer3",
        "jpp:com.jachin.hr.analyzer4": "hr-analyzer4",
    }
    item_id = _HR_ITEM_MAP.get(lookup_id)
    if not item_id:
        return {}
    for base in (_WASM_PLUGINS_DIR, _L3_SKILL_CACHE_DIR):
        plugin_path = base / item_id / "plugin.json"
        if plugin_path.exists():
            try:
                data = json.loads(plugin_path.read_text(encoding="utf-8"))
                cfg = data.get("configs") or data.get("config")
                if isinstance(cfg, dict):
                    return {k: v for k, v in cfg.items() if not (k and str(k).startswith("_"))}
            except Exception as e:
                logger.debug("[Skills] 读取 plugin.json 失败 path=%s err=%s", plugin_path, e)
    return {}


def get_hr_invoke_defaults(skill_id: str = "com.jachin.hr.analyzer4") -> dict[str, Any]:
    """
    从技能配置（plugin.json + skill_registry）读取 HR 透析镜的默认调用参数，
    供 Agent 系统提示词与空参数兜底使用。智能关联技能配置，避免硬编码。
    """
    lookup_id = f"jpp:{skill_id}" if not skill_id.startswith("jpp:") else skill_id
    config_id = lookup_id.replace("jpp:", "")
    proj = Path(__file__).resolve().parent.parent.parent
    cfg = {**_get_hr_plugin_config_defaults(lookup_id), **(_fetch_skill_config(config_id) or {})}
    use_abs = cfg.get("resume_input_dir_use_absolute") in (True, "true", "1", "yes") or cfg.get("use_absolute_path") in (True, "true", "1", "yes")
    try:
        from l3_node.hr_analysis_persist import _resolve_safe_dir, _PROJ_ROOT
        resume_dir = _resolve_safe_dir(cfg.get("resume_input_dir") or "data/hr_resumes", _PROJ_ROOT, use_absolute_path=use_abs)
    except ImportError:
        resume_dir = None
    resume_dir = resume_dir or (proj / "data" / "hr_resumes")
    target_role = (cfg.get("default_target_role") or "").strip()
    if not target_role:
        jd_dir = proj / "config" / "hr_jds"
        if jd_dir.exists():
            for p in sorted(jd_dir.glob("*.md")):
                target_role = p.stem
                break
    if not target_role:
        target_role = "backend_engineer"
    resume_filename = (cfg.get("default_resume") or "").strip()
    if not resume_filename and resume_dir.exists():
        for p in sorted(resume_dir.glob("*.md")) + sorted(resume_dir.glob("*.txt")):
            resume_filename = p.name
            break
    if not resume_filename:
        resume_filename = "zhangsan_resume.md"
    return {
        "target_role": target_role,
        "resume_filename": resume_filename,
        "resume_input_dir": cfg.get("resume_input_dir") or "data/hr_resumes",
        "output_dir": cfg.get("output_dir") or "data/hr_analysis",
    }


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
    """L3 独立运行时的 Native 兜底实现。HR 白名单：client_volumes、data/hr_resumes、config/hr_jds。"""
    workspace = Path.home() / ".jachin" / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    proj = Path(__file__).resolve().parent.parent.parent
    _l3_volume = (Path.home() / ".jachin" / "client_volumes").resolve()
    _hr_allowed = [_l3_volume, (proj / "data" / "hr_resumes").resolve(), (proj / "config" / "hr_jds").resolve()]

    def _under_hr(p: Path) -> bool:
        try:
            abs_p = p.resolve()
            return any(str(abs_p).startswith(str(a)) for a in _hr_allowed)
        except (OSError, RuntimeError):
            return False

    def _assert_under(p: Path) -> None:
        if _under_hr(p):
            return
        if not str(p.resolve()).startswith(str(workspace.resolve())):
            raise ValueError(f"路径越界: {p} 必须在 ~/.jachin/workspace/ 或 client_volumes、data/hr_resumes、config/hr_jds 下")

    def _read_file_content(p: Path) -> str:
        """读取文件，PDF 使用 core.pdf_extractor 提取纯文本（与 MCP read_file 复用）。"""
        if p.suffix.lower() == ".pdf":
            try:
                from core.pdf_extractor import extract_pdf_text
                return extract_pdf_text(p) or ""
            except ImportError:
                return ""
        return p.read_text(encoding="utf-8", errors="replace")

    if tool_id == "core:fs_read":
        raw = (kwargs.get("file_path", "") or "").strip().replace("\\", "/")
        fp = Path(raw).expanduser()
        if not fp.is_absolute():
            # L3 数据卷相对路径：global_resume_pool/Java_杭州 4-6K/xxx.pdf
            cand_vol = (_l3_volume / raw.lstrip("/")).resolve()
            if cand_vol.exists() and cand_vol.is_file() and _under_hr(cand_vol):
                return _read_file_content(cand_vol)
            for base in _hr_allowed:
                cand = (base / fp.name).resolve()
                if cand.exists() and _under_hr(cand):
                    return _read_file_content(cand)
            cand = (proj / raw.lstrip("/")).resolve()
            if cand.exists() and _under_hr(cand):
                return _read_file_content(cand)
            fp = (workspace / raw).resolve()
        if _under_hr(fp):
            return _read_file_content(fp)
        _assert_under(fp)
        return _read_file_content(fp)
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


def _scan_wasm_dir_flat(scan_dir: Path) -> list[dict[str, Any]]:
    """扫描扁平目录（wasm 与 plugin.json 同层）。"""
    tools: list[dict[str, Any]] = []
    if not scan_dir.exists():
        return tools
    for p in scan_dir.iterdir():
        if p.suffix != ".wasm":
            continue
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
            "label": desc.get("name", tool_id),
            "desc": desc.get("description", desc.get("name", tool_id)),
            "params": param_names or ["input"],
            "_wasm_path": str(p.resolve()),
            "_plugin_id": plugin_id,
            "_item_id": p.stem,
            "_name": desc.get("name", plugin_id),
        })
    return tools


def _scan_wasm_dir_nested(scan_dir: Path) -> list[dict[str, Any]]:
    """扫描嵌套目录（每个子目录为 item_id，内含 plugin.json 与 .wasm）。"""
    tools: list[dict[str, Any]] = []
    if not scan_dir.exists():
        return tools
    for subdir in scan_dir.iterdir():
        if not subdir.is_dir():
            continue
        wasm_files = list(subdir.glob("*.wasm"))
        if not wasm_files:
            continue
        plugin_path = subdir / "plugin.json"
        if not plugin_path.exists():
            continue
        try:
            desc = json.loads(plugin_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        p = wasm_files[0]
        plugin_id = desc.get("id") or subdir.name
        tool_id = f"jpp:{plugin_id}"
        params = desc.get("parameters", desc.get("schema", {}).get("input", {}))
        if isinstance(params, dict):
            param_names = list(params.keys()) if params else []
        elif isinstance(params, list):
            param_names = [x.get("name", x) if isinstance(x, dict) else str(x) for x in params]
        else:
            param_names = []
        tools.append({
            "id": tool_id,
            "label": desc.get("name", tool_id),
            "desc": desc.get("description", desc.get("name", tool_id)),
            "params": param_names or ["input"],
            "_wasm_path": str(p.resolve()),
            "_plugin_id": plugin_id,
            "_item_id": subdir.name,
            "_name": desc.get("name", plugin_id),
        })
    return tools


def _get_uninstalled_builtin_skills() -> set[str]:
    """读取用户已卸载的内置技能（从 ~/.jachin/uninstalled_builtin_skills.json）"""
    try:
        from core.skill_registry import get_uninstalled_builtin_skills
        return get_uninstalled_builtin_skills()
    except Exception:
        return set()


def _get_permanently_uninstalled_skills() -> set[str]:
    """读取永久卸载黑名单（回收站彻底删除后，防止 L2 同步重新拉取）"""
    try:
        from core.skill_registry import get_permanently_uninstalled_skills
        return get_permanently_uninstalled_skills()
    except Exception:
        return set()


def _scan_wasm_plugins() -> list[dict[str, Any]]:
    """
    扫描 l3_node/skills/wasm_plugins/ 与 ~/.jachin/l3_skill_cache/ 下的 JPP .wasm 插件。
    后者为 L3 冷启动从 L2 拉取的技能缓存。
    内置 hr-analyzer 统一用 jpp:com.jachin.hr.analyzer（L1 发布 id），避免与 L1 同步重复展示。
    已卸载的内置技能（uninstalled_builtin_skills.json）、永久卸载技能（permanently_uninstalled_skills.json）将被过滤。
    """
    uninstalled = _get_uninstalled_builtin_skills() | _get_permanently_uninstalled_skills()
    tools: list[dict[str, Any]] = []
    seen: set[str] = set()
    for t in _scan_wasm_dir_flat(_WASM_PLUGINS_DIR) + _scan_wasm_dir_nested(_WASM_PLUGINS_DIR) + _scan_wasm_dir_nested(_L3_SKILL_CACHE_DIR):
        tid = t["id"]
        # 内置 hr-analyzer 统一用 L1 id，避免 jpp:hr-analyzer 与 jpp:com.jachin.hr.analyzer 重复
        if tid == "jpp:hr-analyzer" and _WASM_PLUGINS_DIR in Path(t.get("_wasm_path", "")).parents:
            tid = "jpp:com.jachin.hr.analyzer"
            t = {**t, "id": tid}
        item_id = t.get("_item_id") or tid.replace("jpp:", "")
        if item_id in uninstalled:
            continue
        if tid not in seen:
            seen.add(tid)
            tools.append(t)
    return tools


def load_skills_for_ui(allowed_skills: Optional[list[str]] = None) -> list[dict[str, Any]]:
    """
    供 Skill Matrix 等 UI 使用：仅返回 Wasm 技能（L2 同步 + 内置），不含 Native Core。
    Native Core（core:fs_read 等）为 Agent 内部工具，不展示给用户。
    """
    tools = _scan_wasm_plugins()
    if allowed_skills is not None:
        if not allowed_skills:
            return []
        allowed_ids = _build_allowed_ids(allowed_skills)
        tools = [t for t in tools if t["id"] in allowed_ids]
    return tools


def build_hr_stdin_for_debug(
    params: dict[str, Any],
    lookup_id: str = "jpp:com.jachin.hr.analyzer4",
) -> tuple[str, dict[str, Any]]:
    """
    构建 HR 透析镜的 stdin 字符串，供调试脚本使用（不执行 Wasm）。
    返回 (stdin_str, debug_info)，debug_info 含 jd_src, jd_path, jd_preview, has_jd 等。
    """
    stdin_json = dict(params) if params else {}
    debug: dict[str, Any] = {"jd_src": None, "jd_path": None, "jd_preview": "", "has_jd": False, "caller_jd_len": 0}
    config_id = lookup_id.replace("jpp:", "")
    proj = Path(__file__).resolve().parent.parent.parent
    defaults = get_hr_invoke_defaults(config_id)
    if not stdin_json.get("target_role"):
        stdin_json["target_role"] = defaults.get("target_role", "backend_engineer")
    if not stdin_json.get("target_dir"):
        stdin_json["target_dir"] = "data/hr_resumes"
    try:
        from datetime import datetime, timezone, timedelta
        _cn_tz = timezone(timedelta(hours=8))
        stdin_json["reference_date"] = datetime.now(_cn_tz).strftime("%Y-%m-%d")
    except Exception:
        pass
    cfg = {**_get_hr_plugin_config_defaults(lookup_id), **(_fetch_skill_config(config_id) or {})}
    caller_jd = (stdin_json.get("jd_template") or stdin_json.get("jd_content") or "").strip()
    if not caller_jd:
        caller_jd = (cfg.get("JD_template") or cfg.get("jd_template") or "").strip()
    debug["caller_jd_len"] = len(caller_jd)
    if caller_jd:
        stdin_json["jd_template"] = caller_jd
        stdin_json.pop("jd_content", None)
        stdin_json.pop("jd_path", None)
        debug["jd_src"] = "jd_template"
        debug["jd_preview"] = caller_jd[:80] + ("…" if len(caller_jd) > 80 else "")
        debug["has_jd"] = True
    else:
        stdin_json.pop("jd_template", None)
        stdin_json.pop("jd_content", None)
        stdin_json.pop("jd_path", None)
    for k, v in cfg.items():
        if k and not k.startswith("_") and k not in ("JD_template", "jd_template", "jd_path"):
            stdin_json[k] = v
    _hr_files_val = stdin_json.pop("_hr_files", None)
    if _hr_files_val:
        _stdin_str = _hr_files_val + "\n" + json.dumps(stdin_json, ensure_ascii=False, separators=(",", ":"))
    else:
        _stdin_str = json.dumps(stdin_json, ensure_ascii=False, separators=(",", ":"))
    if not debug["has_jd"]:
        debug["has_jd"] = bool((stdin_json.get("jd_template") or "").strip())
    return _stdin_str, debug


def _invoke_wasm(tool_id: str, params: dict[str, Any], ndjson_queue: Optional[Any] = None) -> str:
    """调用 JPP Wasm 插件，通过 core.wasm_runner 执行。"""
    import sys
    tools = _scan_wasm_plugins()
    # jpp:hr-analyzer 已统一为 jpp:com.jachin.hr.analyzer，兼容旧调用
    lookup_id = "jpp:com.jachin.hr.analyzer" if tool_id == "jpp:hr-analyzer" else tool_id
    t = next((x for x in tools if x["id"] == lookup_id), None)
    if not t:
        print(f"[Skill Execute] [Wasm] 未找到技能 tool_id={tool_id}", file=sys.stderr, flush=True)
        return f"[未知 Wasm 技能: {tool_id}]"
    wasm_path = t.get("_wasm_path", "")
    # L1 同步的 com.jachin.hr.analyzer 与内置 hr-analyzer 为同一技能，优先用 wasm_plugins 的（与 execute ABI 兼容，避免 cache 版 __rust_dealloc 不兼容）
    _HR_BUILTIN_MAP = {
        "jpp:com.jachin.hr.analyzer": "hr-analyzer",
        "jpp:com.jachin.hr.analyzer2": "hr-analyzer2",
        "jpp:com.jachin.hr.analyzer3": "hr-analyzer3",
        "jpp:com.jachin.hr.analyzer4": "hr-analyzer4",
    }
    if lookup_id in _HR_BUILTIN_MAP:
        plugin_dir = _HR_BUILTIN_MAP[lookup_id]
        proj_root = Path(__file__).resolve().parent.parent.parent
        candidates = [
            _WASM_PLUGINS_DIR / plugin_dir / "main.wasm",
            proj_root / "l3_node" / "skills" / "wasm_plugins" / plugin_dir / "main.wasm",
            Path.cwd() / "l3_node" / "skills" / "wasm_plugins" / plugin_dir / "main.wasm",
        ]
        for builtin in candidates:
            if builtin.exists():
                wasm_path = str(builtin.resolve())
                print(f"[Skill Execute] [Wasm] 使用内置 {plugin_dir} wasm_path={wasm_path}", file=sys.stderr, flush=True)
                break
        else:
            print(f"[Skill Execute] [Wasm] 内置 {plugin_dir} 未找到，candidates={[str(p) for p in candidates]}", file=sys.stderr, flush=True)
    if not wasm_path or not Path(wasm_path).exists():
        print(f"[Skill Execute] [Wasm] 文件不存在 tool_id={tool_id} path={wasm_path}", file=sys.stderr, flush=True)
        return f"[Wasm 文件不存在: {tool_id}]"
    stdin_json = dict(params) if params else {}
    # HR 简历透视镜 / 透析镜：从技能配置读取默认值，空参数时自动注入
    if lookup_id in ("jpp:com.jachin.hr.analyzer", "jpp:com.jachin.hr.analyzer2", "jpp:com.jachin.hr.analyzer3", "jpp:com.jachin.hr.analyzer4"):
        config_id = lookup_id.replace("jpp:", "")
        proj = Path(__file__).resolve().parent.parent.parent
        defaults = get_hr_invoke_defaults(config_id)
        if not stdin_json.get("target_role"):
            stdin_json["target_role"] = defaults.get("target_role", "backend_engineer")
        if not stdin_json.get("resume_filename") and not stdin_json.get("target_dir") and not stdin_json.get("resume_path"):
            stdin_json["target_dir"] = "data/hr_resumes"
        config = _fetch_skill_config(config_id)
        cfg = {**_get_hr_plugin_config_defaults(lookup_id), **(config or {})}
        # 岗位 JD 仅从招聘大盘传入（jd_template/jd_content），不再从 config/hr_jds、技能配置等读取
        # 批量模式：传了 target_dir 则处理目录下所有简历；否则单文件模式
        if stdin_json.get("target_dir"):
            stdin_json.pop("resume_filename", None)
            stdin_json.pop("resume_path", None)
            # 调用方已传入 _hr_files（如 recruitment_task 收网后的 pdf_paths），直接使用
            paths: list[str] = []
            if stdin_json.get("_hr_files"):
                paths = [p.strip() for p in stdin_json["_hr_files"].split("|||") if p.strip()]
                if paths:
                    logger.info("[Skill Execute] HR 使用调用方传入的 _hr_files count=%d", len(paths))
                    print(f"[Skill Execute] HR _hr_files from caller count={len(paths)}", file=sys.stderr, flush=True)
            # 本地列举目录，注入 resume_paths（绝对路径数组），绕过 MCP list_directory
            if not paths:
                tdir = stdin_json.get("target_dir") or "data/hr_resumes"
                if tdir == "data/hr_resumes":
                    tdir = cfg.get("resume_input_dir") or "data/hr_resumes"
                stdin_json["target_dir"] = tdir
                try:
                    from l3_node.hr_analysis_persist import _resolve_safe_dir, _PROJ_ROOT
                    _l3_vol = Path.home() / ".jachin" / "client_volumes"
                    # L3 数据卷：pool_X/X（收网）、auto_xxx、global_resume_pool/JobFolder
                    if tdir.startswith("pool_") or tdir.startswith("auto_") or tdir.startswith("global_resume_pool"):
                        vol_dir = (_l3_vol / tdir.replace("\\", "/").lstrip("/")).resolve()
                        if vol_dir.is_dir() and str(vol_dir).startswith(str(_l3_vol.resolve())):
                            resume_dir = vol_dir
                        else:
                            resume_dir = None
                    else:
                        resume_dir = _resolve_safe_dir(tdir, _PROJ_ROOT, use_absolute_path=False)
                    if not resume_dir:
                        resume_dir = (proj / tdir.replace("\\", "/").lstrip("/")).resolve()
                    # 兜底：__file__ 解析的 proj 可能不对（如 Desktop  bundled 时），尝试 cwd
                    if not resume_dir.is_dir():
                        alt = (Path.cwd() / tdir.replace("\\", "/").lstrip("/")).resolve()
                        if alt.is_dir():
                            resume_dir = alt
                            logger.info("[Skill Execute] HR 使用 cwd 解析 resume_dir=%s", resume_dir)
                    if resume_dir.is_dir():
                        paths = [
                            str(f.resolve()).replace("\\", "/") for f in sorted(resume_dir.iterdir())
                            if f.is_file() and f.suffix.lower() in (".md", ".txt", ".pdf")
                        ]
                        if paths:
                            rp_val = "|||".join(paths)
                            stdin_json["_hr_files"] = rp_val
                            logger.info("[Skill Execute] HR 本地列举 target_dir=%s count=%d paths=%s", tdir, len(paths), paths[:2])
                            print(f"[Skill Execute] HR _hr_files OK count={len(paths)} len={len(rp_val)}", file=sys.stderr, flush=True)
                        else:
                            logger.warning("[Skill Execute] HR 目录 %s 下无 .md/.txt/.pdf 文件", resume_dir)
                    else:
                        logger.warning("[Skill Execute] HR 简历目录不存在: %s (proj=%s cwd=%s)", resume_dir, proj, Path.cwd())
                except Exception as e:
                    logger.warning("[Skill Execute] HR 本地列举失败: %s", e)
            # _hr_files 为空时直接返回，不调用 Wasm（避免 list_directory 返回项目根）
            if stdin_json.get("target_dir") and not stdin_json.get("_hr_files"):
                print("[Skill Execute] HR EARLY RETURN: resume_paths empty", file=sys.stderr, flush=True)
                return (
                    f"⚠️ 无法列举简历目录：{tdir}\n"
                    f"请确认 data/hr_resumes 存在且包含 .md、.txt 或 .pdf 文件。\n"
                    f"当前工作目录: {Path.cwd()}\n"
                    f"项目根(proj): {proj}"
                )
        else:
            if not stdin_json.get("resume_filename"):
                stdin_json["resume_filename"] = defaults.get("resume_filename", "zhangsan_resume.md")
        # 参考日期：中国时区，供 LLM 判断应届生毕业年份、工作经历时间等，避免未来日期误判
        try:
            from datetime import datetime, timezone, timedelta
            _cn_tz = timezone(timedelta(hours=8))
            stdin_json["reference_date"] = datetime.now(_cn_tz).strftime("%Y-%m-%d")
        except Exception:
            pass
        # 岗位 JD：直接当参数传 jd_template，最简单可靠（不依赖临时文件/mcp_read_file）
        caller_jd = (stdin_json.get("jd_template") or stdin_json.get("jd_content") or "").strip()
        if not caller_jd:
            caller_jd = (cfg.get("JD_template") or cfg.get("jd_template") or "").strip()
        if caller_jd:
            stdin_json["jd_template"] = caller_jd
            stdin_json.pop("jd_content", None)
            stdin_json.pop("jd_path", None)
            logger.info("[Skill Execute] HR 岗位 JD 已传入 jd_template len=%d", len(caller_jd))
        else:
            stdin_json.pop("jd_template", None)
            stdin_json.pop("jd_content", None)
            stdin_json.pop("jd_path", None)
        _has_jd = bool((stdin_json.get("jd_template") or "").strip()) or bool((stdin_json.get("jd_path") or "").strip())
        if not _has_jd:
            logger.warning("[Skill Execute] HR 岗位 JD 为空，请从招聘大盘填写「岗位 JD」")
            print("[Skill Execute] HR 警告: 岗位 JD 为空，分析将缺少【岗位要求】", file=sys.stderr, flush=True)
        for k, v in cfg.items():
            if k and not k.startswith("_") and k not in ("JD_template", "jd_template", "jd_path"):
                stdin_json[k] = v
        # resume_path 依赖 cfg，需在 cfg 合并后解析（批量模式 target_dir 时跳过）
        if "resume_path" not in stdin_json and stdin_json.get("resume_filename") and not stdin_json.get("target_dir"):
            fn = stdin_json.get("resume_filename", "zhangsan_resume.md")
            resume_dir_cfg = (cfg.get("resume_input_dir") or "data/hr_resumes").strip()
            use_abs = cfg.get("resume_input_dir_use_absolute") in (True, "true", "1", "yes") or cfg.get("use_absolute_path") in (True, "true", "1", "yes")
            try:
                from l3_node.hr_analysis_persist import _resolve_safe_dir, _PROJ_ROOT
                resume_dir = _resolve_safe_dir(resume_dir_cfg, _PROJ_ROOT, use_absolute_path=use_abs)
                if resume_dir and (resume_dir / fn).exists():
                    stdin_json["resume_path"] = str((resume_dir / fn).resolve())
                elif (proj / "data" / "hr_resumes" / fn).exists():
                    stdin_json["resume_path"] = str((proj / "data" / "hr_resumes" / fn).resolve())
            except Exception:
                if (proj / "data" / "hr_resumes" / fn).exists():
                    stdin_json["resume_path"] = str((proj / "data" / "hr_resumes" / fn).resolve())
    # HR 技能需 LLM，执行前校验
    if lookup_id in ("jpp:com.jachin.hr.analyzer", "jpp:com.jachin.hr.analyzer2", "jpp:com.jachin.hr.analyzer3", "jpp:com.jachin.hr.analyzer4"):
        try:
            from core.wasm_runner import _host_services
            if not _host_services.get("llm_engine"):
                return "⚠️ LLM 引擎未注册，无法执行 HR 透析镜。请确保 L3 已正确启动并完成与 L2 的配对。"
        except Exception:
            pass
    # 批量模式：文件列表放 stdin 首行，绕过 Wasm JSON extract（易误匹配 jd_template）
    # 当有 jd_template 时，首行放 JD_START:::content:::JD_END，Rust 优先从此解析，避免 JSON 转义导致提取失败
    _hr_files_val = stdin_json.pop("_hr_files", None)
    _jd_first_line = ""
    if stdin_json.get("jd_template") and (stdin_json.get("jd_template") or "").strip():
        _jd_raw = (stdin_json.get("jd_template") or "").strip().replace("\r\n", "\r").replace("\n", "\r")
        _jd_first_line = "JD_START:::" + _jd_raw + ":::JD_END"
    if _hr_files_val:
        if _jd_first_line:
            _stdin_str = _hr_files_val + "\n" + _jd_first_line + "\n" + json.dumps(stdin_json, ensure_ascii=False, separators=(",", ":"))
        else:
            _stdin_str = _hr_files_val + "\n" + json.dumps(stdin_json, ensure_ascii=False, separators=(",", ":"))
    elif _jd_first_line:
        _stdin_str = _jd_first_line + "\n" + json.dumps(stdin_json, ensure_ascii=False, separators=(",", ":"))
    else:
        _stdin_str = json.dumps(stdin_json, ensure_ascii=False, separators=(",", ":"))
    _jd_in = "jd_template" in stdin_json and bool((stdin_json.get("jd_template") or "").strip())
    _jd_src = "jd_template"
    print(f"[Skill Execute] WASM_IN jd_ok={_jd_in} jd_src={_jd_src} stdin_len={len(_stdin_str)}", file=sys.stderr, flush=True)
    # 调试：DEBUG_HR_JD=1 时写入 stdin 到临时文件，便于排查 JD 传入问题
    if _jd_in and __import__("os").environ.get("DEBUG_HR_JD") == "1":
        import tempfile
        _f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        _f.write(_stdin_str)
        _f.close()
        print(f"[Skill Execute] DEBUG_HR_JD stdin 已写入 {_f.name}", file=sys.stderr, flush=True)
    try:
        from core.wasm_runner import run_wasm_plugin
        # 批量模式 3 份简历需 3 次 LLM 调用，燃料需更高
        _fuel = 50_000_000 if lookup_id in ("jpp:com.jachin.hr.analyzer", "jpp:com.jachin.hr.analyzer2", "jpp:com.jachin.hr.analyzer3", "jpp:com.jachin.hr.analyzer4") else 200_000
        result = run_wasm_plugin(
            wasm_path,
            function_name="run",
            fuel_limit=_fuel,
            stdin_json=_stdin_str,
            ndjson_queue=ndjson_queue,
        )
        if result is None:
            print(f"[Skill Execute] [Wasm] 无返回 tool_id={tool_id}", file=sys.stderr, flush=True)
            return "[Wasm 执行未返回结果]"
        result_str = result if isinstance(result, str) else str(result)
        _rpreview = result_str[:500] if len(result_str) <= 500 else result_str[:500] + "..."
        print(f"[Skill Execute] [Wasm] RETURN len={len(result_str)} full={_rpreview!r}", file=sys.stderr, flush=True)
        # HR 透析镜：分析完成后立即写入（在返回给前端/TTS 之前），自动读取技能默认配置
        # 流式模式（ndjson_queue 非空）时由流式 handler 负责持久化，此处跳过
        if ndjson_queue is None and lookup_id in ("jpp:com.jachin.hr.analyzer", "jpp:com.jachin.hr.analyzer2", "jpp:com.jachin.hr.analyzer3", "jpp:com.jachin.hr.analyzer4"):
            _err_prefixes = ("⚠️", "[权限", "[未知", "[Wasm", "[执行")
            try:
                from l3_node.hr_analysis_persist import persist_hr_analysis_result, persist_hr_analysis_batch_item
                from core.wasm_runner import get_last_ndjson_lines
                cfg = _fetch_skill_config(lookup_id.replace("jpp:", ""))
                cfg = {**_get_hr_plugin_config_defaults(lookup_id), **(cfg or {})}
                ndjson_lines = get_last_ndjson_lines()
                count = 0
                if ndjson_lines:
                    for line in ndjson_lines:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            item = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(item, dict):
                            continue
                        if item.get("status") == "done":
                            continue
                        if item.get("status") != "progress":
                            continue
                        report = item.get("report_content")
                        if not report or not isinstance(report, str):
                            continue
                        fn = item.get("filename") or ""
                        stem = (Path(fn).stem.replace("_resume", "").replace("_analysis", "").strip() or Path(fn).stem) if fn else ""
                        if not stem or re.match(r"^resume_\d+$", stem):
                            stem = _extract_stem_from_hr_report(report) or stem or "unknown"
                        persist_hr_analysis_batch_item(lookup_id, report, stem, config=cfg)
                        count += 1
                    if count > 0:
                        result_str = f"✅ 执行成功，本次分析了 {count} 份简历。报告已保存至 data/hr_analysis/ 目录。"
                        logger.info("[Skill Execute] HR NDJSON 批量持久化完成 count=%d", count)
                elif result_str and len(result_str.strip()) > 20 and not any(result_str.strip().startswith(p) for p in _err_prefixes):
                    # 回退：解析 JSON 数组（旧版 Wasm 输出）
                    parsed = None
                    raw = result_str.strip()
                    try:
                        parsed = json.loads(raw)
                    except json.JSONDecodeError:
                        if raw.startswith("["):
                            try:
                                end = raw.rfind("]")
                                if end > 0:
                                    parsed = json.loads(raw[: end + 1])
                            except json.JSONDecodeError:
                                pass
                    if isinstance(parsed, list) and parsed:
                        for item in parsed:
                            if not isinstance(item, dict):
                                continue
                            report = item.get("report")
                            if not report or not isinstance(report, str):
                                continue
                            fn = item.get("filename") or ""
                            stem = (Path(fn).stem.replace("_resume", "").replace("_analysis", "").strip() or Path(fn).stem) if fn else ""
                            if not stem or re.match(r"^resume_\d+$", stem):
                                stem = _extract_stem_from_hr_report(report) or stem or "unknown"
                            persist_hr_analysis_batch_item(lookup_id, report, stem, config=cfg)
                            count += 1
                        if count > 0:
                            result_str = f"✅ 执行成功，本次分析了 {count} 份简历。报告已保存至 data/hr_analysis/ 目录。"
                    else:
                        persist_hr_analysis_result(lookup_id, result_str, stdin_json, config=cfg)
                        result_str = f"✅ 执行成功，本次分析了 1 份简历。报告已保存至 data/hr_analysis/ 目录。\n\n--- 分析报告 ---\n\n{result_str}"
            except Exception as pe:
                logger.warning("[Skills] HR 报告持久化失败: %s", pe)
        if isinstance(result, str):
            return result_str
        if isinstance(result, int):
            return f"[exit {result}]"
        return str(result)
    except Exception as e:
        print(f"[Skill Execute] [Wasm] 异常 tool_id={tool_id} error={e}", file=sys.stderr, flush=True)
        logger.warning("[Skills] Wasm 执行失败 %s: %s", tool_id, e)
        return f"[Wasm 执行失败: {e}]"


def run_tool(
    tool_id: str,
    action_input: str,
    allowed_skills: Optional[list[str]] = None,
    ndjson_queue: Optional[Any] = None,
) -> str:
    """
    根据工具 ID 和输入字符串执行工具，返回可读结果。
    硬拦截：若 allowed_skills 非 None 且 tool_id 不在白名单，直接拒绝执行。
    支持 Native Core 与 JPP Wasm 动态路由。
    """
    import sys
    tool_id = (tool_id or "").strip().lower()
    inp = (action_input or "").strip()
    print(f"[Skill Execute] run_tool 入口 tool_id={tool_id} input_len={len(inp)}", file=sys.stderr, flush=True)

    if not is_tool_allowed(tool_id, allowed_skills):
        print(f"[Skill Execute] 权限拒绝 tool_id={tool_id}", file=sys.stderr, flush=True)
        return "[权限拒绝: 当前子账号未开启该技能]"

    # JPP Wasm 插件：JSON 参数序列化后传入 wasm_runner
    # 兼容 skill_id 无 jpp: 前缀（如 API 传入 com.jachin.hr.analyzer4）
    _jpp_id = tool_id if tool_id.startswith("jpp:") else (f"jpp:{tool_id}" if tool_id.startswith("com.jachin.") else "")
    if _jpp_id:
        params: dict[str, Any] = {}
        if inp:
            try:
                params = json.loads(inp) if inp.strip().startswith("{") else {"input": inp}
            except json.JSONDecodeError:
                params = {"input": inp}
        return _invoke_wasm(_jpp_id, params, ndjson_queue=ndjson_queue)

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
        print(f"[Skill Execute] 未知工具 tool_id={tool_id}", file=sys.stderr, flush=True)
        return f"[未知工具: {tool_id}]"

    print(f"[Skill Execute] [Native] 调用 tool_id={tool_id} params={params}", file=sys.stderr, flush=True)
    try:
        result = _invoke_native(tool_id, **params)
        out_str = str(result)[:200] if result else ""
        print(f"[Skill Execute] [Native] 返回 tool_id={tool_id} result_preview={out_str}...", file=sys.stderr, flush=True)
        if isinstance(result, dict):
            out = result.get("stdout", "")
            err = result.get("stderr", "")
            code = result.get("returncode", 0)
            if err:
                return f"[exit {code}]\nstdout: {out}\nstderr: {err}"
            return out or f"[exit {code}]"
        return str(result)
    except Exception as e:
        print(f"[Skill Execute] [Native] 异常 tool_id={tool_id} error={e}", file=sys.stderr, flush=True)
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
    """生成 Agent system prompt 中的工具描述段落。含 id 供 Action 精确匹配。"""
    _HR_IDS = ("jpp:com.jachin.hr.analyzer", "jpp:com.jachin.hr.analyzer2", "jpp:com.jachin.hr.analyzer3", "jpp:com.jachin.hr.analyzer4")
    lines = []
    for t in tools:
        tid = t.get("id", "")
        desc = t.get("desc", t.get("label", tid))
        if tid in _HR_IDS:
            desc = f"{desc} 【简历分析时直接调用，参数可从技能配置自动读取，可传空对象 {{}}】"
        lines.append(f"- {tid} ({t.get('label', tid)}): {desc}")
    return "\n".join(lines) if lines else "（无可用工具）"
