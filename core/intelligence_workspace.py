"""
智能化阶段 A / E — Workspace 锚点、Compaction 审计、轻量事件总线（JSONL）。

设计来源: docs/JACHIN_VS_OPENCLAW_INTELLIGENCE_ANALYSIS.md §9.8–9.9、INTELLIGENCE_UPGRADE_OVERVIEW §五。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CHECKPOINT_BEGIN = "<!-- MACHINE_CHECKPOINT"
_CHECKPOINT_END = "MACHINE_CHECKPOINT_END -->"


def get_jachin_home() -> Path:
    raw = (os.environ.get("JACHIN_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".jachin"


def _logs_dir() -> Path:
    p = get_jachin_home() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def append_compaction_audit(event: dict[str, Any]) -> None:
    """追加 compaction / 锚点审计一行 JSON（阶段 A 验收）。"""
    try:
        line = json.dumps({"ts": time.time(), **event}, ensure_ascii=False)
        path = _logs_dir() / "compaction_audit.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        logger.debug("[IntelWorkspace] compaction_audit 写入失败: %s", e)


def emit_intelligence_event(event_type: str, payload: dict[str, Any] | None = None) -> None:
    """
    隐式事件总线（阶段 E）：只追加 JSONL，默认无消费端，可后续接 reinforce。
    """
    try:
        rec = {
            "ts": time.time(),
            "type": str(event_type),
            "payload": payload or {},
        }
        path = _logs_dir() / "intelligence_events.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug("[IntelWorkspace] intelligence_events 写入失败: %s", e)


def load_workspace_anchor_paths(cfg_llm: dict[str, Any]) -> list[Path]:
    """
    从 nexus llm.memory_flush.workspace_must_update 读取锚点路径。
    每项为相对于 ~/.jachin/ 的 POSIX 路径，如 memory/MEMORY.md、workspace/task_plan.md。
    """
    mf = cfg_llm.get("memory_flush") if isinstance(cfg_llm.get("memory_flush"), dict) else {}
    raw = mf.get("workspace_must_update")
    if not isinstance(raw, list) or not raw:
        return []
    root = get_jachin_home()
    out: list[Path] = []
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            continue
        rel = item.strip().replace("\\", "/").lstrip("/")
        # 禁止跳出 jachin 根
        cand = (root / rel).resolve()
        try:
            cand.relative_to(root.resolve())
        except ValueError:
            logger.warning("[IntelWorkspace] 跳过非法锚点路径（越界）: %s", item)
            continue
        out.append(cand)
    return out


def snapshot_anchor_states(paths: list[Path]) -> dict[str, tuple[float, int]]:
    """path_str -> (mtime, size)"""
    snap: dict[str, tuple[float, int]] = {}
    for p in paths:
        try:
            if p.exists() and p.is_file():
                st = p.stat()
                snap[str(p)] = (st.st_mtime, st.st_size)
            else:
                snap[str(p)] = (0.0, -1)
        except OSError:
            snap[str(p)] = (0.0, -1)
    return snap


def anchors_stale(before: dict[str, tuple[float, int]], after: dict[str, tuple[float, int]]) -> list[str]:
    """返回刷新后仍未「变化」的锚点（mtime/size 均未变）。"""
    stale: list[str] = []
    for k, b in before.items():
        a = after.get(k)
        if a is None:
            continue
        if b == a:
            stale.append(k)
    return stale


def findings_has_machine_checkpoint() -> bool:
    """findings.md 是否含机器可读 checkpoint 块（阶段 A / B 审计）。"""
    p = get_jachin_home() / "workspace" / "findings.md"
    if not p.exists():
        return False
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return _CHECKPOINT_BEGIN in text and _CHECKPOINT_END in text


def append_findings_checkpoint_block(summary: str, *, source: str = "compaction") -> None:
    """在 findings.md 末尾追加 MACHINE_CHECKPOINT 块（幂等可多次追加）。"""
    ws = get_jachin_home() / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    p = ws / "findings.md"
    block = (
        f"\n\n{_CHECKPOINT_BEGIN}\n"
        f"ts: {time.time()}\nsource: {source}\n{summary.strip()[:4000]}\n{_CHECKPOINT_END}\n"
    )
    try:
        prev = p.read_text(encoding="utf-8") if p.exists() else ""
        p.write_text(prev + block, encoding="utf-8")
    except OSError as e:
        logger.warning("[IntelWorkspace] findings checkpoint 写入失败: %s", e)


def load_post_compaction_audit_config(cfg_llm: dict[str, Any]) -> tuple[bool, str]:
    sec = cfg_llm.get("post_compaction_audit")
    if not isinstance(sec, dict):
        return False, "log"
    enabled = bool(sec.get("enabled", False))
    remediation = str(sec.get("remediation", "log") or "log").lower()
    allowed = ("log", "clarification", "none", "memory_flush_retry")
    if remediation not in allowed:
        remediation = "log"
    return enabled, remediation


def load_anchor_remediate_mode(cfg_llm: dict[str, Any]) -> str:
    """memory_flush.anchor_remediate: none | touch_workspace_anchors | second_llm"""
    mf = cfg_llm.get("memory_flush") if isinstance(cfg_llm.get("memory_flush"), dict) else {}
    m = str(mf.get("anchor_remediate", "none") or "none").lower().strip()
    if m not in ("none", "touch_workspace_anchors", "second_llm"):
        return "none"
    return m


def touch_stale_workspace_anchors(stale_paths: list[str]) -> list[str]:
    """
    对仍位于 ~/.jachin/workspace/ 下的锚点追加机器注释行以更新 mtime（阶段 A 机械补救）。
    """
    ws = (get_jachin_home() / "workspace").resolve()
    touched: list[str] = []
    for sp in stale_paths:
        try:
            p = Path(sp).resolve()
            if not str(p).startswith(str(ws)):
                continue
            if not p.exists() or not p.is_file():
                continue
            with p.open("a", encoding="utf-8") as f:
                f.write(f"\n<!-- jachin_anchor_touch ts={time.time()} -->\n")
            touched.append(str(p))
        except OSError:
            continue
    return touched


def command_sha256(command: str) -> str:
    return hashlib.sha256((command or "").strip().encode("utf-8")).hexdigest()
