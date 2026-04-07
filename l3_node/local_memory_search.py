"""
L3 本地记忆检索（断网侧）：对标 OpenClaw memory_search 的产品封装。

- 数据源：~/.jachin/memory/l3_local.json + 可选 memory/MEMORY.md 分块
- 打分：简易关键词重叠 + 时间半衰衰减
- 重排：MMR（最大边际相关性）降低冗余

供 Native 工具 core:local_memory_search 调用。
"""
from __future__ import annotations

import json
import logging
import math
import re
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_JACHIN_ROOT = Path.home() / ".jachin"
_MEMORY_DIR = _JACHIN_ROOT / "memory"
_LOCAL_DB = _MEMORY_DIR / "l3_local.json"
_MEMORY_MD = _MEMORY_DIR / "MEMORY.md"


def _tokenize(text: str) -> list[str]:
    if not text or not isinstance(text, str):
        return []
    tokens = re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]|\w+", text)
    return [t.lower() for t in tokens if t]


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _age_decay(ts: float, *, half_life_days: float) -> float:
    if half_life_days <= 0:
        return 1.0
    age_sec = max(0.0, time.time() - float(ts or 0))
    age_days = age_sec / 86400.0
    return math.exp(-age_days * math.log(2) / half_life_days)


def _chunks_from_memory_md(max_chunks: int = 24) -> list[dict[str, Any]]:
    if not _MEMORY_MD.exists():
        return []
    try:
        raw = _MEMORY_MD.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.debug("[L3Search] MEMORY.md 读取失败: %s", e)
        return []
    parts = re.split(r"\n(?=#{1,6}\s)", raw)
    out: list[dict[str, Any]] = []
    for i, block in enumerate(parts):
        block = (block or "").strip()
        if len(block) < 20:
            continue
        out.append({
            "id": f"memory_md#{i}",
            "tag": "MEMORY.md",
            "content": block[:8000],
            "timestamp": _MEMORY_MD.stat().st_mtime if _MEMORY_MD.exists() else time.time(),
            "source": "memory_md",
        })
        if len(out) >= max_chunks:
            break
    return out


def _load_l3_entries() -> list[dict[str, Any]]:
    from l3_node.local_memory import load_raw_entries
    from l3_node.local_memory_ranking import sort_entries_by_agent_priority

    try:
        raw = load_raw_entries()
    except Exception as e:
        logger.debug("[L3Search] l3_local 加载失败: %s", e)
        return []
    return sort_entries_by_agent_priority(raw)


def _mmr_select(
    ranked: list[tuple[float, dict[str, Any]]],
    query_toks: set[str],
    *,
    top_k: int,
    lambda_mult: float,
) -> list[dict[str, Any]]:
    """ranked: (relevance_score, doc) 已按分数降序；MMR 贪心选 top_k。"""
    if not ranked:
        return []
    lam = max(0.0, min(1.0, lambda_mult))
    candidates = list(ranked)
    selected: list[dict[str, Any]] = []
    selected_toks: list[set[str]] = []

    def doc_toks(d: dict[str, Any]) -> set[str]:
        return set(_tokenize((d.get("content") or "") + " " + str(d.get("tag", ""))))

    while candidates and len(selected) < top_k:
        best_i = -1
        best_mm = -1e9
        for i, (rel, doc) in enumerate(candidates):
            dt = doc_toks(doc)
            div = 0.0
            if selected_toks:
                div = max(_jaccard(dt, st) for st in selected_toks)
            mmr = lam * rel - (1.0 - lam) * div
            if mmr > best_mm:
                best_mm = mmr
                best_i = i
        if best_i < 0:
            break
        _, doc = candidates.pop(best_i)
        selected.append(doc)
        selected_toks.append(doc_toks(doc))
    return selected


def search_local_memories(
    query: str,
    *,
    top_k: int = 8,
    mmr_lambda: float = 0.55,
    half_life_days: float = 30.0,
    include_memory_md: bool = True,
    candidate_pool: int = 32,
) -> dict[str, Any]:
    """
    对 L3 本地记忆执行检索 + 衰减 + MMR。

    返回 { ok, query, hits: [{id, tag, content, score, source}], meta }
    """
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "query 为空", "hits": []}

    qset = set(_tokenize(q))
    if not qset:
        return {"ok": True, "query": q, "hits": [], "meta": {"reason": "no_query_tokens"}}

    docs: list[dict[str, Any]] = []
    for idx, e in enumerate(_load_l3_entries()):
        cid = str(e.get("id") or f"l3#{idx}")
        docs.append({
            "id": cid,
            "tag": e.get("tag", "general"),
            "content": (e.get("content") or "")[:8000],
            "timestamp": float(e.get("timestamp", 0) or 0),
            "source": str(e.get("source", "l3_local")),
        })
    if include_memory_md:
        docs.extend(_chunks_from_memory_md())

    scored: list[tuple[float, dict[str, Any]]] = []
    for d in docs:
        text = (d.get("content") or "") + " " + str(d.get("tag", ""))
        tset = set(_tokenize(text))
        overlap = _jaccard(qset, tset)
        # 轻量 BM25 风格：重复 query token 加权
        bonus = 0.0
        for tok in qset:
            if len(tok) >= 2 and tok in text.lower():
                bonus += 0.04
        base = min(1.0, overlap * 1.2 + bonus)
        decay = _age_decay(float(d.get("timestamp", 0) or 0), half_life_days=half_life_days)
        rel = base * (0.35 + 0.65 * decay)
        if rel > 0.01:
            d2 = {**d, "score": round(rel, 4)}
            scored.append((rel, d2))

    scored.sort(key=lambda x: -x[0])
    pool = scored[: max(top_k * 4, candidate_pool)]
    hits = _mmr_select(pool, qset, top_k=top_k, lambda_mult=mmr_lambda)
    return {
        "ok": True,
        "query": q,
        "hits": hits,
        "meta": {
            "mmr_lambda": mmr_lambda,
            "half_life_days": half_life_days,
            "pool": len(pool),
            "include_memory_md": include_memory_md,
        },
    }
