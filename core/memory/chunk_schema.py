"""
MemoryChunk - RAG 记忆碎片数据模型

战役一：地基与神经元
定义向量存储的元数据格式，与 docs/RAG_ARCHITECTURE.md 对齐。
"""

from typing import Optional

from pydantic import BaseModel, Field


class MemoryChunk(BaseModel):
    """
    记忆碎片 - 向量库存储单元

    对应 RAG 架构中的 Chunk，支持 Core Memory 铂金标签与多维坐标过滤。
    """

    id: str = Field(..., description="唯一标识，通常为 UUID")
    content: str = Field(..., description="文本内容（原始或摘要）")
    vector: list[float] = Field(..., description="Embedding 向量")
    user_id: str = Field(default="", description="用户 ID，用于多租户过滤")
    device_id: str = Field(default="", description="设备 ID，用于 Edge L1 同步过滤")
    character_id: str = Field(default="", description="人格 ID，用于多角色记忆隔离")
    is_core: bool = Field(default=False, description="铂金标签：True 表示 Core Memory，免疫时间衰减、永不覆写")
    timestamp: int = Field(..., description="Unix 时间戳（秒），用于时效衰减计算")

    model_config = {"extra": "forbid"}

    def to_row_dict(self) -> dict:
        """转为向量库行格式，用于 upsert"""
        return {
            "id": self.id,
            "content": self.content,
            "vector": self.vector,
            "user_id": self.user_id,
            "device_id": self.device_id,
            "character_id": self.character_id,
            "is_core": self.is_core,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_row(cls, row: dict) -> "MemoryChunk":
        """从向量库行反序列化"""
        return cls(
            id=str(row["id"]),
            content=str(row.get("content", "")),
            vector=list(row["vector"]) if isinstance(row.get("vector"), (list, tuple)) else row["vector"],
            user_id=str(row.get("user_id", "")),
            device_id=str(row.get("device_id", "")),
            character_id=str(row.get("character_id", "")),
            is_core=bool(row.get("is_core", False)),
            timestamp=int(row.get("timestamp", 0)),
        )
