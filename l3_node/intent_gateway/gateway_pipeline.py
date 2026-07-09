"""
run_agent 入站流水线：澄清门控 → 附件 L1 规则与 Feature Slots → routing_utterance → 分类面截断重建 → 环境嗅探（含 semantic_layer YAML）。

该 gateway 只提供证据预处理；最终裁决见 docs/07_memory_first_main_agent_and_voice_app_agents.md。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Optional

from l3_node.intent_gateway.bundle import GatewayContextBundle
from l3_node.intent_gateway.clarification_gate import apply_clarification_gate
from l3_node.intent_gateway.global_escape_hatch import apply_global_escape_hatch
from l3_node.intent_gateway.model_resolve import (
    get_classification_model_litellm_id,
    get_multimodal_model_litellm_id,
)
from l3_node.intent_gateway.routing_utterance import compute_routing_utterance
from l3_node.intent_gateway.semantic_router import infer_semantic_route_hint

logger = logging.getLogger(__name__)


def apply_attachment_gateway_rules(bundle: GatewayContextBundle) -> None:
    from l3_node.intent_gateway.config import get_intent_gateway_config

    cfg = get_intent_gateway_config()
    threshold = int(cfg.get("attachment_l2_bytes_threshold", 524288))
    total = sum(m.size_bytes for m in bundle.attachments_sanitized)
    bundle.extra["attachment_total_bytes"] = total
    bundle.extra["attachment_feature_slots"] = bundle.attachment_feature_slots()
    bundle.extra["attachment_has_image"] = any(m.has_image for m in bundle.attachments_sanitized)
    if total >= threshold > 0:
        bundle.extra["attachment_forced_l2_routing"] = True


async def apply_gateway_ingress_pipeline(
    bundle: GatewayContextBundle,
    user_input: str,
    prior_messages: list[dict[str, Any]],
    *,
    on_step: Optional[Callable[[str, str, str], None]] = None,
    run_id: str = "",
    workspace_dir: str = "",
    skip_context_sniffer: bool = False,
) -> None:
    _esc = apply_global_escape_hatch(bundle, user_input or "")
    if _esc.get("escaped"):
        bundle.extra["global_escape_hatch_ingress"] = _esc
    apply_clarification_gate(bundle, user_input or "", prior_messages)
    apply_attachment_gateway_rules(bundle)
    bundle.routing_utterance = compute_routing_utterance(
        user_input=user_input or "",
        prior_messages=prior_messages,
        system_state=bundle.system_state,
    )
    bundle.rebuild_classification_text()
    hint = infer_semantic_route_hint(bundle.classification_text)
    if hint:
        bundle.extra["semantic_route_hint"] = hint

    bundle.extra["gateway_classification_model_litellm"] = get_classification_model_litellm_id()
    bundle.extra["gateway_multimodal_model_litellm"] = get_multimodal_model_litellm_id()

    try:
        obs = {
            "correlation_id": bundle.correlation_id[:16],
            "system_state": str(bundle.system_state),
            "classification_truncated": bundle.classification_truncated,
            "attachment_total_bytes": bundle.extra.get("attachment_total_bytes"),
            "attachment_has_image": bool(bundle.extra.get("attachment_has_image")),
            "gateway_classification_model": bundle.extra.get("gateway_classification_model_litellm"),
            "gateway_multimodal_model": bundle.extra.get("gateway_multimodal_model_litellm"),
            "forced_l2": bool(bundle.extra.get("attachment_forced_l2_routing")),
            "clarification_gate": bundle.extra.get("clarification_gate"),
            # 实时知识意图在 run_agent 内由小模型补判后写入 bundle / bundle.extra（此处恒为初始值）
            "requires_realtime_knowledge": bool(getattr(bundle, "requires_realtime_knowledge", False)),
            # domain_experts 在 run_agent 内由小模型补判后写入（此处恒为初始值）
            "domain_experts": list(getattr(bundle, "domain_experts", None) or []),
        }
        logger.info("[IntentGatewayObs] %s", json.dumps(obs, ensure_ascii=False))
    except Exception:
        pass

    from l3_node.intent_gateway.config import get_intent_gateway_config

    _ig_sniff = get_intent_gateway_config()
    ws = (workspace_dir or "").strip()
    if not ws:
        try:
            from l3_node.jachin_config import get_jachin_root

            ws = str((get_jachin_root() / "workspace").resolve())
        except Exception:
            ws = str(Path.home() / ".jachin" / "workspace")

    if skip_context_sniffer:
        bundle.extra["environment_report"] = {
            "ok": True,
            "skipped": True,
            "reason": "voice_fast_lane",
        }
        bundle.extra["semantic_layer"] = {}
        if bool(_ig_sniff.get("context_sniffer_tracker_enabled", True)):
            try:
                from l3_node.intent_gateway.intent_tracker import emit_intent_tracker_event

                emit_intent_tracker_event(
                    "context_sniffer_skipped",
                    {
                        "correlation_id": (bundle.correlation_id or "")[:32],
                        "run_id": (run_id or bundle.correlation_id or "")[:32],
                        "reason": "voice_fast_lane",
                    },
                )
            except Exception:
                pass
        return
    if not bool(_ig_sniff.get("context_sniffer_enabled", True)):
        bundle.extra["environment_report"] = {
            "ok": True,
            "skipped": True,
            "reason": "context_sniffer_disabled",
        }
        try:
            from l3_node.intent_gateway.workspace_db_context import load_db_semantics_yaml

            bundle.extra["semantic_layer"] = load_db_semantics_yaml(ws)
        except Exception:
            try:
                from l3_node.intent_gateway.workspace_db_context import default_semantic_layer

                bundle.extra["semantic_layer"] = default_semantic_layer()
            except Exception:
                bundle.extra["semantic_layer"] = {}
        if bool(_ig_sniff.get("context_sniffer_tracker_enabled", True)):
            try:
                from l3_node.intent_gateway.intent_tracker import emit_intent_tracker_event

                emit_intent_tracker_event(
                    "context_sniffer_skipped",
                    {
                        "correlation_id": (bundle.correlation_id or "")[:32],
                        "run_id": (run_id or bundle.correlation_id or "")[:32],
                        "reason": "disabled",
                    },
                )
            except Exception:
                pass
        return

    rid = (run_id or bundle.correlation_id or "").strip()
    try:
        _max_total = int(_ig_sniff.get("context_sniffer_max_total_chars", 1500))
    except (TypeError, ValueError):
        _max_total = 1500
    try:
        _max_git = int(_ig_sniff.get("context_sniffer_max_git_chars", 500))
    except (TypeError, ValueError):
        _max_git = 500
    _max_total = max(256, min(_max_total, 16_000))
    _max_git = max(64, min(_max_git, _max_total))

    try:
        from l3_node.intent_gateway.context_sniffer import build_environment_report

        bundle.extra["environment_report"] = await build_environment_report(
            user_input or bundle.user_input or "",
            ws,
            on_step=on_step,
            run_id=rid,
            max_total_chars=_max_total,
            max_git_chars=_max_git,
        )
        _env_rep = bundle.extra.get("environment_report")
        if isinstance(_env_rep, dict) and isinstance(_env_rep.get("semantic_layer"), dict):
            _sl_from_rep = _env_rep["semantic_layer"]
            if _sl_from_rep:
                bundle.extra["semantic_layer"] = _sl_from_rep
            else:
                try:
                    from l3_node.intent_gateway.workspace_db_context import load_db_semantics_yaml

                    bundle.extra["semantic_layer"] = load_db_semantics_yaml(ws)
                except Exception:
                    try:
                        from l3_node.intent_gateway.workspace_db_context import default_semantic_layer

                        bundle.extra["semantic_layer"] = default_semantic_layer()
                    except Exception:
                        bundle.extra["semantic_layer"] = {}
        else:
            try:
                from l3_node.intent_gateway.workspace_db_context import load_db_semantics_yaml

                bundle.extra["semantic_layer"] = load_db_semantics_yaml(ws)
            except Exception:
                try:
                    from l3_node.intent_gateway.workspace_db_context import default_semantic_layer

                    bundle.extra["semantic_layer"] = default_semantic_layer()
                except Exception:
                    bundle.extra["semantic_layer"] = {}
        if bool(_ig_sniff.get("context_sniffer_tracker_enabled", True)):
            try:
                from l3_node.intent_gateway.intent_tracker import emit_intent_tracker_event

                _rep = bundle.extra["environment_report"]
                _meta = _rep.get("meta") if isinstance(_rep, dict) else {}
                emit_intent_tracker_event(
                    "context_sniffer_complete",
                    {
                        "correlation_id": (bundle.correlation_id or "")[:32],
                        "run_id": rid[:32],
                        "truncated": bool(isinstance(_meta, dict) and _meta.get("truncated")),
                        "total_chars": int(isinstance(_meta, dict) and _meta.get("total_chars") or 0),
                        "workspace_dir_tail": ws[-120:] if ws else "",
                    },
                )
            except Exception:
                pass
    except Exception as e:
        logger.debug("[IntentGateway] environment_report 跳过: %s", e)
        bundle.extra["environment_report"] = {"ok": False, "error": str(e)[:200]}
        try:
            from l3_node.intent_gateway.workspace_db_context import load_db_semantics_yaml

            bundle.extra["semantic_layer"] = load_db_semantics_yaml(ws)
        except Exception:
            try:
                from l3_node.intent_gateway.workspace_db_context import default_semantic_layer

                bundle.extra["semantic_layer"] = default_semantic_layer()
            except Exception:
                bundle.extra["semantic_layer"] = {}
        if bool(_ig_sniff.get("context_sniffer_tracker_enabled", True)):
            try:
                from l3_node.intent_gateway.intent_tracker import emit_intent_tracker_event

                emit_intent_tracker_event(
                    "context_sniffer_error",
                    {
                        "correlation_id": (bundle.correlation_id or "")[:32],
                        "run_id": rid[:32],
                        "error": str(e)[:160],
                    },
                )
            except Exception:
                pass
