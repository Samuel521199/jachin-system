"""Task understanding layer for capability routing.

The output is not a tool call.  It is a structured interpretation of the
user's goal plus the best deterministic MissionIntent available today.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from l3_node.mission_intent_schema import MissionIntent, MissionTaskType
from l3_node.semantic_slot_parser import parse_mission_intent


@dataclass
class TaskUnderstanding:
    goal: str
    intent: MissionIntent
    confidence: float
    signals: list[str] = field(default_factory=list)
    missing_slots: list[str] = field(default_factory=list)
    raw_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["intent"] = self.intent.to_dict()
        return data


def _has(text: str, pattern: str) -> bool:
    return bool(re.search(pattern, text, re.I))


def infer_task_understanding(user_input: str) -> TaskUnderstanding:
    text = str(user_input or "").strip()
    intent = parse_mission_intent(text)
    signals: list[str] = []
    goal = intent.task_type.value
    confidence = intent.confidence

    if intent.task_type != MissionTaskType.UNKNOWN:
        signals.extend(intent.reasoning or ["mission_schema_match"])
        return TaskUnderstanding(
            goal=goal,
            intent=intent,
            confidence=confidence,
            signals=signals,
            missing_slots=list(intent.missing_slots),
            raw_text=text,
        )

    if _has(text, r"总结|整理|汇报|简报|进展|新功能|做了什么|干了啥") and _has(text, r"发给|发送给|通知|告诉|发到|转给"):
        goal = "summarize_and_deliver"
        confidence = 0.68
        signals = ["summary_signal", "delivery_signal"]
    elif _has(text, r"发给|发送给|通知|告诉|发消息|发到|转给"):
        goal = "send_message"
        confidence = 0.64
        signals = ["delivery_signal"]
    elif _has(text, r"打开|启动|切换|聚焦|窗口"):
        goal = "app_control"
        confidence = 0.62
        signals = ["app_control_signal"]
    elif _has(text, r"文件|目录|桌面|下载|文档|复制|移动|重命名|删除|上传|附件"):
        goal = "file_operation"
        confidence = 0.6
        signals = ["file_signal"]
    elif _has(text, r"系统|电脑|磁盘|网络|电池|进程|CPU|内存|状态"):
        goal = "system_status"
        confidence = 0.62
        signals = ["system_status_signal"]
    elif _has(text, r"PPT|PowerPoint|幻灯片|演示文稿|slides?"):
        goal = "presentation_create"
        confidence = 0.6
        signals = ["presentation_signal"]
    elif _has(text, r"股票|A股|行情|走势|财报"):
        goal = "finance_analysis"
        confidence = 0.6
        signals = ["finance_signal"]
    else:
        goal = "unknown"
        confidence = 0.0
        signals = []

    return TaskUnderstanding(
        goal=goal,
        intent=intent,
        confidence=confidence,
        signals=signals,
        missing_slots=list(intent.missing_slots),
        raw_text=text,
    )
