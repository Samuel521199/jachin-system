# 蓝队业务闭环测试 - 合法插件
# 声明 internet.access，使用 requests 请求天气 API
# 暴露标准 setup(agent_context) 入口

import json
from datetime import datetime

# 仅当 manifest 声明 internet.access 时，requests 才被允许
try:
    import requests
except ImportError:
    requests = None


def get_current_time() -> str:
    """获取当前本地时间"""
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S")


def get_weather(city: str = "Beijing") -> str:
    """获取天气（使用公共 API，仅演示）"""
    if requests is None:
        return "requests 未安装，无法获取天气"
    try:
        # 使用 wttr.in 公共 API（无需 key）
        url = f"https://wttr.in/{city}?format=j1"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            current = data.get("current_condition", [{}])[0]
            temp = current.get("temp_C", "?")
            desc = current.get("weatherDesc", [{}])[0].get("value", "未知")
            return f"{city}: {temp}°C, {desc}"
    except Exception as e:
        return f"天气查询失败: {e}"
    return "未知"


def setup(agent_context: dict) -> dict:
    """
    标准插件入口 - 供 Commander 路由调用
    
    Args:
        agent_context: 包含 user_id, session_id 等上下文
        
    Returns:
        能力注册表，供 Commander 路由到具体方法
    """
    return {
        "capabilities": [
            {"name": "get_time", "description": "获取当前本地时间，用于回答「现在几点了」等", "handler": handle_get_time, "parameters": {"type": "object", "properties": {}, "required": []}},
            {"name": "get_weather", "description": "查询指定城市的天气，用于回答「北京天气怎么样」等", "handler": handle_get_weather, "parameters": {"type": "object", "properties": {"city": {"type": "string", "description": "城市名，如 Beijing、上海"}}, "required": []}},
        ],
        "intent_keywords": ["几点了", "现在几点", "时间", "天气", "温度"],
    }


def handle_get_time(params: dict) -> dict:
    """处理「现在几点了」等意图"""
    return {
        "success": True,
        "text": f"现在是 {get_current_time()}",
        "tts": f"现在是 {get_current_time()}",
    }


def handle_get_weather(params: dict) -> dict:
    """处理天气查询意图"""
    city = params.get("city", "Beijing")
    result = get_weather(city)
    return {
        "success": True,
        "text": result,
        "tts": result,
    }
