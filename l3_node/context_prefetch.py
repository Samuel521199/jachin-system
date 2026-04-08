"""工具后 workspace Markdown 摘录；路径滑窗 + ReAct 轮次账本（context_path_ledger）去重。见 docs/前台闲聊与后台重负荷任务的物理隔离与背压熔断.md、docs/L3_LIMITATIONS_AND_REMEDIATION_ROADMAP.md §〇。"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_ATTACH_MARKER = "【relevant_context_prefetch】"


def _jachin_workspace() -> Path:
    import os

    return Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin"))) / "workspace"


def _load_prefetch_cfg() -> dict[str, Any]:
    import json as _json
    import os

    root = Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin")))
    p = root / "nexus_config.json"
    dft = {
        "enabled": True,
        "max_workspace_md_files": 28,
        "max_snippet_chars_per_file": 2800,
        "max_total_chars": 9000,
        "max_session_bytes": 120_000,
        "path_sliding_window_size": 12,
        "ledger_iteration_window": 8,
        "skip_tools": (
            "core:local_memory_search",
            "core:local_memory_append",
            "recall_memory",
            "core:check_background_task",
            # SQLite / DB 只读：Observation 应保持短小（表清单或行集），勿再拼 workspace Markdown，
            # 否则易触发误判（长文含 ##/**）并污染用户可见流。
            "list_tables",
            "get_table_schema",
            "db_info",
            "read_records",
            "read_query",
            ":query",
            "write_query",
        ),
    }
    if not p.exists():
        return dft
    try:
        raw = _json.loads(p.read_text(encoding="utf-8"))
        cp = raw.get("context_prefetch")
        if isinstance(cp, dict):
            for k in (
                "enabled",
                "max_workspace_md_files",
                "max_snippet_chars_per_file",
                "max_total_chars",
                "max_session_bytes",
                "path_sliding_window_size",
                "ledger_iteration_window",
            ):
                if k in cp:
                    if k == "enabled":
                        dft[k] = bool(cp[k])
                    else:
                        try:
                            dft[k] = max(0, int(cp[k]))
                        except (TypeError, ValueError):
                            pass
            if isinstance(cp.get("skip_tools"), list):
                dft["skip_tools"] = tuple(str(x).strip().lower() for x in cp["skip_tools"] if str(x).strip())
    except Exception as e:
        logger.debug("[ContextPrefetch] 读配置失败: %s", e)
    return dft


def _extract_keywords(intent: str, *, limit: int = 6) -> list[str]:
    s = (intent or "").strip()
    if not s:
        return []
    out: list[str] = []
    for m in re.finditer(r"[\u4e00-\u9fff]{2,}", s):
        w = m.group(0)
        if w not in out:
            out.append(w)
        if len(out) >= limit:
            break
    if len(out) < limit:
        for m in re.finditer(r"[a-zA-Z][a-zA-Z0-9_\-]{2,}", s):
            w = m.group(0).lower()
            if w not in out:
                out.append(w)
            if len(out) >= limit:
                break
    return out[:limit]


def _norm_path_key(p: Path) -> str:
    try:
        return str(p.resolve()).lower()
    except OSError:
        return str(p).lower()


def _register_tool_paths_for_dedupe(
    tool_id: str,
    action_input: str,
    shown: set[str],
    meta: dict[str, Any],
    window_size: int,
    react_iteration: int = 0,
) -> None:
    tid = (tool_id or "").strip().lower()
    raw = (action_input or "").strip()
    paths: list[str] = []
    if tid == "core:fs_read":
        if raw.startswith("{"):
            try:
                o = json.loads(raw)
                if isinstance(o, dict) and o.get("file_path"):
                    paths.append(str(o["file_path"]))
            except json.JSONDecodeError:
                pass
        if not paths and raw:
            paths.append(raw.split("\n")[0].strip())
    elif tid in ("mcp:read_file", "read_file"):
        if raw.startswith("{"):
            try:
                o = json.loads(raw)
                if isinstance(o, dict):
                    for key in ("path", "file_path", "uri", "target_file"):
                        v = o.get(key)
                        if v:
                            paths.append(str(v))
                            break
            except json.JSONDecodeError:
                pass
        if not paths and raw:
            paths.append(raw.split("\n")[0].strip())
    for p in paths:
        try:
            fp = Path(p).expanduser()
            if not fp.is_absolute():
                fp = (_jachin_workspace() / p).resolve()
            nk = _norm_path_key(fp)
            _prefetch_touch_shown_path(shown, meta, nk, window_size)
            if react_iteration > 0:
                from l3_node.context_path_ledger import touch_tool_read_path_iteration

                touch_tool_read_path_iteration(meta, nk, react_iteration)
        except Exception:
            nk = p.strip().lower()
            _prefetch_touch_shown_path(shown, meta, nk, window_size)
            if react_iteration > 0:
                from l3_node.context_path_ledger import touch_tool_read_path_iteration

                touch_tool_read_path_iteration(meta, nk, react_iteration)


def _prefetch_touch_shown_path(shown: set[str], meta: dict[str, Any], nk: str, window_size: int) -> None:
    """滑出窗口的路径从 shown 移除，允许后续再次 prefetch。"""
    if window_size <= 0:
        shown.add(nk)
        return
    order: list[str] = meta.get("_prefetch_paths_order")
    if not isinstance(order, list):
        order = []
        meta["_prefetch_paths_order"] = order
    while len(order) >= window_size:
        old = order.pop(0)
        shown.discard(old)
    order.append(nk)
    shown.add(nk)


def build_prefetch_attachment(
    ctx: Any,
    tool_id: str,
    action_input: str,
    observation: str,
    *,
    assistant_response: str = "",
) -> str:
    """
    返回可拼在 Observation 后的 Markdown 块；无内容则返回空串。
    ctx 须为 PipelineContext，使用 metadata 中 _prefetch_paths_shown / _prefetch_session_bytes。
    """
    cfg = _load_prefetch_cfg()
    if not cfg.get("enabled", True):
        return ""
    ch = ""
    try:
        ch = str(ctx.metadata.get("_implicit_channel") or "")
    except Exception:
        pass
    if ch in ("background_task",):
        return ""

    tid = (tool_id or "").strip().lower()
    for sk in cfg.get("skip_tools") or ():
        if sk and sk in tid:
            return ""

    meta = ctx.metadata
    _raw_shown = meta.get("_prefetch_paths_shown")
    if isinstance(_raw_shown, set):
        shown = _raw_shown
    else:
        shown = set()
        meta["_prefetch_paths_shown"] = shown

    sess_bytes = int(meta.get("_prefetch_session_bytes") or 0)
    max_sess = int(cfg.get("max_session_bytes") or 120_000)
    if sess_bytes >= max_sess:
        return ""

    _win = int(cfg.get("path_sliding_window_size") or 12)
    _led_w = int(cfg.get("ledger_iteration_window") or 8)
    _react_it = int(meta.get("_react_iteration") or 0)
    _register_tool_paths_for_dedupe(tool_id, action_input, shown, meta, _win, react_iteration=_react_it)

    kws = _extract_keywords(getattr(ctx, "intent", "") or "")
    if not kws:
        return ""

    root = _jachin_workspace()
    if not root.is_dir():
        return ""

    max_files = int(cfg.get("max_workspace_md_files") or 28)
    cap_file = int(cfg.get("max_snippet_chars_per_file") or 2800)
    cap_total = int(cfg.get("max_total_chars") or 9000)

    md_files: list[Path] = []
    skip_dirs = {".background_tasks", ".git", "node_modules", "sandboxes", "__pycache__"}
    for p in sorted(root.rglob("*.md"), key=lambda x: x.stat().st_mtime_ns, reverse=True):
        if len(md_files) >= max_files:
            break
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] in skip_dirs:
            continue
        if any(x in rel.parts for x in skip_dirs):
            continue
        try:
            if p.stat().st_size > 400_000:
                continue
        except OSError:
            continue
        md_files.append(p)

    from l3_node.context_path_ledger import should_block_prefetch_path, touch_prefetch_path_iteration

    scored: list[tuple[int, Path]] = []
    for p in md_files:
        nk = _norm_path_key(p)
        if nk in shown:
            continue
        if _react_it > 0 and should_block_prefetch_path(meta, nk, _react_it, _led_w):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        head = "\n".join(text.splitlines()[:80])
        score = sum(head.count(k) for k in kws)
        if score > 0:
            scored.append((score, p))

    scored.sort(key=lambda x: -x[0])
    chunks: list[str] = []
    used = 0
    for _sc, p in scored[:5]:
        nk = _norm_path_key(p)
        if nk in shown:
            continue
        if _react_it > 0 and should_block_prefetch_path(meta, nk, _react_it, _led_w):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        snippet = text.strip()
        if len(snippet) > cap_file:
            snippet = snippet[:cap_file] + "\n…(truncated)"
        rel = p.relative_to(root)
        block = f"### `{rel}`\n{snippet}"
        if used + len(block) > cap_total:
            break
        chunks.append(block)
        used += len(block)
        _prefetch_touch_shown_path(shown, meta, nk, _win)
        if _react_it > 0:
            touch_prefetch_path_iteration(meta, nk, _react_it)

    if not chunks:
        return ""

    body = "\n\n---\n\n".join(chunks)
    attach = f"{_ATTACH_MARKER}\n以下工作区 Markdown 与用户意图关键词可能相关（已去重路径，勿重复整文件读取）：\n\n{body}"
    meta["_prefetch_session_bytes"] = sess_bytes + len(attach.encode("utf-8", errors="replace"))
    logger.debug("[ContextPrefetch] 附加 %d 块 bytes_total=%s", len(chunks), meta["_prefetch_session_bytes"])
    try:
        _pc = meta.get("_prompt_cycle")
        if _pc is not None and body:
            from l3_node.local_memory import bump_memory_inject_cycle_for_content_hit

            bump_memory_inject_cycle_for_content_hit(body[:800], prompt_cycle=int(_pc))
    except Exception:
        pass
    return attach
