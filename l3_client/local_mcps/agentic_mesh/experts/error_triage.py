"""
Kalaroko E2E / SRE：异常分诊（线上故障 vs 脚本脱轨 vs 节点崩溃）。
"""

from __future__ import annotations

from typing import Any


async def triage_error(page: Any, exception: Exception) -> tuple[str, str]:
    """
    对捕获的异常进行 SRE 级分诊。

    :param page: Playwright ``Page``，可为 ``None``（仅按异常文本分诊）。
    :return: ``(错误分类标签, 极简描述)``
    """
    err_str = str(exception)
    type_name = type(exception).__name__

    # 1. 系统/宿主机崩溃
    if (
        "Event loop is closed" in err_str
        or "TargetClosedError" in type_name
        or "TargetClosedError" in err_str
        or "browser has been closed" in err_str.lower()
        or "Browser closed" in err_str
        or "Browser has been closed" in err_str
    ):
        return "[⚙️ 节点崩溃]", "宿主机或浏览器内核意外终止"

    # 2. 网络或服务端真实故障
    if (
        "net::ERR_" in err_str
        or "HTTP 50" in err_str
        or "HTTP 502" in err_str
        or "HTTP 503" in err_str
        or "[OFFLINE_DETECTED]" in err_str
        or "ERR_CONNECTION" in err_str
        or "ERR_NAME_NOT_RESOLVED" in err_str
    ):
        snippet = err_str[:60] + ("…" if len(err_str) > 60 else "")
        return "[🔴 线上故障]", f"网络阻断或服务器异常: {snippet}"

    # 3. 脚本执行脱轨 (最常见的 Timeout + locator)
    if "Timeout" in err_str and "locator" in err_str.lower():
        page_title = "未知页面"
        if page is not None:
            try:
                page_title = await page.title()
            except Exception:
                pass
        return "[🟡 脚本脱轨]", f"UI可能改版，找不到目标元素。停留页面: {page_title}"

    snippet = err_str[:60] + ("…" if len(err_str) > 60 else "")
    return "[❓ 未知异常]", snippet
