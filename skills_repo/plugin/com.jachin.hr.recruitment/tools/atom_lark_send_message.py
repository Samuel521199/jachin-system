"""
原子 Tool: atom_lark_send_message
让 Lark 机器人主动发言：发送固定文案，或使用阿里百炼生成回复后发送。

前置条件：
  - LARK_APP_ID、LARK_APP_SECRET、LARK_CHAT_ID（目标群/单聊 ID）
  - 若 use_llm=True：由 L3 / 根目录 .env 提供百炼 Key（与主 Agent 一致，勿在插件 .env 单独配模型/Key）
  - Lark 应用需有 im:message 权限，机器人需已加入目标群/单聊

使用 l3_node.channels.lark 通道层实现。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# 确保 l3_node 可导入（plugin 脚本可能从 plugin 目录启动）
def _ensure_l3_importable() -> None:
    if "l3_node" in sys.modules:
        return
    _p = Path(__file__).resolve()
    for _ in range(5):
        _p = _p.parent
        if (_p / "l3_node").is_dir():
            _s = str(_p)
            if _s not in sys.path:
                sys.path.insert(0, _s)
            break


def _ensure_dotenv_loaded() -> None:
    """确保加载 .env（channels.lark.client 会按需加载，此处提前加载以便 LARK_CHAT_ID 等生效）"""
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
    """供 atom_lark_send_message、lark_bot 等调用。使用 HR 专用凭证（与通用 LARK_APP_ID 分离）。"""
    _ensure_l3_importable()
    from l3_node.channels.lark.client import get_lark_api_base, get_tenant_access_token, resolve_hr_lark_credentials

    _ensure_dotenv_loaded()
    aid, sec, yb = resolve_hr_lark_credentials()
    base = yb or get_lark_api_base()
    if aid and sec:
        return get_tenant_access_token(app_id=aid, app_secret=sec, api_base=base)
    return get_tenant_access_token()


def _send_lark_message(
    token: str, receive_id: str, text: str, receive_id_type: str = "chat_id"
) -> bool:
    """向 Lark 发送文本消息。供 atom_lark_send_message、lark_bot 调用。委托 channels.lark。"""
    _ensure_l3_importable()
    from l3_node.channels.lark import send_im_text

    result = send_im_text(
        receive_id=receive_id,
        text=text,
        receive_id_type=receive_id_type,
        token=token,
    )
    return result.get("status") == "success"


def _call_bailian(prompt: str, system: str = "") -> str:
    """同步调用阿里百炼生成回复"""
    import sys
    root = Path(__file__).resolve().parent.parent.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    async def _do():
        try:
            from src.llm_client import invoke_llm_with_model

            return await invoke_llm_with_model(
                prompt,
                system or "你是 HR 招聘辅助机器人，简洁友好地回答问题。",
                "",
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
    :param chat_id: 目标 chat_id / open_id / 邮箱 / 手机，不填则用 LARK_CHAT_ID（**勿填人名**）
    :param receive_id_type: chat_id、open_id 等与飞书 im 消息 API 一致
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
        _ensure_l3_importable()
        from l3_node.channels.lark.client import get_lark_api_base, get_tenant_access_token, resolve_hr_lark_credentials
        from l3_node.channels.lark.receive_resolve import normalize_lark_im_receive

        aid, sec, yb = resolve_hr_lark_credentials()
        if not aid or not sec:
            return {"success": False, "message": "", "error": "请配置 LARK_APP_ID / LARK_APP_SECRET"}
        base = yb or get_lark_api_base()
        token = get_tenant_access_token(app_id=aid, app_secret=sec, api_base=base)
        n_rid, n_rt, n_err = normalize_lark_im_receive(target, receive_id_type, token=token, api_base=base)
        if n_err:
            return {"success": False, "message": "", "error": n_err}
        target = n_rid
        receive_id_type = n_rt
        ok = _send_lark_message(token, target, to_send, receive_id_type)
        if ok:
            return {"success": True, "message": to_send, "error": None}
        return {"success": False, "message": to_send, "error": "Lark API 发送失败"}
    except Exception as e:
        logger.exception("atom_lark_send_message failed")
        return {"success": False, "message": "", "error": str(e)}
