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
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from .config import get_data_root, get_plugin_package_root

_TASKS_FILE = get_data_root() / "lark_tasks.json"


def _feishu_text_implies_resume_harvest_focus(user_text: str) -> bool:
    """
    飞书整句是否明显是「只要收简历 / 先抓简历」，而非推荐打招呼为主。
    命中时合并 jd.json 会写 enable_greet_recommend=false，供 MCP 省略参数时从 jd 读取。
    """
    s = (user_text or "").strip()
    if not s:
        return False
    if re.search(
        r"(仅收网|只抓简历|仅抓简历|不要打招呼|别打招呼|先抓简历|直接抓简历|"
        r"只要简历|只收简历|只下载简历|跳过打招呼|不收打招呼)",
        s,
        re.I,
    ):
        return True
    if re.search(r"抓取.{1,400}\d+\s*份(?:简历)?", s, re.I):
        return True
    if re.search(r"\d+\s*份简历", s, re.I):
        return True
    return False


def _parse_resume_collect_target_from_feishu(user_text: str) -> int | None:
    m = re.search(r"(\d{1,4})\s*份(?:简历)?", (user_text or "").strip())
    if not m:
        return None
    n = int(m.group(1))
    if 1 <= n <= 9999:
        return n
    return None


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
    """确保加载 .env，包括 L3_WS_URL；load_dotenv 不覆盖已存在的变量"""
    try:
        from dotenv import load_dotenv
        for p in [get_plugin_package_root() / ".env", Path.home() / ".jachin" / ".env"]:
            if p.exists():
                load_dotenv(p)
                break
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
    """同步调用阿里百炼（独立模式，包内无 src 时返回提示）"""
    _ensure_dotenv()
    pkg_root = get_plugin_package_root()
    if str(pkg_root) not in sys.path:
        sys.path.insert(0, str(pkg_root))

    async def _do():
        try:
            from src.llm_client import invoke_llm_with_model

            raw = await invoke_llm_with_model(
                prompt,
                "你是 HR 招聘辅助机器人，简洁友好地回答问题。若涉及具体操作（同步、抓取、发布等），请告知已记录任务。必须用自然语言回复，不要输出 JSON。",
                "",
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


def apply_job_select_from_hr_im_text(user_text: str) -> dict[str, Any]:
    """
    从飞书整句提取 Boss 选岗行并写 jd.json / 必要时切换指针。

    供 ``process_lark_message`` 与 L3 ``try_lark_workflow_command_intercept`` 共用：
    IM 通道先命中「仅打招呼 / 再抓」等拦截时，也必须先落盘选岗，否则会沿用旧指针的 jd_select。
    """
    out: dict[str, Any] = {"applied": False, "jd_select": "", "job_folder": "", "job_name": ""}
    ut = (user_text or "").strip()
    if not ut:
        return out
    try:
        from .boss_utils import (
            canonicalize_boss_job_select,
            extract_job_select_line_for_boss_from_hr_chat,
            primary_job_title_from_boss_select_line,
        )
        from .hr_data_paths import (
            ensure_job_dirs_by_folder_key,
            get_job_jd_path_by_folder_key,
            infer_folder_key_from_job_display_name,
            init_job_jd_from_template,
            resolve_recruitment_data_folder_key,
            sanitize_job_folder,
        )
        from l3_node.local_memory import get_hr_recruitment_workflow_pointer

        sel = extract_job_select_line_for_boss_from_hr_chat(ut)
        if not sel:
            return out

        sel_c = canonicalize_boss_job_select(sel) or sel.strip()
        p2 = get_hr_recruitment_workflow_pointer()
        jn_cur = (p2.get("job_name") or "").strip()
        jf_cur = (p2.get("primary_job_folder") or p2.get("job_folder") or "").strip()
        derived = primary_job_title_from_boss_select_line(sel_c)
        fk_new = resolve_recruitment_data_folder_key(jd_select_canon=sel_c, job_title=derived or "")
        fk_cur = sanitize_job_folder(jf_cur) if jf_cur else ""
        switch_job = bool(fk_new and fk_new != fk_cur)
        jd_path: Path | None = None
        if switch_job:
            ensure_job_dirs_by_folder_key(fk_new)
            jd_path = get_job_jd_path_by_folder_key(fk_new)
            if not jd_path.exists():
                init_job_jd_from_template(
                    derived or (primary_job_title_from_boss_select_line(sel_c) or "未命名"),
                    overrides={"job_title": derived, "jd_select": sel_c, "data_folder_key": fk_new},
                    data_folder_key=fk_new,
                )
            wid = (p2.get("workflow_id") or "").strip()
            try:
                from l3_node.local_memory import set_hr_recruitment_workflow_pointer

                set_hr_recruitment_workflow_pointer(
                    wid,
                    job_name=derived,
                    job_folder=fk_new,
                    jd_config_path=str(jd_path),
                    resume_pending_dir=str(jd_path.parent / "pending"),
                )
            except Exception as _e:
                logger.debug("[Lark] 切换指针到新岗位跳过: %s", _e)
        else:
            jcp = (p2.get("jd_config_path") or "").strip()
            if jcp:
                cand = Path(jcp)
                if cand.exists() and "jd_to_publish" not in str(cand).replace("\\", "/"):
                    jd_path = cand
            if jd_path is None and fk_new:
                jd_path = get_job_jd_path_by_folder_key(fk_new)
            elif jd_path is None and jn_cur:
                _fk_cur = infer_folder_key_from_job_display_name(jn_cur)
                if _fk_cur:
                    jd_path = get_job_jd_path_by_folder_key(_fk_cur)
        if jd_path is None:
            return out

        jd_path.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {}
        if jd_path.exists():
            try:
                data = json.loads(jd_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        if not isinstance(data, dict):
            data = {}
        data["jd_select"] = sel_c
        n_tgt_merged = None
        p0_before = 0
        # 仅当句子里明确「收网/抓简历」时才关推荐；纯选岗行（如只发 jd_select）不再自动关，
        # 否则换 Java 岗后 jd 被标成仅收网，易与后续「仅打招呼」混淆，且与 HR 仅切换 Boss 岗意图不符。
        if _feishu_text_implies_resume_harvest_focus(ut):
            data["enable_greet_recommend"] = False
            n_tgt_merged = _parse_resume_collect_target_from_feishu(ut)
            if n_tgt_merged is not None:
                pend_dir = jd_path.parent / "pending"
                if pend_dir.is_dir():
                    p0_before = sum(1 for _ in pend_dir.rglob("*.pdf"))
                data["resume_collect_target"] = n_tgt_merged
                data["analyze_threshold"] = n_tgt_merged
            logger.info(
                "[Lark] 收简历意图：已写 enable_greet_recommend=false"
                + (f", resume_collect_target={n_tgt_merged}" if n_tgt_merged is not None else "")
                + " -> %s",
                jd_path,
            )
        jd_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("[Lark] 已合并 jd_select=%s -> %s", sel, jd_path)
        if n_tgt_merged is not None and p0_before > n_tgt_merged:
            try:
                from l3_node.channels.lark.hr_recruitment_notify import (
                    send_hr_incremental_resume_target_clarify_if_configured,
                )

                jt = (derived or jn_cur or str(data.get("job_title") or "")).strip()
                send_hr_incremental_resume_target_clarify_if_configured(
                    pending_count=p0_before,
                    stated_cumulative_target=n_tgt_merged,
                    job_title=jt,
                )
            except Exception as _clar_e:
                logger.debug("[Lark] 收网目标确认飞书跳过: %s", _clar_e)

        out["applied"] = True
        out["jd_select"] = sel_c
        out["job_name"] = (str(data.get("job_title") or "") or derived or "").strip()
        out["job_folder"] = jd_path.parent.name
    except Exception as e:
        logger.debug("[Lark] apply_job_select_from_hr_im_text 跳过: %s", e)
    return out


def process_lark_message(
    user_text: str,
    chat_id: str = "",
    user_id: str = "",
    *,
    run_agent_fn=None,
    engine=None,
    loop=None,
    timeout: float = 180.0,
    session_messages=None,
) -> dict:
    """
    处理用户消息。

    - 若传入 run_agent_fn + engine（L3 内联模式）：直接调用 run_agent，不转发 WS
    - 若配置了 L3_WS_URL：全部转发给 Jachin L3（含任务），由 L3 Agent + MCP 完成
    - 否则：任务记录到本地；普通问题用百炼回复

    :param user_text: 用户输入
    :param chat_id: 会话 ID（用于任务记录）
    :param user_id: 用户 ID（用于任务记录）
    :param run_agent_fn: 可选，L3 内联时传入 async run_agent
    :param engine: 可选，LiteLLMEngine
    :param loop: 可选，事件循环，用于 run_coroutine_threadsafe
    :param timeout: run_agent 超时
    :param session_messages: 可选，Lark 会话历史（按 chat_id 持久化），供多轮招聘流程追溯
    :return: {"reply": str, "is_task": bool}
    """
    _ensure_dotenv()
    if not user_text or not user_text.strip():
        return {"reply": "", "is_task": False}

    sess = session_messages if isinstance(session_messages, list) else []
    _use_hr_agent = True
    try:
        from l3_node.routing.intent_signals import lark_message_should_use_hr_recruitment

        _use_hr_agent = lark_message_should_use_hr_recruitment(
            user_text.strip(),
            prior_messages=sess,
        )
    except Exception:
        _use_hr_agent = True

    # 记录飞书 chat_id，供收网进度推送（LARK_CHAT_ID 未设时从指针读取）
    if _use_hr_agent and (chat_id or "").strip():
        try:
            from l3_node.local_memory import get_hr_recruitment_workflow_pointer, set_hr_recruitment_workflow_pointer

            p = get_hr_recruitment_workflow_pointer()
            jf = (p.get("primary_job_folder") or p.get("job_folder") or "").strip()
            set_hr_recruitment_workflow_pointer(
                p.get("workflow_id", ""),
                job_name=p.get("job_name", ""),
                job_folder=jf,
                jd_config_path=p.get("jd_config_path", ""),
                resume_pending_dir=p.get("resume_pending_dir", ""),
                lark_chat_id=chat_id.strip(),
            )
        except Exception as e:
            logger.debug("[Lark] 写入 lark_chat_id 跳过: %s", e)

    # 从飞书整句解析 Boss 选岗行并写入 jd.json（如「python工程师 杭州 15-25k开始抓取简历」）
    if _use_hr_agent and (user_text or "").strip():
        try:
            apply_job_select_from_hr_im_text(user_text.strip())
        except Exception as e:
            logger.debug("[Lark] 合并 jd_select 跳过: %s", e)

    # 高优遥控指令：停止收网 / 触发透析镜 — 不经过 LLM
    try:
        from l3_node.lark_workflow_command_interceptor import try_lark_workflow_command_intercept

        cmd_reply = try_lark_workflow_command_intercept(user_text.strip(), channel_id=(chat_id or "").strip())
        if cmd_reply:
            return {"reply": cmd_reply, "is_task": False, "command_intercepted": True}
    except Exception as e:
        logger.debug("[Lark] workflow command intercept 跳过: %s", e)

    # L3 内联模式：im_channels 直接调用，不走 WS
    if run_agent_fn and engine and loop:
        import asyncio

        _cid = (chat_id or "").strip()

        async def _do():
            _channel = "lark_im_dispatcher" if not _use_hr_agent else "lark_hr_recruitment"
            _iatt = {"channel": _channel}
            if _cid:
                _iatt["lark_chat_id"] = _cid
            return await run_agent_fn(
                user_text.strip(),
                engine,
                _session_messages=sess,
                implicit_attribution=_iatt,
            )

        future = asyncio.run_coroutine_threadsafe(_do(), loop)
        try:
            reply = future.result(timeout=timeout)
            return {"reply": reply or "L3 未返回回复", "is_task": False}
        except Exception as e:
            logger.exception("L3 内联 run_agent 失败: %s", e)
            return {"reply": "抱歉，处理时发生错误，请稍后重试。", "is_task": False}

    l3_url = os.environ.get("L3_WS_URL", "").strip()
    # L3 模式：转发给 Jachin，由 L3 调用 MCP 工具；传入 chat_id 以便 L3 持久化会话（「同意」时能拿到上一轮 JD）
    if l3_url:
        _mode = "HR" if _use_hr_agent else "PMO/通用"
        logger.info(
            "L3 壳模式(%s): 转发到 %s chat_id=%s",
            _mode,
            l3_url,
            (chat_id or "")[:20] if chat_id else "无",
        )
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
