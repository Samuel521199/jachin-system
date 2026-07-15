#!/usr/bin/env python
"""Stress-run Memory Lifecycle quality governance.

This script seeds a temporary Cognitive Kernel memory home with healthy,
low-confidence, stale, conflicting, duplicated, and corrupt records, then runs
the deterministic governance pass and writes machine-readable evidence plus a
small Markdown report.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _write_memory(memory_type: str, content: str, *, confidence: float, evidence: list[dict], ttl: str = "permanent"):
    from l3_node.cognitive_kernel.contracts import MemoryWriteRequest
    from l3_node.cognitive_kernel.memory_lifecycle import write_lifecycle_memory

    return write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="memory-quality-stress",
            source_event="memory_quality_governance_stress",
            memory_type=memory_type,
            content=content,
            confidence=confidence,
            ttl=ttl,
            evidence=evidence,
            merge_policy="dedupe_and_merge",
        )
    )


def _age_records(kernel_home: Path, memory_ids: set[str]) -> None:
    store = kernel_home / "memory" / "memory_lifecycle.jsonl"
    aged = []
    for line in store.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except Exception:
            aged.append(line)
            continue
        if obj.get("memory_id") in memory_ids:
            obj["created_at_ms"] = 1
            obj["updated_at_ms"] = 1
            obj["last_verified_at_ms"] = 1
        aged.append(json.dumps(obj, ensure_ascii=False))
    store.write_text("\n".join(aged) + "\n", encoding="utf-8")


def run(output_root: Path) -> dict:
    run_dir = output_root / _stamp()
    kernel_home = run_dir / "kernel_home"
    os.environ["JACHIN_COGNITIVE_KERNEL_HOME"] = str(kernel_home)
    run_dir.mkdir(parents=True, exist_ok=True)

    from l3_node.cognitive_kernel.memory_lifecycle import (
        govern_lifecycle_memories,
        memory_quality_snapshot,
        pending_lifecycle_review_items,
    )

    for index in range(120):
        _write_memory(
            "tool_habit",
            f"Healthy tool habit {index}: verified role executor path should stay available.",
            confidence=0.78,
            evidence=[{"type": "stress", "ok": True, "governance_key": f"healthy:{index}"}],
        )

    for index in range(20):
        _write_memory(
            "failure_hint",
            f"Low confidence failure hint {index}: needs review before future routing.",
            confidence=0.18,
            evidence=[{"type": "stress", "ok": False, "governance_key": f"low:{index}"}],
        )

    stale_ids = set()
    for index in range(12):
        record = _write_memory(
            "project_fact",
            f"Stale project fact {index}: should be revalidated by governance.",
            confidence=0.82,
            evidence=[{"type": "stress", "ok": True, "governance_key": f"stale:{index}"}],
        )
        stale_ids.add(record.memory_id)
    _age_records(kernel_home, stale_ids)

    _write_memory(
        "correction",
        "When speech says lock, resolve it as Lark.",
        confidence=0.84,
        evidence=[{"type": "stress", "governance_key": "speech:lock"}],
    )
    _write_memory(
        "correction",
        "When speech says lock, resolve it as Windows lock screen.",
        confidence=0.84,
        evidence=[{"type": "stress", "governance_key": "speech:lock"}],
    )

    duplicate = {
        "type": "stress",
        "ok": True,
        "governance_key": "duplicate:jachin:path",
    }
    for _ in range(150):
        _write_memory(
            "alias",
            "Jachin project path is D:/Projects/jachi/jachin-system-main.",
            confidence=0.76,
            evidence=[duplicate],
        )

    store = kernel_home / "memory" / "memory_lifecycle.jsonl"
    store.write_text(store.read_text(encoding="utf-8") + "{ corrupt lifecycle memory line\n", encoding="utf-8")

    started = time.perf_counter()
    governance = govern_lifecycle_memories(stale_after_days=1)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    pending = pending_lifecycle_review_items(limit=80)
    snapshot = memory_quality_snapshot()

    checks = {
        "low_confidence_detected": governance["low_confidence_count"] >= 20,
        "stale_detected": governance["stale_unverified_count"] >= 12,
        "conflict_detected": governance["conflict_count"] >= 2,
        "corrupt_line_counted": governance["invalid_raw_line_count"] == 1,
        "duplicate_deduped": governance["total_count"] == 155,
        "pending_queue_populated": len(pending) >= 34,
    }
    passed = all(checks.values())
    evidence = {
        "schema_version": 1,
        "run_dir": str(run_dir),
        "kernel_home": str(kernel_home),
        "elapsed_ms": elapsed_ms,
        "passed": passed,
        "checks": checks,
        "governance": governance,
        "pending_count": len(pending),
        "pending_preview": pending[:10],
        "snapshot": snapshot,
    }

    evidence_path = run_dir / "memory_quality_governance_stress.evidence.json"
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path = run_dir / "memory_quality_governance_stress_report.md"
    report_path.write_text(_render_report(evidence, evidence_path), encoding="utf-8")
    evidence["evidence_path"] = str(evidence_path)
    evidence["report_path"] = str(report_path)
    return evidence


def _render_report(evidence: dict, evidence_path: Path) -> str:
    checks = "\n".join(f"- {name}: {'PASS' if ok else 'FAIL'}" for name, ok in evidence["checks"].items())
    governance = evidence["governance"]
    return f"""# Memory Quality Governance Stress Report

- Result: {"PASS" if evidence["passed"] else "FAIL"}
- Elapsed: {evidence["elapsed_ms"]} ms
- Evidence: `{evidence_path}`
- Total lifecycle memories: {governance["total_count"]}
- Active memories: {governance["active_count"]}
- Pending review queue: {evidence["pending_count"]}
- Invalid raw lifecycle lines: {governance["invalid_raw_line_count"]}

## Checks
{checks}

## Governance Summary
- Low confidence: {governance["low_confidence_count"]}
- Stale unverified: {governance["stale_unverified_count"]}
- Conflicts: {governance["conflict_count"]}
- Failure pressure: {governance["failure_pressure_count"]}
- Review required: {governance["review_required_count"]}
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default=str(ROOT / "output" / "memory_quality_governance"))
    args = parser.parse_args()
    result = run(Path(args.output_root))
    print(json.dumps({k: result[k] for k in ("passed", "elapsed_ms", "evidence_path", "report_path", "checks")}, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
