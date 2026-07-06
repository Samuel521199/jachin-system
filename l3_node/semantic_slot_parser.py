"""Semantic slot parser for OS mission routing.

This deterministic layer is the testable safety net under the LLM intent
parser. It extracts the stable contract fields that downstream workflows need.
"""
from __future__ import annotations

import re
from pathlib import Path

from l3_node.mission_intent_schema import MissionIntent, MissionRiskLevel, MissionSlots, MissionTaskType
from l3_node.voice_entity_correction import correct_voice_entities
from l3_node.voice_semantic_guard import apply_voice_semantic_guard


_ZH_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def _has_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def _extract_windows_path(text: str) -> str:
    quoted = re.search(r"[`\"'\u201c\u201d]([A-Za-z]:[\\/][^`\"'\u201c\u201d]+)[`\"'\u201c\u201d]", text)
    if quoted:
        return quoted.group(1).strip()
    m = re.search(r"([A-Za-z]:[\\/][^\s\uff0c\u3002\uff1b;,]+(?:[\\/][^\s\uff0c\u3002\uff1b;,]+)*)", text)
    return m.group(1).strip() if m else ""


def _zh_int(raw: str, default: int = 3) -> int:
    s = str(raw or "").strip()
    if not s:
        return default
    try:
        return int(s)
    except ValueError:
        pass
    if s in _ZH_DIGITS:
        return _ZH_DIGITS[s]
    if s == "十":
        return 10
    if "十" in s:
        left, right = s.split("十", 1)
        tens = _ZH_DIGITS.get(left, 1 if left == "" else 0)
        ones = _ZH_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    return default


def _extract_since_days(text: str) -> int:
    m = re.search(r"(?:最近|近|过去|这几天|这两天|鏈€杩|杩囧幓)\s*([0-9一二两三四五六七八九十]+)?\s*(?:天|日|澶)", text, re.I)
    if m:
        return max(1, min(30, _zh_int(m.group(1) or "3", 3)))
    if re.search(r"(?:一周|这周|本周|week)", text, re.I):
        return 7
    if re.search(r"(?:今天|今日|today)", text, re.I):
        return 1
    return 3


def _strip_recipient_prefix(text: str) -> str:
    s = str(text or "").strip()
    s = re.sub(r"^(?:群聊|群|单聊|联系人|同事|recipient|to)\s*[:：]?\s*", "", s, flags=re.I)
    return s.strip(" \t\r\n。.!！?？,，:：;；\"'“”‘’")


def _split_recipients(text: str) -> list[str]:
    raw = _strip_recipient_prefix(text)
    raw = re.sub(r"(?:这次|本次)?(?:不要|不用)\s*(?:发给|发送给|发到|发送到).*$", "", raw).strip()
    raw = re.split(r"(?:然后|之后|再|并且|并|发送|发|说|告诉|message|with)\b", raw, maxsplit=1, flags=re.I)[0]
    raw = raw.strip(" \t\r\n。.!！?？,，:：;；")
    parts = re.split(r"\s*(?:、|，|,|；|;|和|与|及|以及|and)\s*", raw)
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        name = _strip_recipient_prefix(part)
        name = re.sub(r"(?:都)?(?:发送|发)(?:同样的)?(?:消息)?$", "", name).strip()
        if not name or name.lower() in {"我", "我这边", "自己", "me", "myself"}:
            continue
        key = name.lower()
        if key not in seen:
            seen.add(key)
            out.append(name)
    return out


def _extract_recipients(text: str, *, allow_trailing_to: bool = False) -> list[str]:
    post_patterns = (
        r"(?:发给|发送给|发到|发送到|发往|转给)\s*([^。！？!?\n]+)$",
        r"(?:鍙戠粰|鍙戦€佺粰|鍙戝埌|鍙戦€佸埌|杞粰)\s*([^。！？!?\n]+)$",
    )
    for pat in post_patterns:
        matches = list(re.finditer(pat, text, re.I))
        if matches:
            tail = matches[-1].group(1)
            tail = re.split(r"(?:，然后|, then|然后|之后|再|并且|并)", tail, maxsplit=1)[0]
            return _split_recipients(tail)
    direct = re.search(r"(?:\u7ed9|\u5411)\s*([A-Za-z\u4e00-\u9fff][A-Za-z\u4e00-\u9fff\s.-]{1,40})\s*(?:\u53d1\u9001|\u53d1)(?:\u4e00\u6761|\u4e2a|\u4e00\u4e0b)?(?:\u6d88\u606f|\u4fe1\u606f|message)?", text, re.I)
    if direct:
        return _split_recipients(direct.group(1))

    pre_patterns = (
        r"(?:给|向)\s*(.+?)\s*(?:发送|发|说|告诉)\s+",
        r"缁.?\s*(.+?)\s*(?:鍙戦€|鍙|璇|鍛婅瘔)",
    )
    for pat in pre_patterns:
        m = re.search(pat, text, re.I)
        if m:
            return _split_recipients(m.group(1))
    if allow_trailing_to:
        tail = re.search(r"(?:给|到|缁.|缁欏埌)\s*([^\s，,。.!！?？；;]+)$", text, re.I)
        if tail:
            return _split_recipients(tail.group(1))
    return []


def _extract_project_name(text: str, project_path: str) -> str:
    if project_path:
        return Path(project_path).name or "project"
    patterns = (
        r"(?:让|请)?\s*Codex\s*(?:分析|总结|查看|搜索)?\s*([A-Za-z][A-Za-z0-9_.-]{1,80}|[\u4e00-\u9fffA-Za-z0-9_.-]{2,80})\s*的",
        r"(?:总结|分析|看看|看下|查看|搜索|整理|梳理)\s*([A-Za-z][A-Za-z0-9_.-]{1,80})\s*(?:最近|项目|最新|的|这几天|这两天)?",
        r"([A-Za-z][A-Za-z0-9_.-]{1,80})\s*(?:项目)?(?:最近|最新|这几天|这两天|鏈€杩).*(?:发给|发送给|发到|发送到|鍙戠粰|鍙戦€佺粰)",
    )
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if not m:
            continue
        name = str(m.group(1) or "").strip()
        if name and name.lower() not in {"codex", "windows", "lark", "ai"}:
            return name
    # Mojibake fixture fallback: preserve ASCII project names embedded in the text.
    for name in re.findall(r"\b[A-Z][A-Za-z0-9_.-]{1,80}\b", text):
        if name.lower() not in {"codex", "windows", "lark", "vivian", "neil"}:
            return name
    return ""


def _extract_feature_query(text: str, project_name: str, project_path: str) -> str:
    head = re.split(r"(?:发给|发送给|发到|发送到|发往|转给|鍙戠粰|鍙戦€佺粰|鍙戝埌|鍙戦€佸埌|杞粰)", text, maxsplit=1, flags=re.I)[0]
    feature = head
    if project_path:
        feature = feature.replace(project_path, "项目")
    if project_name:
        feature = re.sub(re.escape(project_name), "项目", feature, flags=re.I)
    feature = re.sub(r"^(?:请|帮我|麻烦)?\s*(?:让\s*)?Codex\s*", "", feature, flags=re.I)
    feature = re.sub(r"^(?:请|帮我|麻烦)?\s*(?:总结|分析|看看|看下|查看|搜索|整理|梳理)\s*", "", feature)
    feature = re.sub(r"(?:最近|近|过去)\s*[0-9一二两三四五六七八九十]+\s*天", "", feature)
    feature = feature.strip(" \t\r\n，,。.;；")
    if re.search(r"一条一条|按条|条列|几条|几项|bullet|list|鍑犳潯", text, re.I):
        feature = (feature + "；请按条列输出").strip("；")
    return feature or "latest project progress"


def _extract_lark_message(text: str, recipients: list[str]) -> str:
    bare_send = re.search(r"(?:\u7ed9|\u5411)\s*[A-Za-z\u4e00-\u9fff][A-Za-z\u4e00-\u9fff\s.-]{0,40}\s*(?:\u53d1\u9001|\u53d1)(?:\u4e00\u6761|\u4e2a|\u4e00\u4e0b)?(?:\u6d88\u606f|\u4fe1\u606f|message)?\s*$", text, re.I)
    if bare_send:
        return ""
    marker = re.search(r"(?:\u5185\u5bb9\u662f|\u6d88\u606f\u662f|\u6b63\u6587\u662f|\u8bf4\u7684\u662f|message\s+is|content\s+is)\s*(.+)$", text, re.I)
    if marker:
        return marker.group(1).strip(" \t\r\n,.:;!?\u3002\uff0c\uff01\uff1f\uff1a\uff1b\"'\u201c\u201d\u2018\u2019")

    patterns = (
        r"(?:给|向)\s*.+?\s*(?:发送|发|说|告诉)\s*(.+)$",
        r"缁.?\s*.+?\s*(?:鍙戦€|鍙|璇|鍛婅瘔)\s*(.+)$",
    )
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            return m.group(1).strip(" \t\r\n，,。.!！?？:：\"'“”‘’")
    head = re.split(r"(?:发给|发送给|发到|发送到|发往|转给|鍙戠粰|鍙戦€佺粰|鍙戝埌|鍙戦€佸埌|杞粰)", text, maxsplit=1, flags=re.I)[0]
    msg = re.sub(r"^(?:请|帮我|麻烦)?\s*(?:在\s*)?(?:Lark|飞书|Feishu)?\s*(?:里)?\s*(?:给|向)?\s*", "", head, flags=re.I)
    msg = re.sub(r"^(?:发送|发|说|告诉)\s*", "", msg).strip(" \t\r\n，,。.!！?？")
    if msg in {"消息", "一条消息", "message"}:
        return ""
    return msg


_APP_ALIASES: dict[str, tuple[str, ...]] = {
    "lark": ("lark", "feishu", "flybook", "\u98de\u4e66", "飞书", "椋炰功"),
    "chrome": ("chrome", "google chrome"),
    "vscode": ("vs code", "vscode", "visual studio code"),
    "calculator": ("calculator", "calc", "\u8ba1\u7b97\u5668", "计算器", "璁＄畻鍣"),
    "notepad": ("notepad", "\u8bb0\u4e8b\u672c", "记事本", "璁颁簨"),
    "browser": ("browser", "\u6d4f\u89c8\u5668", "浏览器", "edge", "chrome", "娴忚"),
    "explorer": ("explorer", "\u8d44\u6e90\u7ba1\u7406\u5668", "\u6587\u4ef6\u7ba1\u7406\u5668", "资源管理器", "文件管理器", "璧勬簮", "鏂囦欢"),
    "terminal": ("terminal", "powershell", "cmd", "终端", "cmd.exe"),
    "codex": ("codex",),
}


def _has_negated_entity(text: str, alias_pattern: str) -> bool:
    neg = r"(?:不要|不需要|不用|别|不是|无需|without|do\s*not|don't|dont|no)"
    return bool(re.search(neg + r".{0,12}(?:" + alias_pattern + r")", text, re.I))


def _detect_app_name(text: str) -> str:
    for app, aliases in _APP_ALIASES.items():
        pat = "|".join(re.escape(a) for a in aliases)
        if re.search(pat, text, re.I) and not _has_negated_entity(text, pat):
            return app
    return ""


def _detect_actual_app_name(text: str) -> str:
    return _detect_app_name(text)


def _extract_app_control_target(text: str) -> str:
    if not re.search(r"(?:\u6253\u5f00|\u542f\u52a8|\u5207\u6362\u5230|\u805a\u7126|\u8fd0\u884c|focus|open|launch|start|switch)", text, re.I):
        return ""
    return _detect_actual_app_name(text)

def _normalize_arithmetic_expr(expr: str) -> str:
    table = str.maketrans({"×": "*", "÷": "/", "（": "(", "）": ")"})
    out = str(expr or "").translate(table)
    out = re.sub(r"\s+", "", out).replace("x", "*").replace("X", "*")
    return out.strip(" ,.;!?，。；！？")


def _extract_actual_calculator_expression(text: str) -> str:
    normalized = str(text or "").translate(str.maketrans({"×": "*", "÷": "/", "（": "(", "）": ")"}))
    matches = re.findall(r"[0-9.\s()+\-*/xX]*[0-9][0-9.\s()]*[+\-*/xX][0-9.\s()+\-*/xX]*[0-9][0-9.\s()]*", normalized)
    if matches:
        return _normalize_arithmetic_expr(max(matches, key=len))
    return ""


def _zh_number_token_to_int(token: str) -> int | None:
    if not token:
        return None
    try:
        return int(token)
    except ValueError:
        return _zh_int(token, -1) if _zh_int(token, -1) >= 0 else None


def _extract_calculator_expression(text: str) -> str:
    expr = _extract_actual_calculator_expression(text)
    if expr:
        return expr
    if not re.search(r"计算器|calculator|calc|璁＄畻鍣", text, re.I):
        return ""
    token = r"[0-9一二两三四五六七八九十百千万]+"
    op = r"(?:加|减|乘以|乘|除以|除|\+|\-|x|X|\*|/)"
    m = re.search(f"({token})(?:\s*)({op})(?:\s*)({token})(?:(?:\s*)({op})(?:\s*)({token}))?", text)
    if not m:
        return ""
    op_map = {"加": "+", "减": "-", "乘以": "*", "乘": "*", "除以": "/", "除": "/", "x": "*", "X": "*"}
    first = _zh_number_token_to_int(m.group(1))
    second = _zh_number_token_to_int(m.group(3))
    if first is None or second is None:
        return ""
    expr_parts = [str(first), op_map.get(m.group(2), m.group(2)), str(second)]
    if m.group(4) and m.group(5):
        third = _zh_number_token_to_int(m.group(5))
        if third is not None:
            expr_parts.extend([op_map.get(m.group(4), m.group(4)), str(third)])
    return "".join(expr_parts)


def _detect_output_format(text: str) -> str:
    return "bullet_points" if re.search(r"一条一条|按条|条列|几条|几项|bullet|list|鍑犳潯", text, re.I) else ""


def _looks_like_project_delivery(text: str, recipients: list[str], project_name: str, project_path: str) -> bool:
    if not recipients:
        return False
    if re.search(r"(?:\u5185\u5bb9\u662f|\u6d88\u606f\u662f|\u6b63\u6587\u662f|message\s+is|content\s+is)", text, re.I):
        return False
    if project_name or project_path:
        return bool(re.search(r"总结|分析|看看|查看|整理|梳理|Codex|最近|进展|改动|鎬荤粨|鍒嗘瀽|鐪嬬湅|鏈€杩|杩欏嚑", text, re.I))
    return bool(re.search(r"最近|这几天|这两天|改动|进展|总结|整理|几条|鍑犳潯|鏈€杩|杩欏嚑", text, re.I))


def _extract_codex_query_for_lark_delivery(text: str) -> str:
    raw = str(text or "").strip()
    delivery_split = (
        "(?:然后|之后|再|并且|并)"
        "\\s*(?:把|将)?.*?"
        "(?:lark|feishu|flybook|飞书).*?"
        "(?:发给|发送给|发到|发送到|转给)"
    )
    head = re.split(delivery_split, raw, maxsplit=1, flags=re.I)[0]
    head = re.sub(r"^.*?(?:在|用|打开|切到|进入)?\s*codex\s*(?:里面|里|中)?", "", head, flags=re.I).strip()
    head = re.sub(r"^(?:打开|新建|开)?\s*(?:一个)?\s*(?:会话框|会话|对话框|对话)?[\s，,：:]*", "", head).strip()
    m = re.search(r"(?:问(?:他|它)?|询问(?:他|它)?|让(?:他|它)?(?:回答|回复|告诉我)|提问)\s*(.+)$", head, re.I)
    query = (m.group(1) if m else head).strip(" \t\r\n：:，,。？?！!\"'“”‘’")
    if re.search(r"(?:lark|feishu|flybook|飞书).*?(?:发给|发送给|发到|发送到|转给)", query, re.I):
        query = re.sub(r"(?:然后|之后|再|并且|并)?\s*(?:把|将)?.*$", "", query).strip(" \t\r\n：:，,。？?！!\"'“”‘’")
    return query


def _is_codex_ask_lark_delivery(text: str, recipients: list[str]) -> bool:
    raw = str(text or "")
    has_codex = bool(re.search(r"\bcodex\b|openai\s*codex", raw, re.I))
    has_delivery = bool(re.search(r"(?:lark|feishu|flybook|飞书).*(?:发给|发送给|发到|发送到|转给)|(?:发给|发送给|发到|发送到|转给).*(?:lark|feishu|flybook|飞书)", raw, re.I))
    has_ask = bool(re.search(r"问(?:他|它)?|询问(?:他|它)?|让(?:他|它)?(?:回答|回复|告诉我)|提问|会话|对话", raw, re.I))
    return bool(has_codex and has_delivery and has_ask and recipients)


def parse_mission_intent(user_input: str) -> MissionIntent:
    correction = correct_voice_entities(str(user_input or "").strip())
    text = correction.corrected_text.strip()
    slots = MissionSlots(since_days=_extract_since_days(text))
    if not text:
        return MissionIntent(MissionTaskType.UNKNOWN, 0.0, slots, raw_text=text)

    recipients = _extract_recipients(text, allow_trailing_to=False)
    delivery_recipients = _extract_recipients(text, allow_trailing_to=True)
    project_path = _extract_windows_path(text)
    project_name = _extract_project_name(text, project_path)
    app_name = _detect_actual_app_name(text)
    expression = _extract_actual_calculator_expression(text) or _extract_calculator_expression(text)

    memory_match = re.match(r"^\s*([A-Za-z][A-Za-z0-9_.-]{1,80}|[\u4e00-\u9fff]{2,40})\s*=\s*([A-Za-z]:[\\/].+?)\s*$", text)
    if memory_match:
        slots.project_name = memory_match.group(1).strip()
        slots.project_path = memory_match.group(2).strip()
        return MissionIntent(
            MissionTaskType.PROJECT_MEMORY_UPDATE,
            0.94,
            slots,
            reasoning=["project memory assignment"],
            raw_text=text,
        )

    if _is_codex_ask_lark_delivery(text, delivery_recipients):
        slots.app_name = "codex"
        slots.recipients = delivery_recipients
        slots.feature_query = _extract_codex_query_for_lark_delivery(text)
        missing = []
        if not slots.feature_query:
            missing.append("feature_query")
        if not slots.recipients:
            missing.append("recipients")
        return MissionIntent(
            MissionTaskType.CODEX_ASK_LARK_SEND,
            0.9 if not missing else 0.62,
            slots,
            missing_slots=missing,
            risk_level=MissionRiskLevel.LOW,
            reasoning=["codex_ask_then_lark_delivery", "mission_graph:codex_reply->lark_message"],
            raw_text=text,
        )

    if app_name == "calculator" and expression:
        slots.app_name = "calculator"
        slots.expression = expression
        return MissionIntent(
            MissionTaskType.CALCULATOR_CALCULATE,
            0.9,
            slots,
            reasoning=["calculator expression intent"],
            raw_text=text,
        )

    if _looks_like_project_delivery(text, delivery_recipients, project_name, project_path):
        slots.project_name = project_name
        slots.project_path = project_path
        slots.recipients = delivery_recipients
        slots.feature_query = _extract_feature_query(text, project_name, project_path)
        slots.output_format = _detect_output_format(text)
        missing = []
        if not slots.project_name and not slots.project_path:
            missing.append("project")
        if not slots.recipients:
            missing.append("recipients")
        return MissionIntent(
            MissionTaskType.PROJECT_BRIEFING_DELIVERY,
            0.86 if not missing else 0.68,
            slots,
            missing_slots=missing,
            risk_level=MissionRiskLevel.LOW,
            reasoning=["project briefing delivery"],
            raw_text=text,
        )

    if recipients:
        slots.recipients = recipients
        slots.message = _extract_lark_message(text, recipients)
        missing = [] if slots.message else ["message"]
        if not slots.message and re.search(r"(?:给我|帮我|麻烦).*(?:发消息|发送消息)$", text):
            return MissionIntent(MissionTaskType.UNKNOWN, 0.25, slots, raw_text=text)
        return apply_voice_semantic_guard(MissionIntent(
            MissionTaskType.LARK_MESSAGE_SEND,
            0.82 if slots.message else 0.62,
            slots,
            missing_slots=missing,
            risk_level=MissionRiskLevel.LOW,
            reasoning=["explicit recipient message"],
            raw_text=text,
        ), raw_text=text)

    target_app = _extract_app_control_target(text)
    if target_app:
        slots.app_name = target_app
        return apply_voice_semantic_guard(MissionIntent(
            MissionTaskType.APP_CONTROL,
            0.78,
            slots,
            reasoning=["local app control"],
            raw_text=text,
        ), raw_text=text)

    if re.search(r"系统状态|磁盘|内存|CPU|网络|battery|system status", text, re.I):
        return MissionIntent(MissionTaskType.SYSTEM_STATUS_REPORT, 0.72, slots, reasoning=["system status"], raw_text=text)

    return MissionIntent(MissionTaskType.UNKNOWN, 0.2, slots, raw_text=text)
