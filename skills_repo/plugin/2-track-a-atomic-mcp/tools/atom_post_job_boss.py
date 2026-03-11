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


def _normalize_salary(jd: dict) -> dict:
    """从 jd 中解析 salary_min/salary_max，支持 salary_range、字符串等格式"""
    sal_min = jd.get("salary_min")
    sal_max = jd.get("salary_max")
    if sal_min is None or sal_max is None:
        sal_range = jd.get("salary_range", "")
        if sal_range:
            m = re.search(r"(\d+)[^\d]*(\d+)?", str(sal_range))
            if m:
                sal_min = int(m.group(1))
                sal_max = int(m.group(2)) if m.group(2) else sal_min
    if sal_min is not None:
        jd["salary_min"] = int(sal_min) if not isinstance(sal_min, int) else sal_min
    if sal_max is not None:
        jd["salary_max"] = int(sal_max) if not isinstance(sal_max, int) else sal_max
    return jd


def load_jd_config(config_path: str = "") -> dict:
    """
    加载 JD 配置。优先 config_path，其次 data/jd_to_publish.json，最后 recruitment_status。
    """
    path = Path(config_path) if config_path else JD_CONFIG_PATH
    if path.exists():
        try:
            jd = json.loads(path.read_text(encoding="utf-8"))
            return _normalize_salary(jd)
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

            # 9. 薪资（Boss 仅支持下拉选择；必须先选最低月薪，最高月薪字段才会出现）
            def _match_salary_opt(value):
                """从下拉列表中点击匹配的薪资选项。Boss 选项格式：1k、2k、…、10k、15k（需滚动才能看到高位选项）"""
                try:
                    v = int(value)
                except (TypeError, ValueError):
                    return False
                candidates = [f"{v}k", f"{v}K", str(v)] + (
                    [f"{v}-{v+5}K", f"{v}-{v+5}k", f"{v}K以下", f"{v}k以下"] if v <= 30 else []
                )
                fallback = [f"{v+1}k", f"{v-1}k"] if 0 < v < 50 else []
                for c in candidates + fallback:
                    for scope in [page, form_frame]:
                        for opt_sel in [
                            "li.ui-select-item", "li[class*='option']", "div[class*='option']",
                            "[role='option']", "li", "div[class*='item']",
                        ]:
                            try:
                                loc = scope.locator(opt_sel).filter(has_text=c)
                                if loc.count() > 0:
                                    el = loc.first
                                    el.scroll_into_view_if_needed(timeout=2000)
                                    page.wait_for_timeout(150)
                                    el.click(timeout=3000)
                                    return True
                            except Exception:
                                pass
                        try:
                            loc = scope.get_by_text(c, exact=True)
                            if loc.count() > 0:
                                el = loc.first
                                el.scroll_into_view_if_needed(timeout=2000)
                                el.click(timeout=3000)
                                return True
                        except Exception:
                            pass
                    # 兜底：JS 查找并点击文本完全匹配的下拉项（Boss 可能用自定义 DOM 结构）
                    try:
                        clicked = scope.evaluate("""
                            (text) => {
                                const sel = 'li, [role=option], [class*="option"], [class*="item"]';
                                for (const el of document.querySelectorAll(sel)) {
                                    const t = (el.textContent || '').trim();
                                    if (t === text) { el.click(); return true; }
                                }
                                return false;
                            }
                        """, c)
                        if clicked:
                            page.wait_for_timeout(200)
                            return True
                    except Exception:
                        pass
                return False

            sal_min = jd.get("salary_min")
            sal_max = jd.get("salary_max")
            if sal_min is not None or sal_max is not None:
                target_min = sal_min if sal_min is not None else sal_max
                target_max = sal_max if sal_max is not None else sal_min

                # 策略 1：定位「最低月薪」下拉/输入，必须先填此项，最高月薪才会出现
                min_sal_btn = None
                for label in ("最低月薪", "最低薪资", "最低"):
                    try:
                        loc = form_frame.locator("div.ui-select-inner, span.ui-select-placeholder").filter(has_text=label).first
                        if loc.count() > 0:
                            min_sal_btn = loc
                            break
                    except Exception:
                        pass
                if min_sal_btn is None:
                    for sel in ["div.ui-select-inner", "[class*='salary']", "[class*='Salary']"]:
                        try:
                            for i in range(min(8, form_frame.locator(sel).count())):
                                el = form_frame.locator(sel).nth(i)
                                txt = (el.inner_text() or "") + (el.get_attribute("placeholder") or "")
                                if "最低" in txt or ("月薪" in txt and "最高" not in txt):
                                    min_sal_btn = el
                                    break
                            if min_sal_btn is not None:
                                break
                        except Exception:
                            pass

                if min_sal_btn and min_sal_btn.count() > 0:
                    min_sal_btn.first.click()
                    page.wait_for_timeout(1000)  # 等待下拉展开
                    # 若目标薪资较大（如 10k+），下拉可能需滚动才能看到；先尝试滚动下拉面板
                    if target_min and int(target_min) > 5:
                        try:
                            for scope in [page, form_frame]:
                                panel = scope.locator("[class*='dropdown'] [class*='list'], [class*='select'] [class*='list'], .ui-select-dropdown, [class*='option-list']").first
                                if panel.count() > 0:
                                    # 按目标值估算滚动位置（每项约 32px）
                                    panel.evaluate("(el, v) => { el.scrollTop = Math.min(v * 32, el.scrollHeight); }", int(target_min))
                                    page.wait_for_timeout(400)
                                    break
                        except Exception:
                            pass
                    if not _match_salary_opt(target_min):
                        logger.warning("最低薪资未匹配到 %s，请检查 jd_to_publish.json 中的 salary_min", target_min)
                    page.wait_for_timeout(1200)  # 选完后等待最高月薪字段渲染

                # 策略 2：最高月薪（仅当最低已选后才会出现；支持 min=max 如 10-10k，便于后续修改）
                if target_max is not None and min_sal_btn and min_sal_btn.count() > 0:
                    max_sal_btn = None
                    # 策略 A：用「1k=1千元」锚定薪资区（仅此区有此文案），取该区内第 2 个 数字+k 的 select 即最高月薪
                    try:
                        for anchor_text in ["1k=1千元", "10k=1万", "1千元"]:
                            salary_anchor = form_frame.get_by_text(anchor_text, exact=False).first
                            if salary_anchor.count() > 0:
                                row = salary_anchor.locator("xpath=ancestor::*[.//div[contains(@class,'ui-select-inner')]][1]")
                                if row.count() > 0:
                                    selects = row.locator("div.ui-select-inner, span.ui-select-placeholder").filter(has_text=re.compile(r"\d+k", re.I))
                                    if selects.count() >= 2:
                                        max_sal_btn = selects.nth(1)
                                        break
                            if max_sal_btn is not None:
                                break
                    except Exception:
                        pass
                    # 策略 B：从最低月薪向上找 ui-select 容器，取其下一个兄弟
                    if max_sal_btn is None:
                        for level in ["ancestor::div[contains(@class,'ui-select')][1]", "ancestor::*[contains(@class,'select')][1]", "..", "../.."]:
                            try:
                                min_parent = min_sal_btn.locator(f"xpath={level}")
                                if min_parent.count() > 0:
                                    for i in range(1, 5):
                                        next_el = min_parent.locator(f"xpath=following-sibling::*[{i}]")
                                        if next_el.count() == 0:
                                            break
                                        inner = next_el.locator("div.ui-select-inner, span.ui-select-placeholder").first
                                        if inner.count() > 0:
                                            txt = (inner.inner_text() or "").strip()
                                            if "12个月" in txt:
                                                continue
                                            if "本科" not in txt and "学历" not in txt:
                                                max_sal_btn = inner
                                                break
                                    if max_sal_btn is not None:
                                        break
                            except Exception:
                                pass
                    # 策略 C：JS 从最低元素遍历找下一个薪资 select
                    if max_sal_btn is None:
                        try:
                            found = form_frame.evaluate("""
                                (minVal) => {
                                    const inners = document.querySelectorAll('div.ui-select-inner, span.ui-select-placeholder');
                                    let foundMin = false;
                                    for (const el of inners) {
                                        const t = (el.textContent || '').trim();
                                        if (t === String(minVal) + 'k' || t === String(minVal) + 'K') { foundMin = true; continue; }
                                        if (foundMin && /\\d+k/i.test(t) && !t.includes('12个月') && !t.includes('本科')) {
                                            el.click(); return true;
                                        }
                                    }
                                    return false;
                                }
                            """, target_min)
                            if found:
                                max_sal_btn = "js_clicked"
                        except Exception:
                            pass
                    if max_sal_btn is None:
                        # 策略 D：文本含「最高月薪」或「最高」且排除学历
                        for label in ("最高月薪", "最高薪资"):
                            try:
                                loc = form_frame.locator("div.ui-select-inner, span.ui-select-placeholder").filter(has_text=label).first
                                if loc.count() > 0:
                                    cur = (loc.inner_text() or "").strip()
                                    if "本科" not in cur and "学历" not in cur and "不限" not in cur:
                                        max_sal_btn = loc
                                        break
                            except Exception:
                                pass
                    if max_sal_btn == "js_clicked":
                        page.wait_for_timeout(800)
                    elif max_sal_btn and hasattr(max_sal_btn, "count") and max_sal_btn.count() > 0:
                        max_sal_btn.first.click()
                        page.wait_for_timeout(800)
                    if max_sal_btn:
                        if target_max and int(target_max) > 5:
                            try:
                                for scope in [page, form_frame]:
                                    panel = scope.locator("[class*='dropdown'] [class*='list'], .ui-select-dropdown, [class*='option-list']").first
                                    if panel.count() > 0:
                                        panel.evaluate("(el, v) => { el.scrollTop = Math.min(v * 32, el.scrollHeight); }", int(target_max))
                                        page.wait_for_timeout(400)
                                        break
                            except Exception:
                                pass
                        if not _match_salary_opt(target_max):
                            logger.warning("最高薪资未匹配到 %s，请检查 jd_to_publish.json 中的 salary_max", target_max)

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
