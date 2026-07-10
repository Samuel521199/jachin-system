"""
技能运行时接口定义
Skill Runtime Interface Definitions
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List


class SkillRuntime(ABC):
    """技能运行时接口"""

    @abstractmethod
    async def load_skill(self, skill_id: str, manifest_path: str) -> bool:
        """
        加载技能

        Args:
            skill_id: 技能ID
            manifest_path: Manifest文件路径

        Returns:
            bool: 是否成功加载
        """
        pass

    @abstractmethod
    async def execute_capability(
        self,
        skill_id: str,
        capability_name: str,
        input_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        执行技能能力

        Args:
            skill_id: 技能ID
            capability_name: 能力名称
            input_data: 输入数据

        Returns:
            Dict: 执行结果
        """
        pass

    @abstractmethod
    async def unload_skill(self, skill_id: str) -> bool:
        """
        卸载技能

        Args:
            skill_id: 技能ID

        Returns:
            bool: 是否成功卸载
        """
        pass

    @abstractmethod
    async def health_check(self, skill_id: str) -> bool:
        """
        健康检查

        Args:
            skill_id: 技能ID

        Returns:
            bool: 是否健康
        """
        pass


class Sandbox(ABC):
    """沙箱接口"""

    @abstractmethod
    async def create(self, skill_id: str, config: Dict[str, Any]) -> bool:
        """
        创建沙箱环境

        Args:
            skill_id: 技能ID
            config: 沙箱配置

        Returns:
            bool: 是否成功创建
        """
        pass

    @abstractmethod
    async def execute(
        self,
        skill_id: str,
        command: str,
        input_data: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        在沙箱中执行命令

        Args:
            skill_id: 技能ID
            command: 执行的命令
            input_data: 输入数据
            timeout: 超时时间（秒）

        Returns:
            Dict: 执行结果
        """
        pass

    @abstractmethod
    async def destroy(self, skill_id: str) -> bool:
        """
        销毁沙箱环境

        Args:
            skill_id: 技能ID

        Returns:
            bool: 是否成功销毁
        """
        pass

    @abstractmethod
    async def health_check(self, skill_id: str) -> bool:
        """
        检查沙箱健康状态

        Args:
            skill_id: 技能ID

        Returns:
            bool: 是否健康
        """
        pass
