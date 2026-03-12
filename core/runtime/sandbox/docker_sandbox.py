"""
Docker 沙箱实现
Docker Sandbox Implementation
"""

import docker
import logging
import json
import asyncio
from typing import Dict, Any, Optional
from pathlib import Path
from core.runtime.sandbox.base import BaseSandbox
from core.config import settings

logger = logging.getLogger(__name__)


class DockerSandbox(BaseSandbox):
    """Docker沙箱实现"""
    
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化Docker沙箱
        
        Args:
            config: 沙箱配置
        """
        super().__init__(config or {})
        try:
            self.client = docker.from_env()
            logger.info("Docker client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Docker client: {e}")
            raise
        
        # 默认配置
        self.image_prefix = self.config.get("image_prefix", "jachin-skill")
        self.network = self.config.get("network", "jachin-network")
        self.memory_limit = self.config.get("memory_limit", "512m")
        self.cpu_limit = self.config.get("cpu_limit", "0.5")
        self.timeout = self.config.get("timeout", 300)
    
    async def create(self, skill_id: str, config: Dict[str, Any]) -> bool:
        """
        创建Docker容器
        
        Args:
            skill_id: 技能ID
            config: 容器配置
        
        Returns:
            bool: 是否成功创建
        """
        try:
            # 构建镜像名称
            image_name = f"{self.image_prefix}:{skill_id}"
            
            # 检查镜像是否存在
            try:
                self.client.images.get(image_name)
            except docker.errors.ImageNotFound:
                logger.warning(f"Docker image {image_name} not found, trying to build...")
                # TODO: 实现镜像构建逻辑
                return False
            
            # 创建容器
            container_config = {
                "image": image_name,
                "name": f"jachin-skill-{skill_id}",
                "detach": True,
                "network": self.network,
                "mem_limit": config.get("memory_limit", self.memory_limit),
                "cpu_quota": int(float(config.get("cpu_limit", self.cpu_limit)) * 100000),
                "cpu_period": 100000,
                "auto_remove": False,
            }
            
            container = self.client.containers.create(**container_config)
            self._containers[skill_id] = container
            
            logger.info(f"Docker container created for skill {skill_id}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create Docker container for skill {skill_id}: {e}", exc_info=True)
            return False
    
    async def execute(
        self,
        skill_id: str,
        command: str,
        input_data: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        在Docker容器中执行命令
        
        Args:
            skill_id: 技能ID
            command: 执行的命令
            input_data: 输入数据
            timeout: 超时时间（秒）
        
        Returns:
            Dict: 执行结果
        """
        if skill_id not in self._containers:
            # 尝试获取现有容器
            try:
                container = self.client.containers.get(f"jachin-skill-{skill_id}")
                self._containers[skill_id] = container
            except docker.errors.NotFound:
                return {
                    "success": False,
                    "error": f"Container not found for skill {skill_id}",
                }
        
        container = self._containers[skill_id]
        
        try:
            # 准备执行命令
            exec_command = command
            if input_data:
                # 将输入数据作为环境变量或stdin传递
                exec_command = f"{command} '{json.dumps(input_data)}'"
            
            # 执行命令
            exec_result = container.exec_run(
                exec_command,
                timeout=timeout or self.timeout,
            )
            
            # 解析结果
            exit_code = exec_result.exit_code
            output = exec_result.output.decode("utf-8") if exec_result.output else ""
            
            if exit_code == 0:
                try:
                    result_data = json.loads(output)
                except json.JSONDecodeError:
                    result_data = {"output": output}
                
                return {
                    "success": True,
                    "result": result_data,
                    "exit_code": exit_code,
                }
            else:
                return {
                    "success": False,
                    "error": output,
                    "exit_code": exit_code,
                }
                
        except Exception as e:
            logger.error(f"Failed to execute command in container for skill {skill_id}: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
            }
    
    async def destroy(self, skill_id: str) -> bool:
        """
        销毁Docker容器
        
        Args:
            skill_id: 技能ID
        
        Returns:
            bool: 是否成功销毁
        """
        if skill_id not in self._containers:
            # 尝试获取现有容器
            try:
                container = self.client.containers.get(f"jachin-skill-{skill_id}")
            except docker.errors.NotFound:
                logger.warning(f"Container not found for skill {skill_id}")
                return True
        else:
            container = self._containers[skill_id]
        
        try:
            container.stop()
            container.remove()
            del self._containers[skill_id]
            logger.info(f"Docker container destroyed for skill {skill_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to destroy container for skill {skill_id}: {e}", exc_info=True)
            return False
    
    async def health_check(self, skill_id: str) -> bool:
        """
        检查Docker容器健康状态
        
        Args:
            skill_id: 技能ID
        
        Returns:
            bool: 是否健康
        """
        if skill_id not in self._containers:
            try:
                container = self.client.containers.get(f"jachin-skill-{skill_id}")
                self._containers[skill_id] = container
            except docker.errors.NotFound:
                return False
        
        container = self._containers[skill_id]
        
        try:
            container.reload()
            return container.status == "running"
        except Exception as e:
            logger.error(f"Failed to check container health for skill {skill_id}: {e}")
            return False
