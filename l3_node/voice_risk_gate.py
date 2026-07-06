"""Risk and confidence gate for voice STT follow-up recognition.

This module implements Phase 0-C decisioning without binding the product to a
specific cloud ASR provider. Callers can use the returned decision to run a
stronger local model or a cloud fallback when credentials are configured.
"""
from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any


_HIGH_RISK_RE = re.compile(
    r"(?:\u53d1\u6d88\u606f|\u53d1\u9001|\u53d1\u7ed9|\u5220\u9664|\u79fb\u52a8|\u91cd\u547d\u540d|\u8986\u76d6|\u4e0a\u4f20|send|delete|remove|move|rename|overwrite|upload)",
    re.I,
)
_NEGATION_RE = re.compile(r"(?:\u4e0d\u8981|\u522b|\u4e0d\u7528|\u7981\u6b62|do\s*not|don't|dont|no)", re.I)
_GARBLED_RE = re.compile(r"(?:[A-Za-z]\s*[\u4e00-\u9fff]\s*[A-Za-z]|[\u4e00-\u9fff]\s*[A-Za-z]\s*[\u4e00-\u9fff])")


@dataclass
class SecondaryRecognitionDecision:
    should_run: bool
    risk_level: str
    reasons: list[str] = field(default_factory=list)
    preferred_provider: str = "none"
    cloud_fallback_enabled: bool = False
    local_strong_model_enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _suspect_ratio(suspect_tokens: list[dict[str, Any]] | None, text: str) -> float:
    if not suspect_tokens:
        return 0.0
    meaningful = max(1, len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", text or "")))
    return min(1.0, len(suspect_tokens) / meaningful)


def decide_secondary_recognition(
    *,
    text: str,
    confidence: float | None = None,
    suspect_tokens: list[dict[str, Any]] | None = None,
    intent_task_type: str = "",
) -> SecondaryRecognitionDecision:
    raw = str(text or "")
    conf = float(confidence if confidence is not None else 1.0)
    reasons: list[str] = []
    high_risk = bool(_HIGH_RISK_RE.search(raw)) or intent_task_type in {
        "lark_message_send",
        "project_briefing_delivery",
        "codex_ask_lark_send",
        "file_to_app",
    }
    if high_risk:
        reasons.append("high_risk_intent")
    if conf < float(os.getenv("JACHIN_STT_LOW_CONF_THRESHOLD", "0.70")):
        reasons.append("low_stt_confidence")
    if _suspect_ratio(suspect_tokens, raw) > float(os.getenv("JACHIN_STT_SUSPECT_RATIO_THRESHOLD", "0.30")):
        reasons.append("many_suspect_tokens")
    if _GARBLED_RE.search(raw):
        reasons.append("mixed_latin_cjk_garble")
    if _NEGATION_RE.search(raw) and high_risk:
        reasons.append("high_risk_with_negation")

    cloud_enabled = os.getenv("JACHIN_STT_CLOUD_FALLBACK", "").strip().lower() in {"1", "true", "yes", "on"}
    local_enabled = os.getenv("JACHIN_STT_STRONG_LOCAL_MODEL", "").strip() != ""
    should_run = bool(reasons and (high_risk or conf < 0.70 or cloud_enabled or local_enabled))
    provider = "none"
    if should_run:
        if local_enabled:
            provider = "local_strong_model"
        elif cloud_enabled:
            provider = "cloud_asr"
        else:
            provider = "mark_low_confidence"

    return SecondaryRecognitionDecision(
        should_run=should_run,
        risk_level="high" if high_risk else ("medium" if reasons else "low"),
        reasons=reasons,
        preferred_provider=provider,
        cloud_fallback_enabled=cloud_enabled,
        local_strong_model_enabled=local_enabled,
    )
