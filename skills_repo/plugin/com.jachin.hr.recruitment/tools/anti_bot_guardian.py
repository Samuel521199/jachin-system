"""
Boss 直聘反爬/风控检测 + Human-in-the-Loop 衔接

在 Playwright 页面跳转或刷新后调用 check_and_bypass_anti_bot：
- 检测到极验滑块/验证文案 → ask_human_for_decision（无注入则挂起 workflow）
- 统帅「已解决」→ return True 继续 RPA
- 统帅「终止」→ 抛出 AntiBotTerminateException
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 与 ask_human_for_decision 选项文案严格一致（便于分支判断）
OPTION_CONTINUE = "已解决，继续抓取"
OPTION_ABORT = "环境危险，终止任务"

_HUMAN_PROMPT = (
    "🚨 发现 Boss 直聘滑块验证/反爬风控！请统帅在浏览器中手动完成滑动验证后，"
    "点击【已解决】继续执行，或点击【放弃】终止本次任务。"
)  # 与 SKILL/HITL 文案一致；选项见 OPTION_CONTINUE / OPTION_ABORT

# Playwright 选择器：极验 + 常见 Boss 验证文案容器（按需扩展）
_GEE_TEST_SELECTORS = [
    ".geetest_slider_button",
    ".geetest_slider",
    ".geetest_btn",
    "[class*='geetest_widget']",
    "[class*='geetest_holder']",
    "div[class*='geetest']",
    "iframe[src*='geetest']",
]

_TEXT_HINTS = (
    "滑块验证",
    "安全验证",
    "人机验证",
    "行为验证",
    "访问验证",
    "请完成验证",
    "拖动滑块",
    "智能验证",
)


def _ensure_repo_root_on_path() -> None:
    """便于从 MCP 包独立加载时 import l3_node / core。"""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "l3_node").is_dir() and (parent / "core").is_dir():
            s = str(parent)
            if s not in sys.path:
                sys.path.insert(0, s)
            return


def _import_ask_human():
    _ensure_repo_root_on_path()
    from l3_node.mcp_tools.human_ask_tool import ask_human_for_decision

    return ask_human_for_decision


def _import_anti_bot_terminate():
    _ensure_repo_root_on_path()
    from core.errors import AntiBotTerminateException

    return AntiBotTerminateException


def _loc_visible(page: Any, selector: str) -> bool:
    try:
        loc = page.locator(selector).first
        if loc.count() == 0:
            return False
        return bool(loc.is_visible())
    except Exception:
        return False


def _frame_walk_visible(page: Any, selector: str) -> bool:
    if _loc_visible(page, selector):
        return True
    try:
        for frame in page.frames:
            try:
                loc = frame.locator(selector).first
                if loc.count() > 0 and loc.is_visible():
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def detect_boss_anti_bot(page: Any) -> bool:
    """
    检测当前页是否出现 Boss/极验常见风控 UI。
    仅做启发式检测，宁可误报交由人工确认，避免静默封号风险。
    """
    for sel in _GEE_TEST_SELECTORS:
        if _frame_walk_visible(page, sel):
            logger.warning("[AntiBotGuardian] 命中选择器: %s", sel)
            return True

    for hint in _TEXT_HINTS:
        try:
            loc = page.get_by_text(hint, exact=False).first
            if loc.count() > 0 and loc.is_visible():
                logger.warning("[AntiBotGuardian] 命中验证文案: %s", hint)
                return True
        except Exception:
            continue

        try:
            for frame in page.frames:
                try:
                    loc = frame.get_by_text(hint, exact=False).first
                    if loc.count() > 0 and loc.is_visible():
                        logger.warning("[AntiBotGuardian] iframe 命中验证文案: %s", hint)
                        return True
                except Exception:
                    continue
        except Exception:
            pass

    return False


def should_reraise_hitl(exc: BaseException) -> bool:
    """判断是否为 HITL 挂起或反爬终止异常，供 Playwright 入口统一 re-raise。"""
    return type(exc).__name__ in ("SuspendForHumanException", "AntiBotTerminateException")


def check_and_bypass_anti_bot(page: Any, context: dict | None) -> bool:
    """
    页面导航/刷新后调用：若检测到风控则走 HITL，否则直接返回 True。

    Args:
        page: Playwright sync Page
        context: DAG / 业务上下文 dict，可含 ``_human_decision``（续跑注入）

    Returns:
        True — 继续执行后续 Playwright 逻辑

    Raises:
        SuspendForHumanException: 未注入决策时需挂起 workflow
        AntiBotTerminateException: 统帅选择终止任务
    """
    if not detect_boss_anti_bot(page):
        return True

    wc = dict(context or {})
    ask_human = _import_ask_human()
    AntiBotTerminateException = _import_anti_bot_terminate()

    # 物理记录：挂起前写入 HR progress.md（与 Jachin OS 规划绑定）
    if wc.get("_human_decision") is None:
        try:
            from datetime import datetime

            from l3_node.task_planning import append_hr_recruitment_progress

            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            job = (wc.get("job_folder") or wc.get("job_name") or "").strip() or "（未知岗位）"
            wid = str(wc.get("_dag_workflow_id") or "").strip()
            append_hr_recruitment_progress(
                f"> ⚠️ **[{ts}]** Boss **反爬/滑块验证** 触发，工作流即将 **HITL 挂起**；"
                f"请统帅在浏览器完成验证后注入决策。岗位: `{job}` workflow: `{wid}`"
            )
        except Exception as e:
            logger.debug("[AntiBotGuardian] HR progress 记录跳过: %s", e)

    choice = ask_human(
        _HUMAN_PROMPT,
        [OPTION_CONTINUE, OPTION_ABORT],
        injected_choice=wc.get("_human_decision"),
    )
    choice = (choice or "").strip()

    if choice == OPTION_ABORT:
        logger.error("[AntiBotGuardian] 统帅选择终止抓取")
        try:
            from datetime import datetime

            from l3_node.task_planning import append_hr_recruitment_progress

            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            job = (wc.get("job_folder") or wc.get("job_name") or "").strip() or "（未知岗位）"
            append_hr_recruitment_progress(
                f"> 🛑 **[{ts}]** 统帅在反爬对话框选择 **终止任务**。岗位: `{job}`"
            )
        except Exception as e:
            logger.debug("[AntiBotGuardian] HR progress 终止记录跳过: %s", e)
        raise AntiBotTerminateException(OPTION_ABORT)

    if choice != OPTION_CONTINUE:
        logger.warning("[AntiBotGuardian] 未识别的决策 %r，按继续处理", choice)

    logger.info("[AntiBotGuardian] 统帅确认已解决验证，继续 RPA")
    return True
