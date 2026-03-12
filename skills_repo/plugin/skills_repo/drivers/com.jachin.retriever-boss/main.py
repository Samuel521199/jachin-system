"""
com.jachin.retriever-boss - The Retriever (猎犬引擎)
Boss 直聘简历获取 - 系统级驱动

设计要求（来自图1/图2）：
1. 注册为系统级 Driver，置于 drivers/ 目录
2. 声明最高级本地网络和浏览器执行权限，突破 Wasm 沙箱
3. 底层调用宿主机 Playwright 执行浏览器自动化
4. 安全：零密码上云。扫码/Cookie 注入，Cookie 存 ~/.jachin/core/config/
5. Layer 3 无头浏览器弹窗让 HR 扫码登录，拦截 Cookie 存本地
"""

import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional, List

try:
    from core.skills.base_skill import BaseSkill
except ImportError:
    BaseSkill = object

logger = logging.getLogger(__name__)

# Cookie 存储路径：零密码上云，仅存本地
COOKIE_DIR = Path(os.environ.get("JACHIN_CONFIG", os.path.expanduser("~/.jachin/core/config")))
BOSS_COOKIE_FILE = COOKIE_DIR / "boss_zhipin_cookies.json"


def _ensure_cookie_dir() -> Path:
    COOKIE_DIR.mkdir(parents=True, exist_ok=True)
    return COOKIE_DIR


def _load_cookies() -> List[Dict[str, Any]]:
    """从本地安全域加载 Cookie"""
    if not BOSS_COOKIE_FILE.exists():
        return []
    try:
        with open(BOSS_COOKIE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Load cookies failed: {e}")
        return []


def _save_cookies(cookies: List[Dict[str, Any]]) -> None:
    """保存 Cookie 到本地安全域"""
    _ensure_cookie_dir()
    with open(BOSS_COOKIE_FILE, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)


class RetrieverBossSkill(BaseSkill):
    """The Retriever - Boss 直聘简历获取（Driver）"""

    def __init__(self, manifest: Dict[str, Any]):
        if BaseSkill is not object:
            super().__init__(manifest)
        else:
            self.manifest = manifest
            self.skill_id = manifest.get("id", "unknown")

    async def execute(self, capability: str, params: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if capability == "fetch_resumes_by_job":
            return await self.fetch_resumes_by_job(params)
        if capability == "check_cookie_status":
            return await self.check_cookie_status(params)
        return {"success": False, "error": f"Unknown capability: {capability}"}

    async def check_cookie_status(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """检查 Cookie 是否有效。无效时需 Layer 3 弹窗让用户扫码登录"""
        cookies = _load_cookies()
        if not cookies:
            return {
                "success": True,
                "valid": False,
                "message": "无本地 Cookie，请通过 Layer 3 扫码登录 Boss 直聘",
                "action": "scan_qr_login",
            }
        # 简化：仅检查是否存在，实际应请求一个轻量 API 验证
        return {
            "success": True,
            "valid": True,
            "message": "Cookie 已存在，可进行简历搜索",
        }

    async def fetch_resumes_by_job(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        根据岗位在 Boss 直聘搜索简历。
        使用 Playwright 无头浏览器 + 本地 Cookie 静默运行。
        Boss 反爬严格，本实现为框架雏形，实际需：
        - 合理延时、随机 UA、代理轮换
        - 与 Layer 3 配合完成扫码流程
        """
        job_title = params.get("job_title", "")
        job_desc = params.get("job_desc", "")
        max_count = params.get("max_count", 10)

        if not job_title:
            return {"success": False, "error": "job_title is required"}

        cookies = _load_cookies()
        if not cookies:
            return {
                "success": False,
                "error": "无 Cookie，请先扫码登录",
                "action": "scan_qr_login",
            }

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return {"success": False, "error": "playwright not installed. pip install playwright && playwright install chromium"}

        resumes: List[Dict[str, Any]] = []

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context()
                await context.add_cookies(cookies)

                page = await context.new_page()
                # Boss 直聘搜索页（实际 URL 可能变化，此处为示例）
                search_url = "https://www.zhipin.com/web/geek/job?query=" + job_title.replace(" ", "%20")
                await page.goto(search_url, wait_until="domcontentloaded", timeout=15000)

                # 反爬：适度等待
                await page.wait_for_timeout(2000)

                # 解析简历卡片（选择器需根据实际 DOM 调整）
                # 此处为框架代码，实际需根据 Boss 页面结构编写
                items = await page.query_selector_all("[class*='job-card'], [data-job-id]")
                for i, item in enumerate(items[:max_count]):
                    try:
                        text = await item.inner_text()
                        resumes.append({"raw": text[:2000], "index": i + 1})
                    except Exception:
                        pass

                await browser.close()

            return {
                "success": True,
                "resumes": resumes,
                "count": len(resumes),
                "job_title": job_title,
            }
        except Exception as e:
            logger.error(f"fetch_resumes_by_job failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "hint": "Boss 反爬严格，可能需要更新选择器或增加反检测策略",
            }
