"""
原子 Tool: atom_request_resume
收网流程中点击「求简历」按钮。atom_inbox_harvester 遍历对话时，对未发简历的候选人调用。
"""
from __future__ import annotations

import logging

from .human_utils import human_wait

logger = logging.getLogger(__name__)

# 求简历按钮：初沟通、已沟通（提醒对方）、再次求简历 等状态
BOSS_REQUEST_RESUME_SELECTORS = [
    "span.operate-btn:has-text('求简历')",
    ".operate-btn:has-text('求简历')",
    "span:has-text('求简历')",
    "[class*='operate-btn']:has-text('求简历')",
    "[class*='operate']:has-text('求简历')",
    "span:has-text('提醒对方')",
    "[class*='operate-btn']:has-text('提醒对方')",
    "span:has-text('再次求简历')",
    "[class*='operate-btn']:has-text('再次求简历')",
]

# 求简历确认弹窗的「确定」按钮
BOSS_CONFIRM_BTN_SELECTORS = [
    "span.boss-btn-primary.boss-btn:has-text('确定')",
    ".boss-btn-primary.boss-btn:has-text('确定')",
    "span.boss-btn-primary.boss-btn",
    "[class*='boss-btn-primary']:has-text('确定')",
]


def _click_confirm_dialog(page) -> bool:
    """点击求简历弹窗中的「确定」按钮，返回是否成功"""
    for _ in range(3):
        human_wait(page, 0.3, 0.8)
        for sel in BOSS_CONFIRM_BTN_SELECTORS:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.scroll_into_view_if_needed()
                    human_wait(page, 0.15, 0.4)
                    loc.click()
                    human_wait(page, 0.5, 1.2)
                    return True
            except Exception:
                pass
        try:
            if page.get_by_role("button", name="确定").count() > 0:
                page.get_by_role("button", name="确定").first.click()
                human_wait(page, 0.5, 1.2)
                return True
        except Exception:
            pass
    return False


def _click_request_resume_btn(page) -> bool:
    """点击求简历按钮，若出现确认弹窗则点击确定，返回是否成功。
    支持：求简历、提醒对方、再次求简历（已主动沟通场景）。
    """
    human_wait(page, 1.0, 2.0)  # 聊天面板异步加载，等待「求简历」按钮出现
    for attempt in range(3):
        if attempt > 0:
            try:
                page.evaluate("window.scrollBy(0, 300)")
                human_wait(page, 0.2, 0.5)
                page.evaluate("window.scrollBy(0, -300)")
                human_wait(page, 0.15, 0.4)
            except Exception:
                pass
        for role_text in ["求简历", "提醒对方", "再次求简历"]:
            try:
                btn = page.get_by_role("button", name=role_text)
                if btn.count() > 0:
                    btn.first.scroll_into_view_if_needed()
                    human_wait(page, 0.2, 0.5)
                    btn.first.click(force=True)
                    human_wait(page, 0.5, 1.2)
                    _click_confirm_dialog(page)
                    logger.info("求简历按钮已点击（role=button name=%s）", role_text)
                    return True
            except Exception as e:
                logger.debug("role 选择器 %s 失败: %s", role_text, e)
        for sel in BOSS_REQUEST_RESUME_SELECTORS:
            try:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.scroll_into_view_if_needed()
                    human_wait(page, 0.15, 0.5)
                    loc.click(force=True)
                    human_wait(page, 0.5, 1.2)
                    _click_confirm_dialog(page)
                    return True
            except Exception:
                pass
        for text in ["求简历", "提醒对方", "再次求简历"]:
            try:
                hit = page.get_by_text(text, exact=False)
                if hit.count() > 0:
                    for i in range(min(hit.count(), 5)):
                        el = hit.nth(i)
                        try:
                            el.scroll_into_view_if_needed()
                            human_wait(page, 0.1, 0.4)
                            el.click(force=True)
                            human_wait(page, 0.5, 1.2)
                            _click_confirm_dialog(page)
                            logger.info("求简历按钮已点击（text=%s）", text)
                            return True
                        except Exception:
                            continue
            except Exception:
                pass
        for area_sel in ["[class*='chat-content']", "[class*='message-panel']", "[class*='dialog-detail']", "[class*='chat-panel']", "main"]:
            try:
                area = page.locator(area_sel).first
                if area.count() > 0:
                    for text in ["求简历", "提醒对方", "再次求简历"]:
                        hit = area.get_by_text(text, exact=False)
                        if hit.count() > 0:
                            hit.first.scroll_into_view_if_needed()
                            human_wait(page, 0.15, 0.5)
                            hit.first.click(force=True)
                            human_wait(page, 0.5, 1.2)
                            _click_confirm_dialog(page)
                            logger.info("求简历按钮已点击（area=%s text=%s）", area_sel, text)
                            return True
            except Exception:
                pass
        try:
            for sel in [
                "button:has-text('求简历')", "button:has-text('提醒对方')",
                "[role='button']:has-text('求简历')", "[role='button']:has-text('提醒对方')",
                "a:has-text('求简历')", "a:has-text('提醒对方')",
            ]:
                loc = page.locator(sel).first
                if loc.count() > 0:
                    loc.scroll_into_view_if_needed()
                    human_wait(page, 0.15, 0.5)
                    loc.click(force=True)
                    human_wait(page, 0.5, 1.2)
                    _click_confirm_dialog(page)
                    return True
        except Exception:
            pass
        try:
            clicked = page.evaluate("""
                () => {
                    const texts = ['求简历', '提醒对方', '再次求简历'];
                    for (const t of texts) {
                        const els = Array.from(document.querySelectorAll('*')).filter(el => el.textContent?.trim() === t);
                        for (const el of els) {
                            if (el.offsetParent && el.getBoundingClientRect().width > 0) {
                                el.click();
                                return true;
                            }
                        }
                    }
                    return false;
                }
            """)
            if clicked:
                human_wait(page, 0.5, 1.2)
                _click_confirm_dialog(page)
                logger.info("求简历按钮已点击（JS 兜底）")
                return True
        except Exception as e:
            logger.debug("JS 兜底点击失败: %s", e)
        human_wait(page, 0.3, 0.8)
    logger.warning("求简历按钮未找到或点击失败（Boss 可能已改 UI，请检查选择器）")
    return False


def atom_request_resume(
    cdp_url: str = "http://127.0.0.1:9222",
    jd_config_path: str = "",
    job_keyword: str = "",
    candidate_name: str = "",
    candidate_skill: str = "",
) -> dict:
    """
    单人：选职位 → 在左侧列表定位候选人 → 点击「求简历」。
    需 Chrome 调试端口；candidate_name / candidate_skill 与沟通列表展示一致。
    """
    from pathlib import Path

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"success": False, "request_sent": False, "error": "playwright 未安装"}

    from .atom_post_job_boss import get_jd_select, load_jd_config
    from .boss_utils import dismiss_boss_onboarding_overlays, navigate_to_candidate_chat, navigate_to_chat_page, select_job

    jd = load_jd_config(jd_config_path, "") if (jd_config_path and str(jd_config_path).strip() and Path(jd_config_path).exists()) else {}
    job_text = (get_jd_select(jd) if jd else "") or ""
    if not (job_text or "").strip():
        job_text = (job_keyword or "").strip()
    if not job_text:
        return {
            "success": False,
            "request_sent": False,
            "error": "需要 jd_config_path 指向有效 jd.json，或提供 job_keyword（与 Boss 下拉职位行一致）",
        }
    name = (candidate_name or "").strip()
    if not name:
        return {"success": False, "request_sent": False, "error": "candidate_name 不能为空"}
    skill = (candidate_skill or "").strip()
    if not skill:
        return {"success": False, "request_sent": False, "error": "candidate_skill 不能为空（与列表中 source-job 展示一致）"}

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(cdp_url, timeout=15000)
            contexts = browser.contexts
            if not contexts:
                return {"success": False, "request_sent": False, "error": "未找到浏览器上下文"}
            pages = contexts[0].pages
            if not pages:
                return {"success": False, "request_sent": False, "error": "未找到页面"}
            page = None
            for p in pages:
                u = p.url or ""
                if "zhipin.com" in u or "zhpin.com" in u:
                    page = p
                    break
            page = page or pages[0]
            navigate_to_chat_page(page)
            dismiss_boss_onboarding_overlays(page)
            if not select_job(page, job_text):
                return {"success": False, "request_sent": False, "error": f"无法选择职位「{job_text}」"}
            if not navigate_to_candidate_chat(page, job_text, name, skill):
                return {"success": False, "request_sent": False, "error": "未找到对应候选人会话，请检查姓名与技能标签"}
            sent = _click_request_resume_btn(page)
            return {
                "success": bool(sent),
                "request_sent": bool(sent),
                "error": "" if sent else "求简历按钮未点击成功",
            }
    except Exception as e:
        logger.exception("atom_request_resume")
        return {"success": False, "request_sent": False, "error": str(e)}


def atom_request_resume_batch(
    cdp_url: str = "http://127.0.0.1:9222",
    jd_config_path: str = "",
    job_text: str = "",
    max_items: int = 50,
) -> dict:
    """批量遍历沟通列表，对无附件简历的对话点击求简历（与收网编排共用流程）。"""
    from .boss_harvest_orchestrator import harvest_resume_full_flow

    return harvest_resume_full_flow(
        cdp_url=cdp_url,
        job_text=job_text,
        jd_config_path=jd_config_path,
        download_to_pending=True,
        max_items=max_items,
        request_if_no_resume=True,
        filter_tab="全部",
    )
