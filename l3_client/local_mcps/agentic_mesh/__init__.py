"""
Agentic Mesh（幽灵监控网）：AOP 式自愈中间件 + MoE 专家调度。

业务脚本与 Playwright 原子操作保持纯粹；异常由 ``with_phantom_guard`` 外围拦截，
经 ``core_router`` 分类后调度 DOM / 网络等专家，并结合 ``memory_bank`` 做规则复用。
"""

from __future__ import annotations

from l3_client.local_mcps.agentic_mesh.core_router import with_phantom_guard

__all__ = ["with_phantom_guard"]
