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


def _parse_work_order_xml_tool_params(inp: str) -> dict[str, Any]:
    """
    解析 RoleExecutor 常见的 XML 风格参数块（非 JSON 时模型仍可能输出）：
    <parameter=file_path>~/Desktop/x.md</parameter>
    <parameter=topic>标题</parameter>
    <parameter=outline_sections>["a","b"]</parameter>
    与 JSON 互补：用于 util:/sys: 工具在仅输出 XML 时不再得到空 kwargs。
    """
    raw = (inp or "").strip()
    if not raw or "parameter" not in raw.lower():
        return {}
    out: dict[str, Any] = {}
    for m in re.finditer(
        r"<\s*parameter\s*=\s*([a-zA-Z0-9_]+)\s*>(.*?)</\s*parameter\s*>",
        raw,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        key = (m.group(1) or "").strip()
        val = (m.group(2) or "").strip()
        if not key:
            continue
        if val.startswith(("[", "{")):
            try:
                out[key] = json.loads(val)
                continue
            except json.JSONDecodeError:
                pass
        out[key] = val
    return out


def _merge_xml_params_into_util(util_params: dict[str, Any], xml_params: dict[str, Any]) -> None:
    """对缺失或空字符串的键用 XML 解析结果补齐。"""
    for k, v in xml_params.items():
        cur = util_params.get(k)
        if cur is None or (isinstance(cur, str) and not cur.strip()):
            util_params[k] = v


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

# JPP Wasm 内置包目录（相对本模块：primitives/tools/wasm_bundled）
_WASM_PLUGINS_DIR = Path(__file__).resolve().parent / "wasm_bundled"
# L3 冷启动同步缓存：~/.jachin/l3_skill_cache/（从 L2 拉取的技能）
_L3_SKILL_CACHE_DIR = Path.home() / ".jachin" / "l3_skill_cache"

# Native Core 工具定义（对标 core/native_tools.py；写路径见 native_write_allowlist）
NATIVE_TOOLS: list[dict[str, Any]] = [
    {
        "id": "core:fs_read",
        "label": "core:fs_read",
        "desc": (
            "可以读取本机绝大多数常规目录（如 D:\\\\业务数据\\\\ 等）下的文本文件。严禁读取系统级敏感目录。"
            "若用户消息中已附带 [附件: ...]，请直接使用消息正文，勿重复调用本工具。"
            "参数: file_path"
        ),
        "params": ["file_path"],
    },
    {
        "id": "core:fs_write",
        "label": "core:fs_write",
        "desc": "写入文件。【路径特权】可将文件保存到默认 workspace（相对路径相对 workspace），或用户真实桌面/下载/文档目录，例如 ~/Desktop/文件名.xlsx、~/Downloads/报告.md、~/Documents/笔记.txt（须为落在白名单内的绝对路径）。推荐 JSON：{\"file_path\":\"...\",\"content\":\"全文\"}。",
        "params": ["file_path", "content"],
    },
    {
        "id": "core:shell_exec",
        "label": "core:shell_exec",
        "desc": "执行 Shell，cwd=~/.jachin/workspace。JSON：command, timeout, background, sandbox_profile(isolated|sandbox 时 cwd 为 workspace/sandboxes/<id>/)。background=true 时用 core:shell_job_status 查询。",
        "params": ["command"],
    },
    {
        "id": "core:shell_job_status",
        "label": "core:shell_job_status",
        "desc": "查询后台 shell 任务状态与日志尾部。参数: job_id 或 JSON {\"job_id\":\"...\"}",
        "params": ["job_id"],
    },
    {
        "id": "core:shell_job_cancel",
        "label": "core:shell_job_cancel",
        "desc": "取消后台 shell（需 nexus_config intelligence_p1.shell_job_cancel_enabled=true）。参数: job_id",
        "params": ["job_id"],
    },
    {
        "id": "core:apply_patch",
        "label": "core:apply_patch",
        "desc": "阶段 C：将 unified diff 应用到 ~/.jachin/workspace/。参数 JSON：patch_text（或 unified_diff），可选 session_hint、python_ast_validate（true 时对 .py 做 ast.parse 预检）",
        "params": ["patch_text"],
    },
    {
        "id": "core:submit_background_task",
        "label": "core:submit_background_task",
        "desc": "前台投递长耗时任务到后台队列（不阻塞闲聊）。JSON：intent（必填）；可选 require_skills 数组、max_iterations。成功返回 task_id；队列满返回 status=rejected reason=resource_exhausted。",
        "params": ["intent"],
    },
    {
        "id": "core:check_background_task",
        "label": "core:check_background_task",
        "desc": "查询后台任务：JSON {\"task_id\":\"T-xxx\"} 或 task_id 纯文本；或 {\"list_recent\":true} 列出最近任务。",
        "params": ["task_id"],
    },
    {
        "id": "core:check_interrupted_tasks",
        "label": "core:check_interrupted_tasks",
        "desc": "读取断电/崩溃遗留的未完成后台任务摘要（zombie_tasks.json）。可选 JSON {\"consume\":true} 读后清空。新会话或用户问系统状态时应调用，便于询问是否用 core:submit_background_task 重投。",
        "params": [],
    },
    {
        "id": "core:local_memory_search",
        "label": "core:local_memory_search",
        "desc": "L3 Memory Nexus：SQLite+FastEmbed deep_search 语义检索（~/.jachin/palace_db）。JSON：query；可选 top_k（archived mmr/half_life 字段兼容忽略）",
        "params": ["query"],
    },
    {
        "id": "core:local_memory_append",
        "label": "core:local_memory_append",
        "desc": (
            "向 Memory Nexus（User_Persona/Learned_Skills）commit 一条抽屉；立即可被 core:local_memory_search（deep_search）命中。"
            "**禁止**幻觉写入 MEMORY.md。JSON：content（必填）；可选 tags 字符串数组。"
        ),
        "params": ["content"],
    },
    {
        "id": "core:safety_lock_append",
        "label": "core:safety_lock_append",
        "desc": (
            "提交「系统级安防规则」：默认进入 **待审批队列**（不写正式 MD），管理员用 CLI 或控制台「安全锁审批」刷入；"
            "勿尝试提供「管理员密钥」。**日常偏好/项目代号/技术栈喜好禁止走本工具**，应使用 **core:local_memory_append** 写入 Nexus，或用 **core:local_memory_search** / **recall_memory**（同源 Memory Nexus）检索。"
            "可选 **category**（如 backend_framework）：某 category 首条人工批准后，同 category 再次提交将 **自动覆盖** 旧规则（TOFU 同类二次免批）。"
            "JSON：body 或 content（必填）；可选 source、tags、category。"
        ),
        "params": ["body"],
    },
    {
        "id": "core:safety_lock_list_pending",
        "label": "core:safety_lock_list_pending",
        "desc": "列出安全锁待审批条目（pending）。无参数或 tool input: {}。",
        "params": [],
    },
    {
        "id": "core:safety_lock_remove",
        "label": "core:safety_lock_remove",
        "desc": "从 ~/.jachin/JACHIN_SAFETY_LOCK.md 删除含指定 id 的条目块。JSON：entry_id（或 id）。",
        "params": ["entry_id"],
    },
    {
        "id": "core:workflow_run",
        "label": "core:workflow_run",
        "desc": "阶段 C：执行 workspace 下 YAML 工作流（DAG + 可选持久化）。JSON：yaml_path；可选 persistent, run_id, resume, reset, keep_completed_state；步骤 on_failure: abort|continue|retry，max_retries，retry_delay_sec；或与 tool_id 二选一写 domain_ref 调用 L2 子图",
        "params": ["yaml_path"],
    },
    {
        "id": "core:domain_workflow_run",
        "label": "core:domain_workflow_run",
        "desc": "长期架构 L2：执行已注册领域子图。JSON：domain_id（或 domain）+ 领域参数，如 HR：workflow_id, include_analyze, context",
        "params": ["domain_id"],
    },
    {
        "id": "core:apply_patch_rollback",
        "label": "core:apply_patch_rollback",
        "desc": "回滚 core:apply_patch。JSON：backup_id 可选（缺省用 last_success.json）",
        "params": ["backup_id"],
    },
    {
        "id": "core:shell_hitl_approve",
        "label": "core:shell_hitl_approve",
        "desc": "批准 Shell HITL。JSON：hash_hex 或 command 或 pending_id",
        "params": ["hash_hex"],
    },
    {
        "id": "core:compose_essay",
        "label": "core:compose_essay",
        "desc": (
            "写作文：根据 JSON 参数生成 Markdown 作文骨架（可与桌面端生成式 UI 联调）。"
            "字段：topic（主题）；style_id、style_label（文体）；word_count_target（目标字数）；"
            "audience（读者）；tone（语气）；structure（结构）。"
        ),
        "params": ["topic"],
    },
]

try:
    from l3_node.primitives.tools.core_util_tools import UTIL_TOOLS_NATIVES_LIST

    if not any(str(t.get("id", "")).startswith("util:") for t in NATIVE_TOOLS):
        NATIVE_TOOLS.extend(UTIL_TOOLS_NATIVES_LIST)
except Exception as e:
    logger.debug("[Skills] Native util/sys 工具未挂载: %s", e)

try:
    from l3_node.skills.native_tools.akshare_tools import AKSHARE_NATIVE_TOOLS_LIST

    NATIVE_TOOLS.extend(AKSHARE_NATIVE_TOOLS_LIST)
except Exception as e:
    logger.debug("[Skills] AKShare 原生工具未挂载: %s", e)

try:
    from l3_node.skills.native_tools.yfinance_tools import YFINANCE_NATIVE_TOOLS_LIST

    NATIVE_TOOLS.extend(YFINANCE_NATIVE_TOOLS_LIST)
except Exception as e:
    logger.debug("[Skills] yfinance 原生工具未挂载: %s", e)

try:
    from l3_node.primitives.tools.native_extensions import load_native_extension_tools

    NATIVE_TOOLS.extend(load_native_extension_tools())
except Exception as e:
    logger.debug("[Skills] Native extension tools not mounted: %s", e)

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


def _sync_hr_caller_jd_to_skill_jds_files(jd_text: str, target_role: str) -> None:
    """
    Wasm 内 mcp_read_file 常按 target_role 或 hr-analyzer4 读取 ~/.jachin/config/skills/.../hr_jds/*.md。
    若仓库/缓存里残留「云边协同」等测试 JD，会覆盖 stdin 中的 jd_template。
    在每次带 jd_template 调用透析镜前，把本次 JD 写入 hr-analyzer4.md 与 {target_role}.md。
    """
    jd_text = (jd_text or "").strip()
    if not jd_text:
        return
    try:
        from l3_node.jachin_config import get_hr_jds_dir
        from l3_node.paths import get_app_root

        jd_dir = get_hr_jds_dir(get_app_root())
        jd_dir.mkdir(parents=True, exist_ok=True)
        role = (target_role or "backend_engineer").strip() or "backend_engineer"
        for fname in (f"{role}.md", "hr-analyzer4.md"):
            (jd_dir / fname).write_text(jd_text, encoding="utf-8")
        logger.info(
            "[Skill Execute] HR 已将本调用 jd_template 同步至 hr_jds（%s.md + hr-analyzer4.md）len=%d",
            role,
            len(jd_text),
        )
    except Exception as e:
        logger.warning("[Skill Execute] HR 同步 hr_jds JD 文件失败: %s", e)


def _get_hr_plugin_config_defaults(lookup_id: str) -> dict[str, Any]:
    """从 plugin.json 直接读取 HR 技能默认配置（L2 不可用时兜底，确保 output_dir 等可用）"""
    _HR_ITEM_MAP = {"jpp:com.jachin.hr.analyzer4": "hr-analyzer4"}
    item_id = _HR_ITEM_MAP.get(lookup_id)
    if not item_id:
        return {}
    import sys
    bases = [_L3_SKILL_CACHE_DIR]
    if not getattr(sys, "frozen", False):
        bases.append(_WASM_PLUGINS_DIR)
    for base in bases:
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
    proj = Path(__file__).resolve().parent.parent.parent.parent
    cfg = {**_get_hr_plugin_config_defaults(lookup_id), **(_fetch_skill_config(config_id) or {})}
    use_abs = cfg.get("resume_input_dir_use_absolute") in (True, "true", "1", "yes") or cfg.get("use_absolute_path") in (True, "true", "1", "yes")
    try:
        persist_mod = __import__("l3_node.hr_loader", fromlist=["get_hr_analysis_persist"]).get_hr_analysis_persist()
        _resolve_safe_dir = persist_mod._resolve_safe_dir if persist_mod else (lambda *a, **k: None)
        _PROJ_ROOT = __import__("l3_node.paths", fromlist=["get_app_root"]).get_app_root()
        resume_dir = _resolve_safe_dir(cfg.get("resume_input_dir") or "data/hr_resumes", _PROJ_ROOT, use_absolute_path=use_abs)
    except ImportError:
        resume_dir = None
    resume_dir = resume_dir or (proj / "data" / "hr_resumes")
    target_role = (cfg.get("default_target_role") or "").strip()
    if not target_role:
        from l3_node.jachin_config import get_hr_jds_dir
        jd_dir = get_hr_jds_dir(proj)
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


def _persist_hr_analyzer_ndjson_batch(stdin_json: dict[str, Any], lookup_id: str) -> int:
    """
    从 get_last_ndjson_lines() 读取 Wasm 已推送的 progress 行并写入 *_analysis.md。
    正常返回与 Wasm 中途失败（trap）后均可调用：execute_abi 在 func finally 里会 drain 队列。
    """
    if lookup_id != "jpp:com.jachin.hr.analyzer4":
        return 0
    try:
        from core.wasm_runner import get_last_ndjson_lines

        persist_mod = __import__("l3_node.hr_loader", fromlist=["get_hr_analysis_persist"]).get_hr_analysis_persist()
        persist_hr_analysis_batch_item = persist_mod.persist_hr_analysis_batch_item if persist_mod else None
        if not persist_hr_analysis_batch_item:
            return 0
        cfg = _fetch_skill_config(lookup_id.replace("jpp:", ""))
        cfg = {**_get_hr_plugin_config_defaults(lookup_id), **(cfg or {})}
        _caller_out = (stdin_json.get("output_dir") or "").strip()
        if _caller_out:
            cfg = {
                **cfg,
                "output_dir": _caller_out,
                "output_dir_use_absolute": True,
                "use_absolute_path": True,
            }
        ndjson_lines = get_last_ndjson_lines()
        count = 0
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
            logger.info("[Skill Execute] HR NDJSON 批量持久化 count=%d", count)
        return count
    except Exception as e:
        logger.warning("[Skills] HR NDJSON 落盘失败: %s", e)
        return 0


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
    """L3 独立运行时的 Native 兜底实现。HR 白名单：client_volumes、data/hr_resumes、config/skills/.../hr_jds。"""
    try:
        from l3_node.workspace_context import get_effective_workspace_root

        workspace = get_effective_workspace_root()
    except Exception:
        workspace = Path.home() / ".jachin" / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    proj = Path(__file__).resolve().parent.parent.parent.parent
    from l3_node.jachin_config import get_hr_jds_dir
    _l3_volume = (Path.home() / ".jachin" / "client_volumes").resolve()
    _hr_allowed = [_l3_volume, (proj / "data" / "hr_resumes").resolve(), get_hr_jds_dir(proj).resolve()]

    from l3_node.primitives.native_write_allowlist import (
        assert_path_allowed_for_native_read,
        assert_path_allowed_for_native_write,
        path_is_under_allowed_write_roots,
    )

    def _under_hr(p: Path) -> bool:
        """与历史逻辑兼容：是否落在扩展后的 Native 读写白名单内。"""
        return path_is_under_allowed_write_roots(p)

    def _assert_under(p: Path) -> None:
        assert_path_allowed_for_native_write(p)

    def _read_file_content(p: Path) -> str:
        """读取文件，PDF 使用 core.pdf_extractor 提取纯文本（与 MCP read_file 复用）。"""
        if p.suffix.lower() == ".pdf":
            try:
                from core.pdf_extractor import extract_pdf_text
                return extract_pdf_text(p) or ""
            except ImportError:
                return ""
        return p.read_text(encoding="utf-8", errors="replace")

    def _read_after_read_policy(p: Path) -> str:
        try:
            assert_path_allowed_for_native_read(p)
        except ValueError as e:
            return f"[执行失败: {e}]"
        return _read_file_content(p)

    if tool_id == "core:fs_read":
        raw = (kwargs.get("file_path", "") or "").strip().replace("\\", "/")
        fp = Path(raw).expanduser()
        if not fp.is_absolute():
            # L3 数据卷相对路径：global_resume_pool/Java_杭州 4-6K/xxx.pdf
            cand_vol = (_l3_volume / raw.lstrip("/")).resolve()
            if cand_vol.exists() and cand_vol.is_file() and _under_hr(cand_vol):
                return _read_after_read_policy(cand_vol)
            for base in _hr_allowed:
                cand = (base / fp.name).resolve()
                if cand.exists() and _under_hr(cand):
                    return _read_after_read_policy(cand)
            cand = (proj / raw.lstrip("/")).resolve()
            if cand.exists() and _under_hr(cand):
                return _read_after_read_policy(cand)
            fp = (workspace / raw).resolve()
        if _under_hr(fp):
            return _read_after_read_policy(fp)
        return _read_after_read_policy(fp)
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
        import uuid as _uuid

        cmd = kwargs.get("command", "")
        timeout = kwargs.get("timeout", 30)
        prof = str(kwargs.get("sandbox_profile") or "").lower()
        cwd = workspace
        if prof in ("isolated", "sandbox", "jachin_sandbox"):
            cwd = (workspace / "sandboxes" / _uuid.uuid4().hex[:12]).resolve()
            cwd.mkdir(parents=True, exist_ok=True)
        if kwargs.get("background"):
            from l3_node.shell_jobs import start_background_shell

            return start_background_shell(str(cmd), cwd, int(timeout) if timeout else 30)
        r = subprocess.run(
            cmd, shell=True, cwd=str(cwd),
            capture_output=True, text=True, timeout=timeout,
        )
        return {"returncode": r.returncode, "stdout": r.stdout or "", "stderr": r.stderr or ""}
    if tool_id == "core:shell_job_status":
        from l3_node.shell_jobs import format_job_status_report

        return format_job_status_report(str(kwargs.get("job_id", "") or ""))
    if tool_id == "core:shell_job_cancel":
        from l3_node.shell_jobs import cancel_shell_job

        return cancel_shell_job(str(kwargs.get("job_id", "") or ""))
    if tool_id == "core:apply_patch":
        try:
            from core.apply_patch_unified import apply_unified_diff_to_workspace

            return apply_unified_diff_to_workspace(
                str(kwargs.get("patch_text", "") or ""),
                session_hint=str(kwargs.get("session_hint", "") or ""),
            )
        except ImportError as e:
            return {"ok": False, "error": str(e)}
    if tool_id == "core:domain_workflow_run":
        try:
            from l3_node.orchestration.glue import dispatch_domain_workflow

            did = str(kwargs.get("domain_id") or "").strip()
            p = kwargs.get("params")
            if not isinstance(p, dict):
                p = {}
            return dispatch_domain_workflow(did, p)
        except ImportError as e:
            return {"ok": False, "error": str(e)}
    if tool_id == "core:workflow_run":
        try:
            from l3_node.workflow_spec_runner import run_workflow_yaml

            return run_workflow_yaml(
                str(kwargs.get("yaml_path", "") or ""),
                allowed_skills=kwargs.get("allowed_skills"),
                persistent=bool(kwargs.get("persistent", False)),
                run_id=str(kwargs.get("run_id") or "default"),
                resume=bool(kwargs.get("resume", False)),
                reset=bool(kwargs.get("reset", False)),
                keep_completed_state=bool(kwargs.get("keep_completed_state", False)),
            )
        except ImportError as e:
            return {"ok": False, "error": str(e)}
    if tool_id == "core:apply_patch_rollback":
        try:
            from core.apply_patch_unified import rollback_patch_backup

            bid = kwargs.get("backup_id")
            return rollback_patch_backup(str(bid).strip() if bid else None)
        except ImportError as e:
            return {"ok": False, "error": str(e)}
    if tool_id == "core:shell_hitl_approve":
        try:
            from l3_node.shell_hitl import approve_shell_hitl

            return approve_shell_hitl(
                hash_hex=kwargs.get("hash_hex"),
                command=kwargs.get("command"),
                pending_id=kwargs.get("pending_id"),
            )
        except ImportError as e:
            return {"ok": False, "error": str(e)}
    if tool_id == "core:local_memory_search":
        from l3_node.local_memory_search import search_local_memories

        q = str(kwargs.get("query") or "").strip()
        top_k = int(kwargs.get("top_k") or 8)
        mmr_l = float(kwargs.get("mmr_lambda") or 0.55)
        half = float(kwargs.get("half_life_days") or 30.0)
        inc_md = kwargs.get("include_memory_md", True)
        if isinstance(inc_md, str):
            inc_md = inc_md.lower() in ("1", "true", "yes")
        return search_local_memories(
            q,
            top_k=max(1, min(32, top_k)),
            mmr_lambda=mmr_l,
            half_life_days=half,
            include_memory_md=bool(inc_md),
        )
    if tool_id == "core:local_memory_append":
        from l3_node.tools.core_local_memory_append import run_local_memory_append

        tags = kwargs.get("tags")
        if isinstance(tags, str):
            tags = [x.strip() for x in tags.split(",") if x.strip()]
        elif not isinstance(tags, list):
            tags = None
        return run_local_memory_append(
            content=str(kwargs.get("content") or kwargs.get("body") or ""),
            tags=tags,
        )
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
    扫描 JPP .wasm 插件。
    frozen 模式：仅扫描 ~/.jachin/l3_skill_cache/（订阅下载）。
    开发模式：同时扫描 wasm_bundled/ 与 l3_skill_cache/。
    已卸载的内置技能、永久卸载技能将被过滤。
    """
    import sys

    uninstalled = _get_uninstalled_builtin_skills() | _get_permanently_uninstalled_skills()
    tools: list[dict[str, Any]] = []
    seen: set[str] = set()

    # 开发模式：先扫仓库内 wasm_bundled，再扫 ~/.jachin/l3_skill_cache。
    # 否则缓存里过期的 hr-analyzer4（体积小、无 NDJSON 批量落盘）会覆盖同 id 的新版，
    # 表现为 ndjson_lines=0、只回传一段演示 Markdown、result/*_analysis.md 永不更新。
    wasm_sources: list[dict[str, Any]] = []
    if not getattr(sys, "frozen", False):
        wasm_sources.extend(_scan_wasm_dir_nested(_WASM_PLUGINS_DIR))
        wasm_sources.extend(_scan_wasm_dir_flat(_WASM_PLUGINS_DIR))
    wasm_sources.extend(_scan_wasm_dir_nested(_L3_SKILL_CACHE_DIR))

    for t in wasm_sources:
        tid = t["id"]
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
    proj = Path(__file__).resolve().parent.parent.parent.parent
    defaults = get_hr_invoke_defaults(config_id)
    if not stdin_json.get("target_role"):
        stdin_json["target_role"] = defaults.get("target_role", "backend_engineer")
    if not stdin_json.get("target_dir"):
        stdin_json["target_dir"] = "data/hr_resumes"
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
    _caller_output_dir_dbg = (stdin_json.get("output_dir") or "").strip()
    _skip_out_keys_dbg = frozenset({"output_dir", "output_dir_use_absolute", "use_absolute_path"})
    for k, v in cfg.items():
        if k and not k.startswith("_") and k not in ("JD_template", "jd_template", "jd_path"):
            if _caller_output_dir_dbg and k in _skip_out_keys_dbg:
                continue
            stdin_json[k] = v
    try:
        from l3_node.hr_reference_time import apply_hr_analysis_reference_time

        apply_hr_analysis_reference_time(stdin_json)
    except Exception:
        pass
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
    lookup_id = tool_id
    t = next((x for x in tools if x["id"] == lookup_id), None)
    if not t:
        print(f"[Skill Execute] [Wasm] 未找到技能 tool_id={tool_id}", file=sys.stderr, flush=True)
        return f"[未知 Wasm 技能: {tool_id}]"
    wasm_path = t.get("_wasm_path", "")
    _HR_BUILTIN_MAP = {"jpp:com.jachin.hr.analyzer4": "hr-analyzer4"}
    if lookup_id in _HR_BUILTIN_MAP and (not wasm_path or not Path(wasm_path).exists()):
        plugin_dir = _HR_BUILTIN_MAP[lookup_id]
        proj_root = Path(__file__).resolve().parent.parent.parent.parent
        candidates = [
            _L3_SKILL_CACHE_DIR / plugin_dir / "main.wasm",
            _L3_SKILL_CACHE_DIR / "hr-analyzer4" / "main.wasm",
            _WASM_PLUGINS_DIR / plugin_dir / "main.wasm",
            proj_root / "l3_node" / "primitives" / "tools" / "wasm_bundled" / plugin_dir / "main.wasm",
            Path.cwd() / "l3_node" / "primitives" / "tools" / "wasm_bundled" / plugin_dir / "main.wasm",
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
    if lookup_id == "jpp:com.jachin.hr.analyzer4":
        config_id = lookup_id.replace("jpp:", "")
        proj = Path(__file__).resolve().parent.parent.parent.parent
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
                    persist_mod = __import__("l3_node.hr_loader", fromlist=["get_hr_analysis_persist"]).get_hr_analysis_persist()
                    _resolve_safe_dir = persist_mod._resolve_safe_dir if persist_mod else (lambda *a, **k: None)
                    _PROJ_ROOT = __import__("l3_node.paths", fromlist=["get_app_root"]).get_app_root()
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
        # 调用方已传 output_dir（如 …/hr_recruitment/<岗>/result）时，禁止 plugin.json 里 data/hr_analysis 覆盖，
        # 否则报告落到项目 data/hr_analysis，职位 result 下看不到 *_analysis.md。
        _caller_output_dir = (stdin_json.get("output_dir") or "").strip()
        _skip_out_keys = frozenset({"output_dir", "output_dir_use_absolute", "use_absolute_path"})
        for k, v in cfg.items():
            if k and not k.startswith("_") and k not in ("JD_template", "jd_template", "jd_path"):
                if _caller_output_dir and k in _skip_out_keys:
                    continue
                stdin_json[k] = v
        try:
            from l3_node.hr_reference_time import apply_hr_analysis_reference_time

            apply_hr_analysis_reference_time(stdin_json)
        except Exception:
            pass
        # resume_path 依赖 cfg，需在 cfg 合并后解析（批量模式 target_dir 时跳过）
        if "resume_path" not in stdin_json and stdin_json.get("resume_filename") and not stdin_json.get("target_dir"):
            fn = stdin_json.get("resume_filename", "zhangsan_resume.md")
            resume_dir_cfg = (cfg.get("resume_input_dir") or "data/hr_resumes").strip()
            use_abs = cfg.get("resume_input_dir_use_absolute") in (True, "true", "1", "yes") or cfg.get("use_absolute_path") in (True, "true", "1", "yes")
            try:
                persist_mod = __import__("l3_node.hr_loader", fromlist=["get_hr_analysis_persist"]).get_hr_analysis_persist()
                _resolve_safe_dir = persist_mod._resolve_safe_dir if persist_mod else (lambda *a, **k: None)
                _PROJ_ROOT = __import__("l3_node.paths", fromlist=["get_app_root"]).get_app_root()
                resume_dir = _resolve_safe_dir(resume_dir_cfg, _PROJ_ROOT, use_absolute_path=use_abs)
                if resume_dir and (resume_dir / fn).exists():
                    stdin_json["resume_path"] = str((resume_dir / fn).resolve())
                elif (proj / "data" / "hr_resumes" / fn).exists():
                    stdin_json["resume_path"] = str((proj / "data" / "hr_resumes" / fn).resolve())
            except Exception:
                if (proj / "data" / "hr_resumes" / fn).exists():
                    stdin_json["resume_path"] = str((proj / "data" / "hr_resumes" / fn).resolve())
    # HR 技能需 LLM，执行前校验
    if lookup_id == "jpp:com.jachin.hr.analyzer4":
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
    if lookup_id == "jpp:com.jachin.hr.analyzer4" and _jd_in:
        _tr = (stdin_json.get("target_role") or "backend_engineer").strip() or "backend_engineer"
        _sync_hr_caller_jd_to_skill_jds_files((stdin_json.get("jd_template") or "").strip(), _tr)
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
        _fuel = 50_000_000 if lookup_id == "jpp:com.jachin.hr.analyzer4" else 200_000
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
        if ndjson_queue is None and lookup_id == "jpp:com.jachin.hr.analyzer4":
            _err_prefixes = ("⚠️", "[权限", "[未知", "[Wasm", "[执行")
            try:
                persist_mod = __import__("l3_node.hr_loader", fromlist=["get_hr_analysis_persist"]).get_hr_analysis_persist()
                persist_hr_analysis_result = persist_mod.persist_hr_analysis_result if persist_mod else None
                count = _persist_hr_analyzer_ndjson_batch(stdin_json, lookup_id)
                if count > 0:
                    result_str = f"✅ 执行成功，本次分析了 {count} 份简历。报告已保存至 data/hr_analysis/ 目录。"
                elif result_str and len(result_str.strip()) > 20 and not any(result_str.strip().startswith(p) for p in _err_prefixes):
                    # 回退：解析 JSON 数组（旧版 Wasm 输出）
                    persist_hr_analysis_batch_item = persist_mod.persist_hr_analysis_batch_item if persist_mod else None
                    cfg = _fetch_skill_config(lookup_id.replace("jpp:", ""))
                    cfg = {**_get_hr_plugin_config_defaults(lookup_id), **(cfg or {})}
                    _caller_out = (stdin_json.get("output_dir") or "").strip()
                    if _caller_out:
                        cfg = {
                            **cfg,
                            "output_dir": _caller_out,
                            "output_dir_use_absolute": True,
                            "use_absolute_path": True,
                        }
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
                    jcount = 0
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
                            if persist_hr_analysis_batch_item:
                                persist_hr_analysis_batch_item(lookup_id, report, stem, config=cfg)
                            jcount += 1
                        if jcount > 0:
                            result_str = f"✅ 执行成功，本次分析了 {jcount} 份简历。报告已保存至 data/hr_analysis/ 目录。"
                    else:
                        if persist_hr_analysis_result:
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
        _partial = 0
        if ndjson_queue is None and lookup_id == "jpp:com.jachin.hr.analyzer4":
            _partial = _persist_hr_analyzer_ndjson_batch(stdin_json, lookup_id)
            if _partial > 0:
                print(
                    f"[Skill Execute] Wasm 异常但已将前 {_partial} 份 progress 落盘（单次大模型调用对应一条 progress）",
                    file=sys.stderr,
                    flush=True,
                )
        print(f"[Skill Execute] [Wasm] 异常 tool_id={tool_id} error={e}", file=sys.stderr, flush=True)
        logger.warning("[Skills] Wasm 执行失败 %s: %s", tool_id, e)
        if _partial > 0:
            return (
                f"⚠️ HR 透析镜批量在第 {_partial + 1} 份附近中断：已完成 {_partial} 份报告已写入 output_dir，"
                f"请检查后续 PDF 或改用逐份调用（hr_analyze_resume 多文件默认已逐份 Wasm）。\n\n原错误：{e}"
            )
        return f"[Wasm 执行失败: {e}]"


def _run_mcp_tool_via_registry_sync(
    tool_id: str,
    work_order_input: str,
    allowed_skills: Optional[list[str]],
) -> str:
    """
    Native/JPP loader 的 MCP 统一出口。

    Tool pool 会把本地和远端 MCP 暴露为 ``mcp:*``，但 loader 本身只执行 core/JPP。
    这里把所有 MCP 调用交给 MCP Registry，避免能力目录里“看得见”而真实执行层报未知工具。
    """
    import asyncio
    import threading

    async def _invoke() -> str:
        from l3_node.primitives.mcp.registry import get_mcp_registry

        return await get_mcp_registry().invoke(
            tool_id,
            work_order_input,
            allowed_skills=allowed_skills,
        )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_invoke())

    if not loop.is_running():
        return loop.run_until_complete(_invoke())

    box: dict[str, Any] = {}

    def _runner() -> None:
        try:
            box["result"] = asyncio.run(_invoke())
        except BaseException as exc:  # pragma: no cover - re-raised below
            box["error"] = exc

    thread = threading.Thread(target=_runner, name="jachin-mcp-registry-sync-dispatch", daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return str(box.get("result") or "")


def run_tool(
    tool_id: str,
    work_order_input: str,
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
    inp = (work_order_input or "").strip()
    print(f"[Skill Execute] run_tool 入口 tool_id={tool_id} input_len={len(inp)}", file=sys.stderr, flush=True)

    if not is_tool_allowed(tool_id, allowed_skills):
        print(f"[Skill Execute] 权限拒绝 tool_id={tool_id}", file=sys.stderr, flush=True)
        return "[权限拒绝: 当前子账号未开启该技能]"

    if tool_id.startswith("mcp:"):
        print(
            f"[Skill Execute] [MCP Registry] 委托执行 tool_id={tool_id}",
            file=sys.stderr,
            flush=True,
        )
        try:
            return _run_mcp_tool_via_registry_sync(tool_id, inp, allowed_skills)
        except Exception as e:
            print(
                f"[Skill Execute] [MCP Registry] 异常 tool_id={tool_id} error={e}",
                file=sys.stderr,
                flush=True,
            )
            return json.dumps(
                {
                    "ok": False,
                    "status": "failed",
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "tool_id": tool_id,
                    "channel": "mcp_registry",
                },
                ensure_ascii=False,
            )

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

    if tool_id == "core:submit_background_task":
        from l3_node.primitives.agent_tasks.background_task_service import submit_background_task_sync

        return submit_background_task_sync(inp, allowed_skills=allowed_skills)
    if tool_id == "core:check_background_task":
        from l3_node.primitives.agent_tasks.background_task_service import check_background_task_status_sync

        return check_background_task_status_sync(inp)
    if tool_id == "core:check_interrupted_tasks":
        from l3_node.primitives.agent_tasks.background_task_service import check_interrupted_tasks_sync

        return check_interrupted_tasks_sync(inp)

    if tool_id.startswith("util:") or tool_id.startswith("sys:"):
        util_params: dict[str, Any] = {}
        if inp.strip().startswith("{"):
            try:
                o = json.loads(inp)
                if isinstance(o, dict):
                    util_params = o
            except json.JSONDecodeError:
                pass
        _merge_xml_params_into_util(util_params, _parse_work_order_xml_tool_params(inp))
        print(
            f"[Skill Execute] [Native util/sys] tool_id={tool_id} params_keys={list(util_params.keys())}",
            file=sys.stderr,
            flush=True,
        )
        try:
            ures = _invoke_native(tool_id, **util_params)
            if isinstance(ures, dict):
                return json.dumps(ures, ensure_ascii=False, indent=2)
            return str(ures)
        except Exception as e:
            return f"[执行失败: {e}]"

    params = {}
    if tool_id == "core:fs_read":
        # 与 core:fs_write 一致：模型常输出 JSON {"file_path":"..."}，不可整段当路径（否则会拼成 …/workspace/{"file_path":…} 触发 Errno 22）
        params["file_path"] = ""
        if inp.strip().startswith("{"):
            try:
                o = json.loads(inp)
                if isinstance(o, dict):
                    fp = str(o.get("file_path") or o.get("path") or "").strip()
                    if fp:
                        params["file_path"] = fp
            except json.JSONDecodeError:
                pass
        if not params.get("file_path"):
            params["file_path"] = (inp or "target.txt").strip()
    elif tool_id == "core:shell_exec":
        params["background"] = False
        params["timeout"] = 30
        if inp.strip().startswith("{"):
            try:
                obj = json.loads(inp)
                if isinstance(obj, dict):
                    params["command"] = str(obj.get("command", "") or "")
                    if obj.get("timeout") is not None:
                        try:
                            params["timeout"] = int(obj["timeout"])
                        except (TypeError, ValueError):
                            params["timeout"] = 30
                    params["background"] = bool(obj.get("background", False))
                    if obj.get("sandbox_profile") is not None:
                        params["sandbox_profile"] = str(obj.get("sandbox_profile") or "")
                else:
                    params["command"] = inp
            except json.JSONDecodeError:
                params["command"] = inp
        else:
            params["command"] = inp
    elif tool_id == "core:shell_job_status":
        jid = inp.strip()
        if jid.startswith("{"):
            try:
                o = json.loads(jid)
                if isinstance(o, dict):
                    jid = str(o.get("job_id", "") or "").strip()
            except json.JSONDecodeError:
                pass
        params["job_id"] = jid
    elif tool_id == "core:shell_job_cancel":
        jid = inp.strip()
        if jid.startswith("{"):
            try:
                o = json.loads(jid)
                if isinstance(o, dict):
                    jid = str(o.get("job_id", "") or "").strip()
            except json.JSONDecodeError:
                pass
        params["job_id"] = jid
    elif tool_id == "core:local_memory_search":
        params["query"] = ""
        params["top_k"] = 8
        params["mmr_lambda"] = 0.55
        params["half_life_days"] = 30.0
        params["include_memory_md"] = True
        if inp.strip().startswith("{"):
            try:
                o = json.loads(inp)
                if isinstance(o, dict):
                    params["query"] = str(o.get("query") or "").strip()
                    if o.get("top_k") is not None:
                        params["top_k"] = o.get("top_k")
                    if o.get("mmr_lambda") is not None:
                        params["mmr_lambda"] = o.get("mmr_lambda")
                    if o.get("half_life_days") is not None:
                        params["half_life_days"] = o.get("half_life_days")
                    if "include_memory_md" in o:
                        params["include_memory_md"] = o.get("include_memory_md")
            except json.JSONDecodeError:
                params["query"] = inp.strip()
        else:
            params["query"] = inp.strip()
    elif tool_id == "core:local_memory_append":
        params["content"] = ""
        params["tags"] = None
        if inp.strip().startswith("{"):
            try:
                o = json.loads(inp)
                if isinstance(o, dict):
                    params["content"] = str(o.get("content") or o.get("text") or o.get("body") or "").strip()
                    if o.get("tags") is not None:
                        params["tags"] = o.get("tags")
            except json.JSONDecodeError:
                params["content"] = inp.strip()
        else:
            params["content"] = inp.strip()
    elif tool_id == "core:apply_patch":
        params["patch_text"] = inp
        if inp.strip().startswith("{"):
            try:
                o = json.loads(inp)
                if isinstance(o, dict):
                    params["patch_text"] = str(o.get("patch_text") or o.get("unified_diff") or "")
                    if o.get("session_hint") is not None:
                        params["session_hint"] = str(o.get("session_hint", ""))
            except json.JSONDecodeError:
                pass
    elif tool_id == "core:domain_workflow_run":
        body: dict[str, Any] = {}
        if inp.strip().startswith("{"):
            try:
                o = json.loads(inp)
                if isinstance(o, dict):
                    body = dict(o)
            except json.JSONDecodeError:
                pass
        dom = str(body.pop("domain_id", "") or body.pop("domain", "") or "").strip()
        params = {"domain_id": dom, "params": body if body else {}}
    elif tool_id == "core:workflow_run":
        params["yaml_path"] = inp.strip()
        params["persistent"] = False
        params["run_id"] = "default"
        params["resume"] = False
        params["reset"] = False
        params["keep_completed_state"] = False
        if inp.strip().startswith("{"):
            try:
                o = json.loads(inp)
                if isinstance(o, dict):
                    params["yaml_path"] = str(o.get("yaml_path") or o.get("workflow_yaml") or "")
                    if "persistent" in o:
                        params["persistent"] = bool(o.get("persistent"))
                    if o.get("run_id") is not None:
                        params["run_id"] = str(o.get("run_id") or "default")
                    if "resume" in o:
                        params["resume"] = bool(o.get("resume"))
                    if "reset" in o:
                        params["reset"] = bool(o.get("reset"))
                    if "keep_completed_state" in o:
                        params["keep_completed_state"] = bool(o.get("keep_completed_state"))
            except json.JSONDecodeError:
                pass
    elif tool_id == "core:apply_patch_rollback":
        params["backup_id"] = None
        s = inp.strip()
        if s.startswith("{"):
            try:
                o = json.loads(s)
                if isinstance(o, dict) and o.get("backup_id") is not None:
                    params["backup_id"] = str(o.get("backup_id") or "").strip() or None
            except json.JSONDecodeError:
                pass
        elif s:
            params["backup_id"] = s
    elif tool_id == "core:shell_hitl_approve":
        params["hash_hex"] = None
        params["command"] = None
        params["pending_id"] = None
        if inp.strip().startswith("{"):
            try:
                o = json.loads(inp)
                if isinstance(o, dict):
                    params["hash_hex"] = o.get("hash_hex") or o.get("hash")
                    params["command"] = o.get("command")
                    params["pending_id"] = o.get("pending_id")
            except json.JSONDecodeError:
                pass
    elif tool_id == "core:safety_lock_append":
        params["body"] = ""
        params["source"] = "work_order"
        params["tags"] = None
        params["token"] = None
        params["category"] = None
        if inp.strip().startswith("{"):
            try:
                o = json.loads(inp)
                if isinstance(o, dict):
                    params["body"] = str(o.get("body") or o.get("content") or o.get("text") or "")
                    if o.get("source") is not None:
                        params["source"] = str(o.get("source") or "work_order")
                    if o.get("tags") is not None:
                        params["tags"] = o.get("tags")
                    if o.get("token") is not None:
                        params["token"] = o.get("token")
                    elif o.get("secret_token") is not None:
                        params["token"] = o.get("secret_token")
                    if o.get("category") is not None:
                        params["category"] = str(o.get("category") or "").strip() or None
            except json.JSONDecodeError:
                params["body"] = inp
        else:
            params["body"] = inp
    elif tool_id == "core:safety_lock_list_pending":
        pass
    elif tool_id == "core:safety_lock_remove":
        params["entry_id"] = ""
        if inp.strip().startswith("{"):
            try:
                o = json.loads(inp)
                if isinstance(o, dict):
                    params["entry_id"] = str(o.get("entry_id") or o.get("id") or "").strip()
            except json.JSONDecodeError:
                pass
        elif inp.strip():
            params["entry_id"] = inp.strip()
    elif tool_id == "core:compose_essay":
        params = {
            "topic": "",
            "style_id": "",
            "style_label": "",
            "word_count_target": 600,
            "audience": "通用",
            "tone": "正式",
            "structure": "总-分-总",
        }
        if inp.strip().startswith("{"):
            try:
                o = json.loads(inp)
                if isinstance(o, dict):
                    params["topic"] = str(o.get("topic") or "").strip()
                    params["style_id"] = str(o.get("style_id") or o.get("styleId") or "").strip()
                    params["style_label"] = str(o.get("style_label") or o.get("styleLabel") or "").strip()
                    if o.get("word_count_target") is not None or o.get("wordCountTarget") is not None:
                        params["word_count_target"] = o.get("word_count_target", o.get("wordCountTarget", 600))
                    if o.get("audience") is not None:
                        params["audience"] = str(o.get("audience") or "通用").strip()
                    if o.get("tone") is not None:
                        params["tone"] = str(o.get("tone") or "正式").strip()
                    if o.get("structure") is not None:
                        params["structure"] = str(o.get("structure") or "总-分-总").strip()
            except json.JSONDecodeError:
                params["topic"] = inp.strip()
        else:
            params["topic"] = inp.strip()
    elif tool_id == "core:youtube_transcript":
        params["url"] = ""
        params["languages"] = None
        if inp.strip().startswith("{"):
            try:
                o = json.loads(inp)
                if isinstance(o, dict):
                    u = str(o.get("url") or o.get("video_url") or "").strip()
                    if u:
                        params["url"] = u
                    if o.get("languages") is not None:
                        params["languages"] = o.get("languages")
            except json.JSONDecodeError:
                pass
        if not params.get("url"):
            params["url"] = inp.strip()
    elif tool_id == "core:akshare_a_share_hist":
        params = {
            "symbol": "",
            "start_date": "",
            "end_date": "",
            "period": "daily",
            "adjust": "qfq",
        }
        if inp.strip().startswith("{"):
            try:
                o = json.loads(inp)
                if isinstance(o, dict):
                    params["symbol"] = str(o.get("symbol") or "")
                    params["start_date"] = str(o.get("start_date") or "")
                    params["end_date"] = str(o.get("end_date") or "")
                    if o.get("period") is not None:
                        params["period"] = str(o.get("period") or "daily")
                    if o.get("adjust") is not None:
                        params["adjust"] = str(o.get("adjust") or "qfq")
            except json.JSONDecodeError:
                pass
    elif tool_id == "core:akshare_company_info":
        params = {"symbol": "", "report_rows": 12}
        if inp.strip().startswith("{"):
            try:
                o = json.loads(inp)
                if isinstance(o, dict):
                    params["symbol"] = str(o.get("symbol") or "")
                    if o.get("report_rows") is not None:
                        try:
                            params["report_rows"] = int(o.get("report_rows"))
                        except (TypeError, ValueError):
                            params["report_rows"] = 12
            except json.JSONDecodeError:
                pass
    elif tool_id == "core:yfinance_global_market_hist":
        params = {"ticker": "", "period": "1mo", "interval": "1d"}
        if inp.strip().startswith("{"):
            try:
                o = json.loads(inp)
                if isinstance(o, dict):
                    params["ticker"] = str(o.get("ticker") or "")
                    if o.get("period") is not None:
                        params["period"] = str(o.get("period") or "1mo")
                    if o.get("interval") is not None:
                        params["interval"] = str(o.get("interval") or "1d")
            except json.JSONDecodeError:
                pass
    elif tool_id == "core:yfinance_ticker_info":
        params = {"ticker": ""}
        if inp.strip().startswith("{"):
            try:
                o = json.loads(inp)
                if isinstance(o, dict):
                    params["ticker"] = str(o.get("ticker") or "")
            except json.JSONDecodeError:
                pass
    elif tool_id == "core:fs_write":
        parsed_fs = False
        if inp.strip().startswith("{"):
            try:
                o = json.loads(inp)
                if isinstance(o, dict):
                    fp = str(o.get("file_path") or o.get("path") or "").strip()
                    ct = o.get("content")
                    # 与 RoleExecutor 常见输出对齐：整段 JSON 即一次写入
                    if fp or ct is not None:
                        params["file_path"] = fp
                        params["content"] = "" if ct is None else str(ct)
                        parsed_fs = True
            except json.JSONDecodeError:
                pass
        if not parsed_fs:
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
        try:
            from l3_node.primitives.tools.native_extensions import (
                dispatch_native_extension_tool,
                is_native_extension_tool,
                parse_native_extension_work_order_input,
            )
        except Exception:
            dispatch_native_extension_tool = None  # type: ignore[assignment]
            is_native_extension_tool = None  # type: ignore[assignment]
            parse_native_extension_work_order_input = None  # type: ignore[assignment]

        if is_native_extension_tool and is_native_extension_tool(tool_id):
            params = parse_native_extension_work_order_input(tool_id, inp) if parse_native_extension_work_order_input else {}
            print(
                f"[Skill Execute] [Native Extension] 调用 tool_id={tool_id}",
                file=sys.stderr,
                flush=True,
            )
            try:
                result = dispatch_native_extension_tool(tool_id, **params) if dispatch_native_extension_tool else None
                if isinstance(result, dict):
                    ft = (result.get("formatted_text") or "").strip()
                    if ft:
                        body = {k: v for k, v in result.items() if k != "formatted_text"}
                        return (
                            ft
                            + "\n\n---\n[结构化 JSON · FanOut/合并请用下列键，勿用 Markdown #### 重排]\n"
                            + json.dumps(body, ensure_ascii=False, indent=2)
                        )
                    return json.dumps(result, ensure_ascii=False, indent=2)
                return str(result)
            except Exception as e:
                return f"[执行失败: {e}]"
        print(f"[Skill Execute] 未知工具 tool_id={tool_id}", file=sys.stderr, flush=True)
        return f"[未知工具: {tool_id}]"

    print(f"[Skill Execute] [Native] 调用 tool_id={tool_id} params={params}", file=sys.stderr, flush=True)
    try:
        if tool_id == "core:shell_exec":
            from l3_node.intelligence_p1 import assert_shell_exec_allowed
            from l3_node.shell_hitl import assert_shell_hitl_approved

            assert_shell_exec_allowed(str(params.get("command", "") or ""))
            assert_shell_hitl_approved(str(params.get("command", "") or ""))

        fs_cache_inp = ""
        if tool_id == "core:fs_read":
            from l3_node.tool_call_cache import try_get_cached, store_if_cacheable

            fs_cache_inp = json.dumps(
                {"file_path": str(params.get("file_path", "") or "")},
                sort_keys=True,
                ensure_ascii=False,
            )
            _hit = try_get_cached("core:fs_read", fs_cache_inp)
            if _hit is not None:
                return _hit

        result = _invoke_native(tool_id, **params)
        out_str = str(result)[:200] if result else ""
        print(f"[Skill Execute] [Native] 返回 tool_id={tool_id} result_preview={out_str}...", file=sys.stderr, flush=True)
        if isinstance(result, dict):
            if tool_id == "core:fs_write" and result.get("ok") is True:
                return "ok"
            if tool_id in (
                "core:apply_patch",
                "core:apply_patch_rollback",
                "core:workflow_run",
                "core:domain_workflow_run",
                "core:shell_hitl_approve",
                "core:local_memory_search",
                "core:local_memory_append",
                "core:safety_lock_append",
                "core:safety_lock_list_pending",
                "core:safety_lock_remove",
                "core:youtube_transcript",
                "core:akshare_a_share_hist",
                "core:akshare_company_info",
                "core:yfinance_global_market_hist",
                "core:yfinance_ticker_info",
            ):
                return json.dumps(result, ensure_ascii=False, indent=2)
            if result.get("background") and result.get("job_id"):
                jid = result.get("job_id")
                return (
                    f"[后台 shell 已启动] job_id={jid} pid={result.get('pid')} log={result.get('log_path')}\n"
                    f"查询状态请使用 WorkOrder: core:shell_job_status，tool input: "
                    f"{json.dumps({'job_id': jid}, ensure_ascii=False)}"
                )
            out = result.get("stdout", "")
            err = result.get("stderr", "")
            code = result.get("returncode", 0)
            if err:
                msg = f"[exit {code}]\nstdout: {out}\nstderr: {err}"
            else:
                msg = out or f"[exit {code}]"
            if tool_id == "core:fs_read" and fs_cache_inp:
                from l3_node.tool_call_cache import store_if_cacheable

                return store_if_cacheable("core:fs_read", fs_cache_inp, msg)
            return msg
        text_result = str(result)
        if tool_id == "core:fs_read" and fs_cache_inp:
            from l3_node.tool_call_cache import store_if_cacheable

            return store_if_cacheable("core:fs_read", fs_cache_inp, text_result)
        return text_result
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
    包含 Native Core 与 l3_node/primitives/tools/wasm_bundled/ 下的 JPP .wasm 插件。

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


# npm「mcp-sqlite」等社区 MCP 的工具名。官方 @modelcontextprotocol/server-sqlite 未在 npm 发布（404），见 tools/mcp-official。
MCP_SQLITE_COMMUNITY_TOOL_RAW = frozenset(
    {
        "query",
        "db_info",
        "list_tables",
        "get_table_schema",
        "read_records",
        "create_record",
        "update_records",
        "delete_records",
    }
)

# 白名单 merge_sqlite_read 仅追加「探表/只读 SQL」相关 id，不含 create/update/delete_record
MCP_SQLITE_MERGE_READISH_IDS: tuple[str, ...] = (
    "mcp:read_query",
    "read_query",
    "mcp:query",
    "query",
    "mcp:list_tables",
    "list_tables",
    "mcp:get_table_schema",
    "get_table_schema",
    "mcp:db_info",
    "db_info",
    "mcp:read_records",
    "read_records",
)


def tool_entry_looks_like_sqlite_family(t: Any) -> bool:
    """工具字典是否像 MCP SQLite（read_query/write_query 或 id/desc 含 sqlite）。"""
    if not isinstance(t, dict):
        return False
    tid = str(t.get("id") or t.get("label") or "").strip().lower()
    desc = str(t.get("desc") or t.get("description") or "").lower()
    if "sqlite" in tid or "sqlite" in desc:
        return True
    # 少数注册路径仅保留裸名（无 mcp: 前缀），须与 mcp:read_query 同等识别
    if tid in ("read_query", "write_query"):
        return True
    if tid in MCP_SQLITE_COMMUNITY_TOOL_RAW:
        return True
    if tid.startswith("mcp:"):
        r = tid[4:].strip().lower()
        if r in ("read_query", "write_query"):
            return True
        if "read_query" in r or "write_query" in r:
            return True
        if r in MCP_SQLITE_COMMUNITY_TOOL_RAW:
            return True
    return False


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
    tid = (tool_id or "").strip().lower()
    if tid in allowed_ids:
        return True
    # P1+：状态/取消与 shell_exec 绑定，避免白名单漏配导致后台任务无法查询
    if tid in ("core:shell_job_status", "core:shell_job_cancel") and (
        "core:shell_exec" in allowed_ids or "shell_exec" in allowed_ids
    ):
        return True
    if tid == "core:check_background_task" and (
        "core:submit_background_task" in allowed_ids or "submit_background_task" in allowed_ids
    ):
        return True
    if tid == "core:local_memory_append" and (
        "core:local_memory_append" in allowed_ids
        or "local_memory_append" in allowed_ids
        or "core:local_memory_search" in allowed_ids
        or "local_memory_search" in allowed_ids
    ):
        return True
    if tid.startswith("util:") or tid.startswith("sys:"):
        if "native:utility_tools" in allowed_ids or "util:*" in allowed_ids or "sys:*" in allowed_ids:
            return True
    # L2 白名单非空时，默认仅放行列表内 id；显式加入 mcp:* 可放行已在 mcp_servers.json 注册的 MCP（见 tool_pool.merge_local_mcp）
    if tid.startswith("mcp:") and "mcp:*" in allowed_ids:
        return True
    return False


def _mcp_tool_raw_name(tool_id: str) -> str:
    s = (tool_id or "").strip()
    if s.lower().startswith("mcp:"):
        return s[4:].strip().lower()
    return s.lower()


# 与 core.mcp_client.normalize_mcp_schema_aliases 对齐：提示模型用 path 而非 file_path
_FILE_MCP_RAW = frozenset(
    {
        "write_file",
        "read_file",
        "edit_file",
        "create_file",
        "delete_file",
        "move_file",
        "copy_file",
        "get_file_info",
        "list_directory",
        "search_files",
    }
)


def build_tools_description(tools: list[dict[str, Any]]) -> str:
    """生成 Agent system prompt 中的工具描述段落。含 id 供 WorkOrder 精确匹配。"""
    _HR_IDS = ("jpp:com.jachin.hr.analyzer4",)
    lines = []
    for t in tools:
        tid = t.get("id", "")
        desc = t.get("desc", t.get("label", tid))
        if tid in _HR_IDS:
            desc = f"{desc} 【简历分析时直接调用，参数可从技能配置自动读取，可传空对象 {{}}】"
        param_hint = ""
        raw = _mcp_tool_raw_name(tid)
        isc = t.get("inputSchema")
        if isinstance(isc, dict) and isinstance(isc.get("properties"), dict) and isc["properties"]:
            keys = list(isc["properties"].keys())
            req = isc.get("required")
            if isinstance(req, list) and req:
                param_hint = f" tool input 为 JSON；必填键：{', '.join(str(x) for x in req)}。"
            else:
                param_hint = f" tool input 为 JSON；字段：{', '.join(keys)}。"
        else:
            params = t.get("params")
            if isinstance(params, list) and params:
                param_hint = f" tool input 为 JSON，键名（须与 schema 一致）：{', '.join(str(p) for p in params)}。"
        if raw in _FILE_MCP_RAW:
            param_hint += " 【文件类 MCP】键名须为 `path`，不要用 `file_path`。"
            if raw in ("write_file", "edit_file", "create_file"):
                param_hint += " `path` 与 `content` 均须提供。"
        if raw == "write_query":
            param_hint += (
                " 【SQLite·写门禁】数据变更前须先按参谋长格式向统帅悬挂请示；获准后在**本条** JSON 内增加 "
                "`jachin_mcp_write_ack`: true，否则调用会被系统拦截。该键仅用于 Jachin 门禁，不会传给数据库。"
            )
        elif raw == "read_query":
            param_hint += (
                " 【SQLite·只读】`query` 须为 SELECT。用户问「缺货」时应在 SQL 中体现条件（如 WHERE quantity=0 "
                "或与表列名一致），勿依赖 SELECT * 再在文中猜谁缺货。"
            )
        elif raw == "query":
            param_hint += (
                " 【SQLite·只读】npm mcp-sqlite：自定义 SQL 用键名 `sql`（见 schema）。SELECT 查缺货请写清条件（如 quantity=0）。"
            )
        elif "sqlite" in (tid or "").lower():
            param_hint += (
                " 【SQLite 相关】含写语句时须签批并在 JSON 中带 `jachin_mcp_write_ack`: true；只读请用 SELECT。"
            )
        lines.append(f"- {tid} ({t.get('label', tid)}): {desc}{param_hint}")
    return "\n".join(lines) if lines else "（无可用工具）"
