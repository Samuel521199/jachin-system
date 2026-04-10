"""
Scrapling 高匿抓取旁路微服务：独立进程，避免阻塞 L3 主对话。

启动：uvicorn server:app --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import os
import traceback

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, HttpUrl

# Scrapling：HTTP Fetcher 无 headless；高匿浏览器路径为 StealthyFetcher.fetch
from scrapling.fetchers import StealthyFetcher

# 单次页面内操作超时（毫秒），须明显小于上游 util 的 HTTP 超时（15s）
_DEFAULT_PAGE_TIMEOUT_MS = int(os.environ.get("SCRAPLING_PAGE_TIMEOUT_MS", "12000"))
_TEXT_MAX = int(os.environ.get("SCRAPLING_TEXT_MAX_CHARS", "500000"))
_HTML_MAX = int(os.environ.get("SCRAPLING_HTML_MAX_CHARS", "300000"))

app = FastAPI(title="Jachin Scrapling Service", version="1.0.0")


class ScrapeIn(BaseModel):
    url: HttpUrl = Field(..., description="目标页面 URL")


class ScrapeOut(BaseModel):
    url: str
    text: str
    html_excerpt: str
    http_status: int


@app.post("/api/scrape", response_model=ScrapeOut)
def api_scrape(body: ScrapeIn) -> ScrapeOut:
    url_str = str(body.url)
    try:
        resp = StealthyFetcher.fetch(
            url_str,
            headless=True,
            timeout=_DEFAULT_PAGE_TIMEOUT_MS,
            disable_resources=True,
        )
        status = int(getattr(resp, "status", 200) or 200)
        # Response 继承 Selector：可见文本 + 精简 inner HTML
        try:
            raw_text = resp.get_all_text(strip=True, separator="\n")
            text = str(raw_text) if raw_text is not None else ""
        except Exception:
            text = str(getattr(resp, "text", "") or "")
        try:
            hc = resp.html_content
            html_full = str(hc) if hc is not None else ""
        except Exception:
            html_full = ""
        if len(text) > _TEXT_MAX:
            text = text[:_TEXT_MAX] + "\n...[truncated]"
        html_excerpt = html_full[:_HTML_MAX]
        if len(html_full) > _HTML_MAX:
            html_excerpt += "\n...[html truncated]"
        return ScrapeOut(
            url=url_str,
            text=text,
            html_excerpt=html_excerpt,
            http_status=status,
        )
    except Exception as e:
        tb = traceback.format_exc()
        # 避免日志过长；开发时可看 stderr
        print(f"[scrapling-service] scrape failed: {e}\n{tb}", flush=True)
        raise HTTPException(status_code=500, detail=str(e)) from e


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8000, log_level="info")
