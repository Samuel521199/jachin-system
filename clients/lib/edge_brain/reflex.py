"""
Edge Reflex - 边缘反射引擎

极简指令本地匹配，命中则直接执行，不转发 Tier 2。
示例：音量调大、开灯、关灯、查询天气（若本地有 API）
"""

import re
from typing import Optional, Tuple, Dict, Any
from enum import Enum


class ReflexAction(str, Enum):
    """边缘可执行的本地动作"""
    LOCAL_HANDLED = "local_handled"   # 已本地处理
    FORWARD_TO_HIVE = "forward"       # 需转发 Tier 2


# 简单指令正则（可扩展）
SIMPLE_PATTERNS = [
    (r"^(音量|声音)(调大|加大|提高|大一点)$", "volume_up"),
    (r"^(音量|声音)(调小|减小|降低|小一点)$", "volume_down"),
    (r"^(静音|mute)$", "mute"),
    (r"^(开灯|打开灯|亮灯)$", "light_on"),
    (r"^(关灯|关闭灯|熄灯)$", "light_off"),
]


def check_reflex(user_input: str) -> Tuple[ReflexAction, Optional[str], Optional[Dict]]:
    """
    检查是否可边缘反射处理
    
    Returns:
        (action, intent_type, params): 
        - LOCAL_HANDLED: (action, "volume_up", {"delta": 10})
        - FORWARD_TO_HIVE: (action, None, None)
    """
    text = user_input.strip().lower()
    for pattern, intent in SIMPLE_PATTERNS:
        if re.match(pattern, text):
            return ReflexAction.LOCAL_HANDLED, intent, {}
    return ReflexAction.FORWARD_TO_HIVE, None, None
