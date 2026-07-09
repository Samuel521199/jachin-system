"""
L3 本地 WebSocket 服务

监听 127.0.0.1:18981（189xx 系列，与 L2 18888、Sensory 18881 互不冲突），
接收前端 JSON 消息，交给 run_agent 执行，流式回传 chunk、thought、action、observation、answer。

Lark 接入时每次消息新建连接，需通过 chat_id 持久化会话，否则「同意」等回复无法获取上一轮 JD 配置。

【终端- Lark 镜像】终端为主（笔记本），Lark 为从（显示器）：
- 终端可 subscribe_mirror(lark_chat_id) 订阅某 Lark 会话的实时流
- Lark 发消息时：广播 mirror_input 到终端，再执行，回复同时给 Lark 与终端
- 终端发消息时（带 chat_id）：执行后回复给终端，并 POST 到 LARK_MIRROR_PUSH_URL 同步到 Lark
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
import uuid
import zlib
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from l3_node.llm_client import LiteLLMEngine

logger = logging.getLogger(__name__)

from l3_node.lark_session import load_lark_session as _load_lark_session, save_lark_session as _save_lark_session

# 含大段 base64 的附件帧可达数 MB，同步 json.loads 会长时间阻塞 asyncio 事件环，导致全节点 WS「假死」
_WS_JSON_LARGE_BYTES = 150_000
_EARLY_YIELD_TOKENS = ("收到，", "稍等，", "我想想，")
_EARLY_YIELD_COMPLEX_RE = re.compile(
    r"(全部|批量|所有|每个|目录|文件夹|报告|汇总|分析|导出|重构|改造|数据库|SQL|接口|权限|部署|流水线|CI|日志|排查|修复)"
)


async def _ws_json_loads(raw: str | bytes) -> Any:
    if isinstance(raw, bytes):
        s = raw.decode("utf-8", errors="replace")
    else:
        s = raw
    if len(s) >= _WS_JSON_LARGE_BYTES:
        return await asyncio.to_thread(json.loads, s)
    return json.loads(s)


def _ws_msg_is_local_voice(msg: dict) -> bool:
    """本地桌面语音陪伴态：使用 session_id 记账，但不应被当成 Lark 镜像会话。"""
    origin = str(msg.get("origin") or msg.get("source") or "").strip().lower()
    sig = msg.get("implicit_signals")
    sig = sig if isinstance(sig, dict) else {}
    source = str(sig.get("source") or "").strip().lower()
    return (
        origin in {"desktop_voice_companion", "desktop_voice", "voice_latency_bench"}
        or source in {"desktop_voice_companion", "desktop_voice", "voice_latency_bench"}
        or bool(sig.get("desktop_companion"))
        or bool(sig.get("local_voice_session"))
    )


def _looks_like_lark_chat_id(value: str) -> bool:
    cid = (value or "").strip()
    return cid.startswith(("oc_", "ou_", "on_", "om_"))


def _ws_msg_session_key(msg: dict) -> str:
    """桌面 Omni 多会话：本地语音优先 session_id；Lark 镜像优先 chat_id。"""
    if _ws_msg_is_local_voice(msg):
        return str(msg.get("session_id") or msg.get("chat_id") or "").strip()
    return str(msg.get("chat_id") or msg.get("session_id") or "").strip()


def _ws_msg_lark_chat_id(msg: dict, session_key: str = "") -> str:
    """只有明确的 Lark 会话才返回 chat_id；本地语音 session 永不触发镜像/push。"""
    if _ws_msg_is_local_voice(msg):
        return ""
    raw = str(msg.get("chat_id") or "").strip()
    if _looks_like_lark_chat_id(raw):
        return raw
    if _looks_like_lark_chat_id(session_key):
        return session_key
    return ""




def _is_ws_voice_fast_lane(intent: str, implicit_signals: dict | None, msg: dict | None = None) -> bool:
    return False


def _legacy_ws_voice_fast_lane_enabled() -> bool:
    """The pre-kernel voice fast lane has been retired."""
    return False


def _normalize_ws_implicit_signals_for_kernel(
    implicit_signals: dict | None,
    *,
    local_voice_session: bool,
) -> dict | None:
    """Remove legacy bypass flags so voice/text enter the same kernel path."""
    if not isinstance(implicit_signals, dict):
        return implicit_signals
    if not local_voice_session:
        return implicit_signals
    cleaned = dict(implicit_signals)
    removed: list[str] = []
    for key in (
        "voice_fast_lane",
        "skip_context_retrieval",
        "skip_context_sniffer",
        "skip_gateway_enrich",
        "skip_experience_rag",
        "voice_allow_template_reply",
    ):
        if key in cleaned:
            removed.append(key)
            cleaned.pop(key, None)
    cleaned["cognitive_kernel_required"] = True
    cleaned["voice_fast_lane_disabled_by_kernel"] = True
    if removed:
        cleaned["kernel_removed_bypass_flags"] = removed
    return cleaned


def _voice_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _voice_evidence_gate_reply(intent: str, implicit_signals: dict | None) -> tuple[str | None, str]:
    sig = implicit_signals if isinstance(implicit_signals, dict) else {}
    if not sig:
        return None, ""
    text = (intent or "").strip()
    stt_source = str(sig.get("voice_stt_source") or "").strip()
    finalized_value = sig.get("voice_stt_finalized")
    finalized_known = finalized_value is not None and str(finalized_value).strip() != ""
    finalized = _voice_bool(finalized_value)
    provisional = _voice_bool(sig.get("voice_stt_provisional"))
    hotword_dominated = _voice_bool(sig.get("voice_stt_hotword_dominated"))
    lane = str(sig.get("voice_dispatch_lane") or "").strip().lower()
    tier = str(sig.get("voice_dispatch_tier") or "").strip().upper()
    intent_class = str(sig.get("voice_intent_class") or "").strip().upper()
    executable_lane = lane in {"foreground", "background_submit"} or intent_class in {"TASK_SYNC", "TASK_ASYNC"}
    taskish_text = bool(re.search(r"打开|启动|找到|查找|切到|进入|发送|发消息|给.+发|删除|创建|修改|导出|计算器|计算|chrome|lark|飞书|vivian|neil|ethan", text, re.I))

    if hotword_dominated:
        return (
            f"我刚才听到的是“{text}”，但这段语音像是被热词影响了。你可以再说一遍，或者确认这就是你要做的吗？",
            "hotword_dominated",
        )
    if stt_source == "jvs_stream_ws" or provisional or (finalized_known and not finalized):
        if executable_lane or taskish_text or tier != "CHIT_CHAT":
            return (
                f"我刚才只拿到了临时识别结果“{text}”，还不能直接执行。你再说一遍，或者确认要执行这件事吗？",
                "non_final_voice_stt",
            )
    return None, ""
_WS_VOICE_TEMPLATE_TASK_WORDS = (
    "\u6253\u5f00", "\u542f\u52a8", "\u5173\u95ed", "\u53d1\u9001", "\u53d1\u7ed9", "\u6d88\u606f",
    "\u6587\u4ef6", "\u76ee\u5f55", "\u603b\u7ed3", "\u62a5\u544a", "\u626b\u63cf", "\u6e05\u7406",
    "\u63d0\u9192", "\u95f9\u949f", "\u641c\u7d22", "\u67e5\u8be2", "\u5929\u6c14", "\u7535\u8111",
    "\u9879\u76ee", "\u4ee3\u7801", "\u5220\u9664", "\u521b\u5efa", "\u4fee\u6539", "\u5bfc\u51fa",
    "lark", "feishu", "chrome", "cursor", "code", "vivian",
)
_WS_VOICE_TEMPLATE_ALIASES: dict[str, tuple[str, ...]] = {
    "\u4f60\u597d": ("\u6211\u5728\u3002", "\u5728\u5462\u3002", "\u542c\u7740\u5462\u3002", "\u600e\u4e48\u5566\uff1f"),
    "\u4f60\u597d\u5440": ("\u6211\u5728\u3002", "\u5728\u5462\u3002", "\u542c\u7740\u5462\u3002", "\u600e\u4e48\u5566\uff1f"),
    "\u54c8\u55bd": ("\u6211\u5728\u3002", "\u5728\u5462\u3002", "\u542c\u7740\u5462\u3002", "\u600e\u4e48\u5566\uff1f"),
    "hello": ("\u6211\u5728\u3002", "\u5728\u5462\u3002", "\u542c\u7740\u5462\u3002", "\u600e\u4e48\u5566\uff1f"),
    "hi": ("\u6211\u5728\u3002", "\u5728\u5462\u3002", "\u542c\u7740\u5462\u3002", "\u600e\u4e48\u5566\uff1f"),
    "\u5728\u5417": ("\u6211\u5728\u3002", "\u5728\u5462\u3002", "\u542c\u7740\u5462\u3002", "\u600e\u4e48\u5566\uff1f"),
    "\u5728\u561b": ("\u6211\u5728\u3002", "\u5728\u5462\u3002", "\u542c\u7740\u5462\u3002", "\u600e\u4e48\u5566\uff1f"),
    "\u4f60\u5728\u5417": ("\u6211\u5728\u3002", "\u5728\u5462\u3002", "\u542c\u7740\u5462\u3002", "\u600e\u4e48\u5566\uff1f"),
    "\u542c\u5f97\u5230\u5417": ("\u542c\u5230\u4e86\u3002", "\u542c\u5f97\u5f88\u6e05\u695a\u3002", "\u6211\u5728\u542c\u3002"),
    "\u542c\u89c1\u5417": ("\u542c\u5230\u4e86\u3002", "\u542c\u5f97\u5f88\u6e05\u695a\u3002", "\u6211\u5728\u542c\u3002"),
    "\u5582": ("\u6211\u5728\u3002", "\u542c\u7740\u5462\u3002"),
    "\u55ef": ("\u55ef\uff0c\u6211\u5728\u3002", "\u542c\u7740\u5462\u3002"),
    "\u6309\u8bb2\u8bdd": ("\u6211\u5728\u3002", "\u542c\u7740\u5462\u3002"),
    "\u6309\u4f4f\u8bb2\u8bdd": ("\u6211\u5728\u3002", "\u542c\u7740\u5462\u3002"),
    "\u6309\u7740\u8bb2\u8bdd": ("\u6211\u5728\u3002", "\u542c\u7740\u5462\u3002"),
    "\u4e0d\u8bb2\u8bdd": ("\u6211\u5728\u3002", "\u542c\u7740\u5462\u3002"),
    "\u4f60\u4e0d\u8bb2\u8bdd": ("\u6211\u5728\u3002", "\u542c\u7740\u5462\u3002"),
    "\u4f60\u600e\u4e48\u4e0d\u8bb2\u8bdd": ("\u6211\u5728\u3002", "\u521a\u521a\u5728\u542c\u3002"),
    "\u4f60\u600e\u4e48\u4e0d\u8bf4\u8bdd": ("\u6211\u5728\u3002", "\u521a\u521a\u5728\u542c\u3002"),
    "\u8bf4\u8bdd": ("\u6211\u5728\u3002", "\u542c\u7740\u5462\u3002"),
    "\u8bb2\u8bdd": ("\u6211\u5728\u3002", "\u542c\u7740\u5462\u3002"),
}


def _sanitize_ws_voice_fast_lane_intent(intent: str) -> str:
    """修掉 STT 在中文句尾附带的单个英文字母噪声，如“今晚吃什么V”。"""
    text = str(intent or "").strip()
    if not text:
        return ""
    text = re.sub(r"(?<=[\u4e00-\u9fff])[A-Za-z]$", "", text).strip()
    return text


def _voice_fast_lane_model() -> str:
    raw = (
        os.environ.get("JACHIN_VOICE_FAST_LANE_MODEL")
        or os.environ.get("VOICE_FAST_LANE_MODEL")
        or "dashscope/qwen3.5-flash"
    ).strip()
    return raw if raw.startswith(("dashscope/", "qwen/", "openai/")) else f"dashscope/{raw}"


def _clean_ws_voice_fast_reply(text: str) -> str:
    value = str(text or "").strip()
    value = re.sub(r"```[\s\S]*?```", " ", value).strip("` \t\r\n")
    value = re.sub(r"\s+", " ", value).strip()
    if not value:
        return ""
    parts = re.findall(r"[^。！？!?\n]+[。！？!?]?", value)
    spoken = "".join(part.strip() for part in parts[:2]).strip() if parts else value
    return spoken[:90].strip()


def _fallback_ws_voice_fast_reply(intent: str, implicit_signals: dict | None = None) -> str:
    text = _sanitize_ws_voice_fast_lane_intent(intent)
    if _is_ws_voice_presence_ack_intent(text):
        return "我在。"
    if any(w in text for w in ("吃", "晚饭", "中饭", "午饭", "早饭", "喝")):
        return "可以吃点热乎清淡的，比如面、粥或者简单盖饭。"
    if any(w in text for w in ("累", "烦", "难受", "不开心", "压力")):
        return "我在，先缓一口气，慢慢说。"
    return "我听到了，刚刚有点卡；你再说一句，我马上接。"

def _normalize_ws_voice_template_text(intent: str, implicit_signals: dict | None = None) -> str:
    sig = implicit_signals if isinstance(implicit_signals, dict) else {}
    raw = str(sig.get("voice_raw_stt_text") or intent or "")
    text = raw.strip().lower()
    text = re.sub(r"<\|.*?\|>", "", text)
    text = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", "", text)
    text = re.sub(r"[\s\u3002\uff0c\uff01\uff1f\u3001\uff1b\uff1a,.!?;:'\"`~*_#()\[\]{}<>-]+", "", text)
    return text


def _pick_ws_voice_template_reply(intent: str, implicit_signals: dict | None = None) -> str | None:
    sig = implicit_signals if isinstance(implicit_signals, dict) else {}
    raw = str(intent or "").strip()
    if not raw or len(raw) > 24:
        return None
    kind = str(sig.get("voice_fast_lane_kind") or "").strip().lower()
    allow_template = sig.get("voice_allow_template_reply")
    if kind and kind != "presence_template":
        return None
    if allow_template is False or str(allow_template).strip().lower() in {"0", "false", "no"}:
        return None
    joined = raw + "\n" + str(sig.get("voice_raw_stt_text") or "")
    joined_lower = joined.lower()
    if any(word.lower() in joined_lower for word in _WS_VOICE_TEMPLATE_TASK_WORDS):
        return None
    key = _normalize_ws_voice_template_text(raw, implicit_signals)
    if not key:
        return None
    pool = _WS_VOICE_TEMPLATE_ALIASES.get(key)
    if not pool:
        # Conservative fuzzy handling for STT repetition/noise around presence checks.
        if len(key) <= 12 and ("\u4f60\u597d" in key or "\u5728\u5417" in key or "\u8bb2\u8bdd" in key or "\u8bf4\u8bdd" in key):
            pool = ("\u6211\u5728\u3002", "\u542c\u7740\u5462\u3002")
    if not pool:
        return None
    idx = zlib.crc32(f"{key}|{time.time_ns()}|{uuid.uuid4().hex}".encode("utf-8")) % len(pool)
    return pool[idx]

def _is_ws_voice_presence_ack_intent(intent: str) -> bool:
    text = (intent or "").strip()
    if not text or len(text) > 40:
        return False
    question_words = (
        "\u4ec0\u4e48", "\u600e\u4e48", "\u4e3a\u4ec0\u4e48", "\u54ea\u4e2a", "\u54ea\u91cc",
        "\u591a\u5c11", "\u51e0", "\u5417", "\uff1f", "?", "\u5403", "\u559d", "\u8981\u4e0d\u8981",
        "\u5e94\u8be5", "\u63a8\u8350",
    )
    if any(w in text for w in question_words):
        return False
    presence_words = (
        "\u542c\u6211\u8bb2\u8bdd", "\u542c\u6211\u8bf4", "\u8ddf\u6211\u8bf4", "\u966a\u6211",
        "\u8bf4\u70b9\u8bdd", "\u8bb2\u8bdd", "\u8bf4\u8bdd", "\u5728\u5417", "\u4f60\u5728\u5417",
    )
    return any(w in text for w in presence_words)


def _voice_light_task_context_prompt(implicit_signals: dict | None) -> str:
    sig = implicit_signals if isinstance(implicit_signals, dict) else {}
    if not bool(sig.get("inject_light_task_context")):
        return ""
    ctx = sig.get("light_task_context")
    if not isinstance(ctx, dict):
        return ""
    tasks = ctx.get("active_tasks")
    if not isinstance(tasks, list) or not tasks:
        return ""
    compact_tasks: list[dict[str, str]] = []
    for item in tasks[:3]:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("id") or "").strip()[:80]
        title = str(item.get("title") or "").strip()[:120]
        if task_id or title:
            compact_tasks.append({"id": task_id, "title": title})
    if not compact_tasks:
        return ""
    focused_task_id = str(ctx.get("focused_task_id") or "").strip()[:80] or None
    summary = str(ctx.get("summary") or sig.get("task_context_summary") or "").strip()[:160] or None
    payload = {
        "active_tasks": compact_tasks,
        "focused_task_id": focused_task_id,
        "summary": summary,
    }
    return (
        "\n\u8f7b\u91cf\u4efb\u52a1\u72b6\u6001\uff08\u4ec5\u7528\u4e8e\u4fdd\u6301\u966a\u4f34\u6001\u7684\u8fde\u7eed\u611f\uff0c\u4e0d\u8981\u5c55\u5f00\u68c0\u7d22\u6216\u7f16\u9020\u8fdb\u5ea6\uff09\uff1a"
        + json.dumps(payload, ensure_ascii=False)
        + "\n\u5982\u679c\u7528\u6237\u63d0\u5230\u8fd9\u4e9b\u4efb\u52a1\uff0c\u53ef\u4ee5\u7b80\u77ed\u627f\u63a5\u5b83\u4eec\u4ecd\u5728\u540e\u53f0\u4fdd\u6301\u72b6\u6001\uff1b\u4e0d\u8981\u7f16\u9020\u5b8c\u6210\u767e\u5206\u6bd4\u3001\u6b65\u9aa4\u6216\u7ed3\u679c\u3002"
    )


def _voice_fast_lane_messages(intent: str, implicit_signals: dict | None = None) -> list[dict[str, str]]:
    sig = implicit_signals if isinstance(implicit_signals, dict) else {}
    fast_lane_kind = str(sig.get("voice_fast_lane_kind") or "chat_direct").strip().lower()
    task_context_prompt = _voice_light_task_context_prompt(implicit_signals)
    kind_prompt = ""
    if fast_lane_kind == "light_query":
        kind_prompt = "\u5f53\u524d\u8def\u7531\u7c7b\u578b\u662f\u8f7b\u95ee\u7b54\uff0c\u4e0d\u662f presence ack\uff1b\u5fc5\u987b\u76f4\u63a5\u56de\u7b54\u7528\u6237\u95ee\u7684\u95ee\u9898\uff0c\u7edd\u5bf9\u4e0d\u8981\u53ea\u8bf4\u2018\u6211\u5728\u2019\u6216\u2018\u542c\u7740\u5462\u2019\u3002"
    elif fast_lane_kind == "presence_template":
        kind_prompt = "\u5f53\u524d\u8def\u7531\u7c7b\u578b\u662f presence ack\uff1b\u7528\u6237\u4e3b\u8981\u662f\u5728\u786e\u8ba4\u4f60\u662f\u5426\u5728\u7ebf\uff0c\u56de\u590d\u8981\u6781\u77ed\u3002"
    system_prompt = (
        "\u4f60\u662f Jachin \u7684\u966a\u4f34\u6001\u8bed\u97f3\u52a9\u624b\u3002\u5f53\u524d\u662f\u8bed\u97f3\u95f2\u804a\u5feb\u8def\u5f84\u3002"
        "\u76f4\u63a5\u3001\u81ea\u7136\u3001\u6e29\u67d4\u5730\u7528\u4e2d\u6587\u77ed\u7b54\uff0c1\u52302\u53e5\u3002\u7528\u6237\u5982\u679c\u95ee\u4e86\u5177\u4f53\u95ee\u9898\uff0c\u5fc5\u987b\u56de\u7b54\u95ee\u9898\u672c\u8eab\uff0c"
        "\u4e0d\u8981\u53ea\u8bf4\u6211\u5728\u3001\u597d\u5440\u3001\u542c\u7740\u5462\u3002\u53ea\u6709\u7528\u6237\u53ea\u662f\u53eb\u4f60\u966a\u4f34\u6216\u786e\u8ba4\u4f60\u5728\u65f6\uff0c\u624d\u53ef\u7528\u5f88\u77ed\u7684\u8fde\u63a5\u8bed\u3002"
        "\u4e0d\u8981\u5c55\u793a\u63a8\u7406\uff0c\u4e0d\u8981\u8c03\u7528\u5de5\u5177\u3002\u6ca1\u6709\u8f7b\u91cf\u4efb\u52a1\u72b6\u6001\u65f6\uff0c\u4e0d\u8981\u8bf4\u4f60\u6b63\u5728\u5904\u7406\u4efb\u52a1\u3002"
        + kind_prompt
    )
    return [
        {"role": "system", "content": system_prompt + task_context_prompt},
        {"role": "user", "content": (intent or "").strip()},
    ]

def _pick_early_yield_token(intent: str, implicit_signals: dict | None) -> str | None:
    """
    Early Yielding:
    - 复杂请求先吐一个极短语气词 chunk，优先打破“死寂”并触发 TTS。
    - 简单闲聊/快车道不触发，避免无意义前缀。
    """
    text = (intent or "").strip()
    if not text or text.startswith("/"):
        return None
    sig = implicit_signals if isinstance(implicit_signals, dict) else {}
    # 快车道（CHIT_CHAT）不需要 Early Yield。
    if bool(sig.get("voice_fast_lane")) or bool(sig.get("skip_context_retrieval")):
        return None
    tier = str(sig.get("voice_dispatch_tier") or "").upper()
    lane = str(sig.get("voice_dispatch_lane") or "").lower()
    intent_class = str(sig.get("voice_intent_class") or "").upper()
    likely_complex = (
        tier == "LONG_TASK"
        or lane in ("foreground", "background_submit")
        or intent_class in ("TASK_SYNC", "TASK_ASYNC")
        or (len(text) >= 42 and bool(_EARLY_YIELD_COMPLEX_RE.search(text)))
    )
    if not likely_complex:
        return None
    idx = zlib.crc32(text.encode("utf-8")) % len(_EARLY_YIELD_TOKENS)
    return _EARLY_YIELD_TOKENS[idx]


def _coerce_ws_intent_and_inline_attachments(msg: dict) -> tuple[str, list[dict[str, Any]]]:
    """
    解析 ``intent`` / ``content``。

    - 现网：``content`` 为字符串，与 ``intent`` 二选一作为用户输入。
    - 扩展：``content`` 为 OpenAI 多模态列表（text + image_url）时，抽取文案并生成与
      ``attachments_metadata`` 兼容的内联附件（经 ``multimodal_attachments.build_openai_user_content``）。
    """
    intent_raw = msg.get("intent")
    content = msg.get("content")
    if isinstance(content, list):
        texts: list[str] = []
        images: list[dict[str, Any]] = []
        for idx, part in enumerate(content):
            if not isinstance(part, dict):
                continue
            pt = str(part.get("type") or "").lower()
            if pt == "text":
                texts.append(str(part.get("text") or ""))
            elif pt == "image_url":
                iu = part.get("image_url")
                u = ""
                if isinstance(iu, dict):
                    u = str(iu.get("url") or "").strip()
                elif isinstance(iu, str):
                    u = iu.strip()
                if not u:
                    continue
                is_png = "image/png" in u[:120].lower() or "png" in u[:40].lower()
                suf = ".png" if is_png else ".jpg"
                # data URL 勿用 len(整串) 作为 size_bytes：trim 门限按「字节」计，整串偏大约 4/3 会误丢图
                _sz_decl = len(u)
                if u.strip().lower().startswith("data:image/") and ";base64" in u.lower():
                    try:
                        _comma = u.index(",")
                        _b64 = re.sub(r"\s+", "", u[_comma + 1 :])
                        _sz_decl = max((len(_b64) * 3) // 4, 1)
                    except ValueError:
                        pass
                images.append(
                    {
                        "name": f"inline_image_{idx}{suf}",
                        "mime": "image/png" if is_png else "image/jpeg",
                        "has_image": True,
                        "size_bytes": _sz_decl,
                        "image_url": {"url": u},
                    }
                )
        joined = "\n".join(texts).strip()
        if isinstance(intent_raw, str) and intent_raw.strip():
            intent_out = intent_raw.strip()
            if joined and joined not in intent_out:
                intent_out = f"{intent_out}\n{joined}"
        else:
            intent_out = joined
        return intent_out, images
    if isinstance(intent_raw, str) and intent_raw.strip():
        return intent_raw.strip(), []
    if isinstance(content, str):
        return content.strip(), []
    if content is None:
        return "", []
    return str(content).strip(), []


# 终端-Lark 镜像：chat_id -> 订阅该会话的 WebSocket 集合（终端连接）
_mirror_subscribers: dict[str, set] = {}
_mirror_subscribers_lock = asyncio.Lock()
# LARK_MIRROR_PUSH_URL：终端发消息后，可 POST 到独立 lark_bot webhook；未配置或默认 localhost:5000 时，
# 若已配置 Lark 凭证则优先走 Open API 直连（与长连接模式一致，避免 5000 被占用或非 webhook 返回 503）。
_DEFAULT_MIRROR_PUSH = "http://127.0.0.1:5000/api/mirror-push"

WS_HOST = "127.0.0.1"
WS_PORT = 18981  # 189xx 系列，与 L2(18888)、Sensory(18881) 分离


def _resolve_ws_engine(engine: Optional["LiteLLMEngine"]) -> Optional["LiteLLMEngine"]:
    """每轮对话解析当前引擎：--gateway 预热线擎后可能在 engine_ref 内热切换为 L2 下发引擎。"""
    try:
        from l3_node.agent_ref import engine_ref

        cur = engine_ref.get("engine")
        if cur is not None:
            return cur
    except Exception:
        pass
    return engine


async def _send_safe(websocket, payload: dict) -> None:
    """安全发送，忽略连接已关闭等异常。"""
    try:
        await websocket.send(json.dumps(payload, ensure_ascii=False))
    except Exception as e:
        logger.debug("WebSocket send failed: %s", e)


async def _maybe_push_memory_compact_suggest(websocket) -> None:
    """[已停用] 原：定时推送 JSON 梦境合并横幅；Memory Nexus 下不再推送。"""
    logger.debug("[L3 WS] memory_compact_suggest 已全局禁用（Memory Nexus）")
    return


async def _maybe_push_zombie_tasks_snapshot(websocket) -> None:
    """
    订阅 ``subscribe_background_tasks`` 后：若 zombie_tasks.json 仍有未读摘要，补推一条。
    解决「L3 先启动广播、桌面后连 WebSocket」时收不到 zombie_tasks_pending 的问题。
    """
    try:
        from l3_node.primitives.agent_tasks.background_task_service import load_zombie_tasks_snapshot

        tasks = load_zombie_tasks_snapshot()
        if not tasks:
            return
        payload = {
            "type": "background_task",
            "event": "zombie_tasks_pending",
            "count": len(tasks),
            "tasks": [
                {
                    "task_id": z.get("task_id"),
                    "task_prompt": str(z.get("task_prompt") or "")[:800],
                    "previous_status": z.get("previous_status"),
                }
                for z in tasks[:40]
            ],
        }
        await _send_safe(websocket, payload)
        logger.info("[L3 WS] 已向订阅端补推 zombie_tasks_pending count=%d", len(tasks))
    except Exception as e:
        logger.debug("[L3 WS] zombie_tasks_pending 补推跳过: %s", e)


async def _run_scheduled_memory_compact_background(*, force: bool = True) -> None:
    """[已停用] 原：WS 确认后后台跑 JSON 合并；入口保留以兼容旧客户端消息类型。"""
    logger.debug("[L3 WS] memory_compact 后台任务已禁用 force=%s（Memory Nexus）", force)
    return


async def _broadcast_to_mirror_subscribers(chat_id: str, payload: dict) -> None:
    """向订阅该 chat_id 的终端连接广播消息。"""
    if not chat_id or not payload:
        return
    async with _mirror_subscribers_lock:
        subs = _mirror_subscribers.get(chat_id, set()).copy()
    for ws in subs:
        try:
            await _send_safe(ws, payload)
        except Exception:
            pass


async def _voice_template_post_answer_bookkeeping(
    *,
    websocket,
    chat_id: str,
    messages_snapshot: list | None,
    final_content: str,
    run_id: str,
    broadcast: bool,
    chunk_payload: dict | None,
    answer_payload: dict | None,
) -> None:
    """Persist/log template replies after the client has already received answer."""
    metrics: dict[str, Any] = {
        "template": True,
        "session_save_async": True,
        "lark_push_skipped": True,
    }
    if chat_id and messages_snapshot is not None:
        started = time.perf_counter()
        try:
            await asyncio.to_thread(_save_lark_session, chat_id, messages_snapshot)
            metrics["session_save_ms"] = round((time.perf_counter() - started) * 1000.0, 2)
        except Exception as e:
            metrics["session_save_error"] = str(e)[:200]
            logger.debug("[L3 WS] voice template async session save skipped: %s", e)

    started = time.perf_counter()
    try:
        from l3_node.terminal_turn_debug_log import append_final

        append_final(
            "voice_fast_lane_template_final",
            final_content,
            extra={"run_id": run_id, "chat_id": chat_id or None, "post_answer": True},
        )
        metrics["append_final_ms"] = round((time.perf_counter() - started) * 1000.0, 2)
    except Exception as e:
        metrics["append_final_error"] = str(e)[:200]

    if broadcast and chat_id:
        started = time.perf_counter()
        try:
            if chunk_payload:
                await _broadcast_to_mirror_subscribers(chat_id, chunk_payload)
            if answer_payload:
                await _broadcast_to_mirror_subscribers(chat_id, answer_payload)
            metrics["broadcast_ms"] = round((time.perf_counter() - started) * 1000.0, 2)
        except Exception as e:
            metrics["broadcast_error"] = str(e)[:200]

    try:
        await _send_safe(
            websocket,
            {
                "step_type": "voice_template_post_answer_metrics",
                "run_id": run_id,
                "latency_trace": metrics,
            },
        )
    except Exception:
        pass


def _mirror_push_url_effective() -> str:
    return (os.environ.get("LARK_MIRROR_PUSH_URL") or _DEFAULT_MIRROR_PUSH).strip()


def _is_default_mirror_push_url(url: str) -> bool:
    u = (url or "").strip().rstrip("/").lower()
    return u in (
        "http://127.0.0.1:5000/api/mirror-push",
        "http://localhost:5000/api/mirror-push",
    )


def _push_via_lark_open_api_sync(chat_id: str, content: str) -> bool:
    """使用与 IM 长连接相同的凭证，经 Open API 发送文本（不依赖 :5000 webhook）。"""
    cid = (chat_id or "").strip()
    if not cid or content is None:
        return False
    try:
        from l3_node.channels.lark.client import get_lark_api_base, resolve_lark_credentials
        from l3_node.channels.lark.im import send_text

        aid, sec, yb = resolve_lark_credentials()
        if not aid or not sec:
            return False
        base = yb or get_lark_api_base()
        res = send_text(cid, str(content), app_id=aid, app_secret=sec, api_base=base)
        return res.get("status") == "success"
    except Exception as e:
        logger.debug("[L3 WS] mirror 直连 Lark Open API 失败: %s", e)
        return False


async def _push_reply_to_lark(chat_id: str, content: str) -> None:
    """将回复同步到 Lark：默认 URL 或凭证可用时优先 Open API；否则或失败时再 POST mirror-push。"""
    url = _mirror_push_url_effective()
    if not chat_id or content is None:
        return

    from l3_node.react_ui_sanitize import sanitize_final_answer_for_lark_im

    content = sanitize_final_answer_for_lark_im(str(content))
    if not content.strip():
        return

    prefer_direct_first = _is_default_mirror_push_url(url) or not url
    if prefer_direct_first:
        if await asyncio.to_thread(_push_via_lark_open_api_sync, chat_id, content):
            return
        if not url:
            logger.debug("[L3 WS] mirror-push 未配置 URL 且 Lark API 未发送成功，跳过")
            return

    try:
        import httpx

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                r = await client.post(url, json={"chat_id": chat_id, "content": content})
            except httpx.RequestError as e:
                logger.debug("[L3 WS] mirror-push 请求异常: %s", e)
                if await asyncio.to_thread(_push_via_lark_open_api_sync, chat_id, content):
                    logger.info("[L3 WS] mirror-push 不可用，已改用 Lark Open API 发送成功")
                else:
                    logger.warning("[L3 WS] mirror-push 失败（网络）且 Lark API 未成功: %s", e)
                return

            if r.status_code == 200:
                return
            if r.status_code in (502, 503, 504):
                if await asyncio.to_thread(_push_via_lark_open_api_sync, chat_id, content):
                    logger.info(
                        "[L3 WS] mirror-push HTTP %d，已改用 Lark Open API 发送成功",
                        r.status_code,
                    )
                    return
            logger.warning("[L3 WS] mirror-push 失败 status=%d %s", r.status_code, r.text[:200])
    except Exception as e:
        logger.debug("[L3 WS] mirror-push 异常: %s", e)
        if await asyncio.to_thread(_push_via_lark_open_api_sync, chat_id, content):
            logger.info("[L3 WS] mirror-push 异常后已改用 Lark Open API 发送成功")


def _make_on_step(websocket, run_id: str, chat_id: str, broadcast: bool):
    """构建 on_step 回调；chat_id 且 broadcast 时同时广播到镜像订阅者。

    注意：须与 on_chunk、以及 run_agent 返回后的 answer 使用**同一会话级** ``run_id``。
    Agent 传入的第三参为 ``ctx.run_id``（完整 UUID），若写入 payload 会与 chunk 的短 id 不一致，
    桌面端 ``l3ActiveRunIdRef`` 在「末帧为 chunk（短 id）→ answer（长 id）」时会丢弃 answer，
    表现为一直转圈直至超时或中断。此处始终使用本 WS 消息轮次的 ``run_id``。
    """
    def on_step(step_type: str, content: str, _ctx_run_id: str) -> None:
        # 中间步骤（尤其 observation）可能含超大工具输出；仅压缩发往 WS 的副本，不影响执行链路。
        safe = content
        if step_type in ("observation", "action", "thought"):
            try:
                from core.utils.log_utils import truncate_jsonish_text_for_ws_or_log

                safe = truncate_jsonish_text_for_ws_or_log(content or "")
            except Exception:
                _c = content or ""
                if len(_c) > 120_000:
                    safe = _c[:120_000] + f"\n... [已截断，原长度: {len(_c)} 字符]"
                else:
                    safe = _c
        payload = {"step_type": step_type, "content": safe, "run_id": run_id}
        asyncio.create_task(_send_safe(websocket, payload))
        if broadcast and chat_id:
            asyncio.create_task(_broadcast_to_mirror_subscribers(chat_id, payload))
    return on_step


async def _ws_execute_intent_turn(
    websocket,
    engine: "LiteLLMEngine",
    run_agent_fn,
    messages: list[dict],
    msg: dict,
    intent: str,
    chat_id: str,
    origin_terminal: bool,
    attachments_metadata: list | None = None,
) -> None:
    """单轮 intent：与 WS 主循环解耦，便于 run_abort 时 asyncio.cancel。"""
    logger.debug("[L3 WS] 收到输入 intent_len=%d history=%d run_agent (task)", len(intent), len(messages))
    run_id = str(uuid.uuid4())[:8]
    local_voice_session = _ws_msg_is_local_voice(msg)
    lark_chat_id = _ws_msg_lark_chat_id(msg, chat_id)
    broadcast = bool(lark_chat_id)

    _engine = _resolve_ws_engine(engine)
    if _engine is None:
        await _send_safe(
            websocket,
            {
                "step_type": "error",
                "content": (
                    "L3 大模型引擎尚未就绪：请等待 L2 管理员为该节点分配子账号，或在项目 .env 配置 "
                    "DASHSCOPE_API_KEY（或 OPENAI_API_KEY）后重启 L3。"
                ),
                "run_id": run_id,
            },
        )
        if broadcast and chat_id:
            await _broadcast_to_mirror_subscribers(
                chat_id,
                {
                    "step_type": "error",
                    "content": "L3 引擎未就绪（等待 L2 分配或 .env Key）",
                    "run_id": run_id,
                },
            )
        return

    if intent == "/clear":
        messages.clear()
        if chat_id:
            _save_lark_session(chat_id, messages)
        reply_clear = "[System] 后端上下文已强制清空。"
        ans_payload = {"step_type": "answer", "content": reply_clear, "run_id": run_id}
        await _send_safe(websocket, ans_payload)
        if broadcast and chat_id:
            await _broadcast_to_mirror_subscribers(chat_id, ans_payload)
        if origin_terminal and lark_chat_id:
            asyncio.create_task(_push_reply_to_lark(lark_chat_id, reply_clear))
        return

    try:
        from l3_node.lark_workflow_command_interceptor import try_lark_workflow_command_intercept

        cmd_reply = try_lark_workflow_command_intercept(intent, channel_id=lark_chat_id or "")
    except Exception:
        cmd_reply = None
    if cmd_reply:
        if chat_id:
            messages.append({"role": "user", "content": intent})
            messages.append({"role": "assistant", "content": cmd_reply})
            _save_lark_session(chat_id, messages)
            logger.debug("[L3 WS] chat_id=%s 遥控拦截已保存会话", chat_id[:20])
        ans_payload = {"step_type": "answer", "content": cmd_reply, "run_id": run_id}
        await _send_safe(websocket, ans_payload)
        if broadcast and chat_id:
            await _broadcast_to_mirror_subscribers(chat_id, ans_payload)
        if origin_terminal and lark_chat_id:
            asyncio.create_task(_push_reply_to_lark(lark_chat_id, cmd_reply))
        return

    # 通用定时任务确定性拦截（可选；JACHIN_DISABLE_DEFERRED_TIMED_TASK_INTERCEPT=1 时关闭，改由 LLM 调 util:schedule_task）
    _deferred_reply: str | None = None
    _skip_def_ix = (
        os.environ.get("JACHIN_DISABLE_DEFERRED_TIMED_TASK_INTERCEPT", "")
        .strip()
        .lower()
        in ("1", "true", "yes", "on")
    )
    if not _skip_def_ix:
        try:
            from l3_node.deferred_task_scheduler import try_generic_timed_task_intercept

            _deferred_reply = try_generic_timed_task_intercept(intent, lark_chat_id=lark_chat_id or None)
        except Exception:
            _deferred_reply = None
    if _deferred_reply is not None:
        if chat_id:
            messages.append({"role": "user", "content": intent})
            messages.append({"role": "assistant", "content": _deferred_reply})
            _save_lark_session(chat_id, messages)
        ans_payload = {"step_type": "answer", "content": _deferred_reply, "run_id": run_id}
        await _send_safe(websocket, ans_payload)
        if broadcast and chat_id:
            await _broadcast_to_mirror_subscribers(chat_id, ans_payload)
        if origin_terminal and lark_chat_id:
            asyncio.create_task(_push_reply_to_lark(lark_chat_id, _deferred_reply))
        return

    # /test 与 L3 内定时：Lark 经 WS 镜像时不走 im_channels/dispatcher，此处对齐拦截
    test_reply: str | None = None
    try:
        from l3_node.lark_test_file_skill import is_slash_test_command, try_test_lark_file_skill_intercept
        from l3_node.lark_test_schedule import try_test_schedule_intercept

        if is_slash_test_command(intent):
            test_reply = try_test_lark_file_skill_intercept(intent)
        else:
            test_reply = try_test_schedule_intercept(intent)
    except Exception:
        test_reply = None
    if test_reply is not None:
        if chat_id:
            messages.append({"role": "user", "content": intent})
            messages.append({"role": "assistant", "content": test_reply})
            _save_lark_session(chat_id, messages)
        ans_payload = {"step_type": "answer", "content": test_reply, "run_id": run_id}
        await _send_safe(websocket, ans_payload)
        if broadcast and chat_id:
            await _broadcast_to_mirror_subscribers(chat_id, ans_payload)
        if origin_terminal and lark_chat_id:
            asyncio.create_task(_push_reply_to_lark(lark_chat_id, test_reply))
        return

    _voice_diagnostics = msg.get("voice_diagnostics")
    _voice_diagnostics = _voice_diagnostics if isinstance(_voice_diagnostics, dict) else None

    try:
        from l3_node.terminal_turn_debug_log import begin_turn

        begin_turn(
            intent,
            extra={
                "run_id": run_id,
                "channel": "desktop_voice_companion" if local_voice_session else ("websocket_terminal" if origin_terminal else "websocket_lark"),
                "origin_terminal": origin_terminal,
                "local_voice_session": local_voice_session,
                "has_chat_id": bool(lark_chat_id),
                "session_id": chat_id or "",
                **(
                    {
                        "lark_chat_id": lark_chat_id,
                        "lark_reply_chat_id": lark_chat_id,
                    }
                    if lark_chat_id
                    else {}
                ),
                "intent_chars": len(intent),
                "history_msgs_before_turn": len(messages),
                "default_engine_model": getattr(_engine, "model_name", ""),
                **({"voice_diagnostics": _voice_diagnostics} if _voice_diagnostics else {}),
            },
        )
    except Exception:
        pass

    base_on_step = _make_on_step(websocket, run_id, chat_id, broadcast)

    def on_step(step_type: str, content: str, ctx_run_id: str) -> None:
        try:
            from l3_node.terminal_turn_debug_log import append_line

            append_line(step_type, content)
        except Exception:
            pass
        out_content = content
        if step_type == "thought":
            from l3_node.react_ui_sanitize import sanitize_thought_step_for_ui

            out_content = sanitize_thought_step_for_ui(content)
        base_on_step(step_type, out_content, ctx_run_id)

    _imp_sig = msg.get("implicit_signals")
    _imp_sig = _imp_sig if isinstance(_imp_sig, dict) else None
    _imp_sig = _normalize_ws_implicit_signals_for_kernel(
        _imp_sig,
        local_voice_session=local_voice_session,
    )

    _voice_gate_reply, _voice_gate_reason = _voice_evidence_gate_reply(intent, _imp_sig)
    if _voice_gate_reply:
        if isinstance(_imp_sig, dict):
            _imp_sig["voice_evidence_blocked"] = True
            _imp_sig["voice_evidence_block_reason"] = _voice_gate_reason
        try:
            from l3_node.terminal_turn_debug_log import append_final

            append_final(
                "voice_evidence_gate_final",
                _voice_gate_reply,
                extra={
                    "run_id": run_id,
                    "reason": _voice_gate_reason,
                    "voice_stt_source": (_imp_sig or {}).get("voice_stt_source") if isinstance(_imp_sig, dict) else "",
                    "voice_stt_finalized": (_imp_sig or {}).get("voice_stt_finalized") if isinstance(_imp_sig, dict) else None,
                },
            )
        except Exception:
            pass
        from l3_node.react_ui_sanitize import sanitize_final_answer_for_ui

        _gate_payload = {
            "step_type": "answer",
            "content": sanitize_final_answer_for_ui(_voice_gate_reply),
            "run_id": run_id,
            "latency_trace": {
                "voice_evidence_gate": True,
                "reason": _voice_gate_reason,
                "tools_skipped": True,
            },
        }
        await _send_safe(websocket, _gate_payload)
        if broadcast and chat_id:
            await _broadcast_to_mirror_subscribers(chat_id, _gate_payload)
        return

    _early_yield = _pick_early_yield_token(intent, _imp_sig)
    if _early_yield:
        try:
            from l3_node.terminal_turn_debug_log import append_stream_chunk

            append_stream_chunk(_early_yield)
        except Exception:
            pass
        from l3_node.react_ui_sanitize import sanitize_stream_chunk_for_ui

        _early_payload = {
            "step_type": "chunk",
            "content": sanitize_stream_chunk_for_ui(_early_yield),
            "run_id": run_id,
        }
        await _send_safe(websocket, _early_payload)
        if broadcast and chat_id:
            await _broadcast_to_mirror_subscribers(chat_id, _early_payload)

    _ws_voice_fast_lane = _is_ws_voice_fast_lane(intent, _imp_sig, msg)
    try:
        from l3_node.intent_gateway.ood_signals import should_skip_progress_thought_kick

        _skip_kick = should_skip_progress_thought_kick(raw_user_input=intent)
    except Exception:
        _skip_kick = False
    _skip_kick = bool(_skip_kick or _ws_voice_fast_lane)
    if not _skip_kick:
        _kick = {
            "step_type": "thought",
            "content": "已接入任务。若上下文较长会先执行记忆刷新与摘要压缩，随后再推理（可能需数分钟）。",
            "run_id": run_id,
        }
        await _send_safe(websocket, _kick)
        if broadcast and chat_id:
            await _broadcast_to_mirror_subscribers(chat_id, _kick)

    async def on_chunk(chunk: str, *, mirror: bool = True) -> dict:
        try:
            from l3_node.terminal_turn_debug_log import append_stream_chunk

            append_stream_chunk(chunk)
        except Exception:
            pass
        from l3_node.react_ui_sanitize import sanitize_stream_chunk_for_ui

        safe_chunk = sanitize_stream_chunk_for_ui(chunk)
        p = {"step_type": "chunk", "content": safe_chunk, "run_id": run_id}
        await _send_safe(websocket, p)
        if mirror and broadcast and chat_id:
            await _broadcast_to_mirror_subscribers(chat_id, p)
        return p
    if _ws_voice_fast_lane:
        try:
            from l3_node.react_ui_sanitize import sanitize_final_answer_for_ui
            from l3_node.terminal_turn_debug_log import append_final

            _template_reply = _pick_ws_voice_template_reply(intent, _imp_sig)
            if _template_reply:
                logger.info(
                    "[L3 WS] voice fast lane template reply run_id=%s input_len=%d reply=%s",
                    run_id,
                    len(intent or ""),
                    _template_reply,
                )
                chunk_payload = await on_chunk(_template_reply, mirror=False)
                messages_snapshot = None
                if chat_id and messages is not None:
                    messages.append({"role": "user", "content": intent})
                    messages.append({"role": "assistant", "content": _template_reply})
                    messages_snapshot = list(messages)
                _reply_ui = sanitize_final_answer_for_ui(_template_reply)
                ans_payload = {
                    "step_type": "answer",
                    "content": _reply_ui,
                    "run_id": run_id,
                    "latency_trace": {
                        "template": True,
                        "answer_before_session_save": True,
                        "session_save_async": bool(messages_snapshot is not None),
                        "broadcast_async": bool(broadcast and chat_id),
                        "lark_push_skipped": True,
                    },
                }
                await _send_safe(websocket, ans_payload)
                asyncio.create_task(
                    _voice_template_post_answer_bookkeeping(
                        websocket=websocket,
                        chat_id=chat_id,
                        messages_snapshot=messages_snapshot,
                        final_content=_template_reply,
                        run_id=run_id,
                        broadcast=bool(broadcast and chat_id),
                        chunk_payload=chunk_payload,
                        answer_payload=ans_payload,
                    )
                )
                return

            fast_intent = _sanitize_ws_voice_fast_lane_intent(intent)
            if isinstance(_imp_sig, dict) and fast_intent != intent:
                _imp_sig["voice_fast_lane_sanitized_text"] = fast_intent
                _imp_sig["voice_fast_lane_sanitized"] = True
            _fast_model = _voice_fast_lane_model()
            try:
                _fast_tokens = int(os.environ.get("JACHIN_VOICE_FAST_LANE_MAX_TOKENS", "64") or "64")
            except (TypeError, ValueError):
                _fast_tokens = 64
            _fast_tokens = max(16, min(_fast_tokens, 128))
            try:
                _fast_temp = float(os.environ.get("JACHIN_VOICE_FAST_LANE_TEMPERATURE", "0.25") or "0.25")
            except (TypeError, ValueError):
                _fast_temp = 0.25
            _fast_kw: dict[str, Any] = {
                "temperature": max(0.0, min(_fast_temp, 0.8)),
                "max_tokens": _fast_tokens,
                "l3_call_purpose": "voice_fast_lane_ws_direct_llm",
                "l3_run_id": run_id,
                "l3_override_model": _fast_model,
                "extra_body": {"enable_thinking": False},
            }
            logger.info(
                "[L3 WS] voice fast lane direct LLM run_id=%s model=%s input_len=%d",
                run_id,
                _fast_model,
                len(fast_intent or ""),
            )
            _fast_started = time.perf_counter()
            _fast_first_chunk_at = 0.0
            _fast_first_chunk = asyncio.Event()
            _fast_chunks: list[str] = []
            _fast_prompt_chars = sum(len(str(m.get("content") or "")) for m in _voice_fast_lane_messages(fast_intent, _imp_sig))

            async def _fast_on_chunk(chunk: str) -> None:
                nonlocal _fast_first_chunk_at
                _fast_chunks.append(chunk)
                if _fast_first_chunk_at <= 0:
                    _fast_first_chunk_at = (time.perf_counter() - _fast_started) * 1000.0
                _fast_first_chunk.set()
                await on_chunk(chunk)

            try:
                _fast_timeout_s = float(os.environ.get("JACHIN_VOICE_FAST_LANE_TIMEOUT_SEC", "2.5") or "2.5")
            except (TypeError, ValueError):
                _fast_timeout_s = 2.5
            _fast_timeout_s = max(0.5, min(_fast_timeout_s, 8.0))
            _fast_task = asyncio.create_task(
                _engine.generate_response_stream(
                    _voice_fast_lane_messages(fast_intent, _imp_sig),
                    chunk_callback=_fast_on_chunk,
                    tools=None,
                    **_fast_kw,
                )
            )
            _fast_source = "qwen_flash"
            try:
                await asyncio.wait_for(asyncio.shield(_fast_first_chunk.wait()), timeout=_fast_timeout_s)
                reply = await asyncio.wait_for(asyncio.shield(_fast_task), timeout=max(_fast_timeout_s, 1.0) + 2.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "[L3 WS] voice fast lane timeout run_id=%s model=%s timeout=%.2fs; no full-agent fallback",
                    run_id,
                    _fast_model,
                    _fast_timeout_s,
                )
                _fast_task.cancel()
                try:
                    await _fast_task
                except BaseException:
                    pass
                if _fast_chunks:
                    reply = "".join(_fast_chunks)
                    _fast_source = "partial_after_fast_timeout"
                else:
                    reply = _fallback_ws_voice_fast_reply(fast_intent, _imp_sig)
                    _fast_source = "fallback_after_fast_timeout"
                    await on_chunk(reply)
            except Exception as _fast_call_e:
                logger.warning(
                    "[L3 WS] voice fast lane direct failed run_id=%s model=%s err=%s; no full-agent fallback",
                    run_id,
                    _fast_model,
                    type(_fast_call_e).__name__,
                )
                try:
                    if not _fast_task.done():
                        _fast_task.cancel()
                        try:
                            await _fast_task
                        except BaseException:
                            pass
                except Exception:
                    pass
                if _fast_chunks:
                    reply = "".join(_fast_chunks)
                    _fast_source = f"partial_after_fast_error:{type(_fast_call_e).__name__}"
                else:
                    reply = _fallback_ws_voice_fast_reply(fast_intent, _imp_sig)
                    _fast_source = f"fallback_after_fast_error:{type(_fast_call_e).__name__}"
                    await on_chunk(reply)
            reply = _clean_ws_voice_fast_reply(reply or "".join(_fast_chunks) or "")
            if not reply:
                reply = _fallback_ws_voice_fast_reply(fast_intent, _imp_sig)
            _fast_elapsed_ms = round((time.perf_counter() - _fast_started) * 1000.0, 1)
            messages_snapshot = None
            if chat_id and messages is not None:
                messages.append({"role": "user", "content": fast_intent})
                messages.append({"role": "assistant", "content": reply})
                messages_snapshot = list(messages)
            try:
                append_final(
                    "voice_fast_lane_final",
                    reply,
                    extra={
                        "run_id": run_id,
                        "session_id": chat_id or None,
                        "model": _fast_model,
                        "source": _fast_source,
                        "first_chunk_ms": round(_fast_first_chunk_at, 1) if _fast_first_chunk_at else None,
                        "elapsed_ms": _fast_elapsed_ms,
                    },
                )
            except Exception:
                pass
            _reply_ui = sanitize_final_answer_for_ui(reply)
            ans_payload = {
                "step_type": "answer",
                "content": _reply_ui,
                "run_id": run_id,
                "latency_trace": {
                    "voice_fast_lane": True,
                    "template": False,
                    "source": _fast_source,
                    "model": _fast_model,
                    "prompt_style": "voice_fast_lane_short",
                    "prompt_chars": _fast_prompt_chars,
                    "max_tokens": _fast_tokens,
                    "first_chunk_ms": round(_fast_first_chunk_at, 1) if _fast_first_chunk_at else 0,
                    "model_elapsed_ms": _fast_elapsed_ms,
                    "answer_before_session_save": True,
                    "session_save_async": bool(messages_snapshot is not None),
                    "broadcast_async": bool(broadcast and chat_id),
                    "lark_push_skipped": not bool(origin_terminal and lark_chat_id),
                },
            }
            await _send_safe(websocket, ans_payload)
            asyncio.create_task(
                _voice_template_post_answer_bookkeeping(
                    websocket=websocket,
                    chat_id=chat_id,
                    messages_snapshot=messages_snapshot,
                    final_content=reply,
                    run_id=run_id,
                    broadcast=bool(broadcast and chat_id),
                    chunk_payload=None,
                    answer_payload=ans_payload,
                )
            )
            if origin_terminal and lark_chat_id and reply:
                asyncio.create_task(_push_reply_to_lark(lark_chat_id, _reply_ui))
            return
        except asyncio.CancelledError:
            try:
                if "_fast_task" in locals() and _fast_task is not None and not _fast_task.done():
                    _fast_task.cancel()
                    try:
                        await _fast_task
                    except BaseException:
                        pass
            except Exception:
                pass
            logger.info("[L3 WS] voice fast lane cancelled run_id=%s", run_id)
            raise
        except Exception as _fast_e:
            _fast_err = f"{type(_fast_e).__name__}: {_fast_e}".lower()
            if "cancel" in _fast_err:
                logger.info(
                    "[L3 WS] voice fast lane cancelled run_id=%s err=%s; no fallback",
                    run_id,
                    type(_fast_e).__name__,
                )
                raise asyncio.CancelledError() from _fast_e
            logger.warning("[L3 WS] voice fast lane direct failed, fallback run_agent: %s", _fast_e)

    _imp_attr = {
        "channel": "desktop_voice_companion" if local_voice_session else ("websocket_terminal" if origin_terminal else "websocket_lark"),
        "has_chat_id": bool(lark_chat_id),
        "session_id": chat_id or "",
        "local_voice_session": local_voice_session,
    }
    if _voice_diagnostics:
        _imp_attr["voice_diagnostics"] = _voice_diagnostics
    if lark_chat_id:
        _imp_attr["lark_chat_id"] = lark_chat_id
    _att_meta = attachments_metadata
    if _att_meta is None:
        _raw = msg.get("attachments_metadata")
        _att_meta = _raw if isinstance(_raw, list) else None
    _gw_st = msg.get("gateway_system_state")
    _gw_st = str(_gw_st).strip() if _gw_st else None
    _gw_ch = str(msg.get("gateway_clarification_handle") or "").strip()
    try:
        _gw_dl = float(msg.get("gateway_clarification_deadline_ts") or 0.0)
    except (TypeError, ValueError):
        _gw_dl = 0.0
    try:
        reply = await run_agent_fn(
            intent,
            _engine,
            on_step=on_step,
            on_chunk=on_chunk,
            _session_messages=messages,
            implicit_signals=_imp_sig,
            implicit_attribution=_imp_attr,
            attachments_metadata=_att_meta,
            gateway_system_state=_gw_st,
            gateway_clarification_handle=_gw_ch,
            gateway_clarification_deadline_ts=_gw_dl,
        )
        try:
            from l3_node.deferred_task_scheduler import heal_schedule_reply_if_bogus

            _healed = heal_schedule_reply_if_bogus(
                intent, reply or "", lark_chat_id=lark_chat_id or None
            )
            if _healed:
                reply = _healed
                if chat_id and messages:
                    for _hi in range(len(messages) - 1, -1, -1):
                        if messages[_hi].get("role") == "assistant":
                            messages[_hi]["content"] = _healed
                            break
        except Exception as _h_ex:
            logger.debug("[L3 WS] deferred heal 跳过: %s", _h_ex)
        if chat_id and messages:
            _save_lark_session(chat_id, messages)
            logger.debug("[L3 WS] chat_id=%s 已保存会话 %d 条", chat_id[:20], len(messages))

        try:
            from l3_node.terminal_turn_debug_log import append_final

            append_final(
                "final_answer",
                reply or "",
                extra={
                    "run_id": run_id,
                    "session_msgs_saved": len(messages) if chat_id else None,
                    "lark_chat_id": lark_chat_id or None,
                    "lark_reply_chat_id": lark_chat_id or None,
                    "chat_id_suffix": (chat_id[-12:] if chat_id and len(chat_id) >= 12 else chat_id) or "",
                },
            )
        except Exception:
            pass

        from l3_node.react_ui_sanitize import sanitize_final_answer_for_ui

        _reply_ui = sanitize_final_answer_for_ui(reply or "") if (reply or "").strip() else (reply or "")
        ans_payload = {"step_type": "answer", "content": _reply_ui, "run_id": run_id}
        await _send_safe(websocket, ans_payload)
        if broadcast and chat_id:
            await _broadcast_to_mirror_subscribers(chat_id, ans_payload)
        if origin_terminal and lark_chat_id and reply:
            asyncio.create_task(_push_reply_to_lark(lark_chat_id, _reply_ui))
    except asyncio.CancelledError:
        logger.debug("[L3 WS] run_agent 已取消 run_id=%s", run_id)
        raise
    except Exception as e:
        logger.debug("[L3 WS] run_agent 异常 intent_len=%d err=%s", len(intent), type(e).__name__)
        logger.exception("run_agent failed: %s", e)
        try:
            from l3_node.terminal_turn_debug_log import append_final

            append_final(
                "run_agent_exception",
                f"{type(e).__name__}: {e}",
                extra={"run_id": run_id},
            )
        except Exception:
            pass
        err_payload = {"step_type": "error", "content": str(e), "run_id": run_id}
        await _send_safe(websocket, err_payload)
        if broadcast and chat_id:
            await _broadcast_to_mirror_subscribers(chat_id, err_payload)


async def _handle_client(websocket, engine: "LiteLLMEngine", run_agent_fn):
    """处理单客户端连接。维护 per-connection 对话历史；Lark 通过 chat_id 持久化。
    支持终端-Lark 镜像：subscribe_mirror 订阅、广播 mirror_input/answer、终端回复推送到 Lark。"""
    session_messages: list[dict] = []
    _my_lark_chat_id: str = ""  # 本连接订阅的 chat_id（终端镜像模式）
    _bg_task_subscribed: bool = False
    active_turn_task: asyncio.Task | None = None
    # Pre-flight：语音开始后提前预热会话历史，等 STT 文本到达时直接复用。
    _prepared_sessions: dict[str, list[dict]] = {}
    _prepare_tasks: dict[str, asyncio.Task] = {}

    async def _preflight_prepare_session(chat_id: str) -> list[dict]:
        if not chat_id:
            return []
        loaded = await asyncio.to_thread(_load_lark_session, chat_id)
        prepared = loaded if isinstance(loaded, list) else []
        _prepared_sessions[chat_id] = prepared
        logger.debug("[L3 WS][Preflight] chat_id=%s prepared history=%d", chat_id[:20], len(prepared))
        return prepared

    async def _resolve_session_messages(chat_id: str) -> list[dict]:
        nonlocal session_messages
        if not chat_id:
            return session_messages
        task = _prepare_tasks.get(chat_id)
        if task is not None and not task.done():
            # 预热通常已在用户说话阶段完成；这里给一个极短等待窗口，避免因竞态退回冷加载。
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=0.4)
            except asyncio.TimeoutError:
                logger.debug("[L3 WS][Preflight] chat_id=%s warmup still running", chat_id[:20])
            except Exception as e:
                logger.debug("[L3 WS][Preflight] chat_id=%s warmup failed: %s", chat_id[:20], e)
        prepared = _prepared_sessions.pop(chat_id, None)
        if isinstance(prepared, list):
            session_messages = prepared
            return session_messages
        loaded = await asyncio.to_thread(_load_lark_session, chat_id)
        session_messages = loaded if isinstance(loaded, list) else []
        return session_messages

    try:
        async for raw in websocket:
            try:
                if isinstance(raw, (str, bytes)):
                    msg = await _ws_json_loads(raw)
                else:
                    msg = raw
            except (json.JSONDecodeError, TypeError):
                continue
            msg_type = msg.get("type") or msg.get("action", "")
            if msg_type == "manifest":
                await _send_safe(websocket, {"type": "manifest_ack", "caps": msg.get("caps", [])})
                asyncio.create_task(_maybe_push_memory_compact_suggest(websocket))
                continue

            # 终端订阅 Lark 镜像：后续该 chat_id 的消息会广播到此连接
            if msg_type == "subscribe_mirror":
                cid = (msg.get("lark_chat_id") or msg.get("chat_id") or "").strip()
                if cid:
                    async with _mirror_subscribers_lock:
                        _mirror_subscribers.setdefault(cid, set()).add(websocket)
                    _my_lark_chat_id = cid
                    logger.info("[L3 WS] 终端已订阅 Lark 镜像 chat_id=%s", cid[:20])
                continue
            if msg_type == "unsubscribe_mirror":
                cid = _my_lark_chat_id or (msg.get("lark_chat_id") or msg.get("chat_id") or "").strip()
                if cid:
                    async with _mirror_subscribers_lock:
                        s = _mirror_subscribers.get(cid, set())
                        s.discard(websocket)
                        if not s:
                            _mirror_subscribers.pop(cid, None)
                    _my_lark_chat_id = ""
                continue

            if msg_type == "subscribe_background_tasks":
                try:
                    from l3_node.l3_event_bus import register_background_task_subscriber

                    await register_background_task_subscriber(websocket)
                    _bg_task_subscribed = True
                    await _send_safe(
                        websocket,
                        {"type": "background_task_subscribed", "ok": True},
                    )
                    asyncio.create_task(_maybe_push_zombie_tasks_snapshot(websocket))
                except Exception as e:
                    await _send_safe(
                        websocket,
                        {"type": "background_task_subscribed", "ok": False, "error": str(e)},
                    )
                continue

            # 前端 /clear：控制帧清空 per-connection 缓冲与（可选）Lark 持久化会话，不进入 intent/LLM
            if msg_type == "clear_session":
                cid = (_ws_msg_session_key(msg) or _my_lark_chat_id or "").strip()
                session_messages.clear()
                _prepared_sessions.pop(cid, None)
                _pt = _prepare_tasks.pop(cid, None)
                if _pt is not None and not _pt.done():
                    _pt.cancel()
                if cid:
                    _save_lark_session(cid, session_messages)
                logger.debug("[L3 WS] clear_session 已清空 chat_id=%s", cid[:20] if cid else "-")
                continue

            # 语音 Pre-flight：麦克风点亮即发送。后台预热历史会话，隐藏后续首包延迟。
            if msg_type == "prepare_context":
                cid = (_ws_msg_session_key(msg) or _my_lark_chat_id or "").strip()
                if cid:
                    _running = _prepare_tasks.get(cid)
                    if _running is None or _running.done():
                        _prepare_tasks[cid] = asyncio.create_task(_preflight_prepare_session(cid))
                continue

            if msg_type == "voice_diagnostics_append":
                diagnostics = msg.get("voice_diagnostics")
                if isinstance(diagnostics, dict) and diagnostics:
                    try:
                        from l3_node.terminal_turn_debug_log import append_voice_diagnostics

                        session_key = _ws_msg_session_key(msg) or _my_lark_chat_id or ""
                        append_voice_diagnostics(
                            diagnostics,
                            run_id=str(msg.get("run_id") or ""),
                            lark_chat_id=_ws_msg_lark_chat_id(msg, session_key),
                        )
                    except Exception as e:
                        logger.debug("[L3 WS] voice diagnostics append skipped: %s", e)
                continue

            # 终端「停止生成」：取消当前 run_agent 任务，主循环可继续收包（避免与含 intent 的误触 action 混淆）
            _intent_probe = (msg.get("intent") or msg.get("content") or "").strip()
            if msg_type == "run_abort" or (msg_type == "abort" and not _intent_probe):
                t = active_turn_task
                if t is not None and not t.done():
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass
                active_turn_task = None
                continue

            # 记忆整理调度：控制帧（无 intent 亦可）
            if msg_type == "memory_compact_defer":
                try:
                    from l3_node.memory_compact_schedule import defer_hours

                    defer_hours(float(msg.get("hours", 24)))
                except Exception as e:
                    logger.debug("[L3 WS] memory_compact_defer: %s", e)
                await _send_safe(
                    websocket,
                    {
                        "step_type": "system_status",
                        "content": json.dumps({"status": "记忆整理已推迟"}, ensure_ascii=False),
                        "run_id": "",
                    },
                )
                continue
            if msg_type in ("memory_compact_confirm", "memory_compact_auto_start"):
                asyncio.create_task(_run_scheduled_memory_compact_background())
                await _send_safe(
                    websocket,
                    {
                        "step_type": "system_status",
                        "content": json.dumps({"status": "记忆整理已在后台启动"}, ensure_ascii=False),
                        "run_id": "",
                    },
                )
                continue
            if msg_type == "memory_compact_cancel":
                try:
                    from l3_node.memory_compact_control import request_memory_compact_cancel

                    request_memory_compact_cancel()
                except Exception as e:
                    logger.debug("[L3 WS] memory_compact_cancel: %s", e)
                await _send_safe(
                    websocket,
                    {
                        "step_type": "system_status",
                        "content": json.dumps({"status": "已请求取消记忆整理（写入前生效）"}, ensure_ascii=False),
                        "run_id": "",
                    },
                )
                continue

            # 生成式 UI：前端提交 tool 参数，Native 执行或生成说明，不经本轮 LLM ReAct（会话仍落盘）
            if msg_type == "tool_ui_result":
                chat_id = _ws_msg_session_key(msg)
                if chat_id:
                    session_messages = await _resolve_session_messages(chat_id)
                tool_raw = (msg.get("tool_name") or msg.get("tool_id") or "").strip()
                result = msg.get("result")
                run_id = str(uuid.uuid4())[:8]
                broadcast = bool(chat_id)
                tid = tool_raw.lower()
                if tid in ("compose_essay", "core:compose_essay"):
                    tid = "core:compose_essay"
                try:
                    if tid == "core:compose_essay":
                        from l3_node.primitives.tools.loader import run_tool

                        body = json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result)
                        out = await asyncio.to_thread(run_tool, "core:compose_essay", body, None)
                        line_user = f"[tool_ui_result core:compose_essay]\n{body}"
                        session_messages.append({"role": "user", "content": line_user})
                        session_messages.append({"role": "assistant", "content": str(out)})
                        if chat_id:
                            _save_lark_session(chat_id, session_messages)
                        ans_payload = {"step_type": "answer", "content": str(out), "run_id": run_id}
                    elif tid in ("generate_ppt", "core:generate_ppt"):
                        sel = json.dumps(result, ensure_ascii=False) if result is not None else ""
                        out = (
                            "## PPT 模版已选择\n\n"
                            f"参数：{sel}\n\n"
                            "请在对话中请模型根据上述选择继续生成幻灯片大纲或内容。"
                        )
                        line_user = f"[tool_ui_result generate_ppt]\n{sel}"
                        session_messages.append({"role": "user", "content": line_user})
                        session_messages.append({"role": "assistant", "content": out})
                        if chat_id:
                            _save_lark_session(chat_id, session_messages)
                        ans_payload = {"step_type": "answer", "content": out, "run_id": run_id}
                    else:
                        out = f"[tool_ui_result] 未知工具: {tool_raw}"
                        ans_payload = {"step_type": "error", "content": out, "run_id": run_id}
                    await _send_safe(websocket, ans_payload)
                    if broadcast and chat_id:
                        await _broadcast_to_mirror_subscribers(chat_id, ans_payload)
                except Exception as e:
                    logger.exception("[L3 WS] tool_ui_result 失败: %s", e)
                    err_payload = {"step_type": "error", "content": f"[tool_ui_result] 执行失败: {e}", "run_id": run_id}
                    await _send_safe(websocket, err_payload)
                    if broadcast and chat_id:
                        await _broadcast_to_mirror_subscribers(chat_id, err_payload)
                continue

            intent, inline_att = _coerce_ws_intent_and_inline_attachments(msg)
            _base_att = msg.get("attachments_metadata")
            _base_att_list = list(_base_att) if isinstance(_base_att, list) else []
            _merged_att = _base_att_list + inline_att
            attachments_for_turn = _merged_att if _merged_att else None
            if not (intent or "").strip() and not attachments_for_turn:
                continue
            if not (intent or "").strip() and attachments_for_turn:
                intent = "请查看附件并回答。"

            chat_id = _ws_msg_session_key(msg)
            lark_chat_id = _ws_msg_lark_chat_id(msg, chat_id)
            origin_terminal = str(msg.get("origin", "")).lower() == "terminal"
            if chat_id:
                session_messages = await _resolve_session_messages(chat_id)
                logger.debug("[L3 WS] session_id=%s lark_chat=%s 加载历史 %d 条", chat_id[:20], bool(lark_chat_id), len(session_messages))

            # 只有真实 Lark 镜像会话才广播「用户输入」；本地语音陪伴态不要污染镜像流。
            if lark_chat_id:
                asyncio.create_task(_broadcast_to_mirror_subscribers(lark_chat_id, {
                    "step_type": "mirror_input",
                    "content": intent,
                    "run_id": "",
                }))

            _ws_used_siq = False
            if chat_id:
                try:
                    from l3_node.ws_siq_bridge import schedule_ws_turn_via_siq, ws_siq_enabled

                    if ws_siq_enabled():
                        _ws_sk = f"ws:{chat_id}"

                        async def _ws_siq_factory(final_intent: str) -> None:
                            await _ws_execute_intent_turn(
                                websocket,
                                engine,
                                run_agent_fn,
                                session_messages,
                                msg,
                                final_intent,
                                chat_id,
                                origin_terminal,
                                attachments_metadata=attachments_for_turn,
                            )

                        _st = await schedule_ws_turn_via_siq(
                            session_key=_ws_sk,
                            intent=intent,
                            execute_coro_factory=_ws_siq_factory,
                        )
                        if _st != "disabled":
                            _ws_used_siq = True
                except Exception as _ws_siq_ex:
                    logger.debug("[L3 WS] SIQ 调度跳过: %s", _ws_siq_ex)
            if _ws_used_siq:
                continue

            if active_turn_task is not None and not active_turn_task.done():
                if (os.environ.get("JACHIN_WS_SUPERSEDE_ACK", "1").strip().lower() not in ("0", "false", "no", "off")):
                    _ss_msg = json.dumps(
                        {
                            "kind": "prior_turn_superseded",
                            "message": "收到新输入：已停止当前生成并开始处理本轮。",
                        },
                        ensure_ascii=False,
                    )
                    await _send_safe(
                        websocket,
                        {"step_type": "system_status", "content": _ss_msg, "run_id": ""},
                    )
                    if lark_chat_id:
                        await _broadcast_to_mirror_subscribers(
                            lark_chat_id,
                            {
                                "step_type": "system_status",
                                "content": json.dumps(
                                    {
                                        "kind": "prior_turn_superseded",
                                        "message": "（镜像）连接内因新输入已打断上一轮输出。",
                                    },
                                    ensure_ascii=False,
                                ),
                                "run_id": "",
                            },
                        )
                active_turn_task.cancel()
                try:
                    await active_turn_task
                except asyncio.CancelledError:
                    pass
            active_turn_task = asyncio.create_task(
                _ws_execute_intent_turn(
                    websocket,
                    engine,
                    run_agent_fn,
                    session_messages,
                    msg,
                    intent,
                    chat_id,
                    origin_terminal,
                    attachments_metadata=attachments_for_turn,
                )
            )
    except Exception as e:
        logger.warning("WebSocket client error: %s", e)
    finally:
        for _t in _prepare_tasks.values():
            if _t is not None and not _t.done():
                _t.cancel()
        if active_turn_task is not None and not active_turn_task.done():
            active_turn_task.cancel()
            try:
                await active_turn_task
            except asyncio.CancelledError:
                pass
        if _bg_task_subscribed:
            try:
                from l3_node.l3_event_bus import unregister_background_task_subscriber

                await unregister_background_task_subscriber(websocket)
            except Exception:
                pass
        if _my_lark_chat_id:
            async with _mirror_subscribers_lock:
                s = _mirror_subscribers.get(_my_lark_chat_id, set())
                s.discard(websocket)
                if not s:
                    _mirror_subscribers.pop(_my_lark_chat_id, None)
        await websocket.close()


def _is_port_in_use_error(e: BaseException) -> bool:
    """判断是否为端口占用错误（Windows 10048, Linux 98）"""
    if isinstance(e, OSError):
        # Windows: 10048 = WSAEADDRINUSE; Linux: 98 = EADDRINUSE
        return getattr(e, "errno", None) in (10048, 98)
    return False


async def run_ws_server(
    engine: "LiteLLMEngine",
    run_agent_fn,
    host: str = WS_HOST,
    port: int = WS_PORT,
) -> None:
    """启动 WebSocket 服务，异步非阻塞。端口被占用时自动尝试 18982、18983..."""
    try:
        import websockets
    except ImportError:
        raise RuntimeError("需要安装 websockets: pip install websockets")

    async def handler(websocket):
        await _handle_client(websocket, engine, run_agent_fn)

    # 189xx 系列，跳过 18888（L2）、18991（L3 HTTP）
    skip_ports = {18888, 18991}
    ports_to_try = [p for p in range(port, port + 15) if p not in skip_ports][:12]
    last_err: BaseException | None = None
    for i, try_port in enumerate(ports_to_try):
        try:
            server = await websockets.serve(
                handler,
                host,
                try_port,
                # ReAct + 多模态单轮可 30s+；事件环若短暂卡顿（Memory Nexus/GIL 等）须避免误杀连接，
                # 否则桌面端会双断连 → 占位「等待 L3 或 L2」且输入被硬禁用。
                ping_interval=30,
                ping_timeout=120,
                close_timeout=5,
            )
            if i > 0:
                logger.warning(
                    "端口 %d 被占用，已改用 %d。前端需 VITE_SENSORY_WS_PORT=%d 或关闭占用进程",
                    port, try_port, try_port,
                )
            logger.info("L3 WebSocket 服务已启动 ws://%s:%d/sensory", host, try_port)
            try:
                from l3_node.runtime_diag_log import log_runtime_milestone

                log_runtime_milestone(f"WebSocket listening ws://{host}:{try_port}/sensory")
            except Exception:
                pass
            _sig_tokens: list = []

            def _on_stop_signal() -> None:
                async def _close_srv() -> None:
                    logger.info("[WS] 收到停止信号，关闭 WebSocket 服务…")
                    await server.close()

                try:
                    asyncio.get_running_loop().create_task(_close_srv())
                except RuntimeError:
                    pass

            if sys.platform != "win32":
                import signal

                loop = asyncio.get_running_loop()
                for sig in (signal.SIGINT, signal.SIGTERM):
                    try:
                        loop.add_signal_handler(sig, _on_stop_signal)
                        _sig_tokens.append(sig)
                    except (NotImplementedError, RuntimeError, ValueError):
                        pass
            try:
                await server.wait_closed()
            finally:
                if _sig_tokens:
                    import signal

                    loop = asyncio.get_running_loop()
                    for sig in _sig_tokens:
                        try:
                            loop.remove_signal_handler(sig)
                        except Exception:
                            pass
            return
        except OSError as e:
            last_err = e
            if _is_port_in_use_error(e):
                logger.warning("端口 %d 已被占用 (errno=%s)，尝试下一端口...", try_port, getattr(e, "errno", "?"))
                continue
            raise

    # 所有端口均失败
    raise RuntimeError(
        f"端口 {ports_to_try[0]}~{ports_to_try[-1]} 均被占用。请关闭其他 L3 实例: netstat -ano | findstr 18981"
    ) from last_err





