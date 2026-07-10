"""
意图解析器
Intent Parser
"""

import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from core.brain.llm.factory import LLMProviderFactory
from core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class Intent:
    """意图对象"""
    intent_type: str  # 意图类型：skill_execution, device_control, query, etc.
    capability_name: Optional[str] = None  # 需要的能力名称
    capability_type: Optional[str] = None  # 能力类型：action, sensor, processor
    skill_id: Optional[str] = None  # 技能ID（如果已确定）
    parameters: Dict[str, Any] = None  # 参数
    requires_device: bool = False  # 是否需要设备
    device_capability: Optional[str] = None  # 设备能力名称
    confidence: float = 0.0  # 置信度

    def __post_init__(self):
        if self.parameters is None:
            self.parameters = {}


class IntentParser:
    """意图解析器"""

    def __init__(self, llm_provider: Optional[str] = None):
        """
        初始化意图解析器

        Args:
            llm_provider: LLM提供者，默认使用settings中的配置
        """
        self.llm_provider = llm_provider or settings.LLM_PROVIDER
        self.factory = LLMProviderFactory()

    async def parse_intent(self, user_input: str) -> Intent:
        """
        解析用户意图

        Args:
            user_input: 用户输入文本

        Returns:
            Intent: 解析后的意图对象
        """
        try:
            # 使用LLM解析意图
            llm = self.factory.create_provider(self.llm_provider)

            # 构建提示词
            prompt = self._build_intent_prompt(user_input)

            # 调用LLM
            response = await llm.chat(
                messages=[
                    {"role": "system", "content": "You are an intent parser for an AI agent system. Parse user input and extract intent information."},
                    {"role": "user", "content": prompt}
                ],
                model=settings.LLM_MODEL,
                temperature=0.3,  # 低温度以获得更确定的结果
                max_tokens=500,
            )

            # 解析LLM响应
            intent = self._parse_llm_response(user_input, response.text)

            logger.info(f"Parsed intent: {intent.intent_type} (confidence: {intent.confidence})")
            return intent

        except Exception as e:
            logger.error(f"Failed to parse intent: {e}", exc_info=True)
            # 返回默认意图
            return Intent(
                intent_type="unknown",
                confidence=0.0,
            )

    def _build_intent_prompt(self, user_input: str) -> str:
        """
        构建意图解析提示词

        Args:
            user_input: 用户输入

        Returns:
            str: 提示词
        """
        return f"""Parse the following user input and extract intent information. Return a JSON object with the following structure:

{{
  "intent_type": "skill_execution|device_control|query|unknown",
  "capability_name": "name of the capability needed",
  "capability_type": "action|sensor|processor",
  "parameters": {{"key": "value"}},
  "requires_device": true|false,
  "device_capability": "device capability name if needed",
  "confidence": 0.0-1.0
}}

User input: {user_input}

Return only the JSON object, no additional text."""

    def _parse_llm_response(self, user_input: str, llm_response: str) -> Intent:
        """
        解析LLM响应

        Args:
            user_input: 原始用户输入
            llm_response: LLM响应文本

        Returns:
            Intent: 意图对象
        """
        import json
        import re

        try:
            # 尝试提取JSON
            json_match = re.search(r'\{.*\}', llm_response, re.DOTALL)
            if json_match:
                json_str = json_match.group()
                data = json.loads(json_str)
            else:
                # 如果没有找到JSON，尝试直接解析
                data = json.loads(llm_response)

            # 创建Intent对象
            intent = Intent(
                intent_type=data.get("intent_type", "unknown"),
                capability_name=data.get("capability_name"),
                capability_type=data.get("capability_type"),
                parameters=data.get("parameters", {}),
                requires_device=data.get("requires_device", False),
                device_capability=data.get("device_capability"),
                confidence=float(data.get("confidence", 0.0)),
            )

            return intent

        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(f"Failed to parse LLM response as JSON: {e}, using fallback")
            # 使用简单的关键词匹配作为后备方案
            return self._fallback_intent_parsing(user_input)

    def _fallback_intent_parsing(self, user_input: str) -> Intent:
        """
        后备意图解析（基于关键词）

        Args:
            user_input: 用户输入

        Returns:
            Intent: 意图对象
        """
        user_input_lower = user_input.lower()

        # 简单的关键词匹配
        if any(word in user_input_lower for word in ["执行", "运行", "调用", "execute", "run", "call"]):
            return Intent(
                intent_type="skill_execution",
                confidence=0.5,
            )
        elif any(word in user_input_lower for word in ["控制", "打开", "关闭", "control", "turn on", "turn off"]):
            return Intent(
                intent_type="device_control",
                requires_device=True,
                confidence=0.5,
            )
        elif any(word in user_input_lower for word in ["查询", "获取", "问", "query", "get", "ask"]):
            return Intent(
                intent_type="query",
                confidence=0.5,
            )
        else:
            return Intent(
                intent_type="unknown",
                confidence=0.0,
            )
