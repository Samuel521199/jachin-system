from __future__ import annotations

from pathlib import Path
from typing import Any


class VoiceUnderstandingCorrector:
    """STT-only compatibility shim.

    The voice service is deliberately not a business understanding layer. It
    must not correct domain entities, choose intents, fill slots, ask follow-up
    questions, or generate reply plans. Voice input enters L3 as plain text plus
    diagnostic evidence; L3 owns intent, memory, routing, clarification, tools,
    verification, and final wording.

    This class remains only for older imports. Its output shape is stable, but
    every business-understanding field is intentionally empty.
    """

    def __init__(
        self,
        lexicon_file: Path | None = None,
        user_aliases_file: Path | None = None,
    ) -> None:
        self.lexicon_file = lexicon_file
        self.user_aliases_file = user_aliases_file

    def correct(self, text: str) -> dict[str, Any]:
        raw = str(text or "")
        return {
            "raw_text": raw,
            "corrected_text": raw,
            "user_message": "",
            "user_message_source": "",
            "confidence": 0.0,
            "needs_confirmation": False,
            "understanding": {
                "strategy": "stt_passthrough",
                "voice_layer_scope": "stt_only",
                "asr_texts": [{"engine": "jvs", "text": raw}],
                "entity_candidates": [],
                "task_candidates": [],
                "selected": {},
                "reply_plan": {},
                "reply_source": "none",
                "note": "Voice service returns STT text only; L3 owns intent, slots, memory, routing, and follow-up decisions.",
            },
            "reply_plan": {},
        }
