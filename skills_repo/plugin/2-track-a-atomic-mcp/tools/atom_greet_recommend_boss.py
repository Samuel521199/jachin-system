"""
原子 Tool: atom_greet_recommend_boss
在「推荐牛人」页面自动筛选候选人并打招呼。
流程：读 JD → 点击推荐牛人 → 遍历候选人卡片 → 跳过已沟通者 → 第0层小模型初筛 → 打招呼 → 限2人（测试阶段）
"""
import json
import logging
import os
import re
# from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError  # 小模型初筛暂注释
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
JD_CONFIG_PATH = _ROOT / "data" / "jd_to_publish.json"
MIN_MATCH_SCORE = 30
MAX_GREET_PER_RUN = 2


def load_jd_config(config_path: str = "") -> dict:
    """加载 JD 配置，用于 brain_filter 的 hr_criteria"""
    path = Path(config_path) if config_path else JD_CONFIG_PATH
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"load_jd_config failed {path}: {e}")
    return {}


def _rule_filter_fallback(resume_text: str, hr_criteria: str) -> dict:
    """brain_filter API 失败时的规则兜底：简单关键词匹配，存疑时放宽通过"""
    text = (resume_text or "") + (hr_criteria or "")
    pass_ = True
    score = 58
    if "本科" in hr_criteria:
        if not any(x in (resume_text or "") for x in ("本科", "硕士", "博士", "bachelor", "master")):
            pass_ = False
            score = 28
    if pass_ and any(x in hr_criteria for x in ("5年", "5-10", "10年")):
        m = re.search(r"(\d+)[年\+以上]", resume_text or "")
        if m and int(m.group(1)) < 3:
            pass_ = False
            score = 25
    return {"pass": pass_, "reason": "规则兜底", "score": score}


def _jd_to_hr_criteria(jd: dict) -> str:
    """将 JD 转为 brain_filter 使用的 hr_criteria 摘要"""
    parts = []
    if jd.get("job_title"):
        parts.append(f"岗位：{jd['job_title']}")
    if jd.get("education"):
        parts.append(f"学历要求：{jd['education']}")
    if jd.get("experience"):
        parts.append(f"经验要求：{jd['experience']}")
    if jd.get("jd_full"):
        parts.append(jd["jd_full"][:600])
    return "\n".join(parts) or "学历本科，经验3年"


def atom_greet_recommend_boss(
    cdp_url: str = "http://127.0.0.1:9222",
    jd_config_path: str = "",
) -> dict:
    """
    在推荐牛人页面自动筛选并打招呼。
    前置：Chrome 以 --remote-debugging-port 启动，已登录 Boss。
    流程：读 JD → 点推荐牛人 → 遍历卡片 → 跳过有沟通记录的 → 小模型初筛(≥30%符合度) → 打招呼，最多2人。
    """
    jd = load_jd_config(jd_config_path)
    hr_criteria = _jd_to_hr_criteria(jd)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "success": False,
            "greeted_count": 0,
            "skipped_chat_history": 0,
            "skipped_low_score": 0,
            "error": "playwright 未安装",
        }

    # try:
    #     from .brain_filter import brain_filter
    # except ImportError:
    #     from brain_filter import brain_filter

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(cdp_url, timeout=5000)
            contexts = browser.contexts
            if not contexts:
                return {"success": False, "greeted_count": 0, "skipped_chat_history": 0, "skipped_low_score": 0, "error": "未找到浏览器上下文"}
            context = contexts[0]
            pages = context.pages
            if not pages:
                return {"success": False, "greeted_count": 0, "skipped_chat_history": 0, "skipped_low_score": 0, "error": "未找到页面"}

            page = None
            for p in pages:
                try:
                    url = p.url or ""
                    if "geek/recommend" in url and ("zhipin.com" in url or "zhpin.com" in url):
                        page = p
                        break
                except Exception:
                    pass
            if not page:
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

            # 1. 进入推荐牛人页面（仅点左侧菜单，避免误触右上角头像）
            current_url = page.url or ""
            if "geek/recommend" not in current_url:
                rec_btn = page.locator('a[ka="menu-geek-recommend"]').first
                if rec_btn.count() > 0:
                    rec_btn.click()
                    page.wait_for_timeout(2000)
                if "geek/recommend" not in (page.url or ""):
                    page.goto("https://www.zhipin.com/web/geek/recommend", wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(2000)
            if "geek/recommend" not in (page.url or ""):
                page.goto("https://www.zhipin.com/web/geek/recommend", wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(1500)

            # 2. 点击职位下拉框（仅选含职位/薪资的主内容区下拉，排除右上角用户菜单）
            job_title = jd.get("job_title", "")
            if job_title:
                job_dropdown = None
                for f in [page] + list(page.frames):
                    for label in f.locator("div.ui-dropmenu-label").all():
                        try:
                            txt = (label.inner_text() or "").strip()
                            if job_title in txt or (len(job_title) >= 4 and job_title[:4] in txt) or ("K" in txt and "周" not in txt):
                                job_dropdown = label
                                break
                        except Exception:
                            pass
                    if job_dropdown:
                        break
                if job_dropdown:
                    job_dropdown.click()
                    page.wait_for_timeout(800)
                    selected = False
                    for f in [page] + list(page.frames):
                        if selected:
                            break
                        try:
                            opts = f.locator("li, div[class*='option'], div[class*='item'], [class*='dropdown'] li")
                            for idx in range(opts.count()):
                                el = opts.nth(idx)
                                txt = (el.inner_text() or "").strip()
                                if job_title in txt or (len(job_title) >= 4 and job_title[:4] in txt):
                                    el.click()
                                    page.wait_for_timeout(1500)
                                    selected = True
                                    break
                        except Exception:
                            pass

            # 3. 定位候选人卡片（主页面或 iframe；Boss 可能将列表放在 iframe 中）
            def _collect_frames():
                """收集主页面及所有 frame（含嵌套）"""
                seen = set()
                out = [page]
                for f in page.frames:
                    if id(f) not in seen:
                        seen.add(id(f))
                        out.append(f)
                return out

            CARD_SELECTORS = [
                "div.candidate-card-wrap",
                "div[class*='candidate-card']",
                "div[class*='card-wrap']",
                "div.card-inner[data-geek]",
                "div[data-geek]",
                "[class*='geek-card']",
                "[class*='recommend-card']",
                "[data-geek-id]",
            ]

            def _get_cards():
                all_frames = _collect_frames()
                for frame in all_frames:
                    try:
                        for sel in CARD_SELECTORS:
                            loc = frame.locator(sel)
                            try:
                                n = loc.count()
                                if n > 0:
                                    return frame, loc
                            except Exception:
                                pass
                    except Exception:
                        pass
                return None, None

            page.wait_for_timeout(2000)
            frame, cards_loc = _get_cards()
            for _ in range(3):
                if frame and cards_loc:
                    break
                page.wait_for_timeout(2000)
                frame, cards_loc = _get_cards()

            if not frame or not cards_loc:
                curr_url = (page.url or "")[:120]
                return {
                    "success": False,
                    "greeted_count": 0,
                    "skipped_chat_history": 0,
                    "skipped_low_score": 0,
                    "error": f"未找到推荐牛人候选人卡片。当前页: {curr_url}。请确保在推荐牛人页面且列表已加载。",
                }

            n_cards = cards_loc.count()
            greeted = 0
            skipped_chat = 0
            skipped_score = 0

            for i in range(min(n_cards, 20)):
                if greeted >= MAX_GREET_PER_RUN:
                    break
                try:
                    card = cards_loc.nth(i)
                    # 3. 检查是否有沟通记录图标（同事沟通过则跳过；图标可能在卡片内或父容器内）
                    has_chat = card.locator("svg.icon-chat-history").count() > 0
                    if not has_chat:
                        has_chat = card.locator("..").locator("svg.icon-chat-history").count() > 0
                    if has_chat:
                        skipped_chat += 1
                        continue

                    # 4. 点击卡片主体进入简历详情（避免点到打招呼按钮）
                    click_area = card.locator("div.card-inner, div.col-2, span.name").first
                    if click_area.count() > 0:
                        click_area.click()
                    else:
                        card.click()
                    page.wait_for_timeout(1200)

                    # 5. 提取详情页简历文本（可能在主页面或弹窗）
                    resume_text = ""
                    for f in [page] + list(page.frames):
                        try:
                            content = f.locator("div.geek-detail, [class*='detail'], [class*='resume']").first
                            if content.count() > 0:
                                resume_text = content.inner_text() or ""
                                if len(resume_text) > 100:
                                    break
                        except Exception:
                            pass
                    if not resume_text:
                        try:
                            body = page.locator("body")
                            resume_text = body.inner_text()[:4000] or ""
                        except Exception:
                            pass

                    # 第0层初筛：暂时注释小模型，直接使用硬性规则筛选（后续再完善 brain_filter）
                    # if os.environ.get("GREET_USE_RULE_ONLY"):
                    result = _rule_filter_fallback(resume_text, hr_criteria)
                    # else:
                    #     try:
                    #         with ThreadPoolExecutor(max_workers=1) as ex:
                    #             future = ex.submit(brain_filter, online_resume_text=resume_text, hr_criteria=hr_criteria)
                    #             result = future.result(timeout=8)
                    #     except (FuturesTimeoutError, Exception) as e:
                    #         logger.warning(f"brain_filter 超时或失败，使用规则兜底: {e}")
                    #         result = _rule_filter_fallback(resume_text, hr_criteria)
                    score = result.get("score", 0)
                    if not result.get("pass", False) or score < MIN_MATCH_SCORE:
                        skipped_score += 1
                        _close_detail(page)
                        continue

                    # 6. 通过初筛，点击打招呼（支持详情页按钮 class：btn-v2 btn-sure-v2 btn-greet）
                    greet_clicked = False
                    for f in [page] + list(page.frames):
                        if greet_clicked:
                            break
                        for sel in ["button.btn-v2.btn-sure-v2.btn-greet", "button.btn-greet", "button:has-text('打招呼')"]:
                            try:
                                b = f.locator(sel).first
                                if b.count() > 0:
                                    b.click(force=True)
                                    greeted += 1
                                    greet_clicked = True
                                    page.wait_for_timeout(600)
                                    break
                            except Exception:
                                pass

                    # 7. 若有「已向牛人发送招呼」弹窗，点击「知道了」
                    _dismiss_greet_popup(page)

                    # 8. 关闭详情
                    _close_detail(page)
                    page.wait_for_timeout(600)

                except Exception as e:
                    logger.warning(f"atom_greet_recommend_boss card {i} failed: {e}")
                    try:
                        _close_detail(page)
                    except Exception:
                        pass

            return {
                "success": True,
                "greeted_count": greeted,
                "skipped_chat_history": skipped_chat,
                "skipped_low_score": skipped_score,
                "error": "",
            }

    except Exception as e:
        logger.error(f"atom_greet_recommend_boss failed: {e}", exc_info=True)
        return {
            "success": False,
            "greeted_count": 0,
            "skipped_chat_history": 0,
            "skipped_low_score": 0,
            "error": str(e),
        }


def _dismiss_greet_popup(page):
    """若有「已向牛人发送招呼」等弹窗，点击「知道了」"""
    page.wait_for_timeout(400)
    for f in [page] + list(page.frames):
        try:
            btn = f.locator("button:has-text('知道了')").first
            if btn.count() > 0:
                btn.click()
                page.wait_for_timeout(400)
                return
        except Exception:
            pass


def _close_detail(page):
    """点击 × 关闭候选人详情"""
    for f in [page] + list(page.frames):
        try:
            b = f.locator("i.icon-close").first
            if b.count() > 0:
                b.click()
                page.wait_for_timeout(500)
                return
        except Exception:
            pass
