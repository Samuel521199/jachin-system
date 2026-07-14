"""Complex task DAG model for the Memory-first Cognitive Kernel."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .contracts import DecisionContract, WorkOrder
from .ledger import append_event
from .paths import kernel_home

TaskNodeStatus = Literal["pending", "ready", "running", "done", "failed", "blocked", "cancelled"]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time())}_{uuid.uuid4().hex[:10]}"


@dataclass(slots=True)
class TaskDagNode:
    node_id: str
    title: str
    role_agent: str = ""
    tool: str = ""
    capability: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    work_order_input: str = ""
    depends_on: list[str] = field(default_factory=list)
    risk_level: str = "low"
    verification_criteria: list[str] = field(default_factory=list)
    recovery_policy: dict[str, Any] = field(default_factory=dict)
    rollback_hint: str = ""
    status: TaskNodeStatus = "pending"
    work_order_id: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "title": self.title,
            "role_agent": self.role_agent,
            "tool": self.tool,
            "capability": self.capability,
            "inputs": dict(self.inputs),
            "work_order_input": self.work_order_input,
            "depends_on": list(self.depends_on),
            "risk_level": self.risk_level,
            "verification_criteria": list(self.verification_criteria),
            "recovery_policy": dict(self.recovery_policy),
            "rollback_hint": self.rollback_hint,
            "status": self.status,
            "work_order_id": self.work_order_id,
            "evidence": list(self.evidence),
        }


@dataclass(slots=True)
class TaskDag:
    dag_id: str
    turn_id: str
    goal: str
    status: Literal["planned", "running", "done", "failed", "blocked", "cancelled"] = "planned"
    created_at_ms: int = 0
    updated_at_ms: int = 0
    nodes: list[TaskDagNode] = field(default_factory=list)
    background_task_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dag_id": self.dag_id,
            "turn_id": self.turn_id,
            "goal": self.goal,
            "status": self.status,
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
            "nodes": [node.to_dict() for node in self.nodes],
            "background_task_id": self.background_task_id,
            "metadata": dict(self.metadata),
        }


def create_task_dag_from_work_orders(
    *,
    turn_id: str,
    goal: str,
    contract: DecisionContract | None = None,
    work_orders: list[WorkOrder] | None = None,
    background: bool = False,
) -> TaskDag:
    now = _now_ms()
    nodes: list[TaskDagNode] = []
    prior = ""
    for index, work_order in enumerate(work_orders or []):
        tool = str(work_order.inputs.get("tool") or "")
        node = TaskDagNode(
            node_id=_new_id(f"node{index + 1}"),
            title=work_order.task or f"Execute {tool}",
            role_agent=work_order.role_agent,
            tool=tool,
            capability=str(work_order.inputs.get("capability") or tool),
            inputs=dict(work_order.inputs or {}),
            work_order_input=str(work_order.inputs.get("work_order_input") or ""),
            depends_on=[prior] if prior else [],
            risk_level=getattr(work_order.tool_policy.risk_level, "value", str(work_order.tool_policy.risk_level or "low")),
            verification_criteria=list(work_order.verification_criteria or []),
            recovery_policy=dict(work_order.inputs.get("recovery_policy") or {}),
            status="ready" if not prior else "pending",
            work_order_id=work_order.work_order_id,
        )
        nodes.append(node)
        prior = node.node_id
    if not nodes:
        nodes.append(
            TaskDagNode(
                node_id=_new_id("node1"),
                title=goal[:120] or "Plan task",
                role_agent="ConversationAgent",
                verification_criteria=["user-facing plan generated"],
                status="ready",
            )
        )
    dag = TaskDag(
        dag_id=_new_id("dag"),
        turn_id=turn_id,
        goal=goal,
        created_at_ms=now,
        updated_at_ms=now,
        nodes=nodes,
        background_task_id=_new_id("bg") if background else "",
        metadata={
            "decision_id": contract.decision_id if contract else "",
            "task_type": contract.task_type if contract else "",
            "selected_workflow": contract.selected_workflow if contract else "",
        },
    )
    save_task_dag(dag)
    append_event("task_dag_created", turn_id, dag.to_dict())
    return dag


def save_task_dag(dag: TaskDag) -> Path:
    dag.updated_at_ms = _now_ms()
    path = _dag_path(dag.dag_id)
    path.write_text(json.dumps(dag.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    _append_index(dag)
    return path


def load_task_dag(dag_id: str) -> TaskDag | None:
    path = _dag_path(dag_id)
    if not path.exists():
        return None
    try:
        return _dag_from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def list_task_dags(limit: int = 50) -> list[TaskDag]:
    root = _dag_dir()
    dags = []
    for path in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        dag = load_task_dag(path.stem)
        if dag:
            dags.append(dag)
        if len(dags) >= limit:
            break
    return dags


def update_node_status(dag_id: str, node_id: str, status: TaskNodeStatus, evidence: dict[str, Any] | None = None) -> TaskDag | None:
    dag = load_task_dag(dag_id)
    if dag is None:
        return None
    for node in dag.nodes:
        if node.node_id == node_id:
            node.status = status
            if evidence:
                node.evidence.append(evidence)
    _promote_ready_nodes(dag)
    _refresh_dag_status(dag)
    save_task_dag(dag)
    append_event("task_dag_node_updated", dag.turn_id, {"dag_id": dag_id, "node_id": node_id, "status": status})
    return dag


def ready_nodes(dag: TaskDag) -> list[TaskDagNode]:
    _promote_ready_nodes(dag)
    return [node for node in dag.nodes if node.status == "ready"]


def _promote_ready_nodes(dag: TaskDag) -> None:
    done = {node.node_id for node in dag.nodes if node.status == "done"}
    for node in dag.nodes:
        if node.status == "pending" and all(dep in done for dep in node.depends_on):
            node.status = "ready"


def _refresh_dag_status(dag: TaskDag) -> None:
    statuses = {node.status for node in dag.nodes}
    if any(s == "failed" for s in statuses):
        dag.status = "failed"
    elif any(s == "blocked" for s in statuses):
        dag.status = "blocked"
    elif all(s == "done" for s in statuses):
        dag.status = "done"
    elif any(s in {"running", "ready"} for s in statuses):
        dag.status = "running"
    else:
        dag.status = "planned"


def _dag_dir() -> Path:
    path = kernel_home() / "tasks" / "dags"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _dag_path(dag_id: str) -> Path:
    return _dag_dir() / f"{dag_id}.json"


def _append_index(dag: TaskDag) -> None:
    path = kernel_home() / "tasks" / "task_dag_index.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts_ms": _now_ms(), **dag.to_dict()}, ensure_ascii=False) + "\n")


def _dag_from_dict(obj: dict[str, Any]) -> TaskDag:
    nodes = [TaskDagNode(**_node_defaults(node)) for node in obj.get("nodes", []) if isinstance(node, dict)]
    return TaskDag(
        dag_id=str(obj.get("dag_id") or ""),
        turn_id=str(obj.get("turn_id") or ""),
        goal=str(obj.get("goal") or ""),
        status=obj.get("status") or "planned",
        created_at_ms=int(obj.get("created_at_ms") or 0),
        updated_at_ms=int(obj.get("updated_at_ms") or 0),
        nodes=nodes,
        background_task_id=str(obj.get("background_task_id") or ""),
        metadata=obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {},
    )


def _node_defaults(node: dict[str, Any]) -> dict[str, Any]:
    node.setdefault("capability", node.get("tool") or "")
    node.setdefault("inputs", {})
    node.setdefault("risk_level", "low")
    node.setdefault("recovery_policy", {})
    return node
