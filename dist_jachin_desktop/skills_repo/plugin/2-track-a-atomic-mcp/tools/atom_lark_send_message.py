"""
原子 Tool: atom_lark_send_message
让 Lark 机器人主动发言：发送固定文案，或使用阿里百炼生成回复后发送。

前置条件：
  - LARK_APP_ID、LARK_APP_SECRET、LARK_CHAT_ID（目标群/单聊 ID）
  - 若 use_llm=True：DASHSCOPE_API_KEY
  - Lark 应用需有 im:message 权限，机器人需已加入目标群/单聊
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

LARK_API_BASE = "https://open.larksuite.com/open-apis"


def _ensure_dotenv_loaded() -> None:
    if os.environ.get("LARK_APP_ID") or os.environ.get("FEISHU_APP_ID"):
        return
    try:
        from dotenv import load_dotenv
        plugin_root = Path(__file__).resolve().parent.parent.parent
        env_path = plugin_root / ".env"
        if env_path.exists():
            load_dotenv(env_path)
        else:
            load_dotenv()
    except ImportError:
        pass


def _get_tenant_access_token() -> str:
    _ensure_dotenv_loaded()
    app_id = os.environ.get("LARK_APP_ID") or os.environ.get("FEISHU_APP_ID")
    app_secret = os.environ.get("LARK_APP_SECRET") or os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        raise ValueError("请配置 LARK_APP_ID 和 LARK_APP_SECRET")

    import requests
    url = f"{LARK_API_BASE}/auth/v3/tenant_access_token/internal"
    resp = requests.post(url, json={"app_id": app_id, "app_secret": app_secret}, timeout=10)
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"Lark token 失败: {data}")
    return data["tenant_access_token"]


def _send_lark_message(token: str, receive_id: str, text: str, receive_id_type: str = "chat_id") -> bool:
    """向 Lark 发送文本消息。供 atom_lark_send_message 和 lark_bot_conversation 调用"""
    if not receive_id or not text:
        return False
    try:
        import requests
        url = f"{LARK_API_BASE}/im/v1/messages"
        params = {"receive_id_type": receive_id_type}
        payload = {
            "receive_id": receive_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}),
        }
        resp = requests.post(
            url,
            params=params,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
        data = resp.json()
        if data.get("code") != 0:
            logger.warning("Lark 消息发送失败: %s", data.get("msg", data))
            return False
        return True
    except Exception as e:
        logger.warning("Lark 消息发送异常: %s", e)
        return False


def _call_bailian(prompt: str, system: str = "") -> str:
    """同步调用阿里百炼生成回复"""
    import sys
    root = Path(__file__).resolve().parent.parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    async def _do():
        try:
            from src.llm_client import invoke_llm_with_model
            model = os.environ.get("LARK_BOT_LLM_MODEL", "qwen-plus")
            return await invoke_llm_with_model(
                prompt,
                system or "你是 HR 招聘辅助机器人，简洁友好地回答问题。",
                model,
            )
        except Exception as e:
            logger.warning("百炼调用失败: %s", e)
            return f"[暂时无法生成回复: {e}]"

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        return asyncio.run(_do())
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as pool:
        return pool.submit(asyncio.run, _do()).result()


def atom_lark_list_tasks() -> dict:
    """列出已记录的任务（来自 Lark 对话中的任务请求）"""
    tasks_file = Path(__file__).resolve().parent.parent.parent / "data" / "lark_tasks.json"
    if not tasks_file.exists():
        return {"success": True, "tasks": [], "count": 0}
    try:
        tasks = json.loads(tasks_file.read_text(encoding="utf-8"))
        return {"success": True, "tasks": tasks, "count": len(tasks)}
    except Exception as e:
        return {"success": False, "tasks": [], "count": 0, "error": str(e)}


def atom_lark_send_message(
    text: str = "",
    prompt: str = "",
    use_llm: bool = False,
    chat_id: str = "",
    receive_id_type: str = "chat_id",
) -> dict:
    """
    让 Lark 机器人发言。

    :param text: 直接发送的文案（与 prompt 二选一）
    :param prompt: 用户提问/上下文，use_llm=True 时用百炼生成回复后发送
    :param use_llm: 是否用阿里百炼生成回复（需 prompt）
    :param chat_id: 目标 chat_id，不填则用 LARK_CHAT_ID
    :param receive_id_type: chat_id（群/单聊）或 user_id
    :return: {"success": bool, "message": str, "error": str|None}
    """
    _ensure_dotenv_loaded()
    target = chat_id or os.environ.get("LARK_CHAT_ID", "")
    if not target:
        return {"success": False, "message": "", "error": "请配置 LARK_CHAT_ID 或传入 chat_id"}

    to_send = ""
    if text:
        to_send = text
    elif prompt and use_llm:
        to_send = _call_bailian(prompt)
    elif prompt:
        to_send = prompt
    else:
        return {"success": False, "message": "", "error": "请提供 text 或 prompt（use_llm=True 时需 prompt）"}

    try:
        token = _get_tenant_access_token()
        ok = _send_lark_message(token, target, to_send, receive_id_type)
        if ok:
            return {"success": True, "message": to_send, "error": None}
        return {"success": False, "message": to_send, "error": "Lark API 发送失败"}
    except Exception as e:
        logger.exception("atom_lark_send_message failed")
        return {"success": False, "message": "", "error": str(e)}
