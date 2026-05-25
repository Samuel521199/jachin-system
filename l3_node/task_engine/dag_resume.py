"""
DAG 轻量续跑引擎（AO）

原理：
1. 从 hook_events.sqlite3 查询目标 run_id 已完成的 HOOK_ON_TASK_NODE_DONE 事件，
   提取 node_id 集合（「已完成节点」）。
2. 读取当前 active.json TaskDAG，找出 status 为 pending/running 且不在
   「已完成节点」集合中的节点（「待续跑节点」）。
3. 生成 DagResumeResult，包括：
   - 已完成节点列表
   - 待续跑节点列表（含 node_id、title、description）
   - 可供 Agent 直接消费的「续跑意图」字符串

HTTP 端点（l3_node/http_server.py 接入）：
    POST /api/v1/registry/dag-resume
    Body: { "run_id": "...", "dry_run": true }
    Response: DagResumeResult (JSON)

环境变量：
    JACHIN_PERSIST_HOOKS=1   必须开启才有 hook_events 数据
    JACHIN_DAG_RESUME_LIMIT  单次最多续跑节点数（默认 20）
"""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class DagResumeNode:
    node_id: str
    title: str
    description: str
    status: str          # pending / running / failed


@dataclass
class DagResumeResult:
    ok: bool
    run_id: str
    dag_title: str
    completed_node_ids: list[str] = field(default_factory=list)
    pending_nodes: list[DagResumeNode] = field(default_factory=list)
    resume_intent: str = ""      # 可直接传给 run_agent 的续跑意图文本
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["pending_nodes"] = [asdict(n) for n in self.pending_nodes]
        return d


# ---------------------------------------------------------------------------
# 核心逻辑
# ---------------------------------------------------------------------------

def _dag_resume_node_limit() -> int:
    raw = (os.environ.get("JACHIN_DAG_RESUME_LIMIT") or "20").strip()
    try:
        return max(1, min(100, int(raw)))
    except ValueError:
        return 20


def _check_dag_guardrails_before_resume(dag_id: str) -> str | None:
    """
    AP — 续跑前检查 DAG 级 Guardrails。
    返回 None 表示通过；返回非空字符串表示违规的 DagBrief（调用方应阻止续跑）。
    """
    try:
        from l3_node.task_engine.dag_guardrails import DagGuardrailsChecker, dag_guardrails_enabled
        if not dag_guardrails_enabled():
            return None
        checker = DagGuardrailsChecker(dag_id)
        violation = checker.check_dag_budget()
        if violation is not None:
            logger.warning("[DagResume][AP] DAG Guardrails violation: %s", violation.rule)
            return violation.dag_brief()
        return None
    except Exception as e:
        logger.debug("[DagResume][AP] guardrails check failed (skip): %s", e)
        return None


def probe_dag_resume(run_id: str) -> DagResumeResult:
    """
    探测指定 run_id 的 DAG 续跑状态。
    run_id 为空时使用 active.json 中记录的最近一次 run_id（若有）。
    不修改任何状态（只读）。
    """
    from l3_node.engine.persistent_hook_log import read_recent_hook_events
    from l3_node.task_engine.task_dag import load_task_dag_dict

    rid = (run_id or "").strip()

    # ── 1. 读取当前 active.json ─────────────────────────────────────────────
    dag = load_task_dag_dict()
    if not dag:
        return DagResumeResult(
            ok=False,
            run_id=rid,
            dag_title="",
            message="active.json 不存在或格式无效，无法续跑。",
        )

    dag_title = str(dag.get("title") or dag.get("dag_title") or "TaskDAG")

    # 若 run_id 未传，尝试从 DAG 本身获取
    if not rid:
        rid = str(dag.get("run_id") or "")

    nodes_raw: list[dict] = dag.get("nodes") or []
    if not isinstance(nodes_raw, list):
        nodes_raw = []

    # ── 2. 从 hook_events 读取已完成节点 ─────────────────────────────────
    completed_ids: set[str] = set()
    if rid:
        events = read_recent_hook_events(
            limit=500,
            hook="HOOK_ON_TASK_NODE_DONE",
            run_id=rid,
            run_id_exact=True,
        )
        for ev in events:
            meta = ev.get("meta") or {}
            # 优先从 meta 取 node_id；也支持 path 字段
            nid = str(meta.get("node_id") or meta.get("path") or "")
            if nid:
                completed_ids.add(nid)

    # ── 3. 找出待续跑节点 ─────────────────────────────────────────────────
    limit = _dag_resume_node_limit()
    pending: list[DagResumeNode] = []
    for n in nodes_raw:
        if not isinstance(n, dict):
            continue
        nid = str(n.get("node_id") or n.get("id") or "")
        status = str(n.get("status") or "pending").lower()
        # 若 hook_events 里有完成记录，跳过（无论 active.json 状态如何）
        if nid in completed_ids:
            continue
        if status in ("done", "completed", "skipped"):
            continue
        if status in ("pending", "running", "failed"):
            pending.append(DagResumeNode(
                node_id=nid,
                title=str(n.get("title") or n.get("name") or nid)[:120],
                description=str(n.get("description") or n.get("task") or "")[:300],
                status=status,
            ))
        if len(pending) >= limit:
            break

    # ── 4. 生成续跑意图文本 ────────────────────────────────────────────────
    if pending:
        lines = [f"继续执行 TaskDAG「{dag_title}」中尚未完成的节点（run_id={rid or '未知'}）："]
        for nd in pending[:10]:
            lines.append(f"- [{nd.status}] {nd.node_id}: {nd.title}")
        if len(pending) > 10:
            lines.append(f"- … 共 {len(pending)} 个待续跑节点")
        lines.append("请按依赖顺序继续执行上述节点，已完成的节点无需重复执行。")
        resume_intent = "\n".join(lines)
    else:
        resume_intent = f"TaskDAG「{dag_title}」所有节点已完成，无需续跑。"

    # AP — DAG Guardrails 续跑前检查
    gr_brief = _check_dag_guardrails_before_resume(dag_title)
    if gr_brief:
        return DagResumeResult(
            ok=False,
            run_id=rid,
            dag_title=dag_title,
            completed_node_ids=sorted(completed_ids),
            pending_nodes=[],
            resume_intent="",
            message=gr_brief,
        )

    return DagResumeResult(
        ok=True,
        run_id=rid,
        dag_title=dag_title,
        completed_node_ids=sorted(completed_ids),
        pending_nodes=pending,
        resume_intent=resume_intent,
        message=(
            f"探测完成：{len(completed_ids)} 个节点已完成，{len(pending)} 个节点待续跑。"
            if pending else
            f"所有节点已完成（{len(completed_ids)} 个），无需续跑。"
        ),
    )


def apply_dag_resume(run_id: str) -> DagResumeResult:
    """
    在 active.json 中将待续跑节点的 status 重置为 'pending'，
    并更新 DAG 的 run_id 字段为新的续跑 run_id（原 run_id + '.resume'）。
    返回 probe 结果（应用前的状态）。
    """
    from l3_node.task_engine.task_dag import load_task_dag_dict, save_active_task_dag_dict

    result = probe_dag_resume(run_id)
    if not result.ok or not result.pending_nodes:
        return result

    dag = load_task_dag_dict()
    if not dag or not isinstance(dag.get("nodes"), list):
        return result

    pending_ids = {n.node_id for n in result.pending_nodes}
    for n in dag["nodes"]:
        if isinstance(n, dict):
            nid = str(n.get("node_id") or n.get("id") or "")
            if nid in pending_ids:
                n["status"] = "pending"

    # 记录续跑标记
    dag["_resumed_from_run_id"] = run_id
    dag["run_id"] = f"{run_id}.resume"

    save_active_task_dag_dict(dag)
    logger.info(
        "[DagResume] 已应用续跑：run_id=%s pending_nodes=%d",
        run_id,
        len(result.pending_nodes),
    )
    return result


def build_resume_intent_from_active_dag() -> str:
    """仅依据 active.json 生成续跑前缀（无需 hook_events）。"""
    result = probe_dag_resume("")
    if not result.ok or not result.pending_nodes:
        return ""
    return (result.resume_intent or "").strip()
