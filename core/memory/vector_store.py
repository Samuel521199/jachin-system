"""
VectorStore - 基于 Qdrant 的向量存储类

实现长期记忆的向量存储和检索功能。
"""

import logging
import time
from typing import List, Dict, Optional, Any
from uuid import uuid4
import threading

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        Distance,
        VectorParams,
        PointStruct,
        Filter,
        FieldCondition,
        MatchValue,
    )
    QDRANT_AVAILABLE = True
except ImportError:
    QDRANT_AVAILABLE = False
    logging.warning("qdrant-client not installed. VectorStore will not work.")

from core.config import settings

logger = logging.getLogger(__name__)


class VectorStore:
    """
    基于 Qdrant 的向量存储单例类
    
    用于存储和检索长期记忆（Embeddings）。
    """
    
    _instance: Optional["VectorStore"] = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """单例模式实现"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """初始化向量存储"""
        if self._initialized:
            return
        
        if not QDRANT_AVAILABLE:
            raise ImportError(
                "qdrant-client package is required for VectorStore. "
                "Install it with: pip install qdrant-client"
            )
        
        # 从配置读取 Qdrant 连接信息
        # 将 localhost 替换为 127.0.0.1，避免 Windows IPv6 解析导致 503
        base_url = settings.QDRANT_URL
        if "localhost" in base_url:
            base_url = base_url.replace("localhost", "127.0.0.1")
        self.qdrant_url = base_url
        grpc_url = getattr(settings, "QDRANT_GRPC_URL", None)
        if grpc_url and "localhost" in grpc_url:
            grpc_url = grpc_url.replace("localhost", "127.0.0.1")
        self.qdrant_grpc_url = grpc_url

        # 初始化 Qdrant 客户端（带重试，应对 Qdrant 启动阶段返回 503）
        self.client = None
        max_retries = 5
        retry_delay = 3
        client_kwargs = {
            "timeout": 5,
            "check_compatibility": False,  # 跳过版本检查，避免部分 Qdrant 版本对根接口返回 503
        }
        for attempt in range(1, max_retries + 1):
            try:
                if self.qdrant_grpc_url:
                    self.client = QdrantClient(
                        url=self.qdrant_url,
                        grpc_port=int(self.qdrant_grpc_url.split(":")[-1]) if ":" in self.qdrant_grpc_url else None,
                        **client_kwargs,
                    )
                else:
                    self.client = QdrantClient(url=self.qdrant_url, **client_kwargs)
                self.client.get_collections()
                logger.info(f"VectorStore initialized with Qdrant at {self.qdrant_url}")
                break
            except Exception as e:
                if attempt < max_retries:
                    logger.info(
                        f"Qdrant connection attempt {attempt}/{max_retries} failed: {e}, "
                        f"retrying in {retry_delay}s..."
                    )
                    time.sleep(retry_delay)
                else:
                    logger.warning(f"Failed to connect to Qdrant at {self.qdrant_url}: {e}")
                    logger.warning("VectorStore will run in degraded mode (memory-only)")
                    self.client = None
        
        # 默认集合名称
        self.default_collection = "jachin_memories"
        
        # 默认向量维度（Qwen embedding 维度为 1536）
        self.default_vector_size = 1536
        
        # 确保默认集合存在（如果 Qdrant 可用）
        try:
            self._ensure_collection(self.default_collection)
        except Exception as e:
            logger.warning(f"Failed to ensure collection {self.default_collection}: {e}")
            logger.warning("VectorStore will continue with limited functionality")
        
        self._initialized = True
    
    def _check_client(self):
        """检查客户端是否可用"""
        if not self.client:
            raise RuntimeError("Qdrant client is not available. Please ensure Qdrant service is running.")
    
    def _ensure_collection(self, collection_name: str, vector_size: Optional[int] = None):
        """
        确保集合存在，如果不存在则创建
        
        Args:
            collection_name: 集合名称
            vector_size: 向量维度（如果为 None，使用默认值）
        """
        if not self.client:
            raise RuntimeError("Qdrant client is not initialized")
        
        vector_size = vector_size or self.default_vector_size
        
        if not self.client:
            raise RuntimeError("Qdrant client is not initialized")
        
        try:
            # 检查集合是否存在
            collections = self.client.get_collections().collections
            collection_exists = any(
                col.name == collection_name for col in collections
            )
            
            if not collection_exists:
                # 创建新集合
                self.client.create_collection(
                    collection_name=collection_name,
                    vectors_config=VectorParams(
                        size=vector_size,
                        distance=Distance.COSINE,
                    ),
                )
                logger.info(f"Created Qdrant collection: {collection_name}")
            else:
                logger.debug(f"Collection {collection_name} already exists")
        
        except Exception as e:
            logger.error(f"Failed to ensure collection {collection_name}: {e}")
            raise
    
    def _check_client(self):
        """检查客户端是否可用"""
        if not self.client:
            raise RuntimeError("Qdrant client is not available. Please ensure Qdrant service is running.")
    
    async def upsert(
        self,
        text: str,
        embedding: List[float],
        metadata: Optional[Dict[str, Any]] = None,
        point_id: Optional[str] = None,
        collection_name: Optional[str] = None,
    ) -> str:
        """
        添加或更新记忆（向量）
        
        Args:
            text: 文本内容
            embedding: 文本的嵌入向量
            metadata: 元数据（可选），可包含 user_id, timestamp, category 等
            point_id: 点的唯一 ID（如果为 None，自动生成 UUID）
            collection_name: 集合名称（如果为 None，使用默认集合）
        
        Returns:
            点的唯一 ID
        """
        self._check_client()
        collection_name = collection_name or self.default_collection
        
        # 确保集合存在
        self._ensure_collection(collection_name, vector_size=len(embedding))
        
        # 生成点 ID
        if point_id is None:
            point_id = str(uuid4())
        
        # 准备元数据
        payload = {
            "text": text,
            **(metadata or {}),
        }
        
        # 创建点结构
        point = PointStruct(
            id=point_id,
            vector=embedding,
            payload=payload,
        )
        
        try:
            # 执行 upsert
            self.client.upsert(
                collection_name=collection_name,
                points=[point],
            )
            logger.debug(f"Upserted point {point_id} to collection {collection_name}")
            return point_id
        
        except Exception as e:
            logger.error(f"Failed to upsert point: {e}")
            raise
    
    async def search(
        self,
        query_embedding: List[float],
        limit: int = 10,
        score_threshold: Optional[float] = None,
        filter_conditions: Optional[Dict[str, Any]] = None,
        collection_name: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        搜索相似记忆
        
        Args:
            query_embedding: 查询向量
            limit: 返回结果数量上限
            score_threshold: 相似度阈值（0-1），低于此值的结果将被过滤
            filter_conditions: 过滤条件（可选），例如 {"user_id": "user123"}
            collection_name: 集合名称（如果为 None，使用默认集合）
        
        Returns:
            搜索结果列表，每个结果包含 id, score, text, metadata
        """
        self._check_client()
        collection_name = collection_name or self.default_collection
        
        # 构建过滤条件
        filter_obj = None
        if filter_conditions:
            conditions = []
            for key, value in filter_conditions.items():
                conditions.append(
                    FieldCondition(
                        key=key,
                        match=MatchValue(value=value),
                    )
                )
            if conditions:
                filter_obj = Filter(must=conditions)
        
        try:
            # 执行搜索
            search_results = self.client.search(
                collection_name=collection_name,
                query_vector=query_embedding,
                limit=limit,
                score_threshold=score_threshold,
                query_filter=filter_obj,
            )
            
            # 格式化结果
            results = []
            for result in search_results:
                results.append({
                    "id": result.id,
                    "score": result.score,
                    "text": result.payload.get("text", ""),
                    "metadata": {
                        k: v for k, v in result.payload.items() if k != "text"
                    },
                })
            
            logger.debug(
                f"Found {len(results)} results in collection {collection_name}"
            )
            return results
        
        except Exception as e:
            logger.error(f"Failed to search: {e}")
            raise
    
    async def delete(
        self,
        point_id: str,
        collection_name: Optional[str] = None,
    ) -> bool:
        """
        删除指定的记忆点
        
        Args:
            point_id: 点的唯一 ID
            collection_name: 集合名称（如果为 None，使用默认集合）
        
        Returns:
            是否删除成功
        """
        self._check_client()
        collection_name = collection_name or self.default_collection
        
        try:
            self.client.delete(
                collection_name=collection_name,
                points_selector=[point_id],
            )
            logger.debug(f"Deleted point {point_id} from collection {collection_name}")
            return True
        
        except Exception as e:
            logger.error(f"Failed to delete point {point_id}: {e}")
            return False
    
    async def get_by_id(
        self,
        point_id: str,
        collection_name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        根据 ID 获取记忆点
        
        Args:
            point_id: 点的唯一 ID
            collection_name: 集合名称（如果为 None，使用默认集合）
        
        Returns:
            记忆点信息，如果不存在则返回 None
        """
        self._check_client()
        collection_name = collection_name or self.default_collection
        
        try:
            points = self.client.retrieve(
                collection_name=collection_name,
                ids=[point_id],
            )
            
            if not points:
                return None
            
            point = points[0]
            return {
                "id": point.id,
                "vector": point.vector,
                "text": point.payload.get("text", ""),
                "metadata": {
                    k: v for k, v in point.payload.items() if k != "text"
                },
            }
        
        except Exception as e:
            logger.error(f"Failed to get point {point_id}: {e}")
            return None
    
    def health_check(self) -> bool:
        """
        健康检查
        
        Returns:
            Qdrant 服务是否可用
        """
        if not self.client:
            return False
        try:
            # 尝试获取集合列表
            self.client.get_collections()
            return True
        except Exception as e:
            logger.error(f"Qdrant health check failed: {e}")
            return False
    
    def get_collection_info(self, collection_name: Optional[str] = None) -> Dict[str, Any]:
        """
        获取集合信息
        
        Args:
            collection_name: 集合名称（如果为 None，使用默认集合）
        
        Returns:
            集合信息字典
        """
        self._check_client()
        collection_name = collection_name or self.default_collection
        
        try:
            info = self.client.get_collection(collection_name)
            return {
                "name": info.name,
                "points_count": info.points_count,
                "vectors_count": info.vectors_count,
                "config": {
                    "vector_size": info.config.params.vectors.size,
                    "distance": info.config.params.vectors.distance.name,
                },
            }
        except Exception as e:
            logger.error(f"Failed to get collection info: {e}")
            raise


# 全局单例实例
# 延迟初始化，避免在导入时失败
try:
    vector_store = VectorStore()
except Exception as e:
    logger.warning(f"Failed to initialize VectorStore: {e}")
    logger.warning("VectorStore will be None. Some features may not work.")
    vector_store = None
