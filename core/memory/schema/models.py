"""
SQLAlchemy 数据模型
SQLAlchemy Data Models
"""

import uuid
from datetime import datetime
from typing import Optional, Dict, Any
from sqlalchemy import (
    Column, String, Integer, Boolean, Text, TIMESTAMP, ForeignKey,
    Index, JSON, UUID as SQLUUID
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from core.memory.schema.database import Base


class User(Base):
    """用户表"""
    __tablename__ = "users"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(255), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=True, index=True)
    password_hash = Column(String(255), nullable=True)
    role = Column(String(50), default="user")  # admin, user
    created_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())


class Skill(Base):
    """技能表"""
    __tablename__ = "skills"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    skill_id = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    version = Column(String(50), nullable=False)
    description = Column(Text, nullable=True)
    author = Column(String(255), nullable=True)
    license = Column(String(50), nullable=True)
    runtime = Column(String(50), nullable=False)  # docker, wasm, native, ray
    entrypoint = Column(String(255), nullable=True)
    manifest_path = Column(Text, nullable=False)
    install_path = Column(Text, nullable=False)  # skills_repo/{skill_id}/
    status = Column(String(50), default="installed", index=True)  # installed, active, disabled, error
    installed_at = Column(TIMESTAMP, server_default=func.now())
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())
    last_used_at = Column(TIMESTAMP, nullable=True)
    usage_count = Column(Integer, default=0)
    
    # Relationship to capabilities
    skill_capabilities = relationship("SkillCapability", back_populates="skill", cascade="all, delete-orphan")


class SkillCapability(Base):
    """技能能力映射表"""
    __tablename__ = "skill_capabilities"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    skill_id = Column(UUID(as_uuid=True), ForeignKey("skills.id", ondelete="CASCADE"), nullable=False, index=True)
    capability_name = Column(String(255), nullable=False, index=True)
    capability_type = Column(String(50), nullable=True, index=True)  # action, sensor, processor (nullable for backward compatibility)
    description = Column(Text, nullable=True)
    input_schema = Column(JSONB, nullable=True)  # JSON Schema
    output_schema = Column(JSONB, nullable=True)  # JSON Schema
    created_at = Column(TIMESTAMP, server_default=func.now())
    
    # Relationship to skill
    skill = relationship("Skill", back_populates="skill_capabilities")
    
    __table_args__ = (
        Index("idx_skill_capabilities_unique", "skill_id", "capability_name", unique=True),
    )


class Memory(Base):
    """记忆表"""
    __tablename__ = "memories"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    content = Column(Text, nullable=False)
    content_type = Column(String(50), nullable=False)  # text, image, audio, video, file
    vector_id = Column(String(255), nullable=True)  # Qdrant collection ID
    collection_name = Column(String(255), nullable=True)  # Qdrant collection name
    permission_level = Column(String(50), default="private", index=True)  # private, shared, public
    meta_data = Column(JSONB, nullable=True)  # 额外元数据 (renamed from 'metadata' to avoid SQLAlchemy conflict)
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())


class MemoryPermission(Base):
    """记忆权限表"""
    __tablename__ = "memory_permissions"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    memory_id = Column(UUID(as_uuid=True), ForeignKey("memories.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    permission_type = Column(String(50), nullable=False)  # read, write, delete
    granted_at = Column(TIMESTAMP, server_default=func.now())
    granted_by = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    
    __table_args__ = (
        Index("idx_memory_permissions_unique", "memory_id", "user_id", "permission_type", unique=True),
    )


class Task(Base):
    """任务表"""
    __tablename__ = "tasks"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id = Column(String(255), unique=True, nullable=False, index=True)  # Ray task ID
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True, index=True)
    task_type = Column(String(50), nullable=False)  # llm_inference, skill_execution, etc.
    status = Column(String(50), default="pending", index=True)  # pending, running, completed, failed, cancelled
    skill_id = Column(UUID(as_uuid=True), ForeignKey("skills.id"), nullable=True)
    capability_name = Column(String(255), nullable=True)
    input_data = Column(JSONB, nullable=True)
    output_data = Column(JSONB, nullable=True)
    error_message = Column(Text, nullable=True)
    worker_node = Column(String(255), nullable=True)  # Ray worker node ID
    priority = Column(Integer, default=0)
    created_at = Column(TIMESTAMP, server_default=func.now(), index=True)
    started_at = Column(TIMESTAMP, nullable=True)
    completed_at = Column(TIMESTAMP, nullable=True)
    duration_ms = Column(Integer, nullable=True)  # 执行时长（毫秒）


class ClusterNode(Base):
    """集群节点表"""
    __tablename__ = "cluster_nodes"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    node_id = Column(String(255), unique=True, nullable=False, index=True)  # 节点唯一标识
    node_type = Column(String(50), nullable=False, index=True)  # master, worker
    host = Column(String(255), nullable=False)
    port = Column(Integer, nullable=False)
    ray_port = Column(Integer, nullable=True)  # Ray端口
    dapr_port = Column(Integer, nullable=True)  # Dapr端口
    has_gpu = Column(Boolean, default=False)
    gpu_count = Column(Integer, default=0)
    gpu_memory_gb = Column(Integer, nullable=True)
    cpu_count = Column(Integer, default=0)
    memory_gb = Column(Integer, nullable=True)
    disk_gb = Column(Integer, nullable=True)
    status = Column(String(50), default="offline", index=True)  # online, offline, maintenance, error
    last_heartbeat = Column(TIMESTAMP, nullable=True)
    registered_at = Column(TIMESTAMP, server_default=func.now())
    meta_data = Column(JSONB, nullable=True)  # 额外信息 (renamed from 'metadata' to avoid SQLAlchemy conflict)
