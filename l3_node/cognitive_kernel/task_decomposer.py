"""TaskDecomposerAgent for converting a DecisionContract into DAG nodes.

The ReviewBoard decides what the user is asking for and the Arbiter authorizes
the boundary. The decomposer is the first place that turns a goal into concrete
ordered steps. It is deliberately deterministic here; future Skill/MCP
manifests can contribute extra decomposition rules without changing the
Dispatcher.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .contracts import DecisionContract, ReviewSummary, RiskLevel
from .ledger import append_event


def _new_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time())}_{uuid.uuid4().hex[:10]}"


@dataclass(slots=True)
class DecomposedTaskNode:
    node_id: str
    goal: str
    role_agent: str
    tool: str = ""
    capability: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    work_order_input: str = ""
    depends_on: list[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    verification_criteria: list[str] = field(default_factory=list)
    recovery_policy: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "goal": self.goal,
            "role_agent": self.role_agent,
            "tool": self.tool,
            "capability": self.capability,
            "inputs": self.inputs,
            "work_order_input": self.work_order_input,
            "depends_on": list(self.depends_on),
            "risk_level": self.risk_level.value,
            "verification_criteria": list(self.verification_criteria),
            "recovery_policy": dict(self.recovery_policy),
        }


@dataclass(slots=True)
class TaskDecompositionPlan:
    turn_id: str
    decision_id: str
    goal: str
    nodes: list[DecomposedTaskNode] = field(default_factory=list)
    rationale: list[str] = field(default_factory=list)
    available_capabilities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "decision_id": self.decision_id,
            "goal": self.goal,
            "nodes": [node.to_dict() for node in self.nodes],
            "rationale": list(self.rationale),
            "available_capabilities": list(self.available_capabilities),
        }


def decompose_task(
    *,
    contract: DecisionContract,
    summary: ReviewSummary,
    available_capabilities: list[str] | None = None,
) -> TaskDecompositionPlan:
    capabilities = list(available_capabilities or summary.candidate_tools or contract.tool_policy.allowed_tools or [])
    target = summary.target or {}
    nodes: list[DecomposedTaskNode]
    rationale: list[str]

    primary_tool = _primary_tool(contract)
    intent = str(summary.top_intent or "").strip()
    task_type = str(contract.task_type or summary.task_type or "").strip()

    metadata_nodes, metadata_rationale = _decompose_from_capability_metadata(
        contract=contract,
        summary=summary,
        target=target,
        primary_tool=primary_tool,
    )
    if metadata_nodes:
        nodes, rationale = metadata_nodes, metadata_rationale
    elif task_type == "message_delivery" or intent == "message_send" or primary_tool == "mcp:windows_lark_send_message":
        nodes, rationale = _decompose_message_delivery(contract, summary, target)
    elif task_type == "calculator_calculate" or intent == "calculator_calculate" or primary_tool == "mcp:windows_calculator_calculate":
        nodes, rationale = _decompose_calculator(contract, summary, target)
    elif task_type == "app_control":
        nodes, rationale = _decompose_single_tool(contract, summary, target)
    elif task_type == "file_operation":
        nodes, rationale = _decompose_single_tool(contract, summary, target)
    else:
        nodes, rationale = _decompose_single_tool(contract, summary, target)

    plan = TaskDecompositionPlan(
        turn_id=contract.turn_id,
        decision_id=contract.decision_id,
        goal=contract.goal,
        nodes=nodes,
        rationale=rationale,
        available_capabilities=capabilities,
    )
    append_event("task_decomposition_finished", contract.turn_id, plan.to_dict())
    return plan


def _decompose_from_capability_metadata(
    *,
    contract: DecisionContract,
    summary: ReviewSummary,
    target: dict[str, Any],
    primary_tool: str,
) -> tuple[list[DecomposedTaskNode], list[str]]:
    descriptor = _selected_capability_descriptor(summary, primary_tool)
    if not descriptor:
        return [], []
    metadata = descriptor.get("metadata") if isinstance(descriptor.get("metadata"), dict) else {}
    decomposition = metadata.get("decomposition") if isinstance(metadata.get("decomposition"), dict) else {}
    raw_nodes = decomposition.get("nodes") if isinstance(decomposition.get("nodes"), list) else []
    if not raw_nodes:
        return [], []
    nodes: list[DecomposedTaskNode] = []
    alias_to_id: dict[str, str] = {}
    for index, raw in enumerate(raw_nodes):
        if not isinstance(raw, dict):
            continue
        alias = str(raw.get("id") or raw.get("node_id") or f"manifest_step_{index + 1}").strip()
        node_id = _new_id(alias or "manifest_step")
        alias_to_id[alias] = node_id
        depends_on = [
            alias_to_id.get(str(dep), str(dep))
            for dep in (raw.get("depends_on") if isinstance(raw.get("depends_on"), list) else [])
        ]
        node_tool = _render_template(raw.get("tool") or primary_tool, contract=contract, summary=summary, target=target)
        node_inputs = _render_jsonish(raw.get("inputs") or {}, contract=contract, summary=summary, target=target)
        work_order_input = raw.get("work_order_input")
        if isinstance(work_order_input, (dict, list)):
            work_order_input = json.dumps(
                _render_jsonish(work_order_input, contract=contract, summary=summary, target=target),
                ensure_ascii=False,
            )
        else:
            work_order_input = _render_template(work_order_input or "", contract=contract, summary=summary, target=target)
        nodes.append(
            DecomposedTaskNode(
                node_id=node_id,
                goal=_render_template(raw.get("goal") or summary.top_intent or contract.goal, contract=contract, summary=summary, target=target),
                role_agent=_render_template(raw.get("role_agent") or _executor_role(contract.selected_roles), contract=contract, summary=summary, target=target),
                tool=node_tool,
                capability=_render_template(raw.get("capability") or descriptor.get("id") or node_tool, contract=contract, summary=summary, target=target),
                inputs=node_inputs if isinstance(node_inputs, dict) else {},
                work_order_input=work_order_input,
                depends_on=depends_on,
                risk_level=_risk_from_value(raw.get("risk_level"), contract.risk_level),
                verification_criteria=[
                    _render_template(item, contract=contract, summary=summary, target=target)
                    for item in (raw.get("verification_criteria") if isinstance(raw.get("verification_criteria"), list) else contract.verification_criteria)
                ],
                recovery_policy=_render_jsonish(raw.get("recovery_policy") or {}, contract=contract, summary=summary, target=target)
                if isinstance(raw.get("recovery_policy") or {}, dict)
                else {},
            )
        )
    if not nodes:
        return [], []
    return nodes, [f"TaskDecomposerAgent used capability metadata decomposition from {descriptor.get('id') or 'manifest'}."]


def _selected_capability_descriptor(summary: ReviewSummary, primary_tool: str) -> dict[str, Any]:
    candidates = summary.capability_candidates or []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        descriptor = candidate.get("descriptor") if isinstance(candidate.get("descriptor"), dict) else {}
        if not descriptor:
            continue
        if primary_tool and descriptor.get("id") == primary_tool:
            return descriptor
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        descriptor = candidate.get("descriptor") if isinstance(candidate.get("descriptor"), dict) else {}
        if descriptor:
            return descriptor
    return {}


def _render_jsonish(value: Any, *, contract: DecisionContract, summary: ReviewSummary, target: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return _render_template(value, contract=contract, summary=summary, target=target)
    if isinstance(value, dict):
        return {str(k): _render_jsonish(v, contract=contract, summary=summary, target=target) for k, v in value.items()}
    if isinstance(value, list):
        return [_render_jsonish(v, contract=contract, summary=summary, target=target) for v in value]
    return value


def _render_template(value: Any, *, contract: DecisionContract, summary: ReviewSummary, target: dict[str, Any]) -> str:
    text = str(value or "")
    replacements = {
        "$goal": contract.goal,
        "$intent": summary.top_intent,
        "$task_type": contract.task_type or summary.task_type,
        "$tool": _primary_tool(contract),
        "$target.name": str(target.get("name") or target.get("app") or ""),
        "$target.app": str(target.get("app") or target.get("name") or ""),
        "$target.path": str(target.get("path") or target.get("name") or ""),
        "$target.expression": str(target.get("expression") or ""),
        "$target.message": str(target.get("message") or ""),
    }
    if "$target.recipients_json" in text:
        recipients = target.get("recipients") if isinstance(target.get("recipients"), list) else []
        replacements["$target.recipients_json"] = json.dumps([str(x) for x in recipients if str(x).strip()], ensure_ascii=False)
    for key, replacement in replacements.items():
        text = text.replace(key, replacement)
    return text


def _risk_from_value(value: Any, default: RiskLevel) -> RiskLevel:
    try:
        return RiskLevel(str(value or default.value).lower())
    except Exception:
        return default


def _decompose_message_delivery(
    contract: DecisionContract,
    summary: ReviewSummary,
    target: dict[str, Any],
) -> tuple[list[DecomposedTaskNode], list[str]]:
    app_name = str(target.get("app") or "Lark").strip() or "Lark"
    send_tool = _primary_tool(contract)
    open_node = DecomposedTaskNode(
        node_id=_new_id("decomp_open_app"),
        goal=f"Open or focus {app_name} before sending the message",
        role_agent="AppControlExecutorAgent",
        tool="mcp:windows_open_app",
        capability="app_control.open_or_focus",
        inputs={
            "tool": "mcp:windows_open_app",
            "intent": "open_app",
            "target": {"type": "app", "name": app_name, "source": "task_decomposer"},
            "decomposition_role": "prepare_message_app",
        },
        work_order_input=json.dumps({"app": app_name}, ensure_ascii=False),
        risk_level=RiskLevel.LOW,
        verification_criteria=[f"{app_name} is running or focused before message send"],
        recovery_policy={"strategy": "retry_then_switch_window", "max_attempts": 2},
    )
    send_node = DecomposedTaskNode(
        node_id=_new_id("decomp_send_message"),
        goal="Send the requested message to the resolved recipient(s)",
        role_agent="MessageExecutorAgent",
        tool=send_tool,
        capability="message.send",
        inputs={
            "tool": send_tool,
            "intent": summary.top_intent,
            "target": target,
            "decomposition_role": "send_message",
        },
        work_order_input=_work_order_input_for(summary, send_tool),
        depends_on=[open_node.node_id],
        risk_level=contract.risk_level,
        verification_criteria=list(contract.verification_criteria or ["message send evidence is visible"]),
        recovery_policy={"strategy": "preview_verify_retry", "max_attempts": 3},
    )
    return [open_node, send_node], ["TaskDecomposerAgent split message delivery into app focus and message send."]


def _decompose_calculator(
    contract: DecisionContract,
    summary: ReviewSummary,
    target: dict[str, Any],
) -> tuple[list[DecomposedTaskNode], list[str]]:
    calculate_tool = _primary_tool(contract)
    open_node = DecomposedTaskNode(
        node_id=_new_id("decomp_open_calculator"),
        goal="Open or focus Windows Calculator before entering the expression",
        role_agent="AppControlExecutorAgent",
        tool="mcp:windows_open_app",
        capability="app_control.open_or_focus",
        inputs={
            "tool": "mcp:windows_open_app",
            "intent": "open_app",
            "target": {"type": "app", "name": "Calculator", "source": "task_decomposer"},
            "decomposition_role": "prepare_calculator",
        },
        work_order_input=json.dumps({"app": "Calculator"}, ensure_ascii=False),
        risk_level=RiskLevel.LOW,
        verification_criteria=["Calculator is running or focused"],
        recovery_policy={"strategy": "retry_open_then_focus", "max_attempts": 2},
    )
    calc_node = DecomposedTaskNode(
        node_id=_new_id("decomp_calculate"),
        goal="Enter the expression and verify the result",
        role_agent="AppControlExecutorAgent",
        tool=calculate_tool,
        capability="calculator.calculate",
        inputs={
            "tool": calculate_tool,
            "intent": summary.top_intent,
            "target": target,
            "decomposition_role": "calculate_expression",
        },
        work_order_input=_work_order_input_for(summary, calculate_tool),
        depends_on=[open_node.node_id],
        risk_level=contract.risk_level,
        verification_criteria=list(contract.verification_criteria or ["calculator result is verified"]),
        recovery_policy={"strategy": "clear_and_retype_expression", "max_attempts": 3},
    )
    return [open_node, calc_node], ["TaskDecomposerAgent split calculator task into app focus and expression calculation."]


def _decompose_single_tool(
    contract: DecisionContract,
    summary: ReviewSummary,
    target: dict[str, Any],
) -> tuple[list[DecomposedTaskNode], list[str]]:
    tool = _primary_tool(contract)
    if not tool:
        return [], ["TaskDecomposerAgent found no executable tool in DecisionContract."]
    node = DecomposedTaskNode(
        node_id=_new_id("decomp_step"),
        goal=_task_text(summary),
        role_agent=_executor_role(contract.selected_roles),
        tool=tool,
        capability=tool,
        inputs={
            "tool": tool,
            "intent": summary.top_intent,
            "target": target,
            "decomposition_role": "single_step",
        },
        work_order_input=_work_order_input_for(summary, tool),
        risk_level=contract.risk_level,
        verification_criteria=list(contract.verification_criteria or []),
        recovery_policy={"strategy": "capability_playbook_then_retry", "max_attempts": 2},
    )
    return [node], ["TaskDecomposerAgent kept the task as a single executable step."]


def _primary_tool(contract: DecisionContract) -> str:
    return str(contract.tool_policy.allowed_tools[0] if contract.tool_policy.allowed_tools else "").strip()


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
    if tool == "mcp:windows_open_app":
        app = str(target.get("name") or target.get("app") or "").strip()
        return json.dumps({"app": app}, ensure_ascii=False)
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
    if tool in {"mcp:windows_file_open", "mcp:windows_file_reveal_in_explorer"}:
        path = str(target.get("path") or target.get("name") or "").strip()
        return json.dumps({"path": path}, ensure_ascii=False)
    return ""
