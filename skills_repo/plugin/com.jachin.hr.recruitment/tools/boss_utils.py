"""Boss 直聘工具共享：Cookie 加载、导航等（含拟人化等待）"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .config import _get_jachin_root
from .human_utils import human_wait

logger = logging.getLogger(__name__)

COOKIE_PATHS = [
    Path(__file__).resolve().parent.parent / ".cookie" / "boss_zhipin_cookies.json",
    Path.home() / ".hr_plugin" / "config" / "boss_zhipin_cookies.json",
    _get_jachin_root() / "config" / "boss_zhipin_cookies.json",
]

BOSS_JOB_SEARCH_INPUT_SELECTORS = [
    "input.chat-job-search",
    "input[placeholder*='职位']",
    "input[placeholder*='请输入']",
]
BOSS_JOB_TRIGGER_SELECTORS = [
    "input.chat-job-search",
    "span.chat-select-job",
    "div.ui-dropmenu-label span.chat-select-job",
    "div.ui-dropmenu-label:has(span.chat-select-job)",
    "div.ui-dropmenu-label",
    ".chat-select-job",
    "[class*='chat-select-job']",
    "[class*='job-select']",
    "div[class*='dropmenu']:has(span.chat-select-job)",
]
BOSS_JOB_DROPDOWN_SELECTORS = BOSS_JOB_TRIGGER_SELECTORS

BOSS_CHAT_ITEM_SELECTORS = [
    "div.geek-item",
    ".geek-item",
    "[class*='geek-item']",
]


def load_cookies() -> list[dict[str, Any]]:
    for p in COOKIE_PATHS:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("Load cookies failed %s: %s", p, e)
    return []


def _normalize_job_text(s: str) -> str:
    if not s:
        return ""
    return " ".join(s.split())


def _normalize_for_match(s: str) -> str:
    if not s:
        return ""
    return " ".join(s.replace("_", " ").split())


def _strip_for_compare(s: str) -> str:
    if not s:
        return ""
    for c in " _（）()":
        s = s.replace(c, "")
    return s


def _to_boss_search_term(raw: str, full_job_text: str) -> str:
    r, f = (raw or "").strip().lower(), (full_job_text or "").lower()
    if ("go" in r or "go" in f) and "golang" not in r and "golang" not in f:
        if "后端" in (raw or "") or "后端" in (full_job_text or "") or "开发" in (raw or ""):
            return "golang"
    return raw


def _get_current_job_label(page) -> str:
    for sel in ["span.chat-select-job", ".chat-select-job", "div.ui-dropmenu-label"]:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            t = (loc.inner_text() or loc.text_content() or "").strip()
            if t:
                return t
        except Exception:
            pass
    return ""


def _verify_job_selected(page, job_text: str) -> tuple[bool, str]:
    try:
        current = _get_current_job_label(page)
        if not current or current == "全部职位":
            return False, current if current else "(空)"
        want = _normalize_job_text(job_text)
        curr = _normalize_job_text(current)
        if "Golang" in want and "Golang" not in curr:
            return False, current
        if "Java" in want and "Java" not in curr and "Golang" in curr:
            return False, current
        if ("go" in want.lower() or "golang" in want.lower()) and "golang" in curr.lower():
            return True, current
        want_core = _strip_for_compare(want)[:12]
        curr_core = _strip_for_compare(curr)
        if want_core and (want_core in curr_core or want[:10] in curr):
            return True, current
        want_stripped = _strip_for_compare(job_text)
        curr_stripped = _strip_for_compare(current)
        if want_stripped[:10] in curr_stripped or curr_stripped[:10] in want_stripped:
            return True, current
        if "Golang" in want and "Golang" in curr and ("杭州" in curr or "25" in curr or "40" in curr):
            return True, current
        return False, current
    except Exception:
        return False, ""


def select_all_positions(page) -> bool:
    try:
        page.bring_to_front()
        human_wait(page, 0.2, 0.5)
    except Exception:
        pass
    current = _get_current_job_label(page)
    if current == "全部职位":
        logger.info("已为「全部职位」，跳过")
        return True
    try:
        for trigger_sel in [
            "div.ui-dropmenu-label:has(span.chat-select-job)",
            "span.chat-select-job",
            "div.ui-dropmenu-label",
        ]:
            trig = page.locator(trigger_sel).first
            if trig.count() > 0 and trig.is_visible():
                trig.click()
                human_wait(page, 0.6, 1.2)
                break
    except Exception as e:
        logger.warning("点击职位下拉失败: %s", e)
        return False
    try:
        all_opt = page.get_by_text("全部职位", exact=True).first
        if all_opt.count() > 0 and all_opt.is_visible():
            all_opt.click()
            human_wait(page, 1.0, 1.8)
        else:
            fallback = page.locator("[class*='dropmenu-item'], [class*='option']").filter(has_text="全部职位").first
            if fallback.count() > 0 and fallback.is_visible():
                fallback.click()
                human_wait(page, 1.0, 1.8)
    except Exception as e:
        logger.warning("选择「全部职位」选项失败: %s", e)
        return False
    if _get_current_job_label(page) == "全部职位":
        logger.info("已选择「全部职位」")
        return True
    logger.warning("选择「全部职位」后校验未通过")
    return False


def select_job(page, job_text: str) -> bool:
    if not job_text or not job_text.strip():
        return False
    try:
        page.bring_to_front()
        human_wait(page, 0.2, 0.5)
    except Exception:
        pass
    if _verify_job_selected(page, job_text):
        logger.info("职位已选中，跳过")
        return True
    job_key = job_text.replace("_", " ").replace("  ", " ").strip()
    raw = job_key.split()[0] if job_key else job_text
    if len(raw) < 4:
        raw = job_text[:20]
    search_value = _to_boss_search_term(raw, job_text)
    try:
        all_job_btn = page.get_by_text("全部职位", exact=False).first
        if all_job_btn.count() > 0 and all_job_btn.is_visible():
            all_job_btn.click()
            human_wait(page, 0.6, 1.2)
        else:
            for trigger_sel in ["span.chat-select-job", "div.ui-dropmenu-label:has(span.chat-select-job)", "input[placeholder*='职位']"]:
                trig = page.locator(trigger_sel).first
                if trig.count() > 0 and trig.is_visible():
                    trig.click()
                    human_wait(page, 0.6, 1.0)
                    break
    except Exception:
        pass
    inp = None
    for sel in BOSS_JOB_SEARCH_INPUT_SELECTORS:
        loc = page.locator(sel).first
        if loc.count() > 0 and loc.is_visible():
            inp = loc
            break
    if not inp or inp.count() == 0:
        logger.warning("未找到职位搜索框")
        return False
    inp.click()
    human_wait(page, 0.3, 0.6)
    inp.fill("")
    human_wait(page, 0.15, 0.3)
    inp.fill(search_value)
    human_wait(page, 0.8, 1.5)
    if page.get_by_text("没有相关职位", exact=False).count() > 0:
        inp.fill("")
        human_wait(page, 0.5, 0.8)
    candidates = page.get_by_text(search_value, exact=False).filter(has_text="K")
    n = candidates.count()
    if n >= 1:
        try:
            candidates.nth(0).scroll_into_view_if_needed()
            human_wait(page, 0.2, 0.4)
            candidates.nth(0).click()
            human_wait(page, 1.2, 2.0)
            if _verify_job_selected(page, job_text):
                logger.info("已选择下拉第1项")
                human_wait(page, 0.5, 0.8)
                return True
        except Exception as e:
            logger.debug("点第1项失败: %s", e)
    if n >= 2:
        try:
            candidates.nth(1).scroll_into_view_if_needed()
            human_wait(page, 0.2, 0.4)
            candidates.nth(1).click()
            human_wait(page, 1.2, 2.0)
            if _verify_job_selected(page, job_text):
                logger.info("已选择下拉第2项")
                human_wait(page, 0.5, 0.8)
                return True
        except Exception as e:
            logger.debug("点第2项失败: %s", e)
    logger.warning("下拉第1、第2项均未选择成功，失败")
    return False


def _is_chat_inbox_page(url: str) -> bool:
    u = (url or "").lower()
    if "zhipin.com" not in u and "zhpin.com" not in u:
        return False
    if "/geek/chat" in u or "/chat/index" in u:
        return True
    if "/chat" in u and "recommend" not in u and "job/list" not in u:
        return True
    return False


def _is_user_menu_dropdown_open(page) -> bool:
    try:
        for kw in ("个人中心", "退出", "账号权益"):
            hit = page.get_by_text(kw, exact=False).first
            if hit.count() > 0 and hit.is_visible():
                return True
    except Exception:
        pass
    return False


def _close_user_menu_if_open(page) -> None:
    if _is_user_menu_dropdown_open(page):
        logger.info("检测到用户菜单已展开（可能误点头像），关闭后点击沟通")
        try:
            page.keyboard.press("Escape")
            human_wait(page, 0.3, 0.6)
        except Exception:
            pass


def navigate_to_chat_page(page) -> bool:
    try:
        _close_user_menu_if_open(page)
        url = page.url or ""
        if _is_chat_inbox_page(url):
            logger.debug("已在沟通页，跳过导航")
            return True
        chat_btn = page.locator('a[ka="menu-geek-chat"]').first
        if chat_btn.count() > 0:
            chat_btn.click()
            human_wait(page, 1.5, 2.5)
            if _is_chat_inbox_page(page.url or ""):
                return True
        for target_url in [
            "https://www.zhipin.com/web/geek/chat",
            "https://www.zhipin.com/web/chat/index",
        ]:
            try:
                page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
                human_wait(page, 1.5, 2.5)
                if "zhipin.com" in (page.url or "") or "zhpin.com" in (page.url or ""):
                    return True
            except Exception:
                continue
    except Exception as e:
        logger.warning("navigate_to_chat_page 失败: %s", e)
    return True


def navigate_to_candidate_chat(page, job_keyword: str, candidate_name: str, candidate_skill: str) -> bool:
    job_dropdown = None
    for sel in BOSS_JOB_DROPDOWN_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                job_dropdown = loc
                break
        except Exception:
            pass
    if job_dropdown:
        job_dropdown.click()
        human_wait(page, 0.5, 1.2)
        job_option = page.locator(
            f"li:has-text('{job_keyword}'), [class*='option']:has-text('{job_keyword}'), [class*='item']:has-text('{job_keyword}')"
        ).first
        if job_option.count() > 0:
            job_option.click()
            human_wait(page, 0.3, 0.8)
        else:
            try:
                menu = page.locator("[class*='dropmenu'], [class*='dropdown']")
                if menu.count() > 0:
                    menu.get_by_text(job_keyword, exact=False).first.click()
                human_wait(page, 0.3, 0.8)
            except Exception:
                pass
        human_wait(page, 0.2, 0.5)
    chat_item = None
    for sel in BOSS_CHAT_ITEM_SELECTORS:
        try:
            loc = page.locator(sel).filter(
                has=page.locator(f"span.geek-name:has-text('{candidate_name}')")
            ).filter(
                has=page.locator(f"span.source-job:has-text('{candidate_skill}')")
            ).first
            if loc.count() > 0:
                chat_item = loc
                break
        except Exception:
            try:
                loc = page.locator(sel).filter(
                    has=page.get_by_text(candidate_name)
                ).filter(has=page.get_by_text(candidate_skill)).first
                if loc.count() > 0:
                    chat_item = loc
                    break
            except Exception:
                pass
    if not chat_item or chat_item.count() == 0:
        return False
    chat_item.scroll_into_view_if_needed()
    human_wait(page, 0.15, 0.5)
    chat_item.click()
    human_wait(page, 1.0, 2.0)
    return True
