"""
LanceDBStore - 本地嵌入式向量库实现

战役一：地基与神经元
实现 VectorStoreProtocol，连接本地 data/lancedb 目录，
支持 MemoryChunk 的 upsert 与 metadata 精准过滤检索。
"""

import logging
from pathlib import Path
from typing import Any

from core.config import settings
from core.memory.chunk_schema import MemoryChunk
from core.memory.store_protocol import VectorStoreProtocol

logger = logging.getLogger(__name__)

try:
    import lancedb
    LANCEDB_AVAILABLE = True
except ImportError:
    LANCEDB_AVAILABLE = False
    lancedb = None  # type: ignore

TABLE_NAME = "jachin_long_term_memory"
DEFAULT_VECTOR_DIM = 1536  # text-embedding-3-small


def _build_where_clause(filter_dict: dict[str, Any] | None) -> str | None:
    """
    将 filter_dict 转为 LanceDB where 子句。

    仅支持等值过滤，避免 SQL 注入。
    """
    if not filter_dict:
        return None
    parts = []
    for k, v in filter_dict.items():
        if v is None:
            parts.append(f"({k} IS NULL)")
        elif isinstance(v, bool):
            parts.append(f"({k} IS {'TRUE' if v else 'FALSE'})")
        elif isinstance(v, (int, float)):
            parts.append(f"({k} = {v})")
        else:
            # 字符串：转义单引号
            safe = str(v).replace("'", "''")
            parts.append(f"({k} = '{safe}')")
    return " AND ".join(parts) if parts else None


class LanceDBStore:
    """
    LanceDB 向量存储实现

    连接本地目录（默认 data/lancedb），表名 jachin_long_term_memory。
    支持 user_id、device_id、character_id、is_core 等元数据精准过滤。
    """

    def __init__(self, path: str | Path | None = None):
        """
        Args:
            path: LanceDB 数据目录，默认从 settings.LANCEDB_PATH 读取
        """
        if not LANCEDB_AVAILABLE:
            raise ImportError(
                "lancedb 未安装。请执行: pip install lancedb"
            )
        self.path = Path(path or settings.LANCEDB_PATH)
        self.path.mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(str(self.path))
        self._table = self._ensure_table()

    def _ensure_table(self):
        """创建或打开表"""
        if TABLE_NAME in self._db.table_names():
            return self._db.open_table(TABLE_NAME)
        # 创建空表：使用 list[dict] 推断 schema（LanceDB 支持）
        dummy = MemoryChunk(
            id="__schema_init__",
            content="",
            vector=[0.0] * DEFAULT_VECTOR_DIM,
            user_id="",
            device_id="",
            character_id="",
            is_core=False,
            timestamp=0,
        ).to_row_dict()
        tbl = self._db.create_table(TABLE_NAME, data=[dummy], mode="overwrite")
        logger.info("LanceDB 表 %s 已创建", TABLE_NAME)
        return tbl

    def upsert(self, chunks: list[MemoryChunk]) -> None:
        """
        插入或更新记忆碎片。

        LanceDB 无原生 upsert，采用 add 追加。若需更新，可先按 id 删除再插入
        （战役三实现时优化）。
        """
        if not chunks:
            return
        rows = [c.to_row_dict() for c in chunks]
        # 过滤掉 schema 占位行（若存在）
        rows = [r for r in rows if r.get("id") != "__schema_init__"]
        if not rows:
            return
        self._table.add(rows)
        logger.debug("LanceDB upsert %d 条", len(rows))

    def search(
        self,
        query_vector: list[float],
        limit: int,
        filter_dict: dict[str, Any] | None = None,
    ) -> list[MemoryChunk]:
        """
        语义检索，支持元数据精准过滤。
        """
        q = self._table.search(query_vector).limit(limit)
        # 排除 schema 占位行
        base_where = "(id != '__schema_init__')"
        extra = _build_where_clause(filter_dict)
        where = f"{base_where} AND ({extra})" if extra else base_where
        q = q.where(where)
        results = q.to_list()
        chunks = []
        for r in results:
            try:
                # LanceDB 返回的 _distance 为相似度，可忽略
                row = {k: v for k, v in r.items() if k != "_distance"}
                chunks.append(MemoryChunk.from_row(row))
            except Exception as e:
                logger.warning("解析检索结果失败: %s", e)
        return chunks
