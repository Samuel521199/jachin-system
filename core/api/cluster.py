"""
集群管理API
Cluster Management API
"""

from fastapi import APIRouter, HTTPException, Depends, Query, status
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from core.brain.ray_cluster.cluster_manager import RayClusterManager
from core.brain.ray_cluster.task_scheduler import TaskScheduler
from core.brain.ray_cluster.resource_monitor import ResourceMonitor
from core.brain.ray_cluster.task_types import RayTask, TaskType, TaskStatus
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from core.memory.schema import get_db, Task, ClusterNode
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v3/cluster", tags=["cluster"])


# Pydantic模型
class NodeInfo(BaseModel):
    """节点信息"""
    node_id: str
    node_type: str
    host: str
    port: int
    status: str
    resources: Dict[str, Any] = {}


class TaskInfo(BaseModel):
    """任务信息"""
    task_id: str
    task_type: str
    status: str
    skill_id: Optional[str] = None
    capability_name: Optional[str] = None
    worker_node: Optional[str] = None
    created_at: Optional[str] = None
    completed_at: Optional[str] = None


class ClusterStats(BaseModel):
    """集群统计信息"""
    nodes: Dict[str, int] = {}  # 按状态分组的节点数
    tasks: Dict[str, int] = {}  # 按状态分组的任务数
    resources: Dict[str, Any] = {}
    utilization: Dict[str, float] = {}


# 依赖注入
def get_cluster_manager() -> RayClusterManager:
    """获取集群管理器实例（仅构造，不连接 Ray）"""
    return RayClusterManager()


def get_task_scheduler(manager: RayClusterManager = Depends(get_cluster_manager)) -> TaskScheduler:
    """获取任务调度器实例"""
    return TaskScheduler(manager)


def get_resource_monitor(manager: RayClusterManager = Depends(get_cluster_manager)) -> ResourceMonitor:
    """获取资源监控器实例"""
    return ResourceMonitor(manager)


@router.get("/nodes", response_model=List[NodeInfo])
async def list_nodes(
    manager: RayClusterManager = Depends(get_cluster_manager)
):
    """
    列出所有集群节点
    
    Returns:
        List[NodeInfo]: 节点列表
    """
    try:
        cluster_info = manager.get_cluster_info()
        nodes = cluster_info.get("nodes", [])
        
        node_list = []
        for i, node in enumerate(nodes):
            node_type = "master" if i == 0 else "worker"
            node_list.append(NodeInfo(
                node_id=node.get("node_id", "unknown"),
                node_type=node_type,
                host=node.get("host", ""),
                port=node.get("port", 0),
                status="online" if node.get("alive") else "offline",
                resources=node.get("resources", {}),
            ))
        
        return node_list
        
    except Exception as e:
        logger.error(f"Failed to list nodes: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/nodes/{node_id}", response_model=NodeInfo)
async def get_node(
    node_id: str,
    manager: RayClusterManager = Depends(get_cluster_manager)
):
    """
    获取节点详情
    
    Args:
        node_id: 节点ID
    
    Returns:
        NodeInfo: 节点信息
    """
    try:
        monitor = ResourceMonitor(manager)
        node_info = monitor.get_node_resources(node_id)
        
        if "error" in node_info:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=node_info["error"]
            )
        
        return NodeInfo(
            node_id=node_id,
            node_type="worker",
            host="",
            port=0,
            status="online",
            resources=node_info.get("resources", {}),
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get node {node_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/tasks", response_model=List[TaskInfo])
async def list_tasks(
    status_filter: Optional[str] = Query(None, alias="status"),
    skill_id: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db)
):
    """
    列出所有任务
    
    Args:
        status_filter: 状态过滤
        skill_id: 技能ID过滤
        limit: 返回数量限制
    
    Returns:
        List[TaskInfo]: 任务列表
    """
    try:
        query = select(Task)
        
        if status_filter:
            query = query.where(Task.status == status_filter)
        
        if skill_id:
            query = query.where(Task.skill_id == skill_id)
        
        query = query.order_by(Task.created_at.desc()).limit(limit)
        
        async for session in get_db():
            result = await session.execute(query)
            task_records = result.scalars().all()
            
            tasks = []
            for task_record in task_records:
                tasks.append(TaskInfo(
                    task_id=task_record.task_id,
                    task_type=task_record.task_type,
                    status=task_record.status,
                    skill_id=str(task_record.skill_id) if task_record.skill_id else None,
                    capability_name=task_record.capability_name,
                    worker_node=task_record.worker_node,
                    created_at=task_record.created_at.isoformat() if task_record.created_at else None,
                    completed_at=task_record.completed_at.isoformat() if task_record.completed_at else None,
                ))
            
            return tasks
        
    except Exception as e:
        logger.error(f"Failed to list tasks: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/tasks/{task_id}", response_model=TaskInfo)
async def get_task(
    task_id: str,
    scheduler: TaskScheduler = Depends(get_task_scheduler),
    db: AsyncSession = Depends(get_db)
):
    """
    获取任务详情
    
    Args:
        task_id: 任务ID
    
    Returns:
        TaskInfo: 任务信息
    """
    try:
        # 先从调度器获取
        ray_task = scheduler.get_task(task_id)
        if ray_task:
            return TaskInfo(
                task_id=ray_task.task_id,
                task_type=ray_task.task_type.value,
                status=ray_task.status.value,
                skill_id=ray_task.skill_id,
                capability_name=ray_task.capability_name,
                worker_node=ray_task.worker_node,
                created_at=ray_task.created_at.isoformat() if ray_task.created_at else None,
                completed_at=ray_task.completed_at.isoformat() if ray_task.completed_at else None,
            )
        
        # 从数据库获取
        async for session in get_db():
            result = await session.execute(
                select(Task).where(Task.task_id == task_id)
            )
            task_record = result.scalar_one_or_none()
            
            if not task_record:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Task {task_id} not found"
                )
            
            return TaskInfo(
                task_id=task_record.task_id,
                task_type=task_record.task_type,
                status=task_record.status,
                skill_id=str(task_record.skill_id) if task_record.skill_id else None,
                capability_name=task_record.capability_name,
                worker_node=task_record.worker_node,
                created_at=task_record.created_at.isoformat() if task_record.created_at else None,
                completed_at=task_record.completed_at.isoformat() if task_record.completed_at else None,
            )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get task {task_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_task(
    task_id: str,
    scheduler: TaskScheduler = Depends(get_task_scheduler)
):
    """
    取消任务
    
    Args:
        task_id: 任务ID
    
    Returns:
        204 No Content
    """
    try:
        success = await scheduler.cancel_task(task_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task {task_id} not found or cannot be cancelled"
            )
        return None
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to cancel task {task_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


def _default_cluster_stats() -> ClusterStats:
    """Ray/集群不可用时返回的默认统计，避免前端 500."""
    return ClusterStats(
        nodes={"online": 0, "offline": 0, "total": 0},
        tasks={"pending": 0, "running": 0, "completed": 0, "failed": 0, "total": 0},
        resources={},
        utilization={},
    )


@router.get("/stats", response_model=ClusterStats)
async def get_cluster_stats(
    manager: RayClusterManager = Depends(get_cluster_manager),
    monitor: ResourceMonitor = Depends(get_resource_monitor),
    scheduler: TaskScheduler = Depends(get_task_scheduler)
):
    """
    获取集群统计信息。当 Ray/集群不可用时返回空统计（200），不返回 500，便于控制台 Horizon/MindStream 正常展示。
    """
    try:
        # 获取节点统计
        cluster_info = manager.get_cluster_info()
        nodes = cluster_info.get("nodes", [])
        node_stats = {
            "online": sum(1 for n in nodes if n.get("alive", False)),
            "offline": sum(1 for n in nodes if not n.get("alive", False)),
            "total": len(nodes),
        }
        # 获取任务统计
        scheduler_stats = scheduler.get_statistics()
        task_stats = {
            "pending": scheduler_stats.get("pending", 0),
            "running": scheduler_stats.get("running", 0),
            "completed": scheduler_stats.get("completed", 0),
            "failed": scheduler_stats.get("failed", 0),
            "total": scheduler_stats.get("total", 0),
        }
        # 获取资源信息
        resources = monitor.get_cluster_resources()
        utilization = monitor.get_resource_utilization()
        return ClusterStats(
            nodes=node_stats,
            tasks=task_stats,
            resources=resources.get("available", {}),
            utilization=utilization,
        )
    except Exception as e:
        logger.warning("Cluster stats unavailable, returning empty stats: %s", e)
        return _default_cluster_stats()
