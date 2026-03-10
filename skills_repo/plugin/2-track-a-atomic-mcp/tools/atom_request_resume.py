"""
原子 Tool: atom_request_resume
仅负责点击「求简历」按钮。不包含：打招呼、下载 PDF。（含拟人化等待）
"""
import logging

from .boss_utils import navigate_to_candidate_chat, select_job
from .human_utils import human_wait

logger = logging.getLogger(__name__)

BOSS_CHAT_LIST_SELECTORS = [
    "div.geek-item",
    ".geek-item",
    "[class*='geek-item']",
]

# 求简历按钮：初沟通、已沟通（提醒对方）、再次求简历 等状态（Boss 可能改 UI，多选器兜底）
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

# 仅当存在「点击预览附件简历」按钮时视为已发简历。不匹配「附件简历」tab（所有人都有）、职位卡片等
BOSS_RESUME_SENT_SELECTORS = [
    "span.card-btn:has-text('点击预览附件简历')",
    "text=点击预览附件简历",
]


def atom_request_resume(
    cdp_url: str = "http://127.0.0.1:9222",
    job_keyword: str = "java",
    candidate_name: str = "付华斌",
    candidate_skill: str = "Java",
) -> dict:
    """
    点击「求简历」按钮：选择职位 → 进入候选人对话 → 若对方未发简历则点击求简历。

    前置条件：Chrome 以 --remote-debugging-port 启动，登录 Boss 直聘并打开「沟通」页。

    Args:
        cdp_url: Chrome 远程调试地址
        job_keyword: 职位关键词，用于在「全部职位」下拉中匹配
        candidate_name: 目标候选人姓名（测试固定：付华斌）
        candidate_skill: 目标候选人技能标签（如 Java）

    Returns:
        {"success": bool, "request_sent": bool, "error": str}
        request_sent: 是否执行了求简历操作
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"success": False, "request_sent": False, "error": "playwright 未安装"}

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(cdp_url, timeout=5000)
            contexts = browser.contexts
            if not contexts:
                return {"success": False, "request_sent": False, "error": "未找到浏览器上下文"}
            context = contexts[0]
            pages = context.pages
            if not pages:
                return {"success": False, "request_sent": False, "error": "未找到页面"}

            page = None
            for p in pages:
                try:
                    url = p.url or ""
                    if "zhipin.com" in url or "zhpin.com" in url:
                        page = p
                        break
                except Exception:
                    pass
            if not page:
                page = pages[0]

            page.wait_for_load_state("domcontentloaded", timeout=5000)

            if not navigate_to_candidate_chat(page, job_keyword, candidate_name, candidate_skill):
                return {
                    "success": False,
                    "request_sent": False,
                    "error": f"未找到候选人「{candidate_name} {candidate_skill}」的对话",
                }

            logger.info("已进入 %s %s 的对话", candidate_name, candidate_skill)

            has_resume = False
            for sel in BOSS_RESUME_SENT_SELECTORS:
                try:
                    if page.locator(sel).count() > 0:
                        has_resume = True
                        break
                except Exception:
                    pass
            if not has_resume:
                try:
                    has_resume = page.get_by_text("附件简历-", exact=False).count() > 0
                except Exception:
                    pass

            if has_resume:
                logger.info("对方已发送简历，无需求简历")
                return {"success": True, "request_sent": False, "error": ""}

            req_btn = None
            for sel in BOSS_REQUEST_RESUME_SELECTORS:
                try:
                    loc = page.locator(sel).first
                    if loc.count() > 0:
                        req_btn = loc
                        break
                except Exception:
                    pass
            if not req_btn or req_btn.count() == 0:
                return {
                    "success": False,
                    "request_sent": False,
                    "error": "未找到「求简历」按钮",
                }
            req_btn.scroll_into_view_if_needed()
            human_wait(page, 0.15, 0.5)
            req_btn.click()
            human_wait(page, 0.5, 1.2)
            _click_confirm_dialog(page)
            logger.info("已点击求简历并确认")
            return {"success": True, "request_sent": True, "error": ""}
    except Exception as e:
        logger.error(f"atom_request_resume failed: {e}", exc_info=True)
        err_msg = str(e)
        if "connect" in err_msg.lower():
            err_msg = f"{err_msg}\n提示：请用 scripts\\launch_chrome_debug.ps1 启动 Chrome"
        return {"success": False, "request_sent": False, "error": err_msg}


def _has_resume(page) -> bool:
    """判断当前对话中对方是否已发简历（PDF 附件）。不匹配「附件简历」tab、在线简历等。"""
    for sel in BOSS_RESUME_SENT_SELECTORS:
        try:
            if page.locator(sel).count() > 0:
                logger.debug("_has_resume=True: 匹配选择器 %s", sel)
                return True
        except Exception:
            pass
    # 移除「附件简历-」全页匹配：tab「附件简历」或空状态「附件简历-暂无」会误触发
    # 仅靠 BOSS_RESUME_SENT_SELECTORS（点击预览附件简历）即可判断
    return False


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
        # 优先：Playwright 内置 role 选择器（Boss 改 UI 时更稳）
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
        # 回退：按文本查找可点击元素（Boss 新 UI 可能类名变化），exact=False 兼容按钮带额外文案
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
        # 限定在聊天区域查找（避免点到左侧列表）
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

        # 再尝试：button/span 等可点击元素，force 绕过遮挡
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
        # 兜底：JS 按文本点击（Boss 可能用非标准元素）
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


def atom_request_resume_batch(
    cdp_url: str = "http://127.0.0.1:9222",
    job_text: str = "资深Golang语言开发_杭州 25-40K",
    max_items: int = 50,
) -> dict:
    """
    遍历左侧对话列表，对每个未发简历的对话点击「求简历」。

    1. 选择指定岗位 JD（如「资深Golang语言开发_杭州 25-40K」）
    2. 遍历列表中的对话项，逐个点击进入
    3. 若对方已发简历则跳过，若有「求简历」按钮则点击

    前置：Chrome 以 --remote-debugging-port 启动，已打开 Boss 直聘「沟通」页。

    Returns:
        {"success": bool, "requested_count": int, "skipped_has_resume": int, "processed": list, "error": str}
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "success": False,
            "requested_count": 0,
            "skipped_has_resume": 0,
            "processed": [],
            "error": "playwright 未安装",
        }

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(cdp_url, timeout=5000)
            contexts = browser.contexts
            if not contexts:
                return {"success": False, "requested_count": 0, "skipped_has_resume": 0, "processed": [], "error": "未找到浏览器上下文"}
            context = contexts[0]
            pages = context.pages
            if not pages:
                return {"success": False, "requested_count": 0, "skipped_has_resume": 0, "processed": [], "error": "未找到页面"}

            page = None
            for p in pages:
                try:
                    url = p.url or ""
                    if "zhipin.com" in url or "zhpin.com" in url:
                        page = p
                        break
                except Exception:
                    pass
            if not page:
                page = pages[0]

            page.wait_for_load_state("domcontentloaded", timeout=5000)
            try:
                page.bring_to_front()
                human_wait(page, 0.3, 0.7)
            except Exception:
                pass

            if not select_job(page, job_text):
                return {
                    "success": False,
                    "requested_count": 0,
                    "skipped_has_resume": 0,
                    "processed": [],
                    "error": f"无法选择职位「{job_text}」，请确认该职位存在且未选错。宁可报错也不在错误职位下求简历。",
                }

            human_wait(page, 0.8, 1.5)

            chat_items_loc = None
            for sel in BOSS_CHAT_LIST_SELECTORS:
                try:
                    loc = page.locator(sel)
                    if loc.count() > 0:
                        chat_items_loc = loc
                        break
                except Exception:
                    pass

            if not chat_items_loc or chat_items_loc.count() == 0:
                return {
                    "success": True,
                    "requested_count": 0,
                    "skipped_has_resume": 0,
                    "processed": [],
                    "error": "未找到对话列表项",
                }

            n = min(chat_items_loc.count(), max_items)
            requested = 0
            skipped_resume = 0
            processed = []

            for i in range(n):
                try:
                    item = chat_items_loc.nth(i)
                    item.scroll_into_view_if_needed()
                    human_wait(page, 0.2, 0.6)
                    name_el = item.locator("span.geek-name").first
                    job_el = item.locator("span.source-job").first
                    name = (name_el.inner_text() or "").strip() if name_el.count() > 0 else f"item_{i}"
                    job = (job_el.inner_text() or "").strip() if job_el.count() > 0 else ""
                    label = f"{name} ({job})" if job else name

                    item.click()
                    human_wait(page, 1.2, 2.5)

                    if _has_resume(page):
                        skipped_resume += 1
                        processed.append({"label": label, "action": "skipped_has_resume"})
                        logger.info("跳过 %s：已发简历", label)
                        continue

                    if _click_request_resume_btn(page):
                        requested += 1
                        processed.append({"label": label, "action": "request_sent"})
                        logger.info("已对 %s 点击求简历", label)
                    else:
                        processed.append({"label": label, "action": "no_btn"})
                except Exception as e:
                    logger.warning("处理第 %d 项失败: %s", i + 1, e)
                    processed.append({"label": f"item_{i}", "action": "error", "error": str(e)})

            return {
                "success": True,
                "requested_count": requested,
                "skipped_has_resume": skipped_resume,
                "processed": processed,
                "error": "",
            }
    except Exception as e:
        logger.error(f"atom_request_resume_batch failed: {e}", exc_info=True)
        err_msg = str(e)
        if "connect" in err_msg.lower():
            err_msg = f"{err_msg}\n提示：请用 scripts\\launch_chrome_debug.ps1 启动 Chrome"
        return {
            "success": False,
            "requested_count": 0,
            "skipped_has_resume": 0,
            "processed": [],
            "error": err_msg,
        }
