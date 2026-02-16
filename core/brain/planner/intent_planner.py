"""
Intent Planner - 意图规划器
将用户的自然语言映射到具体的插件ID和方法

核心逻辑：
1. 获取所有可用插件的 manifest 和能力列表
2. 使用 LLM 分析用户意图
3. 匹配到最合适的插件和方法
4. 返回执行计划
"""

import logging
import json
import yaml
from typing import Dict, Any, List, Optional
from pathlib import Path
from dataclasses import dataclass

from core.brain.llm.factory import LLMProviderFactory
from core.system.plugin_manager import PluginManager
from core.config import settings
from core.monitoring import get_performance_monitor
import time

logger = logging.getLogger(__name__)


@dataclass
class ExecutionPlan:
    """执行计划"""
    plugin_id: str  # 插件ID
    method_name: str  # 方法名
    parameters: Dict[str, Any]  # 参数
    confidence: float  # 置信度（0.0-1.0）
    reasoning: Optional[str] = None  # 推理过程（可选）


class IntentPlanner:
    """意图规划器"""
    
    def __init__(
        self,
        plugin_manager: PluginManager,
        llm_provider: Optional[str] = None
    ):
        """
        初始化意图规划器
        
        Args:
            plugin_manager: 插件管理器实例
            llm_provider: LLM提供者，默认使用settings中的配置
        """
        self.plugin_manager = plugin_manager
        self.llm_provider = llm_provider or settings.LLM_PROVIDER
        self.factory = LLMProviderFactory()
        self._capabilities_cache: Optional[List[Dict[str, Any]]] = None
    
    async def plan(
        self,
        user_query: str,
        available_plugins: Optional[List[str]] = None
    ) -> Optional[ExecutionPlan]:
        """
        规划执行计划
        
        Args:
            user_query: 用户查询（自然语言）
            available_plugins: 可用插件列表（如果为 None，则扫描所有插件）
        
        Returns:
            ExecutionPlan: 执行计划，如果无法匹配则返回 None
        """
        monitor = get_performance_monitor()
        start_time = time.time()
        
        try:
            # 1. 获取插件能力列表
            capabilities = await self._get_capabilities(available_plugins)
            if not capabilities:
                logger.warning("No capabilities available")
                monitor.record(
                    "intent.planning",
                    time.time() - start_time,
                    success=False,
                    tags={"error": "no_capabilities"}
                )
                return None
            
            # 2. 构建 LLM 提示词
            prompt = self._build_planning_prompt(user_query, capabilities)
            
            # 3. 调用 LLM
            llm = self.factory.create_provider(self.llm_provider)
            llm_start = time.time()
            response_text = await llm.chat(
                messages=[
                    {
                        "role": "system",
                        "content": self._get_system_prompt()
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.3,  # 低温度以获得更确定的结果
                max_tokens=1000,
            )
            
            # 确保 response_text 是字符串
            if not isinstance(response_text, str):
                response_text = str(response_text)
            
            # 4. 解析 LLM 响应
            plan = self._parse_llm_response(response_text)
            
            duration = time.time() - start_time
            llm_duration = time.time() - llm_start
            
            # 记录 LLM 调用性能
            monitor.record(
                "llm.call",
                llm_duration,
                success=True,
                tags={"provider": self.llm_provider, "task": "intent_planning"}
            )
            
            if plan and plan.confidence > 0.5:  # 置信度阈值
                logger.info(
                    f"Planned execution: {plan.plugin_id}.{plan.method_name} "
                    f"(confidence: {plan.confidence:.2f})"
                )
                # 记录成功的规划
                monitor.record(
                    "intent.planning",
                    duration,
                    success=True,
                    tags={"plugin_id": plan.plugin_id, "method_name": plan.method_name, "confidence": str(plan.confidence)}
                )
                return plan
            else:
                logger.warning(f"Low confidence plan: {plan.confidence if plan else 0.0}")
                # 记录低置信度规划
                monitor.record(
                    "intent.planning",
                    duration,
                    success=False,
                    tags={"error": "low_confidence", "confidence": str(plan.confidence) if plan else "0.0"}
                )
                return None
                
        except Exception as e:
            logger.error(f"Failed to plan execution: {e}", exc_info=True)
            duration = time.time() - start_time
            # 记录失败的规划
            monitor.record(
                "intent.planning",
                duration,
                success=False,
                tags={"error": str(e)[:50]}
            )
            return None
    
    async def _get_capabilities(
        self,
        available_plugins: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        获取所有插件的能力列表
        
        Args:
            available_plugins: 可用插件列表（如果为 None，则扫描所有插件）
        
        Returns:
            List[Dict]: 能力列表，每个元素包含 plugin_id, method_name, description 等
        """
        if self._capabilities_cache is not None:
            return self._capabilities_cache
        
        capabilities = []
        
        # 获取插件列表
        if available_plugins is None:
            # 扫描所有 bundled skills
            available_plugins = self.plugin_manager.scan_bundled_skills()
        
        # 读取每个插件的 manifest
        for plugin_id in available_plugins:
            try:
                # 查找 manifest.yaml
                skill_dir = None
                bundled_dir = self.plugin_manager.skills_repo_dir / "_bundled" / plugin_id
                if bundled_dir.exists():
                    skill_dir = bundled_dir
                else:
                    skill_dir = self.plugin_manager.skills_repo_dir / plugin_id
                    if not skill_dir.exists():
                        continue
                
                manifest_file = skill_dir / "manifest.yaml"
                if not manifest_file.exists():
                    continue
                
                # 读取 manifest
                manifest_data = yaml.safe_load(manifest_file.read_text(encoding='utf-8'))
                
                # 提取能力信息
                plugin_capabilities = manifest_data.get("capabilities", [])
                plugin_name = manifest_data.get("name", plugin_id)
                plugin_description = manifest_data.get("description", "")
                
                for cap in plugin_capabilities:
                    capabilities.append({
                        "plugin_id": plugin_id,
                        "plugin_name": plugin_name,
                        "plugin_description": plugin_description,
                        "method_name": cap.get("name", ""),
                        "description": cap.get("description", ""),
                        "input_schema": cap.get("input_schema", {}),
                        "output_schema": cap.get("output_schema", {}),
                    })
                    
            except Exception as e:
                logger.warning(f"Failed to read manifest for {plugin_id}: {e}")
                continue
        
        self._capabilities_cache = capabilities
        logger.info(f"Loaded {len(capabilities)} capabilities from {len(available_plugins)} plugins")
        return capabilities
    
    def _get_system_prompt(self) -> str:
        """获取系统提示词"""
        return """You are an intelligent task planner for an AI agent system. Your job is to analyze user queries and match them to the most appropriate plugin and method.

You will receive:
1. A user query in natural language
2. A list of available plugins and their capabilities

Your task:
- Understand the user's intent
- Find the best matching plugin and method
- Extract parameters from the user query (if any)
- Return a JSON object with the execution plan

Guidelines:
- Match user intent to plugin capabilities based on descriptions
- Consider synonyms and related terms (e.g., "电脑卡" -> "performance", "查看状态" -> "get_status")
- Extract parameters from the query when possible
- Provide a confidence score (0.0-1.0) indicating how well the match fits
- If no good match is found, return null or set confidence to 0.0

Return format (JSON):
{
  "plugin_id": "com.jachin.sys-monitor",
  "method_name": "get_performance_snapshot",
  "parameters": {},
  "confidence": 0.95,
  "reasoning": "User wants to check system performance, which matches the get_performance_snapshot capability"
}"""
    
    def _build_planning_prompt(
        self,
        user_query: str,
        capabilities: List[Dict[str, Any]]
    ) -> str:
        """
        构建规划提示词
        
        Args:
            user_query: 用户查询
            capabilities: 能力列表
        
        Returns:
            str: 提示词
        """
        # 格式化能力列表
        capabilities_text = self._format_capabilities(capabilities)
        
        return f"""Analyze the following user query and find the best matching plugin and method.

User Query: "{user_query}"

Available Capabilities:
{capabilities_text}

Return a JSON object with the execution plan. If no good match is found, return {{"confidence": 0.0}}."""
    
    def _format_capabilities(self, capabilities: List[Dict[str, Any]]) -> str:
        """
        格式化能力列表为文本
        
        Args:
            capabilities: 能力列表
        
        Returns:
            str: 格式化的文本
        """
        lines = []
        for cap in capabilities:
            plugin_id = cap["plugin_id"]
            plugin_name = cap["plugin_name"]
            method_name = cap["method_name"]
            description = cap["description"]
            
            lines.append(
                f"- Plugin: {plugin_id} ({plugin_name})\n"
                f"  Method: {method_name}\n"
                f"  Description: {description}"
            )
        
        return "\n\n".join(lines)
    
    def _parse_llm_response(self, llm_response: str) -> Optional[ExecutionPlan]:
        """
        解析 LLM 响应
        
        Args:
            llm_response: LLM 响应文本
        
        Returns:
            ExecutionPlan: 执行计划，如果解析失败则返回 None
        """
        import re
        
        try:
            # 尝试提取 JSON
            json_match = re.search(r'\{.*\}', llm_response, re.DOTALL)
            if json_match:
                json_str = json_match.group()
                data = json.loads(json_str)
            else:
                # 如果没有找到 JSON，尝试直接解析
                data = json.loads(llm_response)
            
            # 检查置信度
            confidence = float(data.get("confidence", 0.0))
            if confidence <= 0.0:
                return None
            
            # 创建 ExecutionPlan
            plan = ExecutionPlan(
                plugin_id=data.get("plugin_id", ""),
                method_name=data.get("method_name", ""),
                parameters=data.get("parameters", {}),
                confidence=confidence,
                reasoning=data.get("reasoning")
            )
            
            # 验证必需字段
            if not plan.plugin_id or not plan.method_name:
                logger.warning("Missing required fields in execution plan")
                return None
            
            return plan
            
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(f"Failed to parse LLM response as JSON: {e}")
            logger.debug(f"LLM response: {llm_response}")
            return None
    
    def clear_cache(self):
        """清除能力列表缓存"""
        self._capabilities_cache = None
