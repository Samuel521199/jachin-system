"""State snapshot facade for the Cognitive Kernel.

This module merges caller metadata with the lightweight State Watcher. It never
runs blocking UI/OCR/file scans in the hot path.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from .contracts import StateSnapshot
from .state_service import get_state_fabric_snapshot, start_state_fabric_service


def _now_ms() -> int:
    return int(time.time() * 1000)


def build_state_snapshot(
    *,
    run_id: str,
    channel: str = "",
    implicit_attribution: dict[str, Any] | None = None,
    desktop_companion_context: dict[str, Any] | None = None,
    gateway_system_state: str | None = None,
) -> StateSnapshot:
    start_state_fabric_service()
    implicit = implicit_attribution or {}
    companion = desktop_companion_context or {}
    now = _now_ms()
    watcher = get_state_fabric_snapshot()

    active_window: dict[str, Any] = dict(watcher.get("active_window") or {})
    for key in ("active_window", "foreground_window", "window_title", "app_name"):
        value = implicit.get(key) if isinstance(implicit, dict) else None
        if value:
            active_window[key] = value

    voice_state = {
        "source": companion.get("voice_stt_source") or "",
        "raw_text": companion.get("voice_raw_stt_text") or companion.get("voice_asr_raw_text") or "",
        "corrected_text": companion.get("voice_corrected_text") or "",
        "final_text": companion.get("voice_final_text") or companion.get("voice_routed_text") or "",
        "fast_lane": bool(companion.get("voice_fast_lane")),
    }

    task_state = {
        "run_id": run_id,
        "channel": channel or "",
        "gateway_system_state": gateway_system_state or "",
        "resource_tags": implicit.get("resource_tags") if isinstance(implicit, dict) else None,
        "watcher_sampled_at_ms": watcher.get("sampled_at_ms"),
    }

    risk_state = dict(watcher.get("risk_state") or {})
    risk_state = {
        "unsaved_documents": "unknown",
        "modal_dialogs": "unknown",
        "permission_prompts": "unknown",
        **risk_state,
    }

    return StateSnapshot(
        snapshot_id=f"state_{uuid.uuid4().hex[:12]}",
        generated_at_ms=now,
        freshness_ms=max(0, now - int(watcher.get("sampled_at_ms") or now)),
        active_window=active_window,
        running_apps=list(watcher.get("running_apps") or []),
        recent_app_events=list(watcher.get("recent_app_events") or []),
        task_state=task_state,
        voice_state=voice_state,
        resource_state=dict(watcher.get("resource_state") or {}),
        risk_state=risk_state,
    )
