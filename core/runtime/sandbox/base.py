"""
沙箱基类
Sandbox Base Class
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from core.runtime.interfaces import Sandbox


class BaseSandbox(Sandbox):
    """沙箱基类"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化沙箱

        Args:
            config: 沙箱配置
        """
        self.config = config
        self._containers: Dict[str, Any] = {}  # skill_id -> container/process

    @abstractmethod
    async def create(self, skill_id: str, config: Dict[str, Any]) -> bool:
        """创建沙箱环境"""
        pass

    @abstractmethod
    async def execute(
        self,
        skill_id: str,
        command: str,
        input_data: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """在沙箱中执行命令"""
        pass

    @abstractmethod
    async def destroy(self, skill_id: str) -> bool:
        """销毁沙箱环境"""
        pass

    @abstractmethod
    async def health_check(self, skill_id: str) -> bool:
        """检查沙箱健康状态"""
        pass
