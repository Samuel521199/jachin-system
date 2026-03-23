"""
Jachin Nexus V2 - L2 向量梦境引擎 (LanceDB)

L2 启动时初始化 LanceDB，默认 ~/.jachin/lancedb_data。
K8s 部署：设置 JACHIN_LANCEDB_PATH 或 JACHIN_DATA_DIR 指向共享卷（如 NFS），
确保多 Pod 横向扩展时记忆数据一致。
memories 表 Schema: id, vector, text, node_id, sub_account_id, timestamp, namespace。
namespace: 记忆范围/命名空间，默认 default，用于细粒度权限隔离（如客服知识库、部门共享记忆）。
语义级记忆检索与梦境消解（向量相似度去重）。
P2-9：可选 `reinforce_score` 列（新表 init 行含默认值）；检索加权另依赖 `memory_reinforcement.json` 侧车。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any

from core.embedding import BaseEmbedder, get_embedder

logger = logging.getLogger(__name__)

def _get_lancedb_path() -> Path:
    """LanceDB 存储路径，K8s 可配置为共享卷"""
    lancedb_path = os.environ.get("JACHIN_LANCEDB_PATH")
    if lancedb_path:
        return Path(lancedb_path).expanduser()
    data_dir = os.environ.get("JACHIN_DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser() / "lancedb_data"
    return Path.home() / ".jachin" / "lancedb_data"

_LANCEDB_PATH = _get_lancedb_path()
_TABLE_NAME = "memories"
_NEXUS_CONFIG = Path.home() / ".jachin" / "nexus_config.json"


def _p2_reinforce_params() -> tuple[bool, float, float]:
    """是否启用检索强化、权重、单项 raw 上限（与侧车合并前）。"""
    try:
        if not _NEXUS_CONFIG.exists():
            return True, 0.12, 3.0
        cfg = json.loads(_NEXUS_CONFIG.read_text(encoding="utf-8"))
        sec = cfg.get("intelligence_p2")
        if not isinstance(sec, dict):
            return True, 0.12, 3.0
        if sec.get("reinforce_search_enabled") is False:
            return False, 0.0, 3.0
        w = float(sec.get("reinforce_weight", 0.12))
        mx = float(sec.get("reinforce_max_boost", 3.0))
        return True, max(0.0, min(0.5, w)), max(0.5, min(20.0, mx))
    except Exception:
        return True, 0.12, 3.0


def _get_embedder() -> BaseEmbedder | None:
    """获取 Embedder，失败时返回 None"""
    try:
        return get_embedder()
    except Exception as e:
        logger.warning("[L2Memory] Embedder 初始化失败: %s", e)
        return None


def _run_embed_sync(embedder: BaseEmbedder, text: str) -> list[float] | None:
    """同步运行 embed_text，兼容已有事件循环"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(embedder.embed_text(text))
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, embedder.embed_text(text))
        return future.result()


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a < 1e-9 or norm_b < 1e-9:
        return 0.0
    return dot / (norm_a * norm_b)


def _ensure_memories_table(db_path: Path, embedder: BaseEmbedder) -> bool:
    """确保 memories 表存在。Schema: id, vector, text, node_id, sub_account_id, timestamp, memory_tier。
    memory_tier: short_term（碎片）| long_term（梦境融合后）。"""
    try:
        import lancedb
        db = lancedb.connect(str(db_path))
        if _TABLE_NAME not in db.table_names():
            sample_vec = [0.0] * embedder.dimension
            db.create_table(
                _TABLE_NAME,
                data=[{
                    "id": "init",
                    "vector": sample_vec,
                    "text": "",
                    "node_id": "",
                    "sub_account_id": "",
                    "timestamp": time.time(),
                    "memory_tier": "short_term",
                    "namespace": "default",
                    "reinforce_score": 0.0,
                }],
            )
            logger.info("[L2Memory] memories 表已创建于 %s", db_path)
        return True
    except ImportError:
        logger.warning("[L2Memory] lancedb 未安装")
        return False
    except Exception as e:
        logger.warning("[L2Memory] 表初始化失败: %s", e)
        return False


def init_l2_lancedb() -> bool:
    """
    L2 启动时调用，初始化 LanceDB 实例。
    创建或打开 memories 表。
    """
    emb = _get_embedder()
    if not emb:
        return False
    _LANCEDB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return _ensure_memories_table(_LANCEDB_PATH, emb)


def dream_optimize_semantic(
    entries: list[dict[str, Any]],
    embedder: BaseEmbedder,
    sim_threshold: float = 0.92,
    max_entries: int = 100,
) -> list[tuple[dict[str, Any], list[float]]]:
    """
    梦境消解：语义级去重。
    将 entries 转为 (entry, vector)，过滤掉与已保留项语义过于相似的条目。
    """
    kept: list[tuple[dict[str, Any], list[float]]] = []
    for e in entries[:max_entries * 2]:  # 多取一些，过滤后可能不足
        content = e.get("content", "") or str(e)
        if isinstance(content, dict):
            import json
            content = json.dumps(content, ensure_ascii=False)
        content = (content or "").strip()
        if not content:
            continue
        vec = _run_embed_sync(embedder, content)
        if not vec:
            continue
        # 与已保留项比较，若过于相似则跳过
        is_dup = False
        for _, kept_vec in kept:
            if _cosine_similarity(vec, kept_vec) >= sim_threshold:
                is_dup = True
                break
        if not is_dup:
            kept.append((e, vec))
        if len(kept) >= max_entries:
            break
    return kept


def sync_memories_to_lancedb(
    sub_account_id: str,
    node_id: str,
    entries: list[dict[str, Any]],
    namespace: str = "default",
) -> list[dict[str, Any]]:
    """
    将记忆同步到 LanceDB。
    先做语义梦境消解，再写入。按 (sub_account_id, node_id, namespace) 覆盖：先删除该节点该命名空间旧记忆，再插入。
    namespace 默认 default，用于细粒度记忆范围隔离。
    返回优化后的 entries（用于回传 optimized_memory）。
    """
    emb = _get_embedder()
    if not emb:
        logger.warning("[L2Memory] Embedder 不可用，跳过 LanceDB 写入")
        return entries[:100]  # 回退：简单截断

    optimized = dream_optimize_semantic(entries, emb)
    if not optimized:
        return []

    try:
        import lancedb
        _LANCEDB_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not _ensure_memories_table(_LANCEDB_PATH, emb):
            return [e for e, _ in optimized]

        db = lancedb.connect(str(_LANCEDB_PATH))
        tbl = db.open_table(_TABLE_NAME)

        # 删除该节点该命名空间旧记忆（SQL 字符串需转义单引号）
        sa = str(sub_account_id).replace("'", "''")
        nd = str(node_id).replace("'", "''")
        ns = str(namespace or "default").replace("'", "''")
        pred = f"(sub_account_id = '{sa}') AND (node_id = '{nd}') AND (namespace = '{ns}')"
        try:
            if hasattr(tbl, "delete"):
                tbl.delete(pred)
        except Exception as ex:
            # 兼容旧表无 namespace 列：回退为仅按 node 删除
            pred_fallback = f"(sub_account_id = '{sa}') AND (node_id = '{nd}')"
            try:
                if hasattr(tbl, "delete"):
                    tbl.delete(pred_fallback)
            except Exception as ex2:
                logger.debug("[L2Memory] delete 旧记忆: %s", ex2)

        # 插入新记忆
        rows = []
        for e, vec in optimized:
            content = e.get("content", "") or str(e)
            if isinstance(content, dict):
                import json
                content = json.dumps(content, ensure_ascii=False)
            mem_id = f"mem-{secrets.token_hex(8)}"
            rows.append({
                "id": mem_id,
                "vector": vec,
                "text": content,
                "node_id": node_id,
                "sub_account_id": sub_account_id,
                "timestamp": time.time(),
                "memory_tier": "short_term",
                "namespace": namespace or "default",
            })
        if rows:
            tbl.add(rows)
        return [e for e, _ in optimized]
    except Exception as e:
        logger.warning("[L2Memory] sync_memories_to_lancedb 失败: %s", e)
        return [e for e, _ in optimized]


def get_short_term_memories(
    sub_account_id: str,
    limit: int = 100,
    namespace: str | None = None,
) -> list[dict[str, Any]]:
    """
    获取指定子账号的 short_term 记忆碎片（用于梦境融合）。
    包含 memory_tier='short_term' 或 memory_tier 缺失（兼容旧数据）的条目。
    namespace 可选：若提供则仅返回该命名空间；否则返回全部（兼容旧逻辑）。
    """
    try:
        import lancedb
        db = lancedb.connect(str(_LANCEDB_PATH))
        if _TABLE_NAME not in db.table_names():
            return []
        tbl = db.open_table(_TABLE_NAME)
        df = tbl.to_pandas()
        if df.empty or "sub_account_id" not in df.columns:
            return []
        df = df[df["sub_account_id"] == sub_account_id].copy()
        df = df[df["id"] != "init"]
        if namespace and "namespace" in df.columns:
            df = df[(df["namespace"].isna()) | (df["namespace"] == "") | (df["namespace"] == namespace)]
        if "memory_tier" in df.columns:
            df = df[(df["memory_tier"].isna()) | (df["memory_tier"] == "") | (df["memory_tier"] == "short_term")]
        df = df.sort_values("timestamp", ascending=True).head(limit)
        out = []
        for _, r in df.iterrows():
            out.append({
                "id": str(r.get("id", "")),
                "text": str(r.get("text", "")),
                "vector": r.get("vector"),
                "timestamp": float(r.get("timestamp", 0)),
                "node_id": str(r.get("node_id", "")),
            })
        return out
    except Exception as e:
        logger.warning("[L2Memory] get_short_term_memories 失败: %s", e)
        return []


def insert_long_term_memory(
    sub_account_id: str,
    text: str,
    node_id: str = "",
    namespace: str = "default",
) -> bool:
    """将 LLM 融合后的长期记忆写入 LanceDB，memory_tier=long_term。"""
    emb = _get_embedder()
    if not emb or not (text or "").strip():
        return False
    vec = _run_embed_sync(emb, text.strip())
    if not vec:
        return False
    try:
        import lancedb
        _LANCEDB_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not _ensure_memories_table(_LANCEDB_PATH, emb):
            return False
        db = lancedb.connect(str(_LANCEDB_PATH))
        tbl = db.open_table(_TABLE_NAME)
        mem_id = f"mem-lt-{secrets.token_hex(8)}"
        row = {
            "id": mem_id,
            "vector": vec,
            "text": text.strip(),
            "node_id": node_id,
            "sub_account_id": sub_account_id,
            "timestamp": time.time(),
            "memory_tier": "long_term",
            "namespace": namespace or "default",
            "reinforce_score": 0.0,
        }
        try:
            tbl.add([row])
        except Exception:
            row.pop("reinforce_score", None)
            tbl.add([row])
        logger.info("[L2Memory] 长期记忆已写入: %s", mem_id[:20])
        return True
    except Exception as e:
        logger.warning("[L2Memory] insert_long_term_memory 失败: %s", e)
        return False


def delete_memories_by_ids(ids: list[str]) -> int:
    """按 id 列表物理删除记忆。返回成功删除条数。"""
    if not ids:
        return 0
    try:
        import lancedb
        db = lancedb.connect(str(_LANCEDB_PATH))
        if _TABLE_NAME not in db.table_names():
            return 0
        tbl = db.open_table(_TABLE_NAME)
        # 转义单引号，构建 IN 子句
        safe_ids = [str(i).replace("'", "''") for i in ids if i and str(i) != "init"]
        if not safe_ids:
            return 0
        in_clause = ", ".join(f"'{x}'" for x in safe_ids)
        pred = f"id IN ({in_clause})"
        tbl.delete(pred)
        return len(safe_ids)
    except Exception as e:
        logger.warning("[L2Memory] delete_memories_by_ids 失败: %s", e)
        return 0


def _content_token_jaccard(a: str, b: str) -> float:
    """用于 MMR 的轻量相似度（与 BM25 分词一致）。"""
    ta = set(_tokenize_for_bm25(a))
    tb = set(_tokenize_for_bm25(b))
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def _mmr_rerank_by_content(
    items: list[dict[str, Any]],
    *,
    limit: int,
    lambda_: float,
    score_key: str = "_hybrid_score",
) -> list[dict[str, Any]]:
    """按 MMR 从已按 score_key 降序的池中选出 limit 条，降低语义重复。"""
    lam = max(0.0, min(1.0, float(lambda_)))
    pool = list(items)
    selected: list[dict[str, Any]] = []
    while pool and len(selected) < limit:
        best_i = -1
        best_m = -1e9
        for i, c in enumerate(pool):
            rel = float(c.get(score_key, 0) or 0)
            content = str(c.get("content", "") or "")
            div = 0.0
            if selected:
                div = max(_content_token_jaccard(content, str(s.get("content", "") or "")) for s in selected)
            mmr = lam * rel - (1.0 - lam) * div
            if mmr > best_m:
                best_m = mmr
                best_i = i
        if best_i < 0:
            break
        selected.append(pool.pop(best_i))
    return selected


def _tokenize_for_bm25(text: str) -> list[str]:
    """
    为 BM25 分词：英文按空格/标点，中文按字符。
    兼容专有名词、技术术语的精确匹配。
    """
    import re
    if not text or not isinstance(text, str):
        return []
    # 提取连续字母数字、CJK 字符
    tokens = re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]|\w+", text)
    return [t.lower() for t in tokens if len(t) > 0]


def _memory_hit_row(
    c: dict[str, Any],
    *,
    explain: bool,
    vector_weight: float,
    text_weight: float,
    profile: str,
    used_bm25_in_base: bool,
) -> dict[str, Any]:
    """投影为 API 行；explain=True 时附带可解释分量（统一 memory_score 叙事）。"""
    row: dict[str, Any] = {
        "id": c["id"],
        "content": c["content"],
        "created_at": c["created_at"],
    }
    if not explain:
        return row
    vec = float(c.get("_vec_score", 0) or 0)
    bm25 = float(c.get("_bm25_score", 0) or 0)
    bonus = float(c.get("_reinforce_bonus", 0) or 0)
    tot = float(c.get("_hybrid_score", 0) or 0)
    if used_bm25_in_base:
        base = vector_weight * vec + text_weight * bm25
    else:
        base = vec
    row["explain"] = {
        "memory_scoring_profile": profile,
        "vec_score": round(vec, 6),
        "bm25_norm": round(bm25, 6),
        "vector_weight": vector_weight,
        "text_weight": text_weight if used_bm25_in_base else 0.0,
        "base_hybrid": round(base, 6),
        "reinforce_bonus": round(bonus, 6),
        "total_rank_score": round(tot, 6),
        "formula_ref": "docs/MEMORY_SCORING.md",
    }
    return row


def _hybrid_search(
    sub_account_id: str,
    query: str,
    node_id: str | None,
    limit: int,
    namespaces: list[str] | None,
    emb: BaseEmbedder,
    vector_weight: float = 0.7,
    text_weight: float = 0.3,
    candidate_multiplier: int = 4,
    *,
    explain: bool = False,
) -> list[dict[str, Any]]:
    """
    混合检索：向量相似度 × vector_weight + BM25 关键词分数 × text_weight。
    参考 OpenClaw：专有名词、技术术语由 BM25 捕获，语义由向量捕获。
    """
    import lancedb

    profile = "A_sum_cap"
    try:
        from core.db.memory_score import load_memory_scoring_config

        _msc = load_memory_scoring_config()
        vector_weight = float(_msc.get("vector_weight", vector_weight))
        text_weight = float(_msc.get("text_weight", text_weight))
        mmr_enabled = bool(_msc.get("mmr_enabled", True))
        mmr_lambda = float(_msc.get("mmr_lambda", 0.55))
        mmr_pool_mul = int(_msc.get("mmr_pool_multiplier", 3) or 3)
        profile = str(_msc.get("profile", "A_sum_cap") or "A_sum_cap")
    except Exception:
        mmr_enabled, mmr_lambda, mmr_pool_mul = True, 0.55, 3

    vec = _run_embed_sync(emb, query)
    if not vec:
        return []

    db = lancedb.connect(str(_LANCEDB_PATH))
    if _TABLE_NAME not in db.table_names():
        return []
    tbl = db.open_table(_TABLE_NAME)

    # 取更多候选用于 BM25 重排
    raw = tbl.search(vec).limit(limit * candidate_multiplier).to_list()
    ns_set = set(namespaces) if namespaces is not None else None
    p2_on, p2_w, p2_mx = _p2_reinforce_params()

    # 过滤 + 收集向量距离（LanceDB 返回 _distance，越小越相似）
    candidates: list[dict[str, Any]] = []
    for r in raw:
        if r.get("sub_account_id") != sub_account_id:
            continue
        if node_id and r.get("node_id") != node_id:
            continue
        if ns_set is not None:
            r_ns = r.get("namespace") or "default"
            if r_ns not in ns_set:
                continue
        if str(r.get("id", "")) == "init":
            continue
        dist = float(r.get("_distance", 1.0))
        # 余弦距离 -> 相似度：1 - dist（LanceDB 余弦距离 0=相同，2=相反）
        vec_score = max(0.0, 1.0 - dist / 2.0) if dist <= 2.0 else 0.0
        row_rf = r.get("reinforce_score")
        candidates.append({
            "id": r.get("id", ""),
            "content": r.get("text", ""),
            "created_at": r.get("timestamp", 0),
            "_vec_score": vec_score,
            "_row_reinforce": row_rf,
        })

    if not candidates:
        return []

    def _finalize_hybrid(cands: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cands.sort(key=lambda x: -x.get("_hybrid_score", 0))
        if mmr_enabled and len(cands) > limit:
            take = min(len(cands), max(limit * mmr_pool_mul, limit * 2))
            pool = cands[:take]
            return _mmr_rerank_by_content(
                pool, limit=limit, lambda_=mmr_lambda, score_key="_hybrid_score",
            )
        return cands[:limit]

    # BM25 重排
    try:
        from rank_bm25 import BM25Okapi
        corpus_tokens = [_tokenize_for_bm25(c["content"]) for c in candidates]
        query_tokens = _tokenize_for_bm25(query)
        if not query_tokens or not any(ct for ct in corpus_tokens):
            # 无有效 token 时仅用向量 + P2 强化
            try:
                from core.db.memory_reinforcement import hybrid_reinforce_bonus

                for c in candidates:
                    b = (
                        hybrid_reinforce_bonus(
                            str(c["id"]),
                            c.get("_row_reinforce"),
                            weight=p2_w,
                            max_boost=p2_mx,
                        )
                        if p2_on
                        else 0.0
                    )
                    c["_reinforce_bonus"] = b
                    c["_hybrid_score"] = c.get("_vec_score", 0) + b
            except ImportError:
                for c in candidates:
                    c["_reinforce_bonus"] = 0.0
                    c["_hybrid_score"] = c.get("_vec_score", 0)
            final = _finalize_hybrid(candidates)
            return [
                _memory_hit_row(
                    c, explain=explain, vector_weight=vector_weight, text_weight=text_weight,
                    profile=profile, used_bm25_in_base=False,
                )
                for c in final
            ]

        bm25 = BM25Okapi(corpus_tokens)
        bm25_scores = bm25.get_scores(query_tokens)
        max_bm = max(bm25_scores) if bm25_scores.size > 0 else 0.0
        for i, c in enumerate(candidates):
            raw_bm = float(bm25_scores[i]) if i < len(bm25_scores) else 0.0
            c["_bm25_score"] = raw_bm / max_bm if max_bm > 0 else 0.0

        # 加权融合 + P2-9 检索强化
        try:
            from core.db.memory_reinforcement import hybrid_reinforce_bonus
        except ImportError:
            hybrid_reinforce_bonus = None  # type: ignore[misc, assignment]
        for c in candidates:
            base = (
                vector_weight * c.get("_vec_score", 0) +
                text_weight * c.get("_bm25_score", 0)
            )
            bonus = 0.0
            if p2_on and hybrid_reinforce_bonus is not None:
                bonus = hybrid_reinforce_bonus(
                    str(c["id"]),
                    c.get("_row_reinforce"),
                    weight=p2_w,
                    max_boost=p2_mx,
                )
            c["_reinforce_bonus"] = bonus
            c["_hybrid_score"] = base + bonus
        final = _finalize_hybrid(candidates)
    except ImportError:
        # rank_bm25 未安装时回退为纯向量
        try:
            from core.db.memory_reinforcement import hybrid_reinforce_bonus

            for c in candidates:
                b = (
                    hybrid_reinforce_bonus(
                        str(c["id"]),
                        c.get("_row_reinforce"),
                        weight=p2_w,
                        max_boost=p2_mx,
                    )
                    if p2_on
                    else 0.0
                )
                c["_reinforce_bonus"] = b
                c["_hybrid_score"] = c.get("_vec_score", 0) + b
        except ImportError:
            for c in candidates:
                c["_reinforce_bonus"] = 0.0
                c["_hybrid_score"] = c.get("_vec_score", 0)
        final = _finalize_hybrid(candidates)

    return [
        _memory_hit_row(
            c, explain=explain, vector_weight=vector_weight, text_weight=text_weight,
            profile=profile, used_bm25_in_base=True,
        )
        for c in final
    ]


def search_memories_vector(
    sub_account_id: str,
    query: str,
    node_id: str | None,
    limit: int = 10,
    namespaces: list[str] | None = None,
    hybrid: bool = True,
    *,
    explain: bool = False,
) -> list[dict[str, Any]]:
    """
    记忆检索。hybrid=True 时使用混合检索（向量 70% + BM25 30%），
    对专有名词、技术术语更友好；hybrid=False 时仅向量检索。
    explain=True 时每条结果含 explain 可解释分（见 MEMORY_SCORING.md）。
    """
    emb = _get_embedder()
    if not emb:
        return []

    try:
        import lancedb
        db = lancedb.connect(str(_LANCEDB_PATH))
        if _TABLE_NAME not in db.table_names():
            return []
    except Exception as e:
        logger.warning("[L2Memory] search 连接失败: %s", e)
        return []

    if hybrid:
        return _hybrid_search(
            sub_account_id, query, node_id, limit, namespaces, emb,
            vector_weight=0.7, text_weight=0.3, candidate_multiplier=4,
            explain=explain,
        )

    # 纯向量模式（兼容旧逻辑）+ P2-9 强化重排
    profile = "A_sum_cap"
    vw, tw = 1.0, 0.0
    try:
        from core.db.memory_score import load_memory_scoring_config

        _cf = load_memory_scoring_config()
        profile = str(_cf.get("profile", "A_sum_cap") or "A_sum_cap")
        vw = float(_cf.get("vector_weight", 1.0))
        tw = float(_cf.get("text_weight", 0.0))
    except Exception:
        pass

    vec = _run_embed_sync(emb, query)
    if not vec:
        return []
    p2_on, p2_w, p2_mx = _p2_reinforce_params()
    try:
        from core.db.memory_reinforcement import hybrid_reinforce_bonus
    except ImportError:
        hybrid_reinforce_bonus = None  # type: ignore[misc, assignment]
    try:
        tbl = db.open_table(_TABLE_NAME)
        raw = tbl.search(vec).limit(limit * 8).to_list()
        ns_set = set(namespaces) if namespaces is not None else None
        scored: list[tuple[float, dict[str, Any]]] = []
        for r in raw:
            if r.get("sub_account_id") != sub_account_id:
                continue
            if node_id and r.get("node_id") != node_id:
                continue
            if ns_set is not None:
                r_ns = r.get("namespace") or "default"
                if r_ns not in ns_set:
                    continue
            if str(r.get("id", "")) == "init":
                continue
            dist = float(r.get("_distance", 1.0))
            vec_score = max(0.0, 1.0 - dist / 2.0) if dist <= 2.0 else 0.0
            mid = str(r.get("id", ""))
            bonus = 0.0
            if p2_on and hybrid_reinforce_bonus is not None:
                bonus = hybrid_reinforce_bonus(mid, r.get("reinforce_score"), weight=p2_w, max_boost=p2_mx)
            tot = vec_score + bonus
            scored.append(
                (
                    tot,
                    {
                        "id": mid,
                        "content": r.get("text", ""),
                        "created_at": r.get("timestamp", 0),
                        "_vec_score": vec_score,
                        "_bm25_score": 0.0,
                        "_reinforce_bonus": bonus,
                        "_hybrid_score": tot,
                    },
                )
            )
        scored.sort(key=lambda x: -x[0])
        return [
            _memory_hit_row(
                x[1],
                explain=explain,
                vector_weight=vw,
                text_weight=tw,
                profile=profile,
                used_bm25_in_base=False,
            )
            for x in scored[:limit]
        ]
    except Exception as e:
        logger.warning("[L2Memory] search_memories_vector 失败: %s", e)
        return []
