"""
Kalaroko 巡检飞书推送 — **自建应用 Open API**（交互式卡片 + 回复盖楼）。

环境变量（必填 ``FEISHU_APP_SECRET``；其余可省略用下列默认）：
  FEISHU_APP_ID       — 自建应用 App ID（默认与仓库约定占位一致时请覆盖）
  FEISHU_APP_SECRET   — 自建应用 Secret（**勿提交 Git**，仅本机 / CI Secret）
  FEISHU_CHAT_ID      — 群 chat_id（``oc_...``）

说明：主卡片顶部为 Markdown **汇总表**（多轮 ``all_metrics_history``）；各轮完整 Markdown 快报通过 **reply** 挂在话题下。
表头与「巡检时间」列按 ``KALAROKO_REPORT_TZ`` 展示（默认北京时间 **UTC+8**），与 E2E 战报一致。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

# --- 飞书自建应用（Open API）---
# Secret 禁止硬编码进仓库：仅通过环境变量注入。
FEISHU_APP_ID = (os.environ.get("FEISHU_APP_ID") or "cli_a940990299f8ded2").strip()
FEISHU_APP_SECRET = (os.environ.get("FEISHU_APP_SECRET") or "").strip()
FEISHU_CHAT_ID = (
    os.environ.get("FEISHU_CHAT_ID") or "oc_8e1930be00682b87fc4411905b5bc5ef"
).strip()

_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
_MESSAGES_URL = "https://open.feishu.cn/open-apis/im/v1/messages"


def inspection_lark_open_api_ready() -> bool:
    """是否已配置 Open API（可替代原 Webhook 巡检推送）。"""
    return bool(FEISHU_APP_ID and FEISHU_APP_SECRET and FEISHU_CHAT_ID)


def inspection_lark_webhook_url() -> str | None:
    """兼容旧 import：巡检仅走 Open API（``inspection_lark_open_api_ready`` / ``FEISHU_*``），恒为 ``None``。"""
    return None


def _post_json(url: str, body: dict[str, Any], *, timeout_sec: float = 45.0) -> dict[str, Any]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw) if raw else {}


def _get_feishu_token_sync() -> str:
    """tenant_access_token（同步）。"""
    j = _post_json(
        _TOKEN_URL,
        {"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
        timeout_sec=25.0,
    )
    if int(j.get("code", -1)) != 0:
        logger.error("[kalaroko_inspect_lark] tenant_token 失败: %s", j)
        return ""
    tok = (j.get("tenant_access_token") or "").strip()
    if not tok:
        logger.error("[kalaroko_inspect_lark] tenant_token 空响应: %s", j)
    return tok


def _send_feishu_card_sync(token: str, card: dict[str, Any]) -> str:
    """发送交互式卡片（schema 2.0），返回 message_id。"""
    url = f"{_MESSAGES_URL}?receive_id_type=chat_id"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "receive_id": FEISHU_CHAT_ID,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60.0) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    j = json.loads(raw) if raw else {}
    if int(j.get("code", -1)) != 0:
        logger.error("[kalaroko_inspect_lark] 发送卡片失败: %s", j)
        return ""
    mid = (j.get("data") or {}).get("message_id") or ""
    if not mid:
        logger.error("[kalaroko_inspect_lark] 无 message_id: %s", j)
    return str(mid)


def _reply_feishu_message_sync(token: str, message_id: str, text: str) -> None:
    """在主消息下回复一条文本（盖楼）。"""
    url = f"{_MESSAGES_URL}/{message_id}/reply"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "msg_type": "text",
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60.0) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    j = json.loads(raw) if raw else {}
    if int(j.get("code", -1)) != 0:
        logger.warning("[kalaroko_inspect_lark] reply 失败: %s", j)


def _chunk_text(text: str, max_chars: int) -> list[str]:
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


def _kalaroko_report_tzinfo() -> tuple[Any, str]:
    """
    与 ``scripts/test_kalaroko_default_scenarios_e2e._kalaroko_report_tzinfo`` 对齐：
    默认北京时间 UTC+8；``KALAROKO_REPORT_TZ=malaysia`` 等为吉隆坡。
    """
    raw = (os.environ.get("KALAROKO_REPORT_TZ") or "utc8").strip().lower()
    malaysia_keys = frozenset(
        {"malaysia", "my", "asia/kuala_lumpur", "kuala_lumpur"},
    )
    try:
        from zoneinfo import ZoneInfo

        if raw in malaysia_keys:
            return ZoneInfo("Asia/Kuala_Lumpur"), "马来西亚时间 (UTC+8)"
        return ZoneInfo("Asia/Shanghai"), "北京时间 (UTC+8)"
    except Exception:
        tz8 = timezone(timedelta(hours=8))
        if raw in malaysia_keys:
            return tz8, "马来西亚时间 (UTC+8)"
        return tz8, "北京时间 (UTC+8)"


def _format_inspection_round_time_cell(raw: Any) -> str:
    """
    将 ``captured_at``（多为 UTC 的 ISO8601）换算为报告时区（默认 **UTC+8**）后输出 ``HH:MM:SS``。
    与战报正文 ``KALAROKO_REPORT_TZ`` 一致；解析失败为 ``-``。
    """
    try:
        tz, _tz_label = _kalaroko_report_tzinfo()
        raw_time = (raw or "").strip() if isinstance(raw, str) else str(raw or "").strip()
        if not raw_time:
            return "-"
        s = raw_time
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(tz).strftime("%H:%M:%S")
    except Exception:
        return "-"


def _cell_round_trip(row: dict[str, Any], load_key: str, ok_key: str) -> str:
    ok = row.get(ok_key)
    ms = row.get(load_key)
    if ok is False:
        return "🔴 失败"
    if ms is None:
        return "🔴 N/A"
    try:
        sec = float(ms) / 1000.0
        return f"🟢 {sec:.2f}s"
    except (TypeError, ValueError):
        return "🔴 N/A"


def _build_summary_table_md(hist: list[dict[str, Any]]) -> str:
    """与 ``_extract_comparison_metrics`` 字段对齐的 Markdown 表。"""
    tz, tz_label = _kalaroko_report_tzinfo()
    now = datetime.now(timezone.utc).astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"**📅 巡检时间（{tz_label}）：{now}**",
        "",
        "| 巡检轮次 | 巡检时间 | 首页 | Tongits King | Color Blitz | Royal Pusoy |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for i, row in enumerate(hist or []):
        r = i + 1
        time_cell = _format_inspection_round_time_cell(row.get("captured_at"))
        home = _cell_round_trip(row, "page_load", "page_success")
        tg = _cell_round_trip(row, "tongits_king_load", "tongits_king_success")
        cb = _cell_round_trip(row, "color_blitz_load", "color_blitz_success")
        rp = _cell_round_trip(row, "royal_pusoy_load", "royal_pusoy_success")
        lines.append(f"| {r} | {time_cell} | {home} | {tg} | {cb} | {rp} |")
    if not hist:
        lines.append("| — | - | （无多轮指标） | — | — | — |")
    return "\n".join(lines)


def _card_payload_v2(
    *,
    table_md: str,
    llm_analysis: str | None,
    footer_note: str,
) -> dict[str, Any]:
    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": table_md},
    ]
    llm = (llm_analysis or "").strip()
    if llm:
        # 卡片体过大易被拒；长分析放盖楼由调用方决定，此处只收前段
        cap = int(os.environ.get("KALAROKO_LARK_CARD_LLM_MAX_CHARS", "6000") or "6000")
        cap = max(500, min(cap, 12000))
        if len(llm) > cap:
            llm = llm[:cap] + "\n\n…（完整分析见下方回复）"
        elements.append({"tag": "hr"})
        elements.append({"tag": "markdown", "content": f"**🧠 AI 综合诊断**\n{llm}"})
    elements.append({"tag": "hr"})
    # schema 2.0 不支持 tag:note（Err 200861），底部提示改用 markdown
    elements.append({"tag": "markdown", "content": footer_note})
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": "🚨 Kalaroko PH 7x24 巡检战报"},
            "template": "blue",
        },
        "body": {"elements": elements},
    }


async def send_kalaroko_inspection_to_lark(
    *,
    markdown_report: str | None,
    llm_analysis: str | None,
    runs: int,
    interval: int,
    summary_model: str | None,
    line_sink: Callable[[str], None] | None = None,
    chunk_chars: int = 18000,
    delay_sec: float = 0.35,
    all_metrics_history: list[dict[str, Any]] | None = None,
) -> None:
    """
    发送主卡片（表 + 可选 AI 摘要）+ **reply** 挂载各轮 Markdown 详单。
    ``chunk_chars``：单条回复最大字符（飞书文本约 20k 上限，保守 18k）。
    """
    if not inspection_lark_open_api_ready():
        logger.info(
            "[kalaroko_inspect_lark] 未配置 FEISHU_APP_SECRET / FEISHU_CHAT_ID，跳过 Open API 推送"
        )
        return

    def emit(msg: str) -> None:
        print(f"[Lark inspect] {msg}", flush=True)
        if line_sink:
            try:
                line_sink(f"[Lark] {msg}")
            except Exception:
                pass

    emit("开始向飞书 Open API 推送巡检（卡片 + 回复盖楼）…")
    try:
        token = await asyncio.to_thread(_get_feishu_token_sync)
        if not token:
            emit("推送失败: 无 tenant_access_token")
            return
        emit("已获取 tenant token，正在组装主卡片…")

        hist = list(all_metrics_history or [])
        table_md = _build_summary_table_md(hist)
        meta = (
            f"\n\n> 轮数：**{runs}** · 间隔：**{interval}s** · 模型：`{summary_model or 'N/A'}`"
        )
        table_md = table_md + meta
        card = _card_payload_v2(
            table_md=table_md,
            llm_analysis=llm_analysis,
            footer_note=(
                "💡 各轮次完整 Markdown 快报与耗时追踪见本消息 **回复（话题流）** 👇"
            ),
        )

        parent_id = await asyncio.to_thread(_send_feishu_card_sync, token, card)
        if not parent_id:
            emit("推送失败: 发送主卡片失败")
            return
        emit(f"主卡片已发送 message_id={parent_id!r}，开始话题盖楼…")

        md_body = (markdown_report or "").strip()
        if md_body:
            parts = _chunk_text(md_body, max(2000, min(int(chunk_chars), 19000)))
            n = len(parts)
            for i, part in enumerate(parts, start=1):
                header = f"【巡检详单 {i}/{n}】\n\n"
                body = header + part
                await asyncio.to_thread(
                    _reply_feishu_message_sync, token, parent_id, body
                )
                emit(
                    f"盖楼进度 {i}/{n} 已发送（本段 {len(body)} 字符）"
                )
                if i < n:
                    await asyncio.sleep(max(0.0, float(delay_sec)))

        emit(f"推送完成 message_id={parent_id!r}")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")[:800]
        except Exception:
            body = ""
        logger.exception("[kalaroko_inspect_lark] HTTP 错误: %s body=%s", e, body)
        emit(f"HTTP 异常: {e!r}")
    except Exception as e:
        logger.exception("[kalaroko_inspect_lark] 推送异常: %s", e)
        emit(f"异常: {e!r}")


async def send_lark_alert_card_and_thread(
    *,
    title: str,
    markdown: str,
    chunk_chars: int = 16000,
    delay_sec: float = 0.35,
) -> None:
    """
    调度器 / 晨报等：短标题卡片 + 正文盖楼（与巡检共用同一套 App 凭证）。
    """
    if not inspection_lark_open_api_ready():
        logger.warning("[kalaroko_inspect_lark] Open API 未配置，跳过告警推送: %s", title)
        return

    token = await asyncio.to_thread(_get_feishu_token_sync)
    if not token:
        logger.error("[kalaroko_inspect_lark] 告警跳过：无 token (%s)", title)
        return
    card = {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title[:100]},
            "template": "orange",
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": "**正文见下方回复（话题流）**",
                }
            ],
        },
    }
    mid = await asyncio.to_thread(_send_feishu_card_sync, token, card)
    if not mid:
        return
    parts = _chunk_text(markdown or "", max(2000, min(chunk_chars, 19000))) or [""]
    n = len(parts)
    for i, part in enumerate(parts, start=1):
        hdr = f"**{title}** ({i}/{n})\n\n" if n > 1 else f"**{title}**\n\n"
        await asyncio.to_thread(_reply_feishu_message_sync, token, mid, hdr + part)
        if i < n:
            await asyncio.sleep(max(0.0, float(delay_sec)))
