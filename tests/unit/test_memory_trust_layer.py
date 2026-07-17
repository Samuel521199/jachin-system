from __future__ import annotations

from l3_node.cognitive_kernel.contracts import MemoryEvidence, MemoryWriteRequest
from l3_node.cognitive_kernel.memory_lifecycle import LifecycleMemoryRecord, govern_lifecycle_memory, write_lifecycle_memory
from l3_node.cognitive_kernel.memory_recall_agent import _apply_recall_trust
from l3_node.cognitive_kernel.memory_trust import (
    TRUST_CONFIRMED,
    TRUST_CONFLICTED,
    TRUST_FLOATING,
    TRUST_REJECTED,
    decorate_memory_evidence,
    infer_memory_trust,
    lifecycle_record_trust_defaults,
    should_recall_memory,
    trust_weight,
)


def test_confirmed_memory_is_decorated_and_boosted() -> None:
    item = MemoryEvidence(
        memory_id="mem_confirmed",
        memory_type="alias",
        content="Neil messages should use Lark.",
        source="test",
        confidence=0.5,
        confirmed_by_user=True,
    )

    decorated = decorate_memory_evidence(item)

    assert decorated.trust_state == TRUST_CONFIRMED
    assert decorated.confirmed_by_user is True
    assert decorated.confidence > item.confidence
    assert "state=confirmed" in decorated.relevance_reason


def test_rejected_memory_is_filtered_before_recall_candidates() -> None:
    rejected = MemoryEvidence(
        memory_id="mem_rejected",
        memory_type="alias",
        content="wrong alias",
        source="test",
        confidence=0.9,
        trust_state=TRUST_REJECTED,
    )
    floating = MemoryEvidence(
        memory_id="mem_floating",
        memory_type="alias",
        content="floating alias",
        source="test",
        confidence=0.5,
    )

    trusted, ranking, conflicts = _apply_recall_trust([rejected, floating])

    assert [item.memory_id for item in trusted] == ["mem_floating"]
    assert any(row["memory_id"] == "mem_rejected" and row["filtered"] for row in ranking)
    assert conflicts == []


def test_conflicted_memory_survives_but_requires_confirmation() -> None:
    item = MemoryEvidence(
        memory_id="mem_conflict",
        memory_type="correction",
        content="conflicting app alias",
        source="test",
        confidence=0.8,
        trust_state=TRUST_CONFLICTED,
        trust_reason="test_conflict",
    )

    trusted, ranking, conflicts = _apply_recall_trust([item])

    assert trusted[0].trust_state == TRUST_CONFLICTED
    assert ranking[0]["memory_trust_weight"] == trust_weight(TRUST_CONFLICTED)
    assert conflicts[0]["type"] == "memory_trust_requires_confirmation"


def test_memory_write_request_infers_rejected_trust_from_source_event() -> None:
    request = MemoryWriteRequest(
        turn_id="turn_1",
        source_event="user_rejected_alias",
        memory_type="alias",
        content="Lock means Lark.",
        confidence=0.7,
    )

    defaults = lifecycle_record_trust_defaults(request)

    assert defaults["trust_state"] == TRUST_REJECTED
    assert defaults["recall_allowed"] is False


def test_lifecycle_record_to_evidence_preserves_trust_metadata() -> None:
    record = LifecycleMemoryRecord(
        memory_id="mem_record",
        memory_type="tool_habit",
        content="Use Lark for Neil.",
        confidence=0.7,
        trust_state=TRUST_FLOATING,
        trust_reason="system_inferred",
        user_attitude=TRUST_FLOATING,
    )

    evidence = record.to_evidence("unit recall")

    assert infer_memory_trust(evidence)[0] == TRUST_FLOATING
    assert should_recall_memory(evidence) is True
    assert evidence.trust_reason


def test_rejected_similar_memory_forces_future_write_confirmation(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    rejected = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="trust-prior-reject-1",
            source_event="system_inferred_alias",
            memory_type="alias",
            content="Lock means Lark.",
            confidence=0.8,
            evidence=[{"alias_key": "voice:lock"}],
        )
    )
    govern_lifecycle_memory(memory_id=rejected.memory_id, action="reject", note="wrong voice alias")

    next_record = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="trust-prior-reject-2",
            source_event="system_inferred_alias",
            memory_type="alias",
            content="Lock should open Lark.",
            confidence=0.8,
            evidence=[{"alias_key": "voice:lock"}],
        )
    )

    assert next_record.trust_state == TRUST_CONFLICTED
    assert next_record.review_required is True
    assert next_record.review_reason == "similar_memory_trust_governance_requires_confirmation"
    assert next_record.confidence < 0.8
    assert any(item.get("type") == "memory_trust_prior" for item in next_record.evidence)


def test_confirmed_similar_memory_boosts_future_write(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("JACHIN_COGNITIVE_KERNEL_HOME", str(tmp_path / "kernel"))

    confirmed = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="trust-prior-confirm-1",
            source_event="system_inferred_alias",
            memory_type="alias",
            content="Neil messages should use Lark.",
            confidence=0.55,
            evidence=[{"alias_key": "recipient:neil:lark"}],
        )
    )
    govern_lifecycle_memory(memory_id=confirmed.memory_id, action="confirm", note="Neil uses Lark")

    next_record = write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="trust-prior-confirm-2",
            source_event="system_inferred_alias",
            memory_type="alias",
            content="Messages to Neil go through Lark.",
            confidence=0.55,
            evidence=[{"alias_key": "recipient:neil:lark"}],
        )
    )

    assert next_record.trust_state == TRUST_CONFIRMED
    assert next_record.trust_reason == "trust_prior:similar_memory_confirmed_by_user"
    assert next_record.confidence > 0.55
    assert next_record.recall_allowed is True
