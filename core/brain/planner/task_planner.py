"""
任务规划器
Task Planner
"""

import logging
import uuid
from typing import List, Dict, Any, Optional
from core.brain.planner.intent_parser import IntentParser, Intent
from core.brain.planner.resource_allocator import ResourceAllocator
from core.brain.ray_cluster.task_types import RayTask, TaskType, create_skill_task, create_llm_task
from core.system.plugin_manager import PluginManager
from core.registry.registry import DeviceRegistry

logger = logging.getLogger(__name__)


class TaskPlanner:
    """任务规划器"""
    
    def __init__(
        self,
        intent_parser: IntentParser,
        resource_allocator: ResourceAllocator,
        plugin_manager: PluginManager,
        device_registry: Optional[DeviceRegistry] = None
    ):
        """
        初始化任务规划器
        
        Args:
            intent_parser: 意图解析器
            resource_allocator: 资源分配器
            plugin_manager: 插件管理器（技能唯一入口）
            device_registry: 设备注册表（可选）
        """
        self.intent_parser = intent_parser
        self.resource_allocator = resource_allocator
        self.skill_registry = plugin_manager  # 兼容接口：get_skill, list_skills
        self.device_registry = device_registry
    
    async def plan_task(
        self,
        user_input: str,
        user_id: Optional[str] = None
    ) -> List[RayTask]:
        """
        规划任务
        
        流程：
        1. 解析用户意图
        2. 识别需要的技能和能力
        3. 查询设备能力（如果需要）
        4. 生成任务计划
        5. 资源分配
        6. 返回任务列表
        
        Args:
            user_input: 用户输入文本
            user_id: 用户ID（可选）
        
        Returns:
            List[RayTask]: 任务列表
        """
        try:
            # Step 1: 解析意图
            intent = await self.intent_parser.parse_intent(user_input)
            logger.info(f"Parsed intent: {intent.intent_type}")
            
            # Step 2: 根据意图类型生成任务
            tasks = []
            
            if intent.intent_type == "skill_execution":
                tasks = await self._plan_skill_execution(intent, user_id)
            elif intent.intent_type == "device_control":
                tasks = await self._plan_device_control(intent, user_id)
            elif intent.intent_type == "query":
                tasks = await self._plan_query(intent, user_input, user_id)
            else:
                logger.warning(f"Unknown intent type: {intent.intent_type}")
                # 默认尝试作为查询处理
                tasks = await self._plan_query(intent, user_input, user_id)
            
            # Step 3: 资源分配
            for task in tasks:
                node_id = await self.resource_allocator.allocate_resources(task)
                if node_id:
                    task.worker_node = node_id
            
            logger.info(f"Planned {len(tasks)} tasks for intent: {intent.intent_type}")
            return tasks
            
        except Exception as e:
            logger.error(f"Failed to plan task: {e}", exc_info=True)
            return []
    
    async def _plan_skill_execution(
        self,
        intent: Intent,
        user_id: Optional[str]
    ) -> List[RayTask]:
        """
        规划技能执行任务
        
        Args:
            intent: 意图对象
            user_id: 用户ID
        
        Returns:
            List[RayTask]: 任务列表
        """
        tasks = []
        
        def _compute_from_capability(manifest: Dict, cap_name: str) -> tuple:
            """根据 capability compute 字段返回 (num_cpus, num_gpus)"""
            for cap in manifest.get("capabilities", []):
                if cap.get("name") == cap_name:
                    compute = cap.get("compute", "cpu_light")
                    if compute == "gpu_heavy":
                        return 2, 1
                    if compute == "cpu_medium":
                        return 2, 0
                    return 1, 0
            return 1, 0

        # 查找技能
        if intent.skill_id:
            manifest = await self.skill_registry.get_skill(intent.skill_id)
            if manifest:
                cap_name = intent.capability_name or "default"
                num_cpus, num_gpus = _compute_from_capability(manifest, cap_name)
                num_cpus = intent.parameters.get("num_cpus", num_cpus)
                num_gpus = intent.parameters.get("num_gpus", num_gpus)
                task = create_skill_task(
                    skill_id=intent.skill_id,
                    capability_name=cap_name,
                    input_data=intent.parameters or {},
                    num_cpus=num_cpus,
                    num_gpus=num_gpus,
                )
                tasks.append(task)
        elif intent.capability_name:
            skills = await self.skill_registry.list_skills()
            for skill_info in skills:
                capabilities = skill_info.get("capabilities", [])
                for cap in capabilities:
                    if cap.get("name") == intent.capability_name:
                        manifest = await self.skill_registry.get_skill(skill_info["skill_id"])
                        num_cpus, num_gpus = _compute_from_capability(
                            manifest or {}, intent.capability_name
                        )
                        task = create_skill_task(
                            skill_id=skill_info["skill_id"],
                            capability_name=intent.capability_name,
                            input_data=intent.parameters or {},
                            num_cpus=num_cpus,
                            num_gpus=num_gpus,
                        )
                        tasks.append(task)
                        break
        
        return tasks
    
    async def _plan_device_control(
        self,
        intent: Intent,
        user_id: Optional[str]
    ) -> List[RayTask]:
        """
        规划设备控制任务
        
        Args:
            intent: 意图对象
            user_id: 用户ID
        
        Returns:
            List[RayTask]: 任务列表
        """
        tasks = []
        
        # TODO: 实现设备控制任务规划
        # 需要查询设备注册表，找到匹配的设备
        # 然后创建设备控制任务
        
        logger.warning("Device control task planning not yet fully implemented")
        return tasks
    
    async def _plan_query(
        self,
        intent: Intent,
        user_input: str,
        user_id: Optional[str]
    ) -> List[RayTask]:
        """
        规划查询任务（使用LLM）
        
        Args:
            intent: 意图对象
            user_input: 用户输入
            user_id: 用户ID
        
        Returns:
            List[RayTask]: 任务列表
        """
        from core.config import settings
        
        # 创建LLM推理任务
        task = create_llm_task(
            provider=settings.LLM_PROVIDER,
            model=settings.LLM_MODEL,
            messages=[
                {"role": "user", "content": user_input}
            ],
            temperature=0.7,
            max_tokens=2000,
        )
        
        return [task]
    
    async def find_skill_by_capability(
        self,
        capability_name: str,
        capability_type: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        根据能力查找技能
        
        Args:
            capability_name: 能力名称
            capability_type: 能力类型（可选）
        
        Returns:
            Dict: 技能信息，如果找不到则返回None
        """
        skills = await self.skill_registry.list_skills()
        
        for skill_info in skills:
            capabilities = skill_info.get("capabilities", [])
            for cap in capabilities:
                if cap.get("name") == capability_name:
                    if capability_type is None or cap.get("type") == capability_type:
                        return skill_info
        
        return None
