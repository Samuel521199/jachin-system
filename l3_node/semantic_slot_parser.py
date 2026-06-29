"""Semantic slot parser for OS mission routing.

This parser deliberately combines broad lexical recall with slot extraction.
An optional LLM-backed parser can be added behind the same schema later, but
the deterministic layer stays as the safety net and testable contract.
"""
from __future__ import annotations

import re
from pathlib import Path

from l3_node.mission_intent_schema import MissionIntent, MissionRiskLevel, MissionSlots, MissionTaskType


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def _has_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def _extract_windows_path(text: str) -> str:
    quoted = re.search(r"[`\"“']([A-Za-z]:[\\/][^`\"”']+)[`\"”']", text)
    if quoted:
        return quoted.group(1).strip()
    m = re.search(r"([A-Za-z]:[\\/][^\s，。；;,]+(?:[\\/][^\s，。；;,]+)*)", text)
    return m.group(1).strip() if m else ""


def _extract_since_days(text: str) -> int:
    m = re.search(r"(?:最近|近|过去)\s*([0-9一二三四五六七八九十两]+)\s*天", text)
    if m:
        raw = m.group(1)
        zh = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        try:
            return max(1, min(30, int(raw)))
        except ValueError:
            return max(1, min(30, zh.get(raw, 3)))
    if re.search(r"(?:最近|近|过去)\s*(?:一)?周|这周|本周", text):
        return 7
    if re.search(r"今天|今日", text):
        return 1
    return 3


def _strip_recipient_prefix(text: str) -> str:
    s = str(text or "").strip()
    s = re.sub(r"^(?:群聊|群|单聊|联系人|同事)\s*[:：]\s*", "", s)
    return s.strip(" \t\r\n。.!！?？")


def _split_recipients(text: str) -> list[str]:
    raw = _strip_recipient_prefix(text)
    raw = re.sub(r"(?:这次|本次)?(?:不要|不)\s*(?:发给|发送给|发到|发送到).*$", "", raw).strip()
    parts = re.split(r"\s*(?:、|，|,|；|;|和|与|及|以及|and)\s*", raw)
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        name = _strip_recipient_prefix(part)
        name = re.sub(r"(?:都)?(?:发送|发)(?:同样的)?(?:消息)?$", "", name).strip()
        if not name:
            continue
        key = name.lower()
        if key not in seen:
            seen.add(key)
            out.append(name)
    return out


def _extract_recipients(text: str) -> list[str]:
    matches = list(re.finditer(r"(?:发给|发送给|发到|发送到|发往|转给)\s*(.+)$", text, re.I))
    if not matches:
        m = re.search(r"给\s*(.+?)\s*(?:发送|发|说|告诉)", text, re.I)
        if m:
            return _split_recipients(m.group(1))
        tail = re.search(r"(?:给|给到)\s*([^，。；;,.!?！？]+)$", text, re.I)
        if tail:
            recipients = _split_recipients(tail.group(1))
            return [r for r in recipients if r not in {"我", "我这边", "自己"}]
        return []
    tail = matches[-1].group(1)
    tail = re.split(r"(?:，然后|, then|然后再|再\s|并\s*$)", tail, maxsplit=1)[0]
    return _split_recipients(tail)


def _extract_project_name(text: str, project_path: str) -> str:
    if project_path:
        return Path(project_path).name or "project"
    patterns = (
        r"(?:让|请)?\s*Codex\s*(?:分析|总结|查看|搜索)?\s*([A-Za-z][A-Za-z0-9_.-]{1,80}|[\u4e00-\u9fffA-Za-z0-9_.-]{2,80})\s*的",
        r"(?:总结|分析|看看|看下|查看|搜索|整理|梳理)\s*([A-Za-z][A-Za-z0-9_.-]{1,80}|[\u4e00-\u9fffA-Za-z0-9_.-]{2,80})\s*(?:最近|近|过去|项目|最新|的|这几天|这两天)",
        r"([A-Za-z][A-Za-z0-9_.-]{1,80})\s*(?:项目)?(?:最近|最新|这几天|这两天).*(?:发给|发送给|发到|发送到)",
    )
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if not m:
            continue
        name = str(m.group(1) or "").strip()
        if name and name.lower() not in {"codex", "windows", "lark"}:
            return name
    return ""


def _extract_feature_query(text: str, project_name: str, project_path: str) -> str:
    head = re.split(r"(?:发给|发送给|发到|发送到|发往|转给)", text, maxsplit=1)[0]
    feature = head
    if project_path:
        feature = feature.replace(project_path, "项目")
    if project_name:
        feature = re.sub(re.escape(project_name), "项目", feature, flags=re.I)
    feature = re.sub(r"^(?:请|帮我|麻烦)?\s*(?:让\s*)?Codex\s*", "", feature, flags=re.I)
    feature = re.sub(r"^(?:请|帮我|麻烦)?\s*(?:总结|分析|看看|看下|查看|搜索|整理|梳理)\s*", "", feature)
    feature = re.sub(r"(?:最近|近|过去)\s*[0-9一二三四五六七八九十两]+\s*天", "", feature)
    feature = feature.strip(" ，,。；;")
    if re.search(r"一条一条|按条|条列|几条|几项|bullet|list", text, re.I):
        feature = (feature + "；请按条列输出").strip("；")
    return feature or "latest project progress"


def _extract_lark_message(text: str, recipients: list[str]) -> str:
    m = re.search(r"给\s*.+?\s*(?:发送|发|说|告诉)\s*(.+)$", text, re.I)
    if m:
        return m.group(1).strip(" ：:，,。")
    head = re.split(r"(?:发给|发送给|发到|发送到|发往|转给)", text, maxsplit=1)[0]
    msg = re.sub(r"^(?:请|帮我|麻烦)?\s*(?:在\s*)?(?:Lark|飞书)?\s*(?:里)?\s*(?:给|向)?\s*", "", head, flags=re.I)
    msg = re.sub(r"^(?:发送|发|说|告诉)\s*", "", msg).strip(" ：:，,。")
    if msg in {"消息", "一条消息"}:
        return ""
    return msg


def parse_mission_intent(user_input: str) -> MissionIntent:
    text = str(user_input or "").strip()
    compact = _compact(text)
    slots = MissionSlots(since_days=_extract_since_days(text))
    reasoning: list[str] = []
    if not text:
        return MissionIntent(MissionTaskType.UNKNOWN, 0.0, slots, raw_text=text)

    recipients = _extract_recipients(text)
    project_path = _extract_windows_path(text)
    project_name = _extract_project_name(text, project_path)

    if project_path and re.search(r"(?:记住|保存|设置)?\s*[\u4e00-\u9fffA-Za-z0-9_.-]{1,80}\s*(?:=|＝|是|路径是|项目路径是)", text):
        name_match = re.search(r"(?:记住|保存|设置)?\s*([\u4e00-\u9fffA-Za-z0-9_.-]{1,80})\s*(?:=|＝|是|路径是|项目路径是)", text)
        slots.project_name = (name_match.group(1).strip() if name_match else project_name) or project_name
        slots.project_path = project_path
        missing = []
        if not slots.project_name:
            missing.append("project_name")
        if not slots.project_path:
            missing.append("project_path")
        return MissionIntent(
            MissionTaskType.PROJECT_MEMORY_UPDATE,
            0.9 if not missing else 0.62,
            slots,
            missing_slots=missing,
            reasoning=["project memory assignment"],
            raw_text=text,
        )

    summary_like = _has_any(text, (r"总结|分析|看看|看下|看一下|查看|搜索|整理|梳理|复盘", r"最新进展|做了什么|干了啥|改了啥|workflow|bug|代码|项目|目录|文件夹|这块"))
    send_like = _has_any(text, (r"发给|发送给|发到|发送到|发往|转给|发群里", r"给.+(?:发送|发|说|告诉)", r"(?:给|给到)\s*[^，。；;,.!?！？]+$"))
    project_like = bool(project_path or project_name or re.search(r"项目|这个项目|目录|代码|workflow|OS\s*assistant|OS\s*助手|这块|bug|最新进展|做了什么|干了啥|改了啥|改动|变更", text, re.I))
    if summary_like and send_like and project_like:
        slots.project_name = project_name
        slots.project_path = project_path
        slots.feature_query = _extract_feature_query(text, project_name, project_path)
        slots.recipients = recipients
        if re.search(r"\bbug\b|问题|报错|异常", text, re.I):
            slots.bug_query = slots.feature_query
        if re.search(r"一条一条|按条|条列|几条|几项|bullet|list", text, re.I):
            slots.output_format = "bullet_points"
        missing = []
        if not (slots.project_name or slots.project_path):
            missing.append("project")
        if not slots.recipients:
            missing.append("recipients")
        confidence = 0.62 + (0.13 if recipients else 0) + (0.12 if project_name or project_path else 0) + (0.05 if "codex" in compact else 0) + (0.05 if slots.output_format else 0)
        reasoning.append("summary+send+project signals")
        return MissionIntent(
            MissionTaskType.PROJECT_BRIEFING_DELIVERY,
            min(confidence, 0.95),
            slots,
            missing_slots=missing,
            risk_level=MissionRiskLevel.LOW,
            reasoning=reasoning,
            raw_text=text,
        )

    if send_like and recipients:
        slots.recipients = recipients
        slots.message = _extract_lark_message(text, recipients)
        missing = [] if slots.message else ["message"]
        return MissionIntent(
            MissionTaskType.LARK_MESSAGE_SEND,
            0.78 if slots.message else 0.58,
            slots,
            missing_slots=missing,
            reasoning=["send+recipient signals"],
            raw_text=text,
        )

    if _has_any(text, (r"打开|启动|切换到|聚焦",)):
        app_aliases = {
            "lark": ("lark", "飞书"),
            "calculator": ("calculator", "计算器"),
            "notepad": ("notepad", "记事本"),
            "browser": ("browser", "浏览器", "edge", "chrome"),
            "explorer": ("explorer", "资源管理器", "文件管理器"),
            "codex": ("codex",),
            "terminal": ("terminal", "终端", "powershell", "cmd"),
        }
        for app, aliases in app_aliases.items():
            if any(a.lower() in compact or a in text for a in aliases):
                slots.app_name = app
                break
        return MissionIntent(
            MissionTaskType.APP_CONTROL,
            0.82 if slots.app_name else 0.55,
            slots,
            missing_slots=[] if slots.app_name else ["app_name"],
            reasoning=["app control verb"],
            raw_text=text,
        )

    if _has_any(text, (r"系统状态|电脑状态|磁盘|CPU|内存|网络|电池|进程|工作现场|办公现场",)):
        return MissionIntent(
            MissionTaskType.SYSTEM_STATUS_REPORT,
            0.78,
            slots,
            reasoning=["system status signals"],
            raw_text=text,
        )

    if _has_any(text, (r"附加|附件|上传|发送文件|把.*文件.*发|把.*文件.*传",)):
        slots.file_path = project_path
        app = re.search(r"(?:到|给|进)\s*(Lark|飞书|浏览器|browser|邮件|邮箱)", text, re.I)
        if app:
            raw = app.group(1).lower()
            slots.app_name = "lark" if raw in {"lark", "飞书"} else raw
        missing = []
        if not slots.file_path:
            missing.append("file_path")
        if not slots.app_name:
            missing.append("app_name")
        return MissionIntent(
            MissionTaskType.FILE_TO_APP,
            0.76 if not missing else 0.55,
            slots,
            missing_slots=missing,
            risk_level=MissionRiskLevel.MEDIUM,
            reasoning=["file transfer to app signals"],
            raw_text=text,
        )

    return MissionIntent(MissionTaskType.UNKNOWN, 0.0, slots, raw_text=text)
