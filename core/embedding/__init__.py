"""
Jachin Nexus v8.0 - 可插拔向量引擎 (Pluggable Vector Engine)

策略模式双引擎：Cloud (OpenAI) / Edge (ONNX Local)
"""
from __future__ import annotations

import asyncio
import json
import os
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from rich.console import Console

logger = logging.getLogger(__name__)
console = Console()

_CONFIG_PATH = Path.home() / ".jachin" / "nexus_config.json"
_DEFAULT_EMBEDDING_DIM = 384  # all-MiniLM-L6-v2 / text-embedding-3-small


def _load_embedding_config() -> dict[str, Any]:
    """从 ~/.jachin/nexus_config.json 读取 embedding 配置"""
    if not _CONFIG_PATH.exists():
        return {"embedding_mode": "cloud"}
    try:
        data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        emb = data.get("embedding")
        if isinstance(emb, dict):
            cfg = {"embedding_mode": emb.get("embedding_mode", "cloud"), **emb}
        else:
            cfg = {"embedding_mode": data.get("embedding_mode", "cloud")}
        # 从 llm_keys 读取 dashscope，供 DashScope Embedding 使用
        llm_keys = data.get("llm_keys") or {}
        if isinstance(llm_keys, dict) and llm_keys.get("dashscope"):
            cfg.setdefault("dashscope_api_key", llm_keys.get("dashscope"))
        return cfg
    except Exception as e:
        logger.warning("读取 nexus_config.json 失败: %s，使用默认 cloud 模式", e)
        return {"embedding_mode": "cloud"}


class BaseEmbedder(ABC):
    """Embedding 抽象基类"""

    @abstractmethod
    async def embed_text(self, text: str) -> list[float]:
        """将文本转换为向量。"""
        ...

    @property
    def dimension(self) -> int:
        """向量维度"""
        return _DEFAULT_EMBEDDING_DIM


class OpenAIEmbedder(BaseEmbedder):
    """极速云端核：调用 OpenAI / 兼容 API 生成向量"""

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.model = model
        self._client = None
        self._api_key = api_key
        self._base_url = base_url

    def _get_client(self) -> Any:
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self._api_key, base_url=self._base_url or None)
        return self._client

    async def embed_text(self, text: str) -> list[float]:
        """调用 OpenAI Embeddings API"""
        try:
            client = self._get_client()
            loop = asyncio.get_running_loop()
            r = await loop.run_in_executor(
                None,
                lambda: client.embeddings.create(model=self.model, input=text),
            )
            return r.data[0].embedding
        except Exception as e:
            logger.warning("OpenAI Embedding 失败: %s", e)
            return []

    @property
    def dimension(self) -> int:
        if self.model == "text-embedding-3-small":
            return 1536
        if self.model == "text-embedding-3-large":
            return 3072
        if "text-embedding-v" in self.model:
            return 1536  # DashScope text-embedding-v2/v3 默认
        return 1536


class DashScopeEmbedder(BaseEmbedder):
    """阿里云 DashScope 文本嵌入（text-embedding-v2/v3）"""

    def __init__(
        self,
        model: str = "text-embedding-v2",
        api_key: str | None = None,
    ) -> None:
        self.model = model
        self._api_key = api_key or None

    def _get_key(self) -> str | None:
        if self._api_key:
            return self._api_key
        return os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("QWEN_API_KEY")

    async def embed_text(self, text: str) -> list[float]:
        """调用 DashScope Embedding API（OpenAI 兼容接口）"""
        key = self._get_key()
        if not key:
            logger.warning("DashScope Embedding 失败: 未配置 DASHSCOPE_API_KEY")
            return []
        try:
            from openai import OpenAI

            try:
                from core.brain.llm.dashscope_regional import get_dashscope_regional_api_base

                _default_base = get_dashscope_regional_api_base()
            except ImportError:
                _default_base = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            base = (os.environ.get("DASHSCOPE_API_BASE", "").strip() or _default_base)
            client = OpenAI(api_key=key, base_url=base)
            loop = asyncio.get_running_loop()
            r = await loop.run_in_executor(
                None,
                lambda: client.embeddings.create(model=self.model, input=text),
            )
            return r.data[0].embedding
        except Exception as e:
            logger.warning("DashScope Embedding 失败: %s", e)
            return []

    @property
    def dimension(self) -> int:
        return 1536


class ONNXEmbedder(BaseEmbedder):
    """深渊边缘核：本地 sentence-transformers / ONNX，断网可用"""

    DEFAULT_MODEL = "all-MiniLM-L6-v2"  # ~90MB，384 维，可离线

    def __init__(self, model_path: str | Path | None = None) -> None:
        self.model_path = Path(model_path) if model_path else None
        self._model = None

    def _ensure_loaded(self) -> bool:
        """懒加载：sentence-transformers（支持 ONNX 后端若已安装 onnxruntime）"""
        if self._model is not None:
            return True
        try:
            import onnxruntime
        except ImportError:
            logger.info("onnxruntime 未安装。可选: pip install onnxruntime 获得更轻量推理")
            console.print("[dim]💡 安装 onnxruntime 可获得更轻量推理: pip install onnxruntime[/dim]")

        try:
            from sentence_transformers import SentenceTransformer
            model_name = str(self.model_path) if self.model_path and self.model_path.exists() else self.DEFAULT_MODEL
            self._model = SentenceTransformer(model_name)
            return True
        except ImportError:
            console.print(
                "[red]⚠ Edge 引擎需要 sentence-transformers。请执行: pip install sentence-transformers[/red]"
            )
            return False

    async def embed_text(self, text: str) -> list[float]:
        """本地推理"""
        if not self._ensure_loaded():
            return []
        try:
            loop = asyncio.get_running_loop()
            emb = await loop.run_in_executor(None, lambda: self._model.encode(text))
            return emb.tolist() if hasattr(emb, "tolist") else list(emb)
        except Exception as e:
            logger.exception("Edge Embedding 失败: %s", e)
            return []

    @property
    def dimension(self) -> int:
        return 384  # all-MiniLM-L6-v2


def get_embedder(config: dict[str, Any] | None = None) -> BaseEmbedder:
    """
    工厂函数：根据配置返回对应 Embedder 实例。

    Args:
        config: 若为 None，则从 ~/.jachin/nexus_config.json 读取

    Returns:
        OpenAIEmbedder、DashScopeEmbedder 或 ONNXEmbedder
    """
    if config is None:
        cfg = _load_embedding_config()
    else:
        cfg = config

    mode = (cfg.get("embedding_mode") or "cloud").lower()
    if mode == "local" or mode == "edge":
        return ONNXEmbedder(model_path=cfg.get("onnx_model_path"))
    # cloud 模式：优先 OpenAI，无 Key 时回退 DashScope（用户常用阿里云）
    openai_key = cfg.get("openai_api_key") or os.environ.get("OPENAI_API_KEY")
    dashscope_key = cfg.get("dashscope_api_key") or os.environ.get("DASHSCOPE_API_KEY")
    if openai_key:
        return OpenAIEmbedder(
            model=cfg.get("openai_model", "text-embedding-3-small"),
            api_key=openai_key,
        )
    if dashscope_key:
        return DashScopeEmbedder(
            model=cfg.get("dashscope_model", "text-embedding-v2"),
            api_key=dashscope_key,
        )
    return OpenAIEmbedder(
        model=cfg.get("openai_model", "text-embedding-3-small"),
        api_key=None,
    )
