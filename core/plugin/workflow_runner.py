"""
Workflow Runner - 复合蓝图 DAG 调度引擎

当 execution_model === "composite" 时，边缘智能体扮演「交响乐团指挥家」：
1. 解析图谱 (Parse DAG)：从 workflow.json 构建任务有向无环图
2. 唤醒武器 (Instantiate Plugins)：通过 SandboxEngine/PluginManager 拉起依赖插件
3. 数据接力 (Data Pipelining)：监听上游输出，精准注入下游 execute()
4. 事件驱动：通过 Event Bus 订阅 trigger 事件，响应式唤醒

与 ForgeCompiler 的 LABEL_TO_PLUGIN 保持同步。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

# Trigger 节点 (type+label) → 事件类型（供 Event Bus 订阅）
TRIGGER_TO_EVENT: dict[tuple[str, str], str] = {
    ("trigger", "语音"): "audio.input",
    ("trigger", "语音输入"): "audio.input",
    ("trigger", "定时"): "cron.trigger",
    ("trigger", "定时任务"): "cron.trigger",
}

# 节点标签 → 底层插件 ID 映射（与 cloud/nexus ForgeCompiler 同步）
LABEL_TO_PLUGIN: dict[str, str] = {
    "语音输入": "core-vad-audio",
    "定时任务": "core-cron-trigger",
    "qwen 路由分析": "core-llm-qwen",
    "意图解析": "core-llm-intent",
    "意图分析 llm": "core-llm-intent",
    "搜索天气": "com.jachin.weather",
    "执行代码": "core-sandbox-exec",
}


def _infer_plugin_id(node: dict[str, Any]) -> str | None:
    """从节点推断 plugin_id"""
    data = node.get("data") or {}
    if data.get("pluginId"):
        return data["pluginId"]
    label = (data.get("label") or "").strip()
    if not label:
        return None
    return LABEL_TO_PLUGIN.get(label) or LABEL_TO_PLUGIN.get(label.lower())


def _topological_order(routes: list[dict], node_ids: set[str]) -> list[str]:
    """根据 routes 计算拓扑序"""
    in_degree: dict[str, int] = {n: 0 for n in node_ids}
    out_edges: dict[str, list[str]] = defaultdict(list)

    for r in routes:
        f, t = r.get("from"), r.get("to")
        if f and t and f in node_ids and t in node_ids:
            out_edges[f].append(t)
            in_degree[t] += 1

    queue = [n for n in node_ids if in_degree[n] == 0]
    order: list[str] = []
    while queue:
        n = queue.pop(0)
        order.append(n)
        for succ in out_edges[n]:
            in_degree[succ] -= 1
            if in_degree[succ] == 0:
                queue.append(succ)

    return order


def _invoke_plugin(plugin_id: str, capability: str, payload: dict[str, Any]) -> dict[str, Any]:
    """
    统一调用插件：支持 _sandbox_plugins（JMP/HeavyProcess）和 Ray Actor
    """
    try:
        from core.system.plugin_manager import get_plugin_manager

        pm = get_plugin_manager()
    except ImportError:
        logger.warning("PluginManager 不可用，无法执行工作流节点")
        return {"success": False, "error": "PluginManager unavailable"}

    # 1. 尝试 _sandbox_plugins（JMP 插件、HeavyProcess）
    sandbox_plugins = getattr(pm, "_sandbox_plugins", None) or {}
    entry = sandbox_plugins.get(plugin_id)
    if entry is not None:
        # HeavyProcessClient 或类似句柄
        if hasattr(entry, "execute"):
            result = entry.execute(capability, payload)
            if isinstance(result, dict) and "status_code" in result:
                if result.get("status_code", 500) != 200:
                    return {
                        "success": False,
                        "error": result.get("error_message", "plugin error"),
                        "raw": result,
                    }
                return {"success": True, "data": result.get("payload", result)}
            return {"success": True, "data": result}
        # setup() 返回的 dict，含 capabilities[].handler
        if isinstance(entry, dict):
            caps = entry.get("capabilities", [])
            for c in caps:
                if isinstance(c, dict) and c.get("name") == capability:
                    handler = c.get("handler")
                    if callable(handler):
                        out = handler(payload)
                        return {"success": True, "data": out}
            # 回退：找第一个 handler
            for c in caps:
                if isinstance(c, dict) and callable(c.get("handler")):
                    out = c["handler"](payload)
                    return {"success": True, "data": out}
        return {"success": False, "error": f"Unknown entry type for {plugin_id}"}

    # 2. 尝试 Ray Actor（bundled skills）
    try:
        import ray

        actor = pm.get_actor(plugin_id)
        if actor is not None:
            ref = actor.execute.remote(capability, payload)
            result = ray.get(ref)
            if isinstance(result, dict) and result.get("success") is False:
                return {"success": False, "error": result.get("error", "actor error")}
            return {"success": True, "data": result.get("result", result)}
    except Exception as e:
        logger.debug("Ray actor invoke failed for %s: %s", plugin_id, e)

    return {"success": False, "error": f"Plugin {plugin_id} not loaded"}


class WorkflowRunner:
    """
    复合蓝图 DAG 调度引擎
    """

    def __init__(self, workflow: dict[str, Any]) -> None:
        """
        Args:
            workflow: { routes, dependencies, nodes, edges }
        """
        self.workflow = workflow
        self.routes = workflow.get("routes") or []
        self.nodes = {n["id"]: n for n in (workflow.get("nodes") or []) if n.get("id")}
        self.node_ids = set(self.nodes)
        self._order = _topological_order(self.routes, self.node_ids)
        self._node_plugin: dict[str, str] = {}
        for nid, n in self.nodes.items():
            pid = _infer_plugin_id(n)
            if pid:
                self._node_plugin[nid] = pid

    def run(self, initial_payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        执行 DAG：从 trigger 注入 initial_payload，沿 routes 数据接力

        Args:
            initial_payload: 触发节点输入，如 {"text": "今天多伦多天气怎么样"}

        Returns:
            {"success": bool, "data": ..., "error": ...}
        """
        initial_payload = initial_payload or {}
        outputs: dict[str, Any] = {}

        # 找到无入边的节点（trigger），将 initial_payload 作为其输出
        in_degree: dict[str, int] = {n: 0 for n in self.node_ids}
        for r in self.routes:
            t = r.get("to")
            if t and t in self.node_ids:
                in_degree[t] += 1
        triggers = [n for n in self.node_ids if in_degree[n] == 0]
        if triggers:
            # 将 initial_payload 注入第一个 trigger
            first_trigger = triggers[0]
            outputs[first_trigger] = initial_payload.get("text", initial_payload)

        for node_id in self._order:
            if node_id in outputs:
                continue  # trigger 已注入
            plugin_id = self._node_plugin.get(node_id)
            if not plugin_id:
                logger.warning("节点 %s 无对应 plugin_id，跳过", node_id)
                continue

            # 收集上游输出
            upstream_outputs: list[Any] = []
            for r in self.routes:
                if r.get("to") == node_id:
                    from_id = r.get("from")
                    if from_id and from_id in outputs:
                        upstream_outputs.append(outputs[from_id])

            if not upstream_outputs:
                logger.warning("节点 %s 无上游数据，跳过", node_id)
                continue

            # 合并上游：单输入直接传，多输入 Join 合并为 list（支持 Join 节点）
            payload: dict[str, Any] = (
                {"input": upstream_outputs[0]}
                if len(upstream_outputs) == 1
                else {"input": upstream_outputs, "join": True}
            )
            if isinstance(payload.get("input"), dict) and "text" in payload["input"]:
                payload["text"] = payload["input"].get("text")

            capability = "default"
            node = self.nodes.get(node_id)
            if node:
                caps = (node.get("data") or {}).get("capability")
                if caps:
                    capability = caps if isinstance(caps, str) else "default"

            result = _invoke_plugin(plugin_id, capability, payload)
            if not result.get("success"):
                return {
                    "success": False,
                    "error": result.get("error", "node execution failed"),
                    "node_id": node_id,
                    "plugin_id": plugin_id,
                }
            outputs[node_id] = result.get("data", result)

        # 取最后一个节点的输出作为最终结果
        last_output = None
        for nid in reversed(self._order):
            if nid in outputs:
                last_output = outputs[nid]
                break
        return {
            "success": True,
            "data": last_output or outputs,
            "outputs": outputs,
        }


def _infer_trigger_event_type(node: dict[str, Any]) -> str | None:
    """从 trigger 节点推断事件类型"""
    node_type = (node.get("type") or "").lower()
    label = (node.get("data") or {}).get("label") or ""
    for (t, l), ev in TRIGGER_TO_EVENT.items():
        if t in node_type and l in label:
            return ev
    return None


def register_workflow_to_event_bus(workflow_id: str, workflow: dict[str, Any]) -> None:
    """
    将工作流注册到 Event Bus：根据 trigger 节点订阅事件类型，
    当事件到达时自动唤醒 WorkflowRunner 执行 DAG。
    """
    try:
        from core.event_bus import emit, subscribe, start_consumer
    except ImportError:
        logger.debug("Event bus not available, skipping workflow registration")
        return

    in_degree: dict[str, int] = {}
    nodes = {n["id"]: n for n in (workflow.get("nodes") or []) if n.get("id")}
    routes = workflow.get("routes") or []
    for nid in nodes:
        in_degree[nid] = 0
    for r in routes:
        t = r.get("to")
        if t and t in nodes:
            in_degree[t] = in_degree.get(t, 0) + 1

    triggers = [n for n in nodes if in_degree.get(n, 0) == 0]
    for nid in triggers:
        node = nodes.get(nid)
        if not node:
            continue
        event_type = _infer_trigger_event_type(node)
        if not event_type:
            continue

        async def _handler(ev: Any, wf_id: str = workflow_id, wf: dict = workflow) -> None:
            payload = getattr(ev, "payload", ev) if hasattr(ev, "payload") else ev
            runner = WorkflowRunner(wf)
            result = runner.run(payload)
            if result.get("success"):
                logger.info("Workflow %s executed via event %s", wf_id, event_type)
            else:
                logger.warning("Workflow %s event run failed: %s", wf_id, result.get("error"))

        subscribe(event_type, f"{workflow_id}:{nid}", _handler)
        logger.info("Workflow %s subscribed to event %s (trigger %s)", workflow_id, event_type, nid)

    start_consumer()
