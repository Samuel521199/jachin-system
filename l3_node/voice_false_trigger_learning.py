"""Learning layer for always-on voice false-trigger decisions.

The voice guard is intentionally conservative. This module keeps the adaptive
part outside the guard itself: every guard decision, user confirmation result,
and owner voiceprint validation signal is written as evidence, then converted
into small bounded threshold hints.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from l3_node.cognitive_kernel.ledger import append_event
from l3_node.cognitive_kernel.paths import state_dir

DEFAULT_THRESHOLDS: dict[str, float | bool] = {
    "continuous_action_confirm_threshold": 0.55,
    "continuous_non_action_drop_threshold": 0.38,
    "ptt_action_confirm_threshold": 0.35,
    "ptt_non_action_drop_threshold": 0.22,
    "risky_continuous_confirm_threshold": 0.72,
    "risky_ptt_confirm_threshold": 0.55,
    "speaker_ambiguous_requires_confirmation": True,
}


def record_voice_false_trigger_learning(
    decision: dict[str, Any] | Any,
    *,
    turn_id: str = "",
    source: str = "voice_guard",
    accepted_override: bool | None = None,
) -> dict[str, Any]:
    data = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision or {})
    evidence = dict(data.get("evidence") or {})
    entry = {
        "schema_version": 1,
        "ts_ms": _now_ms(),
        "turn_id": turn_id or evidence.get("run_id") or "voice",
        "source": source,
        "action": str(data.get("action") or ""),
        "reason_code": str(data.get("reason_code") or ""),
        "confidence": data.get("confidence"),
        "mode": str(data.get("mode") or evidence.get("voice_interaction_mode") or ""),
        "accepted_override": accepted_override,
        "speaker": evidence.get("speaker") if isinstance(evidence.get("speaker"), dict) else {},
        "input_preview": str(evidence.get("input_preview") or "")[:180],
    }
    _append_jsonl(_learning_path(), entry)
    append_event("voice_false_trigger_learning_recorded", str(entry["turn_id"]), entry)
    _append_growth_event(entry)
    return entry


def record_voice_owner_validation_result(
    *,
    result_type: str,
    accepted: bool | None = None,
    score: float | None = None,
    threshold: float | None = None,
    reason: str = "",
    source: str = "voice_owner_live_check",
    turn_id: str = "voice-owner-live",
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entry = {
        "schema_version": 1,
        "ts_ms": _now_ms(),
        "turn_id": turn_id,
        "source": source,
        "action": "owner_validation",
        "reason_code": f"owner_validation_{result_type}",
        "confidence": score,
        "mode": "live_owner_voiceprint",
        "accepted_override": accepted,
        "speaker": {
            "accepted": accepted,
            "score": score,
            "threshold": threshold,
            "status": reason or result_type,
        },
        "input_preview": "",
        "evidence": dict(evidence or {}),
    }
    _append_jsonl(_learning_path(), entry)
    append_event("voice_owner_validation_recorded", turn_id, entry)
    _append_growth_event(entry)
    return entry


def voice_false_trigger_threshold_overrides(*, limit: int = 300) -> dict[str, Any]:
    events = _read_recent_learning(limit=limit)
    thresholds: dict[str, Any] = dict(DEFAULT_THRESHOLDS)
    if not events:
        thresholds.update({"adaptive": False, "sample_count": 0, "reason_counts": {}})
        return thresholds

    reason_counts = Counter(str(e.get("reason_code") or "") for e in events)
    sample_count = len(events)
    low_action_confirmed = _count(events, "low_confidence_action", accepted=True)
    low_action_cancelled = _count(events, "low_confidence_action", accepted=False)
    non_action_noise = sum(
        reason_counts.get(reason, 0)
        for reason in (
            "low_confidence_non_action",
            "background_noise_fragment",
            "filler_or_backchannel",
            "empty_utterance",
            "duplicate_fragment",
        )
    )
    speaker_ambiguous_or_reject = sum(
        reason_counts.get(reason, 0)
        for reason in (
            "speaker_verification_ambiguous",
            "non_owner_speaker",
            "owner_validation_reject",
            "owner_validation_ambiguous",
        )
    )

    action_threshold = float(thresholds["continuous_action_confirm_threshold"])
    if low_action_confirmed >= 3 and low_action_confirmed >= max(2, low_action_cancelled * 2):
        action_threshold -= 0.04
    if low_action_cancelled >= 2 and low_action_cancelled >= low_action_confirmed:
        action_threshold += 0.05
    thresholds["continuous_action_confirm_threshold"] = _clamp(action_threshold, 0.48, 0.68)

    non_action_threshold = float(thresholds["continuous_non_action_drop_threshold"])
    noise_ratio = non_action_noise / max(1, sample_count)
    if non_action_noise >= 5 and noise_ratio >= 0.35:
        non_action_threshold += 0.04
    if non_action_noise >= 10 and noise_ratio >= 0.55:
        non_action_threshold += 0.03
    thresholds["continuous_non_action_drop_threshold"] = _clamp(non_action_threshold, 0.34, 0.45)

    ptt_threshold = float(thresholds["ptt_action_confirm_threshold"])
    if low_action_confirmed >= 4 and low_action_cancelled == 0:
        ptt_threshold -= 0.02
    if low_action_cancelled >= 3:
        ptt_threshold += 0.04
    thresholds["ptt_action_confirm_threshold"] = _clamp(ptt_threshold, 0.30, 0.42)

    thresholds["speaker_ambiguous_requires_confirmation"] = speaker_ambiguous_or_reject >= 1
    thresholds.update(
        {
            "adaptive": True,
            "sample_count": sample_count,
            "reason_counts": dict(reason_counts),
            "learning_path": str(_learning_path()),
        }
    )
    return thresholds


def latest_voice_learning_summary(*, limit: int = 300) -> dict[str, Any]:
    events = _read_recent_learning(limit=limit)
    reason_counts = Counter(str(e.get("reason_code") or "") for e in events)
    source_counts = Counter(str(e.get("source") or "") for e in events)
    owner_events = [e for e in events if str(e.get("reason_code") or "").startswith("owner_validation_")]
    return {
        "sample_count": len(events),
        "reason_counts": dict(reason_counts),
        "source_counts": dict(source_counts),
        "owner_validation_count": len(owner_events),
        "thresholds": voice_false_trigger_threshold_overrides(limit=limit),
        "learning_path": str(_learning_path()),
    }


def _learning_path() -> Path:
    return state_dir() / "voice_false_trigger_learning.jsonl"


def _read_recent_learning(*, limit: int = 300) -> list[dict[str, Any]]:
    path = _learning_path()
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-max(1, limit) :]:
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def _append_growth_event(entry: dict[str, Any]) -> None:
    try:
        from l3_node.cognitive_kernel.memory_growth import append_raw_event

        append_raw_event(
            category="evidence",
            source=str(entry.get("source") or "voice_false_trigger_learning"),
            stream="voice_false_trigger_learning",
            payload={"voice_false_trigger_learning": entry},
            source_refs=[
                {
                    "type": "cognitive_kernel_ledger",
                    "event_type": "voice_false_trigger_learning_recorded",
                    "turn_id": str(entry.get("turn_id") or "voice"),
                }
            ],
            review={
                "review_candidate": True,
                "promotion_targets": ["playbooks", "concepts"],
                "priority": "normal",
                "reason": "voice_false_trigger_threshold_learning",
            },
        )
    except Exception:
        append_event(
            "voice_false_trigger_learning_growth_append_failed",
            str(entry.get("turn_id") or "voice"),
            {"reason_code": entry.get("reason_code"), "source": entry.get("source")},
        )


def _count(events: list[dict[str, Any]], reason_code: str, *, accepted: bool) -> int:
    return sum(
        1
        for event in events
        if str(event.get("reason_code") or "") == reason_code and event.get("accepted_override") is accepted
    )


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, round(value, 4)))


def _now_ms() -> int:
    return int(time.time() * 1000)
