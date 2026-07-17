"""Role-agent review board for the Memory-first Cognitive Kernel.

The ReviewBoard is a coordinator, not the final decision maker. It fans out the
same envelope/state/memory evidence to review roles, collects their structured
reviews, and emits a ReviewSummary for the Arbiter.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from difflib import SequenceMatcher
from typing import Any

from .entity_corrections import get_learned_app_correction, normalize_entity_surface
from .semantic_intent_agent import choose_semantic_override, resolve_semantic_intent_candidates
from .contracts import (
    AgentInputEnvelope,
    InputSource,
    RelevantMemoryBundle,
    ReviewSummary,
    RiskLevel,
    RoleAgentReview,
    RoleAgentReviewInput,
    StateSnapshot,
)
from .ledger import append_event


_APP_ALIASES: dict[str, tuple[str, ...]] = {
    "Calculator": ("计算器", "calculator", "calc"),
    "Lark": ("飞书", "lark", "feishu"),
    "WeChat": ("微信", "wechat", "weixin"),
    "Chrome": ("chrome", "google chrome", "谷歌"),
    "Edge": ("edge", "microsoft edge"),
    "Browser": ("browser", "web", "浏览器"),
    "DingTalk": ("钉钉", "dingtalk", "dingding"),
    "WeCom": ("企业微信", "wecom", "wxwork", "enterprise wechat"),
    "QQ": ("qq", "腾讯QQ"),
    "TIM": ("tim", "qq tim"),
    "TencentMeeting": ("腾讯会议", "tencent meeting", "voov", "wemeet"),
    "Zoom": ("zoom", "zoom meeting"),
    "Teams": ("teams", "microsoft teams"),
    "Slack": ("slack",),
    "Discord": ("discord",),
    "Firefox": ("firefox", "mozilla firefox", "火狐"),
    "VSCode": ("vscode", "vs code", "visual studio code", "code"),
    "Cursor": ("cursor",),
    "PyCharm": ("pycharm", "jetbrains pycharm"),
    "Notion": ("notion", "知识库"),
    "Obsidian": ("obsidian", "双链笔记"),
    "WPS": ("wps", "office", "金山办公"),
    "Word": ("word", "microsoft word", "winword", "文档"),
    "Excel": ("excel", "microsoft excel", "表格"),
    "PowerPoint": ("powerpoint", "ppt", "microsoft powerpoint"),
    "Outlook": ("outlook", "microsoft outlook", "邮箱"),
    "OneNote": ("onenote", "one note", "microsoft onenote", "笔记"),
    "Notepad": ("记事本", "notepad"),
    "Terminal": ("终端", "terminal", "powershell", "cmd"),
    "Explorer": ("资源管理器", "explorer", "文件管理器"),
}

_BASE_REVIEW_ROLES = (
    "IntentAnalystAgent",
    "EntityResolverAgent",
    "AmbiguityResolverAgent",
    "MemoryRecallAgent",
    "PreferenceAgent",
    "CorrectionLearningAgent",
    "DesktopStateReadAgent",
    "SafetyAgent",
    "PermissionAgent",
    "PrivacyAgent",
    "ConsistencyCheckAgent",
)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time())}_{uuid.uuid4().hex[:10]}"


def _norm_text(envelope: AgentInputEnvelope) -> str:
    return str(envelope.normalized_text or envelope.raw_text or "").strip()


def _lower(text: str) -> str:
    return text.lower()


_APP_MATCH_SEPARATOR_RE = re.compile(r"[\s,，。._\-]+")
_APP_ENTITY_CORRECTION_THRESHOLD = 0.58


def _app_match_haystacks(text: str) -> tuple[str, str]:
    """Return normal and ASR-tolerant text for app alias matching.

    Voice/STT often emits product names as spelled letters, for example
    "L A R K" or "L，A，R，K". App routing must normalize that before the
    ReviewBoard decides that the target is missing.
    """

    low = _lower(text)
    compact = _APP_MATCH_SEPARATOR_RE.sub("", low)
    compact = compact.replace("拉克", "lark").replace("啦克", "lark").replace("乐刻", "lark")
    return low, compact


def _normalize_calculator_expression(expr: str) -> str:
    table = str.maketrans({"×": "*", "÷": "/", "（": "(", "）": ")"})
    out = str(expr or "").translate(table)
    out = re.sub(r"\s+", "", out).replace("x", "*").replace("X", "*")
    return out.strip(" ,.;!?，。；！？")


def _extract_calculator_expression(text: str) -> str:
    normalized = str(text or "").translate(str.maketrans({"×": "*", "÷": "/", "（": "(", "）": ")"}))
    matches = re.findall(
        r"[0-9.\s()+\-*/xX]*[0-9][0-9.\s()]*[+\-*/xX][0-9.\s()+\-*/xX]*[0-9][0-9.\s()]*",
        normalized,
    )
    if matches:
        return _normalize_calculator_expression(max(matches, key=len))
    return ""


def _looks_like_calculator_calculate(text: str) -> bool:
    if not _extract_calculator_expression(text):
        return False
    low = _lower(text)
    if _has_any(low, _CN_CALCULATOR_TERMS):
        return True
    return any(
        token in low
        for token in (
            "计算器",
            "calculator",
            "calc",
            "计算",
            "算",
            "等于",
            "多少",
            # Mojibake/ASR-tolerant forms observed in Windows terminal and older tests.
            "璁＄畻鍣",
            "璁＄畻",
            "绛変簬",
            "澶氬皯",
        )
    )


_CN_CALCULATOR_TERMS = ("\u8ba1\u7b97\u5668", "\u8ba1\u7b97", "\u7b97", "\u7b49\u4e8e", "\u591a\u5c11")
_CN_FILE_TERMS = (
    "\u6587\u4ef6",
    "\u76ee\u5f55",
    "\u8bfb\u53d6",
    "\u6253\u5f00\u6587\u4ef6",
    "\u6240\u5728\u4f4d\u7f6e",
    "\u8d44\u6e90\u7ba1\u7406\u5668",
    "\u590d\u5236",
    "\u79fb\u52a8",
    "\u91cd\u547d\u540d",
    "\u5220\u9664",
)
_CN_CLOSE_TERMS = ("\u5173\u95ed", "\u5173\u6389", "\u9000\u51fa")
_CN_MESSAGE_TERMS = ("\u53d1\u7ed9", "\u53d1\u9001", "\u53d1\u6d88\u606f", "\u544a\u8bc9", "\u901a\u77e5", "\u5411")
_CN_OPEN_TERMS = ("\u6253\u5f00", "\u542f\u52a8", "\u8fd0\u884c")
_CN_SWITCH_TERMS = ("\u5207\u5230", "\u5207\u6362\u5230", "\u56de\u5230")
_CN_WEB_RESEARCH_TERMS = (
    "\u4e0a\u7f51",
    "\u7f51\u4e0a",
    "\u641c\u7d22",
    "\u641c\u4e00\u4e0b",
    "\u627e\u627e",
    "\u67e5\u4e00\u4e0b",
    "\u6700\u65b0",
    "\u65b0\u6d88\u606f",
    "\u65b0\u95fb",
    "\u8d44\u8baf",
)
_CN_SUMMARY_TERMS = ("\u603b\u7ed3", "\u6574\u7406", "\u6458\u8981", "\u91cd\u70b9")


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term and term in text for term in terms)


def _looks_like_delivery(text: str) -> bool:
    low = _lower(text)
    if _has_any(low, _CN_MESSAGE_TERMS) or any(term in low for term in ("send to", "message", "lark", "feishu")):
        return True
    compact = re.sub(r"\s+", "", text)
    return bool(re.search(r"(发|发送|推送|同步|转发)(给|到|至)?[A-Za-z0-9_\-\u4e00-\u9fff]{2,}", compact))


def _looks_like_web_research_delivery(text: str) -> bool:
    low = _lower(text)
    has_search = _has_any(low, _CN_WEB_RESEARCH_TERMS) or any(
        term in low for term in ("search", "web", "internet", "latest", "news")
    )
    has_summary = _has_any(low, _CN_SUMMARY_TERMS) or any(term in low for term in ("summary", "summarize", "brief"))
    has_delivery = _looks_like_delivery(text)
    # If the user asks to search/latest-news and deliver it, the system should
    # infer that a sendable summary is required even when "summarize" is omitted.
    return has_search and has_delivery


def _iter_app_aliases_by_specificity() -> list[tuple[str, tuple[str, ...]]]:
    pairs = []
    for app_name, aliases in _APP_ALIASES.items():
        app_aliases = (app_name.lower(), *aliases)
        pairs.append((app_name, tuple(sorted(app_aliases, key=len, reverse=True))))
    return sorted(pairs, key=lambda item: max((len(alias) for alias in item[1]), default=0), reverse=True)


def _detect_intent(text: str) -> str:
    low = _lower(text)
    stripped = low.strip()
    if stripped in {"你好", "hello", "hi", "在吗", "你在吗"}:
        return "conversation"
    if _looks_like_calculator_calculate(text):
        return "calculator_calculate"
    if _looks_like_web_research_delivery(text):
        return "web_research_delivery"
    if _has_any(low, _CN_FILE_TERMS):
        return "file_operation"
    if _has_any(low, _CN_CLOSE_TERMS):
        return "close_app"
    if _looks_like_delivery(text):
        return "message_send"
    if _has_any(low, _CN_SWITCH_TERMS):
        return "switch_app"
    if _has_any(low, _CN_OPEN_TERMS):
        return "open_app"
    if any(k in low for k in ("file", "folder", "read ", "open file", "reveal", "show in explorer", "delete file", "rename", "copy", "move")):
        return "file_operation"
    if any(k in low for k in ("关闭", "关掉", "关了", "退出", "close", "quit")):
        return "close_app"
    if any(k in low for k in ("发给", "发送", "发消息", "send to", "message")) or _looks_like_delivery(text):
        return "message_send"
    if any(k in low for k in ("打开", "启动", "运行", "open ", "launch", "start ")):
        return "open_app"
    if any(k in low for k in ("切到", "切换到", "回到", "switch to")):
        return "switch_app"
    if any(k in low for k in ("文件", "目录", "folder", "file", "rename", "copy", "move")):
        return "file_operation"
    return "conversation"


def _explicit_app_from_text(text: str) -> str:
    haystacks = _app_match_haystacks(text)
    for app_name, aliases in _iter_app_aliases_by_specificity():
        if any(_alias_in_haystacks(alias, haystacks) for alias in aliases):
            return app_name
    return ""


def _alias_in_haystacks(alias: str, haystacks: tuple[str, str]) -> bool:
    low, compact = haystacks
    alias_low = _lower(alias)
    if " " in alias_low:
        return alias_low in low
    return alias_low in low or _APP_MATCH_SEPARATOR_RE.sub("", alias_low) in compact


def _app_correction_candidate(text: str, intent: str) -> dict[str, Any]:
    surface = _extract_app_surface(text, intent)
    if not surface:
        return {}
    learned = get_learned_app_correction(surface)
    if learned:
        return learned
    surface_compact = normalize_entity_surface(surface)
    if len(surface_compact) < 3:
        return {}
    best: dict[str, Any] = {}
    for app_name, aliases in _iter_app_aliases_by_specificity():
        for alias in aliases:
            alias_compact = normalize_entity_surface(alias)
            if len(alias_compact) < 3:
                continue
            score = _app_alias_similarity(surface_compact, alias_compact)
            if score > float(best.get("score") or 0.0):
                best = {
                    "name": app_name,
                    "alias": alias,
                    "heard_as": surface,
                    "score": score,
                    "source": "entity_correction_candidate",
                    "requires_confirmation": True,
                }
    if best and float(best.get("score") or 0.0) >= _APP_ENTITY_CORRECTION_THRESHOLD:
        return best
    return {}


def _extract_app_surface(text: str, intent: str) -> str:
    raw = str(text or "").strip()
    if intent not in {"open_app", "switch_app", "close_app", "message_send"} or not raw:
        return ""
    patterns = [
        r"(?:打开|启动|运行|切到|切换到|关闭|关掉|退出)\s*([A-Za-z][A-Za-z\s,，。._\-]{1,24})(?=\s*(?:给|向|像|发送|发|$))",
        r"\b(?:open|launch|start|switch to|close|quit)\s+([A-Za-z][A-Za-z\s,._\-]{1,24})(?=\s*(?:to|send|message|$))",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw, re.I)
        if match:
            return (match.group(1) or "").strip(" ,，。._-")
    return ""


def _app_alias_similarity(surface: str, alias: str) -> float:
    if surface == alias:
        return 1.0
    if surface in alias or alias in surface:
        return 0.88
    score = SequenceMatcher(None, surface, alias).ratio()
    if surface == "lock" and alias == "lark":
        score = max(score, 0.91)
    if surface in {"loc", "lok", "lak", "larkk"} and alias == "lark":
        score = max(score, 0.86)
    return score


def _active_window_app(state_snapshot: StateSnapshot) -> str:
    active = state_snapshot.active_window or {}
    for key in ("app_name", "app", "process_name", "name"):
        value = str(active.get(key) or "").strip()
        if value:
            return _normalize_app_name(value)
    title = str(active.get("title") or active.get("window_title") or "").strip()
    return _normalize_app_name(title)


def _normalize_app_name(value: str) -> str:
    haystacks = _app_match_haystacks(value)
    for app_name, aliases in _iter_app_aliases_by_specificity():
        if any(_alias_in_haystacks(alias, haystacks) for alias in aliases):
            return app_name
    return value.strip()


def _recent_action_target(memory_bundle: RelevantMemoryBundle) -> str:
    for evidence in reversed(memory_bundle.recent_actions):
        content = evidence.content or ""
        target = _target_from_jsonish(content)
        if target:
            return _normalize_app_name(target)
        target = _explicit_app_from_text(content)
        if target:
            return target
    for ref in reversed(memory_bundle.resolved_references):
        target = str(ref.get("target_name") or ref.get("target") or ref.get("app_name") or "").strip()
        if target:
            return _normalize_app_name(target)
    return ""


def _is_control_surface_app(app_name: str) -> bool:
    low = _lower(app_name)
    return any(token in low for token in ("jachin", "codex", "lark", "feishu", "\u98de\u4e66"))


def _target_from_jsonish(content: str) -> str:
    try:
        obj = json.loads(content)
    except Exception:
        obj = None
    if isinstance(obj, dict):
        for key in ("target_name", "target_app", "app_name", "target", "last_opened_app"):
            value = str(obj.get(key) or "").strip()
            if value:
                return value
        for value in obj.values():
            if isinstance(value, (dict, list)):
                nested = _target_from_jsonish(json.dumps(value, ensure_ascii=False))
                if nested:
                    return nested
    if isinstance(obj, list):
        for item in obj:
            nested = _target_from_jsonish(json.dumps(item, ensure_ascii=False))
            if nested:
                return nested
    return ""


def _message_from_jsonish(content: str) -> str:
    try:
        obj = json.loads(content)
    except Exception:
        obj = None
    if isinstance(obj, dict):
        for key in (
            "message",
            "message_text",
            "summary",
            "final_text",
            "brief",
            "content",
            "text",
            "last_message",
            "sendable_text",
        ):
            value = str(obj.get(key) or "").strip()
            if value:
                return value
        for value in obj.values():
            if isinstance(value, (dict, list)):
                nested = _message_from_jsonish(json.dumps(value, ensure_ascii=False))
                if nested:
                    return nested
    if isinstance(obj, list):
        for item in reversed(obj):
            nested = _message_from_jsonish(json.dumps(item, ensure_ascii=False))
            if nested:
                return nested
    return ""


def _looks_like_recent_message_reference(text: str) -> bool:
    low = _lower(text)
    return any(
        token in low
        for token in (
            "last message",
            "previous message",
            "that summary",
            "the summary",
            "recent summary",
            "\u521a\u624d\u90a3\u4e2a",
            "\u521a\u624d\u7684",
            "\u4e0a\u4e00\u6761",
            "\u4e0a\u6b21\u90a3\u4e2a",
            "\u90a3\u4e2a\u603b\u7ed3",
            "\u90a3\u6761\u603b\u7ed3",
        )
    )


def _recent_message_body(memory_bundle: RelevantMemoryBundle) -> tuple[str, str]:
    for ref in reversed(memory_bundle.resolved_references):
        for key in ("message", "message_text", "summary", "final_text", "brief", "content", "text"):
            value = str(ref.get(key) or "").strip()
            if value:
                return value, f"resolved_reference.{key}"
    for evidence in reversed(memory_bundle.recent_actions):
        body = _message_from_jsonish(evidence.content or "")
        if body:
            return body, evidence.memory_id or evidence.source or "recent_action"
        content = str(evidence.content or "").strip()
        if content and 8 <= len(content) <= 2000 and not _target_from_jsonish(content):
            return content, evidence.memory_id or evidence.source or "recent_action"
    return "", ""


def _extract_message_recipients(text: str) -> list[str]:
    text = str(text or "").strip()
    if not text:
        return []
    patterns = [
        # "像 Neil 发送..." is a common typo for "向 Neil 发送...".
        # Only treat it as a recipient marker when it appears in an explicit send context.
        r"(?:给|向|像)\s*([A-Za-z0-9_\-\u4e00-\u9fff、,，和与\s]+?)\s*(?:发送|发)(?:一条|一个|一下)?(?:消息|信息|message)?",
        r"(?:发给|发送给|给)\s*([A-Za-z0-9_\-\u4e00-\u9fff、,，和与\s]+?)\s*(?:[:：]|说|发送|发|$)",
        r"(?:总结|整理|摘要|提炼|概括)?\s*(?:发|发送|推送|同步|转发)(?:给|到|至)?\s*([A-Za-z][A-Za-z0-9_\-]{1,}|[\u4e00-\u9fff]{2,8})\s*$",
        r"(?:send\s+to|message)\s+([A-Za-z0-9_\-\s,]+?)(?:[:：]|$)",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if not m:
            continue
        raw = (m.group(1) or "").strip()
        raw = re.sub(r"(消息|信息|内容|同事|用户|群聊)$", "", raw).strip()
        parts = [x.strip() for x in re.split(r"[、,，]|和|与|\band\b", raw) if x.strip()]
        return parts[:8]
    return []


def _extract_message_body(text: str, recipients: list[str] | None = None) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    quoted = re.findall(r"[`\"“”'‘’](.*?)[`\"“”'‘’]", text)
    if quoted:
        return quoted[-1].strip()
    if ":" in text or "：" in text:
        tail = re.split(r"[:：]", text, maxsplit=1)[-1].strip()
        if tail:
            return tail
    explicit = re.search(r"(?:内容|消息|正文)\s*(?:是|为|=|:|：)\s*(.+)$", text, re.I)
    if explicit:
        body = (explicit.group(1) or "").strip()
        if body and not any(body == r for r in recipients or []):
            return body
    m = re.search(r"(?:内容|消息|说|发送|发)\s*(?:是|为|:|：)?\s*(.+)$", text, re.I)
    if m:
        body = (m.group(1) or "").strip()
        if body and not any(body == r for r in recipients or []):
            return body
    m = re.search(r"(?:发给|发送给|给).+?(?:[:：]|说)\s*(.+)$", text, re.I)
    if m:
        return (m.group(1) or "").strip()
    return ""


def _extract_web_research_query(text: str, recipients: list[str] | None = None) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    query = raw
    for recipient in recipients or []:
        if not recipient:
            continue
        query = re.sub(rf"(?:发给|发送给|发到|给)\s*{re.escape(str(recipient))}\s*$", "", query, flags=re.I).strip()
    query = re.sub(r"(?:，|,|；|;)?\s*(?:总结|整理|摘要|提炼|概括)(?:后|一下)?\s*(?:发给|发送给|发到|给|发|发送|推送|同步|转发).*$", "", query, flags=re.I).strip()
    query = re.sub(r"(?:，|,|；|;)?\s*(?:然后|再|并且|同时)\s*(?:发给|发送给|发到|给|发|发送|推送|同步|转发).*$", "", query, flags=re.I).strip()
    query = re.sub(r"(?:发给|发送给|发到|给|发|发送|推送|同步|转发)\s*[A-Za-z0-9_\-\u4e00-\u9fff\s,，、和]+$", "", query, flags=re.I).strip()
    query = re.sub(r"(?:，|,|；|;)?\s*(?:总结|整理|摘要|提炼|概括)(?:后|一下)?$", "", query, flags=re.I).strip()
    query = re.sub(r"(?:，|,|；|;)?\s*(?:然后|再|并且|同时)$", "", query, flags=re.I).strip()
    query = re.sub(r"^(?:上网|联网|去网上|帮我|请你|请|搜索|查一下|检索)\s*", "", query, flags=re.I).strip()
    query = re.sub(r"(?:搜索|查一下|检索)\s*", "", query, count=1, flags=re.I).strip()
    query = query.strip(" ，,。.;；:：")
    return query or raw


def _extract_file_path(text: str) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    quoted = re.findall(r"[`\"“”'‘’](.*?)[`\"“”'‘’]", text)
    for value in quoted:
        value = value.strip()
        if value and (("\\" in value) or ("/" in value) or ("." in value)):
            return value
    m = re.search(r"([A-Za-z]:\\[^\s，。；;]+)", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"((?:[\w\-.]+[\\/])+[\w\-.]+)", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:读取|打开|查看|定位|删除|重命名|复制|移动|写入|reveal|open|read|delete|rename|copy|move|write)\s+(?:文件|file)?\s*([^\s，。；;]+)", text, re.I)
    if m:
        return m.group(1).strip()
    return ""


def _extract_file_operation(text: str) -> str:
    low = _lower(text)
    if any(k in low for k in ("write", "save", "create", "new file", "写入", "保存", "创建", "新建")):
        return "write"
    if any(k in low for k in ("所在位置", "资源管理器", "定位", "显示位置")):
        return "reveal"
    if any(k in low for k in ("reveal", "show in explorer", "open location", "瀹氫綅", "鎵€鍦ㄤ綅缃", "璧勬簮绠＄悊鍣")):
        return "reveal"
    if any(k in low for k in ("open", "鎵撳紑", "鍚姩")):
        return "open"
    if any(k in low for k in ("write", "save", "copy", "move", "rename", "delete", "鍐欏叆", "淇濆瓨", "澶嶅埗", "绉诲姩", "閲嶅懡鍚", "鍒犻櫎")):
        return "mutating"
    return "read"


def _extract_file_content(text: str) -> str:
    text = str(text or "").strip()
    if not text:
        return ""
    quoted = re.findall(r"[`\"'“”‘’](.*?)[`\"'“”‘’]", text)
    if quoted:
        return quoted[-1].strip()
    patterns = [
        r"(?:content|text)\s*(?:is|=|:)\s*(.+)$",
        r"(?:内容|写入内容|文本)\s*(?:是|为|=|：|:)?\s*(.+)$",
        r"(?:write|save)\s+(.+?)\s+(?:to|into)\s+",
        r"(?:写入|保存)\s+(.+?)\s+(?:到|进)\s+",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return (match.group(1) or "").strip()
    return ""


def _extract_target(text: str, intent: str, state_snapshot: StateSnapshot, memory_bundle: RelevantMemoryBundle) -> dict[str, Any]:
    if intent == "calculator_calculate":
        expression = _extract_calculator_expression(text)
        return {
            "type": "calculator",
            "name": "Calculator",
            "expression": expression,
            "source": "input_text",
        }
    if intent == "web_research_delivery":
        recipients = _extract_message_recipients(text)
        query = _extract_web_research_query(text, recipients)
        freshness = "latest" if any(term in _lower(text) for term in ("最新", "今天", "今日", "news", "latest", "today")) else ""
        return {
            "type": "web_research_delivery",
            "app": "Lark",
            "query": query,
            "name": query,
            "recipients": recipients,
            "freshness": freshness,
            "delivery_stub": f"网页研究摘要生成中：{query}" if query else "网页研究摘要生成中",
            "source": "input_text",
        }
    if intent == "message_send":
        recipients = _extract_message_recipients(text)
        message = _extract_message_body(text, recipients)
        message_source = "input_text"
        if (not message or _looks_like_recent_message_reference(text)) and memory_bundle:
            remembered_message, remembered_source = _recent_message_body(memory_bundle)
            if remembered_message:
                message = remembered_message
                message_source = "recent_memory"
        explicit_app = _explicit_app_from_text(text)
        correction = {} if explicit_app else _app_correction_candidate(text, intent)
        app = explicit_app or correction.get("name") or "Lark"
        if recipients or message or app == "Lark":
            target = {
                "type": "lark_message",
                "app": app,
                "recipients": recipients,
                "message": message,
                "source": "input_text",
                "message_source": message_source,
            }
            if message_source == "recent_memory":
                target["message_memory_source"] = remembered_source
            if correction:
                target.update(
                    {
                        "source": correction.get("source") or "entity_correction_candidate",
                        "requires_entity_confirmation": bool(correction.get("requires_confirmation", True)),
                        "heard_as": correction.get("heard_as"),
                        "surface_norm": correction.get("surface_norm") or normalize_entity_surface(str(correction.get("heard_as") or "")),
                        "candidate_alias": correction.get("alias"),
                        "entity_score": correction.get("score"),
                    }
                )
            return target
    if intent == "file_operation":
        path = _extract_file_path(text)
        if path:
            return {
                "type": "file",
                "path": path,
                "name": path,
                "operation": _extract_file_operation(text),
                "content": _extract_file_content(text),
                "source": "input_text",
            }
    explicit_app = _explicit_app_from_text(text)
    if explicit_app:
        return {"type": "app", "name": explicit_app, "source": "input_text"}
    correction = _app_correction_candidate(text, intent)
    if correction:
        return {
            "type": "app",
            "name": correction.get("name"),
            "source": correction.get("source") or "entity_correction_candidate",
            "requires_entity_confirmation": bool(correction.get("requires_confirmation", True)),
            "heard_as": correction.get("heard_as"),
            "surface_norm": correction.get("surface_norm") or normalize_entity_surface(str(correction.get("heard_as") or "")),
            "candidate_alias": correction.get("alias"),
            "entity_score": correction.get("score"),
        }
    if intent in {"close_app", "switch_app"}:
        active = _active_window_app(state_snapshot)
        recent = _recent_action_target(memory_bundle)
        if recent and active and _is_control_surface_app(active):
            return {"type": "app", "name": recent, "source": "recent_action_memory"}
        if active:
            return {"type": "app", "name": active, "source": "active_window"}
        if recent:
            return {"type": "app", "name": recent, "source": "recent_action_memory"}
    return {}


def _input_context_from_envelope(envelope: AgentInputEnvelope) -> dict[str, Any]:
    evidence = envelope.modality_evidence or {}
    adapter = evidence.get("input_adapter") if isinstance(evidence.get("input_adapter"), dict) else {}
    voice_norm = evidence.get("voice_language_normalization") if isinstance(evidence.get("voice_language_normalization"), dict) else {}
    context: dict[str, Any] = {
        "source": envelope.source.value,
        "raw_text": envelope.raw_text,
        "normalized_text": envelope.normalized_text,
    }
    constraint_text = f"{envelope.raw_text or ''} {envelope.normalized_text or ''}".lower()
    if "dry-run" in constraint_text or "dry run" in constraint_text or "preview only" in constraint_text or "只演练" in constraint_text or "不要发送" in constraint_text:
        context["dry_run"] = True
        context["send_allowed"] = False
        context["delivery_mode"] = "dry_run"
    if "live-run" in constraint_text or "live run" in constraint_text or "真实发送" in constraint_text or "立即发送" in constraint_text:
        context["dry_run"] = False
        context["send_allowed"] = True
        context["delivery_mode"] = "live_run"
    if envelope.confidence is not None:
        context["confidence"] = envelope.confidence
    if adapter:
        context["adapter_changed"] = bool(adapter.get("changed"))
        context["adapter_steps"] = adapter.get("steps") or []
    if voice_norm:
        context["voice_language"] = {
            "pending_confirmation_detected": bool(voice_norm.get("pending_confirmation_detected")),
            "pending_cancellation_detected": bool(voice_norm.get("pending_cancellation_detected")),
            "corrections": (voice_norm.get("correction") or {}).get("corrections") or [],
            "suspect_tokens": (voice_norm.get("correction") or {}).get("suspect_tokens") or [],
        }
    return context


def _attach_input_context(target: dict[str, Any], envelope: AgentInputEnvelope) -> dict[str, Any]:
    if not target:
        return target
    enriched = dict(target)
    enriched.setdefault("input_context", _input_context_from_envelope(envelope))
    return enriched


def _task_type_for_intent(intent: str) -> str:
    if intent == "calculator_calculate":
        return "calculator_calculate"
    if intent in {"open_app", "close_app", "switch_app"}:
        return "app_control"
    if intent == "message_send":
        return "message_delivery"
    if intent == "web_research_delivery":
        return "web_research_delivery"
    if intent == "file_operation":
        return "file_operation"
    return "conversation"


def _tool_for_intent(intent: str, target: dict[str, Any] | None = None) -> str:
    target = target or {}
    if intent == "calculator_calculate":
        return "mcp:windows_calculator_calculate"
    if intent == "file_operation":
        operation = str(target.get("operation") or "read").strip().lower()
        if operation == "open":
            return "mcp:windows_file_open"
        if operation == "reveal":
            return "mcp:windows_file_reveal_in_explorer"
        if operation == "write":
            return "core:fs_write"
        return "core:fs_read"
    return {
        "open_app": "mcp:windows_open_app",
        "close_app": "mcp:windows_window_close",
        "switch_app": "mcp:windows_window_switch",
        "web_research_delivery": "mcp:web_research_delivery",
        "message_send": "mcp:windows_lark_send_message"
        if (target.get("recipients") and str(target.get("message") or "").strip())
        else "",
    }.get(intent, "")


def _merge_target_patch(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    if not patch:
        return dict(base or {})
    merged = dict(base or {})
    for key, value in patch.items():
        if value in (None, "", []):
            continue
        merged[key] = value
    for key in ("recipients", "message", "expression", "path", "content", "operation"):
        if key in base and base.get(key) not in (None, "", []) and patch.get(key) in (None, "", []):
            merged[key] = base.get(key)
    return merged


def _workflow_target_from_override(
    text: str,
    task_type: str,
    base: dict[str, Any],
    patch: dict[str, Any],
) -> dict[str, Any]:
    target = dict(patch or {})
    target.setdefault("type", task_type)
    target.setdefault("source", "semantic_workflow_override")
    target.setdefault("app", "Lark")
    recipients = base.get("recipients") if isinstance(base.get("recipients"), list) else []
    if recipients and not target.get("recipients"):
        target["recipients"] = recipients
    message = str(base.get("message") or "").strip()
    if task_type == "codex_ask_lark_send" and message and not target.get("feature_query"):
        target["feature_query"] = message
    if not target.get("feature_query"):
        target["feature_query"] = str(text or "").strip()
    if task_type == "web_research_delivery":
        target["type"] = "web_research_delivery"
        target["app"] = "Lark"
        if not target.get("query"):
            target["query"] = _extract_web_research_query(text, recipients)
        target.setdefault("name", target.get("query") or "")
        if not target.get("freshness"):
            target["freshness"] = "latest" if any(term in _lower(text) for term in ("最新", "今天", "今日", "news", "latest", "today")) else ""
        target.setdefault("delivery_stub", f"网页研究摘要生成中：{target.get('query') or str(text or '').strip()}")
    return target


def _risk_for(intent: str, target: dict[str, Any], state_snapshot: StateSnapshot) -> tuple[RiskLevel, bool, str]:
    if intent == "conversation":
        return RiskLevel.LOW, False, ""
    if intent == "calculator_calculate":
        return RiskLevel.LOW, False, ""
    risk_state = state_snapshot.risk_state or {}
    active = state_snapshot.active_window or {}
    unsaved = (
        _truthy_risk(risk_state.get("unsaved_documents"))
        or _truthy_risk(risk_state.get("unsaved_document"))
        or _truthy_risk(active.get("unsaved"))
        or "unsaved" in [str(x).lower() for x in active.get("risk_flags", [])]
    )
    if intent == "close_app" and unsaved:
        name = target.get("name") or "当前应用"
        return RiskLevel.HIGH, True, f"要关闭 {name} 吗？当前状态提示可能有未保存内容。"
    if intent == "message_send":
        return RiskLevel.HIGH, False, ""
    if intent == "web_research_delivery":
        return RiskLevel.HIGH, False, ""
    if intent == "file_operation":
        if str(target.get("operation") or "").lower() in {"mutating", "write"}:
            return RiskLevel.HIGH, True, "文件写入、移动、重命名或删除需要先确认后执行。"
        return RiskLevel.LOW, False, ""
    return RiskLevel.LOW, False, ""


def _voice_needs_clarification(envelope: AgentInputEnvelope, intent: str) -> tuple[bool, str]:
    if envelope.source != InputSource.VOICE or intent == "conversation":
        return False, ""
    modality = envelope.modality_evidence or {}
    voice_norm = modality.get("voice_language_normalization") if isinstance(modality.get("voice_language_normalization"), dict) else {}
    if bool(voice_norm.get("pending_confirmation_detected")) or bool(voice_norm.get("pending_cancellation_detected")):
        return False, ""
    confidence = envelope.confidence
    voice = modality.get("voice") or {}
    if confidence is None:
        raw_conf = voice.get("confidence") or voice.get("stt_confidence")
        try:
            confidence = float(raw_conf) if raw_conf is not None else None
        except Exception:
            confidence = None
    if confidence is not None and confidence < 0.72:
        raw_text = str(envelope.raw_text or "").strip()
        normalized_text = str(envelope.normalized_text or "").strip()
        if raw_text and normalized_text and raw_text != normalized_text:
            return (
                True,
                f"\u6211\u542c\u5230\u7684\u662f\u201c{raw_text}\u201d\uff0c\u7406\u89e3\u4e3a\u201c{normalized_text}\u201d\u3002\u8981\u6309\u8fd9\u4e2a\u6267\u884c\u5417\uff1f\u8bf7\u56de\u590d\u201c\u662f\u201d\u6216\u201c\u5426\u201d\u3002",
            )
        return True, "\u8bed\u97f3\u8bc6\u522b\u7f6e\u4fe1\u5ea6\u504f\u4f4e\uff0c\u8981\u6267\u884c\u8fd9\u4e2a\u64cd\u4f5c\u5417\uff1f\u8bf7\u56de\u590d\u201c\u662f\u201d\u6216\u201c\u5426\u201d\u3002"
    return False, ""

def run_review_board(
    *,
    envelope: AgentInputEnvelope,
    state_snapshot: StateSnapshot,
    memory_bundle: RelevantMemoryBundle,
    review_roles: tuple[str, ...] | None = None,
) -> ReviewSummary:
    text = _norm_text(envelope)
    intent = _detect_intent(text)
    task_type = _task_type_for_intent(intent)
    target = _attach_input_context(_extract_target(text, intent, state_snapshot, memory_bundle), envelope)
    tool = _tool_for_intent(intent, target)
    semantic_candidates = resolve_semantic_intent_candidates(
        text=text,
        base_intent=intent,
        base_task_type=task_type,
        base_target=target,
        state_snapshot=state_snapshot,
        memory_bundle=memory_bundle,
    )
    semantic_override = choose_semantic_override(
        text=text,
        base_intent=intent,
        base_task_type=task_type,
        base_tool=tool,
        base_target=target,
        candidates=semantic_candidates,
    )
    if semantic_override:
        previous_task_type = task_type
        intent = semantic_override.intent or intent
        task_type = semantic_override.task_type or _task_type_for_intent(intent)
        if task_type in {"project_briefing_delivery", "codex_ask_lark_send", "web_research_delivery"} and task_type != previous_task_type:
            target = _workflow_target_from_override(text, task_type, target, semantic_override.target_patch)
        else:
            target = _merge_target_patch(target, semantic_override.target_patch)
        target = _attach_input_context(target, envelope)
        tool = semantic_override.tool or _tool_for_intent(intent, target)
    else:
        task_type = _task_type_for_intent(intent) if not task_type else task_type
        target = _attach_input_context(target, envelope)
        tool = tool or _tool_for_intent(intent, target)
    risk, risk_needs_clarification, risk_question = _risk_for(intent, target, state_snapshot)
    voice_needs_clarification, voice_question = _voice_needs_clarification(envelope, intent)
    missing_target = intent in {"open_app", "close_app", "switch_app"} and not target
    missing_message_slots = intent == "message_send" and (
        not target.get("recipients") or not str(target.get("message") or "").strip()
    )
    missing_web_research_slots = intent == "web_research_delivery" and (
        not target.get("recipients") or not str(target.get("query") or "").strip()
    )
    entity_correction_needs_confirmation = bool(target.get("requires_entity_confirmation"))
    needs_clarification = bool(
        risk_needs_clarification
        or voice_needs_clarification
        or missing_target
        or missing_message_slots
        or missing_web_research_slots
        or entity_correction_needs_confirmation
    )
    clarification_question = risk_question or voice_question
    if entity_correction_needs_confirmation and not clarification_question:
        candidate_name = str(target.get("name") or target.get("app") or "").strip()
        heard_as = str(target.get("heard_as") or "").strip()
        if candidate_name:
            clarification_question = f"我听到的是“{heard_as or '这个应用'}”，你是不是想操作 {candidate_name}？请回复“是”或“否”。"
    if missing_target and not clarification_question:
        clarification_question = "你想操作哪个应用？"
    if missing_message_slots and not clarification_question:
        if not target.get("recipients") and not str(target.get("message") or "").strip():
            clarification_question = "你想把什么内容发给谁？"
        elif not target.get("recipients"):
            clarification_question = "你想把这条消息发给谁？"
        else:
            clarification_question = "你想发送什么内容？"

    if missing_web_research_slots and not clarification_question:
        if not target.get("recipients") and not str(target.get("query") or "").strip():
            clarification_question = "\u4f60\u60f3\u641c\u7d22\u4ec0\u4e48\u5185\u5bb9\uff0c\u5e76\u53d1\u9001\u7ed9\u8c01\uff1f"
        elif not target.get("recipients"):
            clarification_question = "\u7f51\u9875\u7814\u7a76\u6458\u8981\u8981\u53d1\u9001\u7ed9\u8c01\uff1f"
        else:
            clarification_question = "\u4f60\u60f3\u4e0a\u7f51\u641c\u7d22\u4ec0\u4e48\u5185\u5bb9\uff1f"

    review_session_id = _new_id("review")
    candidate_intents = list(dict.fromkeys([intent, *[c.intent for c in semantic_candidates if c.intent]]))
    candidate_entities = [target] if target else []
    candidate_entities.extend([c.target_patch for c in semantic_candidates if c.target_patch])
    semantic_candidate_dicts = [c.to_dict() for c in semantic_candidates]
    capability_candidate_dicts = [c.to_dict() for c in semantic_candidates if c.capability_id or c.descriptor]
    active_review_roles = review_roles or _review_roles_for(
        envelope=envelope,
        intent=intent,
        task_type=task_type,
        target=target,
        risk=risk,
        missing_target=missing_target,
        needs_clarification=needs_clarification,
        memory_bundle=memory_bundle,
    )
    reviews: list[RoleAgentReview] = []
    for role_id in active_review_roles:
        review_input = RoleAgentReviewInput(
            review_session_id=review_session_id,
            turn_id=envelope.turn_id,
            role_id=role_id,
            input_envelope=envelope,
            state_snapshot=state_snapshot,
            memory_bundle=memory_bundle,
            candidate_intents=candidate_intents,
            candidate_entities=candidate_entities,
            constraints={
                "semantic_candidates": semantic_candidate_dicts,
                "capability_candidates": capability_candidate_dicts,
            },
        )
        reviews.append(
            _review_as_role(
                review_input=review_input,
                intent=intent,
                task_type=task_type,
                target=target,
                tool=tool,
                risk=risk,
                needs_clarification=needs_clarification,
                clarification_question=clarification_question,
            )
        )

    selected_roles = _selected_roles_for(intent)
    summary = ReviewSummary(
        review_session_id=review_session_id,
        turn_id=envelope.turn_id,
        reviews=reviews,
        top_intent=intent,
        task_type=task_type,
        target=target,
        selected_roles=selected_roles,
        candidate_tools=[tool] if tool else [],
        risk_level=risk,
        confidence=_summary_confidence(intent, target, needs_clarification),
        needs_clarification=needs_clarification,
        clarification_question=clarification_question,
        conflicts=_collect_conflicts(memory_bundle, state_snapshot, intent, target),
        rationale=_summary_rationale(intent, target, risk, needs_clarification),
        semantic_candidates=semantic_candidate_dicts,
        capability_candidates=capability_candidate_dicts,
    )
    append_event("review_board_summary", envelope.turn_id, summary.to_dict())
    return summary


def _review_roles_for(
    *,
    envelope: AgentInputEnvelope,
    intent: str,
    task_type: str,
    target: dict[str, Any],
    risk: RiskLevel,
    missing_target: bool,
    needs_clarification: bool,
    memory_bundle: RelevantMemoryBundle,
) -> tuple[str, ...]:
    roles = list(_BASE_REVIEW_ROLES)
    if envelope.source == InputSource.VOICE:
        roles.append("VoiceEvidenceAgent")
    if task_type == "app_control":
        roles.extend(["AppAliasResolverAgent", "WindowContextAgent", "AppStateAgent", "AppControlPlannerAgent"])
        if intent == "open_app":
            roles.append("AppLaunchPlannerAgent")
        if intent == "close_app":
            roles.extend(["AppClosePlannerAgent", "ConfirmationAgent"])
    elif task_type == "message_delivery":
        roles.extend(["CommunicationPlannerAgent", "CommunicationWorker", "ConfirmationAgent"])
    elif task_type == "web_research_delivery":
        roles.extend(
            [
                "BrowserWorker",
                "BrowserExecutorAgent",
                "CommunicationPlannerAgent",
                "CommunicationWorker",
                "MessageExecutorAgent",
                "VerificationAgent",
            ]
        )
    elif task_type == "file_operation":
        roles.extend(["FileContextAgent", "FileWorker", "ConfirmationAgent"])
    else:
        roles.extend(["ConversationAgent", "UserFacingReplyAgent"])
    if risk in {RiskLevel.HIGH, RiskLevel.CRITICAL} or missing_target or needs_clarification or memory_bundle.conflicts:
        roles.extend(["ConfirmationAgent", "RecoveryAgent", "RetryPlannerAgent"])
    return tuple(dict.fromkeys(roles))


def _truthy_risk(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "on", "detected", "present", "unsaved"}


def _review_as_role(
    *,
    review_input: RoleAgentReviewInput,
    intent: str,
    task_type: str,
    target: dict[str, Any],
    tool: str,
    risk: RiskLevel,
    needs_clarification: bool,
    clarification_question: str,
) -> RoleAgentReview:
    role_id = review_input.role_id
    rationale: list[str] = []
    evidence: list[dict[str, Any]] = []
    recommended_roles: list[str] = []
    can_execute = False
    confidence = 0.7

    if role_id == "IntentAnalystAgent":
        rationale.append(f"Input maps to intent {intent}.")
        evidence.append({"type": "input_text", "text": _norm_text(review_input.input_envelope)})
        semantic_candidates = review_input.constraints.get("semantic_candidates") or []
        if semantic_candidates:
            evidence.append({"type": "semantic_intent_candidates", "items": semantic_candidates[:5]})
        confidence = 0.86 if intent != "conversation" else 0.78
    elif role_id == "EntityResolverAgent":
        if target:
            rationale.append(f"Resolved target {target.get('name')} from {target.get('source')}.")
            evidence.append({"type": "target", **target})
            confidence = 0.9 if target.get("source") == "input_text" else 0.78
        else:
            rationale.append("No concrete target resolved.")
            confidence = 0.42
    elif role_id == "AmbiguityResolverAgent":
        if target and target.get("source") in {"active_window", "recent_action_memory"}:
            rationale.append("Short reference resolved from state or recent memory.")
        elif intent in {"close_app", "switch_app"}:
            rationale.append("Short reference still ambiguous.")
        else:
            rationale.append("No ambiguous reference found.")
        confidence = 0.8 if target else 0.5
    elif role_id == "VoiceEvidenceAgent":
        voice = review_input.input_envelope.modality_evidence.get("voice") if review_input.input_envelope.modality_evidence else None
        adapter = (
            review_input.input_envelope.modality_evidence.get("input_adapter")
            if review_input.input_envelope.modality_evidence
            else None
        )
        rationale.append("Voice Input Adapter evidence reviewed." if voice or adapter else "No voice evidence attached.")
        evidence.append(
            {
                "type": "voice_evidence",
                "source": review_input.input_envelope.source.value,
                "voice": voice or {},
                "input_adapter": adapter or {},
            }
        )
        confidence = 0.7
    elif role_id == "MemoryRecallAgent":
        rationale.append("RelevantMemoryBundle was reviewed as the only memory source for this turn.")
        growth_refs = _memory_growth_refs(review_input.memory_bundle)
        if growth_refs:
            rationale.append(f"Memory Growth supplied {len(growth_refs)} concept/playbook references.")
        evidence.append(
            {
                "type": "memory_bundle",
                "recent_actions": len(review_input.memory_bundle.recent_actions),
                "active_tasks": len(review_input.memory_bundle.active_tasks),
                "preferences": len(review_input.memory_bundle.user_preferences),
                "corrections": len(review_input.memory_bundle.corrections),
                "conflicts": len(review_input.memory_bundle.conflicts),
                "confidence": review_input.memory_bundle.confidence,
                "memory_growth_refs": growth_refs,
            }
        )
        confidence = max(0.45, min(0.92, review_input.memory_bundle.confidence or 0.62))
    elif role_id == "PreferenceAgent":
        rationale.append("User preferences and safety preferences were reviewed for default choices.")
        evidence.append(
            {
                "type": "preference_memory",
                "user_preferences": len(review_input.memory_bundle.user_preferences),
                "safety_preferences": len(review_input.memory_bundle.safety_preferences),
                "tool_habits": len(review_input.memory_bundle.tool_habits),
            }
        )
        confidence = 0.72 if review_input.memory_bundle.user_preferences or review_input.memory_bundle.safety_preferences else 0.5
    elif role_id == "CorrectionLearningAgent":
        rationale.append("Correction memory was reviewed; any new correction must be written only through MemoryWriteAgent.")
        evidence.append(
            {
                "type": "correction_memory",
                "corrections": len(review_input.memory_bundle.corrections),
                "aliases": len(review_input.memory_bundle.aliases),
            }
        )
        confidence = 0.75 if review_input.memory_bundle.corrections else 0.5
    elif role_id == "DesktopStateReadAgent":
        rationale.append("Desktop state snapshot was reviewed; no live desktop action is performed in review.")
        evidence.append(
            {
                "type": "state_snapshot",
                "active_window": review_input.state_snapshot.active_window,
                "running_apps_count": len(review_input.state_snapshot.running_apps),
                "recent_app_events_count": len(review_input.state_snapshot.recent_app_events),
            }
        )
        confidence = 0.84 if review_input.state_snapshot.active_window else 0.58
    elif role_id == "WindowContextAgent":
        rationale.append("Window context was reviewed for active-window and unsaved-content signals.")
        evidence.append({"type": "window_context", "active_window": review_input.state_snapshot.active_window})
        confidence = 0.82 if review_input.state_snapshot.active_window else 0.5
    elif role_id == "AppStateAgent":
        rationale.append("App state was reviewed from running apps and recent app events.")
        evidence.append(
            {
                "type": "app_state",
                "running_apps_count": len(review_input.state_snapshot.running_apps),
                "recent_app_events_count": len(review_input.state_snapshot.recent_app_events),
            }
        )
        confidence = 0.78
    elif role_id == "FileContextAgent":
        rationale.append("File context was reviewed without mutating files.")
        if target:
            evidence.append({"type": "file_context", **target})
        confidence = 0.78 if target else 0.5
    elif role_id == "SafetyAgent":
        rationale.append(f"Risk classified as {risk.value}.")
        evidence.append(
            {
                "type": "risk_state",
                "active_window": review_input.state_snapshot.active_window,
                "risk_state": review_input.state_snapshot.risk_state,
            }
        )
        confidence = 0.83
    elif role_id == "PermissionAgent":
        rationale.append("Permission boundary reviewed; final authorization remains with the Cognitive Kernel.")
        evidence.append({"type": "permission_review", "intent": intent, "task_type": task_type, "risk": risk.value})
        confidence = 0.8
    elif role_id == "PrivacyAgent":
        rationale.append("Privacy exposure reviewed for files, contacts, messages, and outbound side effects.")
        evidence.append({"type": "privacy_review", "task_type": task_type, "target_type": target.get("type") if target else ""})
        confidence = 0.82 if task_type in {"message_delivery", "file_operation"} else 0.68
    elif role_id == "ConfirmationAgent":
        rationale.append("Confirmation need reviewed for ambiguity or elevated risk.")
        evidence.append({"type": "confirmation_review", "needs_clarification": needs_clarification, "question": clarification_question})
        confidence = 0.84 if needs_clarification else 0.62
    elif role_id == "AppAliasResolverAgent":
        if target:
            rationale.append(f"App alias resolution produced target {target.get('name')} from {target.get('source')}.")
            evidence.append({"type": "app_alias", **target})
            confidence = 0.88
        else:
            rationale.append("No app alias was resolved.")
            confidence = 0.44
    elif role_id == "AppControlPlannerAgent":
        if task_type in {"app_control", "calculator_calculate"}:
            recommended_roles.append("AppControlExecutorAgent")
            rationale.append(f"App control intent can use {tool}.")
            confidence = 0.84 if target else 0.45
        else:
            rationale.append("Not an app-control task.")
            confidence = 0.5
    elif role_id == "AppLaunchPlannerAgent":
        if intent == "open_app":
            recommended_roles.append("AppControlExecutorAgent")
            rationale.append(f"App launch can be authorized through {tool}.")
            confidence = 0.86 if target else 0.45
        else:
            rationale.append("Not an app-launch task.")
            confidence = 0.45
    elif role_id == "AppClosePlannerAgent":
        if intent == "close_app":
            recommended_roles.append("AppControlExecutorAgent")
            rationale.append(f"App close can be authorized through {tool} after risk review.")
            confidence = 0.84 if target else 0.45
        else:
            rationale.append("Not an app-close task.")
            confidence = 0.45
    elif role_id in {"CommunicationPlannerAgent", "CommunicationWorker"}:
        if task_type in {"message_delivery", "web_research_delivery"}:
            recommended_roles.append("MessageExecutorAgent")
            rationale.append("Communication task can be planned for MessageExecutorAgent after safety and privacy review.")
            evidence.append({"type": "communication_target", **target} if target else {"type": "communication_target"})
            has_payload = target.get("message") or target.get("query") or target.get("delivery_stub")
            confidence = 0.82 if target.get("recipients") and has_payload else 0.55
        else:
            rationale.append("Not a communication task.")
            confidence = 0.45
    elif role_id in {"BrowserWorker", "BrowserExecutorAgent"}:
        if task_type == "web_research_delivery":
            recommended_roles.append("BrowserExecutorAgent")
            rationale.append("Web research can be searched, fetched, and summarized under manifest-driven BrowserExecutorAgent steps.")
            evidence.append({"type": "web_research_target", **target} if target else {"type": "web_research_target"})
            confidence = 0.84 if target.get("query") else 0.55
        else:
            rationale.append("Not a browser research task.")
            confidence = 0.45
    elif role_id == "FileWorker":
        if task_type == "file_operation":
            recommended_roles.append("FileExecutorAgent")
            rationale.append("File task can be planned for FileExecutorAgent within WorkOrder scope.")
            evidence.append({"type": "file_target", **target} if target else {"type": "file_target"})
            confidence = 0.82 if target else 0.5
        else:
            rationale.append("Not a file task.")
            confidence = 0.45
    elif role_id == "ConversationAgent":
        if intent == "conversation":
            recommended_roles.append("UserFacingReplyAgent")
            rationale.append("Input should be answered conversationally.")
            confidence = 0.88
        else:
            rationale.append("Input is not primarily conversational.")
            confidence = 0.4
    elif role_id == "UserFacingReplyAgent":
        rationale.append("Final wording should be generated only after TurnClosure or kernel decision boundaries are known.")
        recommended_roles.append("UserFacingReplyAgent")
        confidence = 0.82 if intent == "conversation" else 0.62
    elif role_id == "ConsistencyCheckAgent":
        rationale.append("Consistency between memory, state, target, and intent was reviewed.")
        evidence.append({"type": "consistency", "conflicts": review_input.memory_bundle.conflicts})
        confidence = 0.8 if not review_input.memory_bundle.conflicts else 0.6
    elif role_id in {"RecoveryAgent", "RetryPlannerAgent"}:
        rationale.append("Recovery options are available if execution verification fails or ambiguity remains.")
        evidence.append({"type": "recovery_readiness", "risk": risk.value, "needs_clarification": needs_clarification})
        confidence = 0.74
    else:
        rationale.append(f"{role_id} is registered for this stage but has no specialized review adapter yet.")
        confidence = 0.5

    return RoleAgentReview(
        review_id=_new_id("role_review"),
        review_session_id=review_input.review_session_id,
        turn_id=review_input.turn_id,
        role_id=role_id,
        candidate_intents=[intent],
        candidate_entities=[target] if target else [],
        confidence=confidence,
        risk_level=risk,
        recommended_roles=recommended_roles,
        proposed_task_type=task_type,
        proposed_tool=tool,
        rationale=rationale,
        evidence=evidence,
        needs_clarification=needs_clarification,
        clarification_question=clarification_question,
        can_execute=can_execute,
    )


def _selected_roles_for(intent: str) -> list[str]:
    if intent == "calculator_calculate":
        return _dedupe_roles(
            [
                "MemoryRecallAgent",
                "DesktopStateReadAgent",
                "AppAliasResolverAgent",
                "AppControlPlannerAgent",
                "SafetyAgent",
                "PermissionAgent",
                "AppControlExecutorAgent",
                "VerificationAgent",
                "AuditAgent",
                "RecoveryAgent",
                "RetryPlannerAgent",
                "MemoryWriteAgent",
                "UserFacingReplyAgent",
            ]
        )
    if intent == "open_app":
        return _dedupe_roles(
            [
                "MemoryRecallAgent",
                "DesktopStateReadAgent",
                "AppAliasResolverAgent",
                "AppLaunchPlannerAgent",
                "AppControlPlannerAgent",
                "SafetyAgent",
                "PermissionAgent",
                "AppControlExecutorAgent",
                "VerificationAgent",
                "AuditAgent",
                "RecoveryAgent",
                "RetryPlannerAgent",
                "MemoryWriteAgent",
                "UserFacingReplyAgent",
            ]
        )
    if intent == "close_app":
        return _dedupe_roles(
            [
                "MemoryRecallAgent",
                "DesktopStateReadAgent",
                "WindowContextAgent",
                "AppStateAgent",
                "AppClosePlannerAgent",
                "SafetyAgent",
                "PermissionAgent",
                "ConfirmationAgent",
                "AppControlExecutorAgent",
                "VerificationAgent",
                "AuditAgent",
                "RecoveryAgent",
                "RetryPlannerAgent",
                "MemoryWriteAgent",
                "UserFacingReplyAgent",
            ]
        )
    if intent == "switch_app":
        return _dedupe_roles(
            [
                "MemoryRecallAgent",
                "DesktopStateReadAgent",
                "WindowContextAgent",
                "AppControlPlannerAgent",
                "SafetyAgent",
                "PermissionAgent",
                "AppControlExecutorAgent",
                "VerificationAgent",
                "AuditAgent",
                "RecoveryAgent",
                "MemoryWriteAgent",
                "UserFacingReplyAgent",
            ]
        )
    if intent == "message_send":
        return _dedupe_roles(
            [
                "MemoryRecallAgent",
                "EntityResolverAgent",
                "CommunicationPlannerAgent",
                "CommunicationWorker",
                "SafetyAgent",
                "PermissionAgent",
                "PrivacyAgent",
                "ConfirmationAgent",
                "MessageExecutorAgent",
                "VerificationAgent",
                "AuditAgent",
                "RecoveryAgent",
                "RetryPlannerAgent",
                "MemoryWriteAgent",
                "UserFacingReplyAgent",
            ]
        )
    if intent == "web_research_delivery":
        return _dedupe_roles(
            [
                "MemoryRecallAgent",
                "EntityResolverAgent",
                "BrowserWorker",
                "BrowserExecutorAgent",
                "CommunicationPlannerAgent",
                "CommunicationWorker",
                "SafetyAgent",
                "PermissionAgent",
                "PrivacyAgent",
                "MessageExecutorAgent",
                "VerificationAgent",
                "AuditAgent",
                "RecoveryAgent",
                "RetryPlannerAgent",
                "MemoryWriteAgent",
                "UserFacingReplyAgent",
            ]
        )
    if intent == "file_operation":
        return _dedupe_roles(
            [
                "MemoryRecallAgent",
                "EntityResolverAgent",
                "FileContextAgent",
                "FileWorker",
                "SafetyAgent",
                "PermissionAgent",
                "PrivacyAgent",
                "ConfirmationAgent",
                "FileExecutorAgent",
                "VerificationAgent",
                "AuditAgent",
                "RecoveryAgent",
                "RetryPlannerAgent",
                "MemoryWriteAgent",
                "UserFacingReplyAgent",
            ]
        )
    return _dedupe_roles(["MemoryRecallAgent", "ConversationAgent", "UserFacingReplyAgent", "MemoryWriteAgent"])


def _dedupe_roles(roles: list[str]) -> list[str]:
    return list(dict.fromkeys(roles))


def _summary_confidence(intent: str, target: dict[str, Any], needs_clarification: bool) -> float:
    if needs_clarification:
        return 0.52
    if intent == "calculator_calculate" and target.get("expression"):
        return 0.86
    if intent == "web_research_delivery" and target.get("query") and target.get("recipients"):
        return 0.84
    if intent in {"open_app", "close_app", "switch_app"} and target:
        return 0.84
    if intent == "conversation":
        return 0.78
    return 0.68


def _collect_conflicts(
    memory_bundle: RelevantMemoryBundle,
    state_snapshot: StateSnapshot,
    intent: str,
    target: dict[str, Any],
) -> list[dict[str, Any]]:
    conflicts = list(memory_bundle.conflicts or [])
    if intent == "close_app" and target.get("source") == "recent_action_memory":
        active = _active_window_app(state_snapshot)
        if active and _normalize_app_name(active) != _normalize_app_name(str(target.get("name") or "")):
            conflicts.append(
                {
                    "conflict_type": "memory_vs_state",
                    "memory_claim": target,
                    "state_claim": {"active_window": active},
                    "severity": "medium",
                    "suggested_resolution": "prefer active_window unless user explicitly says recently opened app",
                }
            )
    return conflicts


def _summary_rationale(intent: str, target: dict[str, Any], risk: RiskLevel, needs_clarification: bool) -> list[str]:
    rationale = [f"ReviewBoard top intent: {intent}.", f"Risk: {risk.value}."]
    if target:
        rationale.append(f"Target resolved: {target.get('name')} via {target.get('source')}.")
    if intent == "web_research_delivery":
        rationale.append("Web research delivery will use capability metadata to search, fetch, summarize, send, and verify.")
    if needs_clarification:
        rationale.append("Execution blocked until ambiguity or safety concern is clarified.")
    return rationale


def _memory_growth_refs(memory_bundle: RelevantMemoryBundle, limit: int = 6) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for bucket_name in ("project_facts", "tool_habits", "failure_hints", "historical_task_summaries"):
        for item in getattr(memory_bundle, bucket_name, []) or []:
            if not str(item.source or "").startswith("Memory Growth"):
                continue
            refs.append(
                {
                    "bucket": bucket_name,
                    "memory_id": item.memory_id,
                    "memory_type": item.memory_type,
                    "source": item.source,
                    "confidence": item.confidence,
                    "artifact_path": _memory_growth_artifact_path(item.content),
                    "preview": item.content[:360],
                    "relevance_reason": item.relevance_reason,
                }
            )
            if len(refs) >= limit:
                return refs
    return refs


def _memory_growth_artifact_path(content: str) -> str:
    match = re.search(r"(?:concept|playbook) path=([^;]+)", str(content or ""))
    return match.group(1).strip() if match else ""
