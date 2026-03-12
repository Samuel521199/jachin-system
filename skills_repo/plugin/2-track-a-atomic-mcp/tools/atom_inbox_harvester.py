"""
原子 Tool: atom_inbox_harvester
点击「全部职位」/职位下拉 → 选择对应职位（从 jd.json 的 job_text）→ 遍历左侧所有候选人会话 →
若有「点击预览附件简历」按钮则执行 PDF 下载，否则跳过继续下一个。

反爬/拟人化：stealth 隐身、随机等待、深呼吸机制。
"""
import logging
import random
import re
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .boss_utils import navigate_to_chat_page, select_job, select_all_positions, _get_current_job_label
from .local_archiver import local_archiver
from .human_utils import human_wait

logger = logging.getLogger(__name__)

BOSS_CHAT_ITEM_SELECTORS = [
    "div.geek-item",
    ".geek-item",
    "[class*='geek-item']",
]

ZHIPIN_BASE = "https://www.zhipin.com"

# 下载按钮选择器（含 icon-attacthment 拼写变体，Boss 可能已修正为 attachment）
BOSS_DOWNLOAD_SELECTORS = [
    'use[xlink\\:href="#icon-attacthment-download"]',
    'use[href="#icon-attacthment-download"]',
    'use[xlink\\:href="#icon-attachment-download"]',
    'use[href="#icon-attachment-download"]',
    'svg use[xlink\\:href="#icon-attacthment-download"]',
    'svg use[href="#icon-attacthment-download"]',
    'svg use[xlink\\:href="#icon-attachment-download"]',
    'svg use[href="#icon-attachment-download"]',
    'svg.boss-svg.svg-icon use[xlink\\:href="#icon-attacthment-download"]',
    'svg.boss-svg.svg-icon use[href="#icon-attachment-download"]',
    '[class*="download"] svg',
    '[class*="icon-download"]',
    '#download',
    'button#download',
    '[id="download"]',
    'button:has-text("保存简历")',
    'a:has-text("保存简历")',
    'span:has-text("保存简历")',
    'button:has-text("下载")',
    'a:has-text("下载")',
    'a[download]',
    '[title*="下载"]',
    '[title*="Download"]',
    '[aria-label*="下载"]',
    '[aria-label*="download"]',
]

# 优先匹配弹窗/预览区内的关闭按钮，避免误点右上角头像下拉的 icon-close
BOSS_PREVIEW_CLOSE_SELECTORS = [
    "[class*='boss-dialog'] i.icon-close",
    "[class*='boss-dialog'] [class*='icon-close']",
    "[class*='preview'] i.icon-close",
    "[class*='dialog'] [class*='close']",
    "[class*='drawer'] i.icon-close",
    "[class*='modal'] i.icon-close",
    "i.icon-close",
    ".icon-close",
    "[class*='icon-close']",
    "i[class*='close']",
]

# 仅匹配「点击预览附件简历」或「附件简历-」，避免误匹配职位卡片等含「点击预览」的 UI
BOSS_PREVIEW_BTN_SELECTORS = [
    "span.card-btn:has-text('点击预览附件简历')",
    "text=点击预览附件简历",
    "a:has-text('点击预览附件简历')",
]


def _close_preview_dialog(page, pages) -> bool:
    """点击预览弹窗的 × 关闭按钮（i.icon-close），返回列表页以便继续遍历。"""
    for p in pages:
        for sel in BOSS_PREVIEW_CLOSE_SELECTORS:
            try:
                loc = p.locator(sel).first
                if loc.count() > 0:
                    loc.scroll_into_view_if_needed()
                    human_wait(p, 0.15, 0.5)
                    loc.click()
                    human_wait(p, 0.4, 0.9)
                    logger.debug("已点击关闭按钮")
                    return True
            except Exception:
                pass
        for frame in p.frames:
            for sel in BOSS_PREVIEW_CLOSE_SELECTORS:
                try:
                    loc = frame.locator(sel).first
                    if loc.count() > 0:
                        loc.scroll_into_view_if_needed()
                        human_wait(p, 0.15, 0.5)
                        loc.click()
                        human_wait(p, 0.4, 0.9)
                        logger.debug("已点击关闭按钮(iframe)")
                        return True
                except Exception:
                    pass
    try:
        hit = page.get_by_role("button", name="关闭").first
        if hit.count() > 0:
            hit.click()
            human_wait(page, 0.4, 0.9)
            return True
    except Exception:
        pass
    return False


def has_preview_attachment_btn(page) -> bool:
    """当前页面是否存在「点击预览附件简历」按钮（对方已发简历时会显示）"""
    for sel in BOSS_PREVIEW_BTN_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                return True
        except Exception:
            pass
    try:
        if page.get_by_text("点击预览附件简历", exact=False).count() > 0:
            return True
    except Exception:
        pass
    try:
        if page.get_by_text("附件简历-", exact=False).count() > 0:
            return True
    except Exception:
        pass
    return False


def _extract_pdf_url_from_viewer(pages) -> str | None:
    """从 PDF.js viewer iframe 的 file= 参数或 embed/object 提取 PDF 真实下载 URL。"""
    for page in pages:
        try:
            for iframe_sel in [
                'iframe[src*="viewer.html"][src*="file="]',
                'iframe[src*="file="]',
                'iframe[src*="preview4boss"]',
                'iframe[src*="pdf"]',
            ]:
                iframes = page.locator(iframe_sel)
                cnt = iframes.count()
                for i in range(cnt):
                    src = iframes.nth(i).get_attribute("src")
                    if not src:
                        continue
                    if "file=" in src:
                        parsed = urlparse(src)
                        params = parse_qs(parsed.query)
                        file_param = params.get("file", [None])[0]
                        if not file_param:
                            continue
                        path = unquote(file_param)
                        if "preview4boss" in path or "download" in path or "attachment" in path or ".pdf" in path:
                            if path.startswith("http://") or path.startswith("https://"):
                                url = path
                            else:
                                url = ZHIPIN_BASE + path if path.startswith("/") else ZHIPIN_BASE + "/" + path
                            logger.info("从 viewer iframe 提取 PDF URL: %s...", url[:80])
                            return url
        except Exception as e:
            logger.debug("提取 viewer URL 时出错: %s", e)
    return None


def _is_valid_pdf(body: bytes) -> bool:
    """判断是否为有效 PDF（魔数 %PDF）。"""
    if not body or len(body) < 100:
        return False
    return body[:4] == b"%PDF" or b"%PDF" in body[:1024]


def _fetch_pdf_by_url(request_api, url: str, timeout: int = 15000) -> bytes | None:
    """使用 Playwright request（带 cookie）直接 GET 下载 PDF。"""
    try:
        resp = request_api.get(url, timeout=timeout)
        if resp.ok:
            body = resp.body()
            if _is_valid_pdf(body):
                return body
    except Exception as e:
        logger.warning("URL 直接下载失败 %s: %s", url[:60], e)
    return None


def _do_download(
    context, pages, page_for_request, pre_captured_urls: list[str] | None = None
) -> tuple[bytes | None, str]:
    """
    执行 PDF 下载，多级降级策略：
    1. 从 viewer iframe 提取 URL → 直接 GET
    2. 用预捕获或拦截到的 URL 直接 GET（pre_captured_urls 来自点击预览前的 response 监听）
    3. 点击下载图标 → expect_download
    Returns:
        (content, error_msg)
    """
    content = None
    request_api = page_for_request.request
    captured_urls = list(pre_captured_urls) if pre_captured_urls else []

    # 策略 1：viewer iframe 提取 URL
    pdf_url = _extract_pdf_url_from_viewer(pages)
    if pdf_url:
        content = _fetch_pdf_by_url(request_api, pdf_url)
        if content:
            logger.info("策略1 viewer URL：下载成功，%d 字节", len(content))
            return content, ""

    # 策略 2：优先用预捕获的 URL（点击预览时已监听）
    for url in captured_urls:
        content = _fetch_pdf_by_url(request_api, url)
        if content:
            logger.info("策略2 预捕获 URL：下载成功，%d 字节", len(content))
            return content, ""

    # 策略 3：补充 response 监听后点击下载（可能预览加载较慢）
    def on_response(response):
        try:
            url = (response.url or "").strip()
            if "zhipin.com" not in url and "zhpin.com" not in url:
                return
            ct = (response.headers.get("content-type") or "").lower()
            is_pdf = "pdf" in ct or url.endswith(".pdf") or "application/octet-stream" in ct
            if "preview4boss" in url or "download" in url or "attachment" in url or is_pdf:
                captured_urls.append(url)
        except Exception:
            pass

    for p in pages:
        try:
            p.on("response", on_response)
        except Exception:
            pass

    try:
        human_wait(page_for_request, 0.5, 1.2)

        # 策略 4：点击下载/保存简历（优先 dialog 内）
        for p in pages:
            for frame in p.frames:
                for sel in BOSS_DOWNLOAD_SELECTORS:
                    try:
                        loc = frame.locator(sel)
                        if loc.count() > 0:
                            with context.expect_download(timeout=15000) as download_info:
                                loc.first.click(force=True)
                            download = download_info.value
                            path = download.path()
                            if path and Path(path).exists():
                                content = Path(path).read_bytes()
                                download.delete()
                            if content and len(content) >= 100:
                                logger.info("策略4 点击下载：成功，%d 字节", len(content))
                                return content, ""
                    except Exception:
                        pass
        # 兜底：按文本查找「保存简历」
        for p in pages:
            for frame in p.frames:
                try:
                    btn = frame.get_by_text("保存简历", exact=False).first
                    if btn.count() > 0:
                        with context.expect_download(timeout=15000) as download_info:
                            btn.click(force=True)
                        download = download_info.value
                        path = download.path()
                        if path and Path(path).exists():
                            content = Path(path).read_bytes()
                            download.delete()
                        if content and len(content) >= 100:
                            logger.info("策略4 保存简历：成功，%d 字节", len(content))
                            return content, ""
                except Exception:
                    pass

        human_wait(page_for_request, 1.5, 2.5)

        # 策略 2 回退：再次尝试捕获到的 URL
        for url in captured_urls:
            content = _fetch_pdf_by_url(request_api, url)
            if content:
                logger.info("策略2 二次 URL：下载成功，%d 字节", len(content))
                return content, ""
    finally:
        for p in pages:
            try:
                p.remove_listener("response", on_response)
            except Exception:
                pass

    if not content or len(content) < 100:
        return None, "下载 PDF 失败（已进入预览页但无法获取文件，点击或 URL 均未成功）"
    return content, ""


def download_resume_from_preview_page(
    cdp_url: str = "http://127.0.0.1:9222",
    download_to_pending: bool = True,
    candidate_name: str = "preview",
    file_label: str = "",
) -> dict:
    """
    从当前已打开的「简历预览弹窗」下载 PDF。
    前置：用户已用 --remote-debugging-port 启动 Chrome，并停留在简历预览弹窗界面。

    Args:
        cdp_url: Chrome 远程调试地址
        download_to_pending: 是否保存到 data/{职位}/pending/
        candidate_name: 归档时的候选人标识
        file_label: 自定义文件名（不含.pdf）

    Returns:
        {"success": bool, "pdf_path": str, "error": str}
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"success": False, "pdf_path": "", "error": "playwright 未安装"}

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(cdp_url, timeout=5000)
            contexts = browser.contexts
            if not contexts:
                return {"success": False, "pdf_path": "", "error": "未找到浏览器上下文"}
            context = contexts[0]
            pages = context.pages
            if not pages:
                return {"success": False, "pdf_path": "", "error": "未找到页面"}

            for page in pages:
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=2000)
                except Exception:
                    pass

            content, err = _do_download(context, pages, pages[0])
            if err:
                return {"success": False, "pdf_path": "", "error": err}

            if download_to_pending:
                out = local_archiver(
                    pdf_bytes=content,
                    candidate_name=candidate_name,
                    file_label=file_label,
                )
                if out.get("success"):
                    return {"success": True, "pdf_path": out["saved_path"], "error": ""}
                return {"success": False, "pdf_path": "", "error": out.get("error", "归档失败")}

            tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
            tmp.write(content)
            tmp.close()
            return {"success": True, "pdf_path": tmp.name, "error": ""}
    except Exception as e:
        logger.error(f"download_resume_from_preview_page failed: {e}", exc_info=True)
        err_msg = str(e)
        if "Target closed" in err_msg or "connect" in err_msg.lower():
            err_msg = f"{err_msg}\n提示：请用 scripts\\launch_chrome_debug.ps1 启动 Chrome"
        return {"success": False, "pdf_path": "", "error": err_msg}


def click_preview_and_download(
    page,
    context,
    pages,
    file_label: str = "",
    candidate_name: str = "preview",
    download_to_pending: bool = True,
    save_dir: str | Path | None = None,
    job_folder: str = "",
) -> dict:
    """
    在当前对话页点击「点击预览附件简历」并下载 PDF。
    前置：page 已在目标候选人对话内，不包含选择职位、点击候选人等导航逻辑。

    Returns:
        {"success": bool, "pdf_path": str, "error": str}
    """
    preview_btn = None
    for sel in BOSS_PREVIEW_BTN_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                preview_btn = loc
                break
        except Exception:
            pass
    if not preview_btn or preview_btn.count() == 0:
        try:
            hit = page.get_by_text("点击预览附件简历", exact=False).first
            if hit.count() > 0:
                preview_btn = hit
        except Exception:
            pass
    if not preview_btn or preview_btn.count() == 0:
        return {
            "success": False,
            "pdf_path": "",
            "error": "未找到「点击预览附件简历」按钮",
        }

    # 先设置 response 监听（必须在点击预览之前），以便捕获 PDF 请求 URL
    captured_urls: list[str] = []

    def on_response(response):
        try:
            url = (response.url or "").strip()
            if "zhipin.com" not in url and "zhpin.com" not in url:
                return
            ct = (response.headers.get("content-type") or "").lower()
            is_pdf_ct = "pdf" in ct or "application/octet-stream" in ct
            is_pdf_url = ".pdf" in url or "preview4boss" in url or "download" in url or "attachment" in url or "resume" in url
            if is_pdf_ct or (is_pdf_url and response.status == 200):
                captured_urls.append(url)
                logger.info("收网监听: 捕获 URL %s...", url[:90])
        except Exception:
            pass

    for p in pages:
        try:
            p.on("response", on_response)
        except Exception:
            pass

    preview_btn.scroll_into_view_if_needed()
    human_wait(page, 0.2, 0.6)
    preview_btn.click()
    # 预览弹窗加载 PDF 需时，延长等待
    human_wait(page, 2.5, 4.0)

    try:
        content, err = _do_download(context, pages, page, pre_captured_urls=captured_urls)
    finally:
        for p in pages:
            try:
                p.remove_listener("response", on_response)
            except Exception:
                pass
    if err:
        _close_preview_dialog(page, pages)
        return {"success": False, "pdf_path": "", "error": err}

    if download_to_pending:
        out = local_archiver(
            pdf_bytes=content,
            candidate_name=candidate_name,
            file_label=file_label,
            target_dir=save_dir,
            job_folder=job_folder,
            use_flat_dir=bool(save_dir and job_folder),
        )
        _close_preview_dialog(page, pages)
        if out.get("success"):
            return {"success": True, "pdf_path": out["saved_path"], "error": ""}
        return {"success": False, "pdf_path": "", "error": out.get("error", "归档失败")}

    _close_preview_dialog(page, pages)
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.write(content)
    tmp.close()
    return {"success": True, "pdf_path": tmp.name, "error": ""}


def _select_filter_tab(page, filter_tab: str) -> bool:
    """点击消息列表筛选 Tab（如「新招呼」「全部」）。"""
    if not filter_tab or not filter_tab.strip():
        return True
    try:
        hit = page.get_by_text(filter_tab.strip(), exact=False).first
        if hit.count() > 0:
            hit.click()
            human_wait(page, 0.5, 1.2)
            return True
    except Exception:
        pass
    return False


# 每次收网最多处理数（防机器人），0 表示不限制由调用方决定
MAX_OPS_PER_RUN = 5


def atom_inbox_harvester_full_flow(
    cdp_url: str = "http://127.0.0.1:9222",
    job_text: str = "资深Golang语言开发_杭州 25-40K",
    download_to_pending: bool = True,
    max_items: int = 50,
    save_dir: str | Path | None = None,
    filter_tab: str = "",
    request_if_no_resume: bool = True,
    job_folder: str = "",
    max_ops_per_run: int = 0,
    use_all_positions: bool = True,
    stop_when_downloaded: int = 0,
) -> dict:
    """
    收网抓取完整流程：选择职位 → 从上往下依次遍历左侧候选人会话 →
    - 有简历：执行 PDF 下载到 save_dir/data/{职位}/pending
    - 无简历：点击「求简历」向求职者索要简历（request_if_no_resume=True 时）

    前置：Chrome 以 --remote-debugging-port 启动，停留在 Boss 沟通页。
    max_ops_per_run: 每轮最多处理数；0 表示不限制，遍历整个列表直到 max_items 或 stop_when_downloaded。
    stop_when_downloaded: 当本次已下载份数 >= 此值时提前结束（0 表示不提前停）。

    Returns:
        {"success": bool, "pdf_paths": list, "downloaded": int, "requested_count": int,
         "processed": list, "error": str}
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"success": False, "pdf_paths": [], "downloaded": 0, "requested_count": 0, "processed": [], "error": "playwright 未安装"}

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(cdp_url, timeout=5000)
            contexts = browser.contexts
            if not contexts:
                return {"success": False, "pdf_paths": [], "downloaded": 0, "requested_count": 0, "processed": [], "error": "未找到浏览器上下文"}
            context = contexts[0]
            pages = context.pages
            if not pages:
                return {"success": False, "pdf_paths": [], "downloaded": 0, "requested_count": 0, "processed": [], "error": "未找到页面"}

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

            try:
                from playwright_stealth import stealth_sync
                for p in pages:
                    try:
                        stealth_sync(p)
                        logger.debug("stealth_sync applied to page")
                    except Exception as e:
                        logger.debug("stealth_sync skip: %s", e)
            except ImportError:
                logger.debug("playwright-stealth not installed, skip")

            page.wait_for_load_state("domcontentloaded", timeout=5000)
            try:
                page.bring_to_front()
                human_wait(page, 0.3, 0.7)
            except Exception:
                pass

            # 若在个人中心等非沟通页，先安全跳转到沟通页
            navigate_to_chat_page(page)
            human_wait(page, 0.5, 1.0)

            # use_all_positions=True 时直接选「全部职位」（便于新职位短时内完成测试）
            if use_all_positions:
                if not select_all_positions(page):
                    return {
                        "success": False,
                        "pdf_paths": [],
                        "downloaded": 0,
                        "requested_count": 0,
                        "processed": [],
                        "error": "无法选择「全部职位」",
                    }
            elif not select_job(page, job_text):
                return {
                    "success": False,
                    "pdf_paths": [],
                    "downloaded": 0,
                    "requested_count": 0,
                    "processed": [],
                    "error": f"无法选择职位「{job_text}」，请确认该职位存在。",
                }

            human_wait(page, 0.8, 1.5)

            if filter_tab:
                _select_filter_tab(page, filter_tab)
                human_wait(page, 0.3, 0.8)

            def _find_chat_items():
                for sel in BOSS_CHAT_ITEM_SELECTORS:
                    try:
                        loc = page.locator(sel)
                        if loc.count() > 0:
                            return loc
                    except Exception:
                        pass
                return None

            chat_items_loc = _find_chat_items()
            # 当前 tab 无对话时，若未选「全部」则回退到「全部」再试（便于求简历）
            if (not chat_items_loc or chat_items_loc.count() == 0) and filter_tab and filter_tab.strip() != "全部":
                logger.info("当前 tab「%s」无对话，回退到「全部」再试", filter_tab)
                _select_filter_tab(page, "全部")
                human_wait(page, 0.5, 1.2)
                chat_items_loc = _find_chat_items()

            if not chat_items_loc or chat_items_loc.count() == 0:
                logger.warning(
                    "未找到左侧求职者对话列表（filter_tab=%s request_if_no_resume=%s），无法求简历",
                    filter_tab or "(空)",
                    request_if_no_resume,
                )
                return {
                    "success": True,
                    "pdf_paths": [],
                    "downloaded": 0,
                    "requested_count": 0,
                    "processed": [],
                    "error": "未找到左侧求职者对话列表",
                }

            # max_ops_per_run=0 时遍历整个列表，否则限制每轮处理数（防机器人）
            ops_limit = max_ops_per_run if max_ops_per_run > 0 else max_items
            n = min(chat_items_loc.count(), max_items, ops_limit)
            logger.info("本轮最多处理 %d 个对话（共 %d 个候选），stop_when_downloaded=%d", n, chat_items_loc.count(), stop_when_downloaded)
            pdf_paths = []
            downloaded = 0
            requested_count = 0
            processed = []

            if request_if_no_resume:
                from .atom_request_resume import _click_request_resume_btn

            def _random_wait_after_op():
                """每次聊天/下载后进行随机等待（3~8秒），模拟人类操作"""
                human_wait(page, 3.0, 8.0)
                logger.info("[Anti-Bot] 操作后已等待，继续下一个")
            for i in range(n):
                try:
                    item = chat_items_loc.nth(i)
                    item.scroll_into_view_if_needed()
                    human_wait(page, 0.2, 0.6)

                    name_el = item.locator("span.geek-name").first
                    job_el = item.locator("span.source-job").first
                    candidate_name = (name_el.inner_text() or "").strip() if name_el.count() > 0 else f"候选人_{i}"
                    job_from_item = (job_el.inner_text() or "").strip() if job_el.count() > 0 else ""
                    label = f"{candidate_name} ({job_from_item})" if job_from_item else candidate_name

                    item.click()
                    human_wait(page, 1.2, 2.5)

                    if not has_preview_attachment_btn(page):
                        if request_if_no_resume:
                            if _click_request_resume_btn(page):
                                requested_count += 1
                                processed.append({"label": label, "action": "request_sent"})
                                logger.info("已对 %s 点击求简历（无附件简历）", label)
                                _random_wait_after_op()
                            else:
                                processed.append({"label": label, "action": "skipped_no_preview"})
                                logger.info("跳过 %s：无「求简历」按钮或点击失败", label)
                                human_wait(page, 0.4, 0.9)
                        else:
                            processed.append({"label": label, "action": "skipped_no_request"})
                            logger.info("跳过 %s：无简历但未开启自动求简历（request_if_no_resume=False）", label)
                            human_wait(page, 0.4, 0.9)
                        continue

                    job_title_text = _get_current_job_label(page) or ""
                    if job_title_text == "全部职位":
                        job_title_text = ""
                    job_part = job_title_text or job_from_item or job_text

                    identity_text = ""
                    try:
                        hit = page.get_by_text(re.compile(r"\d+年应届生|\d+年经验")).first
                        if hit.count() > 0:
                            identity_text = (hit.inner_text() or "").strip()
                    except Exception:
                        pass
                    file_label = f"【{job_part}】{candidate_name}" + (f" {identity_text}" if identity_text else "")

                    # 防重复下载：若该候选人简历已存在则跳过，避免封号
                    skipped_exists = False
                    try:
                        from .local_archiver import _extract_job_folder
                        subdir = _extract_job_folder(file_label, job_part)
                        cand_dir = Path(save_dir) if (save_dir and job_folder) else (Path(save_dir) / subdir if save_dir else None)
                        if cand_dir and cand_dir.is_dir():
                            for f in cand_dir.glob("*.pdf"):
                                if candidate_name in (f.stem or ""):
                                    logger.info("发现已存在简历，跳过下载以防封号: %s", f.name)
                                    pdf_paths.append(str(f.resolve()))
                                    processed.append({"label": label, "action": "skipped_exists", "path": str(f)})
                                    skipped_exists = True
                                    break
                    except Exception:
                        pass
                    if skipped_exists:
                        human_wait(page, 0.3, 0.6)
                        continue

                    out = click_preview_and_download(
                        page=page,
                        context=context,
                        pages=pages,
                        file_label=file_label,
                        candidate_name=candidate_name,
                        download_to_pending=download_to_pending,
                        save_dir=save_dir,
                        job_folder=job_folder,
                    )
                    if out.get("success") and out.get("pdf_path"):
                        pdf_paths.append(out["pdf_path"])
                        downloaded += 1
                        processed.append({"label": label, "action": "downloaded", "path": out["pdf_path"]})
                        logger.info("已下载 %s 简历: %s", label, out["pdf_path"])
                        _random_wait_after_op()
                        # 简历已满则提前结束
                        if stop_when_downloaded > 0 and downloaded >= stop_when_downloaded:
                            logger.info("已下载 %d 份简历，达到目标，提前结束遍历", downloaded)
                            break
                    else:
                        processed.append({"label": label, "action": "download_failed", "error": out.get("error", "")})
                        logger.warning("下载 %s 失败: %s", label, out.get("error", ""))
                        human_wait(page, 0.5, 1.2)
                except Exception as e:
                    logger.warning("处理第 %d 个对话失败: %s", i + 1, e)
                    processed.append({"label": f"item_{i}", "action": "error", "error": str(e)})

            return {
                "success": True,
                "pdf_paths": pdf_paths,
                "downloaded": downloaded,
                "requested_count": requested_count,
                "processed": processed,
                "error": "",
            }
    except Exception as e:
        logger.error(f"atom_inbox_harvester_full_flow failed: {e}", exc_info=True)
        err_msg = str(e)
        if "Target closed" in err_msg or "connect" in err_msg.lower():
            err_msg = f"{err_msg}\n提示：请用 scripts\\launch_chrome_debug.ps1 启动 Chrome"
        return {"success": False, "pdf_paths": [], "downloaded": 0, "requested_count": 0, "processed": [], "error": err_msg}
