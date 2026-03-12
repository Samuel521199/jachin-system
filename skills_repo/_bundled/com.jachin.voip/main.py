"""
com.jachin.voip - 网络电话 (Mock 版)
模拟打印 "Calling user..."，manifest 标记 capability 为 user.reach
"""

import logging
from typing import Dict, Any

from core.skills.base_skill import BaseSkill

logger = logging.getLogger(__name__)


class VoipSkill(BaseSkill):
    """网络电话技能 (Mock)"""

    def __init__(self, manifest: Dict[str, Any]):
        super().__init__(manifest)

    async def voip_call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Mock: 模拟拨打电话"""
        title = params.get("title", "提醒")
        message = params.get("message", "您有一条待确认事项")
        logger.info("Calling user... title=%s message=%s", title, message)
        print(f"[VoIP Mock] Calling user... title={title} message={message}")
        return {"success": True, "message": "Call initiated (mock)"}
