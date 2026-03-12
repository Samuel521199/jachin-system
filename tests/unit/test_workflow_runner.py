"""
WorkflowRunner 单元测试
"""
import pytest

from core.plugin.workflow_runner import (
    LABEL_TO_PLUGIN,
    WorkflowRunner,
    _infer_plugin_id,
    _topological_order,
)


def test_label_to_plugin_mapping():
    """LABEL_TO_PLUGIN 与 ForgeCompiler 保持一致"""
    assert LABEL_TO_PLUGIN["语音输入"] == "core-vad-audio"
    assert LABEL_TO_PLUGIN["意图解析"] == "core-llm-intent"
    assert LABEL_TO_PLUGIN["搜索天气"] == "com.jachin.weather"


def test_infer_plugin_id_from_plugin_id():
    """优先使用 data.pluginId"""
    node = {"id": "n1", "data": {"pluginId": "custom-plugin", "label": "语音输入"}}
    assert _infer_plugin_id(node) == "custom-plugin"


def test_infer_plugin_id_from_label():
    """无 pluginId 时从 label 推断"""
    node = {"id": "n1", "data": {"label": "意图解析"}}
    assert _infer_plugin_id(node) == "core-llm-intent"


def test_topological_order():
    """拓扑序正确"""
    routes = [
        {"from": "a", "to": "b"},
        {"from": "a", "to": "c"},
        {"from": "b", "to": "d"},
        {"from": "c", "to": "d"},
    ]
    order = _topological_order(routes, {"a", "b", "c", "d"})
    assert order.index("a") < order.index("b")
    assert order.index("a") < order.index("c")
    assert order.index("b") < order.index("d")
    assert order.index("c") < order.index("d")


def test_workflow_runner_parse_dag():
    """WorkflowRunner 正确解析 DAG"""
    workflow = {
        "routes": [{"from": "voice-input", "to": "intent-llm"}],
        "dependencies": ["core-vad-audio", "core-llm-intent"],
        "nodes": [
            {"id": "voice-input", "type": "trigger", "data": {"label": "语音输入"}},
            {"id": "intent-llm", "type": "llm", "data": {"label": "意图解析"}},
        ],
        "edges": [{"source": "voice-input", "target": "intent-llm"}],
    }
    runner = WorkflowRunner(workflow)
    assert "voice-input" in runner._node_plugin
    assert runner._node_plugin["voice-input"] == "core-vad-audio"
    assert runner._node_plugin["intent-llm"] == "core-llm-intent"
    assert "voice-input" in runner._order
    assert "intent-llm" in runner._order


def test_workflow_runner_run_mocked():
    """WorkflowRunner.run 在 mock 插件下完成数据接力"""
    from unittest.mock import patch

    workflow = {
        "routes": [{"from": "voice-input", "to": "intent-llm"}],
        "dependencies": ["core-vad-audio", "core-llm-intent"],
        "nodes": [
            {"id": "voice-input", "type": "trigger", "data": {"label": "语音输入"}},
            {"id": "intent-llm", "type": "llm", "data": {"label": "意图解析"}},
        ],
        "edges": [{"source": "voice-input", "target": "intent-llm"}],
    }

    def mock_invoke(plugin_id: str, capability: str, payload: dict):
        if plugin_id == "core-llm-intent":
            text = payload.get("text") or (payload.get("input") or {}).get("text", "")
            return {"success": True, "data": {"intent": "weather", "text": text}}
        return {"success": False, "error": "unknown plugin"}

    with patch("core.plugin.workflow_runner._invoke_plugin", side_effect=mock_invoke):
        runner = WorkflowRunner(workflow)
        result = runner.run({"text": "今天多伦多天气怎么样"})

    assert result["success"] is True
    assert "intent" in (result.get("data") or {})
    assert result.get("data", {}).get("intent") == "weather"
