"""
Jachin Nexus V2 - L2 向量梦境引擎 (LanceDB)

L2 启动时初始化 LanceDB，默认 ~/.jachin/lancedb_data。
K8s 部署：设置 JACHIN_LANCEDB_PATH 或 JACHIN_DATA_DIR 指向共享卷（如 NFS），
确保多 Pod 横向扩展时记忆数据一致。
memories 表 Schema: id, vector, text, node_id, sub_account_id, timestamp, namespace。
namespace: 记忆范围/命名空间，默认 default，用于细粒度权限隔离（如客服知识库、部门共享记忆）。
语义级记忆检索与梦境消解（向量相似度去重）。
"""
from __future__ import annotations

import asyncio
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
        tbl.add([{
            "id": mem_id,
            "vector": vec,
            "text": text.strip(),
            "node_id": node_id,
            "sub_account_id": sub_account_id,
            "timestamp": time.time(),
            "memory_tier": "long_term",
            "namespace": namespace or "default",
        }])
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


def search_memories_vector(
    sub_account_id: str,
    query: str,
    node_id: str | None,
    limit: int = 10,
    namespaces: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    向量相似度检索。
    将 query 转为向量，在 LanceDB 中搜索 sub_account_id 下最相关的 Top-K 条记忆。
    node_id 可选：若提供则仅搜该节点。
    namespaces 可选：若提供则仅在允许的命名空间内检索；空列表=无结果；None=不按 namespace 过滤（兼容旧逻辑）。
    """
    emb = _get_embedder()
    if not emb:
        return []

    vec = _run_embed_sync(emb, query)
    if not vec:
        return []

    try:
        import lancedb
        db = lancedb.connect(str(_LANCEDB_PATH))
        if _TABLE_NAME not in db.table_names():
            return []
        tbl = db.open_table(_TABLE_NAME)

        # LanceDB search 返回全表相似度排序，需在内存中按 sub_account_id、node_id 过滤
        # 先取更多结果，再过滤
        raw = tbl.search(vec).limit(limit * 5).to_list()
        ns_set = set(namespaces) if namespaces is not None else None
        results = []
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
            results.append({
                "id": r.get("id", ""),
                "content": r.get("text", ""),
                "created_at": r.get("timestamp", 0),
            })
            if len(results) >= limit:
                break
        return results
    except Exception as e:
        logger.warning("[L2Memory] search_memories_vector 失败: %s", e)
        return []
