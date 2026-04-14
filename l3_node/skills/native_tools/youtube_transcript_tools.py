"""
YouTube 字幕 — Python 原生工具（PyPI ``youtube-transcript-api``）。

使用与 requests/urllib3 一致的网络栈，**继承进程环境变量** ``HTTP_PROXY`` / ``HTTPS_PROXY``（及系统代理配置），
避免 Node stdio MCP 内 **fetch 忽略代理** 导致的 ``fetch failed``。

依赖：``pip install youtube-transcript-api``（见 ``core/requirements.txt``）。
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

YOUTUBE_NATIVE_TOOLS_LIST: list[dict[str, Any]] = [
    {
        "id": "core:youtube_transcript",
        "label": "core:youtube_transcript",
        "desc": (
            "【YouTube 字幕 · 必须优先】用户给出 YouTube 链接并要求总结、知识点、人生建议、字幕时，"
            "**必须先调用本工具**拉取字幕长文本（禁止仅用 mcp:fetch 抓页面壳；禁止为省轮次改投 core:submit_background_task）。"
            "依赖 Python 库 youtube-transcript-api，尊重 HTTP(S)_PROXY。"
            "JSON：**url**（必填）须为完整 `https://www.youtube.com/watch?v=...`、`/shorts/...` 或 `https://youtu.be/...`；"
            "**禁止**只传裸 video id。返回 ok、transcript、video_id；失败时返回 error。"
        ),
        "params": ["url"],
    },
]


def extract_youtube_video_id(url_or_id: str) -> str:
    s = (url_or_id or "").strip()
    if not s:
        raise ValueError("url 为空")
    if re.fullmatch(r"[a-zA-Z0-9_-]{10,12}", s) and "://" not in s and "/" not in s:
        return s
    m = re.search(
        r"(?:youtube\.com/watch\?[^#\s]*[&?]v=|youtube\.com/(?:embed|shorts|live)/|youtu\.be/)([a-zA-Z0-9_-]{6,})",
        s,
        re.I,
    )
    if m:
        return m.group(1)
    m2 = re.search(r"[?&]v=([a-zA-Z0-9_-]{6,})", s, re.I)
    if m2:
        return m2.group(1)
    raise ValueError(f"无法从输入解析 YouTube video id，请传完整 https 链接: {s[:160]}")


def get_youtube_transcript_payload(url: str, languages: list[str] | None = None) -> dict[str, Any]:
    """
    拉取字幕并拼成单字符串。失败返回 ok=False 与 error（可归因）。

    兼容 ``youtube-transcript-api`` **1.x**（``YouTubeTranscriptApi().fetch``）与旧版类方法 ``get_transcript``。
    """
    try:
        vid = extract_youtube_video_id(url)
    except ValueError as e:
        return {"ok": False, "error_class": "config", "error": str(e)}

    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        return {
            "ok": False,
            "error_class": "config",
            "error": "未安装 youtube-transcript-api，请执行: pip install youtube-transcript-api",
        }

    langs = list(
        languages
        or ["zh-Hans", "zh-CN", "zh-TW", "zh", "en", "en-US", "en-GB"]
    )
    lines: list[str] = []
    last_err = ""

    def _lines_from_fetched(fetched: Any) -> None:
        for seg in fetched or []:
            t = str(getattr(seg, "text", "") or "").strip()
            if not t and isinstance(seg, dict):
                t = str(seg.get("text") or "").strip()
            if t:
                lines.append(t)

    # 1.x：实例方法 fetch(video_id, languages=...)
    api = YouTubeTranscriptApi()
    if hasattr(api, "fetch"):
        try:
            fetched = api.fetch(vid, languages=tuple(langs))
            _lines_from_fetched(fetched)
        except Exception as e1:
            last_err = str(e1)
            logger.info("[youtube_transcript] fetch 指定语言失败，重试默认: %s", last_err[:240])
            try:
                fetched = api.fetch(vid)
                _lines_from_fetched(fetched)
            except Exception as e2:
                return {
                    "ok": False,
                    "error_class": "permanent" if "disabled" in str(e2).lower() else "transient",
                    "error": str(e2),
                    "video_id": vid,
                    "hint": last_err[:400] if last_err else "",
                }
    else:
        # 0.6.x 类方法
        data: list[dict[str, Any]] | None = None
        try:
            data = YouTubeTranscriptApi.get_transcript(vid, languages=langs)  # type: ignore[attr-defined]
        except Exception as e1:
            last_err = str(e1)
            try:
                data = YouTubeTranscriptApi.get_transcript(vid)  # type: ignore[attr-defined]
            except Exception as e2:
                return {
                    "ok": False,
                    "error_class": "transient",
                    "error": str(e2),
                    "video_id": vid,
                    "hint": last_err[:400],
                }
        for seg in data or []:
            if isinstance(seg, dict) and str(seg.get("text") or "").strip():
                lines.append(str(seg.get("text")).strip())

    if not lines:
        return {"ok": False, "error_class": "per_item", "error": "字幕列表为空或不可用", "video_id": vid}

    full = "\n".join(lines)
    return {
        "ok": True,
        "video_id": vid,
        "transcript": full,
        "char_count": len(full),
    }


def dispatch_youtube_transcript_core(tool_id: str, **kwargs: Any) -> dict[str, Any]:
    if tool_id != "core:youtube_transcript":
        return {"ok": False, "error_class": "config", "error": f"未知工具: {tool_id}"}
    url = str(kwargs.get("url") or kwargs.get("video_url") or "").strip()
    if not url and isinstance(kwargs.get("input"), dict):
        url = str(kwargs["input"].get("url") or "").strip()
    if not url:
        return {"ok": False, "error_class": "config", "error": "缺少 url（须为完整 YouTube https 链接）"}
    langs = kwargs.get("languages")
    if isinstance(langs, str):
        langs = [x.strip() for x in langs.split(",") if x.strip()]
    elif not isinstance(langs, list):
        langs = None
    return get_youtube_transcript_payload(url, languages=langs)
