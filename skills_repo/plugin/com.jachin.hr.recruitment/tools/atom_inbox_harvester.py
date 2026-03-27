"""
原子 Tool: atom_inbox_harvester
点击「全部职位」/职位下拉 → 选择对应职位（从 jd.json 的 job_text）→ 遍历左侧所有候选人会话 →
若有「点击预览附件简历」按钮则执行 PDF 下载，否则跳过继续下一个。

反爬/拟人化：stealth 隐身、随机等待、深呼吸机制。
"""
from __future__ import annotations

import logging
import os
import random
import re
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .anti_bot_guardian import check_and_bypass_anti_bot, should_reraise_hitl
from .atom_request_resume import _click_request_resume_btn
from .boss_utils import (
    _get_current_job_label,
    _verify_job_selected,
    dismiss_boss_onboarding_overlays,
    is_boss_identity_verify_page,
    navigate_to_chat_page,
    select_all_positions,
    select_job,
)
from .human_utils import human_wait
from .local_archiver import _extract_job_folder, local_archiver
from .os_signal_probe import os_stop_requested as _os_stop_requested

logger = logging.getLogger(__name__)

# Boss 沟通页在验证码/SPA 下 domcontentloaded 可能迟迟不满足；过短会导致「继续」后首轮收网直接超时退出
_BOSS_PAGE_DOMCONTENTLOADED_MS = int(os.environ.get("JACHIN_BOSS_INBOX_DOMLOAD_MS", "28000") or "28000")
_CDP_CONNECT_MS = int(os.environ.get("JACHIN_BOSS_CDP_CONNECT_MS", "15000") or "15000")


def _wait_domcontentloaded_lenient(page, *, timeout_ms: int | None = None) -> None:
    """
    等待主帧 domcontentloaded；超时仅告警并继续（页面常仍可操作）。
    验证码后重连 CDP 时，Playwright 日志可能出现 networkidle 与 domcontentloaded 不同步。
    """
    ms = int(timeout_ms if timeout_ms is not None else _BOSS_PAGE_DOMCONTENTLOADED_MS)
    ms = max(3000, min(120000, ms))
    try:
        page.wait_for_load_state("domcontentloaded", timeout=ms)
    except Exception as e:
        logger.warning(
            "[inbox] domcontentloaded 未在 %dms 内就绪（%s），页面可能仍可用，继续收网流程",
            ms,
            e,
        )
        try:
            human_wait(page, 0.4, 0.9)
        except Exception:
            pass


def _harvest_return(
    *,
    success: bool,
    pdf_paths: list | None,
    downloaded: int = 0,
    requested_count: int = 0,
    processed: list | None = None,
    error: str = "",
    chat_list_count: int = -1,
    inbox_no_chats: bool | None = None,
    **extra: object,
) -> dict:
    """统一收网返回结构；供调度器根据「左侧沟通列表」动态调整推荐/收网频率。"""
    proc = [] if processed is None else processed
    paths = [] if pdf_paths is None else pdf_paths
    c = int(chat_list_count)
    if inbox_no_chats is not None:
        nc = bool(inbox_no_chats)
    else:
        nc = c == 0 if c >= 0 else False
    out: dict = {
        "success": success,
        "pdf_paths": paths,
        "downloaded": downloaded,
        "requested_count": requested_count,
        "processed": proc,
        "error": error,
        "chat_list_count": c,
        "inbox_no_chats": nc,
    }
    out.update(extra)
    return out


BOSS_CHAT_ITEM_SELECTORS = [
    "div.geek-item",
    ".geek-item",
    "[class*='geek-item']",
]

ZHIPIN_BASE = "https://www.zhipin.com"

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
    '[class*="download"]',
    '[class*="icon-download"]',
    '[class*="download"] svg',
    '[class*="icon-download"] svg',
    '[data-testid*="download"]',
    '[data-testid*="Download"]',
    '#download',
    'button#download',
    '[id="download"]',
    'button:has-text("保存简历")',
    'a:has-text("保存简历")',
    'span:has-text("保存简历")',
    'div:has-text("保存简历")',
    'button:has-text("下载")',
    'a:has-text("下载")',
    'span:has-text("下载")',
    'div:has-text("下载")',
    'a[download]',
    '[title*="下载"]',
    '[title*="Download"]',
    '[aria-label*="下载"]',
    '[aria-label*="download"]',
    '[class*="dialog"] button:has-text("下载")',
    '[class*="dialog"] a:has-text("下载")',
    '[class*="preview"] button:has-text("下载")',
    '[class*="modal"] [class*="download"]',
]

BOSS_PREVIEW_CLOSE_SELECTORS = [
    "[class*='boss-dialog'] i.icon-close",
    "[class*='boss-dialog'] [class*='icon-close']",
    "[class*='preview'] i.icon-close",
    "[class*='resume-preview'] i.icon-close",
    "[class*='dialog'] [class*='close']",
    "[class*='drawer'] i.icon-close",
    "[class*='modal'] i.icon-close",
    "[class*='icon-close'][class*='preview']",
    ".icon-close",
    "[class*='icon-close']",
    "i[class*='close']",
    "i.icon-close",
]

BOSS_PREVIEW_BTN_SELECTORS = [
    "span.card-btn:has-text('点击预览附件简历')",
    "text=点击预览附件简历",
    "a:has-text('点击预览附件简历')",
]


def dismiss_boss_resume_overlay(page, pages) -> None:
    """
    关闭「附件简历」PDF 预览遮罩（boss-dynamic-dialog + pdf-viewer-b iframe）。
    若不关，左侧 div.geek-item 点击会被弹层拦截（subtree intercepts pointer events）。
    """
    pages = list(pages) if pages else [page]
    try:
        dlg = page.locator('div.dialog-wrap.active[data-type="boss-dialog"]')
        if dlg.count() > 0:
            for sel in (
                "i.icon-close",
                ".icon-close",
                "[class*='layer-close']",
                "[class*='dialog-close']",
                "span.close",
                ".boss-popup__close",
                "button[aria-label*='关闭']",
                "[id^='boss-dynamic-dialog'] i.icon-close",
            ):
                try:
                    btn = dlg.first.locator(sel).first
                    if btn.count() > 0 and btn.is_visible():
                        btn.click(timeout=2500)
                        human_wait(page, 0.35, 0.7)
                        logger.info("[收网] 已关闭 Boss 附件预览弹层 (%s)", sel)
                        break
                except Exception:
                    pass
    except Exception as e:
        logger.debug("[收网] 关闭附件弹层(主层): %s", e)
    _close_preview_dialog(page, pages)
    try:
        for _ in range(3):
            page.keyboard.press("Escape")
            human_wait(page, 0.2, 0.45)
    except Exception:
        pass


def _close_preview_dialog(page, pages) -> bool:
    for p in pages:
        for sel in BOSS_PREVIEW_CLOSE_SELECTORS:
            try:
                loc = p.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    loc.scroll_into_view_if_needed(timeout=3000)
                    human_wait(p, 0.15, 0.5)
                    loc.click(timeout=3000)
                    human_wait(p, 0.4, 0.9)
                    logger.debug("已点击关闭按钮")
                    return True
            except Exception:
                pass
        for frame in p.frames:
            for sel in BOSS_PREVIEW_CLOSE_SELECTORS:
                try:
                    loc = frame.locator(sel).first
                    if loc.count() > 0 and loc.is_visible():
                        loc.scroll_into_view_if_needed(timeout=3000)
                        human_wait(p, 0.15, 0.5)
                        loc.click(timeout=3000)
                        human_wait(p, 0.4, 0.9)
                        logger.debug("已点击关闭按钮(iframe)")
                        return True
                except Exception:
                    pass
    try:
        hit = page.get_by_role("button", name="关闭").first
        if hit.count() > 0 and hit.is_visible():
            hit.click(timeout=3000)
            human_wait(page, 0.4, 0.9)
            return True
    except Exception:
        pass
    try:
        page.keyboard.press("Escape")
        human_wait(page, 0.3, 0.6)
        return True
    except Exception:
        pass
    return False


def has_preview_attachment_btn(page) -> bool:
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
    return False


def _extract_pdf_url_from_viewer(pages) -> str | None:
    for page in pages:
        try:
            for iframe_sel in [
                'iframe[src*="viewer.html"][src*="file="]',
                'iframe[src*="file="]',
                'iframe[src*="preview4boss"]',
                'iframe[src*="pdf"]',
            ]:
                iframes = page.locator(iframe_sel, timeout=5000)
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
    if not body or len(body) < 100:
        return False
    return body[:4] == b"%PDF" or b"%PDF" in body[:1024]


def _fetch_pdf_by_url(request_api, url: str, timeout: int = 10000) -> bytes | None:
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
    context,
    pages,
    page_for_request,
    pre_captured_urls: list[str] | None = None,
    os_context=None,
) -> tuple[bytes | None, str]:
    content = None
    request_api = page_for_request.request
    captured_urls = list(pre_captured_urls) if pre_captured_urls else []

    if _os_stop_requested(os_context):
        return None, "OS_STOP_HARVEST"

    pdf_url = _extract_pdf_url_from_viewer(pages)
    if pdf_url:
        content = _fetch_pdf_by_url(request_api, pdf_url)
        if content:
            logger.info("策略1 viewer URL：下载成功，%d 字节", len(content))
            return content, ""

    for url in captured_urls:
        content = _fetch_pdf_by_url(request_api, url)
        if content:
            logger.info("策略2 预捕获 URL：下载成功，%d 字节", len(content))
            return content, ""

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
        human_wait(page_for_request, 2.5, 4.5)
        _DL_TIMEOUT = 6000
        for p in pages:
            if _os_stop_requested(os_context):
                return None, "OS_STOP_HARVEST"
            for frame in p.frames:
                if _os_stop_requested(os_context):
                    return None, "OS_STOP_HARVEST"
                for sel in BOSS_DOWNLOAD_SELECTORS:
                    try:
                        loc = frame.locator(sel, timeout=3000)
                        if loc.count() > 0:
                            with context.expect_download(timeout=_DL_TIMEOUT) as download_info:
                                loc.first.click(force=True, timeout=3000)
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
        for p in pages:
            if _os_stop_requested(os_context):
                return None, "OS_STOP_HARVEST"
            for frame in p.frames:
                if _os_stop_requested(os_context):
                    return None, "OS_STOP_HARVEST"
                for text in ("保存简历", "下载"):
                    try:
                        btn = frame.get_by_text(text, exact=False).first
                        if btn.count() > 0:
                            with context.expect_download(timeout=_DL_TIMEOUT) as download_info:
                                btn.click(force=True, timeout=3000)
                            download = download_info.value
                            path = download.path()
                            if path and Path(path).exists():
                                content = Path(path).read_bytes()
                                download.delete()
                            if content and len(content) >= 100:
                                logger.info("策略4 点击 %s：成功，%d 字节", text, len(content))
                                return content, ""
                    except Exception:
                        pass
        for p in pages:
            if _os_stop_requested(os_context):
                return None, "OS_STOP_HARVEST"
            for frame in p.frames:
                if _os_stop_requested(os_context):
                    return None, "OS_STOP_HARVEST"
                for role, name in [("button", "下载"), ("link", "下载"), ("button", "保存简历")]:
                    try:
                        btn = frame.get_by_role(role, name=name)
                        if btn.count() > 0:
                            with context.expect_download(timeout=_DL_TIMEOUT) as download_info:
                                btn.first.click(force=True, timeout=3000)
                            download = download_info.value
                            path = download.path()
                            if path and Path(path).exists():
                                content = Path(path).read_bytes()
                                download.delete()
                            if content and len(content) >= 100:
                                logger.info("策略4 role=%s name=%s：成功，%d 字节", role, name, len(content))
                                return content, ""
                    except Exception:
                        pass

        human_wait(page_for_request, 1.5, 2.5)
        for url in captured_urls:
            if _os_stop_requested(os_context):
                return None, "OS_STOP_HARVEST"
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


def _wait_for_viewer_or_capture(pages, page_for_wait, captured_urls: list, max_wait_ms: int = 6000) -> None:
    try:
        if captured_urls:
            return
        per_page_timeout = min(4.0, max_wait_ms / 1000)
        for p in pages:
            try:
                p.wait_for_selector(
                    'iframe[src*="viewer.html"], iframe[src*="file="], iframe[src*="preview4boss"]',
                    timeout=per_page_timeout,
                )
                logger.debug("viewer iframe 已出现")
                return
            except Exception:
                pass
    except Exception as e:
        logger.debug("等待 viewer 超时或跳过: %s", e)


def click_preview_and_download(
    page,
    context,
    pages,
    file_label: str = "",
    candidate_name: str = "preview",
    download_to_pending: bool = True,
    save_dir: str | Path | None = None,
    job_folder: str = "",
    os_context=None,
) -> dict:
    if _os_stop_requested(os_context):
        return {"success": False, "pdf_path": "", "error": "OS_STOP_HARVEST", "stopped_by_os": True}

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

    preview_btn.scroll_into_view_if_needed(timeout=8000)
    human_wait(page, 0.2, 0.6)
    if _os_stop_requested(os_context):
        return {"success": False, "pdf_path": "", "error": "OS_STOP_HARVEST", "stopped_by_os": True}
    preview_btn.click(timeout=5000)
    human_wait(page, 1.0, 2.0)
    if _os_stop_requested(os_context):
        return {"success": False, "pdf_path": "", "error": "OS_STOP_HARVEST", "stopped_by_os": True}
    pages = list(context.pages)
    _wait_for_viewer_or_capture(pages, page, captured_urls, max_wait_ms=6000)
    human_wait(page, 3.0, 5.0)
    if _os_stop_requested(os_context):
        return {"success": False, "pdf_path": "", "error": "OS_STOP_HARVEST", "stopped_by_os": True}

    content, err = _do_download(context, pages, page, pre_captured_urls=captured_urls, os_context=os_context)

    for p in pages:
        try:
            p.remove_listener("response", on_response)
        except Exception:
            pass
    pages = list(context.pages)
    if err:
        dismiss_boss_resume_overlay(page, pages)
        if err == "OS_STOP_HARVEST":
            return {"success": False, "pdf_path": "", "error": err, "stopped_by_os": True}
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
        dismiss_boss_resume_overlay(page, pages)
        if out.get("success"):
            return {"success": True, "pdf_path": out["saved_path"], "error": ""}
        return {"success": False, "pdf_path": "", "error": out.get("error", "归档失败")}

    dismiss_boss_resume_overlay(page, pages)
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.write(content)
    tmp.close()
    return {"success": True, "pdf_path": tmp.name, "error": ""}


def _inject_stop_harvest_for_workflow(os_context: dict | None) -> None:
    """与飞书「停止收网」一致：持久化 STOP_HARVEST，便于「继续」统一恢复。"""
    if not os_context or not isinstance(os_context, dict):
        return
    wid = str(os_context.get("_dag_workflow_id") or "").strip()
    if not wid:
        return
    try:
        from core.workflow_engine import DAGWorkflow, SIGNAL_STOP_HARVEST

        DAGWorkflow.inject_signal(wid, SIGNAL_STOP_HARVEST)
        logger.warning("[收网] 已注入 %s workflow=%s（验证页刹车）", SIGNAL_STOP_HARVEST, wid)
    except Exception as e:
        logger.debug("[收网] inject_signal 跳过: %s", e)


def _notify_lark_boss_verify_slider(job_label: str = "") -> None:
    jn = (job_label or "").strip() or "招聘任务"
    try:
        from l3_node.channels.lark.hr_recruitment_notify import send_hr_recruitment_progress_message
    except ImportError:
        logger.debug("[收网] Lark 通知跳过（无 hr_recruitment_notify）")
        return
    msg = (
        f"🛑【{jn}】Boss 已跳转**访客身份验证/异常访问**页，自动化已**立即停止**，请勿再连点。\n"
        f"请在 Chrome 中**手动完成滑块验证**并回到沟通页后，在飞书回复 **继续** 以恢复收网。"
    )
    try:
        send_hr_recruitment_progress_message(msg, message_kind="hr_boss_verify")
    except Exception as e:
        logger.warning("[收网] 飞书通知失败: %s", e)


def _exit_harvest_on_boss_verify(
    page,
    os_context: dict | None,
    job_folder: str,
    pdf_paths: list,
    downloaded: int,
    requested_count: int,
    processed: list,
    *,
    chat_list_count: int = -1,
) -> dict:
    logger.error(
        "[收网] 检测到 Boss 身份验证页，停止本轮遍历 job=%s url=%s",
        job_folder,
        (page.url or "")[:120],
    )
    _notify_lark_boss_verify_slider(job_folder)
    _inject_stop_harvest_for_workflow(os_context)
    if os_context is not None:
        os_context["_os_playwright_stop"] = True
    c = int(chat_list_count)
    return _harvest_return(
        success=True,
        pdf_paths=pdf_paths,
        downloaded=downloaded,
        requested_count=requested_count,
        processed=processed,
        error="boss_identity_verify_slider",
        chat_list_count=c,
        inbox_no_chats=(c == 0) if c >= 0 else None,
        stopped_by_os=True,
        stopped_by_boss_verify_slider=True,
    )


def _select_filter_tab(page, filter_tab: str) -> bool:
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
    use_all_positions: bool = False,
    stop_when_downloaded: int = 0,
    workflow_hitl_context: dict | None = None,
    os_context: dict | None = None,
) -> dict:
    """
    收网抓取完整流程：选择职位 → 从上往下依次遍历左侧候选人会话 →
    - 有简历：执行 PDF 下载到 save_dir（通常为 ~/.jachin/workspace/hr_recruitment/{职位}/pending）
    - 无简历：点击「求简历」向求职者索要简历（request_if_no_resume=True 时）

    前置：Chrome 以 --remote-debugging-port 启动，停留在 Boss 沟通页。
    use_all_positions: True 选「全部职位」、忽略 job_text（仅联调）；默认 False 按 job_text 选职位。

    workflow_hitl_context:
        可选 DAG 上下文（含 ``_human_decision``），供反爬/滑块检测时 HITL 注入。
    os_context:
        可选 OS/Workflow 上下文（与 HarvestLoop 共用）：长循环内探针 STOP_HARVEST 秒级刹车。
        未传 workflow_hitl_context 时，反爬亦使用 os_context。
    """
    _hitl = workflow_hitl_context if workflow_hitl_context is not None else os_context
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return _harvest_return(success=False, pdf_paths=[], error="playwright 未安装")

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(cdp_url, timeout=max(5000, _CDP_CONNECT_MS))
            contexts = browser.contexts
            if not contexts:
                return _harvest_return(success=False, pdf_paths=[], error="未找到浏览器上下文")
            context = contexts[0]
            pages = context.pages
            if not pages:
                return _harvest_return(success=False, pdf_paths=[], error="未找到页面")

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

            _wait_domcontentloaded_lenient(page)
            try:
                page.bring_to_front()
                human_wait(page, 0.3, 0.7)
            except Exception:
                pass
            check_and_bypass_anti_bot(page, _hitl)

            navigate_to_chat_page(page)
            human_wait(page, 0.5, 1.0)
            dismiss_boss_onboarding_overlays(page)
            check_and_bypass_anti_bot(page, _hitl)

            if use_all_positions:
                if not select_all_positions(page):
                    return _harvest_return(success=False, pdf_paths=[], error="无法选择「全部职位」")
            elif not select_job(page, job_text):
                return _harvest_return(
                    success=False,
                    pdf_paths=[],
                    error=f"无法选择职位「{job_text}」，请确认该职位存在。",
                )
            elif not use_all_positions:
                human_wait(page, 0.5, 1.0)
                lbl_now = _get_current_job_label(page)
                if lbl_now == "全部职位" or not (lbl_now or "").strip():
                    return _harvest_return(
                        success=False,
                        pdf_paths=[],
                        error=(
                            "职位未切换成功，顶部仍为「全部职位」。请先手动关闭新手引导（点「我知道了」），"
                            "再重试；或确认 Boss 下拉中的职位文案与 job_text 完全一致。"
                        ),
                    )
                ok_sel, cur_sel = _verify_job_selected(page, job_text)
                if not ok_sel:
                    return _harvest_return(
                        success=False,
                        pdf_paths=[],
                        error=f"职位与目标不符：期望「{job_text}」，当前「{cur_sel}」。",
                    )

            human_wait(page, 0.8, 1.5)
            check_and_bypass_anti_bot(page, _hitl)

            if filter_tab:
                _select_filter_tab(page, filter_tab)
                human_wait(page, 0.3, 0.8)
                check_and_bypass_anti_bot(page, _hitl)

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
            if (not chat_items_loc or chat_items_loc.count() == 0) and filter_tab and filter_tab.strip() != "全部":
                logger.info("当前 tab「%s」无对话，回退到「全部」再试", filter_tab)
                _select_filter_tab(page, "全部")
                human_wait(page, 0.5, 1.2)
                chat_items_loc = _find_chat_items()
                check_and_bypass_anti_bot(page, _hitl)

            if not chat_items_loc or chat_items_loc.count() == 0:
                logger.warning(
                    "未找到左侧求职者对话列表（filter_tab=%s request_if_no_resume=%s），无法求简历",
                    filter_tab or "(空)",
                    request_if_no_resume,
                )
                return _harvest_return(
                    success=True,
                    pdf_paths=[],
                    error="未找到左侧求职者对话列表",
                    chat_list_count=0,
                    inbox_no_chats=True,
                )

            chat_list_total = int(chat_items_loc.count())
            check_and_bypass_anti_bot(page, _hitl)
            if is_boss_identity_verify_page(page):
                return _exit_harvest_on_boss_verify(
                    page, os_context, job_folder, [], 0, 0, [], chat_list_count=chat_list_total
                )

            ops_limit = max_ops_per_run if max_ops_per_run > 0 else max_items
            n = min(chat_list_total, max_items, ops_limit)
            logger.info("本轮最多处理 %d 个对话（共 %d 个候选），stop_when_downloaded=%d", n, chat_list_total, stop_when_downloaded)
            pdf_paths = []
            downloaded = 0
            requested_count = 0
            processed = []

            def _random_wait_after_op():
                human_wait(page, 2.0, 4.0)
                logger.info("[Anti-Bot] 操作后已等待，继续下一个")

            # 翻页/遍历会话列表：每轮开头探针，实现飞书 STOP 秒级响应（等价于 while has_next_page 内嵌探针）
            for i in range(n):
                if _os_stop_requested(os_context):
                    return _harvest_return(
                        success=True,
                        pdf_paths=pdf_paths,
                        downloaded=downloaded,
                        requested_count=requested_count,
                        processed=processed,
                        error="",
                        chat_list_count=chat_list_total,
                        inbox_no_chats=False,
                        stopped_by_os=True,
                    )
                if is_boss_identity_verify_page(page):
                    return _exit_harvest_on_boss_verify(
                        page,
                        os_context,
                        job_folder,
                        pdf_paths,
                        downloaded,
                        requested_count,
                        processed,
                        chat_list_count=chat_list_total,
                    )
                try:
                    item = chat_items_loc.nth(i)
                    try:
                        item.scroll_into_view_if_needed(timeout=10000)
                    except Exception as scroll_err:
                        if is_boss_identity_verify_page(page):
                            return _exit_harvest_on_boss_verify(
                                page,
                                os_context,
                                job_folder,
                                pdf_paths,
                                downloaded,
                                requested_count,
                                processed,
                                chat_list_count=chat_list_total,
                            )
                        logger.warning("第 %d 项 scroll 超时或失败，跳过: %s", i + 1, scroll_err)
                        continue
                    human_wait(page, 0.2, 0.6)

                    name_el = item.locator("span.geek-name").first
                    job_el = item.locator("span.source-job").first
                    candidate_name = (name_el.inner_text() or "").strip() if name_el.count() > 0 else f"候选人_{i}"
                    job_from_item = (job_el.inner_text() or "").strip() if job_el.count() > 0 else ""
                    label = f"{candidate_name} ({job_from_item})" if job_from_item else candidate_name

                    fresh_pages = list(context.pages)
                    dismiss_boss_resume_overlay(page, fresh_pages)
                    clicked_item = False
                    last_click_err: Exception | None = None
                    for _attempt in range(8):
                        if _os_stop_requested(os_context):
                            return _harvest_return(
                                success=True,
                                pdf_paths=pdf_paths,
                                downloaded=downloaded,
                                requested_count=requested_count,
                                processed=processed,
                                error="",
                                chat_list_count=chat_list_total,
                                inbox_no_chats=False,
                                stopped_by_os=True,
                            )
                        dismiss_boss_resume_overlay(page, list(context.pages))
                        try:
                            item.click(timeout=6000)
                            clicked_item = True
                            break
                        except Exception as _ce:
                            last_click_err = _ce
                            human_wait(page, 0.35, 0.75)
                    if not clicked_item:
                        raise last_click_err or RuntimeError("geek-item 点击失败")
                    human_wait(page, 1.2, 2.5)
                    check_and_bypass_anti_bot(page, _hitl)
                    if is_boss_identity_verify_page(page):
                        return _exit_harvest_on_boss_verify(
                            page,
                            os_context,
                            job_folder,
                            pdf_paths,
                            downloaded,
                            requested_count,
                            processed,
                            chat_list_count=chat_list_total,
                        )

                    if not has_preview_attachment_btn(page):
                        if request_if_no_resume:
                            if _click_request_resume_btn(page):
                                requested_count += 1
                                processed.append({"label": label, "action": "request_sent"})
                                logger.info("已对 %s 点击求简历（无附件简历）", label)
                                _random_wait_after_op()
                                if is_boss_identity_verify_page(page):
                                    return _exit_harvest_on_boss_verify(
                                        page,
                                        os_context,
                                        job_folder,
                                        pdf_paths,
                                        downloaded,
                                        requested_count,
                                        processed,
                                        chat_list_count=chat_list_total,
                                    )
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

                    skipped_exists = False
                    try:
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
                        os_context=os_context,
                    )
                    if out.get("stopped_by_os"):
                        logger.warning("🚨 [OS 中断] 预览/下载流程中捕获 STOP_HARVEST")
                        return _harvest_return(
                            success=True,
                            pdf_paths=pdf_paths,
                            downloaded=downloaded,
                            requested_count=requested_count,
                            processed=processed,
                            error="",
                            chat_list_count=chat_list_total,
                            inbox_no_chats=False,
                            stopped_by_os=True,
                        )
                    if out.get("success") and out.get("pdf_path"):
                        pdf_paths.append(out["pdf_path"])
                        downloaded += 1
                        processed.append({"label": label, "action": "downloaded", "path": out["pdf_path"]})
                        logger.info("已下载 %s 简历: %s", label, out["pdf_path"])
                        _random_wait_after_op()
                        if is_boss_identity_verify_page(page):
                            return _exit_harvest_on_boss_verify(
                                page,
                                os_context,
                                job_folder,
                                pdf_paths,
                                downloaded,
                                requested_count,
                                processed,
                                chat_list_count=chat_list_total,
                            )
                        if stop_when_downloaded > 0 and downloaded >= stop_when_downloaded:
                            logger.info("已下载 %d 份简历，达到目标，提前结束遍历", downloaded)
                            break
                    else:
                        processed.append({"label": label, "action": "download_failed", "error": out.get("error", "")})
                        logger.warning("下载 %s 失败: %s", label, out.get("error", ""))
                        human_wait(page, 0.5, 1.2)
                except Exception as e:
                    if should_reraise_hitl(e):
                        raise
                    if is_boss_identity_verify_page(page):
                        return _exit_harvest_on_boss_verify(
                            page,
                            os_context,
                            job_folder,
                            pdf_paths,
                            downloaded,
                            requested_count,
                            processed,
                            chat_list_count=chat_list_total,
                        )
                    logger.warning("处理第 %d 个对话失败: %s", i + 1, e)
                    try:
                        dismiss_boss_resume_overlay(page, list(context.pages))
                    except Exception:
                        pass
                    processed.append({"label": f"item_{i}", "action": "error", "error": str(e)})

            return _harvest_return(
                success=True,
                pdf_paths=pdf_paths,
                downloaded=downloaded,
                requested_count=requested_count,
                processed=processed,
                error="",
                chat_list_count=chat_list_total,
                inbox_no_chats=False,
            )
    except Exception as e:
        if should_reraise_hitl(e):
            raise
        logger.error("atom_inbox_harvester_full_flow failed: %s", e, exc_info=True)
        err_msg = str(e)
        if "Target closed" in err_msg or "connect" in err_msg.lower():
            err_msg = f"{err_msg}\n提示：请用 scripts\\launch_chrome_debug.ps1 启动 Chrome"
        return _harvest_return(success=False, pdf_paths=[], error=err_msg)
