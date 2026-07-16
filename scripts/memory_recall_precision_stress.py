#!/usr/bin/env python
"""Stress-test lifecycle memory recall precision under heavy noise.

This is intentionally pure memory testing: no desktop UI, no Lark, no browser,
and no external side effects. It seeds a temporary Cognitive Kernel memory home
with many noisy and near-duplicate records, then verifies that targeted queries
still retrieve the intended memories near the top.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
import uuid
from pathlib import Path
from statistics import mean


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _stamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _write(memory_type: str, content: str, *, confidence: float, evidence: list[dict], ttl: str = "permanent"):
    from l3_node.cognitive_kernel.contracts import MemoryWriteRequest
    from l3_node.cognitive_kernel.memory_lifecycle import write_lifecycle_memory

    return write_lifecycle_memory(
        MemoryWriteRequest(
            turn_id="memory-recall-precision-stress",
            source_event="memory_recall_precision_stress",
            memory_type=memory_type,
            content=content,
            confidence=confidence,
            ttl=ttl,
            evidence=evidence,
            merge_policy="dedupe_and_merge",
        )
    )


def _seed_noise(count: int, rng: random.Random) -> None:
    from l3_node.cognitive_kernel.memory_confidence import classify_memory_layer
    from l3_node.cognitive_kernel.memory_lifecycle import (
        LifecycleMemoryRecord,
        _load_records,
        _now_ms,
        _rewrite_records,
        ttl_to_expiry_ms,
    )

    topics = [
        "browser focus recovery",
        "Lark message verification",
        "Jachin project path",
        "calculator visual OCR",
        "Windows file reveal",
        "Neil contact alias",
        "memory governance conflict",
        "PMO report reminder",
        "English vocab cache",
        "desktop window switch",
    ]
    memory_types = ["tool_habit", "failure_hint", "project_fact", "historical_task_summary", "correction", "alias"]
    now = _now_ms()
    records = _load_records(include_expired=True)
    for index in range(count):
        topic = rng.choice(topics)
        memory_type = rng.choice(memory_types)
        content = (
            f"Noise memory {index}: {topic}; similar wording but not the target. "
            f"token={rng.randrange(10_000_000)} owner=stress domain=noise"
        )
        ttl = "30d" if index % 9 else "permanent"
        evidence = [
            {
                "type": "recall_noise",
                "ok": rng.random() > 0.25,
                "governance_key": f"noise:{index % 97}",
                "domain": "noise",
            }
        ]
        content_hash = hashlib.sha256(f"{memory_type}:{' '.join(content.strip().lower().split())}".encode("utf-8")).hexdigest()[:24]
        records.append(
            LifecycleMemoryRecord(
                memory_id=f"mem_noise_{uuid.uuid4().hex[:16]}",
                memory_type=memory_type,
                content=content,
                source_event="memory_recall_precision_stress",
                confidence=rng.uniform(0.42, 0.86),
                ttl=ttl,
                expires_at_ms=ttl_to_expiry_ms(ttl, memory_type),
                created_at_ms=now + index,
                updated_at_ms=now + index,
                hit_count=1,
                success_count=1 if evidence[0]["ok"] is True else 0,
                failure_count=1 if evidence[0]["ok"] is False else 0,
                last_verified_at_ms=now if evidence[0]["ok"] is True else 0,
                layer=classify_memory_layer(memory_type, ttl),
                domain="noise",
                owner="user",
                skill_id="",
                content_hash=content_hash,
                tags=[memory_type, "memory_recall_precision_stress", topic],
                evidence=evidence,
                merge_policy="bulk_seed",
            )
        )
    _rewrite_records(records)


def _seed_confusers(rng: random.Random) -> None:
    confusers = [
        ("alias", "Jachin old project path was D:/Archive/jachin-old, but this is obsolete noise.", "project:jachin:old"),
        ("alias", "Jachin docs path is D:/Projects/jachi/docs-only, not the system root.", "project:jachin:docs"),
        ("correction", "When speech says lock, do not assume Lark unless the user confirms.", "speech:lock:guard"),
        ("failure_hint", "Browser focus timeout can be ignored if the active window is already correct.", "browser:focus:weak"),
        ("failure_hint", "Lark post-send verification can fail when OCR is unavailable, but do not report sent.", "lark:send:weak"),
        ("project_fact", "Neil may appear in old test messages, but this is not a contact routing rule.", "contact:neil:noise"),
    ]
    for index, (memory_type, content, key) in enumerate(confusers):
        _write(
            memory_type,
            content + f" confuser_id={index}",
            confidence=0.74 + rng.random() * 0.08,
            evidence=[{"type": "recall_confuser", "governance_key": key, "ok": True}],
        )


def _seed_targets() -> list[dict]:
    targets = [
        {
            "id": "target_jachin_path",
            "memory_type": "alias",
            "content": "Jachin 项目路径就是 D:/Projects/jachi/jachin-system-main，用于本机主项目开发。",
            "queries": ["Jachin项目路径在哪里", "jachin system main path", "本机主项目开发路径"],
        },
        {
            "id": "target_lock_lark",
            "memory_type": "correction",
            "content": "语音或文本把 Lark 识别成 lock、lok、洛克时，优先纠错为 Lark，并打开飞书/Lark。",
            "queries": ["lock是不是lark", "lok 打开什么应用", "洛克 飞书 纠错"],
        },
        {
            "id": "target_browser_recovery",
            "memory_type": "failure_hint",
            "content": "浏览器打开后如果后台已启动但前台验证失败，先尝试 switch_existing_window，再延长 timeout 验证前台窗口。",
            "queries": ["浏览器前台验证失败怎么恢复", "browser focus failed switch existing window", "后台已启动但窗口没前台"],
        },
        {
            "id": "target_lark_send_verification",
            "memory_type": "failure_hint",
            "content": "Lark 发送消息必须有发送后截图或 OCR 证据；只有 queued 或 ok=true 但没有 post-send 证据时必须判失败。",
            "queries": ["Lark发送为什么不能只看queued", "post send verification missing", "没有发送后证据不能报成功"],
        },
        {
            "id": "target_neil_allowlist",
            "memory_type": "safety_preference",
            "content": "真实 Lark 发送压测只允许 Neil 和测试备注冒烟草稿，其他联系人必须在工具调用前拦截。",
            "queries": ["真实发送允许哪些收件人", "Neil 测试备注冒烟草稿 白名单", "Lark live confirmed allowlist"],
        },
    ]
    for target in targets:
        record = _write(
            target["memory_type"],
            target["content"],
            confidence=0.92,
            evidence=[
                {
                    "type": "recall_target",
                    "target_id": target["id"],
                    "governance_key": target["id"],
                    "ok": True,
                    "domain": "jachin",
                }
            ],
            ttl="permanent",
        )
        target["memory_id"] = record.memory_id
    return targets


def _seed_expired_decoy(targets: list[dict]) -> None:
    from l3_node.cognitive_kernel.contracts import MemoryWriteRequest
    from l3_node.cognitive_kernel.memory_lifecycle import write_lifecycle_memory

    for target in targets:
        write_lifecycle_memory(
            MemoryWriteRequest(
                turn_id="memory-recall-expired-decoy",
                source_event="memory_recall_precision_stress",
                memory_type=target["memory_type"],
                content=f"Expired decoy for {target['id']}: {target['content']}",
                confidence=0.98,
                ttl="1ms",
                evidence=[{"type": "expired_decoy", "target_id": target["id"], "ok": True}],
                merge_policy="append_action_chain",
            )
        )
    time.sleep(0.01)


def run(output_root: Path, *, noise_count: int, seed: int, top_k: int, include_governance: bool = True) -> dict:
    run_dir = output_root / _stamp()
    kernel_home = run_dir / "kernel_home"
    os.environ["JACHIN_COGNITIVE_KERNEL_HOME"] = str(kernel_home)
    run_dir.mkdir(parents=True, exist_ok=True)

    from l3_node.cognitive_kernel.memory_lifecycle import (
        expire_lifecycle_memories,
        govern_lifecycle_memories,
        memory_quality_snapshot,
        recall_lifecycle_memories,
        warm_lifecycle_memory_index,
    )

    rng = random.Random(seed)
    started_seed = time.perf_counter()
    _seed_noise(noise_count, rng)
    _seed_confusers(rng)
    targets = _seed_targets()
    _seed_expired_decoy(targets)
    expired_count = expire_lifecycle_memories()
    seed_elapsed_ms = int((time.perf_counter() - started_seed) * 1000)
    index_warmup = warm_lifecycle_memory_index()

    cases = []
    recall_times = []
    reciprocal_ranks = []
    top1 = 0
    top3 = 0
    for target in targets:
        for query in target["queries"]:
            started = time.perf_counter()
            hits = recall_lifecycle_memories(query, memory_types=[target["memory_type"]], limit=top_k)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            recall_times.append(elapsed_ms)
            hit_ids = [item.memory_id for item in hits]
            rank = (hit_ids.index(target["memory_id"]) + 1) if target["memory_id"] in hit_ids else 0
            if rank == 1:
                top1 += 1
            if 1 <= rank <= 3:
                top3 += 1
            reciprocal_ranks.append(1.0 / rank if rank else 0.0)
            cases.append(
                {
                    "target_id": target["id"],
                    "query": query,
                    "memory_type": target["memory_type"],
                    "expected_memory_id": target["memory_id"],
                    "rank": rank,
                    "elapsed_ms": elapsed_ms,
                    "hit_preview": [
                        {
                            "memory_id": item.memory_id,
                            "memory_type": item.memory_type,
                            "confidence": item.confidence,
                            "content": item.content[:180],
                        }
                        for item in hits[:5]
                    ],
                }
            )

    governance = {}
    snapshot = {}
    governance_elapsed_ms = 0
    if include_governance:
        started_governance = time.perf_counter()
        governance = govern_lifecycle_memories(stale_after_days=1)
        snapshot = memory_quality_snapshot()
        governance_elapsed_ms = int((time.perf_counter() - started_governance) * 1000)
    total_cases = len(cases)
    metrics = {
        "noise_count": noise_count,
        "target_count": len(targets),
        "case_count": total_cases,
        "top1": top1,
        "top3": top3,
        "top1_rate": round(top1 / max(1, total_cases), 4),
        "top3_rate": round(top3 / max(1, total_cases), 4),
        "mrr": round(mean(reciprocal_ranks), 4),
        "avg_recall_ms": round(mean(recall_times), 2) if recall_times else 0,
        "max_recall_ms": max(recall_times) if recall_times else 0,
        "seed_elapsed_ms": seed_elapsed_ms,
        "governance_elapsed_ms": governance_elapsed_ms,
        "index_warmup_elapsed_ms": int(index_warmup.get("elapsed_ms") or 0),
        "index_term_count": int(index_warmup.get("term_count") or 0),
        "index_posting_count": int(index_warmup.get("posting_count") or 0),
        "expired_count": expired_count,
        "include_governance": include_governance,
    }
    checks = {
        "top1_rate_at_least_0_80": metrics["top1_rate"] >= 0.80,
        "top3_rate_at_least_0_95": metrics["top3_rate"] >= 0.95,
        "mrr_at_least_0_90": metrics["mrr"] >= 0.90,
        "avg_recall_under_250ms": metrics["avg_recall_ms"] <= 250,
        "expired_decoys_filtered": expired_count >= len(targets),
    }
    evidence = {
        "schema_version": 1,
        "passed": all(checks.values()),
        "run_dir": str(run_dir),
        "kernel_home": str(kernel_home),
        "metrics": metrics,
        "checks": checks,
        "targets": targets,
        "cases": cases,
        "governance": governance,
        "snapshot": snapshot,
    }
    evidence_path = run_dir / "memory_recall_precision_stress.evidence.json"
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path = run_dir / "memory_recall_precision_stress_report.md"
    report_path.write_text(_render_report(evidence, evidence_path), encoding="utf-8")
    evidence["evidence_path"] = str(evidence_path)
    evidence["report_path"] = str(report_path)
    return evidence


def _render_report(evidence: dict, evidence_path: Path) -> str:
    metrics = evidence["metrics"]
    checks = "\n".join(f"- {name}: {'PASS' if ok else 'FAIL'}" for name, ok in evidence["checks"].items())
    failures = [case for case in evidence["cases"] if not case["rank"] or case["rank"] > 3]
    failure_lines = "\n".join(
        f"- {case['target_id']} query={case['query']!r} rank={case['rank']}"
        for case in failures[:20]
    ) or "- None"
    return f"""# Memory Recall Precision Stress Report

- Result: {"PASS" if evidence["passed"] else "FAIL"}
- Evidence: `{evidence_path}`
- Noise memories: {metrics["noise_count"]}
- Target memories: {metrics["target_count"]}
- Cases: {metrics["case_count"]}
- Top1 rate: {metrics["top1_rate"]}
- Top3 rate: {metrics["top3_rate"]}
- MRR: {metrics["mrr"]}
- Avg recall: {metrics["avg_recall_ms"]} ms
- Max recall: {metrics["max_recall_ms"]} ms
- Seed elapsed: {metrics["seed_elapsed_ms"]} ms
- Index warmup elapsed: {metrics["index_warmup_elapsed_ms"]} ms
- Index terms: {metrics["index_term_count"]}
- Index postings: {metrics["index_posting_count"]}
- Governance included: {metrics["include_governance"]}
- Governance elapsed: {metrics["governance_elapsed_ms"]} ms

## Checks
{checks}

## Failed / Weak Cases
{failure_lines}
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default=str(ROOT / "output" / "memory_recall_precision"))
    parser.add_argument("--noise-count", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=20260715)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--skip-governance", action="store_true", help="Focus on recall scale/performance without full governance scan.")
    args = parser.parse_args()
    result = run(
        Path(args.output_root),
        noise_count=args.noise_count,
        seed=args.seed,
        top_k=args.top_k,
        include_governance=not args.skip_governance,
    )
    print(json.dumps({k: result[k] for k in ("passed", "metrics", "checks", "evidence_path", "report_path")}, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
