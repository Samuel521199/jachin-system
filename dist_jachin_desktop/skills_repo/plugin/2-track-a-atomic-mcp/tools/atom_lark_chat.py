"""
原子 Tool: atom_lark_chat
Lark 机器人 AI 对话核心逻辑。

L3 壳模式（配置 L3_WS_URL）：
  - Lark 仅做转发，不调用任何 LLM/API
  - 所有消息 → 转发给 Jachin L3 WebSocket → L3 Agent + MCP 执行 → 回复回传 Lark

独立模式（未配置 L3_WS_URL）：
  - 普通问题 → 阿里百炼生成回复
  - 任务关键词 → 记录到 data/lark_tasks.json，返回「已记录」

供 MCP、Webhook 处理器、交互脚本共同调用。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
_TASKS_FILE = _ROOT / "data" / "lark_tasks.json"

# 任务关键词：命中则视为「做任务」，只记录不执行
TASK_KEYWORDS = [
    "同步", "多维表", "多维表格", "bitable",
    "抓取", "收网", "简历", "harvest",
    "发布", "发职位", "post", "职位发布",
    "打招呼", "greet", "推荐牛人",
    "求简历", "request",
    "执行", "运行", "跑一下",
]


def _ensure_dotenv() -> None:
    """确保加载 plugin/.env，包括 L3_WS_URL；load_dotenv 不覆盖已存在的变量"""
    try:
        from dotenv import load_dotenv
        load_dotenv(_ROOT / ".env")
    except ImportError:
        pass


def _is_task_request(text: str) -> bool:
    """判断是否为任务请求"""
    if not text or not text.strip():
        return False
    t = text.strip().lower()
    for kw in TASK_KEYWORDS:
        if kw.lower() in t or kw in text:
            return True
    return False


def _record_task(user_id: str, chat_id: str, text: str) -> None:
    """记录任务到文件"""
    _TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tasks = []
    if _TASKS_FILE.exists():
        try:
            tasks = json.loads(_TASKS_FILE.read_text(encoding="utf-8"))
        except Exception:
            tasks = []
    tasks.append({
        "user_id": user_id,
        "chat_id": chat_id,
        "text": text,
        "recorded_at": datetime.now().isoformat(),
    })
    _TASKS_FILE.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("已记录任务: %s", text[:50])


def _to_chat_reply(raw: str) -> str:
    """
    将 LLM 返回转为适合聊天展示的文案。
    若返回的是 JSON（如 fallback 的 verdict/reason），转为友好自然语言。
    """
    if not raw or not raw.strip():
        return "抱歉，没有收到有效回复，请稍后再试～"
    raw = raw.strip()
    # 检测是否为 fallback/评审类的 JSON 输出
    if (raw.startswith("{") and ("verdict" in raw or "reason" in raw or "brief" in raw)):
        try:
            obj = json.loads(raw)
            reason = obj.get("reason") or obj.get("brief") or obj.get("summary", "")
            if "[规则模式]" in reason or "DASHSCOPE" in reason or "API_KEY" in reason:
                return "抱歉，AI 服务暂时繁忙，请稍后再试～如有紧急需求可联系管理员。"
            if reason:
                return reason
        except json.JSONDecodeError:
            pass
    return raw


def _call_l3_ws(intent: str, chat_id: str = "") -> str:
    """
    将用户意图转发到 Jachin L3 WebSocket，由 L3 Agent + MCP 完成。
    协议：发送 intent (+ chat_id) → 收取 thought/action/observation/chunk/answer，整合后返回。
    chat_id 用于 L3 持久化会话，否则 Lark 每次新建连接导致「同意」等回复无法获取上一轮 JD 配置。
    """
    _ensure_dotenv()
    url = os.environ.get("L3_WS_URL", "").strip()
    if not url:
        return ""
    try:
        import websockets
    except ImportError:
        logger.warning("转发 L3 需要 websockets: pip install websockets")
        return ""

    # 支持多端口：配置逗号分隔，或自动尝试 18982、18983
    import re
    if "," in url:
        urls_to_try = [u.strip() for u in url.split(",") if u.strip()]
    else:
        urls_to_try = [url]
        m = re.search(r":(\d+)(/|$)", url)
        if m:
            base_port = int(m.group(1))
            for p in (base_port + 1, base_port + 2):
                if p not in (18888, 18990):
                    urls_to_try.append(re.sub(r":\d+(/|$)", f":{p}\\1", url))

    async def _do() -> str:
        answer = ""
        terminal_lines = []  # 终端输出：thought/action/observation，同步到 Lark

        # L3 启动需网关审批约 5–8 秒，首次连接失败时重试（最多 4 次，间隔 3 秒）
        max_retries = 4
        retry_delay = 3.0

        for attempt in range(max_retries):
            for try_url in urls_to_try:
                try:
                    logger.info("L3 连接尝试 [%d/%d]: %s", attempt + 1, max_retries, try_url)
                    async with websockets.connect(try_url, open_timeout=10, close_timeout=5) as ws:
                        logger.info("L3 已连接，发送 intent chat_id=%s", (chat_id or "")[:20] if chat_id else "无")
                        await ws.send(json.dumps({"type": "manifest", "caps": ["stream_chunk"]}, ensure_ascii=False))
                        payload = {"intent": intent, "origin": "lark"}
                        if chat_id and str(chat_id).strip():
                            payload["chat_id"] = str(chat_id).strip()
                        await ws.send(json.dumps(payload, ensure_ascii=False))
                        async for raw in ws:
                            try:
                                msg = json.loads(raw) if isinstance(raw, str) else {}
                            except json.JSONDecodeError:
                                continue
                            st = msg.get("step_type", "")
                            content = (msg.get("content") or "").strip()
                            if st == "answer":
                                answer = content
                                break
                            if st == "error":
                                return f"L3 执行出错：{content}" if content else "L3 执行出错，请稍后重试"
                            if st == "chunk":
                                answer += content
                            if st in ("thought", "action", "observation") and content:
                                terminal_lines.append(f"【{st}】{content[:200]}{'...' if len(content) > 200 else ''}")
                    break  # 成功则跳出端口重试
                except Exception as e:
                    logger.warning("L3 连接失败 %s: %s", try_url, e)
                    if try_url == urls_to_try[-1] and attempt == max_retries - 1:
                        return (
                            f"无法连接 L3 服务（{e}），请确认：\n"
                            "1) Jachin 桌面端已打开（L3 由桌面端启动）\n"
                            "2) 或已运行 scripts/run_l3.ps1\n"
                            "3) 端口 18981 未被占用\n"
                            "4) MCP 与 L3 同机运行（127.0.0.1 仅限本机；云上 MCP 无法连本机 L3）"
                        )
                    continue
            else:
                # 所有 URL 均失败，等待后重试
                if attempt < max_retries - 1:
                    logger.info("L3 未就绪，%s 秒后重试...", retry_delay)
                    await asyncio.sleep(retry_delay)
                continue
            break  # 成功则跳出重试

        if not answer:
            return "[L3 未返回有效回复]"
        logger.info("L3 返回成功，len=%d", len(answer))
        # 终端输出同步到 Lark：若有 thought/action， append 到回复前（简要）
        if terminal_lines:
            summary = "\n".join(terminal_lines[-5:])  # 最多 5 条
            return f"{summary}\n\n---\n{answer}"
        return answer

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        return asyncio.run(_do())
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as pool:
        return pool.submit(asyncio.run, _do()).result()


def _call_bailian(prompt: str) -> str:
    """同步调用阿里百炼"""
    _ensure_dotenv()
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

    async def _do():
        try:
            from src.llm_client import invoke_llm_with_model
            model = os.environ.get("LARK_BOT_LLM_MODEL", "qwen-plus")
            raw = await invoke_llm_with_model(
                prompt,
                "你是 HR 招聘辅助机器人，简洁友好地回答问题。若涉及具体操作（同步、抓取、发布等），请告知已记录任务。必须用自然语言回复，不要输出 JSON。",
                model,
            )
            return _to_chat_reply(raw or "")
        except Exception as e:
            logger.warning("百炼调用失败: %s", e)
            return "抱歉，暂时无法生成回复，请稍后再试～"

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        return asyncio.run(_do())
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as pool:
        return pool.submit(asyncio.run, _do()).result()


def process_lark_message(user_text: str, chat_id: str = "", user_id: str = "") -> dict:
    """
    处理用户消息。

    - 若配置了 L3_WS_URL：全部转发给 Jachin L3（含任务），由 L3 Agent + MCP 完成
    - 否则：任务记录到本地；普通问题用百炼回复

    :param user_text: 用户输入
    :param chat_id: 会话 ID（用于任务记录）
    :param user_id: 用户 ID（用于任务记录）
    :return: {"reply": str, "is_task": bool}
    """
    _ensure_dotenv()
    if not user_text or not user_text.strip():
        return {"reply": "", "is_task": False}

    l3_url = os.environ.get("L3_WS_URL", "").strip()
    # L3 模式：转发给 Jachin，由 L3 调用 MCP 工具；传入 chat_id 以便 L3 持久化会话（「同意」时能拿到上一轮 JD）
    if l3_url:
        logger.info("L3 壳模式: 转发到 %s chat_id=%s", l3_url, (chat_id or "")[:20] if chat_id else "无")
        reply = _call_l3_ws(user_text.strip(), chat_id=chat_id or "")
        return {"reply": reply or "L3 未返回回复", "is_task": False}

    # 独立模式：任务记录，普通问题百炼
    if _is_task_request(user_text):
        _record_task(user_id or "unknown", chat_id or "", user_text)
        snippet = user_text[:50] + ("..." if len(user_text) > 50 else "")
        return {"reply": f"已记录您的任务：「{snippet}」，稍后处理～", "is_task": True}
    reply = _call_bailian(user_text.strip())
    return {"reply": reply, "is_task": False}


def atom_lark_chat(user_text: str, chat_id: str = "", user_id: str = "") -> dict:
    """
    MCP 原子工具：处理用户消息，返回回复文案。
    若为任务请求，会记录到 data/lark_tasks.json。
    """
    try:
        out = process_lark_message(user_text, chat_id, user_id)
        return {"success": True, "reply": out["reply"], "is_task": out["is_task"]}
    except Exception as e:
        logger.exception("atom_lark_chat failed")
        return {"success": False, "reply": "", "is_task": False, "error": str(e)}
