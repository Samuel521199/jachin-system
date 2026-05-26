"""
Jachin Nexus v8.0 - Native Core 内置标准库

写路径白名单见 l3_node.primitives.native_write_allowlist（workspace、HR 数据卷、Desktop/Downloads/Documents 等）。
读路径：与写解耦，仅受 l3_node.primitives.fs_path_blacklist 敏感路径拦截（非黑名单即可读，含各盘符业务目录）。
HR 透析镜相对路径解析不变。
越界访问抛出 SecurityException。
"""
from __future__ import annotations

import json
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

# 工作目录根，绝对不可越界
_WORKSPACE_ROOT = Path.home() / ".jachin" / "workspace"


def _dedupe_pmo_lark_pull_segments(raw: str) -> str:
    """Manifest files[] 常为 ``pmo_lark_pull\\\\01_…``，与 output_dir 拼接会出现双段 pmo_lark_pull。"""
    s = (raw or "").replace("\\", "/")
    while "pmo_lark_pull/pmo_lark_pull/" in s.lower():
        s = re.sub(r"pmo_lark_pull/pmo_lark_pull/", "pmo_lark_pull/", s, flags=re.I)
    return s


def _read_text_file(p: Path) -> str:
    if p.suffix.lower() == ".pdf":
        from core.pdf_extractor import extract_pdf_text

        return extract_pdf_text(p) or ""
    return p.read_text(encoding="utf-8", errors="replace")


def try_resolve_pmo_lark_md_if_missing(requested: Path) -> Path | None:
    """
    PMO 拉表 md 路径回退：项目根或 workspace 下 ``pmo_lark_pull``，
    先按 basename，再按 ``_vew…`` 后缀匹配（缓解 Agent 复制错文件名）。
    """
    try:
        req = requested.expanduser()
        if req.exists() and req.is_file():
            return req.resolve()
        bn = req.name
        if not bn.lower().endswith(".md"):
            return None
        view_m = re.search(r"(_vew[a-zA-Z0-9]+\.md)$", bn, re.I)
        view_suffix = view_m.group(1) if view_m else None

        search_roots: list[Path] = []
        ws = _WORKSPACE_ROOT.resolve()
        for base in (ws / "pmo_lark_pull", _PROJ_ROOT / "pmo_lark_pull"):
            if base.is_dir():
                search_roots.append(base)
                try:
                    for sub in base.iterdir():
                        if sub.is_dir():
                            search_roots.append(sub)
                except OSError:
                    pass

        def _collect(root: Path, pattern: str) -> list[Path]:
            out: list[Path] = []
            try:
                out.extend(p for p in root.glob(pattern) if p.is_file())
            except OSError:
                pass
            return out

        for root in search_roots:
            exact = root / bn
            if exact.is_file():
                return exact.resolve()
            for hit in _collect(root, bn):
                return hit.resolve()

        if view_suffix:
            hits: list[Path] = []
            for root in search_roots:
                hits.extend(_collect(root, f"*{view_suffix}"))
            if len(hits) == 1:
                return hits[0].resolve()
            if hits:
                try:
                    return max(hits, key=lambda p: p.stat().st_mtime).resolve()
                except OSError:
                    return hits[0].resolve()
        return None
    except (OSError, RuntimeError, ValueError):
        return None


def try_resolve_workspace_file_if_missing(requested: Path) -> Path | None:
    """
    当请求路径位于 ~/.jachin/workspace 下但文件不存在时，按 basename（及同名父目录）在
    pmo_lark_pull 与整 workspace 内回退查找，缓解 Manifest 序号/路径漂移与 Agent 复制路径误差。

    返回解析到的真实 Path；无法匹配或未处于 workspace 下则返回 None。
    """
    try:
        req = requested.expanduser()
        if req.exists() and req.is_file():
            return req.resolve()
        ws = _WORKSPACE_ROOT.resolve()
        try:
            req.resolve().relative_to(ws)
        except ValueError:
            return None
        bn = req.name
        if not bn:
            return None
        want_parent = req.parent.name
        candidates: list[Path] = []
        pull = ws / "pmo_lark_pull"
        if pull.is_dir():
            try:
                candidates.extend(pull.rglob(bn))
            except OSError:
                pass
        if not candidates:
            try:
                candidates.extend(ws.rglob(bn))
            except OSError:
                pass
        files = [c for c in candidates if c.is_file()]
        if not files and re.search(r"_vew[a-zA-Z0-9]+\.md$", bn, re.I) and pull.is_dir():
            ix = bn.lower().rfind("_vew")
            if ix >= 0:
                suffix_glob = f"*{bn[ix:]}"
                try:
                    for sub in pull.iterdir():
                        if not sub.is_dir():
                            continue
                        if want_parent and sub.name != want_parent:
                            continue
                        for cand in sub.glob(suffix_glob):
                            if cand.is_file():
                                files.append(cand)
                except OSError:
                    pass
        if not files:
            return None
        if len(files) == 1:
            return files[0].resolve()
        same_parent = [c for c in files if c.parent.name == want_parent]
        if len(same_parent) == 1:
            return same_parent[0].resolve()
        try:
            return max(same_parent or files, key=lambda x: x.stat().st_mtime).resolve()
        except OSError:
            return (same_parent[0] if same_parent else files[0]).resolve()
    except (OSError, RuntimeError, ValueError):
        return None


# 项目根（core 位于 project/core/）
_PROJ_ROOT = Path(__file__).resolve().parent.parent
# L3 本地数据卷（Boss 收网 PDF 蓄水池）
_L3_VOLUME_ROOT = Path.home() / ".jachin" / "client_volumes"


def _get_hr_read_allowed() -> list[Path]:
    """HR 透析镜白名单（规范 075: config/skills/.../hr_jds）"""
    from l3_node.jachin_config import get_hr_jds_dir
    return [
        _L3_VOLUME_ROOT.resolve(),
        (_PROJ_ROOT / "data" / "hr_resumes").resolve(),
        get_hr_jds_dir(_PROJ_ROOT).resolve(),
    ]


_HR_READ_ALLOWED = None  # 延迟初始化避免循环导入


def _hr_allowed() -> list[Path]:
    global _HR_READ_ALLOWED
    if _HR_READ_ALLOWED is None:
        _HR_READ_ALLOWED = _get_hr_read_allowed()
    return _HR_READ_ALLOWED


class SecurityException(Exception):
    """Wasm/Native Core sandbox violation"""


def _is_under_hr_whitelist(path: Path) -> bool:
    """路径是否在 HR 透析镜白名单（data/hr_resumes、config/skills/.../hr_jds）下"""
    try:
        abs_path = path.resolve()
        for allowed in _hr_allowed():
            if str(abs_path).startswith(str(allowed)):
                return True
    except (OSError, RuntimeError):
        pass
    return False


def _assert_under_workspace(path: Path) -> None:
    """断言路径在 Native 写白名单内（workspace、HR 卷、Desktop/Documents/Downloads 等），否则抛出 SecurityException。"""
    from l3_node.primitives.native_write_allowlist import assert_path_allowed_for_native_write

    try:
        assert_path_allowed_for_native_write(path)
    except ValueError as e:
        raise SecurityException(str(e)) from e


def _assert_read_allowed(path: Path) -> None:
    """读取：仅敏感路径黑名单；通过则放行（含 D:\\、E:\\ 等业务路径）。"""
    from l3_node.primitives.native_write_allowlist import assert_path_allowed_for_native_read

    try:
        assert_path_allowed_for_native_read(path)
    except ValueError as e:
        raise SecurityException(str(e)) from e


def _fs_read_file_not_found_hint(resolved: Path) -> str:
    """文件不存在时返回可检索提示，引导模型使用对话内已注入的附件正文，勿臆造 Downloads 路径。"""
    return (
        "[执行失败: 文件不存在] "
        f"路径: {resolved}。"
        " 说明：通过 Omni/聊天上传的附件，正文通常已出现在用户消息中的「[附件: … 内容]」块，"
        "L3 进程所在机器上不一定存在同名的 ~/Downloads/ 或桌面路径，请勿猜测本机路径重复读取。"
        " 请直接根据对话中的附件正文作答；仅当确认文件已落在 workspace / 白名单目录且路径可靠时再使用本工具。"
    )


def core_fs_read(file_path: str) -> str:
    """
    读取本机已存在文件。默认允许常规盘符与业务目录；仅拒绝敏感路径黑名单（密钥、系统目录等，见 fs_path_blacklist）。
    相对路径仍先按 HR 透析镜与 workspace 规则解析。
    PDF 自动提取纯文本。

    Args:
        file_path: 相对或绝对路径

    Returns:
        文件内容

    Raises:
        SecurityException: 命中敏感路径黑名单
    """
    raw_in = (file_path or "").strip()
    if len(raw_in) >= 2 and (
        (raw_in[0] == raw_in[-1] == '"') or (raw_in[0] == raw_in[-1] == "'")
    ):
        raw_in = raw_in[1:-1].strip()
    from l3_node.primitives.native_tool_json import coerce_file_path_from_tool_input

    coerced = coerce_file_path_from_tool_input(raw_in)
    if coerced:
        raw_in = coerced
    elif raw_in.startswith("{"):
        return (
            "[执行失败: file_path 解析失败] Action Input 须为 JSON {\"file_path\":\"绝对或 workspace 相对路径\"} "
            "或裸路径字符串；勿把整段未闭合 JSON 当作路径。"
        )
    raw = _dedupe_pmo_lark_pull_segments(raw_in).replace("\\", "/")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        # 先尝试 HR 白名单路径（供 Agent 读取简历/JD）
        for base in _hr_allowed():
            cand = (base / p.name).resolve()
            if cand.exists() and _is_under_hr_whitelist(cand):
                _assert_read_allowed(cand)
                return _read_text_file(cand)
        # 仓库相对路径（docs/、skills_repo/ 等）：优先于 ~/.jachin/workspace
        cand = (_PROJ_ROOT / raw.lstrip("/")).resolve()
        if cand.is_file():
            _assert_read_allowed(cand)
            return _read_text_file(cand)
        from l3_node.workspace_context import get_effective_workspace_root

        p = (get_effective_workspace_root() / raw).resolve()
    else:
        p = p.resolve()
        deduped = Path(_dedupe_pmo_lark_pull_segments(str(p)))
        if deduped.is_file():
            p = deduped.resolve()
    if _is_under_hr_whitelist(p):
        _assert_read_allowed(p)
        if not p.exists():
            alt = try_resolve_workspace_file_if_missing(p)
            if alt is not None and alt.exists():
                p = alt
        if not p.exists():
            return _fs_read_file_not_found_hint(p)
        return _read_text_file(p)
    _assert_read_allowed(p)
    if not p.exists():
        alt = try_resolve_workspace_file_if_missing(p)
        if alt is None:
            alt = try_resolve_pmo_lark_md_if_missing(p)
        if alt is None and not raw.startswith("/") and "://" not in raw:
            cand = (_PROJ_ROOT / raw.lstrip("/")).resolve()
            if cand.is_file():
                alt = cand
        if alt is not None and alt.exists():
            p = alt
    if not p.exists():
        return _fs_read_file_not_found_hint(p)
    return _read_text_file(p)


def core_fs_write(file_path: str, content: str) -> None:
    """
    写入文件。路径须在 Native 白名单内：workspace（相对路径相对有效 workspace）、
    client_volumes / HR 目录，或用户 Desktop、Downloads、Documents。

    Args:
        file_path: 相对或绝对路径
        content: 写入内容

    Raises:
        SecurityException: 路径越界
    """
    from l3_node.primitives.native_tool_json import coerce_file_path_from_tool_input, parse_fs_write_tool_input
    from l3_node.workspace_context import get_effective_workspace_root

    fp_in = (file_path or "").strip()
    ct_in = content if content is not None else ""
    if fp_in.startswith("{") and not str(ct_in or "").strip():
        parsed = parse_fs_write_tool_input(fp_in)
        fp_in = parsed.get("file_path") or fp_in
        if parsed.get("content"):
            ct_in = parsed["content"]
    else:
        fp_in = coerce_file_path_from_tool_input(fp_in) or fp_in

    p = Path(fp_in).expanduser()
    if not p.is_absolute():
        p = (get_effective_workspace_root() / fp_in).resolve()
    else:
        p = p.resolve()
    _assert_under_workspace(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(ct_in), encoding="utf-8")


def _shell_cwd_for_profile(sandbox_profile: str | None) -> Path:
    """
    阶段 D：sandbox_profile=isolated|sandbox 时，在 workspace/sandboxes/<id>/ 下执行。
    """
    _WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    prof = (sandbox_profile or "").strip().lower()
    if prof in ("isolated", "sandbox", "jachin_sandbox"):
        sub = _WORKSPACE_ROOT / "sandboxes" / uuid.uuid4().hex[:12]
        sub.mkdir(parents=True, exist_ok=True)
        return sub
    return _WORKSPACE_ROOT


def core_shell_exec(
    command: str,
    timeout: int = 30,
    *,
    background: bool = False,
    sandbox_profile: str | None = None,
) -> Any:
    """
    执行 Shell 命令。默认 cwd=~/.jachin/workspace/。
    sandbox_profile=isolated|sandbox 时 cwd 为临时沙箱子目录（阶段 D）。
    background=True 时启动后台任务并返回注册信息 dict（P1+）。
    """
    from l3_node.intelligence_p1 import assert_shell_exec_allowed

    cmd = (command or "").strip()
    assert_shell_exec_allowed(cmd)
    cwd = _shell_cwd_for_profile(sandbox_profile)
    if background:
        from l3_node.shell_jobs import start_background_shell

        return start_background_shell(cmd, cwd, int(timeout) if timeout else 30)
    result = subprocess.run(
        cmd,
        shell=True,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode, result.stdout or "", result.stderr or ""


def core_apply_patch(
    patch_text: str,
    *,
    session_hint: str = "",
    backup: bool = True,
    python_ast_validate: bool | None = None,
) -> dict[str, Any]:
    """阶段 C：将 unified diff 应用到 workspace（默认备份，可回滚）。"""
    from core.apply_patch_unified import apply_unified_diff_to_workspace

    return apply_unified_diff_to_workspace(
        patch_text or "",
        session_hint=session_hint or "",
        backup=backup,
        python_ast_validate=python_ast_validate,
    )


def core_apply_patch_rollback(backup_id: str | None = None) -> dict[str, Any]:
    """回滚最近一次或指定 backup_id 的 apply_patch。"""
    from core.apply_patch_unified import rollback_patch_backup

    return rollback_patch_backup(backup_id)


def core_shell_hitl_approve(
    *,
    hash_hex: str | None = None,
    command: str | None = None,
    pending_id: str | None = None,
) -> dict[str, Any]:
    """阶段 D：批准 Shell HITL（哈希 / 原命令 / pending_id）。"""
    from l3_node.shell_hitl import approve_shell_hitl

    return approve_shell_hitl(hash_hex=hash_hex, command=command, pending_id=pending_id)


def core_domain_workflow_run(
    domain_id: str,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """L2/L3：按 domain_id 执行已注册领域子图（如 hr_recruitment）。"""
    did = (domain_id or "").strip()
    if not did:
        return {"ok": False, "error": "缺少 domain_id", "layer": 2}
    from l3_node.orchestration.glue import dispatch_domain_workflow

    p = params if isinstance(params, dict) else {}
    return dispatch_domain_workflow(did, p or None)


def core_workflow_run(
    yaml_rel_path: str,
    *,
    allowed_skills: list[str] | None = None,
    persistent: bool = False,
    run_id: str = "default",
    resume: bool = False,
    reset: bool = False,
    keep_completed_state: bool = False,
) -> dict[str, Any]:
    """阶段 C：执行 workspace 下 YAML 工作流（可选持久化 / 续跑）。"""
    from l3_node.workflow_spec_runner import run_workflow_yaml

    return run_workflow_yaml(
        yaml_rel_path,
        allowed_skills=allowed_skills,
        persistent=persistent,
        run_id=run_id,
        resume=resume,
        reset=reset,
        keep_completed_state=keep_completed_state,
    )


def dispatch_native_tool(tool_id: str, **kwargs: Any) -> Any:
    """
    根据 core:xxx 标识分发到对应 Native 函数。

    Args:
        tool_id: core:fs_read | core:fs_write | core:shell_exec | core:shell_job_status | core:shell_job_cancel
        **kwargs: 工具参数（shell_exec 可含 background；见 intelligence_p1 / shell_jobs）

    Returns:
        工具执行结果
    """
    _tid = (tool_id or "").strip()
    if _tid.startswith("util:") or _tid.startswith("sys:"):
        from l3_node.primitives.tools.core_util_tools import dispatch_util_native_tool

        return dispatch_util_native_tool(_tid, **kwargs)

    if tool_id == "core:fs_read":
        return core_fs_read(kwargs.get("file_path", ""))
    if tool_id == "core:fs_write":
        core_fs_write(kwargs.get("file_path", ""), kwargs.get("content", ""))
        return {"ok": True}
    if tool_id == "core:shell_exec":
        bg = bool(kwargs.get("background", False))
        out = core_shell_exec(
            kwargs.get("command", ""),
            timeout=kwargs.get("timeout", 30),
            background=bg,
            sandbox_profile=kwargs.get("sandbox_profile"),
        )
        if bg and isinstance(out, dict):
            return out
        code, stdout, stderr = out  # type: ignore[misc]
        return {"returncode": code, "stdout": stdout, "stderr": stderr}
    if tool_id == "core:shell_job_status":
        from l3_node.shell_jobs import format_job_status_report

        return format_job_status_report(str(kwargs.get("job_id", "") or ""))
    if tool_id == "core:shell_job_cancel":
        from l3_node.shell_jobs import cancel_shell_job

        return cancel_shell_job(str(kwargs.get("job_id", "") or ""))
    if tool_id == "core:apply_patch":
        return core_apply_patch(
            str(kwargs.get("patch_text", "") or kwargs.get("unified_diff", "") or ""),
            session_hint=str(kwargs.get("session_hint", "") or ""),
            backup=kwargs.get("backup", True) is not False,
            python_ast_validate=kwargs.get("python_ast_validate"),
        )
    if tool_id == "core:local_memory_search":
        from l3_node.local_memory_search import search_local_memories

        raw = kwargs.get("query")
        if raw is None and isinstance(kwargs.get("input"), dict):
            raw = kwargs["input"].get("query")
        q = str(raw or "").strip()
        top_k = kwargs.get("top_k", 8)
        try:
            top_k = int(top_k)
        except (TypeError, ValueError):
            top_k = 8
        mmr_l = kwargs.get("mmr_lambda", 0.55)
        try:
            mmr_l = float(mmr_l)
        except (TypeError, ValueError):
            mmr_l = 0.55
        half = kwargs.get("half_life_days", 30.0)
        try:
            half = float(half)
        except (TypeError, ValueError):
            half = 30.0
        inc_md = kwargs.get("include_memory_md", True)
        if isinstance(inc_md, str):
            inc_md = inc_md.lower() in ("1", "true", "yes")
        out = search_local_memories(
            q,
            top_k=max(1, min(32, top_k)),
            mmr_lambda=mmr_l,
            half_life_days=half,
            include_memory_md=bool(inc_md),
        )
        try:
            if isinstance(out, dict) and out.get("ok") and out.get("hits"):
                from l3_node.local_memory import touch_entries_from_search_hits

                touch_entries_from_search_hits(list(out["hits"]))
        except Exception:
            pass
        return out
    if tool_id == "core:local_memory_append":
        from l3_node.tools.core_local_memory_append import run_local_memory_append

        body = str(kwargs.get("content") or kwargs.get("body") or kwargs.get("text") or "").strip()
        if not body and isinstance(kwargs.get("input"), dict):
            body = str(kwargs["input"].get("content") or kwargs["input"].get("body") or "").strip()
        tags = kwargs.get("tags")
        if isinstance(tags, str):
            tags = [x.strip() for x in tags.split(",") if x.strip()]
        elif not isinstance(tags, list):
            tags = None
        return run_local_memory_append(content=body, tags=tags)
    if tool_id == "core:apply_patch_rollback":
        bid = kwargs.get("backup_id")
        s = str(bid).strip() if bid is not None and str(bid).strip() else ""
        return core_apply_patch_rollback(s if s else None)
    if tool_id == "core:shell_hitl_approve":
        return core_shell_hitl_approve(
            hash_hex=kwargs.get("hash_hex"),
            command=kwargs.get("command"),
            pending_id=kwargs.get("pending_id"),
        )
    if tool_id == "core:domain_workflow_run":
        raw = kwargs.get("params")
        if raw is None and isinstance(kwargs.get("input"), dict):
            raw = kwargs.get("input")
        if isinstance(raw, str) and raw.strip().startswith("{"):
            import json as _json

            try:
                raw = _json.loads(raw)
            except Exception:
                raw = {}
        if not isinstance(raw, dict):
            raw = {}
        raw = dict(raw)
        did = str(kwargs.get("domain_id") or kwargs.get("domain") or "").strip()
        if not did:
            did = str(raw.pop("domain_id", "") or raw.pop("domain", "") or "").strip()
        return core_domain_workflow_run(did, params=raw)
    if tool_id == "core:workflow_run":
        return core_workflow_run(
            str(kwargs.get("yaml_path", "") or kwargs.get("workflow_yaml", "") or ""),
            allowed_skills=kwargs.get("allowed_skills"),
            persistent=bool(kwargs.get("persistent", False)),
            run_id=str(kwargs.get("run_id") or "default"),
            resume=bool(kwargs.get("resume", False)),
            reset=bool(kwargs.get("reset", False)),
            keep_completed_state=bool(kwargs.get("keep_completed_state", False)),
        )
    if tool_id == "core:safety_lock_append":
        from l3_node.jachin_safety_lock import append_verified_fact

        body = str(kwargs.get("body") or kwargs.get("content") or "")
        src = str(kwargs.get("source") or "core:safety_lock_append")
        tags = kwargs.get("tags")
        if isinstance(tags, str):
            tags = [x.strip() for x in tags.split(",") if x.strip()]
        elif not isinstance(tags, list):
            tags = None
        tok = kwargs.get("token") if kwargs.get("token") is not None else kwargs.get("secret_token")
        tok_s = str(tok).strip() if tok is not None and str(tok).strip() else None
        cat = kwargs.get("category")
        cat_s = str(cat).strip() if cat is not None and str(cat).strip() else None
        return append_verified_fact(body, source=src, tags=tags, token=tok_s, category=cat_s)
    if tool_id == "core:safety_lock_list_pending":
        from l3_node.jachin_safety_lock import list_pending_entries

        return list_pending_entries()
    if tool_id == "core:safety_lock_remove":
        from l3_node.jachin_safety_lock import remove_entry_by_id

        eid = str(kwargs.get("entry_id") or kwargs.get("id") or "").strip()
        return remove_entry_by_id(eid)
    if tool_id == "core:compose_essay":
        return core_compose_essay(
            topic=str(kwargs.get("topic") or "").strip(),
            style_id=str(kwargs.get("style_id") or kwargs.get("styleId") or "").strip(),
            style_label=str(kwargs.get("style_label") or kwargs.get("styleLabel") or "").strip(),
            word_count_target=kwargs.get("word_count_target", kwargs.get("wordCountTarget", 600)),
            audience=str(kwargs.get("audience") or "通用").strip(),
            tone=str(kwargs.get("tone") or "正式").strip(),
            structure=str(kwargs.get("structure") or "总-分-总").strip(),
        )
    if tool_id in ("core:yfinance_global_market_hist", "core:yfinance_ticker_info"):
        from l3_node.skills.native_tools.yfinance_tools import dispatch_yfinance_core

        return dispatch_yfinance_core(tool_id, **kwargs)
    if tool_id in ("core:akshare_a_share_hist", "core:akshare_company_info"):
        from l3_node.skills.native_tools.akshare_tools import dispatch_akshare_core

        return dispatch_akshare_core(tool_id, **kwargs)
    if tool_id in ("core:db_query", "core:db_write", "core:pmo_import_json", "core:pmo_init_gap_report"):
        from l3_node.tools.pmo_db_tools import dispatch_pmo_db_tool

        return dispatch_pmo_db_tool(tool_id, **kwargs)
    raise ValueError(f"Unknown Native Core tool: {tool_id}")


def core_compose_essay(
    topic: str = "",
    style_id: str = "",
    style_label: str = "",
    word_count_target: Any = 600,
    audience: str = "通用",
    tone: str = "正式",
    structure: str = "总-分-总",
) -> str:
    """
    根据用户在生成式 UI 中确认的规格，生成作文 Markdown 骨架（不二次调用 LLM，便于与客户端 Opt-in 面板联调）。
    """
    try:
        wc = int(word_count_target)
    except (TypeError, ValueError):
        wc = 600
    wc = max(200, min(5000, wc))
    t = topic or "（待补主题）"
    sl = style_label or style_id or "记叙文"
    lines = [
        f"# 作文草稿：{t}",
        "",
        "## 写作规格",
        "",
        f"| 项目 | 选择 |",
        f"|------|------|",
        f"| 文体 | {sl} |",
        f"| 目标字数（约） | {wc} |",
        f"| 读者 | {audience} |",
        f"| 语气 | {tone} |",
        f"| 结构 | {structure} |",
        "",
        "## 正文骨架",
        "",
        f"（以下为按 **{structure}** 铺排的占位段落，你可据此扩写成约 {wc} 字。）",
        "",
        "### 开篇",
        f"- 以 **{tone}** 语气切入，点题「{t}」，呼应 **{audience}** 读者的阅读预期。",
        "",
        "### 主体",
        "- 展开 2～3 个层次或事例，注意与文体 **"
        + sl
        + "** 相符（记叙重画面与情感，议论重论点与论据，说明重条理与定义）。",
        "",
        "### 收束",
        "- 回扣主题，可升华或呼吁，避免空喊口号。",
        "",
        "---",
        "",
        "*本稿由 `core:compose_essay` 根据客户端面板参数生成；若需润色，请继续在对话中说明修改方向。*",
    ]
    return "\n".join(lines)
