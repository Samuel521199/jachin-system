"""Mission preview confirmation, cancellation, and patch control.

This module is deliberately state-light: it stores one pending OS mission as a
JSON record, plus a small history of user decisions.  Execution still belongs
to the router/workflow layer; this file only controls whether a prepared
mission should run, wait, be modified, or be cancelled.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from l3_node.mission_intent_schema import CapabilityRoute, MissionIntent, MissionRiskLevel, MissionSlots, MissionTaskType
from l3_node.mission_memory_center import mission_memory_path


def pending_mission_path() -> Path:
    explicit = os.environ.get("JACHIN_OS_PENDING_MISSION_PATH")
    if explicit:
        return Path(explicit).expanduser()
    return mission_memory_path().with_name("os_pending_mission.json")


def confirmation_mode() -> str:
    return os.environ.get("JACHIN_OS_MISSION_CONFIRM_MODE", "high_risk_only").strip().lower() or "high_risk_only"


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _recipient_list(raw: str) -> list[str]:
    text = str(raw or "").strip(" \t\r\n。.!！?？")
    text = re.sub(r"^(?:联系人|收件人|群聊|同事)\s*[:：]\s*", "", text)
    text = re.sub(r"(?:都)?(?:发送|发过去|发消息)?$", "", text).strip()
    parts = re.split(r"\s*(?:、|，|,|；|;|和|与|以及|and)\s*", text)
    out: list[str] = []
    seen: set[str] = set()
    for item in parts:
        name = item.strip(" \t\r\n。.!！?？")
        if not name:
            continue
        key = name.lower()
        if key not in seen:
            seen.add(key)
            out.append(name)
    return out


def is_confirmation_command(text: str) -> bool:
    s = str(text or "").strip().lower()
    if not s:
        return False
    return bool(
        re.fullmatch(
            r"(确认|确认执行|执行|开始执行|开始|继续|可以|好的|好|没问题|就这样|按这个执行|run|go|confirm|execute)",
            s,
            re.I,
        )
    )


def is_cancel_command(text: str) -> bool:
    s = str(text or "").strip().lower()
    return bool(re.fullmatch(r"(取消|取消执行|不执行|先不执行|算了|停止|放弃|cancel|stop|abort)", s, re.I))


def looks_like_patch_command(text: str) -> bool:
    s = str(text or "").strip()
    return bool(
        re.search(
            r"(改成|改为|修改|换成|不要发|别发|发给|发送给|时间范围|最近\s*\d+\s*天|按条|条列|只总结|关注|focus)",
            s,
            re.I,
        )
    )


def should_hold_for_confirmation(intent: MissionIntent, plan: Any, mode: str | None = None) -> bool:
    mode = (mode or confirmation_mode()).strip().lower()
    if mode in {"never", "off", "auto"}:
        return False
    if mode in {"always", "all"}:
        return True
    risk = intent.risk_level.value if isinstance(intent.risk_level, MissionRiskLevel) else str(intent.risk_level)
    if mode in {"high", "high_risk", "high_risk_only", "extreme", "extreme_only", "extreme_risk_only"}:
        return bool(getattr(plan, "requires_confirmation", False)) or risk == MissionRiskLevel.HIGH.value
    if bool(getattr(plan, "requires_confirmation", False)) or risk == MissionRiskLevel.HIGH.value:
        return True
    if mode in {"external", "external_effects", "send", "default"}:
        return intent.task_type in {
            MissionTaskType.PROJECT_BRIEFING_DELIVERY,
            MissionTaskType.LARK_MESSAGE_SEND,
            MissionTaskType.FILE_TO_APP,
        }
    return False


def load_pending_mission() -> dict[str, Any] | None:
    path = pending_mission_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def clear_pending_mission() -> None:
    path = pending_mission_path()
    try:
        path.unlink(missing_ok=True)
    except TypeError:
        if path.exists():
            path.unlink()


def save_pending_mission(payload: dict[str, Any]) -> dict[str, Any]:
    path = pending_mission_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    now = _now()
    data = dict(payload)
    data.setdefault("pending_id", uuid.uuid4().hex[:12])
    data.setdefault("created_at", now)
    data["updated_at"] = now
    data["status"] = "pending_confirmation"
    data["pending_path"] = str(path)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def mission_intent_from_dict(data: dict[str, Any]) -> MissionIntent:
    slots_data = data.get("slots") if isinstance(data.get("slots"), dict) else {}
    slots = MissionSlots(**{k: slots_data[k] for k in MissionSlots.__dataclass_fields__ if k in slots_data})
    task_type = MissionTaskType(data.get("task_type") or MissionTaskType.UNKNOWN.value)
    risk = MissionRiskLevel(data.get("risk_level") or MissionRiskLevel.LOW.value)
    return MissionIntent(
        task_type=task_type,
        confidence=float(data.get("confidence") or 0.0),
        slots=slots,
        missing_slots=list(data.get("missing_slots") or []),
        risk_level=risk,
        reasoning=list(data.get("reasoning") or []),
        raw_text=str(data.get("raw_text") or ""),
    )


def capability_route_from_dict(data: dict[str, Any]) -> CapabilityRoute:
    return CapabilityRoute(
        ok=bool(data.get("ok")),
        tool_id=str(data.get("tool_id") or ""),
        workflow_id=str(data.get("workflow_id") or ""),
        reason=str(data.get("reason") or ""),
        evidence_policy=str(data.get("evidence_policy") or "write_router_and_tool_evidence"),
        required_slots=list(data.get("required_slots") or []),
        missing_slots=list(data.get("missing_slots") or []),
    )


def patch_intent_from_text(intent: MissionIntent, text: str) -> tuple[MissionIntent, list[dict[str, Any]]]:
    changes: list[dict[str, Any]] = []
    s = str(text or "").strip()

    m = re.search(r"(?:不要发|别发)\s*(.+?)\s*(?:，|,|。|;|；)?\s*(?:改成|改为|换成|发给|发送给)\s*(.+)$", s)
    if not m:
        m = re.search(r"(?:改发给|改成发给|改为发给|发给|发送给|收件人改成|收件人改为)\s*(.+)$", s)
        recipients_raw = m.group(1) if m else ""
    else:
        recipients_raw = m.group(2)
    if recipients_raw:
        recipients_raw = re.split(r"(?:，|。|；|;)\s*(?:时间|范围|最近|只总结|关注|按条|条列)", recipients_raw, maxsplit=1)[0]
        recipients = _recipient_list(recipients_raw)
        if recipients:
            before = list(intent.slots.recipients)
            intent.slots.recipients = recipients
            intent.missing_slots = [slot for slot in intent.missing_slots if slot != "recipients"]
            changes.append({"slot": "recipients", "before": before, "after": recipients})

    m = re.search(r"(?:最近|时间范围(?:改成|改为)?|改成最近|改为最近)\s*([0-9]{1,2})\s*天", s)
    if m:
        before = intent.slots.since_days
        intent.slots.since_days = max(1, min(30, int(m.group(1))))
        changes.append({"slot": "since_days", "before": before, "after": intent.slots.since_days})

    if re.search(r"(?:按条|条列|一条一条|bullet|list)", s, re.I):
        before = intent.slots.output_format
        intent.slots.output_format = "bullet_points"
        if "按条" not in intent.slots.feature_query and "条列" not in intent.slots.feature_query:
            intent.slots.feature_query = (intent.slots.feature_query + "；请按条列输出").strip("；")
        changes.append({"slot": "output_format", "before": before, "after": intent.slots.output_format})

    m = re.search(r"(?:只总结|关注|聚焦|focus(?: on)?)\s*(.+)$", s, re.I)
    if m:
        topic = m.group(1).strip(" 。.!！?？")
        if topic:
            before = intent.slots.feature_query
            intent.slots.feature_query = topic
            changes.append({"slot": "feature_query", "before": before, "after": topic})

    return intent, changes
