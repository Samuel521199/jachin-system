"""Arbiter for ReviewSummary -> DecisionContract -> WorkOrder planning."""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

from .contracts import DecisionContract, ReviewSummary, RiskLevel, ToolPolicy, WorkOrder
from .capability_governance_policy import apply_governance_to_contract, evaluate_capability_governance
from .ledger import append_event, record_decision, record_work_order
from .memory_trust import should_recall_memory, trust_score_detail
from .task_decomposer import DecomposedTaskNode, decompose_task


def _new_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time())}_{uuid.uuid4().hex[:10]}"


def arbitrate_review_summary(summary: ReviewSummary, *, goal: str = "") -> DecisionContract:
    """Create the kernel's final DecisionContract from role reviews.

    Review roles only provide evidence. The Arbiter owns the final contract:
    workflow, role set, risk gate, allowed tools, clarification, and criteria.
    """

    task_type = summary.task_type or "conversation"
    memory_context_refs = _memory_context_refs(summary)
    tool_reliability = _candidate_tool_reliability(summary.candidate_tools, memory_context_refs)
    tool = tool_reliability[0]["tool"] if tool_reliability else (summary.candidate_tools[0] if summary.candidate_tools else "")
    requires_confirmation = _requires_confirmation(summary)
    execution_allowed = bool(tool and not requires_confirmation and not summary.needs_clarification)
    if task_type == "conversation":
        execution_allowed = False
    clarification = summary.clarification_question
    if requires_confirmation and not clarification:
        clarification = "这个操作风险较高，请确认后再执行。"
    memory_rationale = _memory_rationale(memory_context_refs)
    tool_rationale = _tool_reliability_rationale(tool_reliability)

    contract = DecisionContract(
        decision_id=_new_id("decision"),
        turn_id=summary.turn_id,
        task_type=task_type,
        goal=(goal or _goal_from_summary(summary))[:1000],
        selected_workflow=_workflow_for(summary),
        selected_roles=list(summary.selected_roles),
        risk_level=summary.risk_level,
        tool_policy=ToolPolicy(
            allowed_tools=[tool] if tool else [],
            denied_tools=[],
            risk_level=summary.risk_level,
            requires_confirmation=requires_confirmation,
            confirmation_reason=clarification if requires_confirmation else "",
            verification_required=bool(tool),
        ),
        execution_allowed=execution_allowed,
        clarification_question=clarification,
        verification_criteria=_verification_criteria(summary),
        rationale=[
            "Arbiter accepted ReviewBoard evidence and produced the final DecisionContract.",
            *memory_rationale,
            *tool_rationale,
            *summary.rationale,
        ],
        memory_context_refs=memory_context_refs,
    )
    governance_policy = evaluate_capability_governance(summary=summary, contract=contract)
    apply_governance_to_contract(contract, governance_policy)
    record_decision(contract)
    append_event(
        "arbiter_decision",
        summary.turn_id,
        {
            "review_session_id": summary.review_session_id,
            "decision_id": contract.decision_id,
            "top_intent": summary.top_intent,
            "task_type": contract.task_type,
            "execution_allowed": contract.execution_allowed,
            "selected_roles": contract.selected_roles,
            "target": summary.target,
            "candidate_tools": summary.candidate_tools,
            "candidate_tool_reliability": tool_reliability,
            "risk_level": contract.risk_level.value,
            "requires_confirmation": contract.tool_policy.requires_confirmation,
            "memory_context_refs": memory_context_refs,
            "capability_governance_policy": governance_policy.to_dict(),
        },
    )
    return contract


def build_work_order_from_decision(contract: DecisionContract, summary: ReviewSummary) -> WorkOrder | None:
    work_orders = build_work_orders_from_decision(contract, summary)
    return work_orders[0] if work_orders else None


def build_work_orders_from_decision(contract: DecisionContract, summary: ReviewSummary) -> list[WorkOrder]:
    if not contract.tool_policy.allowed_tools:
        return []
    if not contract.execution_allowed and not contract.tool_policy.requires_confirmation:
        return []
    decomposition = decompose_task(contract=contract, summary=summary)
    work_orders = [_work_order_from_decomposed_node(contract, summary, node) for node in decomposition.nodes]
    _annotate_success_execution_order_advice(work_orders)
    for work_order in work_orders:
        record_work_order(work_order, contract.turn_id)
    append_event(
        "arbiter_work_orders_created",
        contract.turn_id,
        {
            "review_session_id": summary.review_session_id,
            "decision_id": contract.decision_id,
            "work_order_ids": [work_order.work_order_id for work_order in work_orders],
            "decomposition": decomposition.to_dict(),
            "memory_context_refs": contract.memory_context_refs,
        },
    )
    return work_orders


def _work_order_from_decomposed_node(contract: DecisionContract, summary: ReviewSummary, node: DecomposedTaskNode) -> WorkOrder:
    execution_preference = _execution_preference_from_node(node)
    node_candidate_tools = _candidate_tools_for_node(summary, node)
    tool_reliability = _candidate_tool_reliability(node_candidate_tools, contract.memory_context_refs)
    selected_tool = str((tool_reliability[0].get("tool") if tool_reliability else "") or node.tool or "").strip()
    selected_reliability = tool_reliability[0] if tool_reliability else {}
    tool_policy = ToolPolicy(
        allowed_tools=[selected_tool] if selected_tool else [],
        denied_tools=list(contract.tool_policy.denied_tools),
        risk_level=node.risk_level,
        requires_confirmation=contract.tool_policy.requires_confirmation,
        confirmation_reason=contract.tool_policy.confirmation_reason,
        verification_required=contract.tool_policy.verification_required,
    )
    inputs = {
        **dict(node.inputs or {}),
        "tool": selected_tool,
        "planned_tool": node.tool,
        "candidate_tools_for_node": node_candidate_tools,
        "capability": node.capability,
        "work_order_input": node.work_order_input,
        "review_session_id": summary.review_session_id,
        "memory_context_refs": contract.memory_context_refs,
        "decomposition_node_id": node.node_id,
        "depends_on": list(node.depends_on),
        "recovery_policy": dict(node.recovery_policy),
        "execution_preference": execution_preference,
        "candidate_tool_reliability": tool_reliability,
        "selected_tool_reliability": selected_reliability,
    }
    if selected_tool and selected_tool != node.tool:
        inputs["tool_selection_reason"] = "memory_growth_reliability_preferred_alternate_tool"
    if "governance_policy" not in inputs:
        inputs["governance_policy"] = {
            "reason": "not_attached_by_decomposer",
            "execution_mode": "normal",
        }
    return WorkOrder(
        work_order_id=_new_id("work"),
        decision_id=contract.decision_id,
        role_agent=node.role_agent,
        task=node.goal,
        inputs=inputs,
        tool_policy=tool_policy,
        expected_outputs=["execution_report", "observable_evidence"],
        verification_criteria=list(node.verification_criteria or contract.verification_criteria),
        status="pending",
    )


def _candidate_tools_for_node(summary: ReviewSummary, node: DecomposedTaskNode) -> list[str]:
    out: list[str] = []
    for tool in (node.tool,):
        clean = str(tool or "").strip()
        if clean:
            out.append(clean)
    recovery_paths = node.recovery_policy.get("capability_recovery_paths")
    has_node_recovery_paths = isinstance(recovery_paths, list) and bool(recovery_paths)
    if not has_node_recovery_paths:
        for tool in summary.candidate_tools or []:
            clean = str(tool or "").strip()
            if clean and _is_executable_node_tool_candidate(clean, summary, node):
                out.append(clean)
    if isinstance(recovery_paths, list):
        for path in recovery_paths:
            if not isinstance(path, dict):
                continue
            if not _recovery_path_matches_node_tool(path, node.tool):
                continue
            for key in ("next_tool", "tool", "fallback_tool", "alternate_tool"):
                clean = str(path.get(key) or "").strip()
                if clean:
                    out.append(clean)
    return list(dict.fromkeys(out))


def _is_executable_node_tool_candidate(tool: str, summary: ReviewSummary, node: DecomposedTaskNode) -> bool:
    clean = str(tool or "").strip()
    if not clean:
        return False
    tail = clean.split(":", 1)[-1].strip().lower()
    non_executable = {
        str(summary.task_type or "").strip().lower(),
        str(summary.top_intent or "").strip().lower(),
        str(node.capability or "").strip().lower(),
        str(node.inputs.get("intent") or "").strip().lower(),
    }
    if tail in {item for item in non_executable if item}:
        return False
    if clean.startswith(("mcp:", "core:")):
        return True
    return bool(re.match(r"^[a-z][a-z0-9_.-]+:[a-z0-9_.-]+$", clean, flags=re.I))


def _recovery_path_matches_node_tool(path: dict[str, Any], node_tool: str) -> bool:
    node = str(node_tool or "").strip().lower()
    if not node:
        return False
    explicit_tool = str(path.get("current_tool") or path.get("from_tool") or path.get("source_tool") or "").strip().lower()
    if explicit_tool and explicit_tool == node:
        return True
    next_tool = str(path.get("next_tool") or path.get("tool") or path.get("fallback_tool") or path.get("alternate_tool") or "").strip().lower()
    if next_tool and next_tool == node:
        return True
    when = str(path.get("when") or path.get("failure_reason") or path.get("condition") or "").strip().lower()
    tail = node.split(":", 1)[-1]
    parts = [part for part in re.split(r"[^a-z0-9]+", tail) if part]
    if tail and tail in when:
        return True
    return any(part and len(part) >= 4 and part in when for part in parts)


def _annotate_success_execution_order_advice(work_orders: list[WorkOrder]) -> None:
    for work_order in work_orders:
        preference = work_order.inputs.get("execution_preference")
        if not isinstance(preference, dict):
            continue
        chain = preference.get("preferred_work_order_chain")
        if not isinstance(chain, list) or not chain:
            continue
        matched_index, matched_step = _match_preferred_chain_step(work_order, chain)
        advice = {
            "mode": "non_destructive",
            "reason": "preferred_work_order_chain_match" if matched_step else "preferred_work_order_chain_not_matched",
            "matched": bool(matched_step),
            "matched_step": matched_step,
            "matched_index": matched_index,
            "chain_length": len(chain),
        }
        preference["execution_order_advice"] = advice
        work_order.inputs["execution_order_advice"] = advice


def _match_preferred_chain_step(work_order: WorkOrder, chain: list[Any]) -> tuple[int | None, str]:
    hay = " ".join(
        str(part or "")
        for part in (
            work_order.role_agent,
            work_order.task,
            work_order.inputs.get("tool"),
            work_order.inputs.get("capability"),
            work_order.inputs.get("decomposition_role"),
            work_order.inputs.get("intent"),
            work_order.inputs.get("work_order_input"),
        )
    ).lower()
    for index, raw_step in enumerate(chain):
        step = str(raw_step or "").strip()
        if not step:
            continue
        tokens = [token for token in re.split(r"[^a-z0-9]+", step.lower()) if token]
        if tokens and all(token in hay for token in tokens):
            return index, step
        if step.lower() in hay:
            return index, step
    return None, ""


def _candidate_tool_reliability(candidate_tools: list[str], refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not candidate_tools:
        return []
    rows: list[dict[str, Any]] = []
    for index, tool in enumerate(candidate_tools):
        tool_name = str(tool or "").strip()
        if not tool_name:
            continue
        matched_refs = _tool_matched_memory_refs(tool_name, refs)
        stats = _tool_reliability_stats(matched_refs)
        health = _tool_reliability_health(stats)
        score = _tool_reliability_score(stats, health, index)
        rows.append(
            {
                "tool": tool_name,
                "score": round(score, 4),
                "health": health,
                "matched_ref_count": len(matched_refs),
                "success_rate": stats["success_rate"],
                "use_count": stats["use_count"],
                "failure_count": stats["failure_count"],
                "last_failure_reason": stats["last_failure_reason"],
                "rank_source": "memory_growth_success_path" if matched_refs else "candidate_order",
                "original_index": index,
                "matched_memory_ids": [str(ref.get("memory_id") or "") for ref in matched_refs[:3] if str(ref.get("memory_id") or "")],
            }
        )
    rows.sort(key=lambda row: (float(row["score"]), -int(row["original_index"])), reverse=True)
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
        row["selected"] = rank == 1
    return rows


def _tool_matched_memory_refs(tool: str, refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tool_low = tool.lower()
    aliases = _tool_alias_tokens(tool_low)
    exact: list[dict[str, Any]] = []
    alias_matches: list[dict[str, Any]] = []
    for ref in refs or []:
        if not isinstance(ref, dict):
            continue
        hay = " ".join(
            str(ref.get(key) or "")
            for key in ("memory_id", "artifact_path", "preview", "relevance_reason", "source", "memory_type")
        ).lower()
        if tool_low in hay:
            exact.append(ref)
        elif any(alias and alias in hay for alias in aliases):
            alias_matches.append(ref)
    return exact if exact else alias_matches


def _tool_alias_tokens(tool: str) -> list[str]:
    tail = tool.split(":")[-1]
    parts = [part for part in re.split(r"[^a-z0-9]+", tail) if part]
    aliases = [tail.replace("_", "-"), tail.replace("-", "_"), tail]
    aliases.extend(parts)
    if "lark" in parts:
        aliases.extend(["lark", "send_lark_message", "windows_lark"])
    if "browser" in parts or "web" in parts:
        aliases.extend(["browser", "chrome", "edge"])
    if "calculator" in parts:
        aliases.extend(["calculator", "calc"])
    return list(dict.fromkeys([alias.lower() for alias in aliases if len(alias) >= 3]))


def _tool_reliability_stats(refs: list[dict[str, Any]]) -> dict[str, Any]:
    rates: list[float] = []
    use_count = 0
    failure_count = 0
    last_failure_reason = ""
    confidence = 0.0
    for ref in refs:
        confidence = max(confidence, _float(ref.get("confidence"), 0.0))
        rate = _extract_ref_float(ref, "artifact_success_rate", "memory_success_rate", "success_rate")
        if rate is not None:
            rates.append(rate)
        use_count += _extract_ref_int(ref, "artifact_use_count", "memory_use_count")
        failure_count += _extract_ref_int(ref, "artifact_failure_count", "memory_failure_count")
        last_failure_reason = last_failure_reason or _extract_ref_value(ref, "artifact_last_failure_reason", "memory_last_failure_reason")
    success_rate = max(rates) if rates else 0.0
    return {
        "confidence": confidence,
        "success_rate": round(success_rate, 3),
        "use_count": use_count,
        "failure_count": failure_count,
        "last_failure_reason": last_failure_reason,
    }


def _tool_reliability_health(stats: dict[str, Any]) -> str:
    use_count = int(stats.get("use_count") or 0)
    failure_count = int(stats.get("failure_count") or 0)
    success_rate = _float(stats.get("success_rate"), 0.0)
    if use_count >= 2 and (success_rate < 0.5 or failure_count >= 3):
        return "degraded"
    if use_count >= 2 and success_rate >= 0.75 and failure_count < 3:
        return "reliable"
    return "unproven"


def _tool_reliability_score(stats: dict[str, Any], health: str, original_index: int) -> float:
    base = 0.05 * max(0, 20 - original_index)
    confidence = _float(stats.get("confidence"), 0.0)
    success_rate = _float(stats.get("success_rate"), 0.0)
    use_count = int(stats.get("use_count") or 0)
    failure_count = int(stats.get("failure_count") or 0)
    score = base + confidence * 0.35 + success_rate * 0.5 + min(0.12, use_count * 0.012)
    if health == "reliable":
        score += 0.18
    if health == "degraded":
        score -= 0.55
    score -= min(0.22, failure_count * 0.04)
    return score


def _extract_ref_value(ref: dict[str, Any], *keys: str) -> str:
    hay = f"{ref.get('preview') or ''} {ref.get('relevance_reason') or ''}"
    for key in keys:
        match = re.search(rf"{re.escape(key.lower())}[:=]\s*([^;\s\n]+)", hay, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip().strip("`'\"")
    return ""


def _extract_ref_float(ref: dict[str, Any], *keys: str) -> float | None:
    value = _extract_ref_value(ref, *keys)
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_ref_int(ref: dict[str, Any], *keys: str) -> int:
    value = _extract_ref_value(ref, *keys)
    if not value:
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _tool_reliability_rationale(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return []
    selected = rows[0]
    if selected.get("rank_source") == "candidate_order":
        return []
    return [
        (
            "Arbiter selected candidate tool "
            f"{selected.get('tool')} using Memory Growth reliability "
            f"health={selected.get('health')} success_rate={selected.get('success_rate')}."
        )
    ]


def _execution_preference_from_node(node: DecomposedTaskNode) -> dict[str, Any]:
    preference = node.inputs.get("success_playbook_preference")
    if not isinstance(preference, dict):
        preference = node.recovery_policy.get("success_playbook_preference")
    if not isinstance(preference, dict):
        return {
            "source": "none",
            "preferred_execution_strategy": "",
            "preferred_work_order_chain": [],
            "selected_memory_id": "",
            "candidate_count": 0,
        }
    return {
        "source": str(preference.get("source") or "memory_growth_success_playbook"),
        "selection_reason": str(preference.get("selection_reason") or ""),
        "selected_memory_id": str(preference.get("selected_memory_id") or ""),
        "selected_artifact_path": str(preference.get("selected_artifact_path") or ""),
        "selected_confidence": preference.get("selected_confidence"),
        "selected_success_rate": preference.get("selected_success_rate"),
        "selected_health": str(preference.get("selected_health") or ""),
        "selected_use_count": preference.get("selected_use_count"),
        "selected_failure_count": preference.get("selected_failure_count"),
        "selected_last_failure_reason": str(preference.get("selected_last_failure_reason") or ""),
        "preferred_execution_strategy": str(preference.get("preferred_execution_strategy") or ""),
        "preferred_work_order_chain": [
            str(item)
            for item in (preference.get("preferred_work_order_chain") if isinstance(preference.get("preferred_work_order_chain"), list) else [])
            if str(item).strip()
        ],
        "candidate_count": int(preference.get("candidate_count") or 0),
    }


def _memory_context_refs(summary: ReviewSummary, limit: int = 8) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for review in summary.reviews or []:
        for evidence in review.evidence or []:
            if not isinstance(evidence, dict):
                continue
            for ref in evidence.get("memory_growth_refs") or []:
                if isinstance(ref, dict):
                    decorated = {**ref, **trust_score_detail(ref)}
                    if not should_recall_memory(decorated):
                        continue
                    refs.append(decorated)
                    if len(refs) >= limit:
                        return refs
    return refs


def _memory_rationale(refs: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    playbook_refs = [ref for ref in refs if "playbook" in str(ref.get("memory_id") or "").lower()]
    concept_refs = [ref for ref in refs if "concept" in str(ref.get("memory_id") or "").lower()]
    if playbook_refs:
        out.append(f"Arbiter considered {len(playbook_refs)} Memory Growth playbook reference(s).")
    if concept_refs:
        out.append(f"Arbiter considered {len(concept_refs)} Memory Growth concept reference(s).")
    return out


def _requires_confirmation(summary: ReviewSummary) -> bool:
    if summary.needs_clarification:
        return True
    if summary.risk_level in {RiskLevel.CRITICAL, RiskLevel.HIGH} and summary.top_intent in {"close_app", "file_operation"}:
        return True
    return False


def _goal_from_summary(summary: ReviewSummary) -> str:
    target = summary.target.get("name") if summary.target else ""
    if target:
        return f"{summary.top_intent}: {target}"
    return summary.top_intent or summary.task_type or "answer user"


def _workflow_for(summary: ReviewSummary) -> str:
    if summary.task_type == "calculator_calculate":
        return "reviewed_calculator_calculate_workflow"
    if summary.task_type == "app_control":
        return "reviewed_app_control_workflow"
    if summary.task_type == "message_delivery":
        return "reviewed_message_delivery_workflow"
    if summary.task_type == "web_research_delivery":
        return "reviewed_web_research_delivery_workflow"
    if summary.task_type == "file_operation":
        return "reviewed_file_operation_workflow"
    return "conversation_reply_workflow"


def _verification_criteria(summary: ReviewSummary) -> list[str]:
    target = summary.target.get("name") if summary.target else ""
    if summary.top_intent == "open_app":
        return [
            f"{target or 'target app'} appears in running apps or foreground window",
            "window/app state is refreshed after execution",
        ]
    if summary.top_intent == "close_app":
        return [
            f"{target or 'target app'} is no longer foreground or no longer running",
            "no unsaved-content prompt remains unhandled",
        ]
    if summary.top_intent == "switch_app":
        return [f"{target or 'target app'} becomes foreground window"]
    if summary.task_type == "calculator_calculate":
        expression = str((summary.target or {}).get("expression") or "").strip()
        return [
            f"Windows Calculator receives expression {expression}" if expression else "Windows Calculator receives the requested expression",
            "Calculator result is verified by clipboard or visual/OCR evidence",
        ]
    if summary.task_type == "message_delivery":
        return ["message target and content preview match", "send result has observable evidence"]
    if summary.task_type == "web_research_delivery":
        return [
            "search results include at least one URL",
            "fetch or browser extraction returns readable evidence",
            "summary is grounded in fetched/search evidence",
            "Lark send result has observable evidence",
        ]
    if summary.task_type == "file_operation":
        return ["file operation result matches expected path and content evidence"]
    return ["no external-world action required"]


def _executor_role(roles: list[str]) -> str:
    for role in roles:
        if role.endswith("ExecutorAgent"):
            return role
    if "MemoryWriteAgent" in roles:
        return "MemoryWriteAgent"
    return "ToolExecutionAgent"


def _task_text(summary: ReviewSummary) -> str:
    target = summary.target.get("name") if summary.target else ""
    if target:
        return f"{summary.top_intent} {target}"
    return summary.top_intent or summary.task_type


def _work_order_input_for(summary: ReviewSummary, tool: str) -> str:
    target = summary.target or {}
    if tool == "mcp:windows_calculator_calculate":
        expression = str(target.get("expression") or "").strip()
        return json.dumps({"expression": expression, "expected": ""}, ensure_ascii=False)
    if tool == "mcp:windows_lark_send_message":
        recipients = target.get("recipients") if isinstance(target.get("recipients"), list) else []
        message = str(target.get("message") or "").strip()
        return json.dumps(
            {
                "recipients_json": json.dumps([str(x) for x in recipients if str(x).strip()], ensure_ascii=False),
                "message": message,
                "max_attempts": 2,
            },
            ensure_ascii=False,
        )
    if tool == "core:fs_read":
        path = str(target.get("path") or target.get("name") or "").strip()
        return json.dumps({"path": path}, ensure_ascii=False)
    if tool == "core:fs_write":
        path = str(target.get("path") or target.get("name") or "").strip()
        content = str(target.get("content") or "").strip()
        return json.dumps({"path": path, "content": content}, ensure_ascii=False)
    if tool == "mcp:windows_file_open":
        path = str(target.get("path") or target.get("name") or "").strip()
        return json.dumps({"path": path}, ensure_ascii=False)
    if tool == "mcp:windows_file_reveal_in_explorer":
        path = str(target.get("path") or target.get("name") or "").strip()
        return json.dumps({"path": path}, ensure_ascii=False)
    return ""
