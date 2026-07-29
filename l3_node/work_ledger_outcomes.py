"""Verified outcome graph and methodology promotion for Work Ledger."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any


_OUTCOME_LOCK = threading.RLock()
_METHODOLOGY_REVIEW_ACTIONS = {"approve", "reject", "reset"}


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = "\x1f".join(str(part or "").strip().lower() for part in parts)
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:20]}"


def _graph_path(project_key: str) -> Path:
    from l3_node.work_ledger import work_ledger_home

    return work_ledger_home() / "project_outcomes" / f"{project_key}.json"


def _load_saved_graph(project_key: str) -> dict[str, Any]:
    path = _graph_path(project_key)
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                data.setdefault("methodology_reviews", {})
                return data
    except (OSError, ValueError, TypeError):
        pass
    return {
        "schema_version": 1,
        "project_key": project_key,
        "methodology_reviews": {},
    }


def _save_graph(graph: dict[str, Any]) -> None:
    path = _graph_path(str(graph.get("project_key") or ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    graph["updated_at"] = _now_iso()
    temp_path = path.with_suffix(
        f"{path.suffix}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        temp_path.write_text(
            json.dumps(graph, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        for attempt in range(6):
            try:
                os.replace(temp_path, path)
                return
            except PermissionError:
                if attempt >= 5:
                    raise
                time.sleep(0.02 * (attempt + 1))
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


def _entry_node(
    fact_id: str,
    entry: dict[str, Any],
    kind: str,
) -> dict[str, Any]:
    entry_id = str(
        entry.get("entry_id")
        or _stable_id(kind, fact_id, entry.get("text"), entry.get("observed_at"))
    )
    return {
        "node_id": entry_id,
        "node_type": kind,
        "fact_id": fact_id,
        "text": str(entry.get("text") or "").strip(),
        "observed_at": str(entry.get("observed_at") or ""),
        "session_id": str(entry.get("session_id") or ""),
        "event_id": str(entry.get("event_id") or ""),
        "verification_evidence_id": str(
            entry.get("verification_evidence_id") or ""
        ),
    }


def _latest_before(
    nodes: list[dict[str, Any]],
    observed_at: str,
) -> dict[str, Any] | None:
    eligible = [
        row
        for row in nodes
        if not observed_at
        or not row.get("observed_at")
        or str(row.get("observed_at")) <= observed_at
    ]
    if not eligible:
        return None
    return sorted(eligible, key=lambda row: str(row.get("observed_at") or ""))[-1]


def _edge(
    source_id: str,
    target_id: str,
    relation: str,
    *,
    fact_id: str,
    evidence_id: str = "",
) -> dict[str, Any]:
    return {
        "edge_id": _stable_id("edge", source_id, target_id, relation),
        "source_id": source_id,
        "target_id": target_id,
        "relation": relation,
        "fact_id": fact_id,
        "verification_evidence_id": evidence_id,
    }


def _completion_outcomes(
    fact: dict[str, Any],
) -> list[dict[str, Any]]:
    fact_id = str(fact.get("fact_id") or "")
    completions = [
        row
        for row in fact.get("lifecycle") or []
        if isinstance(row, dict) and row.get("to_state") == "completed"
    ]
    outcomes: list[dict[str, Any]] = []
    for position, transition in enumerate(completions):
        transition_id = str(
            transition.get("transition_id")
            or _stable_id(
                "transition",
                fact_id,
                transition.get("session_id"),
                transition.get("observed_at"),
            )
        )
        final_state = str(fact.get("state") or "")
        is_latest_completion = position == len(completions) - 1
        status = "historical"
        if is_latest_completion and final_state == "completed":
            status = "active"
        elif is_latest_completion and final_state == "reopened":
            status = "invalidated"
        elif final_state == "superseded":
            status = "superseded"
        outcomes.append(
            {
                "outcome_id": _stable_id("outcome", fact_id, transition_id),
                "fact_id": fact_id,
                "summary": str(fact.get("canonical_summary") or ""),
                "status": status,
                "trust_level": str(fact.get("trust_level") or ""),
                "completion_session_id": str(transition.get("session_id") or ""),
                "completed_at": str(transition.get("observed_at") or ""),
                "completion_reason": str(transition.get("reason") or ""),
                "verification_evidence_id": str(
                    transition.get("verification_evidence_id") or ""
                ),
                "source_types": list(fact.get("source_types") or []),
                "occurrence_count": int(fact.get("occurrence_count") or 0),
                "superseded_by_fact_id": str(
                    fact.get("superseded_by_fact_id") or ""
                ),
            }
        )
    return outcomes


def _methodology_candidate(
    fact: dict[str, Any],
    outcomes: list[dict[str, Any]],
    review: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if str(fact.get("trust_level") or "") != "user_confirmed":
        return None
    if str(fact.get("state") or "") != "completed":
        return None
    active_outcomes = [row for row in outcomes if row.get("status") == "active"]
    failures = [
        row for row in fact.get("failure_attempts") or [] if isinstance(row, dict)
    ]
    decisions = [row for row in fact.get("decisions") or [] if isinstance(row, dict)]
    actions = [
        row for row in fact.get("next_actions") or [] if isinstance(row, dict)
    ]
    completion_count = sum(
        1
        for row in fact.get("lifecycle") or []
        if isinstance(row, dict) and row.get("to_state") == "completed"
    )
    if not active_outcomes or not failures or not decisions or not actions:
        return None
    if completion_count < 2:
        return None

    latest_failure = failures[-1]
    latest_decision = decisions[-1]
    latest_action = actions[-1]
    candidate_id = _stable_id(
        "method",
        fact.get("fact_id"),
        latest_failure.get("text"),
        latest_decision.get("text"),
        latest_action.get("text"),
    )
    review_data = review if isinstance(review, dict) else {}
    status = str(review_data.get("status") or "pending_review")
    return {
        "candidate_id": candidate_id,
        "fact_id": str(fact.get("fact_id") or ""),
        "title": f"从失败恢复并完成：{fact.get('canonical_summary')}",
        "trigger": str(latest_failure.get("text") or ""),
        "decision": str(latest_decision.get("text") or ""),
        "action": str(latest_action.get("text") or ""),
        "result": str(active_outcomes[-1].get("completion_reason") or ""),
        "status": status,
        "quality": {
            "completion_count": completion_count,
            "failure_count": len(failures),
            "decision_count": len(decisions),
            "occurrence_count": int(fact.get("occurrence_count") or 0),
            "traceable": True,
        },
        "source_fact_ids": [str(fact.get("fact_id") or "")],
        "source_outcome_ids": [
            str(row.get("outcome_id") or "") for row in active_outcomes
        ],
        "source_session_ids": list(fact.get("session_ids") or []),
        "evidence_ids": list(
            dict.fromkeys(
                str(row.get("verification_evidence_id") or "")
                for row in [latest_failure, latest_decision, latest_action]
                + active_outcomes
                if str(row.get("verification_evidence_id") or "")
            )
        ),
        "reviewed_at": str(review_data.get("reviewed_at") or ""),
        "review_note": str(review_data.get("note") or ""),
    }


def build_project_outcome_graph(project_path: str) -> dict[str, Any]:
    from l3_node.work_ledger_facts import get_project_fact_index

    fact_index = get_project_fact_index(project_path)
    project_key = str(fact_index.get("project_key") or "")
    saved = _load_saved_graph(project_key)
    reviews = (
        saved.get("methodology_reviews")
        if isinstance(saved.get("methodology_reviews"), dict)
        else {}
    )
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    methodology_candidates: list[dict[str, Any]] = []

    for fact in fact_index.get("facts") or []:
        if not isinstance(fact, dict):
            continue
        fact_id = str(fact.get("fact_id") or "")
        if not fact_id:
            continue
        nodes.append(
            {
                "node_id": fact_id,
                "node_type": "fact",
                "summary": str(fact.get("canonical_summary") or ""),
                "state": str(fact.get("state") or ""),
                "trust_level": str(fact.get("trust_level") or ""),
                "first_seen_at": str(fact.get("first_seen_at") or ""),
                "last_seen_at": str(fact.get("last_seen_at") or ""),
            }
        )
        failure_nodes = [
            _entry_node(fact_id, row, "failure")
            for row in fact.get("failure_attempts") or []
            if isinstance(row, dict)
        ]
        decision_nodes = [
            _entry_node(fact_id, row, "decision")
            for row in fact.get("decisions") or []
            if isinstance(row, dict)
        ]
        action_nodes = [
            _entry_node(fact_id, row, "next_action")
            for row in fact.get("next_actions") or []
            if isinstance(row, dict)
        ]
        nodes.extend(failure_nodes + decision_nodes + action_nodes)
        for node in failure_nodes:
            edges.append(
                _edge(
                    fact_id,
                    str(node.get("node_id") or ""),
                    "has_failure",
                    fact_id=fact_id,
                    evidence_id=str(node.get("verification_evidence_id") or ""),
                )
            )
        for node in decision_nodes:
            source = _latest_before(failure_nodes, str(node.get("observed_at") or ""))
            edges.append(
                _edge(
                    str(source.get("node_id") if source else fact_id),
                    str(node.get("node_id") or ""),
                    "informed_decision",
                    fact_id=fact_id,
                    evidence_id=str(node.get("verification_evidence_id") or ""),
                )
            )
        for node in action_nodes:
            source = _latest_before(decision_nodes, str(node.get("observed_at") or ""))
            edges.append(
                _edge(
                    str(source.get("node_id") if source else fact_id),
                    str(node.get("node_id") or ""),
                    "drives_action",
                    fact_id=fact_id,
                    evidence_id=str(node.get("verification_evidence_id") or ""),
                )
            )

        fact_outcomes = _completion_outcomes(fact)
        outcomes.extend(fact_outcomes)
        for outcome in fact_outcomes:
            outcome_node = {
                "node_id": outcome["outcome_id"],
                "node_type": "outcome",
                **outcome,
            }
            nodes.append(outcome_node)
            edges.append(
                _edge(
                    fact_id,
                    outcome["outcome_id"],
                    "verified_as",
                    fact_id=fact_id,
                    evidence_id=str(
                        outcome.get("verification_evidence_id") or ""
                    ),
                )
            )
            source = _latest_before(
                action_nodes,
                str(outcome.get("completed_at") or ""),
            )
            if source:
                edges.append(
                    _edge(
                        str(source.get("node_id") or ""),
                        outcome["outcome_id"],
                        "produced_outcome",
                        fact_id=fact_id,
                        evidence_id=str(
                            outcome.get("verification_evidence_id") or ""
                        ),
                    )
                )
        for predecessor_id in fact.get("supersedes_fact_ids") or []:
            edges.append(
                _edge(
                    str(predecessor_id),
                    fact_id,
                    "superseded_by",
                    fact_id=fact_id,
                )
            )

        candidate_without_review = _methodology_candidate(
            fact,
            fact_outcomes,
            None,
        )
        if candidate_without_review:
            candidate_id = candidate_without_review["candidate_id"]
            candidate = _methodology_candidate(
                fact,
                fact_outcomes,
                reviews.get(candidate_id),
            )
            if candidate:
                methodology_candidates.append(candidate)
                nodes.append(
                    {
                        "node_id": candidate_id,
                        "node_type": "methodology",
                        "title": candidate["title"],
                        "status": candidate["status"],
                        "fact_id": fact_id,
                    }
                )
                edges.extend(
                    _edge(
                        active_outcome["outcome_id"],
                        candidate_id,
                        "suggests_methodology",
                        fact_id=fact_id,
                        evidence_id=str(
                            active_outcome.get("verification_evidence_id") or ""
                        ),
                    )
                    for active_outcome in fact_outcomes
                    if active_outcome.get("status") == "active"
                )

    graph = {
        "schema_version": 1,
        "project_key": project_key,
        "project_path": str(fact_index.get("project_path") or project_path),
        "generated_at": _now_iso(),
        "nodes": nodes,
        "edges": edges,
        "outcomes": outcomes,
        "methodology_candidates": methodology_candidates,
        "methodology_reviews": reviews,
        "summary": {
            "fact_count": len(fact_index.get("facts") or []),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "active_outcome_count": sum(
                1 for row in outcomes if row.get("status") == "active"
            ),
            "historical_outcome_count": sum(
                1 for row in outcomes if row.get("status") != "active"
            ),
            "methodology_pending_count": sum(
                1
                for row in methodology_candidates
                if row.get("status") == "pending_review"
            ),
            "methodology_approved_count": sum(
                1
                for row in methodology_candidates
                if row.get("status") == "approved"
            ),
        },
    }
    _save_graph(graph)
    return graph


def get_project_outcome_graph(project_path: str) -> dict[str, Any]:
    with _OUTCOME_LOCK:
        return build_project_outcome_graph(project_path)


def get_session_outcome_context(session_id: str) -> dict[str, Any]:
    from l3_node.work_ledger import get_session_detail

    session = get_session_detail(session_id, evidence_limit=5)["session"]
    graph = get_project_outcome_graph(str(session.get("project_path") or ""))
    active_outcomes = [
        row for row in graph.get("outcomes") or [] if row.get("status") == "active"
    ]
    return {
        "graph": graph,
        "active_outcomes": active_outcomes,
        "outcomes_this_session": [
            row
            for row in active_outcomes
            if row.get("completion_session_id") == session_id
        ],
        "methodology_pending": [
            row
            for row in graph.get("methodology_candidates") or []
            if row.get("status") == "pending_review"
            and session_id in (row.get("source_session_ids") or [])
        ],
        "methodology_approved": [
            row
            for row in graph.get("methodology_candidates") or []
            if row.get("status") == "approved"
        ],
        "summary": graph.get("summary") or {},
    }


def review_methodology_candidate(
    project_path: str,
    candidate_id: str,
    action: str,
    *,
    note: str = "",
) -> dict[str, Any]:
    clean_action = str(action or "").strip().lower()
    if clean_action not in _METHODOLOGY_REVIEW_ACTIONS:
        raise ValueError("action must be approve, reject, or reset")
    clean_candidate_id = str(candidate_id or "").strip()
    if not clean_candidate_id:
        raise ValueError("candidate_id is required")

    with _OUTCOME_LOCK:
        graph = build_project_outcome_graph(project_path)
        candidate = next(
            (
                row
                for row in graph.get("methodology_candidates") or []
                if row.get("candidate_id") == clean_candidate_id
            ),
            None,
        )
        if not candidate:
            raise ValueError(
                f"methodology candidate not found: {clean_candidate_id}"
            )
        reviews = graph.setdefault("methodology_reviews", {})
        if clean_action == "reset":
            reviews.pop(clean_candidate_id, None)
        else:
            reviews[clean_candidate_id] = {
                "status": "approved" if clean_action == "approve" else "rejected",
                "reviewed_at": _now_iso(),
                "note": str(note or "").strip(),
            }
        _save_graph(graph)
        refreshed = build_project_outcome_graph(project_path)
        reviewed_candidate = next(
            row
            for row in refreshed.get("methodology_candidates") or []
            if row.get("candidate_id") == clean_candidate_id
        )
        return {
            "candidate": reviewed_candidate,
            "graph": refreshed,
        }
