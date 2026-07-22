"""Resolve voice-facing task handles to cancellable run ids.

The desktop UI may know a task by a short background ``task_id`` while the L3
runtime cancels by ``run_id``.  This registry keeps the mapping generic and
source-agnostic so always-on voice can say "stop the current task" without
hard-coding app-specific state.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any


_LOCK = threading.RLock()
_RUNS: dict[str, dict[str, Any]] = {}
_ALIASES: dict[str, str] = {}
_SESSIONS: dict[str, str] = {}
_TTL_SEC = 60 * 60


@dataclass(slots=True)
class VoiceTaskHandleResolution:
    candidates: list[str] = field(default_factory=list)
    selected: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def register_voice_task_handle(
    run_id: str,
    *,
    channel: str = "",
    session_id: str = "",
    aliases: list[str] | None = None,
    title: str = "",
) -> None:
    rid = str(run_id or "").strip()
    if not rid:
        return
    now = time.time()
    alias_list = _clean_ids(list(aliases or []))
    if session_id:
        alias_list.append(str(session_id).strip())
    with _LOCK:
        _gc_locked(now)
        _RUNS[rid] = {
            "run_id": rid,
            "channel": str(channel or ""),
            "session_id": str(session_id or ""),
            "aliases": sorted(set(alias_list)),
            "title": str(title or ""),
            "registered_at": now,
        }
        for alias in alias_list:
            if alias and alias != rid:
                _ALIASES[alias] = rid
        if session_id:
            _SESSIONS[str(session_id).strip()] = rid


def unregister_voice_task_handle(run_id: str) -> None:
    rid = str(run_id or "").strip()
    if not rid:
        return
    with _LOCK:
        rec = _RUNS.pop(rid, None) or {}
        for alias in list(rec.get("aliases") or []):
            if _ALIASES.get(alias) == rid:
                _ALIASES.pop(alias, None)
        sid = str(rec.get("session_id") or "").strip()
        if sid and _SESSIONS.get(sid) == rid:
            _SESSIONS.pop(sid, None)


def resolve_voice_task_handles(
    *,
    target_task_id: str = "",
    voice_context: dict[str, Any] | None = None,
    session_id: str = "",
    channel: str = "",
    exclude_run_ids: list[str] | None = None,
) -> VoiceTaskHandleResolution:
    ctx = voice_context or {}
    active = ctx.get("voice_active_task_context")
    if not isinstance(active, dict):
        active = {}

    requested: list[str] = []
    requested.extend(_clean_ids([target_task_id, session_id, active.get("focused_task_id")]))
    tasks = active.get("active_tasks")
    if isinstance(tasks, list):
        for item in tasks:
            if isinstance(item, dict):
                requested.extend(_clean_ids([item.get("id"), item.get("run_id"), item.get("task_id")]))

    candidates: list[str] = []
    evidence: dict[str, Any] = {
        "target_task_id": target_task_id,
        "session_id": session_id,
        "channel": channel,
        "requested_ids": requested,
        "exclude_run_ids": _clean_ids(list(exclude_run_ids or [])),
        "resolution_steps": [],
    }
    excluded = set(evidence["exclude_run_ids"])

    with _LOCK:
        _gc_locked(time.time())
        for value in requested:
            if value in _RUNS and value not in excluded:
                candidates.append(value)
                evidence["resolution_steps"].append({"input": value, "matched": value, "via": "run_id"})
            alias_match = _ALIASES.get(value)
            if alias_match and alias_match not in excluded:
                candidates.append(alias_match)
                evidence["resolution_steps"].append({"input": value, "matched": alias_match, "via": "alias"})
        sid = str(session_id or "").strip()
        if sid and _SESSIONS.get(sid) and _SESSIONS[sid] not in excluded:
            candidates.append(_SESSIONS[sid])
            evidence["resolution_steps"].append({"input": sid, "matched": _SESSIONS[sid], "via": "session"})
        if not candidates:
            active_runs = sorted(
                [
                    rec
                    for rec in _RUNS.values()
                    if str(rec.get("run_id") or "") and str(rec.get("run_id") or "") not in excluded
                ],
                key=lambda rec: float(rec.get("registered_at") or 0.0),
                reverse=True,
            )
            preferred = [
                rec
                for rec in active_runs
                if (session_id and str(rec.get("session_id") or "") == str(session_id).strip())
                or (channel and str(rec.get("channel") or "") == str(channel).strip())
            ]
            rec = (preferred or active_runs or [None])[0]
            if rec:
                rid = str(rec.get("run_id") or "").strip()
                if rid:
                    candidates.append(rid)
                    evidence["resolution_steps"].append(
                        {
                            "input": session_id or channel or "latest",
                            "matched": rid,
                            "via": "latest_registered_run",
                            "title": str(rec.get("title") or "")[:120],
                        }
                    )

    for value in requested:
        # The id itself may already be a cancellable run_id even if this module
        # did not see the registration, so keep it as a last candidate.
        if value not in excluded:
            candidates.append(value)

    candidates = _dedupe([c for c in candidates if c])
    selected = candidates[0] if candidates else ""
    evidence["candidates"] = candidates
    evidence["selected"] = selected
    return VoiceTaskHandleResolution(candidates=candidates, selected=selected, evidence=evidence)


def cancel_resolved_voice_targets(resolution: VoiceTaskHandleResolution) -> dict[str, Any]:
    try:
        from l3_node.primitives.agent_tasks.agent_cancel import request_cancel_run
    except Exception as exc:
        return {
            "ok": False,
            "error": type(exc).__name__,
            "attempts": [],
            "resolution": resolution.to_dict(),
        }

    attempts: list[dict[str, Any]] = []
    ok_any = False
    for rid in resolution.candidates:
        ok = bool(request_cancel_run(rid))
        attempts.append({"run_id": rid, "ok": ok})
        ok_any = ok_any or ok
        if ok:
            break
    return {
        "ok": ok_any,
        "attempts": attempts,
        "resolution": resolution.to_dict(),
    }


def _clean_ids(values: list[Any]) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text:
            out.append(text[:160])
    return out


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _gc_locked(now: float) -> None:
    stale = [rid for rid, rec in _RUNS.items() if now - float(rec.get("registered_at") or now) > _TTL_SEC]
    for rid in stale:
        rec = _RUNS.pop(rid, None) or {}
        for alias in list(rec.get("aliases") or []):
            if _ALIASES.get(alias) == rid:
                _ALIASES.pop(alias, None)
        sid = str(rec.get("session_id") or "").strip()
        if sid and _SESSIONS.get(sid) == rid:
            _SESSIONS.pop(sid, None)
