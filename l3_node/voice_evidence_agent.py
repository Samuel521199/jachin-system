"""Voice Evidence Agent for the unified Cognitive Kernel ledger.

Voice turns have extra uncertainty: STT text, normalized text, hotword/alias
corrections, interruption decisions, and replan patches.  This module emits one
stable snapshot event per important phase so the Evidence Console can replay
the whole voice ingress path without scraping many unrelated fields.
"""

from __future__ import annotations

import json
import time
from typing import Any


def record_voice_evidence_snapshot(
    *,
    turn_id: str,
    stage: str,
    companion: dict[str, Any] | None = None,
    adaptation: Any | None = None,
    envelope: Any | None = None,
    plan: Any | None = None,
    closure: Any | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Append a normalized voice snapshot to the cognitive ledger.

    Returns the payload for tests/debugging.  Non-voice turns return ``None``.
    """

    ctx = companion or {}
    if not _is_voice(ctx, adaptation=adaptation, envelope=envelope):
        return None
    payload = _build_payload(
        turn_id=turn_id,
        stage=stage,
        companion=ctx,
        adaptation=adaptation,
        envelope=envelope,
        plan=plan,
        closure=closure,
        extra=extra or {},
    )
    try:
        from l3_node.cognitive_kernel.ledger import append_event

        append_event("voice_evidence_snapshot", turn_id or "voice", payload)
    except Exception:
        pass
    return payload


def attach_voice_runtime_ui_protocol(
    text: str,
    *,
    turn_id: str,
    stage: str,
    companion: dict[str, Any] | None = None,
    adaptation: Any | None = None,
    envelope: Any | None = None,
    plan: Any | None = None,
    closure: Any | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Append a compact hidden UI protocol for the Omni chat bubble.

    The ledger remains the source of truth.  This marker is only a transport
    hint so the chat UI can show the voice runtime decision in the same place
    where the user already reads answers.
    """

    ctx = companion or {}
    if not _is_voice(ctx, adaptation=adaptation, envelope=envelope):
        return text
    payload = _build_payload(
        turn_id=turn_id,
        stage=stage,
        companion=ctx,
        adaptation=adaptation,
        envelope=envelope,
        plan=plan,
        closure=closure,
        extra=extra or {},
    )
    protocol = build_voice_runtime_ui_protocol(payload)
    try:
        from l3_node.cognitive_kernel.ledger import append_event

        append_event("voice_runtime_ui_protocol_emitted", turn_id or "voice", protocol)
    except Exception:
        pass
    body = str(text or "").rstrip()
    marker = json.dumps(protocol, ensure_ascii=False, separators=(",", ":"), default=str)
    return f"{body}\n<!-- jachin-ui:voice-runtime {marker} -->"


def build_voice_runtime_ui_protocol(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert the full voice evidence payload into a small chat UI contract."""

    guard = _dict(payload.get("false_trigger_guard"))
    normalization = _dict(payload.get("normalization"))
    planning = _dict(payload.get("planning"))
    closure = _dict(payload.get("closure"))
    extra = _dict(payload.get("extra"))
    status = str(extra.get("status") or "").strip().lower()
    if not status:
        guard_action = str(guard.get("action") or "").lower()
        if guard_action in {"drop", "confirm"}:
            status = guard_action
        elif closure.get("pending_decision"):
            status = "wait"
        elif str(closure.get("verification_status") or "").lower() in {"failed", "fail", "error"}:
            status = "failed"
        elif str(closure.get("closure_type") or "").lower() in {"completed", "done"}:
            status = "done"
        elif planning.get("work_order_count") or planning.get("task_type"):
            status = "running"
        else:
            status = "allow"
    reason_code = str(
        extra.get("reason_code")
        or guard.get("reason_code")
        or payload.get("summary")
        or payload.get("stage")
        or ""
    )
    current_task = _current_task_text(planning=planning, extra=extra)
    protocol: dict[str, Any] = {
        "type": "voice_runtime",
        "status": status,
        "mode": str(payload.get("voice_interaction_mode") or payload.get("source") or ""),
        "raw_text": _clip(payload.get("raw_text"), 240),
        "normalized_text": _clip(payload.get("normalized_text"), 240),
        "confidence": payload.get("stt_confidence"),
        "decision": str(guard.get("action") or status),
        "reason_code": _clip(reason_code, 140),
        "current_task": _clip(current_task, 180),
        "pending_task": _clip(extra.get("pending_task") or "", 180),
        "speaker": _clip(_speaker_label(payload), 80),
        "corrections": _corrections_from_normalization(normalization),
        "stages": _runtime_stages(payload),
        "evidence": {
            "turn_id": payload.get("turn_id"),
            "stage": payload.get("stage"),
            "summary": payload.get("summary"),
        },
    }
    return protocol


def _build_payload(
    *,
    turn_id: str,
    stage: str,
    companion: dict[str, Any],
    adaptation: Any | None,
    envelope: Any | None,
    plan: Any | None,
    closure: Any | None,
    extra: dict[str, Any],
) -> dict[str, Any]:
    raw_text = _first_text(
        getattr(adaptation, "raw_text", ""),
        getattr(envelope, "raw_text", ""),
        companion.get("input_adapter_raw_text"),
        companion.get("voice_raw_stt_text"),
        companion.get("voice_asr_raw_text"),
        companion.get("voice_final_text"),
        companion.get("voice_routed_text"),
    )
    normalized_text = _first_text(
        getattr(adaptation, "normalized_text", ""),
        getattr(envelope, "normalized_text", ""),
        companion.get("input_adapter_normalized_text"),
        companion.get("voice_language_normalized_text"),
        raw_text,
    )
    interruption = _dict(companion.get("voice_interruption_decision"))
    replan = _dict(companion.get("voice_task_replan_patch"))
    normalization = _dict(companion.get("voice_language_normalization"))
    false_trigger_guard = _dict(companion.get("voice_false_trigger_guard"))
    source = _source_value(adaptation, envelope, companion)
    task_info = _task_info(plan)
    closure_info = _closure_info(closure)
    payload: dict[str, Any] = {
        "type": "voice_evidence",
        "stage": str(stage or "voice"),
        "turn_id": str(turn_id or ""),
        "created_at_ms": int(time.time() * 1000),
        "source": source,
        "voice_interaction_mode": str(companion.get("voice_interaction_mode") or ""),
        "voice_stt_source": str(companion.get("voice_stt_source") or companion.get("source") or ""),
        "stt_confidence": _float_or_none(
            companion.get("voice_stt_confidence")
            or companion.get("voice_confidence")
            or getattr(adaptation, "confidence", None)
            or getattr(envelope, "confidence", None)
        ),
        "raw_text": _clip(raw_text, 500),
        "normalized_text": _clip(normalized_text, 500),
        "changed": bool(
            companion.get("input_adapter_changed")
            or companion.get("voice_language_changed")
            or getattr(adaptation, "changed", False)
        ),
        "adapter_steps": list(companion.get("input_adapter_steps") or []),
        "false_trigger_guard": false_trigger_guard,
        "normalization": normalization,
        "interruption": interruption,
        "replan": replan,
        "task_triggered": bool(task_info.get("work_order_count") or task_info.get("task_type")),
        "planning": task_info,
        "closure": closure_info,
        "extra": dict(extra),
    }
    payload["summary"] = _summary(payload)
    return payload


def _is_voice(companion: dict[str, Any], *, adaptation: Any | None, envelope: Any | None) -> bool:
    for key in ("voice_raw_stt_text", "voice_asr_raw_text", "voice_final_text", "voice_routed_text"):
        if str(companion.get(key) or "").strip():
            return True
    mode = str(companion.get("voice_interaction_mode") or "").lower()
    if mode in {"continuous_listen", "wake_conversation", "push_to_talk"}:
        return True
    for item in (adaptation, envelope):
        source = getattr(item, "source", None)
        if str(getattr(source, "value", source) or "").lower() == "voice":
            return True
    return False


def _task_info(plan: Any | None) -> dict[str, Any]:
    if plan is None:
        return {}
    contract = getattr(plan, "decision_contract", None)
    work_orders = getattr(plan, "work_orders", None) or []
    return {
        "task_type": str(getattr(contract, "task_type", "") or ""),
        "goal": _clip(str(getattr(contract, "goal", "") or ""), 300),
        "selected_workflow": str(getattr(contract, "selected_workflow", "") or ""),
        "execution_allowed": bool(getattr(contract, "execution_allowed", False)),
        "risk_level": str(getattr(getattr(contract, "risk_level", None), "value", getattr(contract, "risk_level", "")) or ""),
        "work_order_count": len(work_orders),
        "work_order_ids": [str(getattr(item, "work_order_id", "") or "") for item in work_orders[:8]],
        "role_agents": [str(getattr(item, "role_agent", "") or "") for item in work_orders[:8]],
    }


def _closure_info(closure: Any | None) -> dict[str, Any]:
    if closure is None:
        return {}
    closure_type = getattr(closure, "closure_type", "")
    return {
        "closure_type": str(getattr(closure_type, "value", closure_type) or ""),
        "verification_status": str(getattr(closure, "verification_status", "") or ""),
        "pending_decision": bool(getattr(closure, "pending_decision", None)),
    }


def _summary(payload: dict[str, Any]) -> str:
    bits: list[str] = []
    mode = str(payload.get("voice_interaction_mode") or "")
    if mode:
        bits.append(mode)
    raw = str(payload.get("raw_text") or "")
    norm = str(payload.get("normalized_text") or "")
    if raw and norm and raw != norm:
        bits.append("normalized")
    false_trigger_guard = payload.get("false_trigger_guard") if isinstance(payload.get("false_trigger_guard"), dict) else {}
    guard_action = str(false_trigger_guard.get("action") or "")
    if guard_action and guard_action != "allow":
        bits.append(f"guard:{guard_action}:{false_trigger_guard.get('reason_code') or 'unknown'}")
    interruption = payload.get("interruption") if isinstance(payload.get("interruption"), dict) else {}
    action = str(interruption.get("action") or "")
    if action and action != "none":
        bits.append(f"interrupt:{action}")
    replan = payload.get("replan") if isinstance(payload.get("replan"), dict) else {}
    if replan.get("is_replan"):
        bits.append(f"replan:{replan.get('patch_type') or 'patch'}")
    planning = payload.get("planning") if isinstance(payload.get("planning"), dict) else {}
    if planning.get("task_type"):
        bits.append(f"task:{planning.get('task_type')}")
    return " | ".join(bits) or str(payload.get("stage") or "voice")


def _source_value(adaptation: Any | None, envelope: Any | None, companion: dict[str, Any]) -> str:
    for item in (adaptation, envelope):
        source = getattr(item, "source", None)
        value = getattr(source, "value", source)
        if value:
            return str(value)
    return str(companion.get("input_adapter_source") or "voice")


def _current_task_text(*, planning: dict[str, Any], extra: dict[str, Any]) -> str:
    explicit = str(extra.get("current_task") or "").strip()
    if explicit:
        return explicit
    goal = str(planning.get("goal") or "").strip()
    task_type = str(planning.get("task_type") or "").strip()
    workflow = str(planning.get("selected_workflow") or "").strip()
    pieces = [x for x in (task_type, workflow, goal) if x]
    return " / ".join(pieces[:3])


def _speaker_label(payload: dict[str, Any]) -> str:
    guard = _dict(payload.get("false_trigger_guard"))
    evidence = _dict(guard.get("evidence"))
    speaker = _dict(evidence.get("speaker"))
    for key in ("decision", "trust", "speaker_trust", "status"):
        value = str(speaker.get(key) or "").strip()
        if value:
            return value
    normalization = _dict(payload.get("normalization"))
    ev = _dict(normalization.get("evidence"))
    return str(ev.get("speaker_trust") or "").strip()


def _corrections_from_normalization(normalization: dict[str, Any]) -> list[dict[str, str]]:
    correction = _dict(normalization.get("correction"))
    items = correction.get("corrections")
    if not isinstance(items, list):
        return []
    out: list[dict[str, str]] = []
    for item in items[:6]:
        if not isinstance(item, dict):
            continue
        original = str(item.get("original") or item.get("from") or "").strip()
        canonical = str(item.get("canonical") or item.get("to") or "").strip()
        if not original and not canonical:
            continue
        out.append(
            {
                "from": _clip(original, 40),
                "to": _clip(canonical, 40),
                "reason": _clip(item.get("reason") or "", 60),
                "source": _clip(item.get("kind") or "", 40),
            }
        )
    return out


def _runtime_stages(payload: dict[str, Any]) -> list[dict[str, str]]:
    stages: list[dict[str, str]] = []
    adapter_steps = payload.get("adapter_steps")
    if isinstance(adapter_steps, list):
        for item in adapter_steps[:5]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("stage") or "").strip()
            if not name:
                continue
            status = "error" if item.get("error") else "ok"
            detail = str(
                item.get("reason")
                or item.get("selection_reason")
                or item.get("action")
                or item.get("error")
                or ""
            )
            stages.append({"label": _stage_label(name), "status": status, "detail": _clip(detail, 100)})
    guard = _dict(payload.get("false_trigger_guard"))
    guard_action = str(guard.get("action") or "").strip()
    if guard_action:
        stages.append(
            {
                "label": "噪声/主人判断",
                "status": guard_action,
                "detail": _clip(guard.get("reason_code") or "", 100),
            }
        )
    planning = _dict(payload.get("planning"))
    if planning.get("task_type") or planning.get("work_order_count"):
        stages.append(
            {
                "label": "任务拆解",
                "status": "ok" if planning.get("execution_allowed") else "wait",
                "detail": _clip(planning.get("selected_workflow") or planning.get("task_type") or "", 100),
            }
        )
    closure = _dict(payload.get("closure"))
    if closure:
        stages.append(
            {
                "label": "结束校验",
                "status": _clip(closure.get("verification_status") or closure.get("closure_type") or "ok", 24),
                "detail": "等待用户" if closure.get("pending_decision") else "",
            }
        )
    extra = _dict(payload.get("extra"))
    if extra.get("stage_detail"):
        stages.append({"label": "当前执行", "status": "info", "detail": _clip(extra.get("stage_detail"), 100)})
    return stages[:6]


def _stage_label(name: str) -> str:
    labels = {
        "voice_language_normalizer": "语音文本修正",
        "voice_false_trigger_guard": "噪声/任务判断",
        "voice_interruption_agent": "打断判断",
        "voice_task_replan": "任务修正",
        "input_adapter": "输入适配",
    }
    return labels.get(name, name.replace("_", " "))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clip(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."
