"""
L5 本地记忆「梦境合并」（**已全局停用**）：原 l3_local.json LLM 合并管线。

入口 ``compact_local_memory_if_needed`` 现为 no-op（Memory Nexus / Chroma 取代）。
下文解析/原子写等辅助函数仍保留，供查阅或零星脚本引用。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 与 local_memory._MAX_ENTRIES 对齐：合并后再截断，避免无限增长
_MAX_FINAL_ENTRIES = 200

_MAX_LLM_INPUT_CHARS = 52_000
_MAX_OUTPUT_ENTRIES = 120
_COMPACT_TIMEOUT_SEC = 120.0
# 与 LLM 约定：若启用 response_format=json_object，必须用对象包裹数组
_MEMORY_COMPACT_JSON_KEY = "_memory_compact_items"

# 从杂乱输出中提取首个完整 JSON 数组片段（括号配对，尊重字符串内引号）
_JSON_ARRAY_HEAD_RE = re.compile(r"\[")


def _strip_json_fence(text: str) -> str:
    s = (text or "").strip()
    if not s.startswith("```"):
        return s
    s = re.sub(r"^```\w*\s*", "", s)
    s = re.sub(r"\s*```\s*$", "", s).strip()
    return s


def _extract_balanced_json_array(text: str) -> str | None:
    """从 text 中截取从第一个 '[' 起括号平衡的 JSON 数组子串；失败则 None。"""
    m = _JSON_ARRAY_HEAD_RE.search(text)
    if not m:
        return None
    start = m.start()
    depth = 0
    in_str = False
    esc = False
    quote = ""
    s = text
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == quote:
                in_str = False
            continue
        if c in "\"'":
            in_str = True
            quote = c
            continue
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return None


def _coerce_memory_entry_list(obj: Any) -> list[dict[str, Any]] | None:
    """只接受对象列表；过滤非 dict。"""
    if not isinstance(obj, list):
        return None
    out: list[dict[str, Any]] = []
    for x in obj:
        if isinstance(x, dict):
            out.append(x)
    return out if out else None


def _parse_llm_memory_json(content: str) -> list[dict[str, Any]] | None:
    """
    强韧解析：禁止把未校验文本写入磁盘。
    顺序：去围栏 → 整段 json.loads → 对象键 _memory_compact_items → 括号平衡数组 → 宽松 re 兜底。
    """
    raw = _strip_json_fence((content or "").strip())
    if not raw:
        return None

    # 1) 整段即 JSON
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and _MEMORY_COMPACT_JSON_KEY in obj:
            return _coerce_memory_entry_list(obj.get(_MEMORY_COMPACT_JSON_KEY))
        if isinstance(obj, list):
            return _coerce_memory_entry_list(obj)
    except json.JSONDecodeError:
        pass

    # 2) 平衡括号数组
    frag = _extract_balanced_json_array(raw)
    if frag:
        try:
            obj = json.loads(frag)
            return _coerce_memory_entry_list(obj)
        except json.JSONDecodeError:
            pass

    # 3) 最后兜底：DOTALL 贪婪匹配首个 [...]（可能在 Markdown 噪声中）
    m = re.search(r"\[[\s\S]*\]", raw, flags=re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            return _coerce_memory_entry_list(obj)
        except json.JSONDecodeError:
            pass

    return None


def _validate_roundtrip(entries: list[dict[str, Any]]) -> bool:
    try:
        json.dumps(entries, ensure_ascii=False)
        return True
    except (TypeError, ValueError):
        return False


def _atomic_write_json_array(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".compact.tmp")
    payload = json.dumps(entries, ensure_ascii=False, indent=2)
    tmp.write_text(payload, encoding="utf-8")
    os.replace(str(tmp), str(path))


def _shadow_path(main: Path) -> Path:
    return main.parent / f"{main.name}.shadow"


def _unlink_quiet(p: Path) -> None:
    try:
        if p.exists():
            p.unlink()
    except OSError as e:
        logger.debug("[MemoryCompact] 删除影子文件跳过: %s", e)


def _entry_fingerprint(e: dict[str, Any]) -> tuple[str, str]:
    return (
        str(e.get("tag") or "").strip(),
        (str(e.get("content") or "").strip())[:400],
    )


def _merge_post_snapshot_entries(
    merged: list[dict[str, Any]],
    live_main: list[dict[str, Any]],
    snapshot_ts: float,
) -> list[dict[str, Any]]:
    """
    将快照之后在主库上新增的条目并入 LLM 合并结果，避免并发聊天写入被覆盖。
    """
    fp = {_entry_fingerprint(x) for x in merged if isinstance(x, dict)}
    out: list[dict[str, Any]] = list(merged)
    for e in live_main:
        if not isinstance(e, dict):
            continue
        try:
            ts = float(e.get("timestamp") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        if ts <= snapshot_ts:
            continue
        k = _entry_fingerprint(e)
        if k in fp:
            continue
        out.append(e)
        fp.add(k)
    out.sort(key=lambda x: float(x.get("timestamp") or 0), reverse=True)
    if len(out) > _MAX_FINAL_ENTRIES:
        out = out[:_MAX_FINAL_ENTRIES]
    return out


async def compact_local_memory_if_needed(
    file_path: str,
    threshold: int = 150,
    *,
    force: bool = False,
) -> str:
    """
    若 JSON 数组条目数 > threshold（或 force=True 为显式口令「立刻整理」），调用轻量 LLM 合并后原子覆写。

    Args:
        threshold: 自动/定时/每轮检查路径下的条数下限；force=True 时忽略。
        force: 用户显式口令（整理本地记忆/梦境合并等）时为 True，**无视阈值**立即尝试合并；
            主库为空数组时仍不调用 LLM，返回简短说明。

    Returns:
        成功：简短中文报告；未触发/失败：空字符串（fail-open，不抛错）。
    """
    # [DEPRECATED] 系统已全面迁移至 Memory Nexus（Chroma）。保留 Drawer 原文，不再对 l3_local.json 做破坏性 LLM 合并。
    logger.debug(
        "[Memory Compactor] 触发已拦截（旧 JSON 坍缩全局停用），file=%s threshold=%s force=%s",
        file_path,
        threshold,
        force,
    )
    return ""
