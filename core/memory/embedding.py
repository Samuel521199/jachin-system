"""
Embedding 引擎 - 文本向量化接口

战役一：地基与神经元
VectorStore 只负责存取向量，不负责调用 Embedding API。
本模块提供统一的 Embedding 抽象与实现。
"""

from abc import ABC, abstractmethod
from typing import List

import logging

logger = logging.getLogger(__name__)


class BaseEmbedder(ABC):
    """
    文本向量化抽象基类

    所有 Embedding 实现必须继承此类，保证接口一致。
    """

    @abstractmethod
    def embed_text(self, text: str) -> List[float]:
        """
        单条文本向量化

        Args:
            text: 输入文本

        Returns:
            向量列表，维度由具体模型决定
        """
        pass

    @abstractmethod
    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        批量文本向量化

        Args:
            texts: 输入文本列表

        Returns:
            向量列表的列表，与输入一一对应
        """
        pass

    @property
    def dimension(self) -> int:
        """向量维度"""
        raise NotImplementedError


class OpenAIEmbedder(BaseEmbedder):
    """
    OpenAI Embedding 实现

    调用 text-embedding-3-small 或 text-embedding-ada-002。
    需配置 OPENAI_API_KEY 或通过 base_url 使用兼容接口。
    """

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        """
        Args:
            model: 模型名称，如 text-embedding-3-small, text-embedding-ada-002
            api_key: API Key，默认从环境变量 OPENAI_API_KEY 读取
            base_url: 兼容接口基地址（如 DashScope compatible-mode）
        """
        self.model = model
        self._api_key = api_key
        self._base_url = base_url
        self._client = None
        self._dimension: int | None = None

    def _get_client(self):
        """延迟初始化 OpenAI 客户端"""
        if self._client is None:
            try:
                from openai import OpenAI
                kwargs = {}
                if self._api_key:
                    kwargs["api_key"] = self._api_key
                if self._base_url:
                    kwargs["base_url"] = self._base_url
                self._client = OpenAI(**kwargs)
            except ImportError as e:
                raise ImportError(
                    "openai 包未安装。请执行: pip install openai"
                ) from e
        return self._client

    def embed_text(self, text: str) -> List[float]:
        """单条文本向量化"""
        vectors = self.embed_batch([text])
        return vectors[0] if vectors else []

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """批量文本向量化"""
        if not texts:
            return []

        client = self._get_client()
        try:
            resp = client.embeddings.create(
                model=self.model,
                input=texts,
            )
            # 按输入顺序排列（API 可能乱序返回）
            by_idx = {e.index: e.embedding for e in resp.data}
            return [by_idx[i] for i in range(len(texts))]
        except Exception as e:
            logger.error("OpenAI Embedding 调用失败: %s", e)
            raise

    @property
    def dimension(self) -> int:
        """向量维度（text-embedding-3-small 默认 1536）"""
        if self._dimension is None:
            # 用空字符串探测一次
            v = self.embed_text("")
            self._dimension = len(v)
        return self._dimension
