"""
DAG Workflow 状态机引擎 — 轻量级长流程编排与断点续传

用于 BI 分析、HR 筛选等 SOP 流程，支持异常冻结、断点续跑。
状态持久化: l3_node/local_memory (workflow_states.json)

Usage:
    class FetchNode(WorkflowNode):
        def execute(self, ctx): ...; return {"data": ...}

    wf = DAGWorkflow("bi_analysis").add_node(FetchNode("fetch")).add_node(TransformNode("transform"))
    wf.add_edge("fetch", "transform")
    ctx = wf.run("bi_analysis", {"date": "2026-03-17"})  # 异常时状态已冻结
    # 续跑: wf.resume("bi_analysis")
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any, Callable

from core.errors import SuspendForHumanException

logger = logging.getLogger(__name__)

# 工作流状态：挂起等待人工决策
SUSPENDED_WAITING_HUMAN = "SUSPENDED_WAITING_HUMAN"

# 招聘收网等长循环节点可识别的停止信号
SIGNAL_STOP_HARVEST = "STOP_HARVEST"


# -----------------------------------------------------------------------------
# WorkflowContext — 带信号队列的运行时上下文（FIFO）
# -----------------------------------------------------------------------------


class WorkflowContext(dict):
    """
    DAG 运行时上下文：在普通 dict 上增加 push_signal / pop_signal，
    供 HarvestLoopNode 等长循环节点响应外部中断（如 HR 点击停止收网）。

    信号列表序列化键为 ``_workflow_signals``，便于 local_memory 断点续传。
    """

    def drain_merge_into_context(self) -> None:
        """
        从进程内 workflow_signal_bridge 拉取待处理信号并合并进本上下文（FIFO 追加）。
        供 atom_* Playwright 长循环首行与 try_consume_stop_harvest 共用。
        """
        wid = str(self.get("_dag_workflow_id") or "").strip()
        if not wid:
            return
        try:
            from l3_node.workflow_signal_bridge import drain_merge_into_context as _bridge_drain

            _bridge_drain(self, wid)
        except Exception as e:
            logger.debug("[WorkflowContext] drain_merge_into_context 跳过: %s", e)

    def has_signal(self, signal: str) -> bool:
        """队首是否为指定信号（仅查看，不移除）。业务终止请配合 pop_signal 或 try_consume_stop_harvest。"""
        q = self.get("_workflow_signals")
        if not isinstance(q, list) or not q:
            return False
        return str(q[0]) == str(signal)

    def push_signal(self, signal: str) -> None:
        self.setdefault("_workflow_signals", []).append(str(signal))

    def pop_signal(self) -> str | None:
        q = self.get("_workflow_signals")
        if not q or not isinstance(q, list):
            return None
        return q.pop(0) if q else None

    def peek_signal(self) -> str | None:
        """查看队首信号不移除（供调试）；业务终止请用 try_consume_stop_harvest。"""
        q = self.get("_workflow_signals")
        if not q or not isinstance(q, list) or not q:
            return None
        return str(q[0])


def try_consume_stop_harvest(os_context: Any) -> bool:
    """
    OS 级探针：合并进程内信号桥后，若队首为 STOP_HARVEST 则弹出并返回 True。
    供 atom_* Playwright 长循环内「秒级刹车」；不影响队首其他信号。
    """
    if os_context is None:
        return False
    try:
        if isinstance(os_context, WorkflowContext):
            os_context.drain_merge_into_context()
        else:
            wid = ""
            if isinstance(os_context, dict):
                wid = str(os_context.get("_dag_workflow_id") or "").strip()
            if wid:
                from l3_node.workflow_signal_bridge import drain_merge_into_context

                drain_merge_into_context(os_context, wid)
    except Exception:
        pass
    q = os_context.get("_workflow_signals") if isinstance(os_context, dict) else None
    if q is None and hasattr(os_context, "get"):
        q = os_context.get("_workflow_signals")
    if not isinstance(q, list) or not q:
        return False
    if str(q[0]) != SIGNAL_STOP_HARVEST:
        return False
    q.pop(0)
    return True


def _ensure_workflow_context(ctx: Any) -> WorkflowContext:
    if isinstance(ctx, WorkflowContext):
        return ctx
    return WorkflowContext(dict(ctx or {}))


# -----------------------------------------------------------------------------
# WorkflowNode 基类
# -----------------------------------------------------------------------------


class WorkflowNode(ABC):
    """DAG 节点基类，子类实现 execute/rollback 业务逻辑。"""

    def __init__(self, node_id: str) -> None:
        self.node_id = node_id

    @abstractmethod
    def execute(self, context: dict[str, Any]) -> dict[str, Any]:
        """
        执行节点逻辑。可修改 context，返回结果合并进 context。
        抛出异常时，workflow 会冻结状态并停止。
        """
        ...

    def rollback(self, context: dict[str, Any]) -> None:
        """
        回滚节点（可选）。节点执行失败后，已完成的 preceding 节点可在此回滚。
        默认空实现。
        """
        pass


# -----------------------------------------------------------------------------
# DAGWorkflow
# -----------------------------------------------------------------------------


class DAGWorkflow:
    """
    DAG 编排器：拓扑序执行、断点续传。
    每节点成功后持久化状态；异常时冻结，resume 时跳过已完成节点。
    """

    def __init__(self, workflow_id: str) -> None:
        self.workflow_id = workflow_id
        self._nodes: dict[str, WorkflowNode] = {}
        self._in_edges: defaultdict[str, list[str]] = defaultdict(list)  # to -> [from, ...]
        self._out_edges: defaultdict[str, list[str]] = defaultdict(list)  # from -> [to, ...]

    def add_node(self, node: WorkflowNode) -> "DAGWorkflow":
        """添加节点（幂等：同 node_id 覆盖）。"""
        self._nodes[node.node_id] = node
        return self

    def add_edge(self, from_node_id: str, to_node_id: str) -> "DAGWorkflow":
        """添加边 from -> to。"""
        if from_node_id not in self._nodes or to_node_id not in self._nodes:
            raise ValueError(f"add_edge: 节点 {from_node_id} 或 {to_node_id} 不存在")
        self._out_edges[from_node_id].append(to_node_id)
        self._in_edges[to_node_id].append(from_node_id)
        return self

    def _topological_order(self) -> list[str]:
        """计算拓扑序（入度为 0 优先）。"""
        in_degree: dict[str, int] = {n: 0 for n in self._nodes}
        for to_id in self._in_edges:
            for _ in self._in_edges[to_id]:
                in_degree[to_id] += 1
        queue = [n for n in self._nodes if in_degree[n] == 0]
        order: list[str] = []
        while queue:
            n = queue.pop(0)
            order.append(n)
            for succ in self._out_edges[n]:
                in_degree[succ] -= 1
                if in_degree[succ] == 0:
                    queue.append(succ)
        return order

    def _persist_state(
        self,
        completed_nodes: list[str],
        context: dict[str, Any],
        failed_at_node: str | None = None,
        *,
        suspended_for_human: bool = False,
        suspended_at_node: str | None = None,
        human_pending: dict[str, Any] | None = None,
    ) -> None:
        """持久化到 local_memory (workflow_states.json)。"""
        try:
            import time

            from l3_node.local_memory import save_workflow_state

            payload: dict[str, Any] = {
                "completed_nodes": completed_nodes,
                "state": _sanitize_for_json(context),
                "failed_at_node": failed_at_node,
                "timestamp": time.time(),
            }
            if suspended_for_human and suspended_at_node and human_pending:
                payload["suspended_for_human"] = True
                payload["suspended_at_node"] = suspended_at_node
                payload["human_pending"] = human_pending
            save_workflow_state(self.workflow_id, payload)
        except ImportError as e:
            logger.warning("[WorkflowEngine] local_memory 不可用，状态未持久化: %s", e)
        except Exception as e:
            logger.warning("[WorkflowEngine] 持久化失败: %s", e)

    def run(
        self,
        workflow_id: str | None = None,
        initial_context: dict[str, Any] | None = None,
        *,
        on_node_done: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """
        执行 workflow。首次调用从头执行；若上次失败，内部会 resume。
        workflow_id: 覆盖 self.workflow_id
        initial_context: 初始上下文（resume 时会被加载的 state 覆盖）
        on_node_done: 每节点成功后的回调

        Returns:
            最终 context；若异常则状态已冻结，可调用 resume 续跑。
        """
        wid = workflow_id or self.workflow_id
        context = _ensure_workflow_context(initial_context or {})

        # 尝试加载上次冻结状态
        try:
            from l3_node.local_memory import load_workflow_state

            saved = load_workflow_state(wid)
        except ImportError:
            saved = None

        order = self._topological_order()
        if not order:
            return context

        if saved and (saved.get("failed_at_node") or saved.get("suspended_at_node")):
            # 断点续传（含 HITL 续跑）
            completed = saved.get("completed_nodes") or []
            state = saved.get("state") or {}
            context.update(state)
            context = _ensure_workflow_context(context)
            failed_at = saved.get("failed_at_node") or saved.get("suspended_at_node")
            try:
                idx = order.index(failed_at)
            except ValueError:
                idx = 0
            to_run = order[idx:]
            logger.info("[WorkflowEngine] resume %s: 跳过 %d 个已完成节点，从 %s 继续", wid, len(completed), failed_at)
        else:
            to_run = order
            completed = []

        for node_id in to_run:
            node = self._nodes.get(node_id)
            if not node:
                logger.warning("[WorkflowEngine] 节点 %s 不存在，跳过", node_id)
                continue
            try:
                result = node.execute(context)
                if isinstance(result, dict):
                    context.update(result)
                completed.append(node_id)
                self._persist_state(completed, context, failed_at_node=None)
                if on_node_done:
                    on_node_done(node_id, context)
            except SuspendForHumanException as e:
                logger.warning("[WorkflowEngine] 节点 %s 等待人工决策，挂起 workflow: %s", node_id, e.prompt_msg[:80])
                self._persist_state(
                    completed,
                    context,
                    failed_at_node=None,
                    suspended_for_human=True,
                    suspended_at_node=node_id,
                    human_pending={"prompt_msg": e.prompt_msg, "options": e.options},
                )
                ctx_out = dict(context)
                ctx_out["_suspended"] = True
                ctx_out["_suspended_at_node"] = node_id
                ctx_out["_human_pending"] = {"prompt_msg": e.prompt_msg, "options": e.options}
                ctx_out["_state"] = SUSPENDED_WAITING_HUMAN
                return ctx_out
            except Exception as e:
                logger.exception("[WorkflowEngine] 节点 %s 执行失败，冻结状态: %s", node_id, e)
                self._persist_state(completed, context, failed_at_node=node_id)
                raise

        # 全部成功，可选清理持久化（避免残留）
        try:
            from l3_node.local_memory import delete_workflow_state

            delete_workflow_state(wid)
        except ImportError:
            pass

        return context

    def resume(
        self,
        workflow_id: str | None = None,
        *,
        on_node_done: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """
        从上次失败处续跑。若无冻结状态，则从头执行。
        """
        return self.run(
            workflow_id=workflow_id,
            initial_context=None,
            on_node_done=on_node_done,
        )

    def inject_human_decision_and_resume(
        self,
        workflow_id: str | None = None,
        human_choice: Any = None,
        *,
        on_node_done: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """
        当 workflow 因 ask_human_for_decision 挂起后，外部（API/控制台）收到统帅输入时调用。

        将 human_choice 注入为中断节点的结果，并唤醒 resume() 继续执行。

        Args:
            workflow_id: 目标 workflow，默认 self.workflow_id
            human_choice: 人工决策值，将注入 context["_human_decision"]
            on_node_done: 节点完成回调

        Returns:
            续跑后的最终 context

        Raises:
            ValueError: 若该 workflow 未处于 SUSPENDED_WAITING_HUMAN 状态
        """
        wid = workflow_id or self.workflow_id
        try:
            from l3_node.local_memory import load_workflow_state, save_workflow_state

            saved = load_workflow_state(wid)
        except ImportError as e:
            raise ValueError(f"local_memory 不可用，无法注入人工决策: {e}") from e

        if not saved or not saved.get("suspended_for_human"):
            raise ValueError(
                f"workflow {wid} 未处于 SUSPENDED_WAITING_HUMAN 状态，无法注入。"
                "请确认该 workflow 已因 ask_human_for_decision 挂起。"
            )

        suspended_at = saved.get("suspended_at_node")
        state = saved.get("state") or {}
        state["_human_decision"] = human_choice
        save_workflow_state(
            wid,
            {
                "completed_nodes": saved.get("completed_nodes") or [],
                "state": state,
                "failed_at_node": suspended_at,
                "suspended_for_human": False,
                "suspended_at_node": None,
                "human_pending": None,
                "timestamp": saved.get("timestamp"),
            },
        )
        logger.info("[WorkflowEngine] 已注入人工决策，续跑 workflow %s 从节点 %s", wid, suspended_at)
        return self.resume(workflow_id=wid, on_node_done=on_node_done)

    @staticmethod
    def inject_signal(workflow_id: str, signal: str) -> bool:
        """
        向指定 workflow 注入信号（如 STOP_HARVEST）：写入进程内信号桥 + 持久化 state._workflow_signals。
        正在执行 HarvestLoopNode 的线程应在循环内调用 drain_merge_into_context 拉取。
        """
        wid = (workflow_id or "").strip()
        sig = (signal or "").strip()
        if not wid or not sig:
            return False
        try:
            from l3_node.workflow_signal_bridge import push_signal as bridge_push

            bridge_push(wid, sig)
        except ImportError as e:
            logger.warning("[WorkflowEngine] inject_signal 桥接不可用: %s", e)
            return False
        try:
            from l3_node.local_memory import load_workflow_state, save_workflow_state

            saved = load_workflow_state(wid)
            if saved is not None:
                st = saved.get("state")
                st = dict(st) if isinstance(st, dict) else {}
                q = [str(x) for x in (st.get("_workflow_signals") or []) if x is not None]
                q.append(sig)
                st["_workflow_signals"] = q
                saved = {**saved, "state": st}
                save_workflow_state(wid, saved)
        except Exception as e:
            logger.debug("[WorkflowEngine] inject_signal 持久化跳过（可能尚无状态）: %s", e)
        logger.info("[WorkflowEngine] inject_signal workflow=%s signal=%s", wid, sig)
        return True


# 模块级别名，便于 `from core.workflow_engine import inject_signal`
inject_signal = DAGWorkflow.inject_signal


def _sanitize_for_json(obj: Any) -> Any:
    """递归转换为 JSON 可序列化结构，不可序列化的转为 str。"""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _sanitize_for_json(v) for k, v in obj.items()}
    return str(obj)
