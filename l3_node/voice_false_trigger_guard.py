"""Voice false-trigger guard for always-on listening.

Continuous voice mode should not send every STT fragment into the task planner.
This module makes a small, evidence-friendly decision before interruption,
task decomposition, or tool execution can happen.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


VoiceGuardAction = Literal["allow", "drop", "confirm"]


@dataclass(slots=True)
class VoiceFalseTriggerDecision:
    action: VoiceGuardAction
    is_voice: bool
    mode: str
    reason_code: str
    confidence: float | None = None
    should_continue_planning: bool = True
    should_close_turn: bool = False
    user_visible_reply: str = ""
    reasons: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_voice_false_trigger(
    text: str,
    *,
    voice_context: dict[str, Any] | None = None,
    run_id: str = "",
) -> VoiceFalseTriggerDecision:
    ctx = voice_context or {}
    raw = str(text or "").strip()
    compact = _compact(raw)
    is_voice = _is_voice_context(ctx)
    mode = _voice_mode(ctx)
    stt_confidence = _confidence(ctx)
    thresholds = _adaptive_thresholds()
    owner_evidence_problem = _continuous_owner_evidence_problem(ctx)
    task_session = _active_task_session(ctx)
    active_execution = _active_execution_context(ctx)
    pending_slot_reply = _pending_slot_reply_match(raw, ctx, task_session)
    reasons: list[str] = []
    action: VoiceGuardAction = "allow"
    reason_code = "accepted"
    should_continue = True
    should_close = False
    reply = ""

    if not is_voice:
        reasons.append("not_voice_turn")
    elif bool(ctx.get("voice_false_trigger_skip_once")):
        reason_code = "confirmed_pending_voice"
        reasons.append("pending_voice_confirmation_confirmed")
    elif pending_slot_reply:
        reason_code = "pending_task_slot_reply"
        reasons.append("active_task_session_slot_reply")
    elif _stt_not_finalized(ctx):
        action = "drop"
        reason_code = "stt_not_finalized"
        reasons.append("provisional_or_streaming_stt_fragment")
    elif _speaker_gate_rejected(ctx):
        action = "drop"
        reason_code = "non_owner_speaker"
        reasons.append("speaker_verification_rejected")
    elif owner_evidence_problem and _looks_like_action(compact):
        action = "confirm"
        reason_code = "speaker_owner_evidence_weak"
        reasons.append(owner_evidence_problem)
        reply = _confirm_reply(raw, reason_code)
    elif owner_evidence_problem:
        action = "drop"
        reason_code = "weak_owner_evidence_noise"
        reasons.append(owner_evidence_problem)
    elif _speaker_gate_ambiguous(ctx) and _looks_like_action(compact):
        action = "confirm"
        reason_code = "speaker_verification_ambiguous"
        reasons.append("speaker_verification_ambiguous_for_action")
        reply = _confirm_reply(raw, reason_code)
    elif _is_assistant_echo(ctx):
        action = "drop"
        reason_code = "assistant_playback_echo"
        reasons.append("assistant_or_tts_playback_active")
    elif not compact:
        action = "drop"
        reason_code = "empty_utterance"
        reasons.append("empty_text")
    elif _is_repeated_fragment(compact, ctx):
        action = "drop"
        reason_code = "duplicate_fragment"
        reasons.append("same_text_repeated_too_soon")
    elif _is_filler(compact):
        action = "drop"
        reason_code = "filler_or_backchannel"
        reasons.append("filler_phrase")
    elif active_execution.get("active") and _looks_like_background_noise(raw, compact):
        action = "drop"
        reason_code = "active_task_background_noise_ignored"
        reasons.append("active_execution_background_noise")
    elif task_session.get("active") and _looks_like_background_noise(raw, compact):
        action = "drop"
        reason_code = "pending_task_background_noise_ignored"
        reasons.append("active_task_session_background_noise")
    elif _looks_like_incomplete_action(compact):
        action = "drop"
        reason_code = "incomplete_action_fragment"
        reasons.append("action_phrase_missing_target_or_payload")
    elif _looks_like_background_noise(raw, compact):
        action = "drop"
        reason_code = "background_noise_fragment"
        reasons.append("short_non_action_fragment")
    elif _needs_low_confidence_drop(compact, mode, stt_confidence, thresholds):
        action = "drop"
        reason_code = "low_confidence_non_action"
        reasons.append("low_confidence_non_action_fragment")
    elif _needs_low_confidence_confirmation(compact, mode, stt_confidence, thresholds):
        action = "confirm"
        reason_code = "low_confidence_action"
        reasons.append("action_like_but_low_stt_confidence")
        reply = _confirm_reply(raw, reason_code)
    elif _continuous_missing_speaker_evidence(ctx) and _looks_like_action(compact):
        action = "confirm"
        reason_code = "speaker_verification_ambiguous"
        reasons.append("continuous_action_missing_speaker_evidence")
        reply = _confirm_reply(raw, reason_code)
    elif _needs_risky_confirmation(compact, mode, stt_confidence, thresholds):
        action = "confirm"
        reason_code = "risky_action_requires_voice_confirmation"
        reasons.append("risky_action_with_insufficient_voice_confidence")
        reply = _confirm_reply(raw, reason_code)
    else:
        reasons.append("voice_guard_passed")

    if action != "allow":
        should_continue = False
        should_close = True
        if not reply:
            reply = _drop_reply(reason_code)

    decision = VoiceFalseTriggerDecision(
        action=action,
        is_voice=is_voice,
        mode=mode,
        reason_code=reason_code,
        confidence=stt_confidence,
        should_continue_planning=should_continue,
        should_close_turn=should_close,
        user_visible_reply=reply,
        reasons=reasons,
        evidence={
            "run_id": run_id,
            "input_preview": raw[:180],
            "compact": compact[:180],
            "text_len": len(compact),
            "stt_confidence": stt_confidence,
            "voice_interaction_mode": mode,
            "looks_like_action": _looks_like_action(compact),
            "looks_like_risky_action": _looks_like_risky_action(compact),
            "looks_like_incomplete_action": _looks_like_incomplete_action(compact),
            "speaker": _speaker_evidence(ctx),
            "hotword_hits": _hotword_hits(raw),
            "task_session": task_session,
            "active_execution": active_execution,
            "pending_slot_reply_match": pending_slot_reply,
            "adaptive_thresholds": thresholds,
            "created_at_ms": int(time.time() * 1000),
        },
    )
    _append_guard_event(decision)
    _append_learning_event(decision)
    return decision


def _active_task_session(ctx: dict[str, Any]) -> dict[str, Any]:
    try:
        from l3_node.cognitive_kernel.task_session_manager import active_task_session_context

        return active_task_session_context(
            session_id=str(ctx.get("session_id") or ctx.get("chat_id") or ctx.get("lark_chat_id") or ""),
            channel=str(ctx.get("channel") or ctx.get("bg_channel") or ctx.get("voice_channel") or ""),
        )
    except Exception:
        return {"active": False}


def _active_execution_context(ctx: dict[str, Any]) -> dict[str, Any]:
    raw = ctx.get("voice_active_task_context")
    if not isinstance(raw, dict):
        return {"active": False}
    tasks = raw.get("active_tasks")
    if not isinstance(tasks, list):
        tasks = []
    active_tasks = [item for item in tasks if isinstance(item, dict)]
    return {
        "active": bool(active_tasks),
        "active_task_count": len(active_tasks),
        "focused_task_id": str(raw.get("focused_task_id") or ""),
        "summary": str(raw.get("summary") or ""),
        "source": str(raw.get("source") or "voice_active_task_context"),
    }


def _pending_slot_reply_match(raw: str, ctx: dict[str, Any], task_session: dict[str, Any]) -> bool:
    if not task_session.get("active"):
        return False
    try:
        from l3_node.cognitive_kernel.direct_mainline import pending_slot_reply_available

        return pending_slot_reply_available(
            user_input=raw,
            session_id=str(ctx.get("session_id") or ctx.get("chat_id") or ctx.get("lark_chat_id") or ""),
            channel=str(ctx.get("channel") or ctx.get("bg_channel") or ctx.get("voice_channel") or ""),
        )
    except Exception:
        return False


def _hotword_hits(raw: str) -> list[dict[str, Any]]:
    try:
        from l3_node.voice_entity_correction import find_hotword_hits

        return find_hotword_hits(raw)[:12]
    except Exception:
        return []


def _is_voice_context(ctx: dict[str, Any]) -> bool:
    if not ctx:
        return False
    if any(ctx.get(k) for k in ("voice_raw_stt_text", "voice_asr_raw_text", "voice_final_text", "voice_routed_text")):
        return True
    mode = _voice_mode(ctx)
    if mode in {"continuous_listen", "wake_conversation", "push_to_talk", "ptt"}:
        return True
    source = str(ctx.get("source") or ctx.get("voice_stt_source") or "").lower()
    return "voice" in source or "stt" in source


def _voice_mode(ctx: dict[str, Any]) -> str:
    return str(ctx.get("voice_interaction_mode") or ctx.get("voice_mode") or "").strip().lower()


def _confidence(ctx: dict[str, Any]) -> float | None:
    for key in ("voice_stt_confidence", "voice_confidence", "stt_confidence", "confidence"):
        value = ctx.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _compact(text: str) -> str:
    lowered = str(text or "").strip().lower()
    return re.sub(r"[\s,，。.!！?？;；:：、\"'“”‘’（）()\[\]{}<>《》]+", "", lowered)


def _is_assistant_echo(ctx: dict[str, Any]) -> bool:
    for key in (
        "voice_playback_active",
        "tts_playing",
        "assistant_speaking",
        "voice_is_playback_echo",
        "barge_in_playback_active",
    ):
        if bool(ctx.get(key)):
            return True
    return False


def _stt_not_finalized(ctx: dict[str, Any]) -> bool:
    finalized = ctx.get("voice_stt_finalized")
    provisional = ctx.get("voice_stt_provisional")
    if provisional is True:
        return True
    if finalized is False:
        return True
    return False


def _speaker_gate_rejected(ctx: dict[str, Any]) -> bool:
    if _bool_any(ctx, ("voice_speaker_rejected", "speaker_rejected", "voice_owner_rejected")):
        return True
    accepted = _first_present(ctx, ("voice_speaker_verified", "speaker_verified", "voice_owner_verified", "owner_verified", "voice_speaker_verification_accepted", "voice_owner_track_accepted", "sv_accepted", "accepted", "speakerAccepted"))
    if accepted is False:
        return True
    score = _float_first(ctx, ("voice_speaker_verification_score", "voice_owner_score", "speaker_score", "voice_speaker_score"))
    threshold = _float_first(ctx, ("voice_speaker_verification_threshold", "voice_owner_threshold", "speaker_threshold"))
    if score is not None and threshold is not None and score < threshold:
        strict = _bool_any(ctx, ("voice_speaker_verification_strict", "speaker_verification_strict"))
        if strict:
            return True
    owner_duration = _float_first(ctx, ("voice_owner_duration_ms", "owner_duration_ms"))
    total_duration = _float_first(ctx, ("voice_total_duration_ms", "total_duration_ms", "voice_stt_duration_ms"))
    if owner_duration is not None and total_duration and total_duration > 0:
        ratio = owner_duration / total_duration
        min_ratio = _float_first(ctx, ("voice_owner_min_ratio", "owner_min_ratio")) or 0.35
        if ratio < min_ratio and _bool_any(ctx, ("voice_speaker_verification_strict", "speaker_verification_strict")):
            return True
    return False


def _speaker_gate_ambiguous(ctx: dict[str, Any]) -> bool:
    accepted = _first_present(ctx, ("voice_speaker_verified", "speaker_verified", "voice_owner_verified", "owner_verified", "voice_speaker_verification_accepted", "voice_owner_track_accepted", "sv_accepted", "accepted", "speakerAccepted"))
    if accepted is True:
        return False
    if _bool_any(ctx, ("voice_speaker_profile_missing", "voice_owner_profile_missing")):
        return True
    if str(ctx.get("voice_speaker_verification_status") or "").lower() in {"ambiguous", "unknown", "unavailable", "profile_missing"}:
        return True
    score = _float_first(ctx, ("voice_speaker_verification_score", "voice_owner_score", "speaker_score", "voice_speaker_score"))
    threshold = _float_first(ctx, ("voice_speaker_verification_threshold", "voice_owner_threshold", "speaker_threshold"))
    if score is not None and threshold is not None and score < threshold:
        return True
    return False


def _continuous_missing_speaker_evidence(ctx: dict[str, Any]) -> bool:
    if _voice_mode(ctx) != "continuous_listen":
        return False
    accepted = _first_present(ctx, ("voice_speaker_verified", "speaker_verified", "voice_owner_verified", "owner_verified", "voice_speaker_verification_accepted", "voice_owner_track_accepted", "sv_accepted", "accepted", "speakerAccepted"))
    return accepted is None


def _continuous_owner_evidence_problem(ctx: dict[str, Any]) -> str:
    if _voice_mode(ctx) != "continuous_listen":
        return ""
    accepted = _first_present(ctx, ("voice_speaker_verified", "speaker_verified", "voice_owner_verified", "owner_verified", "voice_speaker_verification_accepted", "voice_owner_track_accepted", "sv_accepted", "accepted", "speakerAccepted"))
    if accepted is not True:
        return ""
    reason = str(ctx.get("voice_owner_track_reason") or ctx.get("speakerReason") or ctx.get("voice_speaker_verification_status") or "").lower()
    if "bypass" in reason or "profile_missing" in reason or "disabled" in reason:
        return f"continuous_owner_track_not_strong:{reason or 'unknown_reason'}"

    owner_duration = _float_first(ctx, ("voice_owner_duration_ms", "owner_duration_ms", "ownerDurationMs"))
    total_duration = _float_first(ctx, ("voice_total_duration_ms", "total_duration_ms", "voice_stt_duration_ms", "totalDurationMs"))
    skipped = _float_first(ctx, ("voice_owner_skipped_segments_count", "owner_skipped_segments_count", "skipped_segments_count", "skippedSegmentsCount"))
    min_duration = _float_first(ctx, ("voice_owner_min_duration_ms", "owner_min_duration_ms")) or 650.0
    min_ratio = _float_first(ctx, ("voice_owner_min_ratio", "owner_min_ratio")) or 0.55
    max_skipped = _float_first(ctx, ("voice_owner_max_skipped_segments", "owner_max_skipped_segments")) or 4.0
    strict = _bool_any(ctx, ("voice_speaker_verification_strict", "speaker_verification_strict"))

    if owner_duration is None or total_duration is None:
        return "continuous_owner_track_metrics_missing" if strict else ""
    if owner_duration < min_duration and strict:
        return f"continuous_owner_track_too_short:{owner_duration:.0f}ms<{min_duration:.0f}ms"
    if total_duration <= 0:
        return "continuous_owner_track_total_duration_invalid" if strict else ""
    ratio = owner_duration / total_duration
    if ratio < min_ratio:
        return f"continuous_owner_track_ratio_low:{ratio:.2f}<{min_ratio:.2f}"
    if skipped is not None and skipped > max_skipped:
        return f"continuous_owner_track_too_fragmented:{skipped:.0f}>{max_skipped:.0f}"
    return ""


def _speaker_evidence(ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "accepted": _first_present(ctx, ("voice_speaker_verified", "speaker_verified", "voice_owner_verified", "owner_verified", "voice_speaker_verification_accepted", "voice_owner_track_accepted", "sv_accepted", "accepted", "speakerAccepted")),
        "status": str(ctx.get("voice_speaker_verification_status") or ctx.get("voice_owner_track_reason") or ctx.get("voice_speaker_verification_reason") or ctx.get("speakerReason") or ""),
        "score": _float_first(ctx, ("voice_speaker_verification_score", "voice_owner_score", "speaker_score", "voice_speaker_score")),
        "threshold": _float_first(ctx, ("voice_speaker_verification_threshold", "voice_owner_threshold", "speaker_threshold")),
        "owner_duration_ms": _float_first(ctx, ("voice_owner_duration_ms", "owner_duration_ms", "ownerDurationMs")),
        "total_duration_ms": _float_first(ctx, ("voice_total_duration_ms", "total_duration_ms", "voice_stt_duration_ms", "totalDurationMs")),
        "owner_ratio": _owner_ratio(ctx),
        "skipped_segments_count": _float_first(ctx, ("voice_owner_skipped_segments_count", "owner_skipped_segments_count", "skipped_segments_count", "skippedSegmentsCount")),
        "continuous_evidence_problem": _continuous_owner_evidence_problem(ctx),
        "strict": _bool_any(ctx, ("voice_speaker_verification_strict", "speaker_verification_strict")),
    }


def _owner_ratio(ctx: dict[str, Any]) -> float | None:
    owner_duration = _float_first(ctx, ("voice_owner_duration_ms", "owner_duration_ms", "ownerDurationMs"))
    total_duration = _float_first(ctx, ("voice_total_duration_ms", "total_duration_ms", "voice_stt_duration_ms", "totalDurationMs"))
    if owner_duration is None or total_duration is None or total_duration <= 0:
        return None
    return owner_duration / total_duration


def _bool_any(ctx: dict[str, Any], keys: tuple[str, ...]) -> bool:
    return any(bool(ctx.get(key)) for key in keys)


def _first_present(ctx: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in ctx:
            return ctx.get(key)
    return None


def _float_first(ctx: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key not in ctx:
            continue
        try:
            return float(ctx.get(key))
        except (TypeError, ValueError):
            continue
    return None


def _is_repeated_fragment(compact: str, ctx: dict[str, Any]) -> bool:
    if not compact:
        return False
    last_text = ""
    for key in ("voice_last_text", "last_voice_text", "voice_last_final_text", "voice_previous_text"):
        value = str(ctx.get(key) or "").strip()
        if value:
            last_text = value
            break
    if not last_text:
        recent = ctx.get("voice_recent_utterances")
        if isinstance(recent, list) and recent:
            latest = recent[-1]
            if isinstance(latest, dict):
                last_text = str(latest.get("text") or latest.get("utterance") or "").strip()
            else:
                last_text = str(latest or "").strip()
    if _compact(last_text) != compact:
        return False
    now_ms = int(time.time() * 1000)
    for key in ("voice_last_text_at_ms", "last_voice_text_at_ms", "voice_previous_text_at_ms"):
        try:
            at_ms = int(float(ctx.get(key)))
        except (TypeError, ValueError):
            continue
        return 0 <= now_ms - at_ms <= 2200
    return True


def _is_filler(compact: str) -> bool:
    return compact in {
        "嗯",
        "啊",
        "哦",
        "呃",
        "额",
        "喂",
        "哈",
        "诶",
        "嗯嗯",
        "啊啊",
        "测试",
        "试一下",
        "听得到吗",
        "hello",
        "hi",
        "hey",
        "test",
    }


def _looks_like_background_noise(raw: str, compact: str) -> bool:
    if _looks_like_background_backchannel_sequence(raw):
        return True
    if len(compact) <= 1:
        return True
    if len(compact) <= 3 and not _looks_like_action(compact):
        return True
    alpha = re.sub(r"[^a-z0-9]", "", raw.lower())
    if alpha and len(alpha) <= 3 and not _looks_like_action(compact):
        return True
    return False


def _looks_like_background_backchannel_sequence(raw: str) -> bool:
    text = str(raw or "").strip().lower()
    if not text:
        return False
    clauses = [x.strip(" \t,，、。！？!?；;:：") for x in re.split(r"[,，、。！？!?；;]+", text) if x.strip(" \t,，、。！？!?；;:：")]
    if not clauses:
        return False
    if any(_looks_like_action(_compact(clause)) for clause in clauses):
        return False
    allowed = {
        "\u884c",
        "\u5bf9",
        "\u597d",
        "\u55ef",
        "\u554a",
        "\u662f",
        "\u662f\u7684",
        "\u5bf9\u7684",
        "\u53ef\u4ee5",
        "\u6ca1\u9519",
        "\u5c31\u770b\u90a3\u4e2a",
        "\u770b\u90a3\u4e2a",
        "\u90a3\u4e2a",
        "\u8fd9\u4e2a",
        "ok",
        "okay",
        "yes",
        "right",
    }
    compacted = [re.sub(r"\s+", "", clause) for clause in clauses]
    return all(clause in allowed for clause in compacted)


def _looks_like_incomplete_action(compact: str) -> bool:
    if not compact:
        return False
    exact = {
        "打开",
        "关闭",
        "发送",
        "发给",
        "计算",
        "删除",
        "移动",
        "复制",
        "搜索",
        "查找",
        "总结",
        "帮我",
        "帮我打开",
        "帮我发",
        "open",
        "close",
        "send",
        "search",
        "delete",
    }
    if compact in exact:
        return True
    return bool(re.search(r"^(打开|关闭|发送|发给|搜索|查找|总结|计算|帮我)(一下|一下这个|那个|这个)?$", compact))


def _needs_low_confidence_confirmation(
    compact: str,
    mode: str,
    confidence: float | None,
    thresholds: dict[str, Any],
) -> bool:
    if confidence is None or not _looks_like_action(compact):
        return False
    if mode in {"continuous_listen", "wake_conversation", ""}:
        return confidence < float(thresholds.get("continuous_action_confirm_threshold") or 0.55)
    if mode in {"push_to_talk", "ptt"}:
        return confidence < float(thresholds.get("ptt_action_confirm_threshold") or 0.35)
    return confidence < 0.45


def _needs_low_confidence_drop(
    compact: str,
    mode: str,
    confidence: float | None,
    thresholds: dict[str, Any],
) -> bool:
    if confidence is None or _looks_like_action(compact):
        return False
    if mode in {"continuous_listen", "wake_conversation", ""}:
        return confidence < float(thresholds.get("continuous_non_action_drop_threshold") or 0.38)
    return confidence < float(thresholds.get("ptt_non_action_drop_threshold") or 0.22)


def _needs_risky_confirmation(
    compact: str,
    mode: str,
    confidence: float | None,
    thresholds: dict[str, Any],
) -> bool:
    if _looks_like_message_send_action(compact):
        return False
    if not _looks_like_risky_action(compact):
        return False
    threshold = (
        float(thresholds.get("risky_continuous_confirm_threshold") or 0.72)
        if mode in {"continuous_listen", "wake_conversation", ""}
        else float(thresholds.get("risky_ptt_confirm_threshold") or 0.55)
    )
    return confidence is None or confidence < threshold


def _looks_like_message_send_action(compact: str) -> bool:
    return bool(re.search(r"(send|message|\u53d1\u9001|\u53d1\u7ed9|\u6d88\u606f)", compact))


def _looks_like_action(compact: str) -> bool:
    return bool(
        re.search(
            r"(打开|关闭|发给|发送|计算|删除|移动|复制|重命名|搜索|查找|读取|总结|lark|飞书|微信|浏览器|文件|计算器|open|close|send|delete|move|search|calculator|browser|wechat)",
            compact,
        )
    )


def _looks_like_risky_action(compact: str) -> bool:
    return bool(re.search(r"(删除|清空|覆盖|移动|重命名|发给|发送|关闭|delete|remove|clear|overwrite|send|close)", compact))


def _confirm_reply(raw: str, reason_code: str) -> str:
    heard = raw.strip()[:80] or "这段语音"
    if reason_code == "risky_action_requires_voice_confirmation":
        return f"我听到的是“{heard}”，这可能会执行发送、关闭或文件变更。请回复“确认执行”或“取消”。"
    if reason_code == "speaker_verification_ambiguous":
        return f"我听到的是“{heard}”，但还不能确认是主人声音。请回复“确认执行”或“取消”。"
    return f"我听到的是“{heard}”，但语音置信度不够高。请回复“确认执行”或重新说一遍。"


def _drop_reply(reason_code: str) -> str:
    if reason_code == "assistant_playback_echo":
        return "已忽略一段可能来自系统播放的回声，未执行任何操作。"
    if reason_code == "duplicate_fragment":
        return "已忽略一段重复语音片段，未重复执行。"
    if reason_code == "non_owner_speaker":
        return "已忽略一段非主人或声纹不匹配的语音，未执行任何操作。"
    if reason_code == "stt_not_finalized":
        return "已忽略一段尚未定稿的语音片段，等待完整语音。"
    if reason_code == "incomplete_action_fragment":
        return "已忽略一段像是没说完的语音片段，未执行任何操作。"
    if reason_code == "low_confidence_non_action":
        return "已忽略一段低置信度背景语音，未执行任何操作。"
    return "已忽略一段可能的噪声语音，未执行任何操作。"


def _append_guard_event(decision: VoiceFalseTriggerDecision) -> None:
    try:
        from l3_node.cognitive_kernel.ledger import append_event

        append_event("voice_false_trigger_guard_evaluated", decision.evidence.get("run_id") or "voice", decision.to_dict())
    except Exception:
        pass


def _adaptive_thresholds() -> dict[str, Any]:
    try:
        from l3_node.voice_false_trigger_learning import voice_false_trigger_threshold_overrides

        return voice_false_trigger_threshold_overrides()
    except Exception:
        return {
            "continuous_action_confirm_threshold": 0.55,
            "continuous_non_action_drop_threshold": 0.38,
            "ptt_action_confirm_threshold": 0.35,
            "ptt_non_action_drop_threshold": 0.22,
            "risky_continuous_confirm_threshold": 0.72,
            "risky_ptt_confirm_threshold": 0.55,
            "speaker_ambiguous_requires_confirmation": True,
            "adaptive": False,
        }


def _append_learning_event(decision: VoiceFalseTriggerDecision) -> None:
    try:
        from l3_node.voice_false_trigger_learning import record_voice_false_trigger_learning

        record_voice_false_trigger_learning(decision, turn_id=decision.evidence.get("run_id") or "voice")
    except Exception:
        pass
