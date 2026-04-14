"""
Omni-Context Sniffer：入站轻量环境报告（Git + 安全锁摘要 + 本地记忆 Top 命中 + db_semantics.md / golden_sql），
硬字符预算，写入 bundle.extra["environment_report"]；并解析 db_semantics.yaml → report["semantic_layer"]（见 workspace_db_context）。

与混合架构关系：docs/architecture/JACHIN_HYBRID_AGENT_ARCHITECTURE.md §4。
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

        # 多取候选再过滤：排除 compaction 的 task_checkpoint（含过时目录快照，勿当「环境事实」）
        res = search_local_memories(
            query or "", top_k=max(top_k * 12, 16), candidate_pool=64
        )
        hits = res.get("hits") if isinstance(res, dict) else []
        if not isinstance(hits, list):
            return "", []
        hits = [
            h
            for h in hits
            if isinstance(h, dict) and str(h.get("tag") or "").strip().lower() != "task_checkpoint"
        ][:top_k]
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
        parts.append(
            "【本地经验（检索摘要）】\n"
            + mem
            + "\n（已排除 compaction 会话摘要，避免过时目录快照当事实。）"
        )
    _sem_raw = report.get("semantic_layer")
    if isinstance(_sem_raw, dict) and _sem_raw:
        try:
            from l3_node.intent_gateway.workspace_db_context import format_semantic_layer_excerpt_for_environment_report

            _sem_ex = format_semantic_layer_excerpt_for_environment_report(_sem_raw).strip()
            if _sem_ex:
                parts.append("【业务语义层 · db_semantics.yaml（结构化）】\n" + _sem_ex)
        except Exception:
            pass
    dbs = str(report.get("db_semantics_snippet") or "").strip()
    if dbs:
        parts.append("【业务语义层 · db_semantics.md】\n" + dbs)
    gfs = str(report.get("golden_sql_fewshot") or "").strip()
    if gfs:
        parts.append("【Golden SQL 少样本（工作区 golden_sql_examples.jsonl）】\n" + gfs)
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

    try:
        from l3_node.intent_gateway.config import get_intent_gateway_config

        _ig_sn = get_intent_gateway_config()
        _db_ctx_on = bool(_ig_sn.get("context_sniffer_workspace_db_context_enabled", True))
        try:
            _sem_mc = int(_ig_sn.get("context_sniffer_db_semantics_max_chars", 480))
        except (TypeError, ValueError):
            _sem_mc = 480
        try:
            _gold_mc = int(_ig_sn.get("context_sniffer_golden_sql_max_chars", 520))
        except (TypeError, ValueError):
            _gold_mc = 520
        try:
            _gold_ne = int(_ig_sn.get("context_sniffer_golden_sql_max_examples", 3))
        except (TypeError, ValueError):
            _gold_ne = 3
    except Exception:
        _db_ctx_on, _sem_mc, _gold_mc, _gold_ne = True, 480, 520, 3

    _reserve = 0
    if _db_ctx_on:
        _reserve = max(0, _sem_mc) + max(0, _gold_mc) + 32
    _classic_max = max(200, int(max_total_chars) - _reserve)

    merged = _apply_total_budget(
        str(git_info.get("combined") or ""),
        safety_raw,
        mem_text,
        max_total=_classic_max,
        max_git=max_git_chars,
    )

    sem_snip = ""
    gold_snip = ""
    if _db_ctx_on:
        try:
            from l3_node.intent_gateway.workspace_db_context import build_workspace_db_context_bundle

            _bundle = build_workspace_db_context_bundle(
                workspace_dir,
                ui,
                semantics_max_chars=max(0, _sem_mc),
                golden_max_chars=max(0, _gold_mc),
                golden_max_examples=max(1, _gold_ne),
            )
            sem_snip = _bundle.get("db_semantics_snippet") or ""
            gold_snip = _bundle.get("golden_sql_fewshot") or ""
        except Exception as e:
            logger.debug("[ContextSniffer] workspace_db_context 跳过: %s", e)

    _total_all = int(merged["total_chars"]) + len(sem_snip) + len(gold_snip)

    try:
        from l3_node.intent_gateway.workspace_db_context import load_db_semantics_yaml

        _semantic_layer = load_db_semantics_yaml(workspace_dir)
    except Exception as e:
        logger.debug("[ContextSniffer] semantic_layer YAML 跳过: %s", e)
        try:
            from l3_node.intent_gateway.workspace_db_context import default_semantic_layer

            _semantic_layer = default_semantic_layer()
        except Exception:
            _semantic_layer = {}

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
        "db_semantics_snippet": sem_snip,
        "golden_sql_fewshot": gold_snip,
        "semantic_layer": _semantic_layer,
        "meta": {
            "truncated": merged["truncated"],
            "total_chars": _total_all,
            "max_total_chars": max_total_chars,
            "workspace_dir": workspace_dir[:200],
            "workspace_db_context_enabled": _db_ctx_on,
        },
    }
    _emit_status(on_step, run_id, "✓ 环境嗅探完成，已写入网关上下文（受字符预算约束）。")
    return report
