"""
原子 Tool: atom_post_job_boss
通过 Playwright CDP 在 Boss 直聘网站自动填写并发布职位。

【配置读取】必须从 data/{岗位名}/jd.json 读取 JD 配置，禁止使用 data/jd_to_publish.json。
jd_config_path 指向 data/{岗位名}/jd.json；若无则从 job_name 推导。推荐牛人、抓简历等后续流程亦从此文件读取。
"""
import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

from .boss_utils import canonicalize_boss_job_select, strip_leading_recruitment_verbs_for_job_chat
from .hr_data_paths import (
    clear_jd_boss_post_published_flag,
    get_job_jd_path,
    ensure_job_dirs,
    init_job_jd_from_template,
    jd_boss_post_marked_published,
    mark_jd_boss_post_published,
    sanitize_job_folder,
)


def load_jd_config(config_path: str = "", job_name: str = "") -> dict:
    """
    加载 JD 配置。仅从 data/{职位}/jd.json 读取，禁止使用 data/jd_to_publish.json。
    优先 config_path（需为 data/{职位}/jd.json），否则从 job_name 推导。
    """
    path = None
    if config_path and config_path.strip():
        p = Path(config_path.strip())
        if "jd_to_publish" in str(p).replace("\\", "/"):
            logger.warning("load_jd_config 禁止使用 jd_to_publish.json，请使用 data/{岗位名}/jd.json")
            return {}
        if p.exists():
            path = p
    if not path and job_name and job_name.strip():
        jd_path = get_job_jd_path(job_name.strip())
        if jd_path.exists():
            path = jd_path
    if path and path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"load_jd_config failed {path}: {e}")
    return {}


def _jd_select_field_looks_complete_boss_line(sel_canon: str) -> bool:
    """jd.json 里显式 jd_select 是否像完整 Boss 顶栏/下拉行（含城市段与 K 薪）。"""
    s = (sel_canon or "").strip()
    if " _ " not in s:
        return False
    if not re.search(r"\d", s):
        return False
    if not re.search(r"[KkＫ]", s):
        return False
    return True


def get_jd_select(jd: dict) -> str:
    """
    获取 Boss「全部职位」下拉框精确匹配用文本。
    规范格式：``岗位名称 _ 工作地点 最低-最高K``（与 Boss 在招列表一致，如 ``Python 工程师 _ 杭州 15-25K``）。

    **若 jd.json 已写入完整 ``jd_select``**（飞书/MCP 合并的选岗行），**优先于** ``salary_min``/``salary_max`` 拼行：
    模板里薪资常未随用户改口而更新，否则会误选成 10-15K 等在招岗而忽略 15-25K 目录。
    仅当 ``jd_select`` 不完整（缺 `` _ `` 或薪资）时，再用结构化字段拼行，最后回退标题。
    """
    title = (jd.get("job_title") or "").strip()
    city = (jd.get("job_location") or "杭州").strip()
    sal_min = jd.get("salary_min")
    sal_max = jd.get("salary_max")

    sel = strip_leading_recruitment_verbs_for_job_chat((jd.get("jd_select") or "").strip())
    sel_canon = canonicalize_boss_job_select(sel) if sel else ""
    if sel_canon and _jd_select_field_looks_complete_boss_line(sel_canon):
        return sel_canon

    if title and sal_min is not None and sal_max is not None:
        return canonicalize_boss_job_select(f"{title} _ {city} {int(sal_min)}-{int(sal_max)}K")
    if title and sal_min is not None:
        return canonicalize_boss_job_select(f"{title} _ {city} {int(sal_min)}K")
    if sel_canon:
        return sel_canon
    return canonicalize_boss_job_select(title or "")


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
    jd_config: dict | str | None = None,
    os_context: dict | None = None,
    force_republish: bool = False,
) -> dict:
    """
    在 Boss 直聘自动填写并发布职位。
    若传 jd_config，先写入 data/{职位}/jd.json 再发布。

    jd.json 中 ``boss_post_published=true`` 时**默认拒绝再次发帖**（发职位与调度分离）；
    仅当 ``force_republish=true``（或 jd 内临时字段，见 MCP）时才会再次走 Boss RPA。

    os_context: 可选 Workflow/DAG 上下文；与 Harvest 工具对齐，发布流程内可按需扩展 STOP 探针。
    """
    _ = os_context  # 预留与 try_consume_stop_harvest 联动
    if jd_config:
        cfg = jd_config
        if isinstance(cfg, str):
            try:
                cfg = json.loads(cfg)
            except json.JSONDecodeError:
                return {"success": False, "posted": False, "error": "jd_config 不是有效 JSON"}
        if isinstance(cfg, dict) and (cfg.get("job_title") or cfg.get("jd_full")):
            job_title = (cfg.get("job_title") or "").strip()
            if job_title:
                try:
                    jd_path = init_job_jd_from_template(job_title, overrides=cfg)
                except ValueError as e:
                    return {"success": False, "posted": False, "error": str(e)}
                jd_config_path = str(jd_path)

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
        return {"success": False, "posted": False, "error": "JD 配置为空，请先填写 data/{职位}/jd.json"}

    _force = bool(force_republish)
    if isinstance(jd, dict) and jd.get("force_republish") is True:
        _force = True
    if jd_boss_post_marked_published(jd) and not _force:
        return {
            "success": True,
            "posted": False,
            "already_published": True,
            "skipped_repost": True,
            "error": "",
            "message": (
                "该岗位 jd.json 已标记 boss_post_published（Boss 侧已发过帖）。"
                "改薪资/JD 文案可只更新 jd.json；改打招呼/收网/透析请改调度字段并 hr_scheduler_send_confirm_prompt 或 add_automated_recruitment_task。"
                "若确需在 Boss 再发一条职位，请传 force_republish=true。"
            ),
        }
    jd_path_for_flag = Path(jd_config_path) if jd_config_path and Path(jd_config_path).exists() else None
    if _force and jd_path_for_flag:
        clear_jd_boss_post_published_flag(jd_path_for_flag)
        jd = load_jd_config(jd_config_path, job_name)

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
            # 兜底：无标签页时新建并导航到 Boss 职位管理页（Chrome 刚启动或用户关闭了所有标签）
            if not pages:
                try:
                    page = context.new_page()
                    page.goto("https://www.zhipin.com/web/chat/job/list?ka=menu-manager-job", wait_until="domcontentloaded", timeout=20000)
                    page.wait_for_timeout(2000)
                    pages = context.pages
                except Exception as e:
                    return {"success": False, "posted": False, "error": f"未找到页面，且新建标签失败: {e}"}
            if not pages:
                return {"success": False, "posted": False, "error": "未找到页面。请确保 Chrome 至少有一个 Boss 直聘标签页打开，或重新运行 launch_chrome_debug.ps1 后重试"}

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

            # 2. 定位职位列表 iframe（SPA 偶发慢加载 / iframe URL 与文档不一致）— 多轮扫描 + 刷新
            _iframe_attempts = max(2, min(8, int(os.environ.get("BOSS_POST_IFRAME_ATTEMPTS", "4"))))

            def _pick_job_frame():
                """优先含 job/list 的 frame 内 div.add-btn；否则任意 frame 含 add-btn。"""
                for f in page.frames:
                    try:
                        if "job/list" in (f.url or ""):
                            loc = f.locator("div.add-btn")
                            if loc.count() > 0:
                                return f
                    except Exception:
                        pass
                for f in page.frames:
                    try:
                        loc = f.locator("div.add-btn")
                        if loc.count() > 0:
                            return f
                    except Exception:
                        pass
                return None

            job_frame = None
            for attempt in range(1, _iframe_attempts + 1):
                page.wait_for_timeout(600 + 400 * attempt)
                job_frame = _pick_job_frame()
                if job_frame:
                    break
                logger.warning(
                    "atom_post_job_boss 未找到 add-btn（第 %d/%d 次），刷新职位管理页后重试",
                    attempt,
                    _iframe_attempts,
                )
                if attempt >= _iframe_attempts:
                    break
                try:
                    page.goto(
                        "https://www.zhipin.com/web/chat/job/list?ka=menu-manager-job",
                        wait_until="domcontentloaded",
                        timeout=25000,
                    )
                    page.wait_for_timeout(2000 + 500 * attempt)
                except Exception as ex:
                    logger.debug("刷新职位管理页: %s", ex)
                    try:
                        page.reload(wait_until="domcontentloaded", timeout=20000)
                        page.wait_for_timeout(2000)
                    except Exception:
                        pass

            if not job_frame:
                curr = (page.url or "")[:120]
                return {
                    "success": False,
                    "posted": False,
                    "error": f"未找到职位列表 iframe（已重试 {_iframe_attempts} 次）。当前页: {curr}",
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

            # 6. 职位类型：脚本多为图1「请选择职类」，图2「请选择职位类型」仅手动出现
            # 图2：选带推荐的列表项；图1：在弹窗内点「Java」「全栈工程师」等
            def _fill_job_category(page_or_frame, main_page, jd: dict):
                """填写职位类型。图2 选推荐项；图1 在弹窗内点具体职位名。"""
                for scope in [page_or_frame, main_page]:
                    job_cat_inp = scope.locator(
                        "input[name='jobCategory'][placeholder='选择职位类型'], "
                        "input.ipt[placeholder='选择职位类型'], "
                        "input[name='jobCategory']"
                    ).first
                    if job_cat_inp.count() > 0:
                        try:
                            # 脚本 click() 会弹出「请选择职类」图1，手动点击会弹出「请选择职位类型」图2。
                            # 用 page.mouse 在元素中心模拟真实鼠标点击，触发与手动相同的弹窗。
                            try:
                                job_cat_inp.wait_for(state="visible", timeout=2000)
                                box = job_cat_inp.bounding_box()
                                if box:
                                    cx = box["x"] + box["width"] / 2
                                    cy = box["y"] + box["height"] / 2
                                    main_page.mouse.click(cx, cy)
                                else:
                                    job_cat_inp.click(timeout=3000)
                            except Exception:
                                job_cat_inp.click(timeout=3000)
                            # 图4：增加等待，确保弹窗渲染
                            main_page.wait_for_timeout(2000)
                            clicked = False
                            clicked_scope = ""
                            # 调试证明：选项弹窗由 Vue Portal 渲染到 main_page。必须只点列表项，不能点底部「查看全部职位类型」链接（会 404）
                            opts_scope = main_page
                            # 列表项有 div.radio-box，「查看全部职位类型」无；用 :has(div.radio-box) 排除底部链接
                            rec_row = opts_scope.locator(
                                "div.job-recommend-content_item:has(div.radio-box):has(span.job-recommend-content_title-icon)"
                            ).first
                            fallback = opts_scope.locator(
                                "div.job-recommend-content_item:has(div.radio-box)"
                            ).filter(has=opts_scope.get_by_text("推荐", exact=False)).first
                            for loc, lbl in [(rec_row, "div_item_radio"), (fallback, "div_item_filter")]:
                                if loc.count() > 0:
                                    try:
                                        loc.wait_for(state="visible", timeout=3000)
                                        loc.click(timeout=3000)
                                        clicked = True
                                        clicked_scope = f"main_page:{lbl}"
                                        break
                                    except Exception:
                                        pass
                            if not clicked:
                                # 兜底：通过 推荐 span 找父行
                                try:
                                    icon_span = opts_scope.locator("span.job-recommend-content_title-icon").first
                                    if icon_span.count() > 0:
                                        parent_row = icon_span.locator("..").locator("..")
                                        if parent_row.locator("div.radio-box").count() > 0:
                                            parent_row.click(timeout=3000)
                                            clicked = True
                                except Exception:
                                    pass
                            # 图1「请选择职类」：先点左侧「互联网/AI」确保右侧为技术岗，再点 Java/全栈工程师等
                            if not clicked:
                                try:
                                    left_cat = opts_scope.get_by_text("互联网/AI", exact=True).first
                                    if left_cat.count() > 0 and left_cat.is_visible():
                                        left_cat.click(timeout=2000)
                                        main_page.wait_for_timeout(600)
                                except Exception:
                                    pass
                                job_title = (jd.get("job_title") or "").strip()
                                path = jd.get("job_category_path") or []
                                candidates = []
                                if "Java" in job_title or "java" in job_title:
                                    candidates = ["Java", "Java开发工程师"]
                                elif "Go" in job_title or "Golang" in job_title or "golang" in job_title:
                                    candidates = ["Golang", "Go"]
                                elif "Python" in job_title or "python" in job_title:
                                    candidates = ["Python"]
                                elif "Node" in job_title or "node" in job_title:
                                    candidates = ["Node.js"]
                                elif path:
                                    candidates = [p for p in path if p and "互联网" not in p and "AI" not in p][:3]
                                if not candidates:
                                    candidates = ["全栈工程师", "Java", "后端开发"]
                                for name in candidates:
                                    try:
                                        el = opts_scope.get_by_text(name, exact=False).first
                                        if el.count() > 0 and el.is_visible():
                                            txt = el.inner_text() or ""
                                            if "查看全部" not in txt and "职位类型" not in txt:
                                                el.click(timeout=2000)
                                                clicked = True
                                                break
                                    except Exception:
                                        pass
                            main_page.wait_for_timeout(1000)
                            if not clicked:
                                for s in [main_page, page_or_frame]:
                                    try:
                                        close_btn = s.locator("i.icon-close").first
                                        if close_btn.count() > 0 and close_btn.is_visible():
                                            box = close_btn.bounding_box()
                                            if box:
                                                cx = box["x"] + box["width"] / 2
                                                cy = box["y"] + box["height"] / 2
                                                main_page.mouse.click(cx, cy)
                                            else:
                                                close_btn.click(timeout=2000)
                                            main_page.wait_for_timeout(500)
                                            logger.debug("已关闭「请选择职类」弹窗 (i.icon-close)")
                                            break
                                    except Exception:
                                        pass
                            return True
                        except Exception as ex:
                            logger.warning("职位类型选择异常（将继续后续步骤）: %s", ex)
                return False

            ok = _fill_job_category(form_frame, page, jd)
            # 图4：检查返回值，失败时记录明确错误
            if not ok:
                logger.warning("职位类型未成功选择，发布时可能被 Boss 校验拦截（请选择职位类型）")

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

                if jd_config_path and Path(jd_config_path).exists():
                    mark_jd_boss_post_published(Path(jd_config_path))
                return {"success": True, "posted": True, "error": ""}
            return {"success": False, "posted": False, "error": "未找到发布按钮"}
    except Exception as e:
        logger.error(f"atom_post_job_boss failed: {e}", exc_info=True)
        err = str(e)
        if "connect" in err.lower():
            err = f"{err}\n提示：请用 scripts\\launch_chrome_debug.ps1 启动 Chrome"
        return {"success": False, "posted": False, "error": err}
