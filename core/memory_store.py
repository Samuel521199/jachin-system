"""
Jachin Nexus v8.0 - 向量记忆存储 (LanceDB Memories)

Dream Weaver 记忆自愈的数据层：支持 is_consolidated 标记，
供梦境引擎聚类、去重、融合后写入高密度核心认知。
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from core.embedding import BaseEmbedder, get_embedder

logger = logging.getLogger(__name__)

_MEMORIES_DB_PATH = Path.home() / ".jachin" / "vector_db"
_MEMORIES_TABLE = "memories"


def _get_embedder() -> BaseEmbedder | None:
    """获取 Embedder，失败时返回 None"""
    try:
        return get_embedder()
    except Exception as e:
        logger.warning("[MemoryStore] Embedder 初始化失败: %s", e)
        return None


def _ensure_memories_table(db_path: Path, embedder: BaseEmbedder) -> bool:
    """确保 memories 表存在且 schema 正确（含 is_consolidated 字段）"""
    try:
        import lancedb
        db = lancedb.connect(str(db_path))
        if _MEMORIES_TABLE not in db.table_names():
            sample_vec = [0.0] * embedder.dimension
            db.create_table(
                _MEMORIES_TABLE,
                data=[{
                    "id": "init",
                    "text": "",
                    "vector": sample_vec,
                    "is_consolidated": True,
                    "created_at": time.time(),
                }],
            )
            logger.info("[MemoryStore] memories 表已创建 (含 is_consolidated)")
        return True
    except ImportError:
        logger.warning("[MemoryStore] lancedb 未安装")
        return False
    except Exception as e:
        logger.warning("[MemoryStore] 表初始化失败: %s", e)
        return False


def get_unconsolidated_memories(limit: int = 50) -> list[dict[str, Any]]:
    """
    获取待处理的记忆碎片（is_consolidated == False）。

    Returns:
        [{"id": str, "text": str, "created_at": float}, ...]
    """
    try:
        import lancedb
        db = lancedb.connect(str(_MEMORIES_DB_PATH))
        if _MEMORIES_TABLE not in db.table_names():
            return []
        tbl = db.open_table(_MEMORIES_TABLE)
        df = tbl.to_pandas()
        if df.empty or "is_consolidated" not in df.columns:
            return []
        df = df[df["is_consolidated"] == False].sort_values("created_at", ascending=True).head(limit)
        return [
            {"id": str(r["id"]), "text": str(r["text"]), "created_at": float(r.get("created_at", 0))}
            for _, r in df.iterrows()
            if str(r.get("id", "")) != "init"
        ]
    except ImportError:
        return []
    except Exception as e:
        logger.warning("[MemoryStore] get_unconsolidated_memories 失败: %s", e)
        return []


def delete_memories(ids: list[str]) -> None:
    """删除指定 ID 的记忆（梦境重塑后清理旧碎片）"""
    if not ids:
        return
    try:
        import lancedb
        db = lancedb.connect(str(_MEMORIES_DB_PATH))
        if _MEMORIES_TABLE not in db.table_names():
            return
        tbl = db.open_table(_MEMORIES_TABLE)
        # LanceDB delete(predicate): 多 id 用 OR 连接
        pred_parts = [f"(id = '{str(i).replace(chr(39), chr(39)+chr(39))}')" for i in ids[:100]]
        if pred_parts and hasattr(tbl, "delete"):
            predicate = " OR ".join(pred_parts)
            tbl.delete(predicate)
            logger.debug("[MemoryStore] 已删除 %d 条记忆", len(ids))
    except ImportError:
        pass
    except Exception as e:
        logger.warning("[MemoryStore] delete_memories 失败: %s", e)


def insert_consolidated_memory(text: str) -> str | None:
    """
    写入一条已整合的高密度记忆（is_consolidated=True）。
    梦境重塑后调用。同时写入 biological_memory.core_memory 供 Agent Prompt 使用。
    """
    mem_id = add_memory_fragment(text, is_consolidated=True)
    if mem_id:
        try:
            from core.biological_memory import add_core_memory
            add_core_memory(tag="dream_weaver", content=text, source_summary="梦境重塑")
        except Exception:
            pass
    return mem_id


def _run_embed_sync(embedder: "BaseEmbedder", text: str) -> list[float] | None:
    """同步运行 embed_text，兼容已有事件循环（agent_loop 等异步上下文）"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(embedder.embed_text(text))
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, embedder.embed_text(text))
        return future.result()


def add_memory_fragment(text: str, *, is_consolidated: bool = False) -> str | None:
    """
    写入一条记忆碎片（默认未整合）。
    供 add_short_term 等调用，实现双写至 LanceDB。

    Returns:
        记忆 ID，失败返回 None
    """
    text = (text or "").strip()
    if not text:
        return None
    emb = _get_embedder()
    if not emb:
        return None
    try:
        vec = _run_embed_sync(emb, text)
        if not vec:
            return None
        _MEMORIES_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not _ensure_memories_table(_MEMORIES_DB_PATH, emb):
            return None
        import lancedb
        db = lancedb.connect(str(_MEMORIES_DB_PATH))
        tbl = db.open_table(_MEMORIES_TABLE)
        mem_id = str(uuid.uuid4())
        tbl.add([{
            "id": mem_id,
            "text": text,
            "vector": vec,
            "is_consolidated": is_consolidated,
            "created_at": time.time(),
        }])
        return mem_id
    except Exception as e:
        logger.debug("[MemoryStore] add_memory_fragment 失败: %s", e)
        return None
