"""
Omni-Context Sniffer：入站轻量环境报告（Git + 安全锁摘要 + 本地记忆 Top 命中），硬字符预算，写入 bundle.extra["environment_report"]。
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# 整份报告正文（git 块 + 安全锁 + 记忆摘录）总预算
_MAX_TOTAL_CHARS = 1500
# Git status + diff --stat 合并上限
_MAX_GIT_CHARS = 500


def _truncate(s: str, max_len: int) -> tuple[str, bool]:
    t = (s or "").strip()
    if len(t) <= max_len:
        return t, False
    return t[: max(0, max_len - 8)].rstrip() + "\n…(截断)", True


def _emit_status(
    on_step: Optional[Callable[[str, str, str], None]],
    run_id: str,
    status: str,
) -> None:
    if not on_step:
        return
    try:
        on_step(
            "system_status",
            json.dumps({"status": status}, ensure_ascii=False),
            run_id or "",
        )
    except Exception as e:
        logger.debug("[ContextSniffer] on_step 失败: %s", e)


def _git_workspace_snippet(workspace_dir: str, max_chars: int) -> dict[str, Any]:
    cwd = Path(workspace_dir)
    out: dict[str, Any] = {"ok": False, "combined": ""}
    if not cwd.is_dir():
        return out
    try:
        st = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=2,
            encoding="utf-8",
            errors="replace",
        )
        ds = subprocess.run(
            ["git", "diff", "--stat"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=2,
            encoding="utf-8",
            errors="replace",
        )
        a = (st.stdout or "").strip()
        b = (ds.stdout or "").strip()
        combined = f"{a}\n---\n{b}".strip() if b else a
        combined, _ = _truncate(combined, max_chars)
        out["ok"] = True
        out["combined"] = combined
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        out["error"] = str(e)[:120]
    except Exception as e:
        out["error"] = str(e)[:120]
        logger.debug("[ContextSniffer] git 采集失败: %s", e)
    return out


def _memory_excerpt(query: str, *, max_chars: int, top_k: int = 2) -> tuple[str, list[dict[str, Any]]]:
    try:
        from l3_node.local_memory_search import search_local_memories

        res = search_local_memories(query or "", top_k=top_k, candidate_pool=24)
        hits = res.get("hits") if isinstance(res, dict) else []
        if not isinstance(hits, list):
            return "", []
        lines: list[str] = []
        used = 0
        slim_hits: list[dict[str, Any]] = []
        for h in hits[:top_k]:
            if not isinstance(h, dict):
                continue
            tag = str(h.get("tag") or h.get("id") or "")
            body = str(h.get("content") or "").strip().replace("\r\n", "\n")
            chunk, _ = _truncate(body, min(400, max(80, max_chars - used - 40)))
            line = f"- [{tag}] {chunk}"
            if used + len(line) > max_chars:
                break
            lines.append(line)
            used += len(line) + 1
            slim_hits.append({"id": h.get("id"), "tag": tag, "score": h.get("score")})
        return "\n".join(lines), slim_hits
    except Exception as e:
        logger.debug("[ContextSniffer] memory 检索失败: %s", e)
        return "", []


def _apply_total_budget(
    git_combined: str,
    safety: str,
    memory_excerpt: str,
    *,
    max_total: int,
    max_git: int,
) -> dict[str, Any]:
    git_part, git_t = _truncate(git_combined, max_git)
    remain = max_total - len(git_part)
    truncated = git_t
    if remain < 60:
        return {
            "git_combined": git_part,
            "safety_lock_snippet": "",
            "memory_excerpt": "",
            "truncated": True,
            "total_chars": len(git_part),
        }
    s_cap = min(max(remain // 2, 200), remain - 40)
    safety_part, st = _truncate(safety, s_cap)
    truncated = truncated or st
    mem_cap = max(0, remain - len(safety_part))
    mem_part, mt = _truncate(memory_excerpt, mem_cap)
    truncated = truncated or mt
    total_len = len(git_part) + len(safety_part) + len(mem_part)
    return {
        "git_combined": git_part,
        "safety_lock_snippet": safety_part,
        "memory_excerpt": mem_part,
        "truncated": truncated,
        "total_chars": total_len,
    }


def format_environment_report_for_prompt(report: Any) -> str:
    """将 environment_report 字典格式化为可注入 system 的短块（空则返回空串）。"""
    if not isinstance(report, dict):
        return ""
    if report.get("ok") is False and not report.get("git") and not report.get("safety_lock_snippet"):
        return ""
    parts: list[str] = []
    git = report.get("git") or {}
    if isinstance(git, dict):
        g = str(git.get("combined") or "").strip()
        if g:
            parts.append("【Git 工作区】\n" + g)
    sl = str(report.get("safety_lock_snippet") or "").strip()
    if sl:
        parts.append("【安全锁（嗅探摘要）】\n" + sl)
    mem = str(report.get("memory_excerpt") or "").strip()
    if mem:
        parts.append("【本地经验（检索摘要）】\n" + mem)
    if not parts:
        return ""
    body = "\n\n".join(parts)
    return f"\n[ENVIRONMENT_REPORT]\n{body}\n[/ENVIRONMENT_REPORT]\n"


async def build_environment_report(
    user_input: str,
    workspace_dir: str,
    *,
    on_step: Optional[Callable[[str, str, str], None]] = None,
    run_id: str = "",
    max_total_chars: int = _MAX_TOTAL_CHARS,
    max_git_chars: int = _MAX_GIT_CHARS,
) -> dict[str, Any]:
    """
    异步构建环境报告（Git 在线程中跑子进程，避免阻塞 loop）。
    总字符预算默认 1500；Git 段默认最多 500。
    """
    _emit_status(on_step, run_id, "⏳ 正在嗅探环境（Git / 安全锁 / 本地记忆）…")
    ui = (user_input or "").strip()

    git_task = asyncio.to_thread(_git_workspace_snippet, workspace_dir, max_git_chars)

    def _safety() -> str:
        try:
            from l3_node.jachin_safety_lock import get_safety_lock_snippet

            return (get_safety_lock_snippet(user_text=ui) or "").strip()
        except Exception as e:
            logger.debug("[ContextSniffer] safety_lock 失败: %s", e)
            return ""

    _emit_status(on_step, run_id, "⏳ 正在加载 JACHIN_SAFETY_LOCK 相关摘要…")
    safety_task = asyncio.to_thread(_safety)

    _mem_cap = min(700, max(120, int(max_total_chars) * 2 // 5))

    def _mem() -> tuple[str, list[dict[str, Any]]]:
        return _memory_excerpt(ui, max_chars=_mem_cap, top_k=2)

    mem_task = asyncio.to_thread(_mem)

    git_info, safety_raw, (mem_text, mem_hits) = await asyncio.gather(
        git_task,
        safety_task,
        mem_task,
    )

    merged = _apply_total_budget(
        str(git_info.get("combined") or ""),
        safety_raw,
        mem_text,
        max_total=max_total_chars,
        max_git=max_git_chars,
    )

    report: dict[str, Any] = {
        "ok": True,
        "git": {
            "ok": bool(git_info.get("ok")),
            "combined": merged["git_combined"],
            "error": git_info.get("error"),
        },
        "safety_lock_snippet": merged["safety_lock_snippet"],
        "memory_excerpt": merged["memory_excerpt"],
        "memory_hits_meta": mem_hits,
        "meta": {
            "truncated": merged["truncated"],
            "total_chars": merged["total_chars"],
            "max_total_chars": max_total_chars,
            "workspace_dir": workspace_dir[:200],
        },
    }
    _emit_status(on_step, run_id, "✓ 环境嗅探完成，已写入网关上下文（受字符预算约束）。")
    return report
