"""System prompt suffix composition: tool ordering, priority eviction, and hard caps."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FOOTER_CHUNK = "react_footer"
_PLAN_DISK = "task_plan_disk"


# 原生 A 股数据（AKShare）：排序靠前，避免模型在大量 MCP 中优先选 mcp:fetch 捏造 URL
_TOOL_SORT_PRIORITY_PREFIXES: tuple[str, ...] = ("core:akshare_", "core:yfinance_")


def sort_tools_by_id(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not tools:
        return []

    def _key(t: dict[str, Any]) -> tuple[int, str]:
        tid = str(t.get("id") or t.get("label") or "").lower()
        pri = 0 if any(tid.startswith(p) for p in _TOOL_SORT_PRIORITY_PREFIXES) else 1
        return (pri, tid)

    return sorted(tools, key=_key)


def _prompt_section_from_nexus() -> dict[str, Any]:
    try:
        from l3_node.jachin_config import get_jachin_root

        p = get_jachin_root() / "nexus_config.json"
    except ImportError:
        p = Path.home() / ".jachin" / "nexus_config.json"
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        sec = raw.get("prompt") if isinstance(raw.get("prompt"), dict) else {}
        return sec if sec else raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def load_prompt_suffix_budget() -> int:
    sec = _prompt_section_from_nexus()
    try:
        v = sec.get("prompt_suffix_max_chars")
        if v is None:
            return 0
        n = int(v)
        return max(0, min(500_000, n))
    except Exception:
        return 0


def load_system_prompt_total_max_chars() -> int:
    sec = _prompt_section_from_nexus()
    try:
        v = sec.get("system_prompt_max_chars")
        if v is None:
            return 0
        n = int(v)
        return max(0, min(2_000_000, n))
    except Exception:
        return 0


def cap_prompt_flat_text(text: str, max_chars: int, marker: str = "\n…(截断)\n") -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    keep = max(0, max_chars - len(marker))
    return text[:keep] + marker


@dataclass
class SuffixChunk:
    tier: str  # high | mid | low（兼容旧逻辑；驱逐顺序以 eviction_rank 为准）
    name: str
    text: str
    eviction_rank: int | None = None  # 越小越先被驱逐；None 时按 tier 推断

    @property
    def chars(self) -> int:
        return len(self.text or "")


def effective_eviction_rank(chunk: SuffixChunk) -> int:
    if chunk.eviction_rank is not None:
        return int(chunk.eviction_rank)
    tier = (chunk.tier or "").lower()
    return {"low": 25, "mid": 55, "high": 85}.get(tier, 50)


def compose_suffix_with_eviction(
    chunks: list[SuffixChunk], max_chars: int, *, log_eviction: bool = True
) -> str:
    """超标时按 eviction_rank 升序驱逐；同 rank 先丢更大的块。

    task_plan_disk 大块先软截断再整块删；硬截断时尽量保留 footer。
    """
    if max_chars <= 0:
        return "".join(c.text for c in chunks if c.text)

    working: list[SuffixChunk] = []
    for c in chunks:
        if not (c.text or "").strip():
            continue
        working.append(
            SuffixChunk(
                c.tier,
                c.name,
                c.text,
                eviction_rank=c.eviction_rank,
            )
        )

    removed: list[str] = []

    def total_sz() -> int:
        return sum(len(x.text) for x in working)

    def victim_key(c: SuffixChunk) -> tuple[int, int]:
        return (effective_eviction_rank(c), -len(c.text))

    while total_sz() > max_chars:
        candidates = [c for c in working if c.name != _FOOTER_CHUNK]
        if not candidates:
            break
        victim = min(candidates, key=victim_key)
        if victim.name == _PLAN_DISK and len(victim.text) > 1200:
            head = min(1000, max(400, max_chars // 3))
            new_text = victim.text[:head] + "\n…(task_plan 截断，首段优先；完整见磁盘 task_plan.md)\n"
            if len(new_text) < len(victim.text):
                idx = working.index(victim)
                working[idx] = SuffixChunk(
                    victim.tier,
                    victim.name,
                    new_text,
                    eviction_rank=victim.eviction_rank,
                )
                if total_sz() <= max_chars:
                    break
                continue
        working.remove(victim)
        removed.append(victim.name)

    # 组装：非 footer 在前，footer 在后，紧急截断不砍掉 SSOT 页脚全文（若 budget 极紧再缩 footer）
    body_chunks = [c for c in working if c.name != _FOOTER_CHUNK]
    footer_chunks = [c for c in working if c.name == _FOOTER_CHUNK]
    body_text = "".join(c.text for c in body_chunks)
    footer_text = "".join(c.text for c in footer_chunks)
    combined = body_text + footer_text
    if len(combined) > max_chars:
        reserve_footer = len(footer_text) + 48
        if reserve_footer >= max_chars:
            combined = cap_prompt_flat_text(footer_text, max_chars, "\n…(footer 截断)\n")
        else:
            allow_body = max_chars - reserve_footer
            body_text = cap_prompt_flat_text(body_text, allow_body, "\n…(suffix 硬截断，保留页脚)\n")
            combined = body_text + footer_text
            if len(combined) > max_chars:
                combined = cap_prompt_flat_text(combined, max_chars, "\n…(suffix 硬截断)\n")

    if removed and log_eviction:
        logger.warning(
            "[prompt_suffix_eviction] removed=%s out_chars=%s budget=%s",
            removed,
            len(combined),
            max_chars,
        )
    return combined


def apply_system_prompt_total_cap(
    *,
    prefix_without_tools: str,
    tools_desc: str,
    prefix_after_tools: str,
    suffix_chunks: list[SuffixChunk],
    suffix_budget: int,
    total_max_chars: int,
) -> tuple[str, str]:
    """总长度超 total_max_chars 时先收紧后缀预算重算，仍超则截断 tools_desc。

    返回 (完整前缀, 后缀)。
    """
    if total_max_chars <= 0:
        suf = compose_suffix_with_eviction(suffix_chunks, suffix_budget, log_eviction=True)
        return prefix_without_tools + tools_desc + prefix_after_tools, suf

    def full_prefix(td: str) -> str:
        return prefix_without_tools + td + prefix_after_tools

    suf = compose_suffix_with_eviction(suffix_chunks, suffix_budget, log_eviction=True)
    prefix = full_prefix(tools_desc)
    if len(prefix) + len(suf) <= total_max_chars:
        return prefix, suf

    # 前缀已占满总预算时先砍工具表，再给后缀留最小隙
    min_suffix_room = min(4096, max(512, total_max_chars // 6))
    if len(prefix) + min_suffix_room > total_max_chars:
        max_td0 = max(
            2048,
            total_max_chars - len(prefix_without_tools) - len(prefix_after_tools) - min_suffix_room - 64,
        )
        tools_desc = cap_prompt_flat_text(
            tools_desc, max_td0, "\n…(工具描述截断；运行时以已注册工具 id 为准)\n"
        )
        prefix = full_prefix(tools_desc)

    room_suffix = total_max_chars - len(prefix) - 32
    if room_suffix < min_suffix_room:
        room_suffix = min_suffix_room
    suf = compose_suffix_with_eviction(suffix_chunks, room_suffix, log_eviction=False)
    if len(prefix) + len(suf) <= total_max_chars:
        suf = compose_suffix_with_eviction(suffix_chunks, room_suffix, log_eviction=True)
        return prefix, suf

    # 二分式收紧后缀：在 [512, room_suffix] 内找最大可行 budget（中间步不打驱逐日志）
    lo, hi = 512, max(512, room_suffix)
    winning_budget = 512
    while lo <= hi:
        mid = (lo + hi) // 2
        trial = compose_suffix_with_eviction(suffix_chunks, mid, log_eviction=False)
        if len(prefix) + len(trial) <= total_max_chars:
            winning_budget = mid
            lo = mid + 1
        else:
            hi = mid - 1
    suf = compose_suffix_with_eviction(suffix_chunks, winning_budget, log_eviction=True)

    prefix = full_prefix(tools_desc)
    if len(prefix) + len(suf) <= total_max_chars:
        return prefix, suf

    max_td = total_max_chars - len(prefix_without_tools) - len(prefix_after_tools) - len(suf) - 48
    max_td = max(4096, max_td)
    td2 = cap_prompt_flat_text(tools_desc, max_td, "\n…(工具描述截断；运行时以已注册工具 id 为准)\n")
    prefix = full_prefix(td2)
    if len(prefix) + len(suf) > total_max_chars:
        max_td2 = max(2048, total_max_chars - len(prefix_without_tools) - len(prefix_after_tools) - len(suf) - 48)
        td2 = cap_prompt_flat_text(tools_desc, max_td2, "\n…(工具描述截断)\n")
        prefix = full_prefix(td2)
    return prefix, suf
