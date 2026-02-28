"""
向量库抽象协议 - VectorStoreProtocol

战役一：地基与神经元
定义向量存储的抽象接口，与 Embedding 解耦。
VectorStore 只负责存取向量，不负责调用 Embedding API。
"""

from typing import Protocol, runtime_checkable

from core.memory.chunk_schema import MemoryChunk


@runtime_checkable
class VectorStoreProtocol(Protocol):
    """
    向量存储协议

    所有向量库实现（LanceDB、Qdrant 等）必须实现此接口。
    """

    def upsert(self, chunks: list[MemoryChunk]) -> None:
        """
        插入或更新记忆碎片

        Args:
            chunks: 记忆碎片列表，每个包含 id, content, vector, 元数据
        """
        ...

    def search(
        self,
        query_vector: list[float],
        limit: int,
        filter_dict: dict | None = None,
    ) -> list[MemoryChunk]:
        """
        语义检索

        Args:
            query_vector: 查询向量
            limit: 返回数量上限
            filter_dict: 元数据过滤，如 {"user_id": "xxx", "device_id": "yyy"}

        Returns:
            匹配的记忆碎片列表，按相似度排序
        """
        ...
