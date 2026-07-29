"""Project-level fact chain for user-confirmed Work Ledger events."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any


_FACT_LOCK = threading.RLock()
_GENERIC_TOKENS = {
    "and",
    "the",
    "for",
    "from",
    "with",
    "into",
    "work",
    "task",
    "project",
    "implemented",
    "updated",
    "changed",
    "fixed",
}
_OPEN_MARKERS = re.compile(
    r"\b(failed|failure|blocked|error|todo|pending|unfinished|retry)\b|"
    r"\u5931\u8d25|\u963b\u585e|\u672a\u5b8c\u6210|\u5f85\u5904\u7406|"
    r"\u5f85\u4fee\u590d|\u91cd\u8bd5"
)
_PROGRESS_MARKERS = re.compile(
    r"\b(in progress|working on|implementing|developing|started)\b|"
    r"\u8fdb\u884c\u4e2d|\u5f00\u53d1\u4e2d|\u5df2\u5f00\u59cb|"
    r"\u6b63\u5728\u5b9e\u73b0|\u6b63\u5728\u4fee\u590d"
)
_COMPLETION_MARKERS = re.compile(
    r"\b(completed|complete|passed|verified|resolved|fixed|closed|done)\b|"
    r"\u5df2\u5b8c\u6210|\u5b8c\u6210|\u5df2\u901a\u8fc7|\u9a8c\u8bc1\u901a\u8fc7|"
    r"\u5df2\u89e3\u51b3|\u5df2\u4fee\u590d|\u5df2\u95ed\u73af"
)
_FAILURE_MARKERS = re.compile(
    r"\b(failed|failure|blocked|error|regression|broken|broke|retry)\b|"
    r"\u5931\u8d25|\u963b\u585e|\u62a5\u9519|\u56de\u5f52\u5931\u8d25|"
    r"\u518d\u6b21\u5931\u8d25|\u91cd\u65b0\u6253\u5f00"
)
_DECISION_MARKERS = re.compile(
    r"\b(decision|decided|selected|chose|choice|tradeoff)\b|"
    r"\u51b3\u5b9a|\u9009\u62e9|\u53d6\u820d|\u7ed3\u8bba"
)
_NEXT_ACTION_MARKERS = re.compile(
    r"\b(next step|follow-up|follow up|todo|pending action)\b|"
    r"\u4e0b\u4e00\u6b65|\u540e\u7eed\u52a8\u4f5c|\u5f85\u529e|\u5f85\u5904\u7406"
)
_SUPERSEDE_MARKERS = re.compile(
    r"\b(superseded|replaced by|obsolete)\b|"
    r"\u5df2\u66ff\u4ee3|\u88ab\u66ff\u4ee3|\u5df2\u5e9f\u5f03|\u6539\u4e3a"
)
_VALID_FACT_STATES = {
    "open",
    "in_progress",
    "completed",
    "reopened",
    "superseded",
}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _canonical_project_path(project_path: str) -> str:
    return str(Path(project_path).expanduser().resolve())


def _project_key(project_path: str) -> str:
    normalized = _canonical_project_path(project_path).lower()
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]
    return f"project_{digest}"


def _fact_path(project_path: str) -> Path:
    from l3_node.work_ledger import work_ledger_home

    return work_ledger_home() / "project_facts" / f"{_project_key(project_path)}.json"


def _empty_index(project_path: str) -> dict[str, Any]:
    canonical = _canonical_project_path(project_path)
    return {
        "schema_version": 2,
        "project_key": _project_key(canonical),
        "project_path": canonical,
        "facts": [],
        "review_queue": [],
        "separation_rules": [],
        "updated_at": "",
    }


def _normalize_fact(fact: dict[str, Any]) -> dict[str, Any]:
    state = str(fact.get("state") or "completed")
    if state not in _VALID_FACT_STATES:
        state = "open" if state == "pending" else "completed"
    fact["state"] = state
    fact.setdefault("lifecycle", [])
    fact.setdefault("decisions", [])
    fact.setdefault("failure_attempts", [])
    fact.setdefault("next_actions", [])
    fact.setdefault("superseded_by_fact_id", "")
    fact.setdefault("supersedes_fact_ids", [])
    fact.setdefault("last_state_change_at", fact.get("first_seen_at") or "")
    fact.setdefault(
        "last_state_change_session_id",
        fact.get("first_session_id") or "",
    )
    fact["state_version"] = len(fact.get("lifecycle") or [])
    return fact


def _load_index(project_path: str) -> dict[str, Any]:
    path = _fact_path(project_path)
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                data.setdefault("facts", [])
                data.setdefault("review_queue", [])
                data.setdefault("separation_rules", [])
                data["schema_version"] = max(2, int(data.get("schema_version") or 1))
                data["facts"] = [
                    _normalize_fact(row)
                    for row in data.get("facts") or []
                    if isinstance(row, dict)
                ]
                return data
    except (OSError, ValueError, TypeError):
        pass
    return _empty_index(project_path)


def _save_index(index: dict[str, Any]) -> None:
    path = _fact_path(str(index.get("project_path") or ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    index["updated_at"] = _now_iso()
    temp_path = path.with_suffix(
        f"{path.suffix}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temp_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        last_error: OSError | None = None
        for attempt in range(6):
            try:
                os.replace(temp_path, path)
                return
            except PermissionError as exc:
                last_error = exc
                if attempt >= 5:
                    raise
                time.sleep(0.02 * (attempt + 1))
        if last_error:
            raise last_error
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


def _tokens(text: str) -> set[str]:
    values = re.findall(
        r"[A-Za-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,8}",
        str(text or "").lower(),
    )
    return {
        value.strip("._-")
        for value in values
        if value.strip("._-") and value.strip("._-") not in _GENERIC_TOKENS
    }


def _artifact_tokens(text: str) -> set[str]:
    artifacts = set()
    pattern = (
        r"(?:[A-Za-z]:[\\/])?[\w.@+-]+(?:[\\/][\w.@+-]+)*"
        r"\.(?:py|rs|ts|tsx|js|jsx|json|md|toml|yaml|yml|ps1|sh|sql)"
    )
    for value in re.findall(pattern, str(text or ""), flags=re.I):
        artifacts.add(value.replace("\\", "/").lower())
    return artifacts


def _event_identity(event: dict[str, Any]) -> dict[str, Any]:
    text = " ".join(
        (
            str(event.get("summary") or ""),
            str(event.get("excerpt") or ""),
        )
    )
    tokens = _tokens(text)
    tokens.update(
        str(item).strip().lower()
        for item in event.get("dedupe_tokens") or []
        if str(item).strip()
    )
    artifacts = _artifact_tokens(text)
    identity_tokens = sorted(tokens)[:100]
    identity_seed = "|".join(sorted(artifacts) + identity_tokens)
    return {
        "tokens": identity_tokens,
        "artifacts": sorted(artifacts),
        "fingerprint": hashlib.sha256(identity_seed.encode("utf-8")).hexdigest(),
    }


def _similarity(left: dict[str, Any], right: dict[str, Any]) -> dict[str, float]:
    left_tokens = set(left.get("identity_tokens") or left.get("tokens") or [])
    right_tokens = set(right.get("identity_tokens") or right.get("tokens") or [])
    intersection = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    token_jaccard = intersection / max(1, union)
    token_containment = intersection / max(1, min(len(left_tokens), len(right_tokens)))

    left_artifacts = set(left.get("artifact_tokens") or left.get("artifacts") or [])
    right_artifacts = set(right.get("artifact_tokens") or right.get("artifacts") or [])
    artifact_intersection = len(left_artifacts & right_artifacts)
    artifact_union = len(left_artifacts | right_artifacts)
    artifact_jaccard = artifact_intersection / max(1, artifact_union)
    score = max(
        token_jaccard,
        token_containment * 0.88,
        token_jaccard * 0.72 + artifact_jaccard * 0.28,
    )
    return {
        "score": round(min(1.0, score), 4),
        "token_jaccard": round(token_jaccard, 4),
        "token_containment": round(token_containment, 4),
        "artifact_jaccard": round(artifact_jaccard, 4),
        "shared_artifacts": float(artifact_intersection),
    }


def _is_strong_match(metrics: dict[str, float]) -> bool:
    if metrics["token_jaccard"] >= 0.74:
        return True
    if metrics["token_containment"] >= 0.88:
        return True
    return metrics["shared_artifacts"] > 0 and metrics["token_containment"] >= 0.58


def _fact_pair_key(left_fact_id: str, right_fact_id: str) -> str:
    values = sorted((str(left_fact_id or ""), str(right_fact_id or "")))
    return "::".join(values)


def _pair_is_separated(
    index: dict[str, Any],
    left_fact_id: str,
    right_fact_id: str,
) -> bool:
    pair_key = _fact_pair_key(left_fact_id, right_fact_id)
    return any(
        isinstance(row, dict)
        and row.get("pair_key") == pair_key
        and row.get("status") == "active"
        for row in index.get("separation_rules") or []
    )


def _event_text(event: dict[str, Any]) -> str:
    return f"{event.get('summary') or ''} {event.get('excerpt') or ''}".strip()


def _classify_event(event: dict[str, Any]) -> dict[str, Any]:
    text = _event_text(event)
    lowered = text.lower()
    explicit_kind = str(
        event.get("fact_event_kind")
        or event.get("lifecycle_event")
        or event.get("event_kind")
        or ""
    ).strip().lower()
    explicit_state = str(event.get("target_state") or "").strip().lower()
    if explicit_state not in _VALID_FACT_STATES:
        explicit_state = ""

    kind = explicit_kind
    if not kind:
        if event.get("superseded_by_fact_id") or _SUPERSEDE_MARKERS.search(lowered):
            kind = "supersede"
        elif _FAILURE_MARKERS.search(lowered):
            kind = "failure"
        elif _COMPLETION_MARKERS.search(lowered):
            kind = "completion"
        elif _PROGRESS_MARKERS.search(lowered):
            kind = "progress"
        elif _DECISION_MARKERS.search(lowered):
            kind = "decision"
        elif _NEXT_ACTION_MARKERS.search(lowered):
            kind = "next_action"
        else:
            kind = "observation"

    decision = str(event.get("decision") or "").strip()
    failure_reason = str(event.get("failure_reason") or "").strip()
    next_action = str(event.get("next_action") or "").strip()
    if not decision and _DECISION_MARKERS.search(lowered):
        decision = str(event.get("summary") or "").strip()
    if not failure_reason and kind in {"failure", "reopen"}:
        failure_reason = str(event.get("summary") or "").strip()
    if not next_action and _NEXT_ACTION_MARKERS.search(lowered):
        next_action = str(event.get("summary") or "").strip()
    return {
        "kind": kind,
        "explicit_state": explicit_state,
        "decision": decision[:500],
        "failure_reason": failure_reason[:500],
        "next_action": next_action[:500],
        "superseded_by_fact_id": str(event.get("superseded_by_fact_id") or "").strip(),
        "supersedes_fact_id": str(event.get("supersedes_fact_id") or "").strip(),
    }


def _initial_fact_state(event: dict[str, Any]) -> str:
    semantics = _classify_event(event)
    if semantics["explicit_state"]:
        return semantics["explicit_state"]
    if semantics["kind"] in {"failure", "reopen"} or _OPEN_MARKERS.search(
        _event_text(event).lower()
    ):
        return "open"
    if semantics["kind"] == "progress":
        return "in_progress"
    if semantics["kind"] == "supersede":
        return "superseded"
    return "completed"


def _transition_target(
    current_state: str,
    semantics: dict[str, Any],
) -> str:
    explicit_state = str(semantics.get("explicit_state") or "")
    if explicit_state:
        return explicit_state
    kind = str(semantics.get("kind") or "observation")
    if kind in {"failure", "reopen"}:
        return "reopened" if current_state == "completed" else "open"
    if kind == "progress":
        return "in_progress"
    if kind in {"completion", "close"}:
        return "completed"
    if kind == "supersede":
        return "superseded"
    return current_state


def _chain_entry(
    kind: str,
    session_id: str,
    event: dict[str, Any],
    occurrence: dict[str, Any],
    text: str,
) -> dict[str, Any]:
    seed = f"{kind}:{session_id}:{event.get('event_id')}:{text}"
    entry_id = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]
    return {
        "entry_id": f"{kind}_{entry_id}",
        "kind": kind,
        "session_id": session_id,
        "event_id": str(event.get("event_id") or ""),
        "text": text[:500],
        "observed_at": occurrence["observed_at"],
        "verification_evidence_id": occurrence.get("verification_evidence_id") or "",
    }


def _append_unique_chain_entry(
    fact: dict[str, Any],
    field: str,
    entry: dict[str, Any],
) -> None:
    values = fact.setdefault(field, [])
    if not any(
        isinstance(row, dict) and row.get("entry_id") == entry["entry_id"]
        for row in values
    ):
        values.append(entry)


def _apply_event_semantics(
    fact: dict[str, Any],
    session_id: str,
    event: dict[str, Any],
    occurrence: dict[str, Any],
    *,
    initial: bool = False,
) -> dict[str, Any] | None:
    semantics = _classify_event(event)
    previous_state = "" if initial else str(fact.get("state") or "open")
    target_state = (
        _initial_fact_state(event)
        if initial
        else _transition_target(previous_state, semantics)
    )
    transition = None
    if initial or target_state != previous_state:
        reason = str(
            event.get("state_reason")
            or semantics.get("failure_reason")
            or event.get("summary")
            or semantics.get("kind")
            or ""
        )[:500]
        transition_seed = (
            f"{fact.get('fact_id')}:{previous_state}:{target_state}:"
            f"{session_id}:{event.get('event_id')}"
        )
        transition = {
            "transition_id": (
                "transition_"
                + hashlib.sha256(transition_seed.encode("utf-8")).hexdigest()[:20]
            ),
            "from_state": previous_state or None,
            "to_state": target_state,
            "reason": reason,
            "event_kind": semantics["kind"],
            "session_id": session_id,
            "event_id": str(event.get("event_id") or ""),
            "observed_at": occurrence["observed_at"],
            "verification_evidence_id": occurrence.get("verification_evidence_id") or "",
        }
        lifecycle = fact.setdefault("lifecycle", [])
        if not any(
            isinstance(row, dict)
            and row.get("transition_id") == transition["transition_id"]
            for row in lifecycle
        ):
            lifecycle.append(transition)
        fact["state"] = target_state
        fact["last_state_change_at"] = occurrence["observed_at"]
        fact["last_state_change_session_id"] = session_id
        fact["state_version"] = len(lifecycle)

    if semantics.get("decision"):
        _append_unique_chain_entry(
            fact,
            "decisions",
            _chain_entry(
                "decision",
                session_id,
                event,
                occurrence,
                str(semantics["decision"]),
            ),
        )
    if semantics.get("failure_reason"):
        _append_unique_chain_entry(
            fact,
            "failure_attempts",
            _chain_entry(
                "failure",
                session_id,
                event,
                occurrence,
                str(semantics["failure_reason"]),
            ),
        )
    if semantics.get("next_action"):
        _append_unique_chain_entry(
            fact,
            "next_actions",
            _chain_entry(
                "next_action",
                session_id,
                event,
                occurrence,
                str(semantics["next_action"]),
            ),
        )
    if semantics.get("superseded_by_fact_id"):
        fact["superseded_by_fact_id"] = semantics["superseded_by_fact_id"]
    fact["last_event_kind"] = semantics["kind"]
    return transition


def _make_occurrence(
    session_id: str,
    event: dict[str, Any],
    verification_evidence_id: str,
) -> dict[str, Any]:
    event_id = str(event.get("event_id") or "")
    occurrence_seed = f"{session_id}:{event_id}"
    occurrence_id = hashlib.sha256(occurrence_seed.encode("utf-8")).hexdigest()[:20]
    return {
        "occurrence_id": f"occ_{occurrence_id}",
        "session_id": session_id,
        "event_id": event_id,
        "summary": str(event.get("summary") or "")[:300],
        "observed_at": str(event.get("observed_at") or _now_iso()),
        "source_types": list(event.get("source_types") or []),
        "source_chain": list(event.get("source_chain") or []),
        "verification_evidence_id": str(verification_evidence_id or ""),
    }


def _new_fact(
    project_path: str,
    session_id: str,
    event: dict[str, Any],
    identity: dict[str, Any],
    occurrence: dict[str, Any],
) -> dict[str, Any]:
    fact = {
        "fact_id": f"fact_{identity['fingerprint'][:20]}",
        "project_key": _project_key(project_path),
        "project_path": _canonical_project_path(project_path),
        "canonical_summary": str(event.get("summary") or "").strip()[:300],
        "state": _initial_fact_state(event),
        "state_version": 0,
        "trust_level": "user_confirmed",
        "identity_tokens": identity["tokens"],
        "artifact_tokens": identity["artifacts"],
        "fingerprints": [identity["fingerprint"]],
        "source_types": sorted(
            {str(item) for item in event.get("source_types") or [] if str(item)}
        ),
        "session_ids": [session_id],
        "first_session_id": session_id,
        "first_seen_at": occurrence["observed_at"],
        "last_seen_at": occurrence["observed_at"],
        "occurrence_count": 1,
        "occurrences": [occurrence],
        "lifecycle": [],
        "decisions": [],
        "failure_attempts": [],
        "next_actions": [],
        "superseded_by_fact_id": "",
        "supersedes_fact_ids": [],
    }
    _apply_event_semantics(
        fact,
        session_id,
        event,
        occurrence,
        initial=True,
    )
    return fact


def _append_occurrence(
    fact: dict[str, Any],
    session_id: str,
    event: dict[str, Any],
    identity: dict[str, Any],
    occurrence: dict[str, Any],
) -> dict[str, Any] | None:
    occurrence_exists = any(
        row.get("occurrence_id") == occurrence["occurrence_id"]
        for row in fact.get("occurrences") or []
        if isinstance(row, dict)
    )
    if not occurrence_exists:
        fact.setdefault("occurrences", []).append(occurrence)
    fact["occurrence_count"] = len(fact.get("occurrences") or [])
    fact["last_seen_at"] = occurrence["observed_at"]
    fact["session_ids"] = list(
        dict.fromkeys((fact.get("session_ids") or []) + [session_id])
    )
    fact["source_types"] = sorted(
        set(fact.get("source_types") or []) | set(event.get("source_types") or [])
    )
    fact["fingerprints"] = list(
        dict.fromkeys(
            (fact.get("fingerprints") or []) + [identity["fingerprint"]]
        )
    )
    return _apply_event_semantics(
        fact,
        session_id,
        event,
        occurrence,
    )


def record_confirmed_work_event(
    session_id: str,
    event: dict[str, Any],
    *,
    verification_evidence_id: str = "",
) -> dict[str, Any]:
    """Record one accepted event and return its stable project fact identity."""
    from l3_node.work_ledger import get_session_detail

    session = get_session_detail(session_id, evidence_limit=5)["session"]
    project_path = str(session.get("project_path") or "").strip()
    if not project_path:
        return {"ok": False, "reason": "session_has_no_project_path", "fact": None}

    identity = _event_identity(event)
    occurrence = _make_occurrence(
        session_id,
        event,
        verification_evidence_id
        or str(event.get("verification_evidence_id") or ""),
    )
    with _FACT_LOCK:
        index = _load_index(project_path)
        facts = [fact for fact in index.get("facts") or [] if isinstance(fact, dict)]
        explicit_fact_id = str(event.get("project_fact_id") or "").strip()
        explicit_fact = next(
            (fact for fact in facts if fact.get("fact_id") == explicit_fact_id),
            None,
        )
        exact = explicit_fact or next(
            (
                fact
                for fact in facts
                if identity["fingerprint"] in (fact.get("fingerprints") or [])
                or any(
                    row.get("occurrence_id") == occurrence["occurrence_id"]
                    for row in fact.get("occurrences") or []
                    if isinstance(row, dict)
                )
            ),
            None,
        )

        ranked: list[tuple[dict[str, float], dict[str, Any]]] = sorted(
            (
                (_similarity(identity, fact), fact)
                for fact in facts
                if fact.get("state") != "superseded" or fact is explicit_fact
            ),
            key=lambda pair: pair[0]["score"],
            reverse=True,
        )
        best_metrics, best_fact = ranked[0] if ranked else (
            {
                "score": 0.0,
                "token_jaccard": 0.0,
                "token_containment": 0.0,
                "artifact_jaccard": 0.0,
                "shared_artifacts": 0.0,
            },
            None,
        )

        fact = exact
        match_type = "new"
        transition: dict[str, Any] | None = None
        if explicit_fact is not None:
            match_type = "explicit_fact_id"
        elif fact is not None:
            match_type = "exact_identity"
        elif best_fact is not None and _is_strong_match(best_metrics):
            fact = best_fact
            match_type = "strong_identity"

        if fact is None:
            fact = _new_fact(project_path, session_id, event, identity, occurrence)
            facts.append(fact)
            lifecycle = fact.get("lifecycle") or []
            transition = lifecycle[-1] if lifecycle else None
            should_review = (
                best_fact is not None
                and best_metrics["score"] >= 0.34
                and not _pair_is_separated(
                    index,
                    str(fact.get("fact_id") or ""),
                    str(best_fact.get("fact_id") or ""),
                )
            )
            if should_review:
                review_seed = f"{fact['fact_id']}:{best_fact['fact_id']}"
                candidate_id = (
                    "fact_review_"
                    + hashlib.sha256(review_seed.encode("utf-8")).hexdigest()[:16]
                )
                review_queue = index.setdefault("review_queue", [])
                if not any(
                    row.get("candidate_id") == candidate_id
                    for row in review_queue
                    if isinstance(row, dict)
                ):
                    review_queue.append(
                        {
                            "candidate_id": candidate_id,
                            "status": "pending",
                            "new_fact_id": fact["fact_id"],
                            "possible_match_fact_id": best_fact["fact_id"],
                            "similarity": best_metrics,
                            "reason": "similar_confirmed_facts_require_user_review",
                            "created_at": _now_iso(),
                        }
                    )
                match_type = "new_with_review_candidate"
        else:
            transition = _append_occurrence(
                fact,
                session_id,
                event,
                identity,
                occurrence,
            )

        supersedes_fact_id = str(
            _classify_event(event).get("supersedes_fact_id") or ""
        ).strip()
        if supersedes_fact_id and supersedes_fact_id != fact.get("fact_id"):
            replaced = next(
                (
                    row
                    for row in facts
                    if row.get("fact_id") == supersedes_fact_id
                ),
                None,
            )
            if replaced is not None:
                fact["supersedes_fact_ids"] = list(
                    dict.fromkeys(
                        (fact.get("supersedes_fact_ids") or [])
                        + [supersedes_fact_id]
                    )
                )
                supersede_event = {
                    "event_id": f"{event.get('event_id')}:supersedes",
                    "summary": (
                        f"Superseded by {fact.get('fact_id')}: "
                        f"{event.get('summary') or ''}"
                    ),
                    "target_state": "superseded",
                    "fact_event_kind": "supersede",
                    "superseded_by_fact_id": fact.get("fact_id"),
                }
                _apply_event_semantics(
                    replaced,
                    session_id,
                    supersede_event,
                    occurrence,
                )
                replaced["session_ids"] = list(
                    dict.fromkeys(
                        (replaced.get("session_ids") or []) + [session_id]
                    )
                )

        index["facts"] = facts
        _save_index(index)
        return {
            "ok": True,
            "fact": dict(fact),
            "match_type": match_type,
            "similarity": best_metrics,
            "review_pending": match_type == "new_with_review_candidate",
            "state_transition": transition,
            "state_changed": transition is not None,
        }


def update_project_fact(
    session_id: str,
    fact_id: str,
    *,
    target_state: str = "",
    reason: str = "",
    decision: str = "",
    failure_reason: str = "",
    next_action: str = "",
    superseded_by_fact_id: str = "",
) -> dict[str, Any]:
    clean_state = str(target_state or "").strip().lower()
    if clean_state and clean_state not in _VALID_FACT_STATES:
        raise ValueError(
            "target_state must be open, in_progress, completed, reopened, or superseded"
        )
    clean_fact_id = str(fact_id or "").strip()
    if not clean_fact_id:
        raise ValueError("fact_id is required")
    event_kind = {
        "completed": "completion",
        "in_progress": "progress",
        "reopened": "reopen",
        "open": "failure" if failure_reason else "progress",
        "superseded": "supersede",
    }.get(clean_state, "observation")
    event_id_seed = (
        f"{session_id}:{clean_fact_id}:{clean_state}:{reason}:"
        f"{decision}:{failure_reason}:{next_action}:{_now_iso()}"
    )
    event_id = hashlib.sha256(event_id_seed.encode("utf-8")).hexdigest()[:20]
    summary = str(reason or decision or failure_reason or next_action or clean_state)
    event = {
        "event_id": f"fact-update-{event_id}",
        "summary": summary,
        "excerpt": summary,
        "project_fact_id": clean_fact_id,
        "target_state": clean_state,
        "fact_event_kind": event_kind,
        "decision": str(decision or ""),
        "failure_reason": str(failure_reason or ""),
        "next_action": str(next_action or ""),
        "superseded_by_fact_id": str(superseded_by_fact_id or ""),
        "source_types": ["user_fact_update"],
        "source_chain": [
            {
                "source_type": "user_fact_update",
                "source_uri": f"work-ledger://project-facts/{clean_fact_id}",
            }
        ],
    }
    return record_confirmed_work_event(session_id, event)


def get_project_fact_index(project_path: str) -> dict[str, Any]:
    if not str(project_path or "").strip():
        return {
            "schema_version": 2,
            "project_key": "",
            "project_path": "",
            "facts": [],
            "all_facts": [],
            "review_queue": [],
            "summary": {
                "fact_count": 0,
                "open_count": 0,
                "in_progress_count": 0,
                "completed_count": 0,
                "reopened_count": 0,
                "superseded_count": 0,
                "review_pending": 0,
            },
        }
    with _FACT_LOCK:
        index = _load_index(project_path)
        facts = sorted(
            [dict(row) for row in index.get("facts") or [] if isinstance(row, dict)],
            key=lambda row: str(row.get("last_seen_at") or ""),
            reverse=True,
        )
        review_queue = [
            dict(row)
            for row in index.get("review_queue") or []
            if isinstance(row, dict) and row.get("status") == "pending"
        ]
        return {
            **index,
            "facts": facts,
            "review_queue": review_queue,
            "summary": {
                "fact_count": len(facts),
                "open_count": sum(1 for row in facts if row.get("state") == "open"),
                "in_progress_count": sum(
                    1 for row in facts if row.get("state") == "in_progress"
                ),
                "completed_count": sum(
                    1 for row in facts if row.get("state") == "completed"
                ),
                "reopened_count": sum(
                    1 for row in facts if row.get("state") == "reopened"
                ),
                "superseded_count": sum(
                    1 for row in facts if row.get("state") == "superseded"
                ),
                "review_pending": len(review_queue),
            },
        }


def get_session_fact_context(session_id: str) -> dict[str, Any]:
    from l3_node.work_ledger import get_session_detail

    session = get_session_detail(session_id, evidence_limit=5)["session"]
    index = get_project_fact_index(str(session.get("project_path") or ""))
    current = [
        fact
        for fact in index.get("facts") or []
        if session_id in (fact.get("session_ids") or [])
    ]
    state_changed_facts: list[dict[str, Any]] = []
    for fact in current:
        session_transitions = [
            row
            for row in fact.get("lifecycle") or []
            if isinstance(row, dict) and row.get("session_id") == session_id
        ]
        if session_transitions:
            state_changed_facts.append(
                {
                    **fact,
                    "session_transitions": session_transitions,
                }
            )
    fact_by_id = {
        str(fact.get("fact_id") or ""): fact
        for fact in index.get("facts") or []
        if isinstance(fact, dict)
    }
    predecessor_facts: list[dict[str, Any]] = []
    predecessor_seen: set[str] = set()
    predecessor_stack = [
        str(predecessor_id)
        for fact in current
        for predecessor_id in fact.get("supersedes_fact_ids") or []
        if str(predecessor_id)
    ]
    while predecessor_stack:
        predecessor_id = predecessor_stack.pop()
        if predecessor_id in predecessor_seen:
            continue
        predecessor_seen.add(predecessor_id)
        predecessor = fact_by_id.get(predecessor_id)
        if not predecessor:
            continue
        predecessor_facts.append(predecessor)
        predecessor_stack.extend(
            str(item)
            for item in predecessor.get("supersedes_fact_ids") or []
            if str(item)
        )
    return {
        "all_facts": index.get("facts") or [],
        "new_facts": [
            fact for fact in current if fact.get("first_session_id") == session_id
        ],
        "continued_facts": [
            fact for fact in current if fact.get("first_session_id") != session_id
        ],
        "state_changed_facts": state_changed_facts,
        "predecessor_facts": predecessor_facts,
        "completed_this_session": [
            fact
            for fact in state_changed_facts
            if any(
                row.get("to_state") == "completed"
                for row in fact.get("session_transitions") or []
            )
        ],
        "reopened_this_session": [
            fact
            for fact in state_changed_facts
            if any(
                row.get("to_state") == "reopened"
                for row in fact.get("session_transitions") or []
            )
        ],
        "superseded_this_session": [
            fact
            for fact in state_changed_facts
            if any(
                row.get("to_state") == "superseded"
                for row in fact.get("session_transitions") or []
            )
        ],
        "prior_open_facts": [
            fact
            for fact in index.get("facts") or []
            if fact.get("state") in {"open", "in_progress", "reopened"}
            and fact.get("first_session_id") != session_id
        ][:20],
        "review_pending": index.get("review_queue") or [],
        "summary": index.get("summary") or {},
    }


def _merge_fact(target: dict[str, Any], source: dict[str, Any]) -> None:
    occurrences = {
        row.get("occurrence_id"): row
        for row in (target.get("occurrences") or []) + (source.get("occurrences") or [])
        if isinstance(row, dict) and row.get("occurrence_id")
    }
    target["occurrences"] = list(occurrences.values())
    target["occurrence_count"] = len(target["occurrences"])
    target["session_ids"] = list(
        dict.fromkeys(
            (target.get("session_ids") or []) + (source.get("session_ids") or [])
        )
    )
    target["source_types"] = sorted(
        set(target.get("source_types") or []) | set(source.get("source_types") or [])
    )
    target["fingerprints"] = list(
        dict.fromkeys(
            (target.get("fingerprints") or []) + (source.get("fingerprints") or [])
        )
    )
    target["last_seen_at"] = max(
        str(target.get("last_seen_at") or ""),
        str(source.get("last_seen_at") or ""),
    )
    for field in ("lifecycle", "decisions", "failure_attempts", "next_actions"):
        merged_entries = {
            str(
                row.get("transition_id")
                or row.get("entry_id")
                or hashlib.sha256(
                    json.dumps(row, sort_keys=True, default=str).encode("utf-8")
                ).hexdigest()
            ): row
            for row in (target.get(field) or []) + (source.get(field) or [])
            if isinstance(row, dict)
        }
        target[field] = sorted(
            merged_entries.values(),
            key=lambda row: str(row.get("observed_at") or ""),
        )
    transitions = target.get("lifecycle") or []
    if transitions:
        latest_transition = transitions[-1]
        target["state"] = latest_transition.get("to_state") or target.get("state")
        target["last_state_change_at"] = latest_transition.get("observed_at") or ""
        target["last_state_change_session_id"] = (
            latest_transition.get("session_id") or ""
        )
    elif "open" in {target.get("state"), source.get("state")}:
        target["state"] = "open"
    target["state_version"] = len(transitions)
    if not target.get("superseded_by_fact_id"):
        target["superseded_by_fact_id"] = source.get("superseded_by_fact_id") or ""
    target["supersedes_fact_ids"] = list(
        dict.fromkeys(
            (target.get("supersedes_fact_ids") or [])
            + (source.get("supersedes_fact_ids") or [])
        )
    )


def review_fact_match(
    project_path: str,
    candidate_id: str,
    action: str,
) -> dict[str, Any]:
    clean_action = str(action or "").strip().lower()
    if clean_action not in {"merge", "separate", "dismiss"}:
        raise ValueError("action must be merge, separate, or dismiss")

    with _FACT_LOCK:
        index = _load_index(project_path)
        candidate = next(
            (
                row
                for row in index.get("review_queue") or []
                if isinstance(row, dict) and row.get("candidate_id") == candidate_id
            ),
            None,
        )
        if not candidate:
            raise ValueError(f"fact review candidate not found: {candidate_id}")
        if candidate.get("status") == "pending" and clean_action == "merge":
            facts = [
                fact for fact in index.get("facts") or [] if isinstance(fact, dict)
            ]
            new_fact = next(
                (
                    fact
                    for fact in facts
                    if fact.get("fact_id") == candidate.get("new_fact_id")
                ),
                None,
            )
            target = next(
                (
                    fact
                    for fact in facts
                    if fact.get("fact_id")
                    == candidate.get("possible_match_fact_id")
                ),
                None,
            )
            if not new_fact or not target:
                raise ValueError("fact merge target is missing")
            _merge_fact(target, new_fact)
            index["facts"] = [
                fact
                for fact in facts
                if fact.get("fact_id") != new_fact.get("fact_id")
            ]
            pair_key = _fact_pair_key(
                str(candidate.get("new_fact_id") or ""),
                str(candidate.get("possible_match_fact_id") or ""),
            )
            for rule in index.get("separation_rules") or []:
                if isinstance(rule, dict) and rule.get("pair_key") == pair_key:
                    rule["status"] = "superseded_by_merge"
                    rule["updated_at"] = _now_iso()
        elif candidate.get("status") == "pending" and clean_action == "separate":
            left_fact_id = str(candidate.get("new_fact_id") or "")
            right_fact_id = str(candidate.get("possible_match_fact_id") or "")
            pair_key = _fact_pair_key(left_fact_id, right_fact_id)
            rules = index.setdefault("separation_rules", [])
            existing_rule = next(
                (
                    row
                    for row in rules
                    if isinstance(row, dict) and row.get("pair_key") == pair_key
                ),
                None,
            )
            if existing_rule is None:
                rules.append(
                    {
                        "rule_id": (
                            "separate_"
                            + hashlib.sha256(pair_key.encode("utf-8")).hexdigest()[:20]
                        ),
                        "pair_key": pair_key,
                        "left_fact_id": left_fact_id,
                        "right_fact_id": right_fact_id,
                        "status": "active",
                        "reason": "user_confirmed_distinct_facts",
                        "created_at": _now_iso(),
                    }
                )
            else:
                existing_rule["status"] = "active"
                existing_rule["updated_at"] = _now_iso()
        if candidate.get("status") == "pending":
            candidate["status"] = "resolved"
            candidate["resolution"] = clean_action
            candidate["resolved_at"] = _now_iso()
            _save_index(index)

    return {
        "candidate": dict(candidate),
        "index": get_project_fact_index(project_path),
    }
