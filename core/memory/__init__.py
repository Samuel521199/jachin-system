"""
Memory module - 记忆管理模块

包含向量存储、记忆检索等功能。
战役一：地基与神经元 - 新增 MemoryChunk、Embedding、LanceDBStore。
"""

from .vector_store import VectorStore, vector_store
from .chunk_schema import MemoryChunk
from .store_protocol import VectorStoreProtocol
from .embedding import BaseEmbedder, OpenAIEmbedder
from .manager import MemoryManager

# LanceDBStore 延迟导入（依赖 lancedb）
def get_lancedb_store():
    """获取 LanceDBStore 实例（延迟初始化）"""
    from .lancedb_store import LanceDBStore
    return LanceDBStore()

__all__ = [
    "VectorStore",
    "vector_store",
    "MemoryChunk",
    "VectorStoreProtocol",
    "BaseEmbedder",
    "OpenAIEmbedder",
    "MemoryManager",
    "get_lancedb_store",
]
