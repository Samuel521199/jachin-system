"""
Jachin Nexus v8.0 - Native Core 内置标准库

权限死锁在 ~/.jachin/workspace/ 下，供 MCP 瘫痪时的 Fallback 使用。
HR 透析镜白名单：允许读取项目 data/hr_resumes、config/hr_jds（解决工作目录与配置文件路径冲突）。
任何其他越界访问直接抛出 SecurityException。
"""
from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from typing import Any

# 工作目录根，绝对不可越界
_WORKSPACE_ROOT = Path.home() / ".jachin" / "workspace"

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
    """断言路径在 workspace 下，否则抛出 SecurityException"""
    abs_path = path.resolve()
    root = _WORKSPACE_ROOT.resolve()
    if not str(abs_path).startswith(str(root)):
        raise SecurityException(
            f"Wasm/Native Core sandbox violation: {path} escapes ~/.jachin/workspace/"
        )


def core_fs_read(file_path: str) -> str:
    """
    读取文件内容。路径必须位于 ~/.jachin/workspace/ 下，
    或 HR 透析镜白名单（data/hr_resumes、config/skills/.../hr_jds）下。
    PDF 文件自动提取纯文本，与 mcp_read_file 行为一致。

    Args:
        file_path: 相对或绝对路径

    Returns:
        文件内容

    Raises:
        SecurityException: 路径越界
    """
    raw = (file_path or "").strip().replace("\\", "/")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        # 先尝试 HR 白名单路径（供 Agent 读取简历/JD）
        for base in _hr_allowed():
            cand = (base / p.name).resolve()
            if cand.exists() and _is_under_hr_whitelist(cand):
                if cand.suffix.lower() == ".pdf":
                    from core.pdf_extractor import extract_pdf_text
                    return extract_pdf_text(cand) or ""
                return cand.read_text(encoding="utf-8", errors="replace")
        cand = (_PROJ_ROOT / raw.lstrip("/")).resolve()
        if cand.exists() and _is_under_hr_whitelist(cand):
            if cand.suffix.lower() == ".pdf":
                from core.pdf_extractor import extract_pdf_text
                return extract_pdf_text(cand) or ""
            return cand.read_text(encoding="utf-8", errors="replace")
        p = (_WORKSPACE_ROOT / raw).resolve()
    if _is_under_hr_whitelist(p):
        if p.suffix.lower() == ".pdf":
            from core.pdf_extractor import extract_pdf_text
            return extract_pdf_text(p) or ""
        return p.read_text(encoding="utf-8", errors="replace")
    _assert_under_workspace(p)
    if p.suffix.lower() == ".pdf":
        from core.pdf_extractor import extract_pdf_text
        return extract_pdf_text(p) or ""
    return p.read_text(encoding="utf-8", errors="replace")


def core_fs_write(file_path: str, content: str) -> None:
    """
    写入文件。路径必须位于 ~/.jachin/workspace/ 下。

    Args:
        file_path: 相对或绝对路径
        content: 写入内容

    Raises:
        SecurityException: 路径越界
    """
    p = Path(file_path).expanduser()
    if not p.is_absolute():
        p = (_WORKSPACE_ROOT / p).resolve()
    _assert_under_workspace(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


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
    raise ValueError(f"Unknown Native Core tool: {tool_id}")
