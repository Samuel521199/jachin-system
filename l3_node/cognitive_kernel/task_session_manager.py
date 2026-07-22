"""Task-session helpers for cross-turn voice/text operation flows.

This layer is intentionally small.  It does not execute tools; it gives the
desktop UI and voice gate a common view of an in-progress task so a follow-up
like ``Neil`` / ``A`` / ``1`` can resume the previous WorkOrder instead of
being treated as an unrelated chat turn or background noise.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .contracts import DecisionContract, WorkOrder
from .ledger import append_event
from .pending_confirmation import load_pending_confirmation


@dataclass(slots=True)
class TaskSessionStep:
    label: str
    status: str = "pending"
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TaskSessionUiProtocol:
    session_id: str
    status: str
    title: str
    current_step: str = ""
    task_type: str = ""
    role_agent: str = ""
    tool: str = ""
    decision_basis: list[str] = field(default_factory=list)
    steps: list[TaskSessionStep] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "task_session",
            "session_id": self.session_id,
            "status": self.status,
            "title": self.title,
            "current_step": self.current_step,
            "task_type": self.task_type,
            "role_agent": self.role_agent,
            "tool": self.tool,
            "decision_basis": list(self.decision_basis),
            "steps": [step.to_dict() for step in self.steps],
            "evidence": dict(self.evidence),
        }


def attach_task_session_ui_protocol(
    text: str,
    *,
    status: str,
    title: str = "",
    current_step: str = "",
    contract: DecisionContract | None = None,
    work_order: WorkOrder | None = None,
    decision_basis: list[str] | None = None,
    steps: list[dict[str, Any] | TaskSessionStep] | None = None,
    evidence: dict[str, Any] | None = None,
) -> str:
    protocol = build_task_session_ui_protocol(
        status=status,
        title=title,
        current_step=current_step,
        contract=contract,
        work_order=work_order,
        decision_basis=decision_basis,
        steps=steps,
        evidence=evidence,
    )
    marker = "<!-- jachin-ui:task-session " + json.dumps(
        protocol.to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
    ) + " -->"
    _append_task_session_event(protocol, contract=contract)
    return f"{str(text or '').rstrip()}\n\n{marker}"


def build_task_session_ui_protocol(
    *,
    status: str,
    title: str = "",
    current_step: str = "",
    contract: DecisionContract | None = None,
    work_order: WorkOrder | None = None,
    decision_basis: list[str] | None = None,
    steps: list[dict[str, Any] | TaskSessionStep] | None = None,
    evidence: dict[str, Any] | None = None,
) -> TaskSessionUiProtocol:
    task_type = str(getattr(contract, "task_type", "") or "")
    role_agent = str(getattr(work_order, "role_agent", "") or "")
    tool = str((getattr(work_order, "inputs", {}) or {}).get("tool") or "")
    session_id = str(getattr(contract, "decision_id", "") or getattr(work_order, "work_order_id", "") or f"task_{int(time.time() * 1000)}")
    normalized_steps = [_coerce_step(step) for step in (steps or _default_steps(status=status, work_order=work_order))]
    return TaskSessionUiProtocol(
        session_id=session_id,
        status=status,
        title=title or _title_for(contract=contract, work_order=work_order),
        current_step=current_step or _current_step_for(status),
        task_type=task_type,
        role_agent=role_agent,
        tool=tool,
        decision_basis=list(decision_basis or _default_decision_basis(contract=contract, work_order=work_order)),
        steps=normalized_steps,
        evidence=dict(evidence or {}),
    )


def active_task_session_context(*, session_id: str = "", channel: str = "") -> dict[str, Any]:
    """Return a compact pending-task context used by voice gating."""

    pending = load_pending_confirmation(session_id=session_id, channel=channel)
    if pending is None:
        return {"active": False}
    work_order = pending.work_order
    contract = pending.contract
    target = work_order.inputs.get("target") if isinstance(work_order.inputs.get("target"), dict) else {}
    payload = _work_order_payload_obj(work_order)
    recipients = target.get("recipients") if isinstance(target.get("recipients"), list) else []
    message = str(target.get("message") or payload.get("message") or "").strip()
    tool = str(work_order.inputs.get("tool") or "").strip()
    missing_slots: list[str] = []
    if "send" in tool.lower() or "message" in tool.lower() or "lark" in tool.lower():
        if not recipients and not payload.get("recipient") and not payload.get("recipients_json"):
            missing_slots.append("recipient")
        if not message:
            missing_slots.append("message")
    return {
        "active": True,
        "session_key": pending.session_key,
        "task_type": contract.task_type,
        "work_order_id": work_order.work_order_id,
        "role_agent": work_order.role_agent,
        "tool": tool,
        "missing_slots": missing_slots,
        "target": target,
        "saved_at_ms": pending.saved_at_ms,
        "expires_at_ms": pending.expires_at_ms,
    }


def _default_steps(*, status: str, work_order: WorkOrder | None) -> list[TaskSessionStep]:
    role = str(getattr(work_order, "role_agent", "") or "RoleExecutor")
    return [
        TaskSessionStep("Goal Interpreter", "done", "识别用户目标和缺失信息"),
        TaskSessionStep("Task Session", "running" if status in {"waiting_user", "running"} else "done", "保存跨轮任务上下文"),
        TaskSessionStep(role, "pending" if status == "waiting_user" else ("done" if status == "done" else status), "等待补槽或执行工具"),
        TaskSessionStep("Verification", "pending" if status in {"waiting_user", "running"} else status, "根据工具返回和证据校验结果"),
    ]


def _default_decision_basis(*, contract: DecisionContract | None, work_order: WorkOrder | None) -> list[str]:
    out: list[str] = []
    if contract is not None:
        out.append(f"intent={contract.task_type}")
        out.append(f"risk={contract.risk_level.value}")
        if contract.tool_policy.requires_confirmation:
            out.append("requires_confirmation=true")
    if work_order is not None:
        tool = str(work_order.inputs.get("tool") or "").strip()
        if tool:
            out.append(f"tool={tool}")
        if work_order.role_agent:
            out.append(f"role={work_order.role_agent}")
    return out


def _title_for(*, contract: DecisionContract | None, work_order: WorkOrder | None) -> str:
    if work_order is not None:
        if work_order.role_agent == "MessageExecutorAgent":
            return "消息发送任务"
        if work_order.role_agent == "AppControlExecutorAgent":
            return "应用控制任务"
        if work_order.role_agent == "FileExecutorAgent":
            return "文件操作任务"
    if contract is not None and contract.task_type:
        return contract.task_type
    return "OS 任务"


def _current_step_for(status: str) -> str:
    return {
        "waiting_user": "等待补充信息",
        "running": "正在执行",
        "done": "已完成并校验",
        "failed": "执行失败，等待恢复",
        "dropped": "已作为噪声忽略",
    }.get(status, status or "处理中")


def _coerce_step(value: dict[str, Any] | TaskSessionStep) -> TaskSessionStep:
    if isinstance(value, TaskSessionStep):
        return value
    return TaskSessionStep(
        label=str(value.get("label") or value.get("name") or "step"),
        status=str(value.get("status") or "pending"),
        detail=str(value.get("detail") or value.get("reason") or ""),
    )


def _work_order_payload_obj(work_order: WorkOrder) -> dict[str, Any]:
    raw = work_order.inputs.get("work_order_input")
    if isinstance(raw, str) and raw.strip():
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict):
                merged = dict(work_order.inputs or {})
                merged.update(obj)
                return merged
        except Exception:
            pass
    return dict(work_order.inputs or {})


def _append_task_session_event(protocol: TaskSessionUiProtocol, *, contract: DecisionContract | None = None) -> None:
    try:
        append_event(
            "task_session_ui_protocol_emitted",
            getattr(contract, "turn_id", "") or protocol.session_id,
            protocol.to_dict(),
        )
    except Exception:
        pass
