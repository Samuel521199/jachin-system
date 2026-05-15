"""
视觉愈合专家：选择器超时、定位器僵死、未知遮罩等 DOM 侧异常的轻量自愈。

不依赖具体业务站点逻辑；仅做通用键盘/浅层点击的组合。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger("agentic_mesh.dom_healer")


class DomHealer:
    def __init__(self, page: Any) -> None:
        self._page = page

    async def attempt_heal(self, *, error_context: str) -> bool:
        """
        返回 True 表示已执行可重试的修复动作（是否真能过关由外层重试原函数验证）。
        """
        actions_tried: list[str] = []

        async def _escape_burst() -> None:
            for _ in range(3):
                try:
                    await self._page.keyboard.press("Escape")
                    await asyncio.sleep(0.12)
                except Exception:
                    break

        async def _click_generic_closes() -> bool:
            selectors = (
                '[aria-label="Close"]',
                'button:has-text("Close")',
                'button:has-text("×")',
                ".modal-close",
                "[data-testid='close']",
            )
            for sel in selectors:
                try:
                    loc = self._page.locator(sel).first
                    n = await loc.count()
                    if n > 0:
                        await loc.click(timeout=800, force=True)
                        await asyncio.sleep(0.1)
                        return True
                except Exception:
                    continue
            return False

        try:
            await _escape_burst()
            hit = await _click_generic_closes()
            actions_tried.append("escape_then_close" if hit else "escape_only")
            logger.info(
                "[DomHealer] 自愈动作: %s | err=%s",
                actions_tried,
                (error_context or "")[:160],
            )
            return True
        except Exception as e:
            logger.warning("[DomHealer] attempt_heal 失败: %s", str(e)[:240])
            return False
