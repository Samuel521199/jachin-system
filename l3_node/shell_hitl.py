"""
阶段 D：Shell 人工批准（HITL）— 命中策略的命令先入队再拒绝，批准哈希后重试。

配置：~/.jachin/nexus_config.json → intelligence_d
- shell_hitl_enabled: bool
- shell_hitl_patterns: 正则字符串列表（任一匹配即需批准）

批准文件：~/.jachin/workspace/shell_hitl_approved.json
{"hashes": ["sha256..."]}

待批准队列：~/.jachin/workspace/pending_shell_approvals.json（列表，供 Console/Lark 消费）
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

from core.intelligence_workspace import command_sha256, emit_intelligence_event, get_jachin_home
from l3_node.jachin_config import get_jachin_root

logger = logging.getLogger(__name__)


def _nexus() -> Path:
    return get_jachin_root() / "nexus_config.json"


def load_intelligence_d_config() -> dict[str, Any]:
    try:
        if not _nexus().exists():
            return {}
        cfg = json.loads(_nexus().read_text(encoding="utf-8"))
        sec = cfg.get("intelligence_d")
        return sec if isinstance(sec, dict) else {}
    except Exception as e:
        logger.debug("[IntelD] 读取 intelligence_d 失败: %s", e)
        return {}


def _approved_path() -> Path:
    p = get_jachin_home() / "workspace" / "shell_hitl_approved.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _pending_path() -> Path:
    p = get_jachin_home() / "workspace" / "pending_shell_approvals.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load_approved_hashes() -> set[str]:
    p = _approved_path()
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        h = data.get("hashes") if isinstance(data, dict) else None
        if isinstance(h, list):
            return {str(x).lower() for x in h if isinstance(x, str)}
    except Exception as e:
        logger.warning("[IntelD] 读取 shell_hitl_approved 失败: %s", e)
    return set()


def _append_pending(command: str, cmd_hash: str) -> str:
    pid = uuid.uuid4().hex[:12]
    item = {
        "id": pid,
        "command": (command or "")[:4000],
        "hash": cmd_hash,
        "created_ts": int(time.time()),
        "status": "pending",
    }
    path = _pending_path()
    cur: list[Any] = []
    if path.exists():
        try:
            cur = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(cur, list):
                cur = []
        except Exception:
            cur = []
    cur.append(item)
    cur = cur[-200:]
    path.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    emit_intelligence_event("shell_hitl_pending", {"id": pid, "hash": cmd_hash})
    return pid


def assert_shell_hitl_approved(command: str) -> None:
    """
    若启用且命中模式且哈希未在批准列表中：入队并 raise ValueError。
    """
    cfg = load_intelligence_d_config()
    if not cfg.get("shell_hitl_enabled"):
        return
    patterns = cfg.get("shell_hitl_patterns")
    if not isinstance(patterns, list) or not patterns:
        return
    cmd = (command or "").strip()
    matched = False
    for pat in patterns:
        if not isinstance(pat, str) or not pat.strip():
            continue
        try:
            if re.search(pat, cmd, re.IGNORECASE | re.DOTALL):
                matched = True
                break
        except re.error as e:
            logger.warning("[IntelD] 无效 shell_hitl_patterns: %s (%s)", pat, e)
    if not matched:
        return
    h = command_sha256(cmd)
    if h.lower() in _load_approved_hashes():
        return
    pending_id = _append_pending(cmd, h)
    raise ValueError(
        "【Shell HITL】该命令需人工批准。pending_id=%s\n"
        "批准方式：① 将哈希写入 shell_hitl_approved.json 的 hashes；② 或调用工具 core:shell_hitl_approve。\n"
        "哈希：%s"
        % (pending_id, h)
    )


def approve_shell_hitl(
    *,
    hash_hex: str | None = None,
    command: str | None = None,
    pending_id: str | None = None,
) -> dict[str, Any]:
    """
    将命令 SHA256 写入批准列表；可选按 pending_id 将队列项标为 approved。
    """
    h = (hash_hex or "").strip().lower()
    if not h and (command or "").strip():
        h = command_sha256(command or "").lower()
    if not h and (pending_id or "").strip():
        pid = str(pending_id).strip()
        path = _pending_path()
        if path.exists():
            try:
                cur = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(cur, list):
                    for item in cur:
                        if isinstance(item, dict) and str(item.get("id", "")) == pid:
                            hx = str(item.get("hash", "") or "").strip().lower()
                            if hx:
                                h = hx
                            item["status"] = "approved"
                            item["approved_ts"] = int(time.time())
                            path.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
                            break
            except Exception as e:
                return {"ok": False, "error": str(e)}
    if not h or len(h) != 64:
        return {"ok": False, "error": "请提供 hash_hex、command 或有效 pending_id"}

    p = _approved_path()
    data: dict[str, Any] = {"hashes": []}
    if p.exists():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("hashes"), list):
                data = raw
        except Exception:
            pass
    hashes = {str(x).lower() for x in (data.get("hashes") or []) if isinstance(x, str)}
    hashes.add(h)
    data["hashes"] = sorted(hashes)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    emit_intelligence_event("shell_hitl_approved", {"hash": h})
    return {"ok": True, "hash": h}
