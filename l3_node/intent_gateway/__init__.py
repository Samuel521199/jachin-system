"""
意图网关战役实现：GatewayContextBundle、截断、附件清洗、Registry、拓扑、缓存、Saga/JIT 桩。
规格见 docs/USER_INTENT_RECOGNITION_REMEDIATION_PLAN.md。
"""
from __future__ import annotations

from l3_node.intent_gateway.bundle import (
    GatewayContextBundle,
    SystemState,
    build_gateway_bundle,
)
from l3_node.intent_gateway.bootstrap import ensure_default_intent_registry
from l3_node.intent_gateway.compensation_registry import (
    get_compensation_registry,
)
from l3_node.intent_gateway.dag_router import (
    propose_subintents_async,
    propose_subintents_from_user_text,
    propose_subintents_heuristic,
    propose_subintents_with_analysis_async,
    split_intents_enabled,
)
from l3_node.intent_gateway.execution_inject import build_gateway_system_inject
from l3_node.intent_gateway.flywheel import emit_intent_gateway_signal, hash_utterance
from l3_node.intent_gateway.gateway_pipeline import apply_gateway_ingress_pipeline
from l3_node.intent_gateway.model_resolve import (
    get_classification_model_litellm_id,
    get_multimodal_model_litellm_id,
)
from l3_node.intent_gateway.envelope import IntentEnvelope, SubIntentNode
from l3_node.intent_gateway.jit_binding import jit_resolve_entity_refs
from l3_node.intent_gateway.rbac_precheck import precheck_l2_subintent_allowed
from l3_node.intent_gateway.registry import get_intent_registry, run_registered_preflights
from l3_node.intent_gateway.saga import SagaCoordinator, SagaStepRecord
from l3_node.intent_gateway.semantic_cache import get_semantic_cache
from l3_node.intent_gateway.semantic_router import merge_route_hints
from l3_node.intent_gateway.topology import topological_order, validate_subintent_dag

ensure_default_intent_registry()

__all__ = [
    "GatewayContextBundle",
    "SystemState",
    "build_gateway_bundle",
    "IntentEnvelope",
    "SubIntentNode",
    "validate_subintent_dag",
    "topological_order",
    "get_intent_registry",
    "run_registered_preflights",
    "get_semantic_cache",
    "precheck_l2_subintent_allowed",
    "get_compensation_registry",
    "SagaCoordinator",
    "SagaStepRecord",
    "jit_resolve_entity_refs",
    "propose_subintents_from_user_text",
    "propose_subintents_heuristic",
    "propose_subintents_async",
    "propose_subintents_with_analysis_async",
    "split_intents_enabled",
    "apply_gateway_ingress_pipeline",
    "get_classification_model_litellm_id",
    "get_multimodal_model_litellm_id",
    "merge_route_hints",
    "build_gateway_system_inject",
    "emit_intent_gateway_signal",
    "hash_utterance",
]
