"""intent_gateway 配置：读 nexus_config.json → intent_gateway 段。"""
from __future__ import annotations

from typing import Any

_DEFAULT: dict[str, Any] = {
    # L2 / 网关侧「小模型」：JSON 分类、指代消解等（与 ReAct 主模型 LLM_MODEL 解耦）
    "classification_model": "qwen-turbo",
    # 附件含图或需视觉理解时的网关/直连 completion 模型
    "multimodal_model": "qwen-vl-max",
    "classification_max_tokens": 2000,
    "classification_head_tokens": 1000,
    "classification_tail_tokens": 1000,
    "semantic_cache_enabled": False,
    "semantic_cache_ttl_seconds": 3600,
    "semantic_cache_max_entries": 2048,
    "rbac_l2_precheck_enabled": False,
    "jit_binding_log_only": True,
    "dag_splitting_enabled": False,
    # 复合意图 LLM 拆分（依赖 dependency_analysis + sub_intents；失败可回落启发式）
    "dag_splitting_llm_enabled": False,
    "dag_splitting_llm_timeout_sec": 12.0,
    "dag_splitting_llm_max_tokens": 1200,
    "dag_splitting_fallback_heuristic": True,
    "attachment_l2_bytes_threshold": 524288,
    "ood_veto_direct_bypass_enabled": True,
    # §12.4 总闸：关则表面 OOD 不硬拦、不因 surface 分数否决直连 bypass（多轮压测/降噪时可关）
    "ood_surface_gate_enabled": False,
    "ood_mixed_injection_enabled": True,
    "ood_hard_block_llm_enabled": True,
    # 提高默认阈值：仅高置信（键盘游走/重复拉丁等）硬拦，减少正常中英技术句误杀
    "ood_hard_block_min_score": 0.88,
    # L0.5：JWT/Base64/JSON/堆栈/K8s 等技术载荷 — 降低误杀（宁可漏过键盘噪声类误报）
    "ood_technical_exemption_enabled": True,
    "ood_hard_block_reply_zh": "",
    # L1.5：小模型语义域判定（闲聊/非业务域 → 拒答，不调主模型）。默认关，企业节点在 nexus intent_gateway 中开启
    "semantic_ood_llm_enabled": False,
    "semantic_ood_timeout_sec": 5.0,
    "semantic_ood_min_confidence": 0.78,
    "semantic_ood_max_input_chars": 4000,
    "semantic_ood_max_tokens": 128,
    "semantic_ood_reply_zh": "",
    # 后台任务通道跳过语义 OOD（避免长任务被误判）
    "semantic_ood_skip_background_task": True,
    "clarification_drift_overlap_min": 0.06,
    "clarification_interrupt_keywords": None,
    # §9.4 L0 全局逃生舱（docs/L3_AMBIGUOUS_INTENT_ARCHITECTURE.md）
    "global_escape_hatch_enabled": True,
    "global_escape_keywords": None,
    # §9.1 槽位追问上限与 Abort 文案（由 slot_filling_guard 使用）
    "slot_gating_enabled": True,
    "slot_filling_max_clarification_retries": 3,
    "slot_filling_abort_reply_zh": "",
    "slot_clarification_ttl_seconds": 600.0,
    "slot_clarification_llm_enabled": False,
    "slot_clarification_llm_timeout_sec": 4.0,
    "slot_clarification_llm_max_tokens": 200,
    # 演示：「重启服务器」类句需 IPv4 槽位（默认关，避免误触生产）
    "slot_filling_demo_restart_enabled": False,
    # §9.2 规划静态扫描器重试上限（执行面接入时与 plan_static_linter 配合）
    "planning_static_linter_max_retries": 2,
    # §9.3 实体消解 Top1/Top2 分数差低于此则禁止静默消解
    "entity_resolver_min_top1_top2_margin": 0.08,
    # §7.2 composite 规划门禁（默认关，避免改变现有 ReAct；企业节点在 nexus 开启）
    "planning_composite_gate_enabled": False,
    "needs_info_gateway_enabled": True,
    # §6 / §3.4：长输入或模糊面可抬升为 composite（配合 planning_composite_gate_enabled）
    "force_planning_phase_first": False,
    "force_planning_min_user_chars": 400,
    "vague_task_treat_as_composite": False,
    # §7.1：DAG 子意图 slot_schema 门控（无 schema 时不触发）
    "subintent_slot_gate_enabled": True,
    # §9 M4/M5：Tracker JSONL（intent_tracker.jsonl）
    "intent_tracker_jsonl_enabled": True,
    # §8.1：槽位 Abort 后单次闲聊式改写系统文案（仍不执行挂起任务）
    "abort_slot_fill_chat_fallback_enabled": False,
    "l1_sandbox_allow_third_party": False,
    "l1_enforce_skill_id_shape": False,
    # §5 / §12.4：向量 Top-K（默认关闭，避免无 Key 环境额外请求；开启后失败自动降级）
    "embedding_router_enabled": False,
    "embedding_model": "",
    "embedding_top_k": 5,
    "embedding_min_top1_similarity": 0.22,
    "embedding_sparse_margin_min": 0.035,
    "embedding_ood_veto_bypass_enabled": True,
    # §6.1：小模型扩写 routing_utterance（默认关闭）
    "classification_llm_rewrite_enabled": False,
    "classification_llm_timeout_sec": 4.0,
    "classification_llm_max_tokens": 256,
    # §12.1：多模态侧路头（默认关闭）
    "multimodal_routing_head_enabled": False,
    "multimodal_routing_head_timeout_sec": 6.0,
    "multimodal_routing_head_max_tokens": 128,
    # §6.3：负反馈 jsonl
    "flywheel_feedback_enabled": False,
    # Omni-Context Sniffer（docs/INTENT_GATEWAY_CONTEXT_SNIFFER_AND_TRANSPARENCY.md）
    "context_sniffer_enabled": True,
    "context_sniffer_max_total_chars": 1500,
    "context_sniffer_max_git_chars": 500,
    # 嗅探完成后写入 intent_tracker.jsonl（correlation_id / run_id 审计）
    "context_sniffer_tracker_enabled": True,
    # 工作区 db_semantics.md + golden_sql_examples.jsonl 注入 [ENVIRONMENT_REPORT]（语义层 + 关键词 Few-Shot）
    "context_sniffer_workspace_db_context_enabled": True,
    "context_sniffer_db_semantics_max_chars": 480,
    "context_sniffer_golden_sql_max_chars": 520,
    "context_sniffer_golden_sql_max_examples": 3,
    # 参谋长人设 + [ENVIRONMENT_REPORT] 注入（关则仍可有 environment_report 数据但不注入 prompt）
    "chief_advisor_prompt_enabled": True,
    # 子意图 DAG 拓扑校验结果写入 intent_tracker（run_id 关联）
    "dag_topology_tracker_enabled": True,
}


def get_intent_gateway_config() -> dict[str, Any]:
    out = dict(_DEFAULT)
    try:
        from l3_node.nexus_config import get_nexus_config

        raw = get_nexus_config() or {}
        ig = raw.get("intent_gateway")
        if isinstance(ig, dict):
            for k, v in ig.items():
                if isinstance(k, str) and k and not k.startswith("_"):
                    out[k] = v
    except Exception:
        pass
    return out
