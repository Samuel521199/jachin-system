"""
原子 Tool: atom_post_job_boss
通过 Playwright CDP 在 Boss 直聘网站自动填写并发布职位。
读取 data/jd_to_publish.json 或 recruitment_status 作为 JD 配置。
"""
import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent
JD_CONFIG_PATH = _ROOT / "data" / "jd_to_publish.json"


def load_jd_config(config_path: str = "") -> dict:
    """
    加载 JD 配置。优先 config_path，其次 data/jd_to_publish.json，最后 recruitment_status。
    """
    path = Path(config_path) if config_path else JD_CONFIG_PATH
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"load_jd_config failed {path}: {e}")

    try:
        from .recruitment_status import load_status
        import re
        s = load_status()
        sal = s.get("salary_range", "")
        sal_min = sal_max = None
        if sal:
            m = re.search(r"(\d+)[^\d]*(\d+)?", str(sal))
            if m:
                sal_min = int(m.group(1))
                sal_max = int(m.group(2)) if m.group(2) else sal_min
        return {
            "recruitment_type": "社招全职",
            "job_title": s.get("job_title", ""),
            "jd_full": s.get("jd_full", ""),
            "job_category_path": [],
            "experience": "",
            "education": "",
            "salary_min": sal_min,
            "salary_max": sal_max,
            "job_keywords": [],
        }
    except Exception:
        pass
    return {}


def _close_review_passed_modal(page) -> None:
    """关闭「审核通过」弹窗（曝光刷新卡/支付弹窗）。优先弹窗内关闭按钮，避免误点右上角头像。"""
    close_selectors = [
        ".boss-dialog i[class*='close']",
        "[class*='dialog'] [class*='close']",
        "[class*='boss-dialog'] [class*='icon-close']",
        "button[aria-label='关闭']",
        "i.icon-close",
        "[class*='icon-close']",
        "span[class*='close']",
    ]
    for f in [page] + list(page.frames):
        try:
            for sel in close_selectors:
                loc = f.locator(sel).first
                if loc.count() > 0:
                    loc.click(timeout=2000)
                    page.wait_for_timeout(500)
                    logger.debug("已关闭审核通过弹窗")
                    return
        except Exception:
            pass


def atom_post_job_boss(
    cdp_url: str = "http://127.0.0.1:9222",
    jd_config_path: str = "",
) -> dict:
    """
    在 Boss 直聘自动填写并发布职位。
    前置：Chrome 以 --remote-debugging-port 启动，已登录 Boss。
    读取 data/jd_to_publish.json 或 jd_config_path 作为 JD 配置。
    """
    jd = load_jd_config(jd_config_path)
    if not jd.get("job_title") and not jd.get("jd_full"):
        return {"success": False, "posted": False, "error": "JD 配置为空，请先填写 data/jd_to_publish.json"}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"success": False, "posted": False, "error": "playwright 未安装"}

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(cdp_url, timeout=5000)
            contexts = browser.contexts
            if not contexts:
                return {"success": False, "posted": False, "error": "未找到浏览器上下文"}
            context = contexts[0]
            pages = context.pages
            if not pages:
                return {"success": False, "posted": False, "error": "未找到页面"}

            # 优先选用已在职位管理页的标签页
            page = None
            for p in pages:
                try:
                    url = p.url or ""
                    if "job/list" in url and ("zhipin.com" in url or "zhpin.com" in url):
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

            # 1. 进入职位管理页
            current_url = page.url or ""
            if "job/list" not in current_url:
                job_mgmt = page.locator('a[href*="job/list"], a[ka="menu-manager-job"]').first
                if job_mgmt.count() > 0:
                    job_mgmt.click()
                    page.wait_for_timeout(2500)
                if "job/list" not in (page.url or ""):
                    page.goto("https://www.zhipin.com/web/chat/job/list?ka=menu-manager-job", wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(3000)

            # 2. 定位职位列表 iframe（job/list-new），按钮在此 frame 内
            page.wait_for_timeout(1200)
            job_frame = None
            for f in page.frames:
                if "job/list" in (f.url or ""):
                    loc = f.locator("div.add-btn")
                    if loc.count() > 0:
                        job_frame = f
                        break
            if not job_frame:
                curr = (page.url or "")[:80]
                return {
                    "success": False,
                    "posted": False,
                    "error": f"未找到职位列表 iframe。当前页: {curr}",
                }

            add_btn = job_frame.locator("div.add-btn").first
            add_btn.click(timeout=10000)
            page.wait_for_timeout(1000)

            # 2.1 若弹出「检测到您有未发布的职位」温馨提示，点击「重新发布」
            for f in page.frames:
                try:
                    repub = f.locator("span.boss-dialog__button.button-outline:has-text('重新发布'), span:has-text('重新发布')").first
                    if repub.count() > 0:
                        repub.click(timeout=5000)
                        page.wait_for_timeout(1000)
                        break
                except Exception:
                    pass

            # 2.2 点击发布后 iframe 会切换，需重新定位包含发布表单的 frame（原 job_frame 可能已 detached）
            page.wait_for_timeout(1000)
            form_frame = None
            for _ in range(5):
                for f in page.frames:
                    try:
                        if f.locator("p.job-type-item").count() > 0 or f.locator("input.job-name-input").count() > 0:
                            form_frame = f
                            break
                    except Exception:
                        pass
                if form_frame:
                    break
                page.wait_for_timeout(1000)
            if not form_frame:
                form_frame = page

            # 3. 在 form_frame 内填写
            rec_type = jd.get("recruitment_type", "社招全职")
            type_btn = form_frame.locator(f"p.job-type-item:has-text('{rec_type}')").first
            if type_btn.count() > 0:
                type_btn.click()
                page.wait_for_timeout(500)

            # 4. 职位名称
            job_title = jd.get("job_title", "")
            if job_title:
                name_inp = form_frame.locator("input.job-name-input, input[name='jobName']").first
                if name_inp.count() > 0:
                    name_inp.fill(job_title)
                    page.wait_for_timeout(800)
                    suggest = form_frame.locator("ul li, [class*='suggest'] li, [class*='dropdown'] li").filter(has_text=job_title[:4]).first
                    if suggest.count() > 0:
                        suggest.click()
                        page.wait_for_timeout(300)

            # 5. 职位描述
            jd_full = jd.get("jd_full", "")
            if jd_full:
                desc = form_frame.locator("textarea[placeholder*='请勿填写']").first
                if desc.count() > 0:
                    desc.fill(jd_full)
                    page.wait_for_timeout(500)

            # 6. 职位类型：系统会根据职位名称自动填充，跳过手动选择

            def _click_dropdown_option(page_or_frame, opt_locator, label: str):
                """点击下拉选项，选项可能被 portal 到 body，优先用 page 查找；不可见时用 JS 点击"""
                for scope in [page, page_or_frame]:
                    loc = scope.locator(opt_locator).filter(has_text=label).first
                    if loc.count() > 0:
                        try:
                            loc.click(timeout=3000)
                            return True
                        except Exception:
                            try:
                                loc.evaluate("el => el.click()")  # JS 点击绕过可见性
                                return True
                            except Exception:
                                pass
                return False

            # 7. 经验
            exp = jd.get("experience", "")
            if exp:
                exp_inp = form_frame.locator("div.ui-select-inner").filter(has_text="选择经验").first
                if exp_inp.count() > 0:
                    exp_inp.click()
                    page.wait_for_timeout(500)
                    _click_dropdown_option(form_frame, "li.ui-select-item, li[class*='option']", exp)
                    page.wait_for_timeout(500)

            # 8. 学历（若已选则跳过；选项可能在 portal 中，用 page 查找 + JS 点击兜底）
            edu = jd.get("education", "")
            if edu:
                edu_inp = form_frame.locator("div.ui-select-inner").filter(has_text="选择学历").first
                if edu_inp.count() == 0:
                    for i in range(form_frame.locator("div.ui-select-inner").count()):
                        el = form_frame.locator("div.ui-select-inner").nth(i)
                        if "学历" in (el.inner_text() or ""):
                            edu_inp = el
                            break
                if edu_inp.count() > 0:
                    cur_txt = edu_inp.first.inner_text() or ""
                    if edu in cur_txt:
                        pass  # 已选目标学历，跳过
                    else:
                        edu_inp.click()
                        page.wait_for_timeout(600)
                        if not _click_dropdown_option(form_frame, "li.ui-select-item, li[class*='option']", edu):
                            pass  # 静默失败，继续后续步骤

            page.wait_for_timeout(300)

            # 9. 薪资（Boss 选项格式可能为 15K/20K/25K 或 15-16K；需先点击下拉再选）
            def _match_salary_opt(value):
                v = int(value)
                # Boss 选项格式：20K、25K、20-25K、15-16K 等
                candidates = [str(v), f"{v}k", f"{v}K", f"{v}-{v+5}K", f"{v}-{v+5}k"]
                fallback = [str(v + 1), f"{v + 1}k", str(v + 2), f"{v + 2}k", str(v - 1), f"{v - 1}k"] if v < 50 else []
                for scope in [page, form_frame]:
                    for c in candidates + fallback:
                        loc = scope.locator("li.ui-select-item, li[class*='option'], div[class*='option']").filter(has_text=c)
                        if loc.count() > 0:
                            try:
                                loc.first.click(timeout=3000)
                                return True
                            except Exception:
                                try:
                                    loc.first.evaluate("el => el.click()")
                                    return True
                                except Exception:
                                    pass
                return False

            sal_min = jd.get("salary_min")
            sal_max = jd.get("salary_max")
            if sal_min is not None or sal_max is not None:
                # 最低薪资：Boss 可能用「最低月薪」或「最低薪资」；用 filter(has_text) 更稳
                min_sal_btn = None
                for label in ("最低月薪", "最低薪资", "最低"):
                    try:
                        loc = form_frame.locator("div.ui-select-inner").filter(has_text=label).first
                        if loc.count() > 0:
                            min_sal_btn = loc
                            break
                    except Exception:
                        pass
                if min_sal_btn is None:
                    # 兜底：遍历所有 ui-select-inner，找含「薪资」或「薪」的
                    for i in range(form_frame.locator("div.ui-select-inner").count()):
                        el = form_frame.locator("div.ui-select-inner").nth(i)
                        txt = (el.inner_text() or "") + (el.get_attribute("placeholder") or "")
                        if "最低" in txt or ("薪资" in txt and "最高" not in txt):
                            min_sal_btn = el
                            break
                if min_sal_btn and min_sal_btn.count() > 0:
                    min_sal_btn.click()
                    page.wait_for_timeout(800)
                    target_min = sal_min if sal_min is not None else sal_max
                    if not _match_salary_opt(target_min):
                        logger.warning("最低薪资选项未匹配到 %s，尝试就近档位", target_min)
                    # Boss 需先选最低薪资，最高薪资字段才会出现；等待下拉关闭 + 第二字段渲染
                    page.wait_for_timeout(1200)

                # 最高月薪（仅当最低已选后才会出现，需等待）
                if sal_max is not None and sal_min != sal_max:
                    # 轮询等待最高月薪字段出现（Boss 动态渲染）
                    max_sal_btn = None
                    for _ in range(5):
                        page.wait_for_timeout(600)
                        try:
                            loc = form_frame.locator("div.ui-select-inner").filter(has_text="最高").first
                            if loc.count() > 0:
                                max_sal_btn = loc
                                break
                        except Exception:
                            pass
                    if max_sal_btn is None:
                        max_sal_btn = form_frame.locator("span.ui-select-placeholder").filter(has_text="最高").locator("..").locator("..").first
                    if max_sal_btn.count() > 0:
                        max_sal_btn.click()
                        page.wait_for_timeout(600)
                        _match_salary_opt(sal_max)

            # 10. 职位关键词：跳过不填

            # 11. 点击发布（表单可能在 form_frame 或弹窗，两处都试）
            publish_btn = form_frame.locator("button[type='submit'].btn-v2.btn-sure-v2, button:has-text('发布')").first
            if publish_btn.count() > 0:
                publish_btn.click()
                page.wait_for_timeout(3000)

                # 12. 审核通过后弹窗（曝光刷新卡等）：约 10s 后自动关闭
                page.wait_for_timeout(10000)  # 等待约 10 秒
                _close_review_passed_modal(page)

                return {"success": True, "posted": True, "error": ""}
            return {"success": False, "posted": False, "error": "未找到发布按钮"}
    except Exception as e:
        logger.error(f"atom_post_job_boss failed: {e}", exc_info=True)
        err = str(e)
        if "connect" in err.lower():
            err = f"{err}\n提示：请用 scripts\\launch_chrome_debug.ps1 启动 Chrome"
        return {"success": False, "posted": False, "error": err}
