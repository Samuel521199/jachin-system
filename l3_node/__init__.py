"""
Jachin Nexus V2 - Layer 3 执行引擎（单主轴 ReAct / run_agent）

持密文 Key，本地解密后直连外部 LLM API；MCP/Skill、网关、语义层、内联 Critic、Experience RAG。
架构 SSOT：docs/architecture/JACHIN_HYBRID_AGENT_ARCHITECTURE.md（OpenClaw 等仅为能力对标语境）。
"""
from __future__ import annotations

__version__ = "2.0.0"
