"""
Lark 高优指令拦截器 — 停止收网 / 触发透析镜分析

在送入 LLM 之前处理纯文本遥控指令，调用 DAGWorkflow.inject_signal 与 resume / 独立透析镜。

匹配规则（摘要）：
- **进度简报**：「进度 / 状态 / 什么进度」等返回当前岗位、抓取 n/m、待透析估算、调度是否在跑及下一步指引（与 L3 启动时飞书推送同源）。
- **再抓 N 份**：「再抓 6 份 / 再抓取10份简历 / 多抓3份」等直接调高收网目标并 **重新注册** 定时任务（不调 LLM）；收网 tick 会按 jd.json 的 jd_select **自动切换** Boss 沟通页职位下拉。飞书可能重复投递同一条文本：短时内按会话去重，避免目标被连加 **2N**。
- **停止收网**：短指令「停止 / 暂停 / stop」或子串（停止收网、别抓了、停抓…）会 inject STOP_HARVEST。
  若整句主要是「停止/关闭 招聘·无人值守·自动化」且**无**收网/抓取类语境，则 **不拦截**，交给 Agent（如 stop_automated_recruitment）。
- **透析镜**：「开始分析 / 分析 / 再分析 / 跑透析镜…」等命中后停收网并登记调度器手动透析（与达 analyze_threshold 同一套琅琊榜流程）；无调度时回退独立 hr_analyze_resume。含 BI/报表/需求分析等歧义时不拦截。
- **模糊意图**：由 L3 通用框架 ``intent_clarification`` + 域插件（当前为 ``intent_clarification_plugins/hr_recruitment_lark``）处理；未精确命中时反问并请发明确短指令。单独「同意/好的」等仍走 L3 会话上下文。
- **同意调度**：**同意调度** / **确认启动** / **同意启动** / **立即启动** / **确认启动无人值守** 等 → 按指针与 `jd.json` 注册 APScheduler。
- **单行同意/确认**（``同意`` / ``确认发布`` / ``就按这个发`` 等，**不含**「同意调度」）：若会话或全局有待确认 JD，先 **落盘**；若 jd **尚未** ``boss_post_published`` 且未带 ``skip_boss_post``，则 **先走 ``atom_post_job_boss``（Boss 发帖）**，成功后再注册无人值守。**不会**误用指针里上一岗。  
  - **「同意调度」/「开始无人值守」** 等短语：只注册 APScheduler，**不要求**再次发帖（适用于已发帖或分支 B 仅开调度）。  
  - 与 Agent 话术对齐：助手说「回复同意将**发布**」时，HR 应发裸 **「同意」**（会触发发帖+调度）；若只补调度则发 **「同意调度」**。
- **恢复挂起岗位**：**恢复挂起岗位：`目录键`**（或 **恢复挂起：`目录键`**）— 换岗抢占后按 ``scheduler_state`` 里挂起项恢复该岗无人值守（Boss 单页互斥，会卸当前其它岗定时）。
- **每轮人数**：问「每轮沟通多少人」返回当前 **收网 max_count / 打招呼 greet_target / 推荐间隔**；「收网改成80人」「打招呼改成20人」「推荐间隔15分钟」写回调度器并重新注册任务。
- **仅收网**：「仅收网 / 只抓简历 / 关闭打招呼 …」→ 写 jd **关推荐牛人** 并重新注册调度（只跑沟通收简历，不与打招呼交替）。
- **清除记忆**：整句「清除全部岗位记忆」等 → 与 ``scripts/reset_hr_recruitment_all.py`` **默认参数**一致的全量清空（含岗位目录、指针、审计、workflow 条、飞书多轮会话、透析输出、client_volumes 等，**默认不保留** ``lark_chat_id``）。「清除岗位：某某」→ 仅移除该岗指针与调度器状态，并在该岗 **jd.json** 写 ``show_in_hr_briefing: false``（**不删** 简历文件）。
- **仅打招呼 N 次**：如「仅打招呼80」「80次打招呼」等 → 启动定时**只打招呼**；若有未完成进度会先提示 **继续仅打招呼** / **仅打招呼N重开**。可与选岗同行一条消息（如 ``Python 工程师 杭州 15-25K，仅打招呼20人``）：会先合并 ``jd_select`` 再注册，避免仍用旧指针岗。
"""
from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
from pathlib import Path

from l3_node.hr_lark_command_lexicon import (
    matches_continue_command,
    matches_hr_analyze_command,
    matches_hr_status_briefing_command,
    matches_stop_harvest_inject,
    recruitment_stop_without_harvest_cue,
)
from l3_node.intent_clarification import try_default_l3_fuzzy_clarification

logger = logging.getLogger(__name__)

# 飞书可能重复投递同一条「分析」；短时内只 ack 一次，避免连发 3 条相同回复并起 3 个后台线程
_ANALYZE_CMD_COOLDOWN_SEC = 42.0
_analyze_cmd_dedup_lock = threading.Lock()
_analyze_cmd_last_key: str | None = None
_analyze_cmd_last_mono: float = 0.0

# 「再抓 N 份」若双投，会两次调用 apply_lark_more_harvest_extra（第二次基准已是新 cap → 连加 2N）
_MORE_HARVEST_CMD_COOLDOWN_SEC = 45.0
_more_harvest_cmd_dedup_lock = threading.Lock()
_more_harvest_cmd_last_key: str | None = None
_more_harvest_cmd_last_mono: float = 0.0

REPLY_STOP = (
    "🛑 已发送强制停止信号！当前调度正在安全挂起，请稍候查看最终战报。"
)
REPLY_ANALYZE = (
    "🧠 收到指令！停止一切抓取，立刻启动 Wasm 透析镜开始分析已入库的简历！"
)
REPLY_ANALYZE_COOLDOWN = (
    "⏳ 透析镜已在执行或刚收到相同指令，请稍候战报（📊【透析镜】），无需重复发送「分析」。"
)

# 「再抓 / 再抓取 N 份」：须匹配「抓取」双字且「抓」单字在长分支之后，否则「再抓取」会被误拆成「再+抓」只吃一个字节
_MORE_HARVEST_RE = re.compile(
    r"^\s*(?:(?:请|帮我|麻烦|辛苦)\s*)?(?:(?:再|多)(?:抓取|抓|收)|继续\s*抓)\s*(\d+)\s*(?:份|个)?(?:简历)?\s*$",
    re.I,
)

_GREET_ONLY_CAMPAIGN_RES = (
    re.compile(r"仅打招呼\s*(\d+)", re.I),
    re.compile(r"只打招呼\s*(\d+)", re.I),
    re.compile(r"只要\s*打招呼\s*(\d+)", re.I),
    re.compile(r"仅需\s*打招呼\s*(\d+)", re.I),
    re.compile(r"打招呼\s*(\d+)\s*次", re.I),
    re.compile(r"(\d+)\s*次\s*打招呼", re.I),
    re.compile(r"(\d+)\s*次.{0,48}打招呼", re.I),
    re.compile(r"打招呼.{0,48}(\d+)\s*次", re.I),
)

_CONTINUE_GREET_ONLY_RE = re.compile(
    r"^\s*(?:继续|续接)\s*仅打招呼\s*$|^\s*仅打招呼\s*(?:续接|继续)\s*$",
    re.I,
)
_RESTART_GREET_ONLY_RE = re.compile(
    r"^\s*仅打招呼\s*(\d+)\s*(?:重开|从零|重新开始)\s*$|^\s*(?:重开|从零)\s*仅打招呼\s*(\d+)\s*$",
    re.I,
)


def _parse_greet_only_total_from_lark(text: str) -> int | None:
    s = (text or "").strip()
    if not s or len(s) > 220:
        return None
    for rx in _GREET_ONLY_CAMPAIGN_RES:
        m = rx.search(s)
        if m:
            try:
                n = int(m.group(1))
                return n if n > 0 else None
            except (TypeError, ValueError, IndexError):
                continue
    return None

_HR_SET_HARVEST_MAX = re.compile(
    r"(?:收网|抓简历|求简历)(?:每轮|一轮|一次)?\s*(?:改|调|设置|设为|改成|换成|增加到|提到)\s*(\d+)\s*(?:人|个)?",
    re.I,
)
_HR_SET_GREET_MAX = re.compile(
    r"(?:打招呼|推荐牛人|推荐)(?:每轮|一轮|一次)?\s*(?:改|调|设置|设为|改成|换成|增加到|提到)\s*(\d+)\s*(?:人|个)?",
    re.I,
)
_HR_SET_REC_INTERVAL = re.compile(
    r"推荐(?:牛人)?\s*(?:间隔|周期)\s*(?:改|调|设为|改成|换成|设置)?\s*(\d+)\s*(?:分钟|分|min)",
    re.I,
)
_HR_BATCH_QUERY = re.compile(
    r"(每轮|每次|一轮|一次).*(多少|几人|几个人|上限|多少人)"
    r"|收网.*(多少|几人|几个人|上限|多少人)"
    r"|抓简历.*(多少|几人|上限|多少人)"
    r"|求简历.*(多少|几人|上限|多少人)"
    r"|打招呼.*(多少|几人|上限|多少人)"
    r"|推荐牛人.*(多少|几人|上限|多少人)"
    r"|沟通.*(上限|多少人)"
    r"|处理.*(上限|多少人)",
    re.I,
)
_AMBIG_PEOPLE_ONLY = re.compile(r"^\s*改成\s*(\d+)\s*人\s*$", re.I)

_HARVEST_ONLY_RE = re.compile(
    r"^\s*(仅收网|只抓简历|只收简历|仅抓简历|关闭打招呼|不要打招呼|停打招呼|关掉打招呼|不要推荐牛人)\s*$",
    re.I,
)

# 句末允许中英文句号、省略号、问号、叹号及尾随空白（飞书常带「。」）
_CLEAR_ALL_HR_MEMORY_TAIL = r"(?:[。…．.!！?？]+)?\s*$"
_CLEAR_ALL_HR_MEMORY_RE = re.compile(
    r"^\s*(清除|清空|删除)(?:掉|去)?(?:全部|所有)?(?:招聘)?(?:岗位)?(?:的)?记忆"
    + _CLEAR_ALL_HR_MEMORY_TAIL
    + r"|^\s*(清除|清空)(?:全部|所有)岗位(?:记忆)?"
    + _CLEAR_ALL_HR_MEMORY_TAIL,
    re.I,
)
_CLEAR_ONE_HR_MEMORY_COLON = re.compile(
    r"^\s*(?:清除|清空|删除)(?:掉|去)?(?:岗位|职位)\s*[：:]\s*(.+?)\s*$",
    re.I,
)
_CLEAR_ONE_HR_MEMORY_REST = re.compile(
    r"^\s*(?:清除|清空|删除)(?:掉|去)?(?:岗位|职位)\s+(.+?)\s*$",
    re.I,
)


def _extract_hr_batch_limit_updates(text: str) -> dict[str, int]:
    t = (text or "").strip()
    out: dict[str, int] = {}
    m = _HR_SET_HARVEST_MAX.search(t)
    if m:
        out["max_count"] = int(m.group(1))
    m = _HR_SET_GREET_MAX.search(t)
    if m:
        out["greet_target"] = int(m.group(1))
    m = _HR_SET_REC_INTERVAL.search(t)
    if m:
        out["recommend_interval_minutes"] = int(m.group(1))
    return out


def _resolve_hr_workflow_id() -> str:
    from l3_node.local_memory import get_hr_recruitment_active_workflow_id, list_workflow_state_ids
    from l3_node.skills.hr_recruitment_dag import HR_RECRUITMENT_DEFAULT_WORKFLOW_ID

    w = get_hr_recruitment_active_workflow_id()
    if w:
        return w
    for i in list_workflow_state_ids():
        low = i.lower()
        if "recruit" in low or "hr_" in low or low.startswith("hr"):
            return i
    return HR_RECRUITMENT_DEFAULT_WORKFLOW_ID


def _run_standalone_hr_analyze() -> None:
    """无 DAG 断点状态时，根据指针直接调用 hr_analyze_resume。"""
    from l3_node.local_memory import get_hr_recruitment_workflow_pointer
    from l3_node.hr_loader import _get_hr_recruitment_plugin_root

    ptr = get_hr_recruitment_workflow_pointer()
    job = (ptr.get("job_name") or "").strip()
    jd_path = (ptr.get("jd_config_path") or "").strip()
    pending = (ptr.get("resume_pending_dir") or "").strip()

    if not pending and job:
        pending = str(Path.home() / ".jachin" / "workspace" / "hr_recruitment" / job / "pending")

    jd_template = ""
    if jd_path:
        try:
            p = Path(jd_path)
            if p.exists():
                jd = json.loads(p.read_text(encoding="utf-8"))
                jd_template = (jd.get("jd_full") or "").strip()
        except Exception as e:
            logger.debug("[LarkCmd] 读取 jd.json: %s", e)
    if not jd_template:
        jd_template = "请根据岗位要求评估候选人简历匹配度。"

    if not pending:
        logger.warning("[LarkCmd] 独立透析镜跳过：无法解析 resume_pending_dir")
        return

    root = _get_hr_recruitment_plugin_root()
    if not root:
        logger.warning("[LarkCmd] 独立透析镜跳过：HR MCP 包未找到")
        return
    import sys

    s = str(root.resolve())
    if s not in sys.path:
        sys.path.insert(0, s)
    from tools.hr_analyze_resume import hr_analyze_resume

    out = hr_analyze_resume(
        target_dir=pending,
        jd_template=jd_template,
        target_role="backend_engineer",
    )
    logger.info("[LarkCmd] 独立透析镜完成 preview=%s", (out or "")[:120])


def _background_hr_analyze() -> None:
    """
    注入 STOP 后触发透析：优先走 APScheduler 规则引擎（与无人值守同一套琅琊榜+Lark），
    再回退 DAG resume，最后独立 hr_analyze_resume。避免 DAG 与调度器并行各跑一轮 Wasm。
    """
    try:
        from core.workflow_engine import DAGWorkflow, SIGNAL_STOP_HARVEST
        from l3_node.local_memory import load_workflow_state, save_workflow_state
        from l3_node.skills.hr_recruitment_dag import build_hr_recruitment_dag

        wid = _resolve_hr_workflow_id()
        DAGWorkflow.inject_signal(wid, SIGNAL_STOP_HARVEST)

        # 1) 调度器优先：登记手动透析 +（若有 check 任务）立即后台 job_check 一轮
        try:
            from l3_node.hr_loader import get_recruitment_scheduler
            from l3_node.local_memory import get_hr_recruitment_workflow_pointer

            ptr = get_hr_recruitment_workflow_pointer()
            job = (ptr.get("job_name") or "").strip()
            rs = get_recruitment_scheduler()
            if rs is not None and job and hasattr(rs, "request_scheduler_manual_analyze"):
                if rs.request_scheduler_manual_analyze(job):
                    logger.info(
                        "[LarkCmd] 已走调度器手动透析 job=%s（与定时 check 互斥，立即或 1 分钟内执行）",
                        job[:60],
                    )
                    return
        except Exception as ex:
            logger.debug("[LarkCmd] 调度器手动透析失败，尝试 DAG/独立透析: %s", ex)

        # 2) DAG 断点续跑（无调度配置时的兜底）
        st = load_workflow_state(wid)
        if st:
            completed = list(st.get("completed_nodes") or [])
            if "harvest_loop" in completed and "analyze_resumes" not in completed:
                st = {**st, "failed_at_node": "analyze_resumes"}
                save_workflow_state(wid, st)
                wf = build_hr_recruitment_dag(wid)
                wf.resume(workflow_id=wid)
                logger.info("[LarkCmd] DAG 从 analyze 续跑 workflow=%s", wid)
                return
            if (
                st.get("failed_at_node")
                or st.get("suspended_at_node")
                or st.get("suspended_for_human")
            ):
                wf = build_hr_recruitment_dag(wid)
                wf.resume(workflow_id=wid)
                logger.info("[LarkCmd] DAG resume 已执行 workflow=%s", wid)
                return

        _run_standalone_hr_analyze()
    except Exception as e:
        logger.exception("[LarkCmd] 后台分析任务失败: %s", e)


def _last_assistant_text(session_msgs: list) -> str:
    for m in reversed(session_msgs or []):
        if isinstance(m, dict) and m.get("role") == "assistant":
            return str(m.get("content") or "")
    return ""


def _lark_assistant_implies_continue_after_verify(text: str) -> bool:
    """上一轮为「完成验证后请回复继续」类话术时返回 True，避免单行「同意」误消费 JD pending。"""
    if not text or len(text) < 20:
        return False
    tail = text[-3000:]
    if not re.search(r"滑块|安全验证|人机验证|验证码|异常访问|需先完成验证|完成验证", tail):
        return False
    if re.search(
        r"请回复\s*[「\"']?(同意|确认)(?:调度|启动)|同意\s*调度|确认启动无人值守",
        tail[-900:],
    ):
        return False
    if re.search(r"(请|烦请).{0,25}回复.{0,12}继续", tail):
        return True
    if "验证" in tail[-1200:] and "继续" in tail[-900:]:
        return True
    return False


def _lark_cmd_prelude_merge_boss_job_select_if_present(raw: str) -> None:
    """
    IM 通道先命中本拦截器时不会执行 ``atom_lark_chat.process_lark_message`` 首段的 jd 合并；
    若 HR 把「选岗行」与短指令写在同一条消息（如 ``Python 工程师 杭州 15-25K，仅打招呼20人``），
    须先落盘 ``jd_select`` / 切换指针，否则仍用旧岗 jd 去 Boss 下拉匹配。
    """
    try:
        from l3_node.hr_loader import _get_hr_recruitment_plugin_root

        pr = _get_hr_recruitment_plugin_root()
        if not pr:
            return
        ps = str(pr.resolve())
        inserted = False
        if ps not in sys.path:
            sys.path.insert(0, ps)
            inserted = True
        try:
            from tools.atom_lark_chat import apply_job_select_from_hr_im_text

            out = apply_job_select_from_hr_im_text((raw or "").strip())
            if out.get("applied"):
                logger.info(
                    "[LarkCmd] 指令前已同步选岗 jd_select=%s folder=%s job_name=%s",
                    (out.get("jd_select") or "")[:100],
                    (out.get("job_folder") or "")[:48],
                    (out.get("job_name") or "")[:48],
                )
        finally:
            if inserted:
                try:
                    sys.path.remove(ps)
                except ValueError:
                    pass
    except Exception as ex:
        logger.debug("[LarkCmd] 指令前选岗同步跳过: %s", ex)


def _map_agree_to_continue_if_verify_context(raw: str, channel_id: str) -> str:
    if not (channel_id or "").strip():
        return raw
    if not re.match(r"^\s*(同意|好的|可以|确认)\s*$", raw, re.I):
        return raw
    from l3_node.lark_session import load_lark_session

    la = _last_assistant_text(load_lark_session(channel_id))
    if _lark_assistant_implies_continue_after_verify(la):
        logger.info("[LarkCmd] 将 %r 依会话上文视为「继续」（验证/恢复收网语境）", raw)
        return "继续"
    return raw


def try_lark_workflow_command_intercept(user_text: str, channel_id: str = "") -> str | None:
    """
    若命中遥控指令则返回飞书回复文案（调用方应直接回复并 **不再** 调用 LLM）。
    未命中返回 None。

    ``channel_id``：会话/聊天 ID，传入后模糊澄清的冷却按会话隔离（可选）。
    """
    global _analyze_cmd_last_key, _analyze_cmd_last_mono
    global _more_harvest_cmd_last_key, _more_harvest_cmd_last_mono
    t = (user_text or "").strip()
    if not t:
        return None

    t_continue = _map_agree_to_continue_if_verify_context(t, channel_id)

    # --- 继续无人值守（须先于「同意」，使验证语境下单行「同意」走恢复而非 JD）---
    if matches_continue_command(t_continue):
        try:
            from l3_node.hr_loader import get_recruitment_scheduler
            from l3_node.channels.lark.hr_recruitment_notify import format_hr_recruitment_progress_line_for_lark

            rs = get_recruitment_scheduler()
            if rs is None or not hasattr(rs, "resume_hr_recruitment_scheduler"):
                return "⚠️ 招聘调度器未加载，无法继续。"
            out = rs.resume_hr_recruitment_scheduler()
            line = format_hr_recruitment_progress_line_for_lark()
            if not line:
                n, cap = int(out.get("pending_pdfs", 0)), int(out.get("collect_cap", 0))
                line = (
                    f"pending 内 PDF：{n} 个 / 收网目标：{cap} 份"
                    if cap > 0
                    else f"pending 内 PDF：{n} 个"
                )
            if out.get("already_running"):
                bits = [
                    "▶️ 已继续：已清除 STOP 信号；**打招呼 / 收网交替** 主定时仍在，将按当前进度推进。",
                ]
            else:
                bits = [
                    "▶️ 已继续：已清除全局停止标志与 STOP_HARVEST。",
                ]
                if out.get("restored_scheduler"):
                    bits.append(
                        "已按上次配置 **重新挂上打招呼↔收网** 主定时。"
                        "（发「停止」时会卸掉主循环，仅保留透析用的分钟检查；需「继续」才会恢复收网/推荐。）"
                    )
                else:
                    err = (out.get("restore_error") or "").strip()
                    if err:
                        bits.append(f"说明：{err[:220]}")
            bits.append(f"当前进度：{line or '（暂无摘要）'}。")
            return "\n".join(bits)
        except Exception as e:
            logger.exception("[LarkCmd] 继续指令失败: %s", e)
            return f"⚠️ 继续失败：{e}"

    # --- 恢复挂起岗位：换岗时 ``add_scheduled_job`` 抢占写入的 scheduler_suspended，可按目录键或岗位名恢复 ---
    _resume_susp = re.match(r"^\s*恢复挂起(?:岗位)?[:：]\s*(.+?)\s*$", t, re.I)
    if _resume_susp:
        raw_q = _resume_susp.group(1).strip().strip("`").strip("「」").strip()
        if raw_q:
            try:
                from l3_node.hr_loader import get_recruitment_scheduler

                rs = get_recruitment_scheduler()
                if rs is None or not hasattr(rs, "resume_hr_job_scheduler_for_folder"):
                    return "⚠️ 招聘调度器未加载，无法恢复挂起岗位。"

                def _resolve_suspended_folder(query: str) -> str:
                    q = (query or "").strip()
                    if not q:
                        return ""
                    lst_fn = getattr(rs, "list_scheduler_suspended_jobs", None)
                    if not callable(lst_fn):
                        return q
                    try:
                        items = lst_fn()
                    except Exception:
                        return q
                    if not items:
                        return q
                    for it in items:
                        jf = str(it.get("job_folder") or "").strip()
                        jn = str(it.get("job_name") or "").strip()
                        if not jf:
                            continue
                        if q == jf or q.lower() == jf.lower():
                            return jf
                    for it in items:
                        jf = str(it.get("job_folder") or "").strip()
                        jn = str(it.get("job_name") or "").strip()
                        if not jf:
                            continue
                        if (q in jn) or (jn and jn in q):
                            return jf
                    return q

                target_jf = _resolve_suspended_folder(raw_q)
                out = rs.resume_hr_job_scheduler_for_folder(job_folder=target_jf)
                if out.get("already_running"):
                    return f"✅ 岗位 **{target_jf}** 的无人值守定时已在跑，无需重复恢复。"
                if not out.get("ok"):
                    err = str(out.get("error") or out.get("restore_error") or "未知错误")[:300]
                    return f"⚠️ 恢复失败：{err}"
                jdisp = str(out.get("job_name") or target_jf)
                mem = str(out.get("job_memory_brief_zh") or "").strip()
                lines = [
                    f"✅ **已按挂起配置恢复岗位**：**{jdisp}**（目录键 `{target_jf}`）。",
                    "Boss 单页互斥：仅本岗定时在跑；简报里可查看其它挂起项。",
                ]
                if mem:
                    _lim = 2000
                    lines.append(mem[:_lim] + ("…" if len(mem) > _lim else ""))
                return "\n".join(lines)
            except Exception as e:
                logger.exception("[LarkCmd] 恢复挂起岗位失败: %s", e)
                return f"⚠️ 恢复挂起岗位失败：{e}"

    # --- 同意调度 / 单行同意：pending JD 优先于指针，避免「新岗产品经理 + 飞书同意」仍注册旧 Python 岗 ---
    _sched_phrases_only = re.match(
        r"^\s*(同意调度|确认调度|启动无人值守调度|确认启动无人值守|开始无人值守"
        r"|确认启动|同意启动|立即启动|现在就启动)\s*$",
        t,
        re.I,
    )
    _bare_agree_jd = re.match(
        r"^\s*(同意|确认|确认发布|就按这个发|直接发布)\s*$",
        t,
        re.I,
    )
    if _sched_phrases_only or _bare_agree_jd:
        try:
            from l3_node.agent_core import (
                _clear_jd_pending_source,
                _persist_jd_config_before_publish,
                _resolve_last_jd_pending,
            )
            from l3_node.hr_loader import _get_hr_recruitment_plugin_root, get_recruitment_scheduler
            from l3_node.local_memory import get_hr_recruitment_workflow_pointer

            ptr = get_hr_recruitment_workflow_pointer()
            jn_ptr = (ptr.get("job_name") or "").strip()
            jdp_ptr = (ptr.get("jd_config_path") or "").strip()

            jd_pending, pending_src = _resolve_last_jd_pending(channel_id or "")
            jt_p = (jd_pending.get("job_title") or "").strip() if jd_pending else ""

            def _agree_job_norm(s: str) -> str:
                return re.sub(r"[\s_\-·]", "", (s or "").casefold())

            use_pending = False
            if jd_pending and jt_p:
                if _bare_agree_jd:
                    use_pending = True
                elif _sched_phrases_only and _agree_job_norm(jt_p) != _agree_job_norm(jn_ptr):
                    use_pending = True

            jn = ""
            jdp = ""
            if use_pending:
                path = _persist_jd_config_before_publish(jd_pending)
                jn = jt_p
                jdp = (path or "").strip()
                try:
                    if pending_src:
                        _clear_jd_pending_source(channel_id or "", pending_src)
                except Exception:
                    pass
                logger.info(
                    "[LarkCmd] 同意调度：已用待确认 JD job_title=%r 覆盖指针岗 %r，jd_path=%s",
                    jt_p,
                    jn_ptr or "(空)",
                    jdp or "(空)",
                )
            else:
                jn = jn_ptr
                jdp = jdp_ptr

            if not jn and not jdp:
                return "⚠️ 未找到招聘岗位指针。请先完成职位发布，或说明岗位名。"
            rs = get_recruitment_scheduler()
            if rs is None:
                return "⚠️ 招聘调度器未加载，无法启动无人值守。"
            _jdp_hint = (jdp or "").strip()
            _hint_arg = _jdp_hint if _jdp_hint and Path(_jdp_hint).is_file() else ""
            digest = rs.get_recruitment_status_digest(jn or "", jd_config_path_hint=_hint_arg)
            if digest.get("scheduler_active"):
                return "✅ 当前岗位无人值守已在运行，无需重复启动。"

            root = _get_hr_recruitment_plugin_root()
            if not root:
                return "⚠️ HR 插件未找到，无法启动调度。"
            import sys

            sroot = str(root.resolve())
            if sroot not in sys.path:
                sys.path.insert(0, sroot)

            post_preamble = ""
            if _bare_agree_jd and (jdp or "").strip() and Path(jdp).is_file():
                # skip_boss_post 仅存于会话 pending（l3_jd_pending*.json），落盘 jd.json 不含此键（见 hr_data_paths）
                skip_post = bool(use_pending and isinstance(jd_pending, dict) and jd_pending.get("skip_boss_post"))
                if not skip_post:
                    try:
                        from tools.hr_data_paths import jd_boss_post_marked_published

                        _pj = json.loads(Path(jdp).read_text(encoding="utf-8"))
                        need_boss_post = not (
                            isinstance(_pj, dict) and jd_boss_post_marked_published(_pj)
                        )
                    except Exception:
                        need_boss_post = True
                    if need_boss_post:
                        from l3_node.skills.mcp_registry import _invoke_atom_post_job_boss_local

                        logger.info("[LarkCmd] 裸「同意」：jd 未标记 boss_post_published，先 atom_post_job_boss path=%s", jdp)
                        post_raw = _invoke_atom_post_job_boss_local(jd_config_path=jdp.strip())
                        try:
                            pr = json.loads(post_raw) if post_raw else {}
                        except json.JSONDecodeError:
                            pr = {}
                        if pr.get("need_login"):
                            return (
                                "⚠️ **需要先登录 Boss 直聘**\n\n"
                                + (str(pr.get("error") or "")).strip()
                                + "\n\nJD 已写入本地。请扫码登录后**再发「同意」**完成发帖并启动无人值守。"
                            )
                        if not (pr.get("posted") or pr.get("already_published")):
                            err = (str(pr.get("error") or "发帖失败")).strip()[:700]
                            return (
                                "⚠️ **Boss 发帖未成功**，已暂不启动无人值守（避免无在招职位空跑）。\n\n"
                                f"原因：{err}\n\n"
                                "请检查 Chrome/CDP 与 Boss 页面后**再发「同意」**重试，或让助手执行 **atom_post_job_boss**。\n\n"
                                "若职位**已在 Boss 在招**、只需开定时任务，请发 **「同意调度」**（不强制发帖），"
                                "或将本岗 `jd.json` 中写入 `boss_post_published: true` 后再试。"
                            )
                        if pr.get("posted"):
                            post_preamble = "✅ **Boss 直聘发帖已完成**。\n\n"
                        elif pr.get("already_published"):
                            post_preamble = "✅ **jd 已标记在招**，跳过重复发帖。\n\n"
                        else:
                            post_preamble = ""

            from tools.hr_scheduler_confirm_prompt import start_scheduler_from_jd_pointer

            out = start_scheduler_from_jd_pointer(job_name=jn, jd_config_path=jdp)
            try:
                robj = json.loads(out) if isinstance(out, str) else {}
            except json.JSONDecodeError:
                robj = {}
            if not isinstance(robj, dict) or not robj.get("ok"):
                err = (robj.get("error") if isinstance(robj, dict) else None) or (out or "")[:220]
                return f"⚠️ 启动无人值守失败：{err}"
            mem = (robj.get("job_memory_brief_zh") or "").strip()
            jdisp = (robj.get("job_name") or jn or "").strip()
            lines = [
                (post_preamble or "")
                + "✅ **无人值守已按 jd.json 中的参数启动**（推荐牛人 / 收网 / 规则引擎定时任务已注册）。",
                f"岗位：**{jdisp}**",
            ]
            if mem:
                lines.append("")
                _lim = 2000
                lines.append(mem[:_lim] + ("…" if len(mem) > _lim else ""))
            try:
                from l3_node.hr_audit_log import append_hr_recruitment_audit_event
                from l3_node.local_memory import get_hr_recruitment_workflow_pointer

                _p = get_hr_recruitment_workflow_pointer()
                append_hr_recruitment_audit_event(
                    "lark_cmd_agree_scheduler",
                    {"job_display": jdisp},
                    job_folder=(_p.get("primary_job_folder") or _p.get("job_folder") or "").strip(),
                    job_name=jdisp,
                )
            except Exception:
                pass
            return "\n".join(lines)
        except Exception as e:
            logger.exception("[LarkCmd] 同意调度失败: %s", e)
            return f"⚠️ 启动失败：{e}"

    # --- 停止收网（子串/正则 + 招聘歧义排除）---
    if matches_stop_harvest_inject(t):
        if recruitment_stop_without_harvest_cue(t):
            logger.debug("[LarkCmd] 跳过 inject：识别为招聘/无人值守停，交给调度器")
            return None
        try:
            from core.workflow_engine import DAGWorkflow, SIGNAL_STOP_HARVEST

            wid = _resolve_hr_workflow_id()
            DAGWorkflow.inject_signal(wid, SIGNAL_STOP_HARVEST)
            logger.info("[LarkCmd] 拦截「停止收网」workflow_id=%s -> STOP_HARVEST", wid)
        except Exception as e:
            logger.exception("[LarkCmd] 停止指令注入失败: %s", e)
            return f"⚠️ 停止信号发送失败：{e}"
        try:
            from l3_node.hr_loader import get_recruitment_scheduler
            from l3_node.local_memory import get_hr_recruitment_workflow_pointer

            ptr = get_hr_recruitment_workflow_pointer()
            jn = (ptr.get("job_name") or "").strip()
            jf = (ptr.get("primary_job_folder") or ptr.get("job_folder") or "").strip()
            rs = get_recruitment_scheduler()
            if rs is not None and hasattr(rs, "remove_harvest_scheduler_jobs") and (jn or jf):
                rs.remove_harvest_scheduler_jobs(jn or jf, job_folder_hint=jf)
                logger.info(
                    "[LarkCmd] 已移除收网/推荐 APScheduler 任务 job=%s folder_hint=%s（透析 check 仍保留）",
                    (jn or jf)[:48],
                    jf[:48] if jf else "",
                )
        except Exception as ex:
            logger.debug("[LarkCmd] 移除收网定时任务跳过: %s", ex)
        try:
            from l3_node.channels.lark.hr_recruitment_notify import format_hr_recruitment_progress_line_for_lark

            prog = format_hr_recruitment_progress_line_for_lark()
        except Exception:
            prog = ""
        msg = REPLY_STOP
        if prog:
            msg += f"\n\n当前进度：{prog}。"
        try:
            from l3_node.hr_audit_log import append_hr_recruitment_audit_event
            from l3_node.local_memory import get_hr_recruitment_workflow_pointer

            _p2 = get_hr_recruitment_workflow_pointer()
            _jn = (_p2.get("job_name") or "").strip() or (_p2.get("primary_job_folder") or "")
            append_hr_recruitment_audit_event(
                "lark_cmd_stop_harvest",
                {"progress_line": (prog or "")[:200]},
                job_folder=(_p2.get("primary_job_folder") or _p2.get("job_folder") or "").strip(),
                job_name=_jn,
            )
        except Exception:
            pass
        return msg

    # --- 仅收网：关闭打招呼，只定时抓沟通页简历 ---
    if _HARVEST_ONLY_RE.match(t.strip()):
        try:
            from l3_node.hr_loader import get_recruitment_scheduler
            from l3_node.channels.lark.hr_recruitment_notify import format_hr_recruitment_progress_line_for_lark

            rs = get_recruitment_scheduler()
            if rs is None or not hasattr(rs, "apply_lark_harvest_only_scheduling"):
                return "⚠️ 招聘调度器未加载，无法切换仅收网。"
            out = rs.apply_lark_harvest_only_scheduling()
            if not out.get("ok"):
                return f"⚠️ {out.get('error', '切换仅收网失败')}"
            jn = (out.get("job_name") or "").strip() or "当前岗位"
            jf_o = (out.get("job_folder") or "").strip()
            line = format_hr_recruitment_progress_line_for_lark(jn, job_folder=jf_o)
            tail = f"\n\n当前进度：{line}。" if line else ""
            return (
                "✅ **已切换为仅收网模式**（已关「推荐/打招呼」，只按间隔在沟通里收简历）。\n\n"
                f"岗位：**{jn}**{tail}\n"
                "· 若仍要先联系候选人，请改 jd 里打开推荐牛人后重新注册调度，或说清需求由助手改参。"
            )
        except Exception as e:
            logger.exception("[LarkCmd] 仅收网失败: %s", e)
            return f"⚠️ 切换仅收网失败：{e}"

    # --- 清除全部岗位：与 scripts/reset_hr_recruitment_all.py 默认行为一致（含审计、workflow、会话、透析、卷等）---
    if _CLEAR_ALL_HR_MEMORY_RE.match(t.strip()):
        try:
            from l3_node.hr_workspace_full_reset import (
                hr_data_root,
                run_full_hr_recruitment_reset_with_retries,
            )

            def _emit_reset(line: str) -> None:
                if (line or "").strip():
                    logger.info("[LarkCmd][HR reset] %s", line)

            ok, rep, ld, lf = run_full_hr_recruitment_reset_with_retries(
                max_rounds=5,
                sleep_seconds=2.0,
                dry_run=False,
                keep_lark_chat=False,
                keep_lark_sessions=False,
                clear_client_volumes=True,
                clear_hr_resume_root=False,
                clear_hr_analysis=True,
                emit=_emit_reset,
            )
            n_loader = int(rep.get("job_dirs_loader") or 0)
            n_swept = int(rep.get("job_dirs_swept") or 0)
            n_wf = int(rep.get("workflow_states_removed") or 0)
            hr_root = hr_data_root()
            tail = (
                f"\n\n⚠️ **仍有残留**（可能被进程占用）：目录 `{ld}` 文件 `{lf}`，请停 Boss/L3 后重试或手工删 ``{hr_root}``。"
                if not ok
                else ""
            )

            return (
                "✅ **已按「一键清空招聘」脚本默认项执行全量清理**（与本地运行 "
                "``python scripts/reset_hr_recruitment_all.py`` 一致）。\n\n"
                f"· 岗位目录：loader 侧约 **{n_loader}** 项，兜底扫描子目录 **{n_swept}** 项。\n"
                f"· 已清：HR 指针（**未保留** ``lark_chat_id``）、审计、workflow 中 hr 条目 **{n_wf}** 条、"
                "``l3_lark_sessions``、透析输出根、``client_volumes``（保留 ``bi_data``）、调度器 ``rec_*``。\n"
                "· 请重新发「**职位 城市 薪资**」绑定新岗；需要保留飞书会话绑定时请改用脚本加 "
                "``--keep-lark-chat`` / ``--keep-lark-sessions`` 在机器上执行。"
                f"{tail}"
            )
        except Exception as e:
            logger.exception("[LarkCmd] 清除全部招聘记忆失败: %s", e)
            return f"⚠️ 清除失败：{e}"

    _mem_colon = _CLEAR_ONE_HR_MEMORY_COLON.match(t.strip())
    _mem_rest = _CLEAR_ONE_HR_MEMORY_REST.match(t.strip()) if not _mem_colon else None
    if _mem_colon or _mem_rest:
        rawq = ((_mem_colon.group(1) if _mem_colon else _mem_rest.group(1)) or "").strip()
        rawq = re.sub(r"(?:的)?记忆\s*$", "", rawq, flags=re.I).strip()
        if rawq:
            try:
                from l3_node.hr_loader import get_recruitment_scheduler
                from l3_node.local_memory import remove_hr_recruitment_job_from_pointer

                ok_m, msg_m, rjn = remove_hr_recruitment_job_from_pointer(rawq)
                if not ok_m:
                    return f"⚠️ {msg_m}"
                rs = get_recruitment_scheduler()
                if rs is not None and rjn and hasattr(rs, "clear_recruitment_scheduler_memory_for_job"):
                    rs.clear_recruitment_scheduler_memory_for_job(rjn)
                return (
                    f"✅ {msg_m}\n\n"
                    "· 已移除该岗的定时任务与调度器里该岗的持久化状态。\n"
                    "· 该岗 **jd.json** 已写 ``show_in_hr_briefing: false``，飞书简报 L3 不再列出（**未删** 简历文件）。\n"
                    "· 再次绑定该岗后会自动改回 ``true``。"
                )
            except Exception as e:
                logger.exception("[LarkCmd] 清除单岗记忆失败: %s", e)
                return f"⚠️ 清除失败：{e}"

    # --- 再抓 N 份（更新 resume_collect_target + 重注册 APScheduler）---
    mh = _MORE_HARVEST_RE.match(t.strip())
    if mh:
        logger.info(
            "[LarkCmd] 命中「再抓 N 份」硬拦截（不调 LLM），raw=%r",
            (t or "")[:160],
        )
        try:
            extra = int(mh.group(1))
        except (TypeError, ValueError):
            extra = 0
        if extra <= 0:
            return "⚠️ 请说明正整数份数，例如：再抓 6 份"
        _lark_cmd_prelude_merge_boss_job_select_if_present(t)
        _mh_key = f"{(channel_id or '').strip().casefold()}\x1e{t.strip().casefold()}"
        _mh_now = time.monotonic()
        with _more_harvest_cmd_dedup_lock:
            if (
                _mh_key == (_more_harvest_cmd_last_key or "")
                and (_mh_now - _more_harvest_cmd_last_mono) < _MORE_HARVEST_CMD_COOLDOWN_SEC
            ):
                logger.info(
                    "[LarkCmd] 忽略重复「再抓 N 份」cooldown=%ss chat_id=%s text=%r",
                    _MORE_HARVEST_CMD_COOLDOWN_SEC,
                    (channel_id or "")[:24],
                    t[:80],
                )
                return (
                    "⏳ **检测到同一条「再抓」指令在短期内重复投递**，已忽略本次，避免收网目标被**连加两次**。\n\n"
                    "常见原因：飞书对同一条消息双投、或 **Webhook 与长连接同时订阅** 各处理一遍。\n\n"
                    "请用 **jd.json** 的 `resume_collect_target` 或发 **进度** 核对当前上限；"
                    "若仍需加量，请隔几十秒再发 **再抓 N 份**（或改用不同份数）。"
                )
            _more_harvest_cmd_last_key = _mh_key
            _more_harvest_cmd_last_mono = _mh_now
        try:
            from l3_node.hr_loader import get_recruitment_scheduler

            rs = get_recruitment_scheduler()
            if rs is None or not hasattr(rs, "apply_lark_more_harvest_extra"):
                return "⚠️ 招聘调度器未加载，无法更新收网目标。"
            try:
                from l3_node.log_broadcaster import broadcast_log

                broadcast_log(
                    f"[Lark HR] 正在应用「再抓 {extra} 份」→ 更新收网目标并注册定时任务（约 30s 内应出现 [收网抓取]）…",
                    "INFO",
                )
            except Exception:
                pass
            out = rs.apply_lark_more_harvest_extra(extra)
            if not out.get("ok"):
                return f"⚠️ {out.get('error', '更新收网目标失败')}"
            old_c = int(out.get("old_cap", 0))
            n = int(out.get("unprocessed", 0))
            pdfn = int(out.get("pending_pdf", 0))
            base_line = int(out.get("base_line", max(old_c, n, pdfn)))
            cap = int(out.get("new_cap", 0))
            jn = (out.get("job_name") or "").strip() or "当前岗位"
            logger.info(
                "[LarkCmd] 「再抓 %s 份」已落库调度 job=%s new_cap=%s base=%s",
                extra,
                jn[:48],
                cap,
                base_line,
            )
            try:
                from l3_node.log_broadcaster import broadcast_log

                broadcast_log(
                    f"[Lark HR] 收网目标已更新：{jn} → 累计 {cap} 份（本次 +{extra}）。"
                    f"请保持 Chrome+Boss 沟通页；等待 [收网抓取] / Playwright 日志。",
                    "SUCCESS",
                )
            except Exception:
                pass
            return (
                f"✅ **收网目标已更新并重新注册定时任务**（{jn}）\n\n"
                f"· **新目标 {cap} 份** = 基准 **{base_line}** + 再抓 **{extra}** 份；"
                f"基准 = max(原目标 {old_c}, 未处理 {n}, 磁盘 PDF {pdfn})，避免已透析后「未处理=0」误算成只加 {extra} 份。\n"
                f"· **jd.json** 已同步 **resume_collect_target** 与 **analyze_threshold** 均为 **{cap}**"
                f"（累计收网上限与自动透析触发份数为同一数字）。\n"
                f"· 请保持 **Chrome 已挂 CDP** 且能打开 Boss 沟通页；收网按 **jd.json → jd_select** 自动选岗。\n"
                f"· 若无 `[收网抓取]` 日志，请看 Playwright/CDP 是否报错。"
            )
        except Exception as e:
            logger.exception("[LarkCmd] 再抓 N 份失败: %s", e)
            return f"⚠️ 更新收网失败：{e}"

    # --- 定时「仅打招呼」：续接 / 重开 / 新开（与「打招呼改成 N 人」互斥）---
    _t_go = t.strip()
    if _CONTINUE_GREET_ONLY_RE.match(_t_go):
        logger.info("[LarkCmd] 命中「继续仅打招呼」raw=%r", (_t or "")[:160])
        _lark_cmd_prelude_merge_boss_job_select_if_present(t)
        try:
            from l3_node.hr_loader import get_recruitment_scheduler

            rs = get_recruitment_scheduler()
            if rs is None or not hasattr(rs, "apply_lark_greet_only_campaign"):
                return "⚠️ 招聘调度器未加载，无法续接仅打招呼。"
            out = rs.apply_lark_greet_only_campaign(0, resume=True)
            if not out.get("ok"):
                return f"⚠️ {out.get('error', '续接失败')}"
            jn = (out.get("job_name") or "").strip() or "当前岗位"
            tot = int(out.get("greet_only_total_target") or 0)
            done0 = int(out.get("greet_only_done_after_register") or 0)
            rim = int(out.get("greet_only_interval_minutes") or 0)
            return (
                f"✅ **已续接「仅打招呼」**（{jn}）\n\n"
                f"· 目标仍为 **{tot}** 次，当前已累计成功 **{done0}** 次（不重置计数）。\n"
                f"· 间隔：约每 **{rim or '（默认）'}** 分钟一步。\n"
                f"· 需 **Chrome CDP**；停止可发 **停止收网**。"
            )
        except Exception as e:
            logger.exception("[LarkCmd] 续接仅打招呼失败: %s", e)
            return f"⚠️ 续接失败：{e}"

    _rsm = _RESTART_GREET_ONLY_RE.match(_t_go)
    if _rsm:
        try:
            n_restart = int(_rsm.group(1) or _rsm.group(2))
        except (TypeError, ValueError):
            n_restart = 0
        if n_restart > 0:
            logger.info(
                "[LarkCmd] 命中「仅打招呼 N 重开」n=%s raw=%r",
                n_restart,
                (_t or "")[:160],
            )
            _lark_cmd_prelude_merge_boss_job_select_if_present(t)
            try:
                from l3_node.hr_loader import get_recruitment_scheduler

                rs = get_recruitment_scheduler()
                if rs is None or not hasattr(rs, "apply_lark_greet_only_campaign"):
                    return "⚠️ 招聘调度器未加载，无法启动仅打招呼。"
                out = rs.apply_lark_greet_only_campaign(n_restart, resume=False)
                if not out.get("ok"):
                    return f"⚠️ {out.get('error', '启动失败')}"
                jn = (out.get("job_name") or "").strip() or "当前岗位"
                rim = int(out.get("greet_only_interval_minutes") or 0)
                return (
                    f"✅ **已「重开」仅打招呼**（{jn}）\n\n"
                    f"· 目标 **{n_restart}** 次，计数已**从零**开始。\n"
                    f"· 间隔：约每 **{rim or '（默认）'}** 分钟一步。"
                )
            except Exception as e:
                logger.exception("[LarkCmd] 仅打招呼重开失败: %s", e)
                return f"⚠️ 启动失败：{e}"

    _ups_early = _extract_hr_batch_limit_updates(_t_go)
    _go_n = None if _ups_early else _parse_greet_only_total_from_lark(t)
    if _go_n is not None:
        logger.info(
            "[LarkCmd] 命中「仅打招呼 N」硬拦截（不调 LLM），n=%s raw=%r",
            _go_n,
            (t or "")[:160],
        )
        _lark_cmd_prelude_merge_boss_job_select_if_present(t)
        try:
            from l3_node.hr_loader import get_recruitment_scheduler

            rs = get_recruitment_scheduler()
            if rs is None or not hasattr(rs, "apply_lark_greet_only_campaign"):
                return "⚠️ 招聘调度器未加载，无法启动仅打招呼。"
            snap = None
            if hasattr(rs, "incomplete_greet_only_for_pointer"):
                snap = rs.incomplete_greet_only_for_pointer()
            if snap and _go_n != int(snap.get("target") or 0):
                tg = int(snap.get("target") or 0)
                dn = int(snap.get("done") or 0)
                return (
                    f"📎 **该岗位有未完成的仅打招呼进度**：已成功 **{dn}** / 目标 **{tg}** 次。\n\n"
                    f"您输入的是新目标 **{_go_n}** 次。若要**接着打**，请发：**继续仅打招呼**。\n"
                    f"若要**放弃旧进度**并按 **{_go_n}** 次重新计数，请发：**仅打招呼{_go_n}重开**。"
                )
            if snap and _go_n == int(snap.get("target") or 0):
                dn = int(snap.get("done") or 0)
                tg = int(snap.get("target") or 0)
                return (
                    f"📎 **该岗位仅打招呼进度**：已成功 **{dn}** / **{tg}** 次（任务已停）。\n\n"
                    "请任选其一：\n"
                    "· **继续仅打招呼** — 接着打完剩余次数\n"
                    f"· **仅打招呼{tg}重开** — 从零重新打 {tg} 次"
                )
            out = rs.apply_lark_greet_only_campaign(_go_n, resume=False)
            if not out.get("ok"):
                return f"⚠️ {out.get('error', '启动仅打招呼失败')}"
            jn = (out.get("job_name") or "").strip() or "当前岗位"
            rim = int(out.get("greet_only_interval_minutes") or 0)
            tot = int(out.get("greet_only_total_target") or _go_n)
            return (
                f"✅ **已启动「仅打招呼」定时任务**（{jn}）\n\n"
                f"· 目标：累计成功打招呼 **{tot}** 次；每个定时间隔内会**尽量打满剩余次数**（直至推荐列表耗尽或反爬/验证中断），不再按交替模式的「每轮 3 人」拆条。\n"
                f"· 定时间隔：约每 **{rim or '（默认）'}** 分钟一步（与 jd 中推荐间隔 / 轮换间隔对齐）。\n"
                f"· **不会**自动收网、**不会**注册透析规则引擎；达标或连续多轮无招呼后自动停表，并飞书问您是否收简历。\n"
                f"· 请保持 **Chrome CDP** 与 Boss 登录；停止可发 **停止收网** 或关闭无人值守。\n"
                f"· 几天后再来同一岗位：可先 **mcp:get_recruitment_job_memory** 或发 **进度**，系统会带历史快照（简历/仅打招呼进度）。"
            )
        except Exception as e:
            logger.exception("[LarkCmd] 仅打招呼任务失败: %s", e)
            return f"⚠️ 启动失败：{e}"

    # --- 每轮收网/打招呼人数：修改或查询（不调 LLM）---
    ts = t.strip()
    ups = _extract_hr_batch_limit_updates(ts)
    if _AMBIG_PEOPLE_ONLY.match(ts) and not ups:
        return (
            "请写明要改哪一项（任选一条发送）：\n"
            "· **收网改成80人** — 每轮在左侧最多处理 80 个会话（有附件下载 PDF，无附件点「求简历」）\n"
            "· **打招呼改成20人** — 每轮「推荐牛人」最多打招呼 20 人\n"
            "· **推荐间隔15分钟** — 推荐↔沟通收简历的轮换间隔（默认交替调度）\n"
            "查看当前配置可发：**每轮沟通多少人** 或 **进度**"
        )
    if ups:
        try:
            from l3_node.hr_loader import get_recruitment_scheduler

            rs = get_recruitment_scheduler()
            if rs is None or not hasattr(rs, "apply_lark_hr_batch_limits"):
                return "⚠️ 招聘调度器未加载，无法修改每轮人数。"
            logger.info("[LarkCmd] 批次参数更新 raw=%r payload=%s", ts[:160], ups)
            try:
                from l3_node.log_broadcaster import broadcast_log

                broadcast_log(f"[Lark HR] 正在写回调度器批次参数 {ups}…", "INFO")
            except Exception:
                pass
            kw: dict[str, int] = {}
            if "max_count" in ups:
                kw["max_count"] = ups["max_count"]
            if "greet_target" in ups:
                kw["greet_target"] = ups["greet_target"]
            if "recommend_interval_minutes" in ups:
                kw["recommend_interval_minutes"] = ups["recommend_interval_minutes"]
            out = rs.apply_lark_hr_batch_limits(**kw)
            if not out.get("ok"):
                return f"⚠️ {out.get('error', '更新失败')}"
            jn = (out.get("job_name") or "").strip() or "当前岗位"
            jf = (out.get("job_folder") or "").strip()
            mc = int(out.get("max_count", 0))
            gt = int(out.get("greet_target", 0))
            rim = int(out.get("recommend_interval_minutes", 0))
            rct = int(out.get("resume_collect_target", 0))
            ath = int(out.get("analyze_threshold", 0))
            try:
                from l3_node.log_broadcaster import broadcast_log

                broadcast_log(
                    f"[Lark HR] 批次参数已更新 {jn}: 收网每轮={mc} 打招呼每轮={gt} 招呼↔收简历间隔={rim}min "
                    f"resume_collect_target={rct} analyze_threshold={ath}",
                    "SUCCESS",
                )
            except Exception:
                pass
            tail = ""
            if "max_count" in ups:
                tail = (
                    f"\n· 累计收简历目标 / 自动透析触发：**{rct}** 份（已与「每轮收网 {mc} 人」对齐）"
                )
            ptr_line = (
                f"\n\n📎 **当前无人值守指针**：岗位 **{jn}**"
                + (f"，数据目录 **`{jf}`**" if jf else "")
                + "。调度与 Boss 选岗均以此为准（与助手卡片里「某岗历史状态」可能不是同一岗）。\n"
                "若本意是**另一岗位**：请先单独发一行 Boss 选岗（如 `Python 工程师 _ 杭州 15-25K`），"
                "或与「打招呼/收网改成…」写在**同一条消息**。"
            )
            return (
                f"✅ **已更新并重新注册定时任务**（{jn}）\n\n"
                f"· 每轮收网（左侧会话上限）：**{mc}** 人\n"
                f"· 每轮推荐打招呼：**{gt}** 人\n"
                f"· 推荐牛人定时间隔：**{rim}** 分钟"
                f"{tail}\n\n"
                f"说明：Boss 单页下「牛人沟通」与「抓简历」严格交替，同一时刻只执行一种，可按轮次结果提前切换。"
                f"单次最多处理上述人数。"
                f"{ptr_line}"
            )
        except Exception as e:
            logger.exception("[LarkCmd] 批次参数更新失败: %s", e)
            return f"⚠️ 更新失败：{e}"
    if _HR_BATCH_QUERY.search(ts) and len(ts) <= 200:
        try:
            from l3_node.hr_loader import get_recruitment_scheduler

            rs = get_recruitment_scheduler()
            if rs is None or not hasattr(rs, "get_recruitment_status_digest"):
                return "⚠️ 无法读取调度配置。"
            d = rs.get_recruitment_status_digest("")
            if not d.get("has_active_job"):
                return "当前无绑定招聘岗位，请先配置无人值守后再问每轮人数。"
            jn = d.get("job_name") or ""
            mc = int(d.get("max_count_per_harvest") or 50)
            gt = int(d.get("greet_target") or 3)
            rim = int(d.get("recommend_interval_minutes") or 15)
            sw = int(d.get("greet_harvest_switch_interval_minutes") or rim or 10)
            eg = bool(d.get("enable_greet_recommend", True))
            logger.info("[LarkCmd] 答复「每轮人数」查询 job=%s", jn[:48])
            interval_line = f"· **推荐↔收简历轮换基准**：**{sw}** 分钟（单页严格交替，可按轮次提前切换）\n"
            return (
                f"📋 **当前岗位：{jn}**\n\n"
                f"· **每轮收网**：左侧会话最多处理 **{mc}** 人（有简历下载、无简历求简历）\n"
                f"· **每轮推荐打招呼**：最多 **{gt}** 人（推荐牛人：{'开启' if eg else '关闭'}）\n"
                f"{interval_line}\n"
                f"修改示例：**收网改成80人**、**打招呼改成20人**、**推荐间隔15分钟**"
            )
        except Exception as e:
            logger.exception("[LarkCmd] 每轮人数查询失败: %s", e)
            return f"⚠️ 读取失败：{e}"

    # --- 分析 / 透析镜 ---
    if matches_hr_analyze_command(t):
        _key = t.strip().casefold()
        _now = time.monotonic()
        with _analyze_cmd_dedup_lock:
            if (
                _key == (_analyze_cmd_last_key or "").casefold()
                and (_now - _analyze_cmd_last_mono) < _ANALYZE_CMD_COOLDOWN_SEC
            ):
                logger.info("[LarkCmd] 忽略重复「分析/透析镜」cooldown=%ss text=%s", _ANALYZE_CMD_COOLDOWN_SEC, t[:80])
                return REPLY_ANALYZE_COOLDOWN
            _analyze_cmd_last_key = t.strip()
            _analyze_cmd_last_mono = _now
        threading.Thread(target=_background_hr_analyze, daemon=True, name="lark-hr-analyze").start()
        logger.info("[LarkCmd] 拦截「分析/透析镜」已启动后台任务 text=%s", t[:80])
        return REPLY_ANALYZE

    # --- 进度 / 状态简报（不重跑 LLM）---
    if matches_hr_status_briefing_command(t):
        try:
            from l3_node.channels.lark.hr_recruitment_notify import build_hr_l3_status_briefing_text

            return build_hr_l3_status_briefing_text(reason="manual")
        except Exception as e:
            logger.exception("[LarkCmd] 进度简报失败: %s", e)
            return f"⚠️ 无法生成进度简报：{e}"

    fuzzy = try_default_l3_fuzzy_clarification(t, channel_id=channel_id or "")
    if fuzzy:
        logger.info("[LarkCmd] L3 模糊意图澄清 text=%r", t[:100])
        return fuzzy

    return None
