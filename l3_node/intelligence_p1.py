"""
智能化升级 P1 / P1+：用户偏好、澄清队列、shell 策略、协同原生派发开关。

- 偏好：~/.jachin/config/user_preferences.json（浅合并 dict）
- 澄清：~/.jachin/workspace/clarification_pending.json（列表，注入 System Prompt）
- P1+：`coordinate_native_tool_dispatch`（默认 true）— 协同子任务 input_data.type=native_tool 时直接 run_tool
- 后台 shell：`l3_node/shell_jobs.py`，配置见 `shell_background_max_jobs`、`shell_job_cancel_enabled` 等
- 配置：~/.jachin/nexus_config.json → intelligence_p1 段
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from l3_node.jachin_config import get_config_root, get_jachin_root

logger = logging.getLogger(__name__)

_MAX_CLARIFICATIONS = 50
_NEXUS_CONFIG = Path.home() / ".jachin" / "nexus_config.json"


def _prefs_path() -> Path:
    p = get_config_root() / "user_preferences.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _clarification_path() -> Path:
    p = get_jachin_root() / "workspace" / "clarification_pending.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def get_intel_p1_config() -> dict[str, Any]:
    """读取 nexus_config.json 中的 intelligence_p1 段。"""
    try:
        if not _NEXUS_CONFIG.exists():
            return {}
        raw = _NEXUS_CONFIG.read_text(encoding="utf-8")
        cfg = json.loads(raw)
        sec = cfg.get("intelligence_p1")
        return sec if isinstance(sec, dict) else {}
    except Exception as e:
        logger.debug("[P1] 读取 nexus_config intelligence_p1 失败: %s", e)
        return {}


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_user_preferences() -> dict[str, Any]:
    path = _prefs_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("[P1] 读取 user_preferences 失败: %s", e)
        return {}


def merge_preference_updates(updates: dict[str, Any]) -> None:
    """浅合并写入用户偏好（仅 str / int / float / bool 值落盘，其余转 str）。"""
    if not updates:
        return
    cur = load_user_preferences()
    for k, v in updates.items():
        if v is None:
            continue
        key = str(k).strip()
        if not key:
            continue
        if isinstance(v, (str, int, float, bool)):
            cur[key] = v
        elif isinstance(v, dict):
            cur[key] = v
        else:
            cur[str(k)] = str(v)
    _atomic_write_json(_prefs_path(), cur)
    logger.info("[P1] 已合并用户偏好键: %s", list(updates.keys()))


def load_clarification_queue() -> list[dict[str, Any]]:
    path = _clarification_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict) and x.get("text")]
        return []
    except Exception as e:
        logger.warning("[P1] 读取 clarification 队列失败: %s", e)
        return []


def enqueue_clarification(text: str) -> None:
    """梦境或子系统发现需用户确认时入队。"""
    t = (text or "").strip()
    if not t:
        return
    q = load_clarification_queue()
    q.append({
        "id": str(uuid.uuid4())[:12],
        "text": t[:2000],
        "created_ts": int(time.time()),
    })
    if len(q) > _MAX_CLARIFICATIONS:
        q = q[-_MAX_CLARIFICATIONS:]
    _atomic_write_json(_clarification_path(), q)
    logger.info("[P1] 待澄清入队: %s…", t[:80])


def dismiss_clarification(item_id: str) -> bool:
    """按 id 移除一条待澄清。"""
    iid = (item_id or "").strip()
    if not iid:
        return False
    q = load_clarification_queue()
    new_q = [x for x in q if str(x.get("id", "")) != iid]
    if len(new_q) == len(q):
        return False
    _atomic_write_json(_clarification_path(), new_q)
    return True


def clear_clarification_queue() -> None:
    _atomic_write_json(_clarification_path(), [])


def get_p1_prompt_injections() -> tuple[str, str]:
    """
    返回 (用户偏好块, 待澄清块)，供 _build_system_prompt 拼接。
    无内容时返回空串。
    """
    cfg = get_intel_p1_config()
    if cfg.get("inject_preferences_to_prompt") is False:
        pref_block = ""
    else:
        prefs = load_user_preferences()
        if prefs:
            lines = [f"- {k}: {v}" for k, v in sorted(prefs.items())[:24]]
            pref_block = (
                "\n【用户偏好（结构化，来自梦境/配置；回答时请尊重）】\n"
                + "\n".join(lines)
                + "\n"
            )
        else:
            pref_block = ""

    if cfg.get("inject_clarifications_to_prompt") is False:
        clar_block = ""
    else:
        q = load_clarification_queue()
        if q:
            lines = []
            for item in q[-12:]:
                tid = item.get("id", "")
                tx = str(item.get("text", "")).strip()
                if tx:
                    lines.append(f"- [{tid}] {tx}")
            if lines:
                clar_block = (
                    "\n【有待用户澄清（请先简要确认或追问，勿臆断）】\n"
                    + "\n".join(lines)
                    + "\n"
                )
            else:
                clar_block = ""
        else:
            clar_block = ""

    return pref_block, clar_block


def strip_dream_auxiliary_lines(text: str) -> str:
    """去掉融合输出中的机器行，再写入向量库/长期记忆。"""
    if not text or not text.strip():
        return text or ""
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("PREFERENCE_JSON:") or s.startswith("CLARIFICATION:"):
            continue
        out.append(line)
    return "\n".join(out).strip()


def ingest_dream_auxiliary_lines(text: str) -> None:
    """
    解析梦境/融合输出末尾的机器行（L2 自然段融合用）：
    PREFERENCE_JSON: {...}
    CLARIFICATION: 一句话
    """
    if not text or not text.strip():
        return
    for line in text.strip().splitlines()[-12:]:
        s = line.strip()
        if s.startswith("PREFERENCE_JSON:"):
            payload = s[len("PREFERENCE_JSON:") :].strip()
            try:
                obj = json.loads(payload)
                if isinstance(obj, dict):
                    merge_preference_updates(obj)
            except json.JSONDecodeError:
                logger.debug("[P1] PREFERENCE_JSON 解析失败: %s", payload[:120])
        elif s.startswith("CLARIFICATION:"):
            msg = s[len("CLARIFICATION:") :].strip()
            if msg:
                enqueue_clarification(msg)


_DEFAULT_SHELL_BLOCKLIST = [
    "rm -rf",
    "mkfs",
    "dd if=",
    ":(){:|:&};:",
    "powershell -enc",
    "> /dev/sd",
    "format c:",
    "diskpart",
]


def assert_shell_exec_allowed(command: str) -> None:
    """
    根据 intelligence_p1 校验 shell 命令；不通过时 raise ValueError。
    """
    cmd = (command or "").strip()
    if not cmd:
        raise ValueError("命令为空")
    cfg = get_intel_p1_config()
    blocklist = cfg.get("shell_exec_blocklist_patterns")
    if not isinstance(blocklist, list) or not blocklist:
        blocklist = _DEFAULT_SHELL_BLOCKLIST
    lowered = cmd.lower()
    for pat in blocklist:
        if not isinstance(pat, str) or not pat.strip():
            continue
        if pat.lower() in lowered:
            raise ValueError(f"shell_exec 已拦截危险模式: {pat!r}")

    mode = str(cfg.get("shell_exec_mode", "open") or "open").lower()
    if mode != "restricted":
        return
    prefixes = cfg.get("shell_exec_allowlist_prefixes")
    if not isinstance(prefixes, list) or not prefixes:
        raise ValueError("shell_exec_mode=restricted 但未配置 shell_exec_allowlist_prefixes，已拒绝执行")
    ok = False
    for p in prefixes:
        if not isinstance(p, str):
            continue
        pre = p.strip()
        if not pre:
            continue
        if lowered.startswith(pre.lower()):
            ok = True
            break
    if not ok:
        raise ValueError(
            "shell_exec 受限模式：命令必须以 allowlist 前缀之一开头。"
            f" 当前: {cmd[:80]!r}"
        )


def merge_preferences_from_dream_item(item: dict[str, Any]) -> None:
    """从梦境 JSON 条目中提取 preferences / preference 字段。"""
    if not isinstance(item, dict):
        return
    raw = item.get("preferences")
    if raw is None:
        raw = item.get("preference")
    if isinstance(raw, dict) and raw:
        merge_preference_updates(raw)
