"""Boss 直聘工具共享：Cookie 加载、导航等（含拟人化等待）"""
import json
import logging
from pathlib import Path
from typing import List, Dict, Any

from .human_utils import human_wait

logger = logging.getLogger(__name__)

COOKIE_PATHS = [
    Path(__file__).resolve().parent.parent.parent / ".cookie" / "boss_zhipin_cookies.json",
    Path.home() / ".hr_plugin" / "config" / "boss_zhipin_cookies.json",
]

# 职位筛选：Boss 新 UI 为搜索框，老 UI 为下拉
BOSS_JOB_TRIGGER_SELECTORS = [
    "input.chat-job-search",
    "span.chat-select-job",
    "div.ui-dropmenu-label span.chat-select-job",
    "div.ui-dropmenu-label",
    ".chat-select-job",
    "[class*='chat-select-job']",
    "[class*='job-select']",
    "div[class*='dropmenu']:has(span)",
]
# 兼容旧变量名
BOSS_JOB_DROPDOWN_SELECTORS = BOSS_JOB_TRIGGER_SELECTORS

BOSS_CHAT_ITEM_SELECTORS = [
    "div.geek-item",
    ".geek-item",
    "[class*='geek-item']",
]


def load_cookies() -> List[Dict[str, Any]]:
    for p in COOKIE_PATHS:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Load cookies failed {p}: {e}")
    return []


def _normalize_job_text(s: str) -> str:
    """标准化职位文本，便于比较（去除多余空格）"""
    if not s:
        return ""
    return " ".join(s.split())


def _normalize_for_match(s: str) -> str:
    """将下划线、多空格统一为单空格，便于 'Java _杭州' 与 'Java_杭州' 精确匹配"""
    if not s:
        return ""
    return " ".join(s.replace("_", " ").split())


def _strip_for_compare(s: str) -> str:
    """去除空格、下划线、括号等，便于模糊匹配"""
    if not s:
        return ""
    for c in " _（）()":
        s = s.replace(c, "")
    return s


def _get_current_job_label(page) -> str:
    """获取当前选中的职位文本，尝试多种选择器（Boss 可能将文案放在不同位置）"""
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
    """
    校验当前选中的职位是否包含目标关键词，避免选错（如误选 Java）。
    返回 (是否通过, 当前选中文本用于错误提示)。
    """
    try:
        current = _get_current_job_label(page)
        if not current or current == "全部职位":
            return False, current if current else "(空)"
        want = _normalize_job_text(job_text)
        curr = _normalize_job_text(current)

        # 必须包含目标职位的核心词（如 Golang、Java），避免交叉误选
        if "Golang" in want and "Golang" not in curr:
            return False, current
        if "Java" in want and "Java" not in curr and "Golang" in curr:
            return False, current

        # 模糊匹配：取目标前 10 个非空字符作为核心，当前文本也归一化后比较
        want_core = _strip_for_compare(want)[:12]
        curr_core = _strip_for_compare(curr)
        if want_core and (want_core in curr_core or want[:10] in curr):
            return True, current
        # 兼容 Boss 显示 "Java _ 杭州 10-15K" 与目标 "Java_杭州 10-15K"（_strip_for_compare 已统一去除空格/下划线）
        want_stripped = _strip_for_compare(job_text)
        curr_stripped = _strip_for_compare(current)
        if want_stripped[:10] in curr_stripped or curr_stripped[:10] in want_stripped:
            return True, current
        # 兼容 Boss 可能显示为「开发（杭州）」：核心词 + 薪资
        if "Golang" in want and "Golang" in curr and ("杭州" in curr or "25" in curr or "40" in curr):
            return True, current
        return False, current
    except Exception:
        return False, ""


def select_job(page, job_text: str) -> bool:
    """
    在 Boss 沟通页点击职位下拉，选择包含 job_text 的选项。
    使用 div.ui-dropmenu-label / span.chat-select-job 定位。
    选择后必须校验，若选中错误职位则返回 False（宁可不做也不误操作）。
    """
    if not job_text or not job_text.strip():
        return False

    try:
        page.bring_to_front()
        human_wait(page, 0.2, 0.6)
    except Exception:
        pass

    # 若当前已选中目标职位，直接返回（避免误操作、兼容 Boss 新 UI）
    ok, _ = _verify_job_selected(page, job_text)
    if ok:
        logger.info("职位「%s」已选中，跳过选择", job_text)
        return True

    job_trigger = None
    for sel in BOSS_JOB_TRIGGER_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                job_trigger = loc
                break
        except Exception:
            pass
    if not job_trigger:
        logger.warning("未找到职位选择触发元素")
        return False

    job_trigger.click()
    human_wait(page, 0.5, 1.2)

    # 提取匹配关键词：支持 "资深Golang语言开发_杭州 25-40K" 或 "Java _杭州 4-6K"（空格/下划线统一）
    job_key = job_text.replace("_", " ").replace("  ", " ").strip()
    if not job_key:
        return False
    parts = job_key.split()
    first_word = parts[0] if parts else job_key
    must_contain = "Golang" if "Golang" in job_text else (first_word if len(first_word) > 2 else job_key[:10])

    # 若为搜索框，仅用核心词（如 Java/Golang）筛选，避免 Boss 精确匹配导致「没有相关职位」
    # 例如输入 "Java 杭州 4-6K" 会搜不到，实际职位是 "Java_杭州 4-6K"
    try:
        inp = page.locator("input.chat-job-search").first
        if inp.count() > 0:
            inp.fill("")
            human_wait(page, 0.15, 0.5)
            inp.fill(first_word[:12])
            human_wait(page, 0.4, 0.9)
    except Exception:
        pass

    # 匹配用的多个关键词，优先精确
    match_keywords = [
        job_key,
        job_key[:20],
        first_word,
        job_key[:15],
    ]

    option_clicked = False

    # 路径 1：传统下拉菜单（div.ui-dropmenu-menu）
    menu_selectors = [
        "div.ui-dropmenu-menu",
        "div[class*='dropmenu-menu']",
        "[class*='dropmenu'][class*='show']",
        ".dropmenu-list",
    ]
    item_selectors = "li, [class*='option'], [class*='item'], [class*='dropmenu-item'], div[role='option']"

    for menu_sel in menu_selectors:
        try:
            menu = page.locator(menu_sel).first
            if menu.count() == 0:
                continue
            items = menu.locator(item_selectors)
            cnt = items.count()
            for i in range(min(cnt, 80)):
                item = items.nth(i)
                txt = (item.inner_text() or "").strip()
                if not txt or len(txt) < 3:
                    continue
                txt_norm = _normalize_for_match(txt)
                for kw in match_keywords:
                    if kw and (kw in txt or _normalize_for_match(kw) in txt_norm):
                        try:
                            item.scroll_into_view_if_needed()
                            human_wait(page, 0.1, 0.4)
                            item.click()
                            option_clicked = True
                        except Exception:
                            pass
                        break
                if option_clicked:
                    break
                job_key_norm = _normalize_for_match(job_key)
                if must_contain in txt and (first_word in txt or job_key[:12] in txt or job_key_norm[:15] in txt_norm):
                    try:
                        item.scroll_into_view_if_needed()
                        human_wait(page, 0.1, 0.4)
                        item.click()
                        option_clicked = True
                    except Exception:
                        pass
                    break
            if option_clicked:
                break
            for kw in match_keywords:
                if not kw:
                    continue
                try:
                    hit = menu.get_by_text(kw, exact=False)
                    if hit.count() > 0:
                        hit.first.click()
                        option_clicked = True
                        break
                except Exception:
                    pass
            if option_clicked:
                break
        except Exception:
            pass

    # 路径 2：Boss 新 UI 搜索框式职位选择（无传统下拉，选项用 get_by_text 全页查找）
    # 页面上既有职位选项也有对话列表的 source-job，需用「职位名+薪资/城市」区分
    if not option_clicked:
        # 构建唯一标识：含薪资或城市，避免点到对话列表的 source-job
        unique_parts = []
        if "25-40" in job_text or "25-40K" in job_text:
            unique_parts.append("25-40")
        if "4-6" in job_text or "4-6K" in job_text:
            unique_parts.append("4-6")
        if "10-15" in job_text or "10-15K" in job_text:
            unique_parts.append("10-15")
        if "杭州" in job_text:
            unique_parts.append("杭州")
        if "40K" in job_text or "40k" in job_text.lower():
            unique_parts.append("40")
        unique_parts.append(job_key[:15])

        job_key_norm = _normalize_for_match(job_key)
        for unique in unique_parts:
            if not unique or len(unique) < 2:
                continue
            try:
                # 优先：文本同时含职位核心词和唯一标识（薪资/城市）
                candidates = page.get_by_text(must_contain, exact=False).filter(has_text=unique)
                if candidates.count() > 0:
                    for i in range(min(candidates.count(), 5)):
                        el = candidates.nth(i)
                        txt = (el.inner_text() or "").strip()
                        txt_norm = _normalize_for_match(txt)
                        if (job_key[:12] in txt or job_key_norm[:15] in txt_norm or
                                (unique in txt and len(txt) > 10) or (unique in txt_norm and len(txt) > 10)):
                            el.scroll_into_view_if_needed()
                            human_wait(page, 0.15, 0.5)
                            el.click()
                            option_clicked = True
                            break
                if option_clicked:
                    break
            except Exception:
                pass

        if not option_clicked:
            # 回退：遍历含 first_word 的元素，用规范化匹配找到目标（兼容 "Java_杭州" vs "Java 杭州"）
            try:
                hits = page.get_by_text(first_word, exact=False)
                for i in range(min(hits.count(), 15)):
                    el = hits.nth(i)
                    txt = (el.inner_text() or "").strip()
                    if len(txt) < 8:
                        continue
                    txt_norm = _normalize_for_match(txt)
                    if job_key_norm[:12] in txt_norm or job_key[:12] in txt:
                        el.scroll_into_view_if_needed()
                        human_wait(page, 0.15, 0.5)
                        el.click()
                        option_clicked = True
                        break
                if not option_clicked:
                    for variant in [job_key, job_key.replace(" ", " _ "), job_text]:
                        if not variant:
                            continue
                        hit = page.get_by_text(variant, exact=False)
                        if hit.count() > 0:
                            for j in range(min(hit.count(), 3)):
                                txt = (hit.nth(j).inner_text() or "").strip()
                                if len(txt) > 10 and _normalize_for_match(job_key)[:10] in _normalize_for_match(txt):
                                    hit.nth(j).scroll_into_view_if_needed()
                                    human_wait(page, 0.15, 0.5)
                                    hit.nth(j).click()
                                    option_clicked = True
                                    break
                            if option_clicked:
                                break
            except Exception:
                pass

    human_wait(page, 0.5, 1.2)

    # 校验：等待 DOM 更新，最多重试 2 次
    ok, current_display = False, ""
    for _ in range(3):
        ok, current_display = _verify_job_selected(page, job_text)
        if ok:
            break
        human_wait(page, 0.3, 0.8)

    # Boss 新 UI：职位选择为搜索框，选中后 label 可能仍为空，无法从 DOM 校验
    if not ok and option_clicked and not current_display:
        logger.info("已点击职位选项，新 UI 无法从 DOM 校验，信任选择结果")
        return True

    if not ok:
        if not option_clicked:
            logger.error(
                "未找到匹配的职位选项「%s」。请确认：1) 已打开 Boss 沟通页 2) 职位存在且未下架 3) 将标签页置于前台。",
                job_text,
            )
        else:
            logger.error(
                "职位选择校验失败：当前显示「%s」与目标「%s」不符。",
                current_display,
                job_text,
            )
        return False
    return True


def navigate_to_candidate_chat(page, job_keyword: str, candidate_name: str, candidate_skill: str) -> bool:
    """
    在 Boss 沟通页：选择职位、点击候选人对话进入聊天。
    Returns:
        True 成功进入对话，False 未找到
    """
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
