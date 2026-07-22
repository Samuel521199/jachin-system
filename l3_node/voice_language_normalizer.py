"""Voice language normalization before Cognitive Kernel planning.

This module is the boundary between noisy STT text and the deterministic
mission router. It keeps the policy narrow: normalize spoken entities, preserve
message bodies, and convert short spoken confirmations only when there is an
active pending DecisionContract.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from l3_node.voice_entity_correction import VoiceCorrectionResult, correct_voice_entities, find_hotword_hits, teach_alias


@dataclass(slots=True)
class VoiceLanguageNormalization:
    raw_text: str
    input_text: str
    normalized_text: str
    is_voice: bool
    correction: VoiceCorrectionResult
    pending_confirmation_detected: bool = False
    pending_cancellation_detected: bool = False
    asr_candidates: list[dict[str, Any]] = field(default_factory=list)
    selected_candidate: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def changed(self) -> bool:
        return self.normalized_text != self.input_text or self.correction.changed

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_text": self.raw_text,
            "input_text": self.input_text,
            "normalized_text": self.normalized_text,
            "is_voice": self.is_voice,
            "pending_confirmation_detected": self.pending_confirmation_detected,
            "pending_cancellation_detected": self.pending_cancellation_detected,
            "correction": {
                "raw_text": self.correction.raw_text,
                "cleaned_text": self.correction.cleaned_text,
                "corrected_text": self.correction.corrected_text,
                "corrections": [asdict(item) for item in self.correction.corrections],
                "suspect_tokens": [asdict(item) for item in self.correction.suspect_tokens],
            },
            "asr_candidates": list(self.asr_candidates),
            "selected_candidate": dict(self.selected_candidate),
            "evidence": dict(self.evidence),
        }


def normalize_voice_language_input(
    user_input: str,
    *,
    session_id: str = "",
    channel: str = "",
    voice_context: dict[str, Any] | None = None,
) -> VoiceLanguageNormalization:
    """Return the text that should enter the Cognitive Kernel.

    For text turns this still runs entity normalization, but confirmation
    rewrites are enabled only for voice turns with an active pending task.
    """

    ctx = voice_context or {}
    is_voice = _is_voice_turn(ctx)
    raw_text = _first_non_empty(
        ctx.get("voice_raw_stt_text"),
        ctx.get("voice_asr_raw_text"),
        ctx.get("voice_final_text"),
        ctx.get("voice_routed_text"),
        user_input,
    )
    input_text = str(user_input or raw_text or "").strip()
    asr_candidates = _collect_asr_candidates(ctx, user_input=input_text)
    selected = _select_voice_candidate(asr_candidates, voice_context=ctx)
    if selected:
        input_text = str(selected.get("text") or input_text).strip()
    correction = correct_voice_entities(input_text)
    normalized = correction.corrected_text.strip()
    action_segment = _extract_actionable_voice_segment(normalized) if is_voice else {}
    if action_segment.get("normalized_text"):
        normalized = str(action_segment.get("normalized_text") or normalized).strip()
    pending = _has_pending_confirmation(session_id=session_id, channel=channel)
    pending_confirm = False
    pending_cancel = False
    if is_voice and pending:
        if _looks_like_spoken_confirmation(normalized):
            normalized = "确认执行"
            pending_confirm = True
        elif _looks_like_spoken_cancellation(normalized):
            normalized = "取消"
            pending_cancel = True
    result = VoiceLanguageNormalization(
        raw_text=str(raw_text or ""),
        input_text=input_text,
        normalized_text=normalized,
        is_voice=is_voice,
        correction=correction,
        pending_confirmation_detected=pending_confirm,
        pending_cancellation_detected=pending_cancel,
        asr_candidates=asr_candidates,
        selected_candidate=selected,
        evidence={
            "session_id": session_id,
            "channel": channel,
            "pending_confirmation_present": pending,
            "voice_source": ctx.get("voice_stt_source") or ctx.get("source") or "",
            "voice_confidence": ctx.get("voice_stt_confidence") or ctx.get("voice_confidence"),
            "asr_candidate_count": len(asr_candidates),
            "asr_candidate_scores": [
                {
                    "text": str(item.get("text") or "")[:120],
                    "source": item.get("source"),
                    "confidence": item.get("confidence"),
                    "score": item.get("score"),
                    "intent": item.get("intent"),
                    "hotword_hits": item.get("hotword_hits"),
                    "hotword_gate": item.get("hotword_gate"),
                    "hotword_gate_reason": item.get("hotword_gate_reason"),
                    "hotword_used_for_selection": item.get("hotword_used_for_selection"),
                    "selection_reason": item.get("selection_reason"),
                    "correction_count": item.get("correction_count"),
                    "suspect_count": item.get("suspect_count"),
                    "suspect_reasons": item.get("suspect_reasons"),
                }
                for item in asr_candidates
            ],
            "selected_candidate_source": selected.get("source") if selected else "",
            "selected_candidate_score": selected.get("score") if selected else None,
            "correction_count": len(correction.corrections),
            "correction_reasons": [item.reason for item in correction.corrections],
            "used_user_memory_alias": any(_reason_is_user_memory(item.reason) for item in correction.corrections),
            "speaker_trust": _speaker_trust_label(ctx),
            "raw_to_normalized": {
                "raw": str(raw_text or ""),
                "selected_input": input_text,
                "normalized": normalized,
            },
            "action_segment": action_segment,
        },
    )
    _maybe_learn_voice_corrections(result, ctx)
    _append_normalization_event(result)
    return result


def extract_actionable_voice_segment(text: str) -> dict[str, Any]:
    """Public helper used by tests and voice ingress diagnostics."""

    return _extract_actionable_voice_segment(text)


def _extract_actionable_voice_segment(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    intent = _action_intent(raw)
    if not intent:
        return {}

    hard_sentences = _split_hard_voice_sentences(raw)
    if not hard_sentences:
        return {}

    selected: list[str] = []
    dropped: list[str] = []
    start_idx = -1
    for idx, sentence in enumerate(hard_sentences):
        if _action_intent(sentence):
            start_idx = idx
            selected.append(sentence)
            break
        dropped.append(sentence)
    if start_idx < 0:
        return {}

    if intent == "message_send":
        selected = _trim_message_send_sentence(selected[0])
        consumed_until = start_idx
        if _message_segment_missing_payload(" ".join(selected)):
            for idx in range(start_idx + 1, len(hard_sentences)):
                sentence = hard_sentences[idx]
                if _is_background_voice_sentence(sentence):
                    dropped.extend(hard_sentences[idx:])
                    consumed_until = idx
                    break
                selected.append(sentence)
                consumed_until = idx
                break
        else:
            consumed_until = start_idx
        if consumed_until + 1 < len(hard_sentences):
            dropped.extend(hard_sentences[consumed_until + 1 :])
    else:
        selected = _trim_non_message_action_sentence(selected[0])
        if start_idx + 1 < len(hard_sentences):
            dropped.extend(hard_sentences[start_idx + 1 :])

    normalized = _join_voice_segments(selected)
    if not normalized:
        return {}
    changed = normalized != raw
    if not changed and not dropped:
        return {}
    return {
        "extracted": True,
        "intent": intent,
        "source_text": raw,
        "normalized_text": normalized,
        "selected_segments": selected,
        "dropped_background_segments": dropped,
        "reason": "action_segment_wins_over_background_voice",
    }


def _split_hard_voice_sentences(text: str) -> list[str]:
    parts = re.split(r"[\u3002\uff01\uff1f!?;\uff1b\r\n]+", str(text or ""))
    out: list[str] = []
    for part in parts:
        value = part.strip(" \t,，、。！？!?；;")
        if value:
            out.append(value)
    return out


def _split_soft_voice_clauses(text: str) -> list[str]:
    return [x.strip(" \t,，、。！？!?；;") for x in re.split(r"[,，、]+", str(text or "")) if x.strip(" \t,，、。！？!?；;")]


def _join_voice_segments(segments: list[str]) -> str:
    cleaned = [str(x or "").strip(" \t,，、。！？!?；;") for x in segments if str(x or "").strip(" \t,，、。！？!?；;")]
    if not cleaned:
        return ""
    return "\uff0c".join(cleaned)


def _action_intent(text: str) -> str:
    low = str(text or "").lower()
    compact = re.sub(r"\s+", "", low)
    if any(term in compact for term in ("\u53d1\u9001\u6d88\u606f", "\u53d1\u6d88\u606f", "\u53d1\u7ed9", "\u53d1\u9001\u7ed9", "\u901a\u77e5", "\u544a\u8bc9")):
        return "message_send"
    if any(term in low for term in ("send message", "message ", "send to", "notify ", "tell ")):
        return "message_send"
    if any(term in compact for term in ("\u6253\u5f00", "\u542f\u52a8", "\u8fd0\u884c")) or re.search(r"\b(open|launch|start)\b", low):
        return "open_app"
    if any(term in compact for term in ("\u5173\u95ed", "\u5173\u6389", "\u9000\u51fa")) or re.search(r"\b(close|quit|exit)\b", low):
        return "close_app"
    if any(term in compact for term in ("\u641c\u7d22", "\u627e\u627e", "\u67e5\u4e00\u4e0b", "\u4e0a\u7f51", "\u6700\u65b0")) or re.search(r"\b(search|latest|news)\b", low):
        return "web_research"
    if any(term in compact for term in ("\u8ba1\u7b97", "\u7b97\u4e00\u4e0b", "\u591a\u5c11")) or re.search(r"\b(calc|calculate)\b", low):
        return "calculator"
    if any(term in compact for term in ("\u6587\u4ef6", "\u76ee\u5f55", "\u8bfb\u53d6", "\u6240\u5728\u4f4d\u7f6e")):
        return "file_operation"
    return ""


def _trim_message_send_sentence(sentence: str) -> list[str]:
    clauses = _split_soft_voice_clauses(sentence)
    if not clauses:
        return []
    selected: list[str] = []
    seen_action = False
    for clause in clauses:
        if not seen_action:
            if _action_intent(clause) == "message_send":
                selected.append(clause)
                seen_action = True
            continue
        if _is_background_voice_sentence(clause):
            break
        selected.append(clause)
        if _message_segment_has_payload(_join_voice_segments(selected)):
            break
    return selected or [sentence]


def _trim_non_message_action_sentence(sentence: str) -> list[str]:
    clauses = _split_soft_voice_clauses(sentence)
    if not clauses:
        return []
    selected: list[str] = []
    for clause in clauses:
        if _is_background_voice_sentence(clause) and selected:
            break
        selected.append(clause)
        if _action_intent(clause) and len(selected) >= 1:
            # App/file/calculator commands are usually self-contained in the
            # first actionable clause. Keeping more clauses often preserves
            # nearby conversation noise.
            break
    return selected or [sentence]


def _message_segment_missing_payload(text: str) -> bool:
    return not _message_segment_has_payload(text)


def _message_segment_has_payload(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    cleaned = re.sub(r"^(?:\u8bf7|\u5e2e\u6211|\u9ebb\u70e6\u4f60)?\s*(?:\u6253\u5f00\s*(?:Lark|\u98de\u4e66)\s*)?", "", raw, flags=re.I)
    cleaned = re.sub(r"^(?:\u53d1\u9001\u6d88\u606f|\u53d1\u6d88\u606f|\u53d1\u9001|\u53d1|\u901a\u77e5|\u544a\u8bc9|send message|message)\s*", "", cleaned, flags=re.I)
    cleaned = cleaned.strip(" \t,，、。！？!?；;:")
    if not cleaned:
        return False
    if _is_background_voice_sentence(cleaned):
        return False
    return True


_BACKGROUND_VOICE_PHRASES = {
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
    "\u8fd9\u4e2a\u90a3\u4e2a",
    "\u4f60\u770b\u90a3\u4e2a",
    "ok",
    "okay",
    "yes",
    "right",
}


def _is_background_voice_sentence(text: str) -> bool:
    compact = re.sub(r"[\s,，、。！？!?；;:：]+", "", str(text or "").lower())
    if not compact:
        return True
    if compact in _BACKGROUND_VOICE_PHRASES:
        return True
    return bool(
        re.fullmatch(
            r"(\u884c|\u5bf9|\u597d|\u55ef|\u554a|\u662f|\u53ef\u4ee5){1,4}(\u5c31)?(\u770b)?(\u90a3\u4e2a|\u8fd9\u4e2a)?",
            compact,
        )
    )


def _is_voice_turn(ctx: dict[str, Any]) -> bool:
    if not ctx:
        return False
    if any(ctx.get(k) for k in ("voice_raw_stt_text", "voice_asr_raw_text", "voice_final_text", "voice_routed_text")):
        return True
    source = str(ctx.get("source") or ctx.get("voice_stt_source") or "").lower()
    return "voice" in source or "stt" in source


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _collect_asr_candidates(ctx: dict[str, Any], *, user_input: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    def add(text: Any, *, source: str, confidence: Any = None) -> None:
        value = str(text or "").strip()
        if not value:
            return
        key = re.sub(r"\s+", " ", value).strip().lower()
        if key in seen:
            return
        seen.add(key)
        try:
            conf = float(confidence) if confidence is not None else None
        except (TypeError, ValueError):
            conf = None
        out.append({"text": value, "source": source, "confidence": conf})

    for key in ("voice_asr_alternatives", "voice_stt_alternatives", "voice_stt_candidates", "voice_nbest"):
        raw = ctx.get(key)
        if isinstance(raw, list):
            for idx, item in enumerate(raw[:8]):
                if isinstance(item, dict):
                    add(
                        item.get("text") or item.get("transcript") or item.get("value") or item.get("sentence"),
                        source=f"{key}.{idx}",
                        confidence=item.get("confidence") or item.get("score"),
                    )
                else:
                    add(item, source=f"{key}.{idx}")

    understanding = ctx.get("voice_stt_understanding")
    if isinstance(understanding, dict):
        for key in ("asr_alternatives", "alternatives", "candidates", "nbest"):
            raw = understanding.get(key)
            if isinstance(raw, list):
                for idx, item in enumerate(raw[:8]):
                    if isinstance(item, dict):
                        add(
                            item.get("text") or item.get("transcript") or item.get("value") or item.get("sentence"),
                            source=f"understanding.{key}.{idx}",
                            confidence=item.get("confidence") or item.get("score"),
                        )
                    else:
                        add(item, source=f"understanding.{key}.{idx}")

    add(ctx.get("voice_raw_stt_text"), source="voice_raw_stt_text", confidence=ctx.get("voice_stt_confidence") or ctx.get("voice_confidence"))
    add(ctx.get("voice_asr_raw_text"), source="voice_asr_raw_text", confidence=ctx.get("voice_stt_confidence") or ctx.get("voice_confidence"))
    add(ctx.get("voice_corrected_text"), source="voice_corrected_text", confidence=ctx.get("voice_stt_confidence") or ctx.get("voice_confidence"))
    add(ctx.get("voice_final_text"), source="voice_final_text", confidence=ctx.get("voice_stt_confidence") or ctx.get("voice_confidence"))
    add(user_input, source="user_input", confidence=ctx.get("voice_stt_confidence") or ctx.get("voice_confidence"))
    return out


def _select_voice_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    if not candidates:
        return {}
    best: dict[str, Any] = {}
    best_score = -1.0
    for candidate in candidates:
        text = str(candidate.get("text") or "").strip()
        if not text:
            continue
        correction = correct_voice_entities(text)
        score = _candidate_score(text, candidate.get("confidence"), correction)
        enriched = {
            **candidate,
            "score": round(score, 3),
            "corrected_text": correction.corrected_text,
            "correction_count": len(correction.corrections),
            "suspect_count": len(correction.suspect_tokens),
        }
        candidate.update(enriched)
        if score > best_score:
            best_score = score
            best = enriched
    return best


def _candidate_score(text: str, confidence: Any, correction: VoiceCorrectionResult) -> float:
    try:
        score = float(confidence) if confidence is not None else 0.62
    except (TypeError, ValueError):
        score = 0.62
    low = str(correction.corrected_text or text or "").lower()
    if correction.corrections:
        score += min(0.24, 0.09 * len(correction.corrections))
    if any(item.kind == "contact" for item in correction.corrections):
        score += 0.12
    if _has_known_contact_surface(correction.corrected_text or text):
        score += 0.08
    if any(term in low for term in ("打开", "启动", "关闭", "发送", "发给", "给")):
        score += 0.08
    if any(term in low for term in ("打开", "启动", "关闭", "open ", "launch", "close", "send", "message", "发送", "发给")):
        score += 0.08
    if correction.suspect_tokens:
        score -= min(0.2, 0.06 * len(correction.suspect_tokens))
    if len(str(text or "").strip()) <= 1:
        score -= 0.2
    return max(0.0, score)


def _has_known_contact_surface(text: str) -> bool:
    low = str(text or "").lower()
    return any(name in low for name in ("neil", "vivian", "samuel", "ethan"))


def _select_voice_candidate(candidates: list[dict[str, Any]], *, voice_context: dict[str, Any] | None = None) -> dict[str, Any]:
    if not candidates:
        return {}
    ctx = voice_context or {}
    enriched_candidates: list[dict[str, Any]] = []
    for idx, candidate in enumerate(candidates):
        text = str(candidate.get("text") or "").strip()
        if not text:
            continue
        correction = correct_voice_entities(text)
        intent = _action_intent(correction.corrected_text or text)
        hotword_hits = find_hotword_hits(text, limit=8)
        hotword_allowed, hotword_reason = _hotword_gate(
            text=text,
            confidence=candidate.get("confidence"),
            correction=correction,
            intent=intent,
            hotword_hits=hotword_hits,
            ctx=ctx,
        )
        score = _candidate_score(
            text,
            candidate.get("confidence"),
            correction,
            ctx=ctx,
            hotword_allowed=hotword_allowed,
            hotword_hits=hotword_hits,
        )
        enriched = {
            **candidate,
            "rank": idx,
            "score": round(score, 3),
            "intent": intent,
            "hotword_hits": hotword_hits,
            "hotword_gate": "allow" if hotword_allowed else "block",
            "hotword_gate_reason": hotword_reason,
            "hotword_used_for_selection": False,
            "corrected_text": correction.corrected_text,
            "correction_count": len(correction.corrections),
            "suspect_count": len(correction.suspect_tokens),
            "suspect_reasons": [item.reason for item in correction.suspect_tokens],
            "speaker_trust": _speaker_trust_label(ctx),
        }
        candidate.update(enriched)
        enriched_candidates.append(enriched)
    if not enriched_candidates:
        return {}

    first = enriched_candidates[0]
    best = max(enriched_candidates, key=lambda item: float(item.get("score") or 0.0))
    if first is best:
        first["selection_reason"] = "primary_asr_best_score"
        return first

    if _first_asr_candidate_should_be_preserved(first, best):
        first["selection_reason"] = "preserve_high_confidence_primary_asr"
        return first

    if _candidate_switch_allowed(first, best):
        best["hotword_used_for_selection"] = bool(best.get("hotword_hits")) and _candidate_hotword_can_select(best)
        best["selection_delta"] = round(float(best.get("score") or 0.0) - float(first.get("score") or 0.0), 3)
        best["selection_reason"] = "alternative_candidate_has_clearer_action_slot"
        return best

    first["selection_reason"] = "primary_asr_preserved_insufficient_switch_margin"
    return first


def _candidate_score(
    text: str,
    confidence: Any,
    correction: VoiceCorrectionResult,
    *,
    ctx: dict[str, Any] | None = None,
    hotword_allowed: bool = True,
    hotword_hits: list[dict[str, Any]] | None = None,
) -> float:
    context = ctx or {}
    score = _candidate_confidence(confidence)
    low = str(correction.corrected_text or text or "").lower()
    intent = _action_intent(correction.corrected_text or text)
    hotword_hits = list(hotword_hits if hotword_hits is not None else find_hotword_hits(text, limit=8))
    if intent:
        score += 0.14
    if correction.corrections:
        score += min(0.28, 0.1 * len(correction.corrections))
    if any(item.kind == "contact" for item in correction.corrections):
        score += 0.12
    if any(item.kind == "app" for item in correction.corrections):
        score += 0.1
    if hotword_hits and hotword_allowed:
        score += min(0.2, sum(float(item.get("weight") or 0) for item in hotword_hits) / 600.0)
    if _has_known_contact_surface(correction.corrected_text or text):
        score += 0.08
    if any(
        term in low
        for term in (
            "\u6253\u5f00",
            "\u542f\u52a8",
            "\u5173\u95ed",
            "\u53d1\u9001",
            "\u53d1\u7ed9",
            "\u7ed9",
            "open ",
            "launch",
            "close",
            "send",
            "message",
        )
    ):
        score += 0.08
    speaker_trust = _speaker_trust_label(context)
    if speaker_trust == "owner":
        score += 0.06
    elif speaker_trust == "rejected":
        score -= 0.55
    elif speaker_trust == "ambiguous" and intent:
        score -= 0.04
    if correction.suspect_tokens:
        score -= min(0.2, 0.06 * len(correction.suspect_tokens))
    if len(str(text or "").strip()) <= 1:
        score -= 0.2
    return max(0.0, score)


def _hotword_gate(
    *,
    text: str,
    confidence: Any,
    correction: VoiceCorrectionResult,
    intent: str,
    hotword_hits: list[dict[str, Any]],
    ctx: dict[str, Any],
) -> tuple[bool, str]:
    """Decide whether domain hotwords may affect candidate selection.

    Native ASR hotwords can help with words like Lark, WeChat, Codex and Neil,
    but they must not bend an already clear non-command sentence into a task.
    This gate only lets hotwords influence ranking when the utterance is
    action-like, low-confidence, pending-slot related, or already corrected.
    """

    if not hotword_hits:
        return False, "no_hotword_hit"
    if _speaker_trust_label(ctx) == "rejected":
        return False, "speaker_rejected"
    if intent:
        return True, "action_intent"
    if correction.corrections:
        return True, "entity_correction"
    if correction.suspect_tokens:
        return True, "suspect_entity"
    if _context_has_pending_task(ctx):
        return True, "pending_task_context"
    if _candidate_confidence(confidence) < 0.78:
        return True, "low_asr_confidence"
    if _looks_like_task_surface(text):
        return True, "task_surface"
    return False, "high_confidence_non_task_asr"


def _context_has_pending_task(ctx: dict[str, Any]) -> bool:
    for key in (
        "pending_confirmation_present",
        "pending_task_present",
        "voice_pending_task",
        "voice_pending_slot",
        "task_session_pending",
        "conversation_pending",
    ):
        value = ctx.get(key)
        if value is True:
            return True
        if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "pending"}:
            return True
    return False


def _looks_like_task_surface(text: str) -> bool:
    low = str(text or "").lower()
    return any(
        term in low
        for term in (
            "\u6253\u5f00",
            "\u542f\u52a8",
            "\u5173\u95ed",
            "\u53d1\u9001",
            "\u53d1\u7ed9",
            "\u7ed9",
            "open ",
            "launch",
            "close",
            "send",
            "message",
        )
    )


def _candidate_confidence(confidence: Any) -> float:
    try:
        return float(confidence) if confidence is not None else 0.62
    except (TypeError, ValueError):
        return 0.62


def _first_asr_candidate_should_be_preserved(first: dict[str, Any], best: dict[str, Any]) -> bool:
    confidence = _candidate_confidence(first.get("confidence"))
    if confidence < 0.86:
        return False
    if _candidate_has_ambiguous_generic_slot(first):
        return False
    if _candidate_missing_required_slot(first):
        return False
    if _candidate_has_direct_entity_correction(first):
        return False
    best_delta = float(best.get("score") or 0.0) - float(first.get("score") or 0.0)
    if best_delta >= 0.32 and _candidate_has_direct_entity_correction(best):
        return False
    return True


def _candidate_switch_allowed(first: dict[str, Any], best: dict[str, Any]) -> bool:
    best_delta = float(best.get("score") or 0.0) - float(first.get("score") or 0.0)
    if best_delta < 0.16:
        return False
    if _candidate_missing_required_slot(best):
        return False
    if _candidate_missing_required_slot(first):
        return True
    if _candidate_has_ambiguous_generic_slot(first) and ((_candidate_has_contact_hotword(best) and _candidate_hotword_can_select(best)) or _candidate_has_direct_entity_correction(best)):
        return True
    if int(first.get("suspect_count") or 0) > 0 and _candidate_has_direct_entity_correction(best):
        return True
    if _candidate_confidence(first.get("confidence")) < 0.72 and (_candidate_has_direct_entity_correction(best) or _candidate_hotword_can_select(best)):
        return True
    return best_delta >= 0.28 and _candidate_has_direct_entity_correction(best)


def _candidate_missing_required_slot(candidate: dict[str, Any]) -> bool:
    text = str(candidate.get("corrected_text") or candidate.get("text") or "").strip()
    intent = str(candidate.get("intent") or _action_intent(text))
    compact = re.sub(r"\s+", "", text.lower())
    if intent == "open_app":
        return bool(re.fullmatch(r"(open|launch|start|\u6253\u5f00|\u542f\u52a8|\u8fd0\u884c)", compact))
    if intent == "message_send":
        return not _message_segment_has_payload(text)
    return False


def _candidate_has_direct_entity_correction(candidate: dict[str, Any]) -> bool:
    return int(candidate.get("correction_count") or 0) > 0


def _candidate_has_ambiguous_generic_slot(candidate: dict[str, Any]) -> bool:
    return "ambiguous_generic_contact_recipient" in {str(x) for x in candidate.get("suspect_reasons") or []}


def _candidate_has_contact_hotword(candidate: dict[str, Any]) -> bool:
    return any(str(item.get("kind") or "") == "contact" for item in candidate.get("hotword_hits") or [] if isinstance(item, dict))


def _candidate_hotword_can_select(candidate: dict[str, Any]) -> bool:
    return bool(candidate.get("hotword_hits")) and str(candidate.get("hotword_gate") or "") == "allow"


def _has_known_contact_surface(text: str) -> bool:
    low = str(text or "").lower()
    return any(name in low for name in ("neil", "vivian", "samuel", "ethan", "\u6d4b\u8bd5\u5907\u6ce8\u5192\u70df\u8349\u7a3f"))


def _speaker_trust_label(ctx: dict[str, Any]) -> str:
    accepted = _first_present(
        ctx,
        (
            "voice_speaker_verified",
            "speaker_verified",
            "voice_owner_verified",
            "owner_verified",
            "voice_speaker_verification_accepted",
            "voice_owner_track_accepted",
            "sv_accepted",
            "accepted",
            "speakerAccepted",
        ),
    )
    rejected = _first_present(ctx, ("voice_speaker_rejected", "speaker_rejected", "voice_owner_rejected"))
    if rejected is True or str(rejected).strip().lower() in {"true", "1", "yes", "rejected"}:
        return "rejected"
    if accepted is True or str(accepted).strip().lower() in {"true", "1", "yes", "accepted"}:
        return "owner"
    status = str(ctx.get("voice_speaker_verification_status") or ctx.get("voice_owner_track_reason") or "").strip().lower()
    if status in {"ambiguous", "unknown", "unavailable", "profile_missing"}:
        return "ambiguous"
    return "unknown"


def _first_present(ctx: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in ctx:
            return ctx.get(key)
    return None


def _maybe_learn_voice_corrections(result: VoiceLanguageNormalization, ctx: dict[str, Any]) -> None:
    if not result.is_voice or not result.correction.corrections:
        return
    trust = _speaker_trust_label(ctx)
    try:
        confidence = float(ctx.get("voice_stt_confidence") or ctx.get("voice_confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if trust != "owner" and confidence < 0.88:
        return
    learned: list[dict[str, Any]] = []
    for item in result.correction.corrections:
        if not item.original or not item.canonical or item.original.lower() == item.canonical.lower():
            continue
        if item.confidence < 0.9:
            continue
        try:
            teach_alias(item.kind, item.canonical, item.original, source="voice_owner_high_confidence")
            learned.append(
                {
                    "kind": item.kind,
                    "original": item.original,
                    "canonical": item.canonical,
                    "confidence": item.confidence,
                }
            )
        except Exception:
            continue
    if learned:
        result.evidence["learned_aliases"] = learned


def _reason_is_user_memory(reason: str) -> bool:
    low = str(reason or "").lower()
    return "user_memory" in low or "voice_user_aliases" in low or ".jachin" in low or "user_alias" in low or "memory" in low


def _has_pending_confirmation(*, session_id: str = "", channel: str = "") -> bool:
    try:
        from l3_node.cognitive_kernel.pending_confirmation import load_pending_confirmation

        return load_pending_confirmation(session_id=session_id, channel=channel) is not None
    except Exception:
        return False


def _compact_spoken_reply(text: str) -> str:
    t = str(text or "").strip().lower()
    t = re.sub(r"[\s,，。.!！?？;；:：、…]+", "", t)
    return t


def _looks_like_spoken_confirmation(text: str) -> bool:
    compact = _compact_spoken_reply(text)
    if compact in {
        "是",
        "是的",
        "对",
        "对的",
        "没错",
        "确认",
        "确认执行",
        "可以",
        "可以执行",
        "继续",
        "继续执行",
        "就这个",
        "就是这个",
        "对就是这个",
        "是这个",
        "打开吧",
        "发吧",
        "发送吧",
        "执行吧",
        "继续吧",
        "goahead",
        "yes",
        "ok",
        "okay",
    }:
        return True
    return bool(re.fullmatch(r"(对|是|确认|可以|没错).{0,6}(这个|执行|打开|发送|发吧|继续)?", compact))


def _looks_like_spoken_cancellation(text: str) -> bool:
    compact = _compact_spoken_reply(text)
    if compact in {
        "不",
        "不是",
        "不对",
        "否",
        "取消",
        "别执行",
        "不要执行",
        "不用了",
        "算了",
        "停下",
        "停止",
        "no",
        "cancel",
        "stop",
    }:
        return True
    return bool(re.fullmatch(r"(不|不是|不对|取消|算了).{0,8}", compact))


def _append_normalization_event(result: VoiceLanguageNormalization) -> None:
    try:
        from l3_node.cognitive_kernel.ledger import append_event

        append_event(
            "voice_language_normalized",
            "voice-language-normalizer",
            {
                "input_text": result.input_text[:300],
                "normalized_text": result.normalized_text[:300],
                "is_voice": result.is_voice,
                "changed": result.changed,
                "corrections": [asdict(item) for item in result.correction.corrections],
                "suspect_tokens": [asdict(item) for item in result.correction.suspect_tokens],
                "pending_confirmation_detected": result.pending_confirmation_detected,
                "pending_cancellation_detected": result.pending_cancellation_detected,
                "evidence": result.evidence,
            },
        )
    except Exception:
        pass
