"""
Brain Orchestrator API
Brain Orchestrator API
"""

from fastapi import APIRouter, HTTPException, Depends, status
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from pathlib import Path
from core.brain.planner.task_planner import TaskPlanner
from core.brain.planner.intent_parser import IntentParser
from core.brain.planner.resource_allocator import ResourceAllocator
from core.brain.ray_cluster.cluster_manager import RayClusterManager
from core.brain.ray_cluster.task_scheduler import TaskScheduler
from core.brain.ray_cluster.task_types import RayTask
from core.registry.registry import DeviceRegistry
from core.system.plugin_manager import PluginManager
from core.system.plugin_executor import PluginExecutor
from core.brain.planner.intent_planner import IntentPlanner, ExecutionPlan
from core.monitoring import get_performance_monitor, PerformanceContext
import logging
import time

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v3/orchestrator", tags=["orchestrator"])


# Pydantic模型
class TaskPlanRequest(BaseModel):
    """任务规划请求"""
    user_input: str
    user_id: Optional[str] = None


class TaskPlanResponse(BaseModel):
    """任务规划响应"""
    success: bool
    tasks: List[Dict[str, Any]] = []
    error: Optional[str] = None


class TaskExecutionRequest(BaseModel):
    """任务执行请求"""
    user_input: str
    user_id: Optional[str] = None
    execute: bool = True  # 是否立即执行


class TaskExecutionResponse(BaseModel):
    """任务执行响应"""
    success: bool
    task_ids: List[str] = []
    results: List[Dict[str, Any]] = []
    error: Optional[str] = None


class PluginInvokeRequest(BaseModel):
    """插件调用请求"""
    plugin_id: Optional[str] = None  # 如果为 None，则使用自然语言查询
    method_name: Optional[str] = None  # 如果为 None，则使用自然语言查询
    payload: Optional[Dict[str, Any]] = None
    trace_id: Optional[str] = None
    user_query: Optional[str] = None  # 自然语言查询（用于自动规划）


class PluginInvokeResponse(BaseModel):
    """插件调用响应"""
    status_code: int
    error_message: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    ui_render_schema: Optional[str] = None
    data_payload: Optional[str] = None  # Base64 编码的 JSON bytes
    trace_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


# 依赖注入 - 创建全局实例（在实际应用中应该使用依赖注入容器）
_planner: Optional[TaskPlanner] = None
_scheduler: Optional[TaskScheduler] = None
_plugin_executor: Optional[PluginExecutor] = None
_intent_planner: Optional[IntentPlanner] = None


def get_task_planner() -> TaskPlanner:
    """获取任务规划器实例"""
    global _planner
    if _planner is None:
        # 创建组件
        intent_parser = IntentParser()
        cluster_manager = RayClusterManager()
        resource_allocator = ResourceAllocator(cluster_manager)
        from core.system.plugin_manager import get_plugin_manager
        plugin_manager = get_plugin_manager()
        device_registry = DeviceRegistry()
        
        _planner = TaskPlanner(
            intent_parser=intent_parser,
            resource_allocator=resource_allocator,
            plugin_manager=plugin_manager,
            device_registry=device_registry,
        )
    return _planner


def get_task_scheduler() -> TaskScheduler:
    """获取任务调度器实例"""
    global _scheduler
    if _scheduler is None:
        cluster_manager = RayClusterManager()
        _scheduler = TaskScheduler(cluster_manager)
    return _scheduler


def get_plugin_executor() -> PluginExecutor:
    """获取插件执行器实例"""
    global _plugin_executor
    if _plugin_executor is None:
        plugins_dir = Path("data/plugins")
        skills_repo_dir = Path("skills_repo")
        plugin_manager = PluginManager(plugins_dir, skills_repo_dir)
        _plugin_executor = PluginExecutor(plugin_manager)
    return _plugin_executor


def get_intent_planner() -> IntentPlanner:
    """获取意图规划器实例"""
    global _intent_planner
    if _intent_planner is None:
        plugins_dir = Path("data/plugins")
        skills_repo_dir = Path("skills_repo")
        plugin_manager = PluginManager(plugins_dir, skills_repo_dir)
        _intent_planner = IntentPlanner(plugin_manager)
    return _intent_planner


@router.post("/plan", response_model=TaskPlanResponse)
async def plan_task(
    request: TaskPlanRequest,
    planner: TaskPlanner = Depends(get_task_planner)
):
    """
    规划任务
    
    Args:
        request: 任务规划请求
    
    Returns:
        TaskPlanResponse: 任务规划结果
    """
    try:
        # 规划任务
        tasks = await planner.plan_task(
            user_input=request.user_input,
            user_id=request.user_id
        )
        
        # 转换为字典格式
        task_dicts = []
        for task in tasks:
            task_dicts.append({
                "task_id": task.task_id,
                "task_type": task.task_type.value,
                "status": task.status.value,
                "skill_id": task.skill_id,
                "capability_name": task.capability_name,
                "worker_node": task.worker_node,
                "priority": task.priority,
            })
        
        return TaskPlanResponse(
            success=True,
            tasks=task_dicts,
        )
        
    except Exception as e:
        logger.error(f"Failed to plan task: {e}", exc_info=True)
        return TaskPlanResponse(
            success=False,
            error=str(e),
        )


@router.post("/execute", response_model=TaskExecutionResponse)
async def execute_task(
    request: TaskExecutionRequest,
    planner: TaskPlanner = Depends(get_task_planner),
    scheduler: TaskScheduler = Depends(get_task_scheduler)
):
    """
    执行任务（规划并执行）
    
    Args:
        request: 任务执行请求
    
    Returns:
        TaskExecutionResponse: 执行结果
    """
    try:
        # 规划任务
        tasks = await planner.plan_task(
            user_input=request.user_input,
            user_id=request.user_id
        )
        
        if not tasks:
            return TaskExecutionResponse(
                success=False,
                error="No tasks generated",
            )
        
        task_ids = []
        results = []
        
        if request.execute:
            # 初始化Ray集群（如果需要）
            cluster_manager = RayClusterManager()
            if not cluster_manager.is_connected():
                await cluster_manager.initialize()
            
            # 提交并执行任务
            for task in tasks:
                try:
                    task_id = await scheduler.submit_task(task)
                    task_ids.append(task_id)
                    
                    # 等待任务完成
                    completed_task = await scheduler.wait_for_task(
                        task_id,
                        timeout=300  # 5分钟超时
                    )
                    
                    results.append({
                        "task_id": task_id,
                        "status": completed_task.status.value,
                        "result": completed_task.result,
                        "error": completed_task.error_message,
                    })
                except Exception as e:
                    logger.error(f"Failed to execute task {task.task_id}: {e}", exc_info=True)
                    results.append({
                        "task_id": task.task_id,
                        "status": "failed",
                        "error": str(e),
                    })
        else:
            # 只规划，不执行
            task_ids = [task.task_id for task in tasks]
        
        return TaskExecutionResponse(
            success=True,
            task_ids=task_ids,
            results=results,
        )
        
    except Exception as e:
        logger.error(f"Failed to execute task: {e}", exc_info=True)
        return TaskExecutionResponse(
            success=False,
            error=str(e),
        )


@router.get("/intent")
async def parse_intent(
    user_input: str,
    parser: IntentParser = Depends(lambda: IntentParser())
):
    """
    解析用户意图（调试用）
    
    Args:
        user_input: 用户输入
    
    Returns:
        Dict: 解析后的意图
    """
    try:
        intent = await parser.parse_intent(user_input)
        return {
            "intent_type": intent.intent_type,
            "capability_name": intent.capability_name,
            "capability_type": intent.capability_type,
            "parameters": intent.parameters,
            "requires_device": intent.requires_device,
            "device_capability": intent.device_capability,
            "confidence": intent.confidence,
        }
    except Exception as e:
        logger.error(f"Failed to parse intent: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/invoke", response_model=PluginInvokeResponse)
async def invoke_plugin(
    request: PluginInvokeRequest,
    executor: PluginExecutor = Depends(get_plugin_executor),
    planner: IntentPlanner = Depends(get_intent_planner)
):
    """
    调用插件（通用接口）
    
    支持两种模式：
    1. 直接调用：提供 plugin_id 和 method_name
    2. 智能规划：提供 user_query，自动匹配插件和方法
    
    支持调用 .jsp 插件和 bundled skills
    
    Args:
        request: 插件调用请求
        executor: 插件执行器
        planner: 意图规划器
    
    Returns:
        PluginInvokeResponse: 插件调用结果（包含 SDUI Schema）
    """
    import json
    import base64
    
    monitor = get_performance_monitor()
    start_time = time.time()
    
    try:
        plugin_id = request.plugin_id
        method_name = request.method_name
        
        # 如果提供了 user_query，使用意图规划器自动匹配
        if request.user_query and (not plugin_id or not method_name):
            logger.info(f"Planning execution for query: {request.user_query}")
            
            # 监控意图规划性能
            planning_start = time.time()
            plan = await planner.plan(request.user_query)
            planning_duration = time.time() - planning_start
            
            monitor.record(
                "intent.planning",
                planning_duration,
                success=plan is not None,
                tags={"user_query": request.user_query[:50]}  # 限制长度
            )
            
            if plan:
                plugin_id = plan.plugin_id
                method_name = plan.method_name
                # 合并规划的参数和请求的参数
                if request.payload:
                    plan.parameters.update(request.payload)
                request.payload = plan.parameters
                logger.info(
                    f"Planned: {plugin_id}.{method_name} "
                    f"(confidence: {plan.confidence:.2f})"
                )
            else:
                raise HTTPException(
                    status_code=404,
                    detail=f"Could not find a matching plugin for query: {request.user_query}"
                )
        
        # 验证必需字段
        if not plugin_id or not method_name:
            raise HTTPException(
                status_code=400,
                detail="Either provide (plugin_id, method_name) or user_query"
            )
        
        # 准备 payload
        payload_bytes = json.dumps(request.payload or {}).encode('utf-8') if request.payload else b'{}'
        
        # 调用插件执行器
        result = await executor.invoke_plugin(
            plugin_id=plugin_id,
            method_name=method_name,
            payload=payload_bytes,
            trace_id=request.trace_id
        )
        
        # 解析 payload（如果是 JSON）
        payload_dict = None
        if result.get("payload"):
            try:
                payload_dict = json.loads(result["payload"].decode('utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError):
                # 如果不是 JSON，返回 base64 编码
                payload_dict = {"raw": base64.b64encode(result["payload"]).decode('utf-8')}
        
        # 处理 data_payload（如果有）
        data_payload_b64 = None
        if result.get("data_payload"):
            data_payload_b64 = base64.b64encode(result["data_payload"]).decode('utf-8')
        
        # 构建技能链（供 SkillChainView 展示）
        metadata = dict(result.get("metadata") or {})
        if "chain" not in metadata and plugin_id and method_name:
            q = (request.user_query or "")[:30]
            metadata["chain"] = [
                {"id": "1", "label": "用户输入" + (f": 「{q}…」" if q else ""), "type": "input"},
                {"id": "2", "label": f"调用: {plugin_id}.{method_name}", "type": "skill"},
                {"id": "3", "label": "完成" if not result.get("error_message") else "结束", "type": "done"},
            ]
        
        return PluginInvokeResponse(
            status_code=result.get("status_code", 200),
            error_message=result.get("error_message"),
            payload=payload_dict,
            ui_render_schema=result.get("ui_render_schema"),
            data_payload=data_payload_b64,
            trace_id=result.get("trace_id") or request.trace_id,
            metadata=metadata
        )
        
    except Exception as e:
        logger.error(f"Failed to invoke plugin: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
