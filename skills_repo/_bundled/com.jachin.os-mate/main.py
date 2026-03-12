"""
com.jachin.os-mate - 系统管家
继承 BaseSkill，实现 shutdown、reboot、volume_set、desktop_notify
desktop_notify 用于 Sentinel Level 1 触达用户
"""

import platform
import subprocess
import logging
from typing import Dict, Any

from core.skills.base_skill import BaseSkill

logger = logging.getLogger(__name__)


class OSMateSkill(BaseSkill):
    """系统管家技能"""

    def __init__(self, manifest: Dict[str, Any]):
        super().__init__(manifest)

    async def shutdown(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """关闭系统"""
        try:
            delay = params.get("delay_seconds", 0)
            if platform.system() == "Windows":
                subprocess.run(["shutdown", "/s", "/t", str(delay)], check=True)
            elif platform.system() in ("Linux", "Darwin"):
                mins = max(0, delay // 60)
                subprocess.run(["shutdown", "-h", f"+{mins}"], check=True)
            return {"success": True, "message": f"System will shutdown in {delay}s"}
        except Exception as e:
            logger.error(f"Shutdown failed: {e}")
            return {"success": False, "error": str(e)}

    async def reboot(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """重启系统"""
        try:
            delay = params.get("delay_seconds", 0)
            if platform.system() == "Windows":
                subprocess.run(["shutdown", "/r", "/t", str(delay)], check=True)
            elif platform.system() in ("Linux", "Darwin"):
                mins = max(0, delay // 60)
                subprocess.run(["shutdown", "-r", f"+{mins}"], check=True)
            return {"success": True, "message": f"System will reboot in {delay}s"}
        except Exception as e:
            logger.error(f"Reboot failed: {e}")
            return {"success": False, "error": str(e)}

    async def volume_set(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """设置系统音量（占位，需平台特定实现）"""
        vol = params.get("volume", 50)
        return {"success": True, "message": f"Volume set to {vol} (placeholder)"}

    async def desktop_notify(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """桌面通知 - 用于 Sentinel user.reach Level 1"""
        title = params.get("title", "提醒")
        message = params.get("message", "您有一条待确认事项")
        try:
            if platform.system() == "Windows":
                # PowerShell 弹窗
                ps = f'[System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms"); [System.Windows.Forms.MessageBox]::Show("{message}", "{title}")'
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                subprocess.Popen(["powershell", "-Command", ps], creationflags=flags)
            elif platform.system() == "Darwin":
                subprocess.run(["osascript", "-e", f'display notification "{message}" with title "{title}"'], check=True)
            else:
                subprocess.run(["notify-send", title, message], check=True)
            return {"success": True, "message": "Notification sent"}
        except Exception as e:
            logger.warning(f"Desktop notify failed: {e}")
            return {"success": False, "error": str(e)}
