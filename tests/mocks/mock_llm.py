"""
Mock LLM Provider
用于测试时模拟 LLM 响应，避免实际调用 LLM API
"""

import json
from typing import List, Dict, Any, Optional, AsyncIterator
from core.brain.llm.base import BaseLLMProvider


class MockLLMProvider(BaseLLMProvider):
    """Mock LLM Provider"""

    def __init__(self, responses: Optional[Dict[str, str]] = None):
        """
        初始化 Mock LLM Provider

        Args:
            responses: 预定义的响应映射 {prompt: response}
        """
        self.responses = responses or {}
        self.call_history: List[Dict[str, Any]] = []

    async def chat(
        self,
        messages: List[Dict[str, str]],
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> str:
        """同步聊天接口"""
        # 记录调用历史
        self.call_history.append({
            "messages": messages,
            "context": context,
            "kwargs": kwargs
        })

        # 提取用户消息
        user_message = None
        for msg in messages:
            if msg.get("role") == "user":
                user_message = msg.get("content", "")
                break

        # 查找预定义响应
        if user_message and user_message in self.responses:
            return self.responses[user_message]

        # 默认响应（用于意图规划测试）
        if "plan" in user_message.lower() or "analyze" in user_message.lower():
            return self._default_intent_planning_response(user_message)

        # 返回默认响应
        return json.dumps({
            "plugin_id": "com.jachin.sys-monitor",
            "method_name": "get_performance_snapshot",
            "parameters": {},
            "confidence": 0.9,
            "reasoning": "Default mock response"
        })

    async def stream_chat(
        self,
        messages: List[Dict[str, str]],
        context: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> AsyncIterator[str]:
        """流式聊天接口"""
        response = await self.chat(messages, context, **kwargs)
        # 模拟流式输出
        words = response.split()
        for word in words:
            yield word + " "

    async def embed(
        self,
        texts: List[str],
        **kwargs
    ) -> List[List[float]]:
        """文本嵌入接口"""
        # 返回随机向量（用于测试）
        import random
        return [[random.random() for _ in range(384)] for _ in texts]

    def get_model_info(self) -> Dict[str, Any]:
        """获取模型信息"""
        return {
            "provider": "mock",
            "model": "mock-llm",
            "version": "1.0.0"
        }

    async def health_check(self) -> bool:
        """健康检查"""
        return True

    def _default_intent_planning_response(self, user_query: str) -> str:
        """默认意图规划响应"""
        # 根据查询内容返回不同的响应
        query_lower = user_query.lower()

        if "状态" in query_lower or "性能" in query_lower or "卡" in query_lower:
            return json.dumps({
                "plugin_id": "com.jachin.sys-monitor",
                "method_name": "get_performance_snapshot",
                "parameters": {},
                "confidence": 0.95,
                "reasoning": "User wants to check system performance"
            })
        elif "文件" in query_lower or "list" in query_lower:
            return json.dumps({
                "plugin_id": "com.jachin.files",
                "method_name": "list_files",
                "parameters": {},
                "confidence": 0.9,
                "reasoning": "User wants to list files"
            })
        else:
            return json.dumps({
                "plugin_id": "com.jachin.sys-monitor",
                "method_name": "get_performance_snapshot",
                "parameters": {},
                "confidence": 0.5,
                "reasoning": "Default fallback response"
            })

    def set_response(self, prompt: str, response: str):
        """设置预定义响应"""
        self.responses[prompt] = response

    def clear_history(self):
        """清除调用历史"""
        self.call_history.clear()


# 便捷函数
def create_mock_llm_provider(responses: Optional[Dict[str, str]] = None) -> MockLLMProvider:
    """创建 Mock LLM Provider"""
    return MockLLMProvider(responses)


def create_intent_planning_mock() -> MockLLMProvider:
    """创建用于意图规划测试的 Mock LLM"""
    responses = {
        "查看系统状态": json.dumps({
            "plugin_id": "com.jachin.sys-monitor",
            "method_name": "get_performance_snapshot",
            "parameters": {},
            "confidence": 0.95,
            "reasoning": "User wants to check system status"
        }),
        "电脑好卡": json.dumps({
            "plugin_id": "com.jachin.sys-monitor",
            "method_name": "get_performance_snapshot",
            "parameters": {},
            "confidence": 0.9,
            "reasoning": "User reports system is slow, needs performance check"
        }),
        "列出文件": json.dumps({
            "plugin_id": "com.jachin.files",
            "method_name": "list_files",
            "parameters": {"path": "/"},
            "confidence": 0.95,
            "reasoning": "User wants to list files"
        })
    }
    return MockLLMProvider(responses)
