"""
Lark 高优指令拦截器 — 停止收网 / 触发透析镜分析

在送入 LLM 之前处理纯文本遥控指令，调用 DAGWorkflow.inject_signal 与 resume / 独立透析镜。

匹配规则（摘要）：
- **停止收网**：短指令「停止 / 暂停 / stop」或子串（停止收网、别抓了、停抓…）会 inject STOP_HARVEST。
  若整句主要是「停止/关闭 招聘·无人值守·自动化」且**无**收网/抓取类语境，则 **不拦截**，交给 Agent（如 stop_automated_recruitment）。
- **透析镜**：「开始分析 / 分析 / 再分析 / 跑透析镜…」等命中后后台停收网并跑分析；含 BI/报表/需求分析等歧义时不拦截。
"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# 明显是「停掉招聘自动化/无人值守」，且无收网抓取语境 → 不抢 inject，走调度器/Agent
_RECRUITMENT_AUTOMATION_STOP = re.compile(
    r"(停止|关闭|取消|结束|不要)(?:了)?(?:所有|全部|整个)?(?:的)?(?:无人值守|自动化)?(?:招聘|无人值守|自动化招聘)"
    r"|(?:招聘|无人值守)(?:任务)?(?:停止|关闭|取消|结束)"
    r"|stop\s*(?:automation|recruitment|hiring|unattended)",
    re.I,
)
# 收网/抓取侧语境：出现则视为与 Playwright 收网相关，可与「停止招聘」同句仍 inject
_HARVEST_OR_FETCH_CONTEXT = re.compile(
    r"收网|抓取|抓简历|抓人|别抓|停抓|harvest|pending|下载简历|打招呼|Playwright|爬虫|收简历|入库简历|简历抓取",
    re.I,
)
# 子串：明确要停当前收网/抓取
_HARVEST_STOP_PHRASES = re.compile(
    r"停止收网|暂停收网|结束收网|别抓了|先别抓|别抓先|停抓|停止抓取|停止抓简历|先停抓|"
    r"别跑了|停手|先停下|立刻停收网|马上停收网|强制停收网|"
    r"stop\s*harvest|halt\s*harvest",
    re.I,
)
# 泛化「立刻停」类（秒停）：短句或与收网语境同现
_URGENT_STOP = re.compile(r"立刻停止|马上停止|立即停止|强制停止|立即停|马上停|立刻停", re.I)

# 分析 / 透析镜：整句或短指令
_ANALYZE_EXACT = frozenset(
    {
        "开始分析",
        "分析简历",
        "分析",
        "再分析",
        "重新分析",
        "再去分析",
        "再跑分析",
        "立即分析",
        "马上分析",
        "跑透析镜",
        "执行透析镜",
        "启动透析镜",
        "开透析镜",
        "透析镜",
        "HR透析镜",
        "hr透析镜",
    }
)
_ANALYZE_EXTRA = re.compile(
    r"^(再|重新)(来)?(一遍|一下)?分析(简历)?$"
    r"|^(立即|马上)(开始)?分析(简历)?$"
    r"|再跑一次透析镜|重新跑透析镜|再跑透析镜|透析镜再跑",
    re.I,
)
# 非 HR 简历分析歧义
_ANALYZE_AMBIGUOUS = re.compile(
    r"BI分析|数据分析|报表分析|需求分析|商业智能|漏斗分析|转化分析|归因分析",
    re.I,
)

REPLY_STOP = (
    "🛑 已发送强制停止信号！当前调度正在安全挂起，请稍候查看最终战报。"
)
REPLY_ANALYZE = (
    "🧠 收到指令！停止一切抓取，立刻启动 Wasm 透析镜开始分析已入库的简历！"
)


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
    """注入停止信号后：优先 DAG resume，否则独立透析镜。"""
    try:
        from core.workflow_engine import DAGWorkflow, SIGNAL_STOP_HARVEST
        from l3_node.local_memory import load_workflow_state, save_workflow_state
        from l3_node.skills.hr_recruitment_dag import build_hr_recruitment_dag

        wid = _resolve_hr_workflow_id()
        DAGWorkflow.inject_signal(wid, SIGNAL_STOP_HARVEST)
        st = load_workflow_state(wid)
        if st:
            completed = list(st.get("completed_nodes") or [])
            # Harvest 已完成但 Analyze 未跑：将断点推到 analyze 节点以便 resume
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


def _recruitment_stop_without_harvest_cue(text: str) -> bool:
    """True：主要是停招聘/无人值守，且句子里没有收网抓取侧线索 → 不 inject，交给 Agent。"""
    if not _RECRUITMENT_AUTOMATION_STOP.search(text):
        return False
    if _HARVEST_OR_FETCH_CONTEXT.search(text) or _HARVEST_STOP_PHRASES.search(text):
        return False
    return True


def _matches_stop_harvest_inject(text: str) -> bool:
    """是否应对当前消息 inject STOP_HARVEST（秒停 Playwright 侧）。"""
    t = text.strip()
    if not t:
        return False
    t_lower = t.lower()
    if t in ("停止", "暂停") or t_lower == "stop":
        return True
    if _HARVEST_STOP_PHRASES.search(t):
        return True
    if _URGENT_STOP.search(t):
        # 短句「立刻停止」类默认秒停；长句且纯招聘停已由 _recruitment_stop_without_harvest_cue 整体 return None
        return len(t) <= 24 or bool(_HARVEST_OR_FETCH_CONTEXT.search(t))
    return False


def _matches_hr_analyze_command(text: str) -> bool:
    """是否走停收网 + 透析镜后台任务。"""
    t = text.strip()
    if not t:
        return False
    if t in _ANALYZE_EXACT:
        return True
    if _ANALYZE_EXTRA.search(t):
        return True
    if _ANALYZE_AMBIGUOUS.search(t):
        return False
    return False


def try_lark_workflow_command_intercept(user_text: str) -> str | None:
    """
    若命中遥控指令则返回飞书回复文案（调用方应直接回复并 **不再** 调用 LLM）。
    未命中返回 None。
    """
    t = (user_text or "").strip()
    if not t:
        return None

    # --- 停止收网（子串/正则 + 招聘歧义排除）---
    if _matches_stop_harvest_inject(t):
        if _recruitment_stop_without_harvest_cue(t):
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
        return REPLY_STOP

    # --- 分析 / 透析镜 ---
    if _matches_hr_analyze_command(t):
        threading.Thread(target=_background_hr_analyze, daemon=True, name="lark-hr-analyze").start()
        logger.info("[LarkCmd] 拦截「分析/透析镜」已启动后台任务 text=%s", t[:80])
        return REPLY_ANALYZE

    return None
