"""
原子 Tool: atom_greet_recommend_boss
在「推荐牛人」页面自动筛选候选人并打招呼。
流程：读 data/{岗位名}/jd.json → 在「全部职位」中选中该职位 → 点击推荐牛人 → 遍历卡片 → 初筛 → 打招呼。
支持 jd_config_path：仅从 data/{岗位名}/jd.json 读取，与 atom_post_job_boss 一致。
"""
import logging
import re
# from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError  # 小模型初筛暂注释
from pathlib import Path

from .anti_bot_guardian import check_and_bypass_anti_bot, should_reraise_hitl
from .atom_post_job_boss import load_jd_config, get_jd_select
from .boss_utils import select_job
from .os_signal_probe import os_stop_requested as _os_stop_requested

logger = logging.getLogger(__name__)

MIN_MATCH_SCORE = 30
MAX_GREET_PER_RUN = 3  # 每轮成功打招呼 3 人即结束本轮


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
    max_greet_per_run: int = 0,
    workflow_hitl_context: dict | None = None,
    os_context: dict | None = None,
) -> dict:
    """
    在推荐牛人页面自动筛选并打招呼。
    前置：Chrome 以 --remote-debugging-port 启动，已登录 Boss。
    流程：读 data/{岗位名}/jd.json（jd_config_path）→ 点击「全部职位」/职位下拉展开 → 选中该职位 → 点推荐牛人 → 遍历卡片 → 初筛 → 打招呼。

    workflow_hitl_context:
        可选 DAG 上下文，可含 ``_human_decision``，供反爬检测时 ask_human 续跑注入。
    os_context:
        可选 OS/Workflow 上下文，遍历卡片时探针 STOP_HARVEST。
    """
    _hitl = workflow_hitl_context if workflow_hitl_context is not None else os_context
    job_name = ""
    if jd_config_path and Path(jd_config_path).exists():
        p = Path(jd_config_path)
        if "data" in p.parts and p.name == "jd.json":
            try:
                idx = list(p.parts).index("data")
                if idx + 1 < len(p.parts):
                    job_name = p.parts[idx + 1]
            except Exception:
                pass
    jd = load_jd_config(jd_config_path, job_name)
    if not jd.get("job_title") and not jd.get("jd_full"):
        return {
            "success": False,
            "greeted_count": 0,
            "skipped_chat_history": 0,
            "skipped_low_score": 0,
            "error": "JD 配置为空，请传入 jd_config_path（指向 data/{岗位名}/jd.json）",
        }
    hr_criteria = _jd_to_hr_criteria(jd)
    limit = max_greet_per_run if max_greet_per_run > 0 else MAX_GREET_PER_RUN

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

            def _is_recommend_page(url: str) -> bool:
                """Boss 推荐牛人页面：geek/recommend 或 chat/recommend"""
                u = (url or "").lower()
                return ("zhipin.com" in u or "zhpin.com" in u) and ("geek/recommend" in u or "chat/recommend" in u)

            page = None
            for p in pages:
                try:
                    if _is_recommend_page(p.url or ""):
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

            page.bring_to_front()
            page.wait_for_load_state("domcontentloaded", timeout=5000)
            check_and_bypass_anti_bot(page, _hitl)

            # 1. 进入推荐牛人页面（优先点左侧菜单，避免多次 goto 导致刷新循环）
            current_url = page.url or ""
            if not _is_recommend_page(current_url):
                rec_btn = page.locator('a[ka="menu-geek-recommend"]').first
                if rec_btn.count() > 0:
                    rec_btn.click()
                    page.wait_for_timeout(2500)
                if not _is_recommend_page(page.url or ""):
                    # Boss 新版本可能用 chat/recommend，先试新 URL 再兜底旧 URL
                    for target_url in [
                        "https://www.zhipin.com/web/chat/recommend",
                        "https://www.zhipin.com/web/geek/recommend",
                    ]:
                        try:
                            page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
                            page.wait_for_timeout(3000)
                            if _is_recommend_page(page.url or ""):
                                break
                        except Exception:
                            continue
            check_and_bypass_anti_bot(page, _hitl)

            # 2. 在「全部职位」中选择与 jd_select 匹配的职位（推荐牛人页：div.ui-dropmenu-label）
            jd_select = get_jd_select(jd)
            if jd_select and not select_job(page, jd_select):
                return {
                    "success": False,
                    "greeted_count": 0,
                    "skipped_chat_history": 0,
                    "skipped_low_score": 0,
                    "error": f"无法在「全部职位」中选择职位「{jd_select}」，请确认 jd.json 中 jd_select 与 Boss 下拉显示一致",
                }
            if jd_select:
                page.wait_for_timeout(2500)  # 职位切换后列表会刷新，等待加载
            check_and_bypass_anti_bot(page, _hitl)

            # 3. 定位候选人卡片（主页面或 iframe；Boss chat/recommend 与 geek/recommend 结构可能不同）
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
                # chat/recommend 页面可能使用的选择器
                "[class*='recommend-list'] > div",
                "[class*='geek-list'] [class*='item']",
                "div[class*='job-card']",
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

            # 等待列表加载稳定，避免页面仍在刷新时查找卡片（减少重试次数，防止「一直刷新」感）
            page.wait_for_timeout(3000)
            frame, cards_loc = _get_cards()
            for attempt in range(2):
                if frame and cards_loc and cards_loc.count() > 0:
                    break
                page.wait_for_timeout(2500)
                frame, cards_loc = _get_cards()
            check_and_bypass_anti_bot(page, _hitl)

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
                if _os_stop_requested(os_context):
                    return {
                        "success": True,
                        "greeted_count": greeted,
                        "skipped_chat_history": skipped_chat,
                        "skipped_low_score": skipped_score,
                        "error": "",
                        "stopped_by_os": True,
                    }
                if greeted >= limit:
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

                    # 4. 点击卡片主体进入简历详情（避免点到打招呼按钮；点击后等待详情加载）
                    click_area = card.locator("div.card-inner, div.col-2, span.name").first
                    try:
                        if click_area.count() > 0:
                            click_area.click(timeout=8000)
                        else:
                            card.click(timeout=8000)
                    except Exception as click_err:
                        logger.debug("点击卡片失败(跳过): %s", click_err)
                        continue
                    page.wait_for_timeout(1800)
                    check_and_bypass_anti_bot(page, _hitl)
                    if _os_stop_requested(os_context):
                        return {
                            "success": True,
                            "greeted_count": greeted,
                            "skipped_chat_history": skipped_chat,
                            "skipped_low_score": skipped_score,
                            "error": "",
                            "stopped_by_os": True,
                        }

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
                                    b.click(force=True, timeout=5000)
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

                    # 9. 满 limit 人即立即退出，不继续遍历剩余卡片
                    if greeted >= limit:
                        logger.info("已成功打招呼 %d 人，达到上限，结束本轮", greeted)
                        break

                except Exception as e:
                    if should_reraise_hitl(e):
                        raise
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
        if should_reraise_hitl(e):
            raise
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
    """点击 × 关闭候选人详情。优先匹配详情区/弹窗内的关闭按钮，避免误点右上角头像下拉。"""
    # 优先在详情区/弹窗内查找，排除 header 区域
    scoped_selectors = [
        "[class*='geek-detail'] i.icon-close",
        "[class*='detail-drawer'] i.icon-close",
        "[class*='chat-content'] i.icon-close",
        "[class*='boss-dialog'] i.icon-close",
        "[class*='drawer'] i.icon-close",
        "[class*='dialog'] [class*='close']",
    ]
    for f in [page] + list(page.frames):
        try:
            for sel in scoped_selectors:
                b = f.locator(sel).first
                if b.count() > 0:
                    b.click()
                    page.wait_for_timeout(500)
                    return
        except Exception:
            pass
    # 兜底：全局查找（可能误点，但总比不关闭好）
    for f in [page] + list(page.frames):
        try:
            b = f.locator("i.icon-close").first
            if b.count() > 0:
                b.click()
                page.wait_for_timeout(500)
                return
        except Exception:
            pass
