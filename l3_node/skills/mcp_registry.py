"""
Jachin Nexus V2 - L3 MCP 工具桥接器

合并本机 stdio MCP、l3_mcp_cache 动态包与 L3 内置工具，维护 known_mcp_tools；
向模型提供 OpenAI/Anthropic 标准 tools 格式。

本机未命中时 ``invoke_via_l2`` → L2 ``POST /api/v2/mcp/invoke``（TaskManager：Pull / HTTP，载荷侧由 L2 签发 Task Token）。
规格：docs/MCP_EXECUTION_MODEL.md、docs/ARCHITECTURE_L3_MCP_HOST_AND_L2_TASK_MANAGER.md。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

from l3_node.paths import get_app_root

logger = logging.getLogger(__name__)

# 官方 mcp-server-fetch 等要求 arguments 含 url；模型 ReAct 输出损坏时 JSON 解析会得到 {} 或 {"url":""} 而无可用 url
_FETCH_URL_IN_JSON = re.compile(r'"url"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', re.I)
_URL_FROM_POS = re.compile(
    r"https?://[a-zA-Z0-9][-a-zA-Z0-9.]{0,253}[a-zA-Z0-9](?::\d+)?(?:/[^\s\"'`{}>]*)?",
    re.I,
)


def extract_http_url_from_corrupted_text(s: str) -> str:
    """从重复/粘连的 ReAct 输出中提取首个可用 http(s) URL。"""
    if not (s or "").strip():
        return ""
    s = s.strip()
    m = _FETCH_URL_IN_JSON.search(s)
    if m:
        try:
            raw = m.group(1).replace("\\\"", '"').strip()
            if raw.startswith("http"):
                return raw
        except Exception:
            pass
    idx = max(s.rfind("https://"), s.rfind("http://"))
    if idx >= 0:
        tail = s[idx:]
        m2 = _URL_FROM_POS.match(tail)
        if m2:
            return m2.group(0).rstrip(".,;)]}>\"'")
    if re.search(r"python\.org", s, re.I):
        return "https://www.python.org"
    return ""


def normalize_mcp_fetch_arguments(
    arguments: dict[str, Any],
    *,
    fallback_text: str = "",
) -> dict[str, Any]:
    """
    补全 fetch 的 url。优先从原始 Action Input 整段文本恢复（解析结果常为 {} 或残缺 JSON）。
    """
    out = dict(arguments) if isinstance(arguments, dict) else {}
    u = (out.get("url") or "").strip()
    if u:
        if u.count("://") > 1:
            fixed = extract_http_url_from_corrupted_text(u)
            if fixed:
                out["url"] = fixed
                return out
        out["url"] = u
        return out
    candidates: list[str] = []
    ft = (fallback_text or "").strip()
    if ft:
        candidates.append(ft)
    for v in out.values():
        if isinstance(v, str) and v.strip():
            candidates.append(v.strip())
    try:
        dumped = json.dumps(out, ensure_ascii=False)
        if dumped and dumped not in ("{}", "null"):
            candidates.append(dumped)
    except Exception:
        pass
    seen: set[str] = set()
    for c in candidates:
        if c in seen:
            continue
        seen.add(c)
        got = extract_http_url_from_corrupted_text(c)
        if got:
            out["url"] = got
            return out
    return out


# 默认 L2 地址（可从 l2_gateway_config.json 读取）
DEFAULT_L2_BASE_URL = "http://localhost:18888"
MCP_TOOLS_PREFIX = "mcp:"
L3_MCP_CACHE = Path.home() / ".jachin" / "l3_mcp_cache"

# L3 本地 MCP 工具（不依赖 L2，下放至 L3 执行）
L3_LOCAL_MCP_TOOLS: list[dict[str, Any]] = [
    {
        "id": "mcp:read_file",
        "label": "mcp:read_file",
        "desc": "[L3 本地] 读取文件内容。支持 .md/.txt 及 .pdf 文本提取。路径需在 workspace、client_volumes、data/hr_resumes、config/skills/.../hr_jds 下。",
        "params": ["path"],
    },
    {
        "id": "mcp:atom_post_job_boss",
        "label": "mcp:atom_post_job_boss",
        "desc": "[L3 本地] **仅**在 Boss 上发帖（与打招呼/收网调度无关）。jd.json 在 boss_post_published=true 后**默认拒绝重复发帖**；改调度数字请用 hr_scheduler_send_confirm_prompt / add_automated_recruitment_task 或 Lark 改批次。若 HR 明确要求**重新发布**另一职位或同岗二次发帖，传 force_republish=true。",
        "params": ["cdp_url", "jd_config_path", "jd_config", "force_republish"],
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
        "desc": "[L3 本地] **真正注册** APScheduler：① **enable_greet_recommend=true**（jd **未写该键时默认 true**，与飞书调度确认单一致）时 **推荐↔收简历** 严格交替 + 规则引擎透析；② **enable_greet_recommend=false** 时**只定时收沟通简历**；③ **greet_only_total_target>0** 则仅打招呼。**resume_collect_target 与 analyze_threshold 会收敛为同一「份数」**（改目标即改透析触发份数）。**岗位名**可配合 **jd_select**。参数可省略读 jd.json。",
        "params": [
            "job_name",
            "analyze_threshold",
            "jd_config_path",
            "enable_greet_recommend",
            "resume_collect_target",
            "max_count_per_harvest_tick",
            "greet_target",
            "greet_harvest_switch_interval_minutes",
            "recommend_interval_minutes",
            "greet_only_total_target",
            "greet_only_interval_minutes",
            "jd_select",
        ],
    },
    {
        "id": "mcp:hr_scheduler_send_confirm_prompt",
        "label": "mcp:hr_scheduler_send_confirm_prompt",
        "desc": "[L3 本地] 职位发布成功后调用：把无人值守默认参数写入 jd.json、标记待确认、向飞书发「调度参数单」。**不启动**定时任务。重点参数：greet_harvest_switch_interval_minutes（推荐↔收简历轮换间隔，默认 10 分钟）、greet_target、resume_collect_target、analyze_threshold（攒够多少份待评价简历触发透析，非按分钟）。已移除 MCP 侧 auto_analyze。",
        "params": [
            "job_name",
            "jd_config_path",
            "greet_harvest_switch_interval_minutes",
            "greet_target",
            "max_count_per_harvest_tick",
            "analyze_threshold",
            "resume_collect_target",
            "enable_greet_recommend",
        ],
    },
    {
        "id": "mcp:stop_automated_recruitment",
        "label": "mcp:stop_automated_recruitment",
        "desc": "[L3 本地] 停止无人值守招聘流程。当 HR 说「关闭」「停止」「取消」招聘时调用。job_name 为空则停止所有岗位的定时任务。",
        "params": ["job_name"],
    },
    {
        "id": "mcp:get_recruitment_job_memory",
        "label": "mcp:get_recruitment_job_memory",
        "desc": "[L3 本地] 读取岗位在磁盘与 scheduler_state 上的招聘历史（pending PDF 数、已生成分析报告数、待透析估算、上次调度参数、定时是否在跑等），返回 hr_brief_zh 供向 HR 宣读。HR 隔一段时间再回到某岗位（如先招 Python 再招 Java 又招 Python）时应先调用，确认续接同一 data 目录还是新开任务。",
        "params": ["job_name"],
    },
    {
        "id": "mcp:list_hr_scheduler_suspended_jobs",
        "label": "mcp:list_hr_scheduler_suspended_jobs",
        "desc": "[L3 本地] 列出因**换岗抢占**挂起、可恢复的岗位（scheduler_state.json 中 scheduler_suspended）。无参数。",
        "params": [],
    },
    {
        "id": "mcp:resume_hr_job_scheduler",
        "label": "mcp:resume_hr_job_scheduler",
        "desc": "[L3 本地] 按 **job_folder（数据目录键，优先）** 或 job_name 恢复挂起的无人值守；会 `remove_all` 后只注册该岗，与 Boss 单页互斥。换岗后想切回上一岗时用。",
        "params": ["job_folder", "job_name"],
    },
    # BI 战报通用 MCP 工具（docs/bi_daily_report/）
    {
        "id": "mcp:atom_web_scraper",
        "label": "mcp:atom_web_scraper",
        "desc": "[L3 本地] 通用网页抓取器。传入 url、output_path、config，抓取表格/JSON 并保存到 client_volumes/bi_data/raw/。",
        "params": ["url", "output_path", "config", "cdp_url"],
    },
    {
        "id": "mcp:atom_bi_natural_retention_collect",
        "label": "mcp:atom_bi_natural_retention_collect",
        "desc": "[L3 本地] 仅抓取「新增用户留存对比」「新增付费留存对比」两表，按四段日期填对比区间，CSV 写入 raw_natural/。参数：period1_start/end、period2_start/end（YYYY-MM-DD）；可选 raw_dir、base_url、cdp_url、auto_ingest、full_spa_config（含 direct_urls 时走直链）。",
        "params": [
            "period1_start",
            "period1_end",
            "period2_start",
            "period2_end",
            "raw_dir",
            "base_url",
            "cdp_url",
            "auto_ingest",
            "full_spa_config",
        ],
    },
    {
        "id": "mcp:atom_lark_notifier",
        "label": "mcp:atom_lark_notifier",
        "desc": "[L3 本地] 通用飞书播报员。传入 webhook_url 或 chat_id、markdown_content、title，发送 Markdown 消息。",
        "params": ["webhook_url", "markdown_content", "title", "chat_id"],
    },
    {
        "id": "mcp:atom_email_sender",
        "label": "mcp:atom_email_sender",
        "desc": "[L3 本地] 通用邮件发射器。传入 smtp_config、to_addrs、subject、body、attachment_paths，发送邮件。",
        "params": ["smtp_config", "to_addrs", "subject", "body", "attachment_paths"],
    },
    {
        "id": "mcp:atom_bi_project_context",
        "label": "mcp:atom_bi_project_context",
        "desc": "[L3 本地] 从配置的 Lark 知识库 Wiki 链接拉取多维表/文档/表格、子页面及文内 Wiki 链接，写入 docs/bi_daily_report/bi_project/，供 BI 理解项目背景。可选参数见 config/mcps/atom_bi_project_context/config.yaml。",
        "params": ["config", "wiki_urls", "output_dir_relative", "max_records_per_table", "max_discovered_links", "recurse_children_depth"],
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
    force_republish: bool = False,
) -> str:
    """L3 本地执行 atom_post_job_boss。若传 jd_config(JSON)，写入 data/{职位}/jd.json 再发布。
    发布前自动检测 Chrome：未连接则启动 Chrome + 打开 Boss 登录页，提示 HR 扫码后回复「已登录」再发布。"""
    import time

    _proj = get_app_root()
    from l3_node.hr_loader import _get_hr_recruitment_plugin_root
    plugin_root = _get_hr_recruitment_plugin_root()
    cdp = (cdp_url or "http://127.0.0.1:9222").rstrip("/")
    if not plugin_root or not plugin_root.exists():
        return json.dumps({"success": False, "posted": False, "error": "HR 招聘 MCP 包未找到，请从 L1 订阅 com.jachin.hr.recruitment"}, ensure_ascii=False)

    # ========== 步骤1（必须最先执行，HR 同意后自动立即执行）：存储配置、创建文件夹 ==========
    # 不打开 Chrome，先完成：data/{岗位名}/、复制 jd_to_publish.example.json → jd.json 并填写、pending/processed/result、排行榜_Summary.md
    force_rep = bool(force_republish)
    if jd_config:
        cfg = jd_config
        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg)
            except json.JSONDecodeError:
                return json.dumps({"success": False, "posted": False, "error": "jd_config 不是有效 JSON"}, ensure_ascii=False)
        if isinstance(cfg, dict):
            force_rep = force_rep or bool(cfg.get("force_republish"))
        if not isinstance(cfg, dict) or not (cfg.get("job_title") or cfg.get("jd_full")):
            return json.dumps({"success": False, "posted": False, "error": "jd_config 必须包含 job_title 和 jd_full，且需与 HR 确认的岗位名称完全一致"}, ensure_ascii=False)
        job_title = (cfg.get("job_title") or "").strip()
        if not job_title:
            return json.dumps({"success": False, "posted": False, "error": "jd_config 中 job_title 不能为空，必须与 HR 确认的岗位名称一致"}, ensure_ascii=False)
        import sys
        if str(plugin_root) not in sys.path:
            sys.path.insert(0, str(plugin_root))
        from tools.boss_utils import canonicalize_boss_job_select, strip_leading_recruitment_verbs_for_job_chat
        from tools.hr_data_paths import init_job_jd_from_template, resolve_recruitment_data_folder_key

        ov = dict(cfg) if isinstance(cfg, dict) else {}
        sel_raw = (ov.get("jd_select") or "").strip()
        canon_sel = (canonicalize_boss_job_select(sel_raw) or sel_raw).strip() if sel_raw else ""
        jt_fk = strip_leading_recruitment_verbs_for_job_chat((ov.get("job_title") or job_title or "").strip())
        data_fk = resolve_recruitment_data_folder_key(
            jd_select_canon=canon_sel,
            job_title=jt_fk,
            jd_doc=ov,
        )
        logger.info(
            "[MCP Registry] 步骤1：按目录键=%r 创建/合并 jd（与飞书 persist / add_automated 一致），非纯 job_title 文件夹",
            data_fk,
        )
        jd_path = init_job_jd_from_template(job_title, overrides=cfg, data_folder_key=data_fk)
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

    def _boss_post_transient(err: str) -> bool:
        """可重试：页面/iframe/超时类；不可重试：配置与业务校验。"""
        if not err:
            return False
        if any(
            x in err
            for x in (
                "JD 配置为空",
                "不是有效 JSON",
                "jd_config_path 不存在",
                "必须包含 job_title",
                "禁止使用 jd_to_publish",
            )
        ):
            return False
        if any(
            x in err
            for x in (
                "未找到职位列表",
                "iframe",
                "未找到页面",
                "未找到浏览器上下文",
                "timeout",
                "Timeout",
                "detached",
                "Target page",
                "closed",
                "导航",
                "net::",
                "ECONNRESET",
            )
        ):
            return True
        el = err.lower()
        if "timeout" in el or "network" in el:
            return True
        return False

    import os as _os

    _max_post = max(1, min(8, int(_os.environ.get("BOSS_POST_MAX_ATTEMPTS", "3"))))
    _delay_post = float(_os.environ.get("BOSS_POST_RETRY_DELAY_SEC", "3"))

    try:
        import sys
        if str(plugin_root) not in sys.path:
            sys.path.insert(0, str(plugin_root))
        from tools.atom_post_job_boss import atom_post_job_boss

        result: dict = {}
        last_err = ""
        for attempt in range(1, _max_post + 1):
            result = atom_post_job_boss(
                cdp_url=cdp,
                jd_config_path=_jd_path,
                force_republish=force_rep,
            )
            if result.get("posted", False) or result.get("already_published"):
                if attempt > 1:
                    logger.info("[MCP Registry] atom_post_job_boss 第 %d 次尝试成功", attempt)
                return json.dumps(result, ensure_ascii=False)
            last_err = str(result.get("error", "") or "")
            if result.get("need_login"):
                break
            if attempt < _max_post and _boss_post_transient(last_err):
                logger.warning(
                    "[StrategyShift] domain=hr step=atom_post_job_boss attempt=%d/%d backoff=%.1fs reason=transient err=%s",
                    attempt,
                    _max_post,
                    _delay_post,
                    last_err[:200],
                )
                time.sleep(_delay_post)
                continue
            break
        if not result.get("posted", False) and not result.get("already_published"):
            logger.warning("[MCP Registry] atom_post_job_boss 返回未发布: %s", result.get("error", "未知"))
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
                        result = atom_post_job_boss(
                            cdp_url=cdp, jd_config_path=_jd_path, force_republish=force_rep
                        )
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
    """L3 本地执行 atom_greet_recommend_boss，直接调用 HR 招聘包工具。"""
    from l3_node.hr_loader import _get_hr_recruitment_plugin_root
    plugin_root = _get_hr_recruitment_plugin_root()
    if not plugin_root or not plugin_root.exists():
        return json.dumps({"success": False, "greeted_count": 0, "error": "HR 招聘 MCP 包未找到，请从 L1 订阅 com.jachin.hr.recruitment"}, ensure_ascii=False)
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
        sched = __import__("l3_node.hr_loader", fromlist=["get_recruitment_scheduler"]).get_recruitment_scheduler()
        if not sched:
            return json.dumps({"ok": False, "error": "HR 招聘 MCP 包未找到，请从 L1 订阅 com.jachin.hr.recruitment"}, ensure_ascii=False)
        remove_scheduled_job, list_scheduled_jobs, set_recruitment_stopped = sched.remove_scheduled_job, sched.list_scheduled_jobs, sched.set_recruitment_stopped
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


def _invoke_atom_bi_natural_retention_collect_local(
    period1_start: str = "",
    period1_end: str = "",
    period2_start: str = "",
    period2_end: str = "",
    raw_dir: str = "",
    base_url: str = "",
    cdp_url: str = "",
    auto_ingest: bool = False,
    full_spa_config: dict | None = None,
) -> str:
    """L3 本地：自然留存对比两表抓取 → raw_natural。"""
    try:
        from l3_node.mcp_tools.bi.tool_natural_retention_collect import atom_bi_natural_retention_collect_mcp

        return atom_bi_natural_retention_collect_mcp(
            period1_start=period1_start or "",
            period1_end=period1_end or "",
            period2_start=period2_start or "",
            period2_end=period2_end or "",
            raw_dir=raw_dir or "",
            base_url=base_url or "",
            cdp_url=cdp_url or "",
            auto_ingest=bool(auto_ingest),
            full_spa_config=full_spa_config if isinstance(full_spa_config, dict) else None,
        )
    except Exception as e:
        logger.warning("[MCP Registry] atom_bi_natural_retention_collect 失败: %s", e)
        import json as _json

        return _json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


def _invoke_atom_web_scraper_local(
    url: str = "",
    output_path: str = "",
    config: dict | None = None,
) -> str:
    """L3 本地执行 atom_web_scraper，路由到 l3_node.mcp_tools.bi.tool_web_scraper。"""
    try:
        from l3_node.mcp_tools.bi.tool_web_scraper import harvest_table_data
        from l3_node.mcp_tools.bi.paths import get_bi_raw_dir
        _path = output_path.strip() if output_path else str(get_bi_raw_dir() / "placeholder.csv")
        result = harvest_table_data(url=url or "", output_path=_path, config=config or {})
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.warning("[MCP Registry] atom_web_scraper 失败: %s", e)
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


def _invoke_atom_lark_notifier_local(
    webhook_url: str = "",
    markdown_content: str = "",
    title: str = "",
    chat_id: str = "",
) -> str:
    """L3 本地执行 atom_lark_notifier，路由到 l3_node.mcp_tools.bi.tool_lark_notifier。"""
    try:
        from l3_node.mcp_tools.bi.tool_lark_notifier import send_lark_markdown
        result = send_lark_markdown(
            webhook_url=webhook_url or "",
            markdown_content=markdown_content or "",
            title=title or None,
            chat_id=chat_id or None,
        )
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.warning("[MCP Registry] atom_lark_notifier 失败: %s", e)
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


def _invoke_atom_email_sender_local(
    smtp_config: dict | None = None,
    to_addrs: list | None = None,
    subject: str = "",
    body: str = "",
    attachment_paths: list | None = None,
) -> str:
    """L3 本地执行 atom_email_sender，路由到 l3_node.mcp_tools.bi.tool_email_sender。"""
    try:
        from l3_node.mcp_tools.bi.tool_email_sender import send_email_with_attachment
        result = send_email_with_attachment(
            smtp_config=smtp_config or {},
            to_addrs=to_addrs or [],
            subject=subject or "",
            body=body or "",
            attachment_paths=attachment_paths or [],
        )
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.warning("[MCP Registry] atom_email_sender 失败: %s", e)
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


def _invoke_atom_bi_project_context_local(arguments: dict[str, Any] | None = None) -> str:
    """L3 本地执行 atom_bi_project_context，路由到 l3_node.mcp_tools.bi.tool_bi_project_context。"""
    try:
        from l3_node.mcp_tools.bi.tool_bi_project_context import sync_bi_project_context

        args = dict(arguments or {})
        nested = args.pop("config", None) if isinstance(args.get("config"), dict) else None
        cfg: dict[str, Any] = dict(nested or {})
        cfg.update(args)
        result = sync_bi_project_context(config=cfg)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logger.warning("[MCP Registry] atom_bi_project_context 失败: %s", e)
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


# 供 agent_core 在 Final Answer 后处理 Markdown 表：与最近一次成功注册任务的份数对齐
last_add_automated_recruitment_task_payload: dict[str, Any] | None = None


def clear_last_add_automated_recruitment_task_payload() -> None:
    """每轮 ReAct 开始时清空，避免沿用上一次的收网份数。"""
    global last_add_automated_recruitment_task_payload
    last_add_automated_recruitment_task_payload = None


def _update_last_add_automated_recruitment_task_payload(obj: dict[str, Any]) -> None:
    """从插件返回的 JSON 更新快照；失败或未含收网份数时清空。"""
    global last_add_automated_recruitment_task_payload
    last_add_automated_recruitment_task_payload = None
    if not isinstance(obj, dict) or not obj.get("ok"):
        return
    try:
        go = int(obj.get("greet_only_total_target") or 0)
    except (TypeError, ValueError):
        go = 0
    if go > 0:
        return
    try:
        n = int(obj.get("resume_collect_target") or obj.get("analyze_threshold") or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return
    last_add_automated_recruitment_task_payload = {
        "ok": True,
        "resume_collect_target": n,
        "analyze_threshold": n,
        "job_name": obj.get("job_name"),
        "enable_greet_recommend": obj.get("enable_greet_recommend"),
    }


def _wrap_add_automated_recruitment_task_reply(raw: str) -> str:
    """Agent / 飞书展示用人话；完整 JSON 仅写日志，避免 HR 看到原始字段。"""
    raw = (raw or "").strip()
    if not raw:
        return "招聘定时任务无返回，请查看 L3 日志。"
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if not isinstance(obj, dict):
        return raw
    _update_last_add_automated_recruitment_task_payload(obj)
    try:
        logger.info("[MCP Registry] add_automated_recruitment_task 技术 JSON: %s", raw[:16000])
    except Exception:
        pass
    try:
        from l3_node.hr_tool_reply_zh import format_add_automated_recruitment_task_result_for_hr

        return format_add_automated_recruitment_task_result_for_hr(obj)
    except Exception as e:
        logger.debug("[MCP Registry] 人话封装失败，回退 JSON: %s", e)
        return raw


def _invoke_add_automated_recruitment_task_local(
    job_name: str = "",
    analyze_threshold: int | None = None,
    jd_config_path: str = "",
    enable_greet_recommend: bool | None = None,
    resume_collect_target: int | None = None,
    max_count_per_harvest_tick: int | None = None,
    greet_target: int | None = None,
    jd_select: str = "",
    greet_harvest_switch_interval_minutes: int | None = None,
    recommend_interval_minutes: int | None = None,
    greet_only_total_target: int | None = None,
    greet_only_interval_minutes: int | None = None,
) -> str:
    """L3 本地：合并 jd_select 后委托插件 add_automated_recruitment_task（参数 None 时从 jd.json 读）。"""
    from l3_node.hr_loader import _get_hr_recruitment_plugin_root

    plugin_root = _get_hr_recruitment_plugin_root()
    if not plugin_root or not plugin_root.exists():
        return _wrap_add_automated_recruitment_task_reply(
            json.dumps(
                {"ok": False, "error": "HR 招聘 MCP 包未找到，请从 L1 订阅 com.jachin.hr.recruitment"},
                ensure_ascii=False,
            )
        )
    if str(plugin_root) not in __import__("sys").path:
        __import__("sys").path.insert(0, str(plugin_root))
    from tools.boss_utils import (
        canonicalize_boss_job_select,
        primary_job_title_from_boss_select_line,
        strip_leading_recruitment_verbs_for_job_chat,
    )
    from tools.hr_data_paths import (
        ensure_job_dirs_by_folder_key,
        get_job_jd_path_by_folder_key,
        infer_folder_key_from_job_display_name,
        init_job_jd_from_template,
        resolve_recruitment_data_folder_key,
    )
    from tools.add_automated_recruitment_task import add_automated_recruitment_task

    jn = strip_leading_recruitment_verbs_for_job_chat((job_name or "").strip())
    jdp_in = (jd_config_path or "").strip()
    jd_sel = (jd_select or "").strip()
    if jd_sel:
        _stripped = strip_leading_recruitment_verbs_for_job_chat(jd_sel)
        if _stripped:
            jd_sel = _stripped

    # 必须先读 jd.json：飞书 apply_job_select 已把指针指到新目录，LLM 仍常带上一岗的 jd_select/job_name
    jd_doc_for_fk: dict[str, Any] | None = None
    fk_from_ptr = ""
    if jdp_in:
        try:
            _jp = Path(jdp_in)
            if _jp.is_file() and _jp.name.lower() == "jd.json":
                _raw = json.loads(_jp.read_text(encoding="utf-8"))
                if isinstance(_raw, dict):
                    jd_doc_for_fk = _raw
                    _s0 = strip_leading_recruitment_verbs_for_job_chat(
                        (_raw.get("jd_select") or "").strip()
                    )
                    _sc = ((canonicalize_boss_job_select(_s0) or _s0).strip()) if _s0 else ""
                    _jt0 = (_raw.get("job_title") or "").strip()
                    if not _jt0 and _sc:
                        _jt0 = (primary_job_title_from_boss_select_line(_sc) or "").strip()
                    fk_from_ptr = (
                        resolve_recruitment_data_folder_key(
                            jd_select_canon=_sc, job_title=_jt0, jd_doc=_raw
                        )
                        or ""
                    )
                    if not fk_from_ptr and _jt0:
                        fk_from_ptr = infer_folder_key_from_job_display_name(_jt0, _raw) or ""
        except Exception as _e:
            logger.debug("[MCP Registry] 预读 jd.json 用于目录键失败: %s", _e)

    if jd_sel and fk_from_ptr:
        jd_sel_canon_llm = (canonicalize_boss_job_select(jd_sel) or jd_sel.strip()).strip()
        _jn_llm = jn
        _derived_llm = primary_job_title_from_boss_select_line(jd_sel_canon_llm) or _jn_llm
        fk_llm = (
            resolve_recruitment_data_folder_key(
                jd_select_canon=jd_sel_canon_llm,
                job_title=_derived_llm,
                jd_doc=None,
            )
            or ""
        )
        if not fk_llm and _derived_llm:
            fk_llm = infer_folder_key_from_job_display_name(_derived_llm, None) or ""
        if fk_llm and fk_from_ptr != fk_llm:
            logger.warning(
                "[MCP Registry] LLM jd_select 目录键=%r 与 jd_config_path 磁盘目录键=%r 不一致，"
                "以指针岗位（飞书选岗）为准，忽略 LLM 的 jd_select/job_name。",
                fk_llm,
                fk_from_ptr,
            )
            jd_sel = ""
            if jd_doc_for_fk:
                _s_disk = strip_leading_recruitment_verbs_for_job_chat(
                    (jd_doc_for_fk.get("jd_select") or "").strip()
                )
                _sc_disk = (
                    (canonicalize_boss_job_select(_s_disk) or _s_disk).strip() if _s_disk else ""
                )
                jn = strip_leading_recruitment_verbs_for_job_chat(
                    (jd_doc_for_fk.get("job_title") or "").strip()
                )
                if not jn and _sc_disk:
                    jn = (primary_job_title_from_boss_select_line(_sc_disk) or "").strip()

    # 显式 jd_select 时：以 Boss 选岗行左侧职位段作为展示用 job_name（避免 LLM 仍传指针旧岗短名）
    if jd_sel:
        derived = primary_job_title_from_boss_select_line(jd_sel)
        if derived and (not jn or jn.strip() != derived.strip()):
            logger.info(
                "[MCP Registry] jd_select 职位段=%r 与 job_name=%r 不一致，以选岗行为准",
                derived,
                jn or "(空)",
            )
            jn = derived

    jd_sel_canon = ""
    if jd_sel:
        jd_sel_canon = (canonicalize_boss_job_select(jd_sel) or jd_sel.strip())
    elif jd_doc_for_fk:
        _sel0 = strip_leading_recruitment_verbs_for_job_chat(
            (jd_doc_for_fk.get("jd_select") or "").strip()
        )
        if _sel0:
            jd_sel_canon = (canonicalize_boss_job_select(_sel0) or _sel0).strip()

    fk = resolve_recruitment_data_folder_key(
        jd_select_canon=jd_sel_canon,
        job_title=jn,
        jd_doc=jd_doc_for_fk,
    )
    if not fk and jn:
        fk = infer_folder_key_from_job_display_name(jn, jd_doc_for_fk)
    if not fk:
        return _wrap_add_automated_recruitment_task_reply(
            json.dumps(
                {
                    "ok": False,
                    "error": "无法解析岗位数据目录键：请提供 jd_select（含城市薪资）或完整 jd.json（job_title+城市+薪资）。",
                },
                ensure_ascii=False,
            )
        )
    want_jd_path = get_job_jd_path_by_folder_key(fk)
    if jdp_in:
        try:
            jp = Path(jdp_in)
            if jp.is_file() and jp.name.lower() == "jd.json":
                if jp.resolve() != want_jd_path.resolve():
                    logger.info(
                        "[MCP Registry] jd_config_path 与解析后的目录键不一致，丢弃 %s 改用 %s (fk=%r)",
                        jdp_in,
                        want_jd_path,
                        fk,
                    )
                    jdp_in = ""
                else:
                    logger.debug(
                        "[MCP Registry] jd_config_path 与目录键一致，保留 %s (fk=%r)",
                        jdp_in,
                        fk,
                    )
        except Exception as _e:
            logger.debug("[MCP Registry] 校验 jd_config_path 跳过: %s", _e)
            jdp_in = ""

    # job_name 仍为空：由插件从指针、jd.json 解析（与 Lark 简报同源）
    if not jn:
        return _wrap_add_automated_recruitment_task_reply(
            add_automated_recruitment_task(
                job_name="",
                analyze_threshold=analyze_threshold,
                jd_config_path=jdp_in,
                enable_greet_recommend=enable_greet_recommend,
                resume_collect_target=resume_collect_target,
                max_count_per_harvest_tick=max_count_per_harvest_tick,
                greet_target=greet_target,
                greet_harvest_switch_interval_minutes=greet_harvest_switch_interval_minutes,
                recommend_interval_minutes=recommend_interval_minutes,
                greet_only_total_target=greet_only_total_target,
                greet_only_interval_minutes=greet_only_interval_minutes,
            )
        )
    ensure_job_dirs_by_folder_key(fk)
    jd_path = get_job_jd_path_by_folder_key(fk)
    if not jd_path.exists():
        _ov: dict[str, Any] = {"job_title": jn}
        if jd_sel_canon:
            _ov["jd_select"] = jd_sel_canon
        init_job_jd_from_template(jn, overrides=_ov, data_folder_key=fk)
    elif jd_sel_canon:
        try:
            _existing = json.loads(jd_path.read_text(encoding="utf-8"))
            if isinstance(_existing, dict):
                _existing["jd_select"] = jd_sel_canon
                _existing["job_title"] = jn
                _existing["data_folder_key"] = fk
                jd_path.write_text(json.dumps(_existing, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("[MCP Registry] 合并 jd_select 到 jd.json 失败: %s", e)
    if jd_path.exists():
        try:
            from tools.hr_data_paths import repair_jd_identity_dict_and_persist

            repair_jd_identity_dict_and_persist(jd_path)
        except Exception as _e:
            logger.debug("[MCP Registry] repair jd 身份跳过: %s", _e)
    default_jd_path = str(get_job_jd_path_by_folder_key(fk))
    final_jdp = jdp_in or default_jd_path
    try:
        return _wrap_add_automated_recruitment_task_reply(
            add_automated_recruitment_task(
                job_name=jn,
                jd_config_path=final_jdp,
                analyze_threshold=analyze_threshold,
                enable_greet_recommend=enable_greet_recommend,
                resume_collect_target=resume_collect_target,
                max_count_per_harvest_tick=max_count_per_harvest_tick,
                greet_target=greet_target,
                greet_harvest_switch_interval_minutes=greet_harvest_switch_interval_minutes,
                recommend_interval_minutes=recommend_interval_minutes,
                greet_only_total_target=greet_only_total_target,
                greet_only_interval_minutes=greet_only_interval_minutes,
            )
        )
    except Exception as e:
        logger.warning("[MCP Registry] add_automated_recruitment_task 失败: %s", e)
        return _wrap_add_automated_recruitment_task_reply(
            json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
        )


def _invoke_hr_scheduler_send_confirm_prompt_local(arguments: dict[str, Any]) -> str:
    from l3_node.hr_loader import _get_hr_recruitment_plugin_root

    plugin_root = _get_hr_recruitment_plugin_root()
    if not plugin_root or not plugin_root.exists():
        return json.dumps({"ok": False, "error": "HR 招聘 MCP 包未找到"}, ensure_ascii=False)
    if str(plugin_root) not in __import__("sys").path:
        __import__("sys").path.insert(0, str(plugin_root))
    from tools.hr_scheduler_confirm_prompt import hr_scheduler_send_confirm_prompt

    def _oi(k: str) -> int | None:
        if k not in arguments:
            return None
        v = arguments.get(k)
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    def _ob(k: str) -> bool | None:
        if k not in arguments:
            return None
        v = arguments.get(k)
        if v is None:
            return None
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if s in ("0", "false", "no", "否", "关", "off"):
            return False
        if s in ("1", "true", "yes", "是", "开", "on"):
            return True
        return None

    jn = (arguments.get("job_name") or "").strip()
    eg = _ob("enable_greet_recommend")
    sw = _oi("greet_harvest_switch_interval_minutes")
    if sw is None:
        sw = _oi("recommend_interval_minutes")
    # 未传的键用 None，由 hr_scheduler_send_confirm_prompt 从 jd.json 读取，避免把默认 3/4 写回覆盖用户已改的数字
    return hr_scheduler_send_confirm_prompt(
        job_name=jn,
        jd_config_path=str(arguments.get("jd_config_path") or ""),
        greet_harvest_switch_interval_minutes=sw,
        greet_target=_oi("greet_target"),
        max_count_per_harvest_tick=_oi("max_count_per_harvest_tick"),
        analyze_threshold=_oi("analyze_threshold"),
        resume_collect_target=_oi("resume_collect_target"),
        enable_greet_recommend=eg,
    )


def _invoke_list_hr_scheduler_suspended_jobs_local() -> str:
    try:
        from l3_node.hr_loader import get_recruitment_scheduler

        sched = get_recruitment_scheduler()
        if not sched:
            return json.dumps(
                {"ok": False, "error": "HR 招聘 MCP 包未找到", "items": []},
                ensure_ascii=False,
            )
        fn = getattr(sched, "list_scheduler_suspended_jobs", None)
        if not callable(fn):
            return json.dumps(
                {"ok": False, "error": "调度器未提供 list_scheduler_suspended_jobs", "items": []},
                ensure_ascii=False,
            )
        items = fn()
        if not isinstance(items, list):
            items = []
        return json.dumps({"ok": True, "items": items, "count": len(items)}, ensure_ascii=False)
    except Exception as e:
        logger.warning("[MCP Registry] list_hr_scheduler_suspended_jobs 失败: %s", e)
        return json.dumps({"ok": False, "error": str(e), "items": []}, ensure_ascii=False)


def _invoke_resume_hr_job_scheduler_local(job_folder: str = "", job_name: str = "") -> str:
    try:
        from l3_node.hr_loader import get_recruitment_scheduler

        sched = get_recruitment_scheduler()
        if not sched:
            return json.dumps({"ok": False, "error": "HR 招聘 MCP 包未找到"}, ensure_ascii=False)
        fn = getattr(sched, "resume_hr_job_scheduler_for_folder", None)
        if not callable(fn):
            return json.dumps({"ok": False, "error": "调度器未提供 resume_hr_job_scheduler_for_folder"}, ensure_ascii=False)
        out = fn(job_folder=str(job_folder or "").strip(), job_name=str(job_name or "").strip())
        return json.dumps(out if isinstance(out, dict) else {"ok": False, "error": str(out)}, ensure_ascii=False)
    except Exception as e:
        logger.warning("[MCP Registry] resume_hr_job_scheduler 失败: %s", e)
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


def _invoke_get_recruitment_job_memory_local(job_name: str = "") -> str:
    """某岗位招聘历史快照，供再次启动调度前向 HR 确认续接或新开。"""
    jn = (job_name or "").strip()
    if not jn:
        return json.dumps({"ok": False, "error": "job_name 不能为空"}, ensure_ascii=False)
    try:
        from l3_node.hr_loader import get_recruitment_scheduler

        sched = get_recruitment_scheduler()
        if not sched:
            return json.dumps(
                {"ok": False, "error": "HR 招聘 MCP 包未找到，请从 L1 订阅 com.jachin.hr.recruitment"},
                ensure_ascii=False,
            )
        mem_fn = getattr(sched, "build_recruitment_job_memory", None)
        if not callable(mem_fn):
            return json.dumps({"ok": False, "error": "调度器未提供 build_recruitment_job_memory"}, ensure_ascii=False)
        mem = mem_fn(jn)
        if not isinstance(mem, dict):
            return json.dumps({"ok": False, "error": "无效快照"}, ensure_ascii=False)
        return json.dumps({"ok": True, **mem}, ensure_ascii=False)
    except Exception as e:
        logger.warning("[MCP Registry] get_recruitment_job_memory 失败: %s", e)
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


def _invoke_read_file_local(path_raw: str) -> str:
    """L3 本地执行 read_file，使用 core.pdf_extractor。"""
    from core.pdf_extractor import extract_pdf_text, SCAN_PLACEHOLDER
    _proj = get_app_root()
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
        from l3_node.jachin_config import get_hr_jds_dir
        raw_norm = raw.lstrip("/")
        plugin_data = _proj / "skills_repo" / "plugin" / "data"
        for base, sub in [
            (_l3_vol, raw_norm),
            (plugin_data, raw_norm),
            (plugin_data, p.name or raw_norm),
            (_proj / "data" / "hr_resumes", p.name or raw_norm),
            (get_hr_jds_dir(_proj), p.name or raw_norm),
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


def _load_tools_from_l3_mcp_cache() -> tuple[list[dict[str, Any]], dict[str, tuple[Path, str, str]]]:
    """
    从 ~/.jachin/l3_mcp_cache/ 扫描 L3_LOCAL MCP，动态加载工具定义。
    开发模式（非 frozen）下同时扫描 skills_repo/plugin/ 下的 L3_LOCAL 包，便于本地开发。
    Returns:
        (tools_list, invoke_map): tools_list 为 {id, label, desc, params} 列表；
        invoke_map 为 tool_id -> (cache_dir, module_path, function_name) 供 invoke 调用。
    """
    import sys

    tools_out: list[dict[str, Any]] = []
    invoke_map: dict[str, tuple[Path, str, str]] = {}

    scan_dirs: list[Path] = []
    if L3_MCP_CACHE.exists():
        for d in L3_MCP_CACHE.iterdir():
            if d.is_dir():
                scan_dirs.append(d)
    if not getattr(sys, "frozen", False):
        try:
            root = get_app_root()
            plugin_root = root / "skills_repo" / "plugin"
            if plugin_root.exists():
                for p in plugin_root.iterdir():
                    if p.is_dir() and (p / "plugin.json").exists():
                        try:
                            pl = json.loads((p / "plugin.json").read_text(encoding="utf-8"))
                            if pl.get("runtime_tier") == "L3_LOCAL":
                                scan_dirs.append(p)
                        except Exception:
                            pass
        except Exception:
            pass

    for subdir in scan_dirs:
        if not subdir.is_dir():
            continue
        plugin_path = subdir / "plugin.json"
        if not plugin_path.exists():
            continue
        try:
            plugin = json.loads(plugin_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug("[MCP Registry] 解析 plugin.json 失败 %s: %s", subdir.name, e)
            continue
        tools_list = plugin.get("tools") or []
        if isinstance(tools_list, dict):
            tools_list = list(tools_list.values()) if tools_list else []
        for t in tools_list:
            tid = t.get("id") if isinstance(t, dict) else str(t)
            if not tid:
                continue
            tid = tid.replace("mcp:", "").strip() if tid.startswith("mcp:") else tid.strip()
            mcp_id = f"{MCP_TOOLS_PREFIX}{tid}"
            module_path = (t.get("module", "") or "").strip() if isinstance(t, dict) else ""
            func_name = (t.get("function", "") or "").strip() if isinstance(t, dict) else ""
            if not module_path or not func_name:
                logger.debug("[MCP Registry] %s 缺少 module/function，跳过", tid)
                continue
            params = t.get("params", ["input"]) if isinstance(t, dict) else ["input"]
            desc = (t.get("desc", "") or t.get("description", "") or mcp_id) if isinstance(t, dict) else mcp_id
            tools_out.append({
                "id": mcp_id,
                "label": mcp_id,
                "desc": f"[L3 缓存] {desc}",
                "params": params if isinstance(params, list) else ["input"],
            })
            invoke_map[mcp_id] = (subdir, module_path, func_name)
            invoke_map[tid] = (subdir, module_path, func_name)
    return tools_out, invoke_map


def _invoke_cached_mcp_tool(
    cache_dir: Path,
    module_path: str,
    func_name: str,
    arguments: dict[str, Any],
) -> str:
    """动态加载 l3_mcp_cache 中的 Python 模块并执行。"""
    import sys
    cache_str = str(cache_dir.resolve())
    prev_path = sys.path.copy()
    try:
        if cache_str not in sys.path:
            sys.path.insert(0, cache_str)
        mod = __import__(module_path, fromlist=[func_name])
        func = getattr(mod, func_name, None)
        if not callable(func):
            return json.dumps({"status": "error", "error": f"未找到可调用函数 {func_name}"}, ensure_ascii=False)
        kwargs = {k: v for k, v in arguments.items() if k != "input"}
        if "input" in arguments and not kwargs:
            kwargs["input"] = arguments["input"]
        result = func(**kwargs)
        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=False)
        return str(result)
    except Exception as e:
        logger.warning("[MCP Registry] 缓存 MCP 执行失败 %s.%s: %s", module_path, func_name, e)
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)
    finally:
        sys.path = prev_path


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
        self._cache_invoke_map: dict[str, tuple[Path, str, str]] = {}

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
        获取 MCP 工具列表。L3 本地优先（L3_LOCAL_MCP_TOOLS + l3_mcp_cache），
        L2 仅作补充；本地开发时可设 JACHIN_L3_LOCAL_ONLY=1 跳过 L2。
        Returns:
            合并后的工具列表，格式与 load_tools 一致：{id, label, desc, params}
        """
        import os

        import httpx

        tools: list[dict[str, Any]] = list(L3_LOCAL_MCP_TOOLS)
        self._known_mcp_tools = set(self._local_mcp_tools)
        local_names = {
            "read_file",
            "atom_post_job_boss",
            "atom_greet_recommend_boss",
            "add_automated_recruitment_task",
            "stop_automated_recruitment",
            "get_recruitment_job_memory",
            "atom_bi_project_context",
        }

        cached_tools, self._cache_invoke_map = _load_tools_from_l3_mcp_cache()
        for ct in cached_tools:
            ct_id = ct.get("id", "")
            if ct_id and ct_id not in self._known_mcp_tools:
                raw = ct_id.replace(MCP_TOOLS_PREFIX, "", 1).strip()
                if raw not in local_names:
                    tools.append(ct)
                    self._known_mcp_tools.add(ct_id)
                    self._local_mcp_tools.add(ct_id)

        if os.environ.get("JACHIN_L3_LOCAL_ONLY", "").strip().lower() in ("1", "true", "yes"):
            self._tools_cache = tools
            logger.info("[MCP Registry] L3 本地优先模式，仅用本地工具 %d 个（JACHIN_L3_LOCAL_ONLY=1，跳过 L2）", len(tools))
            return tools

        # L3 进程内 stdio MCP（长期架构；与 L2 原 mcp_servers.json + inventory/mcps 同源）
        try:
            from l3_node.mcp_stdio_bootstrap import start_l3_stdio_mcp_host

            await start_l3_stdio_mcp_host()
            from core.mcp_client import get_mcp_manager

            mgr = get_mcp_manager()
            stdio_list = mgr.get_all_tools()
            if not stdio_list:
                stdio_list = await mgr.list_tools_async()
            for t in stdio_list:
                name = (t.get("name") or "").strip()
                if not name or name in local_names:
                    continue
                mcp_id = self._mcp_id(name)
                if mcp_id in self._known_mcp_tools:
                    continue
                params: list[str] = []
                schema = t.get("inputSchema") or {}
                if isinstance(schema, dict):
                    props = schema.get("properties") or {}
                    params = list(props.keys()) if props else ["input"]
                desc = t.get("description") or name
                tools.append({
                    "id": mcp_id,
                    "label": mcp_id,
                    "desc": f"[L3 stdio] {desc}",
                    "params": params,
                })
                self._known_mcp_tools.add(mcp_id)
        except ImportError:
            pass
        except Exception as e:
            logger.debug("[MCP Registry] 合并 L3 stdio MCP 失败: %s", e)

        url = f"{self._l2_base_url}/api/v2/mcp/tools"
        logger.info("[MCP Registry] L3 本地优先，L2 补充 url=%s", url)
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

    async def invoke(
        self,
        tool_id: str,
        action_input: str,
        *,
        timeout: float = 30.0,
        allowed_skills: list[str] | None = None,
        allow_l2_delegate: bool = True,
    ) -> str:
        """
        执行 MCP 工具。L3 本地工具（read_file、atom_post_job_boss、atom_greet_recommend_boss）直接执行，其余走 L2。
        allowed_skills: None=开发模式全开；非 None 时执行前校验白名单，未分配则拒绝。
        allow_l2_delegate: 为 False 时禁止 invoke_via_l2（跨节点代跑执行端用，避免 L2↔L3 循环委派）。
        """
        if allowed_skills is not None:
            from l3_node.skills.loader import is_tool_allowed
            if not is_tool_allowed(tool_id, allowed_skills):
                return "[权限拒绝: 当前子账号未开启该技能]"
        from l3_node.tool_call_cache import try_get_cached, store_if_cacheable

        _cached = try_get_cached(tool_id, action_input)
        if _cached is not None:
            return _cached

        out = await self._invoke_impl(
            tool_id, action_input, timeout=timeout, allow_l2_delegate=allow_l2_delegate
        )
        return store_if_cacheable(tool_id, action_input, out)

    async def _invoke_impl(
        self,
        tool_id: str,
        action_input: str,
        *,
        timeout: float = 30.0,
        allow_l2_delegate: bool = True,
    ) -> str:
        """MCP 实际执行（不含权限与 P1 缓存包装）。"""
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
                _fr = arguments.get("force_republish")
                _force_rep = bool(_fr) if _fr is not None else False
                logger.info("[MCP Registry] L3 本地执行 atom_post_job_boss cdp=%s jd_config=%s", cdp_url, "有" if jd_config else "无")
                return await asyncio.to_thread(
                    _invoke_atom_post_job_boss_local,
                    str(cdp_url) if cdp_url else "http://127.0.0.1:9222",
                    str(jd_config_path) if jd_config_path else "",
                    str(jd_config) if jd_config else None,
                    _force_rep,
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
                    _proj = get_app_root()
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
                jd_config_path = (arguments.get("jd_config_path") or "").strip()
                if not (job_name or "").strip() or not jd_config_path:
                    try:
                        from l3_node.local_memory import get_hr_recruitment_workflow_pointer

                        ptr = get_hr_recruitment_workflow_pointer()
                        if not (job_name or "").strip():
                            job_name = (ptr.get("job_name") or "").strip() or job_name
                        if not jd_config_path:
                            jd_config_path = (ptr.get("jd_config_path") or "").strip()
                    except Exception:
                        pass
                if not (job_name or "").strip() or not jd_config_path:
                    _wr = Path.home() / ".jachin" / "workspace" / "hr_recruitment"
                    if _wr.is_dir():

                        def _jd_pick_score(jpath: Path) -> tuple[int, float]:
                            """(越优先越高, mtime)：含 Boss 选岗行 jd_select 的优先于仅 job_title 的短目录，避免误绑 10-15K 旧夹。"""
                            try:
                                st = jpath.stat().st_mtime
                                jd_data = json.loads(jpath.read_text(encoding="utf-8"))
                                if not isinstance(jd_data, dict):
                                    return (0, st)
                                sel = (jd_data.get("jd_select") or "").strip()
                                if " _ " in sel:
                                    return (3, st)
                                if jd_data.get("salary_min") is not None or jd_data.get("salary_max") is not None:
                                    return (2, st)
                                if jd_data.get("job_title"):
                                    return (1, st)
                            except Exception:
                                pass
                            return (0, 0.0)

                        _cands = [
                            (d / "jd.json", _jd_pick_score(d / "jd.json"))
                            for d in _wr.iterdir()
                            if d.is_dir() and (d / "jd.json").is_file()
                        ]
                        for jpf, _ in sorted(_cands, key=lambda x: (-x[1][0], -x[1][1])):
                            try:
                                jd_data = json.loads(jpf.read_text(encoding="utf-8"))
                                if isinstance(jd_data, dict) and jd_data.get("job_title"):
                                    if not (job_name or "").strip():
                                        job_name = str(jd_data["job_title"]).strip()
                                    if not jd_config_path:
                                        jd_config_path = str(jpf.resolve())
                                    logger.info(
                                        "[MCP Registry] add_automated_recruitment_task 从 ~/.jachin/workspace/hr_recruitment 兜底 job=%s path=%s",
                                        job_name,
                                        jpf.parent.name,
                                    )
                                    break
                            except Exception as ex:
                                logger.debug("[MCP Registry] workspace jd 兜底失败: %s", ex)
                if not (job_name or "").strip():
                    try:
                        from l3_node.hr_loader import get_recruitment_scheduler

                        rs = get_recruitment_scheduler()
                        if rs is not None and hasattr(rs, "get_recruitment_status_digest"):
                            d = rs.get_recruitment_status_digest("")
                            if isinstance(d, dict) and d.get("has_active_job") and d.get("job_name"):
                                job_name = str(d["job_name"]).strip()
                                logger.info(
                                    "[MCP Registry] add_automated_recruitment_task job_name 从 digest 兜底: %s",
                                    job_name,
                                )
                    except Exception:
                        pass

                def _mcp_opt_int(key: str) -> int | None:
                    if key not in arguments:
                        return None
                    v = arguments.get(key)
                    if v is None:
                        return None
                    try:
                        return int(v)
                    except (TypeError, ValueError):
                        return None

                def _mcp_opt_bool(key: str) -> bool | None:
                    if key not in arguments:
                        return None
                    v = arguments.get(key)
                    if v is None:
                        return None
                    if isinstance(v, bool):
                        return v
                    s = str(v).strip().lower()
                    if s in ("0", "false", "no", "否", "关", "off"):
                        return False
                    if s in ("1", "true", "yes", "是", "开", "on"):
                        return True
                    return None

                jd_select_arg = (arguments.get("jd_select") or "").strip()
                logger.info(
                    "[MCP Registry] L3 本地执行 add_automated_recruitment_task job_name=%s（省略的数值将读 jd.json）",
                    job_name or "(空)",
                )
                return await asyncio.to_thread(
                    _invoke_add_automated_recruitment_task_local,
                    str(job_name).strip() if job_name else "",
                    _mcp_opt_int("analyze_threshold"),
                    str(jd_config_path) if jd_config_path else "",
                    _mcp_opt_bool("enable_greet_recommend"),
                    _mcp_opt_int("resume_collect_target"),
                    _mcp_opt_int("max_count_per_harvest_tick"),
                    _mcp_opt_int("greet_target"),
                    str(jd_select_arg) if jd_select_arg else "",
                    _mcp_opt_int("greet_harvest_switch_interval_minutes"),
                    _mcp_opt_int("recommend_interval_minutes"),
                    _mcp_opt_int("greet_only_total_target"),
                    _mcp_opt_int("greet_only_interval_minutes"),
                )

            if raw_name == "hr_scheduler_send_confirm_prompt":
                return await asyncio.to_thread(_invoke_hr_scheduler_send_confirm_prompt_local, dict(arguments or {}))

            if raw_name == "stop_automated_recruitment":
                job_name = (arguments.get("job_name", arguments.get("input", "")) or "").strip()
                return _invoke_stop_automated_recruitment_local(job_name=job_name)

            if raw_name == "get_recruitment_job_memory":
                job_name = (arguments.get("job_name", arguments.get("input", "")) or "").strip()
                return _invoke_get_recruitment_job_memory_local(job_name=job_name)

            if raw_name == "list_hr_scheduler_suspended_jobs":
                return await asyncio.to_thread(_invoke_list_hr_scheduler_suspended_jobs_local)

            if raw_name == "resume_hr_job_scheduler":
                jf = (arguments.get("job_folder") or arguments.get("jobFolder") or "").strip()
                jn = (arguments.get("job_name") or arguments.get("jobName") or "").strip()
                return await asyncio.to_thread(_invoke_resume_hr_job_scheduler_local, jf, jn)

            # BI 战报 MCP 工具（docs/bi_daily_report/）
            if raw_name == "atom_web_scraper":
                arguments = dict(arguments)
                _u = (str(arguments.get("url") or "")).strip()
                if not _u:
                    _u = extract_http_url_from_corrupted_text(action_input or "")
                    if _u:
                        arguments["url"] = _u
                cfg = arguments.get("config") or {}
                if isinstance(cfg, dict) and arguments.get("cdp_url"):
                    cfg = {**cfg, "cdp_url": arguments.get("cdp_url")}
                return await asyncio.to_thread(
                    _invoke_atom_web_scraper_local,
                    url=str(arguments.get("url", "") or ""),
                    output_path=arguments.get("output_path", ""),
                    config=cfg,
                )
            if raw_name == "atom_bi_natural_retention_collect":
                fsc = arguments.get("full_spa_config")
                if not isinstance(fsc, dict):
                    fsc = arguments.get("full_spa")
                return await asyncio.to_thread(
                    _invoke_atom_bi_natural_retention_collect_local,
                    str(arguments.get("period1_start", "") or ""),
                    str(arguments.get("period1_end", "") or ""),
                    str(arguments.get("period2_start", "") or ""),
                    str(arguments.get("period2_end", "") or ""),
                    str(arguments.get("raw_dir", "") or ""),
                    str(arguments.get("base_url", "") or ""),
                    str(arguments.get("cdp_url", "") or ""),
                    bool(arguments.get("auto_ingest", False)),
                    fsc if isinstance(fsc, dict) else None,
                )
            if raw_name == "atom_lark_notifier":
                return await asyncio.to_thread(
                    _invoke_atom_lark_notifier_local,
                    webhook_url=arguments.get("webhook_url", ""),
                    markdown_content=arguments.get("markdown_content", ""),
                    title=arguments.get("title", ""),
                    chat_id=arguments.get("chat_id", ""),
                )
            if raw_name == "atom_email_sender":
                return await asyncio.to_thread(
                    _invoke_atom_email_sender_local,
                    smtp_config=arguments.get("smtp_config"),
                    to_addrs=arguments.get("to_addrs", []),
                    subject=arguments.get("subject", ""),
                    body=arguments.get("body", ""),
                    attachment_paths=arguments.get("attachment_paths"),
                )
            if raw_name == "atom_bi_project_context":
                return await asyncio.to_thread(
                    _invoke_atom_bi_project_context_local,
                    arguments if isinstance(arguments, dict) else {},
                )

        if tool_id in self._cache_invoke_map or self._raw_name(tool_id) in self._cache_invoke_map:
            cache_dir, module_path, func_name = self._cache_invoke_map.get(
                tool_id
            ) or self._cache_invoke_map.get(self._raw_name(tool_id), (None, "", ""))
            if cache_dir and module_path and func_name:
                return await asyncio.to_thread(
                    _invoke_cached_mcp_tool,
                    cache_dir, module_path, func_name, self._parse_action_input(action_input),
                )

        try:
            from l3_node.mcp_stdio_bootstrap import start_l3_stdio_mcp_host
            from core.mcp_client import get_mcp_manager, MCPToolNotFoundError as _McpNotFound

            await start_l3_stdio_mcp_host()
            _mgr = get_mcp_manager()
            _rn = self._raw_name(tool_id)
            if _rn and _mgr.can_invoke_stdio_tool(_rn):
                _args = self._parse_action_input(action_input)
                if _rn == "fetch":
                    _args = normalize_mcp_fetch_arguments(_args, fallback_text=action_input or "")
                    if not (str(_args.get("url") or "").strip()):
                        return (
                            "[MCP] fetch 缺少 url：请让 Action Input 为合法 JSON，例如 "
                            '{"url":"https://www.python.org"}'
                        )
                try:
                    return await asyncio.wait_for(
                        _mgr.invoke_tool(_rn, _args),
                        timeout=timeout,
                    )
                except _McpNotFound:
                    pass
                except asyncio.TimeoutError:
                    return f"[MCP] stdio 调用超时 tool={_rn}"
        except ImportError:
            pass
        except Exception as e:
            logger.warning("[MCP Registry] L3 stdio invoke 失败 tool=%s err=%s", tool_id, e)
            return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)

        if not allow_l2_delegate:
            logger.warning("[MCP Registry] 工具 %s 本机不可用且禁止转发 L2（代跑执行上下文）", tool_id)
            return (
                "[MCP] 本机未安装该工具，且当前处于跨节点代跑执行上下文，禁止再转发 L2（避免循环委派）。"
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
        通过 L2 ``POST /api/v2/mcp/invoke`` 触发委托执行（本请求不携带用户 JWT，仅 tool + arguments）。

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

        if raw_name == "fetch":
            arguments = normalize_mcp_fetch_arguments(arguments, fallback_text=inp)

        url = f"{self._l2_base_url}/api/v2/mcp/invoke"
        payload = {"tool_name": raw_name, "arguments": arguments}
        logger.info("[MCP Registry] 调用 L2 invoke tool=%s url=%s", raw_name, url)

        headers: dict[str, str] = {}
        _cfg_p = Path.home() / ".jachin" / "l2_gateway_config.json"
        if _cfg_p.exists():
            try:
                _gc = json.loads(_cfg_p.read_text(encoding="utf-8"))
                _sub = (_gc.get("sub_account_id") or "").strip()
                if _sub:
                    headers["X-Sub-Account-Id"] = _sub
            except Exception:
                pass

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
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
