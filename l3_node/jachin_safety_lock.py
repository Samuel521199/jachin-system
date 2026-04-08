"""
Jachin 安全锁：与 MEMORY.md / 本地向量记忆分离的「已验证事实」。

设计要点（见 docs/JACHIN_SAFETY_LOCK.md 与 JACHIN_SAFETY_LOCK_LEARNING.md）：
- **按需域注入**：默认不按全量 MD 灌入 context；仅当话术命中 db/shell  heuristic 时加载域文件。
- **待审批追加**：默认 learn 开启时，Agent 的 append 只写入 pending，**不**把管理员密钥暴露给模型；
  审批仅通过 CLI（环境变量中的管理员 token）完成。
- **撤销**：core:safety_lock_remove 按 entry_id 从全局 MD 删除条目块。
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from l3_node.tools.core_safety_lock_append import (
    decide_safety_lock_append_path,
    format_category_header_fragment,
    normalize_safety_lock_category,
    remove_lock_blocks_for_category,
    scan_approved_categories_in_text,
)

logger = logging.getLogger(__name__)

SAFETY_LOCK_FILENAME = "JACHIN_SAFETY_LOCK.md"
_MAX_ENTRY_CHARS = 16_000
# 磁盘上单文件软上限（与「注入 context 预算」分离）
_MAX_FILE_CHARS = 200_000
_DEFAULT_HEADER = """# Jachin 安全锁（已验证事实）

与 **MEMORY.md**、**core:local_memory_search** 所见的会话记忆 **分离**。下列条目为人工或受控流程写入的 **可审计事实/约束**；
**与对话中模型推测冲突时，以本文件为准**（见 system prompt 中的「安全锁」段）。

---

"""


def _jachin_root() -> Path:
    return Path(os.environ.get("JACHIN_HOME", str(Path.home() / ".jachin")))


def safety_lock_dir() -> Path:
    p = _jachin_root() / "safety_lock"
    p.mkdir(parents=True, exist_ok=True)
    return p


def pending_dir() -> Path:
    p = safety_lock_dir() / "pending"
    p.mkdir(parents=True, exist_ok=True)
    return p


def global_lock_path() -> Path:
    return _jachin_root() / SAFETY_LOCK_FILENAME


def workspace_lock_path() -> Path:
    return _jachin_root() / "workspace" / SAFETY_LOCK_FILENAME


def _nexus_safety_lock_section() -> dict[str, Any]:
    p = _jachin_root() / "nexus_config.json"
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    sl = raw.get("safety_lock")
    return sl if isinstance(sl, dict) else {}


def learn_enabled() -> bool:
    env = os.environ.get("JACHIN_SAFETY_LOCK_LEARN", "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    sec = _nexus_safety_lock_section()
    return bool(sec.get("learn_enabled"))


def append_requires_approval() -> bool:
    """learn 开启时默认 True：Agent 追加只进 pending，由管理员 CLI 刷入 MD。"""
    if not learn_enabled():
        return False
    sec = _nexus_safety_lock_section()
    if sec.get("direct_append_to_md") is True:
        return False
    return sec.get("append_requires_approval", True)


def inject_full_lock_allowed() -> bool:
    """显式开启「全量注入」模式（仍受 inject_max_total_chars 硬帽约束，非 40 万 token）。"""
    if os.environ.get("JACHIN_SAFETY_LOCK_FULL_INJECT", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return bool(_nexus_safety_lock_section().get("full_inject"))


def _strip_control(s: str) -> str:
    return "".join(c for c in s if ord(c) >= 32 or c in "\n\t\r")


def _build_legacy_merged_body(*, max_chars: int) -> str:
    parts: list[str] = []
    gp = global_lock_path()
    wp = workspace_lock_path()
    try:
        if gp.is_file():
            t = gp.read_text(encoding="utf-8", errors="replace").strip()
            if t:
                parts.append("【全局 JACHIN_SAFETY_LOCK.md】\n" + t)
    except OSError as e:
        logger.debug("[safety_lock] read global: %s", e)
    try:
        if wp.is_file() and wp.resolve() != gp.resolve():
            t2 = wp.read_text(encoding="utf-8", errors="replace").strip()
            if t2:
                parts.append("【工作区 workspace/JACHIN_SAFETY_LOCK.md】\n" + t2)
    except OSError as e:
        logger.debug("[safety_lock] read workspace: %s", e)
    if not parts:
        return ""
    body = "\n\n---\n\n".join(parts)
    if len(body) > max_chars:
        body = body[: max_chars - 48] + "\n\n…(安全锁全量模式已截断)\n"
    return body


def get_safety_lock_snippet(*, user_text: str = "") -> str:
    """
    供 system prompt 注入。
    - 默认：**按需域** + 可选 pin + 极短 legacy 全局头（避免上下文坍塌）。
    - full_inject / JACHIN_SAFETY_LOCK_FULL_INJECT：合并全局+工作区，仍受 max_total 硬帽（默认 ≤32k 字符级预算）。
    """
    sec = _nexus_safety_lock_section()
    try:
        max_total = int(sec.get("inject_max_total_chars") or os.environ.get("JACHIN_SAFETY_LOCK_INJECT_MAX") or 8192)
    except (TypeError, ValueError):
        max_total = 8192
    max_total = max(512, min(max_total, 64_000))
    try:
        per_dom = int(sec.get("inject_per_domain_chars") or 4096)
    except (TypeError, ValueError):
        per_dom = 4096
    per_dom = max(256, min(per_dom, max_total))
    try:
        legacy_head = int(sec.get("legacy_global_head_chars", 2048))
    except (TypeError, ValueError):
        legacy_head = 2048
    legacy_head = max(0, min(legacy_head, max_total))

    if inject_full_lock_allowed():
        cap = min(max_total, 32_000)
        body = _build_legacy_merged_body(max_chars=cap)
        if not body.strip():
            return ""
        return (
            "【安全锁 · 全量注入模式（full_inject；已截断至预算内，禁止依赖超大上下文）】\n"
            f"{body}\n"
        )

    try:
        from l3_node.routing.output_format_signals import heuristic_safety_lock_domains
    except ImportError:
        heuristic_safety_lock_domains = lambda _t: []  # type: ignore[misc, assignment]

    domains = heuristic_safety_lock_domains(user_text or "")
    parts: list[str] = []

    pin_p = safety_lock_dir() / "pin.md"
    try:
        if pin_p.is_file():
            pin_t = pin_p.read_text(encoding="utf-8", errors="replace").strip()
            if pin_t:
                cap_pin = min(2000, max_total // 2 or 2000)
                if len(pin_t) > cap_pin:
                    pin_t = pin_t[:cap_pin] + "\n…(pin.md 截断)\n"
                parts.append("【安全锁·短引 pin.md（全意图可挂载；请保持短小）】\n" + pin_t)
    except OSError as e:
        logger.debug("[safety_lock] pin.md: %s", e)

    dmap = {"db": "db_safety_lock.md", "shell": "shell_safety_lock.md"}
    for d in domains:
        fn = dmap.get(d)
        if not fn:
            continue
        fp = safety_lock_dir() / fn
        try:
            if fp.is_file():
                txt = fp.read_text(encoding="utf-8", errors="replace").strip()
                if txt:
                    if len(txt) > per_dom:
                        txt = txt[:per_dom] + "\n…(域安全锁截断)\n"
                    parts.append(f"【安全锁·域 `{d}`（{fn}）】\n{txt}")
        except OSError as ex:
            logger.debug("[safety_lock] domain %s: %s", d, ex)

    if not domains and legacy_head > 0:
        gp = global_lock_path()
        try:
            if gp.is_file():
                head = gp.read_text(encoding="utf-8", errors="replace")[:legacy_head]
                if head.strip():
                    parts.append(
                        "【安全锁·全局头段（未命中 db/shell 域；仅摘要。完整条目请拆至 safety_lock/db_safety_lock.md 等）】\n"
                        + head
                    )
        except OSError:
            pass

    if not parts:
        return ""

    body = "\n\n---\n\n".join(parts)
    if len(body) > max_total:
        body = body[: max_total - 40] + "\n…(安全锁总预算截断)\n"
    return (
        "【安全锁 · 已验证事实（按需域挂载；与 MEMORY.md 分离；冲突时以安全锁为准）】\n"
        f"{body}\n"
    )


def _append_block_to_md(path: Path, block: str, *, new_file_header: str | None = None) -> dict[str, Any]:
    try:
        existing = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    except OSError:
        existing = ""
    if not existing.strip() and new_file_header:
        existing = new_file_header
    if len(existing) + len(block) > _MAX_FILE_CHARS:
        return {
            "ok": False,
            "error": "file_size_cap",
            "message": f"安全锁文件将超过 {_MAX_FILE_CHARS} 字符；请人工归档。",
        }
    new_text = existing.rstrip() + block
    tmp = path.with_suffix(".md.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        logger.warning("[safety_lock] write failed: %s", e)
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return {"ok": False, "error": "io_error", "message": str(e)}
    return {"ok": True}


def _write_full_lock_md(path: Path, full_text: str, *, new_file_header: str | None = None) -> dict[str, Any]:
    """整文件替换写入（TOFU 覆盖同 category 块后使用），带软上限与原子替换。"""
    text = full_text
    if not (text or "").strip() and new_file_header:
        text = new_file_header
    if len(text) > _MAX_FILE_CHARS:
        return {
            "ok": False,
            "error": "file_size_cap",
            "message": f"安全锁文件将超过 {_MAX_FILE_CHARS} 字符；请人工归档。",
        }
    tmp = path.with_suffix(".md.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        logger.warning("[safety_lock] full write failed: %s", e)
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return {"ok": False, "error": "io_error", "message": str(e)}
    return {"ok": True}


def _make_block(
    body: str,
    *,
    source: str,
    tags: list[str] | None,
    eid: str,
    category: str | None = None,
) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tag_s = ""
    if tags:
        tag_s = ", ".join(str(t).strip() for t in tags if str(t).strip())[:200]
    src = re.sub(r"[\r\n]+", " ", (source or "unknown").strip())[:120]
    cat_frag = format_category_header_fragment(category)
    return (
        f"\n\n---\n\n### 条目 `{ts}` · id=`{eid}` · source=`{src}`{cat_frag}"
        f"{(' · tags=`' + tag_s + '`') if tag_s else ''}\n\n{body}\n"
    )


def append_verified_fact(
    body: str,
    *,
    source: str = "core:safety_lock_append",
    tags: list[str] | None = None,
    token: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """
    Agent 调用：默认进入 **pending**（不暴露管理员密钥给模型）。
    token 参数已废弃（保留签名兼容）；不再用于授权写入正式 MD。

    **TOFU（同类二次免批）**：若提供 `category`（如 backend_framework），且正式 MD 中已存在
    同 category 的已批准条目，则跳过 pending，直接覆盖该 category 下旧块并写入新块。
    """
    _ = token  # 兼容旧客户端；忽略，防止「把密钥塞进 Action Input」的伪安全
    if not learn_enabled():
        return {
            "ok": False,
            "error": "learn_disabled",
            "message": (
                "安全锁写入未开启。请设置 JACHIN_SAFETY_LOCK_LEARN=1 或 nexus safety_lock.learn_enabled。"
            ),
        }
    raw = _strip_control((body or "").strip())
    if not raw:
        return {"ok": False, "error": "empty_body", "message": "body/content 不能为空。"}
    if len(raw) > _MAX_ENTRY_CHARS:
        return {
            "ok": False,
            "error": "body_too_large",
            "message": f"单条正文上限 {_MAX_ENTRY_CHARS} 字符。",
        }

    norm_cat = normalize_safety_lock_category(category)
    path = global_lock_path()
    requires = append_requires_approval()
    existing_text = ""
    try:
        if path.is_file():
            existing_text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.debug("[safety_lock] read global for tofu: %s", e)
    approved_cats = scan_approved_categories_in_text(existing_text)
    decision = decide_safety_lock_append_path(
        append_requires_approval=requires,
        category_norm=norm_cat,
        approved_categories=approved_cats,
    )

    def _finalize_replace_append(*, msg: str, status: str) -> dict[str, Any]:
        eid = uuid.uuid4().hex[:12]
        block = _make_block(raw, source=source, tags=tags, eid=eid, category=norm_cat)
        base = existing_text
        if norm_cat:
            base, n_rm = remove_lock_blocks_for_category(base, norm_cat)
            logger.info("[safety_lock] removed %d block(s) for category=%s", n_rm, norm_cat)
        else:
            base = existing_text
        if not (base or "").strip():
            base = _DEFAULT_HEADER
        new_full = base.rstrip() + block
        wr = _write_full_lock_md(path, new_full, new_file_header=_DEFAULT_HEADER)
        if not wr.get("ok"):
            return wr
        logger.info("[safety_lock] %s id=%s path=%s category=%s", status, eid, path, norm_cat)
        out: dict[str, Any] = {
            "ok": True,
            "status": status,
            "entry_id": eid,
            "path": str(path),
            "message": msg,
        }
        if norm_cat:
            out["category"] = norm_cat
        return out

    if decision == "tofu_auto":
        return _finalize_replace_append(
            status="auto_approved_tofu",
            msg=(
                "【同类二次免批】该 category 在正式安全锁中已存在（首条曾人工审批）；"
                "已自动覆盖同 category 旧条目并写入新规则。"
            ),
        )

    if decision == "pending":
        pid = uuid.uuid4().hex[:16]
        rec: dict[str, Any] = {
            "pending_id": pid,
            "body": raw,
            "source": source,
            "tags": tags or [],
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if norm_cat:
            rec["category"] = norm_cat
        p = pending_dir() / f"{pid}.json"
        try:
            p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as e:
            return {"ok": False, "error": "io_error", "message": str(e)}
        logger.info("[safety_lock] pending id=%s category=%s", pid, norm_cat)
        return {
            "ok": True,
            "status": "pending_approval",
            "pending_id": pid,
            "category": norm_cat,
            "message": (
                "已提交待审批（未写入正式安全锁）。请管理员在本机 shell 执行：\n"
                f"  python -m l3_node.jachin_safety_lock_admin approve {pid}\n"
                "（需设置环境变量 JACHIN_SAFETY_LOCK_ADMIN_TOKEN，勿将密钥提供给模型）。"
            ),
        }

    # direct_md
    if norm_cat:
        return _finalize_replace_append(
            status="appended",
            msg="已直接写入 JACHIN_SAFETY_LOCK.md（direct_append_to_md；同 category 已替换旧块）。",
        )

    eid = uuid.uuid4().hex[:12]
    block = _make_block(raw, source=source, tags=tags, eid=eid, category=None)
    r = _append_block_to_md(path, block, new_file_header=_DEFAULT_HEADER)
    if not r.get("ok"):
        return r
    logger.info("[safety_lock] direct appended id=%s path=%s", eid, path)
    return {
        "ok": True,
        "status": "appended",
        "entry_id": eid,
        "path": str(path),
        "message": "已直接追加到 JACHIN_SAFETY_LOCK.md（direct_append_to_md 模式）。",
    }


def list_pending_entries() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    d = pending_dir()
    if not d.is_dir():
        return {"ok": True, "count": 0, "items": []}
    for f in sorted(d.glob("*.json")):
        try:
            o = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(o, dict):
                items.append(o)
        except Exception:
            continue
    return {"ok": True, "count": len(items), "items": items}


def approve_pending(pending_id: str, admin_token: str) -> dict[str, Any]:
    """仅 CLI / 运维脚本调用：校验主机环境变量中的管理员密钥。"""
    pid = (pending_id or "").strip()
    if not pid:
        return {"ok": False, "error": "empty_pending_id"}
    exp = os.environ.get("JACHIN_SAFETY_LOCK_ADMIN_TOKEN", "").strip()
    if not exp:
        return {
            "ok": False,
            "error": "admin_token_not_configured",
            "message": "请在本机设置环境变量 JACHIN_SAFETY_LOCK_ADMIN_TOKEN（勿写入仓库或发给模型）。",
        }
    if (admin_token or "").strip() != exp:
        return {"ok": False, "error": "forbidden", "message": "管理员密钥不匹配。"}
    p = pending_dir() / f"{pid}.json"
    if not p.is_file():
        return {"ok": False, "error": "not_found", "message": f"未找到 pending_id={pid}"}
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "error": "invalid_pending", "message": str(e)}
    if not isinstance(rec, dict):
        return {"ok": False, "error": "invalid_pending"}
    body = str(rec.get("body") or "").strip()
    if not body:
        return {"ok": False, "error": "empty_body"}
    src = str(rec.get("source") or "approved_pending")
    tags = rec.get("tags")
    if not isinstance(tags, list):
        tags = None
    _rcat = rec.get("category")
    catn = normalize_safety_lock_category(str(_rcat).strip() if _rcat is not None else None)
    eid = uuid.uuid4().hex[:12]
    block = _make_block(body, source=src, tags=tags, eid=eid, category=catn)
    path = global_lock_path()
    if catn:
        try:
            existing_text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        except OSError:
            existing_text = ""
        base, _n = remove_lock_blocks_for_category(existing_text, catn)
        if not (base or "").strip():
            base = _DEFAULT_HEADER
        new_full = base.rstrip() + block
        r = _write_full_lock_md(path, new_full, new_file_header=_DEFAULT_HEADER)
    else:
        r = _append_block_to_md(path, block, new_file_header=_DEFAULT_HEADER)
    if not r.get("ok"):
        return r
    try:
        p.unlink()
    except OSError:
        pass
    logger.info("[safety_lock] approved pending=%s -> entry_id=%s", pid, eid)
    return {"ok": True, "pending_id": pid, "entry_id": eid, "path": str(path)}


def reject_pending(pending_id: str, admin_token: str) -> dict[str, Any]:
    pid = (pending_id or "").strip()
    exp = os.environ.get("JACHIN_SAFETY_LOCK_ADMIN_TOKEN", "").strip()
    if not exp:
        return {"ok": False, "error": "admin_token_not_configured"}
    if (admin_token or "").strip() != exp:
        return {"ok": False, "error": "forbidden"}
    p = pending_dir() / f"{pid}.json"
    if not p.is_file():
        return {"ok": False, "error": "not_found"}
    try:
        p.unlink()
    except OSError as e:
        return {"ok": False, "error": "io_error", "message": str(e)}
    return {"ok": True, "pending_id": pid, "message": "已拒绝并删除 pending 文件。"}


def remove_entry_by_id(entry_id: str) -> dict[str, Any]:
    """从全局 JACHIN_SAFETY_LOCK.md 删除含 id=`entry_id` 的条目块（按分隔符定位，兼容首条目前缀）。"""
    eid = (entry_id or "").strip()
    if not eid:
        return {"ok": False, "error": "empty_id", "message": "entry_id 不能为空。"}
    path = global_lock_path()
    if not path.is_file():
        return {"ok": False, "error": "no_file", "message": "安全锁文件不存在。"}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"ok": False, "error": "io_error", "message": str(e)}
    marker = f"id=`{eid}`"
    idx = text.find(marker)
    if idx < 0:
        return {"ok": False, "error": "not_found", "message": f"未找到 id=`{eid}` 的条目块。"}
    sep = "\n\n---\n\n### 条目"
    block_start = text.rfind(sep, 0, idx)
    if block_start < 0:
        block_start = text.find("### 条目", 0, idx + 1)
        if block_start < 0 or block_start > idx:
            return {"ok": False, "error": "not_found", "message": f"未找到 id=`{eid}` 的条目块。"}
    next_sep = text.find(sep, idx)
    if next_sep < 0:
        block_end = len(text)
    else:
        block_end = next_sep
    new_text = (text[:block_start] + text[block_end:]).rstrip() + "\n"
    try:
        path.write_text(new_text, encoding="utf-8")
    except OSError as e:
        return {"ok": False, "error": "io_error", "message": str(e)}
    logger.info("[safety_lock] removed entry_id=%s", eid)
    return {"ok": True, "removed": eid, "path": str(path)}


def run_maintenance_scan() -> dict[str, Any]:
    """
    旁路维护：启发式扫描（占位）。全量 LLM 冲突检测可后续接 Qwen-Max + 工单。
    当前：统计条目数、文件大小；若条目块过多则提示压实。
    """
    gp = global_lock_path()
    if not gp.is_file():
        return {"ok": True, "message": "无全局安全锁文件", "entry_blocks": 0}
    try:
        text = gp.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"ok": False, "error": str(e)}
    n = len(re.findall(r"^### 条目\s", text, re.MULTILINE))
    warn = []
    if n > 80:
        warn.append("entry_count_high_suggest_compaction")
    if len(text) > 120_000:
        warn.append("file_large_suggest_split_domain_files")
    log_p = safety_lock_dir() / "maintenance.log"
    line = f"{datetime.now(timezone.utc).isoformat()} entries={n} bytes={len(text)} warn={warn}\n"
    try:
        log_p.parent.mkdir(parents=True, exist_ok=True)
        with log_p.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
    return {"ok": True, "entry_blocks": n, "bytes": len(text), "warnings": warn}
