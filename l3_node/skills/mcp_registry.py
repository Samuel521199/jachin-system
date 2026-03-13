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
        "desc": "[L3 本地] 发布职位(publish_job)。必须传入 jd_config（HR 确认的完整 JSON），job_title 需与 HR 确认的岗位名称完全一致。系统会创建 data/{岗位名}/、复制模板填 jd.json、创建 pending/processed/result，再发布。禁止传 jd_config_path 指向 jd_to_publish.json。",
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
        "desc": "[L3 本地] 将岗位加入无人值守招聘调度引擎。每15分钟推荐（每轮成功3人即止）、推荐后20秒收网、每1分钟检查。analyze_threshold 默认4（满4份即分析并停止推荐/收网），analyze_interval_hours 默认0.05。",
        "params": ["job_name", "analyze_threshold", "analyze_interval_hours", "jd_config_path"],
    },
    {
        "id": "mcp:stop_automated_recruitment",
        "label": "mcp:stop_automated_recruitment",
        "desc": "[L3 本地] 停止无人值守招聘流程。当 HR 说「关闭」「停止」「取消」招聘时调用。job_name 为空则停止所有岗位的定时任务。",
        "params": ["job_name"],
    },
]


def _check_chrome_cdp(cdp_url: str = "http://127.0.0.1:9222") -> bool:
    """检测 Chrome 调试端口是否可连接。"""
    import urllib.request
    try:
        req = urllib.request.urlopen(f"{cdp_url.rstrip('/')}/json/version", timeout=3)
        return req.getcode() == 200
    except Exception:
        return False


def _launch_chrome_with_boss_login(project_root: Path) -> bool:
    """启动 Chrome 调试模式并打开 Boss 登录页，供 HR 扫码。多方式尝试，确保在各类环境下能启动。"""
    import os
    import subprocess

    boss_login_url = "https://www.zhipin.com/web/user/?ka=header-login"
    user_data_dir = os.path.join(os.environ.get("TEMP", os.environ.get("TMP", "/tmp")), "chrome-debug-boss")

    # 收集所有可能的 Chrome 路径（含用户安装版）
    chrome_paths = [
        os.environ.get("ProgramFiles", r"C:\Program Files") + r"\Google\Chrome\Application\chrome.exe",
        os.environ.get("ProgramFiles(X86)", r"C:\Program Files (x86)") + r"\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    chrome_exe = None
    for p in chrome_paths:
        if p and os.path.isfile(p):
            chrome_exe = p
            break
    if not chrome_exe:
        logger.warning("[MCP Registry] 未找到 Chrome，请安装后重试")
        return False

    args = ["--remote-debugging-port=9222", f"--user-data-dir={user_data_dir}", boss_login_url]
    chrome_dir = str(Path(chrome_exe).parent)
    flags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        flags |= subprocess.DETACHED_PROCESS
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        flags |= subprocess.CREATE_NEW_PROCESS_GROUP

    # 方式 1：PowerShell Start-Process（在多数环境下最稳，避免脚本路径问题）
    pwsh = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    if not os.path.isfile(pwsh):
        pwsh = "powershell"
    ps_arg_list = ",".join(f"'{a}'" for a in args)
    ps_cmd = f'Start-Process -FilePath "{chrome_exe}" -ArgumentList {ps_arg_list}'
    try:
        subprocess.run(
            [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
            cwd=chrome_dir,
            capture_output=True,
            timeout=10,
        )
        logger.info("[MCP Registry] 已通过 PowerShell Start-Process 启动 Chrome")
        return True
    except Exception as e:
        logger.warning("[MCP Registry] PowerShell Start-Process 失败: %s", e)

    # 方式 2：Python Popen 直接启动
    try:
        subprocess.Popen(
            [chrome_exe, *args],
            cwd=chrome_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags if flags else 0,
        )
        logger.info("[MCP Registry] 已通过 Python Popen 启动 Chrome")
        return True
    except Exception as e:
        logger.warning("[MCP Registry] Python Popen 启动 Chrome 失败: %s", e)

    # 方式 3：cmd start（部分环境对 start 支持更好）
    try:
        subprocess.Popen(
            ["cmd", "/c", "start", "ChromeBoss", chrome_exe] + args,
            cwd=chrome_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags if flags else 0,
        )
        logger.info("[MCP Registry] 已通过 cmd start 启动 Chrome")
        return True
    except Exception as e:
        logger.warning("[MCP Registry] cmd start 启动 Chrome 失败: %s", e)

    # 方式 4：回退到项目内 PowerShell 脚本
    for rel_path in ["scripts/launch_chrome_debug.ps1", "skills_repo/plugin/scripts/launch_chrome_debug.ps1"]:
        launch_script = project_root / rel_path.replace("/", os.sep)
        if launch_script.exists():
            try:
                subprocess.run(
                    [pwsh, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(launch_script), boss_login_url],
                    cwd=str(project_root),
                    capture_output=True,
                    timeout=15,
                )
                logger.info("[MCP Registry] 已通过 PowerShell 脚本启动 Chrome")
                return True
            except Exception as e:
                logger.warning("[MCP Registry] PowerShell 脚本启动 Chrome 失败: %s", e)
    return False


def _invoke_atom_post_job_boss_local(
    cdp_url: str = "",
    jd_config_path: str = "",
    jd_config: str | dict | None = None,
) -> str:
    """L3 本地执行 atom_post_job_boss。若传 jd_config(JSON)，写入 data/{职位}/jd.json 再发布。
    发布前自动检测 Chrome：未连接则启动 Chrome + 打开 Boss 登录页，提示 HR 扫码后回复「已登录」再发布。"""
    import time

    _proj = Path(__file__).resolve().parent.parent.parent
    plugin_root = _proj / "skills_repo" / "plugin" / "2-track-a-atomic-mcp"
    cdp = (cdp_url or "http://127.0.0.1:9222").rstrip("/")
    if not plugin_root.exists():
        return json.dumps({"success": False, "posted": False, "error": f"plugin 路径不存在: {plugin_root}"}, ensure_ascii=False)

    # ========== 步骤1（必须最先执行，HR 同意后自动立即执行）：存储配置、创建文件夹 ==========
    # 不打开 Chrome，先完成：data/{岗位名}/、复制 jd_to_publish.example.json → jd.json 并填写、pending/processed/result、排行榜_Summary.md
    if jd_config:
        cfg = jd_config
        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg)
            except json.JSONDecodeError:
                return json.dumps({"success": False, "posted": False, "error": "jd_config 不是有效 JSON"}, ensure_ascii=False)
        if not isinstance(cfg, dict) or not (cfg.get("job_title") or cfg.get("jd_full")):
            return json.dumps({"success": False, "posted": False, "error": "jd_config 必须包含 job_title 和 jd_full，且需与 HR 确认的岗位名称完全一致"}, ensure_ascii=False)
        job_title = (cfg.get("job_title") or "").strip()
        if not job_title:
            return json.dumps({"success": False, "posted": False, "error": "jd_config 中 job_title 不能为空，必须与 HR 确认的岗位名称一致"}, ensure_ascii=False)
        import sys
        if str(plugin_root) not in sys.path:
            sys.path.insert(0, str(plugin_root))
        from tools.hr_data_paths import init_job_jd_from_template
        logger.info("[MCP Registry] 步骤1：HR 已确认，正在自动创建 data/%s/、复制模板填 jd.json、创建 pending/processed/result", job_title)
        jd_path = init_job_jd_from_template(job_title, overrides=cfg)
        jd_config_path = str(jd_path)
        logger.info("[MCP Registry] 步骤1 完成：配置已保存至 %s", jd_config_path)
    elif not (jd_config_path or "").strip():
        return json.dumps({"success": False, "posted": False, "error": "发布职位必须传入 jd_config（HR 确认的 JSON）或 jd_config_path（指向 data/{岗位名}/jd.json）"}, ensure_ascii=False)
    else:
        # 仅传 jd_config_path 时，必须指向 data/{职位}/jd.json，禁止使用 data/jd_to_publish.json
        p = Path(jd_config_path.strip())
        if "jd_to_publish" in p.parts or (p.name != "jd.json" and "jd_to_publish" in str(p)):
            return json.dumps({"success": False, "posted": False, "error": "jd_config_path 必须指向 data/{岗位名}/jd.json，禁止使用 jd_to_publish.json"}, ensure_ascii=False)
        if not p.exists():
            return json.dumps({"success": False, "posted": False, "error": f"jd_config_path 不存在: {jd_config_path}"}, ensure_ascii=False)

    # ========== 步骤2：连接 Chrome 并执行职位发布 ==========
    logger.info("[MCP Registry] 步骤2：正在连接 Chrome 并发布职位")
    if not _check_chrome_cdp(cdp):
        logger.info("[MCP Registry] Chrome 未连接，启动 Chrome 并打开 Boss 登录页")
        _launch_chrome_with_boss_login(_proj)
        for _ in range(12):  # 最多等 12 秒，Chrome 启动需时
            time.sleep(1)
            if _check_chrome_cdp(cdp):
                break
        if not _check_chrome_cdp(cdp):
            return json.dumps({
                "success": False,
                "posted": False,
                "error": "[需要登录] 已启动 Chrome 并打开 Boss 直聘登录页。请扫码登录后回复「已登录」或「继续发布」，我将立即为您发布职位。",
                "need_login": True,
            }, ensure_ascii=False)
    import sys
    if str(plugin_root) not in sys.path:
        sys.path.insert(0, str(plugin_root))
    _jd_path = (jd_config_path or "").strip()
    try:
        import sys
        if str(plugin_root) not in sys.path:
            sys.path.insert(0, str(plugin_root))
        from tools.atom_post_job_boss import atom_post_job_boss
        result = atom_post_job_boss(
            cdp_url=cdp,
            jd_config_path=_jd_path,
        )
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        err_str = str(e)
        if "connect" in err_str.lower() or "9222" in err_str or "目标计算机" in err_str or "refused" in err_str or "ECONNREFUSED" in err_str:
            _launch_chrome_with_boss_login(_proj)
            for _ in range(12):
                time.sleep(1)
                if _check_chrome_cdp(cdp):
                    try:
                        from tools.atom_post_job_boss import atom_post_job_boss
                        result = atom_post_job_boss(cdp_url=cdp, jd_config_path=_jd_path)
                        return json.dumps(result, ensure_ascii=False)
                    except Exception as retry_e:
                        err_str = str(retry_e)
                    break
            return json.dumps({
                "success": False,
                "posted": False,
                "error": "[需要登录] 已启动 Chrome 并打开 Boss 直聘登录页。请扫码登录后回复「已登录」或「继续发布」，我将立即为您发布职位。",
                "need_login": True,
            }, ensure_ascii=False)
        logger.warning("[MCP Registry] atom_post_job_boss 本地执行失败: %s", e)
        return json.dumps({"success": False, "posted": False, "error": err_str}, ensure_ascii=False)


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


def _invoke_stop_automated_recruitment_local(job_name: str = "") -> str:
    """L3 本地执行 stop_automated_recruitment，移除无人值守招聘定时任务。job_name 为空则停止所有岗位。"""
    try:
        from l3_node.recruitment_scheduler import remove_scheduled_job, list_scheduled_jobs, set_recruitment_stopped
        jn = (job_name or "").strip()
        if jn:
            result = remove_scheduled_job(jn)
            return json.dumps(result, ensure_ascii=False)
        # 停止所有：先设全局停止标志，再移除各岗位任务，阻止后续定时任务执行
        set_recruitment_stopped(True)
        jobs = list_scheduled_jobs()
        removed = []
        for j in jobs:
            folder = (j.get("job_folder") or "").strip()
            if folder:
                r = remove_scheduled_job(folder)
                if r.get("ok"):
                    removed.extend(r.get("removed", []))
        return json.dumps({"ok": True, "message": "已停止所有无人值守招聘任务", "removed": removed}, ensure_ascii=False)
    except Exception as e:
        logger.warning("[MCP Registry] stop_automated_recruitment 失败: %s", e)
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


def _invoke_add_automated_recruitment_task_local(
    job_name: str = "",
    analyze_threshold: int = 4,
    analyze_interval_hours: float = 0.05,
    jd_config_path: str = "",
) -> str:
    """L3 本地执行 add_automated_recruitment_task，向调度器添加岗位。
    jd 存于 data/{职位}/jd.json。
    流程：推荐牛人每15分钟，满3人打招呼→20秒后抓简历，满3份简历→Agent讨论并结束。"""
    if not (job_name or "").strip():
        return json.dumps({"ok": False, "error": "job_name 不能为空"}, ensure_ascii=False)
    _proj = Path(__file__).resolve().parent.parent.parent
    plugin_root = _proj / "skills_repo" / "plugin" / "2-track-a-atomic-mcp"
    if plugin_root.exists() and str(plugin_root) not in __import__("sys").path:
        __import__("sys").path.insert(0, str(plugin_root))
    from tools.hr_data_paths import get_job_jd_path, ensure_job_dirs, init_job_jd_from_template
    jn = (job_name or "").strip()
    ensure_job_dirs(jn)
    jd_path = get_job_jd_path(jn)
    if not jd_path.exists():
        init_job_jd_from_template(jn, overrides={"job_title": jn})
    default_jd_path = str(get_job_jd_path(jn))
    job_config = {
        "job_name": jn,
        "analyze_threshold": int(analyze_threshold) if analyze_threshold is not None else 4,
        "analyze_interval_hours": float(analyze_interval_hours) if analyze_interval_hours is not None else 0.05,
        "jd_config_path": (jd_config_path or "").strip() or default_jd_path,
        "cdp_url": "http://127.0.0.1:9222",
        "max_count": 50,
        "filter_tab": "全部",
        "request_resume": True,
        "use_all_positions": True,
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
        plugin_data = _proj / "skills_repo" / "plugin" / "data"
        for base, sub in [
            (_l3_vol, raw_norm),
            (plugin_data, raw_norm),
            (plugin_data, p.name or raw_norm),
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
        local_names = {"read_file", "atom_post_job_boss", "atom_greet_recommend_boss", "add_automated_recruitment_task", "stop_automated_recruitment"}

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
                # 兜底：职位发布后 LLM 可能未传 job_name，从 data/*/jd.json 读取 job_title（按修改时间取最新）
                if not (job_name or "").strip():
                    _proj = Path(__file__).resolve().parent.parent.parent
                    data_root = _proj / "skills_repo" / "plugin" / "data"
                    if data_root.exists():
                        candidates = [(d / "jd.json", (d / "jd.json").stat().st_mtime) for d in data_root.iterdir() if d.is_dir() and (d / "jd.json").exists()]
                        for jd_path, _ in sorted(candidates, key=lambda x: -x[1]):
                            try:
                                jd_data = json.loads(jd_path.read_text(encoding="utf-8"))
                                if isinstance(jd_data, dict) and jd_data.get("job_title"):
                                    job_name = str(jd_data["job_title"]).strip()
                                    logger.info("[MCP Registry] add_automated_recruitment_task job_name 从 %s 兜底: %s", jd_path.name, job_name)
                                    break
                            except Exception as e:
                                logger.debug("[MCP Registry] 读取 %s 兜底失败: %s", jd_path, e)
                analyze_threshold = arguments.get("analyze_threshold", 4)
                analyze_interval_hours = arguments.get("analyze_interval_hours", 0.05)
                jd_config_path = arguments.get("jd_config_path", "")
                logger.info("[MCP Registry] L3 本地执行 add_automated_recruitment_task job_name=%s", job_name or "(空)")
                return _invoke_add_automated_recruitment_task_local(
                    job_name=str(job_name).strip() if job_name else "",
                    analyze_threshold=int(analyze_threshold) if analyze_threshold is not None else 4,
                    analyze_interval_hours=float(analyze_interval_hours) if analyze_interval_hours is not None else 0.05,
                    jd_config_path=str(jd_config_path) if jd_config_path else "",
                )

            if raw_name == "stop_automated_recruitment":
                job_name = (arguments.get("job_name", arguments.get("input", "")) or "").strip()
                return _invoke_stop_automated_recruitment_local(job_name=job_name)

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
