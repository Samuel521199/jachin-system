"""
Kalaroko 多轮 E2E 巡检完成后，可选通过 Lark 自定义机器人 Webhook 推送全文报告与 AI 综合分析。

环境变量（任一）：
  KALAROKO_INSPECT_LARK_WEBHOOK_URL — 推荐，仅巡检专用
  LARK_WEBHOOK_URL — 与 BI 等共用时的回退

未配置或为空则跳过。正文过长时拆成多条 interactive 卡片发送。
"""

from __future__ import annotations

import asyncio
import os
from typing import Callable

from l3_node.channels.lark.webhook import send_markdown

# 飞书 interactive 卡片 / lark_md 单条不宜过大；保守按字符切块
_DEFAULT_CHUNK = 2400


def inspection_lark_webhook_url() -> str | None:
    for key in ("KALAROKO_INSPECT_LARK_WEBHOOK_URL", "LARK_WEBHOOK_URL"):
        v = (os.getenv(key) or "").strip()
        if v and not v.startswith("${"):
            return v
    return None


def _chunk_text(text: str, max_chars: int) -> list[str]:
    """优先在段落边界截断，避免把一行表格拦腰切断。"""
    s = (text or "").strip()
    if not s:
        return []
    out: list[str] = []
    rest = s
    while rest:
        if len(rest) <= max_chars:
            out.append(rest)
            break
        window = rest[:max_chars]
        cut = window.rfind("\n\n")
        if cut < max_chars // 4:
            cut = window.rfind("\n")
        if cut < max_chars // 4:
            cut = max_chars
        chunk = rest[:cut].strip()
        if chunk:
            out.append(chunk)
        rest = rest[cut:].strip()
    return out


async def send_kalaroko_inspection_to_lark(
    *,
    markdown_report: str | None,
    llm_analysis: str | None,
    runs: int,
    interval: int,
    summary_model: str | None,
    line_sink: Callable[[str], None] | None = None,
    chunk_chars: int = _DEFAULT_CHUNK,
    delay_sec: float = 0.35,
) -> None:
    """
    异步发送（内部用 asyncio.to_thread 调 urllib），失败只打日志不抛。
    """
    url = inspection_lark_webhook_url()
    if not url:
        return

    def emit(msg: str) -> None:
        print(f"[Lark inspect] {msg}", flush=True)
        if line_sink:
            try:
                line_sink(f"[Lark] {msg}")
            except Exception:
                pass

    async def _one(title: str, md: str) -> bool:
        def _sync() -> dict:
            return send_markdown(webhook_url=url, markdown_content=md, title=title)

        try:
            r = await asyncio.to_thread(_sync)
            ok = r.get("status") == "success"
            if not ok:
                emit(f"发送失败: {r.get('error', r)}")
            return ok
        except Exception as e:
            emit(f"发送异常: {e!r}")
            return False

    async def _one_retry(title: str, md: str) -> bool:
        """每条消息最多 4 次尝试，间隔 2s / 4s / 8s（网络抖动容错）。"""
        delays = [2.0, 4.0, 8.0]
        for attempt in range(4):
            ok = await _one(title, md)
            if ok:
                return True
            if attempt < 3:
                emit(f"准备重试 ({attempt + 1}/3 次间隔 {delays[attempt]}s)…")
                await asyncio.sleep(delays[attempt])
        return False

    emit("开始向 Lark 推送巡检结果…")

    header_md = (
        f"**Kalaroko E2E 巡检已完成**\n\n"
        f"- 轮数：**{runs}** · 间隔：**{interval}s**\n"
        f"- 摘要模型（LLM_COMPLEX_MODEL）：`{summary_model or 'N/A'}`\n"
        f"- 含：各轮 Markdown 一至七节；多轮时另附 AI 综合分析（若已生成）。\n"
    )
    await _one_retry("巡检 · 概要", header_md)
    await asyncio.sleep(delay_sec)

    md_body = (markdown_report or "").strip()
    if md_body:
        parts = _chunk_text(md_body, chunk_chars)
        total = len(parts)
        for i, part in enumerate(parts, start=1):
            title = f"巡检 · Markdown 报告 ({i}/{total})"
            await _one_retry(title, part)
            await asyncio.sleep(delay_sec)
    else:
        emit("无 markdown_report，跳过报告正文推送")

    llm = (llm_analysis or "").strip()
    if llm:
        wrapped = f"## AI 综合分析\n\n{llm}"
        parts = _chunk_text(wrapped, chunk_chars)
        total = len(parts)
        for i, part in enumerate(parts, start=1):
            title = f"巡检 · AI 分析 ({i}/{total})"
            await _one_retry(title, part)
            await asyncio.sleep(delay_sec)

    emit("Lark 推送流程结束")
