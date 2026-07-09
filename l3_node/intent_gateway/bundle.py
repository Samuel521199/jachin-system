"""
GatewayContextBundle：统一入站上下文，替代裸字符串贯穿网关算子。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from l3_node.intent_gateway.config import get_intent_gateway_config
from l3_node.intent_gateway.sanitize import (
    SanitizedFileMeta,
    sanitize_attachments_list,
    trim_attachments_metadata_list,
)
from l3_node.intent_gateway.truncation import truncate_for_gateway_classification


class SystemState(str, Enum):
    NORMAL = "NORMAL"
    AWAITING_CLARIFICATION = "AWAITING_CLARIFICATION"


@dataclass
class GatewayContextBundle:
    """完整 user 文本保留于 user_input；classification_text 仅供路由/L2。"""

    user_input: str
    short_memory_context: str = ""
    session_id: str = ""
    correlation_id: str = ""
    tenant_id: str = ""
    channel: str = ""
    system_state: SystemState = SystemState.NORMAL
    clarification_handle: str = ""
    clarification_deadline_ts: float = 0.0
    attachments_raw: list[dict[str, Any]] = field(default_factory=list)
    attachments_sanitized: list[SanitizedFileMeta] = field(default_factory=list)
    routing_utterance: str = ""  # §6.1 供分类/L2 的路由文本（澄清挂接时可异于 user_input）
    #: 仅本轮意图表面句（routing_utterance / user_input）经截断后的文本；**不含** short_memory。
    #: 供路由、OOD、语义缓存键、embedding 分类面等；记忆统一由 Cognitive Kernel 的
    #: MemoryRecallAgent 进入 RelevantMemoryBundle。
    classification_text: str = ""
    classification_truncated: bool = False
    #: 网关小模型判定：本轮是否需要实时外部知识（新闻/行情/文档/天气事实等）
    requires_realtime_knowledge: bool = False
    #: 动态专家智囊团：1–3 个资深身份标签（简单闲聊可为空）；由小模型在 run_agent 内补判写入
    domain_experts: list[str] = field(default_factory=list)
    registry_version: str = "rv0"
    extra: dict[str, Any] = field(default_factory=dict)

    def attachment_feature_slots(self) -> List[Dict[str, Any]]:
        """§12.1 Feature Slots：结构化附件摘要，禁止拼进主 LLM 提示时可单独走侧路/日志/小模型。"""
        out: List[Dict[str, Any]] = []
        for m in self.attachments_sanitized:
            out.append(
                {
                    "size_bytes": m.size_bytes,
                    "mime": m.mime,
                    "name_safe": m.name_safe,
                    "name_fingerprint": m.name_fingerprint,
                    "has_image": m.has_image,
                }
            )
        return out

    def rebuild_classification_text(self) -> None:
        """
        意图分类面（Intent-Context Decoupling）：
        **仅**截断 ``routing_utterance`` 或 ``user_input``。
        ``short_memory_context`` 仅保留为兼容字段，不再作为 System Prompt / 嗅探的
        独立记忆入口。主循环短期记忆必须通过 MemoryRecallAgent 进入 RelevantMemoryBundle。
        旧逻辑 ``short_memory + "---" + tail`` 已移除，以免污染路由、Semantic Cache 键和
        Embedding 分类面。
        """
        cfg = get_intent_gateway_config()
        max_tok = int(cfg.get("classification_max_tokens", 2000))
        head = int(cfg.get("classification_head_tokens", 1000))
        tail = int(cfg.get("classification_tail_tokens", 1000))
        intent_only = (self.routing_utterance or self.user_input or "").strip()
        self.classification_text, self.classification_truncated = truncate_for_gateway_classification(
            intent_only,
            max_tokens=max_tok,
            head_tokens=head,
            tail_tokens=tail,
        )


def build_gateway_bundle(
    *,
    user_input: str,
    short_memory_context: str = "",
    session_id: str = "",
    correlation_id: str = "",
    tenant_id: str = "",
    channel: str = "",
    system_state: str | SystemState = SystemState.NORMAL,
    clarification_handle: str = "",
    clarification_deadline_ts: float = 0.0,
    attachments_metadata: Optional[list[dict[str, Any]]] = None,
    implicit_attribution: Optional[dict[str, Any]] = None,
) -> GatewayContextBundle:
    st = system_state if isinstance(system_state, SystemState) else SystemState(str(system_state or "NORMAL"))
    ch = channel
    tid = tenant_id
    if implicit_attribution and isinstance(implicit_attribution, dict):
        if not ch:
            ch = str(implicit_attribution.get("channel") or "")
        if not tid:
            tid = str(implicit_attribution.get("tenant_id") or implicit_attribution.get("org_id") or "")
    if not tid:
        tid = str(os.environ.get("JACHIN_TENANT_ID") or os.environ.get("JACHIN_ORG_ID") or "").strip()
    att_raw = trim_attachments_metadata_list(list(attachments_metadata or []))
    bundle = GatewayContextBundle(
        user_input=user_input or "",
        short_memory_context=short_memory_context or "",
        session_id=session_id or "",
        correlation_id=correlation_id or "",
        tenant_id=tid,
        channel=ch,
        system_state=st,
        clarification_handle=clarification_handle or "",
        clarification_deadline_ts=float(clarification_deadline_ts or 0.0),
        attachments_raw=att_raw,
        attachments_sanitized=sanitize_attachments_list(att_raw),
    )
    bundle.routing_utterance = bundle.user_input or ""
    bundle.rebuild_classification_text()
    return bundle
