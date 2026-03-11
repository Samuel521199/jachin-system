"""Boss 直聘工具共享：Cookie 加载、导航等（含拟人化等待）"""
import json
import logging
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

from .human_utils import human_wait

logger = logging.getLogger(__name__)

# 配置职位名 → Boss 沟通页显示格式映射（搜索「golang开发工程师」无结果，需用「Golang语言开发」）
JOB_TITLE_TO_BOSS_DISPLAY: dict[str, str] = {
    "golang开发工程师": "Golang语言开发",
    "Golang开发工程师": "Golang语言开发",
    "golang开发": "Golang语言开发",
    "Golang开发": "Golang语言开发",
    "java": "java",
    "Java": "java",
    "Java开发": "java",
    "java开发": "java",
}

# 职位前缀修饰词（Boss 下拉选项通常不含这些，需去掉后匹配）
JOB_TITLE_PREFIXES = ("高级", "资深", "初级", "中级", "实习", "专家", "架构师")


def job_title_to_boss_display(job_title: str) -> str:
    """
    将配置中的职位名（如 高级golang开发工程师）转换为 Boss 沟通页显示的格式（如 Golang语言开发）。
    搜索「高级golang开发工程师」会提示「没有相关职位」，需用「Golang语言开发」才能找到。
    支持：1) 精确映射 2) 去掉前缀后映射 3) 关键词匹配（含 golang→Golang语言开发）
    """
    if not job_title or not job_title.strip():
        return job_title or ""
    key = job_title.strip()
    # 1. 精确匹配
    if key in JOB_TITLE_TO_BOSS_DISPLAY:
        return JOB_TITLE_TO_BOSS_DISPLAY[key]
    # 2. 去掉前缀后匹配（高级golang开发工程师 → golang开发工程师）
    normalized = key
    for prefix in JOB_TITLE_PREFIXES:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :].strip()
            break
    if normalized in JOB_TITLE_TO_BOSS_DISPLAY:
        return JOB_TITLE_TO_BOSS_DISPLAY[normalized]
    # 3. 关键词匹配：含 golang/go 语言 → Golang语言开发
    key_lower = key.lower()
    if "golang" in key_lower or ("go" in key_lower and "语言" in key_lower):
        return "Golang语言开发"
    if "java" in key_lower and "javascript" not in key_lower and "typescript" not in key_lower:
        return "java"
    return key


def build_boss_job_text(
    job_title: str,
    salary_min: Optional[int] = None,
    salary_max: Optional[int] = None,
    city: str = "杭州",
) -> str:
    """
    构建 Boss 沟通页职位选项格式：Golang语言开发_杭州 19-20K。
    配置 job_title 为 golang开发工程师，Boss 显示为 Golang语言开发_杭州 19-20K。
    """
    display = job_title_to_boss_display(job_title)
    if salary_min is not None and salary_max is not None:
        return f"{display}_{city} {salary_min}-{salary_max}K"
    if salary_min is not None:
        return f"{display}_{city} {salary_min}K"
    return f"{display}_{city}" if city else display


def resolve_job_text_for_boss(job_name: str, jd_config_path: Optional[str] = None) -> str:
    """
    将 job_name（配置职位名）解析为 Boss 沟通页可用的 job_text。
    优先从 jd_to_publish.json 读取 salary_min/salary_max 构建完整格式；
    否则仅做职位名映射（如 golang开发工程师 → Golang语言开发）。
    """
    if not job_name or not job_name.strip():
        return job_name or ""
    jn = job_name.strip()
    path = Path(jd_config_path) if jd_config_path else None
    if not path or not path.exists():
        _proj = Path(__file__).resolve().parent.parent.parent
        path = _proj / "skills_repo" / "plugin" / "data" / "jd_to_publish.json"
    if path and path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            smin = data.get("salary_min")
            smax = data.get("salary_max")
            city = data.get("city", "杭州") or "杭州"
            if smin is not None and smax is not None:
                return build_boss_job_text(jn, int(smin), int(smax), city)
            if smin is not None:
                return build_boss_job_text(jn, int(smin), None, city)
        except Exception as e:
            logger.debug("resolve_job_text_for_boss 读取 jd 失败: %s", e)
    return build_boss_job_text(jn)

COOKIE_PATHS = [
    Path(__file__).resolve().parent.parent.parent / ".cookie" / "boss_zhipin_cookies.json",
    Path.home() / ".hr_plugin" / "config" / "boss_zhipin_cookies.json",
]

# 职位筛选：Boss 新 UI 为搜索框，老 UI 为下拉。仅匹配触发按钮(label)，不匹配下拉列表(list/menu)
BOSS_JOB_TRIGGER_SELECTORS = [
    "input.chat-job-search",
    "span.chat-select-job",
    "div.ui-dropmenu-label span.chat-select-job",
    "div.ui-dropmenu-label:has(span.chat-select-job)",
    "div.ui-dropmenu-label",
    ".chat-select-job",
    "[class*='chat-select-job']",
    "[class*='job-select']",
    "div[class*='dropmenu-label']",
]
# 兼容旧变量名
BOSS_JOB_DROPDOWN_SELECTORS = BOSS_JOB_TRIGGER_SELECTORS

BOSS_CHAT_ITEM_SELECTORS = [
    "div.geek-item",
    ".geek-item",
    "[class*='geek-item']",
]

# 左侧「沟通」菜单：仅匹配含「沟通」的链接，排除职位管理(job/list)、推荐牛人(recommend)
BOSS_CHAT_MENU_SELECTORS = [
    'a[ka="menu-geek-chat"]',
    'a[ka="menu-chat"]',
    'nav a:has-text("沟通")',
    'a[href*="/chat"]:has-text("沟通"):not([href*="job/list"]):not([href*="recommend"])',
    '[class*="sidebar"] a:has-text("沟通")',
    '[class*="left"] a:has-text("沟通")',
    '[class*="menu"] a:has-text("沟通")',
]
# 左侧导航区域 x 上限（像素），超过则视为头像区
CHAT_MENU_MAX_X = 400


def _is_chat_page(url: str) -> bool:
    """判断是否为 Boss 沟通页（含对话列表的页面）"""
    u = (url or "").lower()
    if "zhipin.com" not in u and "zhpin.com" not in u:
        return False
    # 沟通页：/web/chat/ 或 /web/geek/chat，排除 recommend、job/list 等
    if "/chat/" in u or "/geek/chat" in u:
        if "recommend" in u or "job/list" in u:
            return False
        return True
    return False


def navigate_to_chat_page(page) -> bool:
    """
    点击左侧「沟通」菜单进入沟通页，用于收网抓取简历。
    仅匹配左侧导航，绝不匹配右上角头像/用户菜单。
    """
    try:
        current_url = page.url or ""
        if _is_chat_page(current_url):
            # 已在沟通页，检查是否有对话列表
            for sel in BOSS_CHAT_ITEM_SELECTORS:
                try:
                    if page.locator(sel).count() > 0:
                        return True
                except Exception:
                    pass
        # 需导航：点击左侧「沟通」菜单（强制 x<400px，绝不点头像）
        for sel in BOSS_CHAT_MENU_SELECTORS:
            try:
                loc = page.locator(sel)
                for i in range(loc.count()):
                    el = loc.nth(i)
                    box = el.bounding_box()
                    if not box:
                        continue
                    # 硬性排除：仅点击左侧导航区（x < 400），头像在右侧
                    if box.get("x", 9999) >= CHAT_MENU_MAX_X:
                        continue
                    el.scroll_into_view_if_needed()
                    human_wait(page, 0.2, 0.5)
                    el.click()
                    human_wait(page, 1.5, 2.5)
                    logger.info("已点击左侧「沟通」菜单进入沟通页")
                    return True
            except Exception as e:
                logger.debug("navigate_to_chat_page 选择器 %s 失败: %s", sel, e)
        # 兜底：直接 goto 沟通页 URL（Boss 沟通页主入口，含对话列表）
        for target_url in [
            "https://www.zhipin.com/web/chat/",
            "https://www.zhipin.com/web/geek/chat",
        ]:
            try:
                page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
                human_wait(page, 2.0, 3.0)
                return True
            except Exception:
                pass
        return False
    except Exception as e:
        logger.warning("navigate_to_chat_page 失败: %s", e)
        return False


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

        # 必须包含目标职位的核心词（如 Golang、Java），避免交叉误选；兼容大小写
        want_lower = want.lower()
        curr_lower = curr.lower()
        if "golang" in want_lower and "golang" not in curr_lower:
            return False, current
        if "java" in want_lower and "java" not in curr_lower and "golang" in curr_lower:
            return False, current

        # 模糊匹配：取目标前 10 个非空字符作为核心，当前文本也归一化后比较；兼容大小写
        want_core = _strip_for_compare(want)[:12].lower()
        curr_core = _strip_for_compare(curr).lower()
        if want_core and (want_core in curr_core or want[:10].lower() in curr_lower):
            return True, current
        # 兼容 Boss 显示 "Java _ 杭州 10-15K" 与目标 "Java_杭州 10-15K"（_strip_for_compare 已统一去除空格/下划线）
        want_stripped = _strip_for_compare(job_text).lower()
        curr_stripped = _strip_for_compare(current).lower()
        if want_stripped[:10] in curr_stripped or curr_stripped[:10] in want_stripped:
            return True, current
        # 兼容 Boss 可能显示为「开发（杭州）」：核心词 + 薪资
        if "golang" in want_lower and "golang" in curr_lower and ("杭州" in curr or "25" in curr or "40" in curr):
            return True, current
        if "java" in want_lower and "java" in curr_lower and ("杭州" in curr or "4" in curr or "6" in curr or "10" in curr or "15" in curr):
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

    def _is_in_avatar_zone(el) -> bool:
        """元素是否在右上角头像区域（x > 60% 视口宽），若是则排除"""
        try:
            box = el.bounding_box()
            if not box:
                return False
            vw = (page.viewport_size or {}).get("width") or 1920
            return box.get("x", 0) > vw * 0.6
        except Exception:
            return False

    job_trigger = None
    for sel in BOSS_JOB_TRIGGER_SELECTORS:
        try:
            loc = page.locator(sel)
            if loc.count() == 0:
                continue
            # 若为 div.ui-dropmenu-label（无 chat-select-job 限定），遍历排除右上角用户菜单
            if sel == "div.ui-dropmenu-label":
                for i in range(loc.count()):
                    el = loc.nth(i)
                    txt = (el.inner_text() or "").strip()
                    if "周" in txt or "个人中心" in txt or "退出" in txt or "账号" in txt or "钱包" in txt:
                        continue
                    if _is_in_avatar_zone(el):
                        continue
                    job_trigger = el
                    break
            else:
                cand = loc.first
                if cand.count() > 0 and _is_in_avatar_zone(cand):
                    # 第一个在头像区，尝试找其他不在头像区的
                    for i in range(1, loc.count()):
                        el = loc.nth(i)
                        if not _is_in_avatar_zone(el):
                            job_trigger = el
                            break
                else:
                    job_trigger = cand
            if job_trigger:
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
    # 首字母大写变体（java->Java），便于匹配 Boss 显示的职位名
    first_word_cap = first_word.capitalize() if len(first_word) > 1 else first_word
    must_contain = "Golang" if "Golang" in job_text else (first_word if len(first_word) > 2 else job_key[:10])

    # 若为搜索框，仅用核心词（如 Java/Golang）筛选，避免 Boss 精确匹配导致「没有相关职位」
    # 输入首字母大写形式（java->Java）以提高 Boss 搜索命中率
    try:
        inp = page.locator("input.chat-job-search").first
        if inp.count() > 0:
            inp.fill("")
            human_wait(page, 0.15, 0.5)
            inp.fill(first_word_cap[:12] if first_word_cap else first_word[:12])
            human_wait(page, 0.4, 0.9)
    except Exception:
        pass

    # 匹配用的多个关键词，优先精确；含首字母大写变体以兼容 java/Java
    match_keywords = [
        job_key,
        job_key[:20],
        first_word_cap,
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
                # 排除已关闭职位，优先选开放中的
                if "关闭" in txt or "(关闭)" in txt:
                    continue
                txt_lower = txt.lower()
                txt_norm = _normalize_for_match(txt)
                for kw in match_keywords:
                    if kw and (kw in txt or kw.lower() in txt_lower or _normalize_for_match(kw) in txt_norm):
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
                must_lower = must_contain.lower()
                if (must_contain in txt or must_lower in txt_lower) and (first_word in txt or first_word_cap in txt or first_word.lower() in txt_lower or job_key[:12] in txt or job_key_norm[:15] in txt_norm):
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
        # 通用提取 X-Y 或 X-YK 薪资区间（如 19-20、10-11）
        for m in re.finditer(r"(\d+)-(\d+)(?:K)?", job_text):
            unique_parts.append(f"{m.group(1)}-{m.group(2)}")
        if "杭州" in job_text:
            unique_parts.append("杭州")
        unique_parts.append(job_key[:15])
        unique_parts.append(first_word_cap)

        job_key_norm = _normalize_for_match(job_key)
        for unique in unique_parts:
            if not unique:
                continue
            if len(unique) < 2 and unique != first_word_cap:
                continue
            try:
                # 优先：文本同时含职位核心词和唯一标识（薪资/城市）；兼容大小写
                candidates = page.get_by_text(must_contain, exact=False).filter(has_text=unique)
                if candidates.count() > 0:
                    for i in range(min(candidates.count(), 5)):
                        el = candidates.nth(i)
                        txt = (el.inner_text() or "").strip()
                        if "关闭" in txt or "(关闭)" in txt:
                            continue
                        txt_norm = _normalize_for_match(txt)
                        if (job_key[:12] in txt or job_key_norm[:15] in txt_norm or
                                (unique in txt and len(txt) > 10) or (unique.lower() in txt.lower() and len(txt) > 10)):
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
            # 回退：遍历含 first_word 或 first_word_cap 的元素（兼容 java/Java）
            try:
                for search_term in [first_word_cap, first_word]:
                    if option_clicked:
                        break
                    hits = page.get_by_text(search_term, exact=False)
                    for i in range(min(hits.count(), 15)):
                        el = hits.nth(i)
                        txt = (el.inner_text() or "").strip()
                        if len(txt) < 8 or "关闭" in txt or "(关闭)" in txt:
                            continue
                        txt_norm = _normalize_for_match(txt)
                        if job_key_norm[:12] in txt_norm or job_key[:12] in txt or search_term.lower() in txt.lower():
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
