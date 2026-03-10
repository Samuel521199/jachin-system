"""The Retriever - 简历搜索（Boss直聘）"""
import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

COOKIE_DIR = Path(os.environ.get("HR_PLUGIN_CONFIG", os.path.expanduser("~/.hr_plugin/config")))
BOSS_COOKIE_FILE = COOKIE_DIR / "boss_zhipin_cookies.json"


def _load_cookies() -> List[Dict[str, Any]]:
    if not BOSS_COOKIE_FILE.exists():
        return []
    try:
        with open(BOSS_COOKIE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Load cookies failed: {e}")
        return []


async def check_cookie_status() -> Dict[str, Any]:
    """检查 Cookie 是否有效"""
    cookies = _load_cookies()
    return {
        "success": True,
        "valid": bool(cookies),
        "message": "Cookie 已存在" if cookies else "无本地 Cookie，请先扫码登录 Boss 直聘",
    }


async def fetch_resumes_by_job(
    job_title: str,
    job_desc: str = "",
    max_count: int = 10,
) -> Dict[str, Any]:
    """根据岗位在 Boss 直聘搜索简历"""
    cookies = _load_cookies()
    if not cookies:
        return {
            "success": False,
            "error": "无 Cookie，请先扫码登录",
            "resumes": [],
            "count": 0,
        }

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {
            "success": False,
            "error": "playwright 未安装: pip install playwright && playwright install chromium",
            "resumes": [],
            "count": 0,
        }

    resumes: List[Dict[str, Any]] = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            await context.add_cookies(cookies)
            page = await context.new_page()
            url = "https://www.zhipin.com/web/geek/job?query=" + job_title.replace(" ", "%20")
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(2000)
            items = await page.query_selector_all("[class*='job-card'], [data-job-id]")
            for i, item in enumerate(items[:max_count]):
                try:
                    text = await item.inner_text()
                    resumes.append({"raw": text[:2000], "index": i + 1})
                except Exception:
                    pass
            await browser.close()
        return {"success": True, "resumes": resumes, "count": len(resumes), "job_title": job_title}
    except Exception as e:
        logger.error(f"fetch_resumes_by_job failed: {e}", exc_info=True)
        return {"success": False, "error": str(e), "resumes": [], "count": 0}
