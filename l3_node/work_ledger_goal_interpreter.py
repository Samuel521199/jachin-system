"""Natural-language goal interpreter for Work Ledger.

The chat adapter still executes commands.  This module only decides whether a
free-form user turn is likely to be a Work Ledger goal and converts it into the
same structured command shape used by explicit shortcuts.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any


def interpret_work_ledger_goal(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    compact = _normalize(raw)
    if _looks_like_plain_chat(compact):
        return None

    path = _extract_project_path(raw)
    project_ref = _resolve_project_ref(raw) if not path else None
    if project_ref and project_ref.get("project_path"):
        path = str(project_ref.get("project_path") or "")
    days = _extract_days(compact)
    scores = {
        "start": _score_start(compact, bool(path)),
        "note": _score_note(compact),
        "collect": _score_collect(compact),
        "generate": _score_generate(compact),
        "end": _score_end(compact),
        "continue": _score_continue(compact),
        "weekly": _score_weekly(compact, days),
        "recall": _score_recall(compact),
        "lark_brief": _score_lark_brief(compact),
    }
    kind, confidence = max(scores.items(), key=lambda item: item[1])
    if confidence < 0.62:
        return None
    command: dict[str, Any] = {
        "kind": kind,
        "raw_text": raw,
        "confidence": round(confidence, 3),
        "interpreter": "work_ledger_goal_interpreter",
        "reason": _reason_for(kind, compact, confidence),
    }
    if days:
        command["days"] = days
    if kind == "start":
        title = _extract_start_title(raw, compact)
        command.update(
            {
                "title": title or time.strftime("%Y-%m-%d 工作记录"),
                "project_path": path,
            }
        )
        if project_ref:
            command["project_memory"] = project_ref
    elif kind == "note":
        command["text"] = _strip_leading_action(raw, ("记录", "补充", "记一下", "记录一下")) or raw
    elif kind == "ai_trace":
        command["text"] = raw
        command["tool_name"] = "AI"
    elif kind in {"recall", "continue"}:
        command["query"] = _extract_query(raw)
        if project_ref:
            command["project_memory"] = project_ref
    return command


def _normalize(text: str) -> str:
    value = text.lower()
    value = value.replace("，", ",").replace("。", ".").replace("：", ":")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _looks_like_plain_chat(text: str) -> bool:
    short_chat = {
        "你好",
        "hi",
        "hello",
        "在吗",
        "你在吗",
        "今天怎么样",
        "谢谢",
        "ok",
    }
    stripped = text.strip(" ,.!?。！？")
    if stripped in short_chat:
        return True
    has_work_cue = any(
        cue in text
        for cue in (
            "工作",
            "任务",
            "日报",
            "周报",
            "记录",
            "复盘",
            "总结",
            "codex",
            "cursor",
            "项目",
            "继续",
            "下班",
            "收工",
            "这周",
            "本周",
            "最近",
            "干了什么",
            "做了什么",
            "沉淀",
            "方法论",
        )
    )
    if not has_work_cue and len(text) <= 18:
        return True
    return False


def _score_start(text: str, has_path: bool) -> float:
    score = 0.0
    if any(word in text for word in ("开始", "新建", "启动", "记录今天", "开始记录")):
        score += 0.42
    if any(word in text for word in ("工作", "任务", "项目", "开发")):
        score += 0.24
    if any(word in text for word in ("今天", "今日", "这项", "这个")):
        score += 0.08
    if has_path:
        score += 0.18
    if any(word in text for word in ("日报", "周报", "总结", "下班", "收工")):
        score -= 0.18
    return min(1.0, max(0.0, score))


def _score_note(text: str) -> float:
    score = 0.0
    if any(word in text for word in ("记录一下", "记一下", "补充记录", "工作记录", "过程记录")):
        score += 0.58
    if any(word in text for word in ("决定", "结论", "卡住", "失败", "风险", "完成", "放弃", "采用")):
        score += 0.22
    return min(1.0, score)


def _score_collect(text: str) -> float:
    score = 0.0
    if any(word in text for word in ("采集证据", "收集证据", "刷新证据", "记录现场", "当前现场")):
        score += 0.78
    if any(word in text for word in ("git", "文件", "diff", "状态")):
        score += 0.12
    return min(1.0, score)


def _score_generate(text: str) -> float:
    score = 0.0
    if any(word in text for word in ("生成", "整理", "写一份", "弄一份")):
        score += 0.3
    if any(word in text for word in ("日报", "工作记录", "工作报告", "今天做了什么", "今天干了什么")):
        score += 0.42
    if any(word in text for word in ("下班", "收工", "结束")):
        score -= 0.12
    if "周报" in text:
        score -= 0.25
    return min(1.0, max(0.0, score))


def _score_end(text: str) -> float:
    score = 0.0
    if any(word in text for word in ("下班", "收工", "结束今天", "结束任务", "结束工作")):
        score += 0.5
    if any(word in text for word in ("日报", "总结", "复盘", "整理")):
        score += 0.24
    if "周报" in text:
        score -= 0.12
    return min(1.0, max(0.0, score))


def _score_continue(text: str) -> float:
    score = 0.0
    if any(word in text for word in ("继续", "接着", "续上", "明天接着", "让 codex 接着", "让 cursor 接着")):
        score += 0.48
    if any(word in text for word in ("昨天", "上次", "之前", "这个项目", "任务")):
        score += 0.22
    if any(word in text for word in ("codex", "cursor", "任务书", "提示词")):
        score += 0.18
    return min(1.0, score)


def _score_weekly(text: str, days: int | None) -> float:
    score = 0.0
    if any(word in text for word in ("周报", "本周", "这周", "最近一周", "最近几天", "最近")):
        score += 0.38
    if any(word in text for word in ("生成", "整理", "写成", "写一份", "总结")):
        score += 0.26
    if any(word in text for word in ("工作", "进展", "做了什么", "干了什么", "项目")):
        score += 0.2
    if days and days >= 7:
        score += 0.08
    return min(1.0, score)


def _score_recall(text: str) -> float:
    score = 0.0
    if any(word in text for word in ("上次", "之前", "最近", "历史", "记忆", "回忆", "查一下")):
        score += 0.3
    if any(word in text for word in ("做到哪", "做了什么", "干了什么", "进展", "经验", "方法论", "卡在哪里")):
        score += 0.36
    if any(word in text for word in ("生成", "写成", "日报", "周报")):
        score -= 0.18
    return min(1.0, max(0.0, score))


def _score_lark_brief(text: str) -> float:
    score = 0.0
    if "lark" in text or "飞书" in text:
        score += 0.24
    if any(word in text for word in ("短版", "简报", "发日报", "日报草稿", "复制")):
        score += 0.36
    if any(word in text for word in ("今天", "工作", "任务")):
        score += 0.12
    return min(1.0, score)


def _extract_days(text: str) -> int | None:
    match = re.search(r"最近\s*(\d{1,2})\s*天", text)
    if match:
        return max(1, min(60, int(match.group(1))))
    if any(word in text for word in ("三十天", "一个月", "30天")):
        return 30
    if any(word in text for word in ("十四天", "两周", "2周", "14天")):
        return 14
    if any(word in text for word in ("一周", "这周", "本周", "7天", "七天", "最近一周")):
        return 7
    if any(word in text for word in ("最近几天", "最近")):
        return 7
    return None


def _extract_project_path(raw: str) -> str:
    match = re.search(r"(?:项目路径|路径)\s*[:：]\s*([A-Za-z]:\\[^\r\n]+)", raw)
    if not match:
        match = re.search(r"([A-Za-z]:\\[^\r\n]+)", raw)
    if not match:
        return ""
    candidate = match.group(1).strip().strip('"')
    return candidate if Path(candidate).exists() else candidate


def _resolve_project_ref(raw: str) -> dict[str, Any] | None:
    try:
        from l3_node.work_ledger_project_memory import resolve_project_reference

        return resolve_project_reference(raw)
    except Exception:
        return None


def _extract_start_title(raw: str, compact: str) -> str:
    value = raw.strip()
    value = re.sub(r"(?:项目路径|路径)\s*[:：]\s*[A-Za-z]:\\[^\r\n]+", "", value).strip()
    value = _strip_leading_action(value, ("开始记录", "开始今天", "开始", "新建", "启动"))
    value = re.sub(r"^(今天|今日)?(的)?", "", value).strip()
    if not value or len(value) < 4:
        if "jachin" in compact:
            return "Jachin 工作记录"
        return time.strftime("%Y-%m-%d 工作记录")
    return value.strip(" ：:，。")


def _extract_query(raw: str) -> str:
    value = raw.strip()
    value = _strip_leading_action(
        value,
        (
            "帮我看看",
            "帮我整理",
            "看看",
            "查一下",
            "回忆一下",
            "继续",
            "接着",
            "明天让 Codex 接着",
            "明天让 Cursor 接着",
        ),
    )
    value = re.sub(r"(生成|整理|写成|写一份|日报|周报|任务书|提示词)", " ", value)
    return re.sub(r"\s+", " ", value).strip(" ：:，。") or raw.strip()


def _strip_leading_action(raw: str, actions: tuple[str, ...]) -> str:
    value = raw.strip()
    for action in actions:
        if value.startswith(action):
            value = value[len(action) :].strip()
            break
    return value.strip(" ：:，。")


def _reason_for(kind: str, text: str, confidence: float) -> str:
    cues = []
    for cue in ("开始", "记录", "日报", "周报", "下班", "收工", "继续", "Codex", "工作", "任务", "项目"):
        if cue.lower() in text:
            cues.append(cue)
    cue_text = ",".join(cues[:6]) or "work-ledger semantic cues"
    return f"{kind} confidence={confidence:.2f}; cues={cue_text}"
