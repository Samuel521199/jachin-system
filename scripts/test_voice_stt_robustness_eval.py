#!/usr/bin/env python3
"""Regression eval for VOICE_STT_ROBUSTNESS_PROPOSAL core capabilities (T0 text chain).

Validates the implemented MVP path without live microphone / STT server:

  STT noise text -> entity correction -> slot parse -> intent route -> plan preview

Covers document sections:
  - SS7.0 MVP (Lexicon, luck/viian correction, message body read-only, slot gates,
    send confirmation, no FileNotFoundError passthrough)
  - SS4.2.0 pre-clean, SS4.2 rules, SS8.1/8.2 acceptance metrics (T0 tier)

Usage (repo root)::

  python scripts/test_voice_stt_robustness_eval.py
  python scripts/test_voice_stt_robustness_eval.py --mvp-only
  python scripts/test_voice_stt_robustness_eval.py --cases data/eval/t0_text_cases.jsonl
  python scripts/test_voice_stt_robustness_eval.py --json-out reports/stt_robustness_t0.json
  python scripts/test_voice_stt_robustness_eval.py --with-pytest

Sample set: ``data/eval/t0_text_cases.jsonl`` (extend-only; do not delete existing rows).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = ROOT / "data" / "eval" / "t0_text_cases.jsonl"

DEFAULT_TOOLS: list[dict[str, str]] = [
    {"id": "mcp:windows_open_app"},
    {"id": "mcp:windows_lark_send_message"},
    {"id": "mcp:windows_calculator_calculate"},
    {"id": "mcp:windows_codex_lark_workflow_template"},
    {"id": "mcp:windows_codex_ask_lark_send"},
]


@dataclass
class LayerSnapshot:
    stt_raw: str = ""
    cleaned_text: str = ""
    corrected_text: str = ""
    corrections: list[dict[str, Any]] = field(default_factory=list)
    suspect_tokens: list[dict[str, Any]] = field(default_factory=list)
    secondary_should_run: bool = False
    secondary_risk_level: str = "low"
    secondary_reasons: list[str] = field(default_factory=list)
    secondary_provider: str = "none"
    task_type: str = ""
    missing_slots: list[str] = field(default_factory=list)
    recipients: list[str] = field(default_factory=list)
    message: str = ""
    app_name: str = ""
    route_ok: bool = False
    route_tool_id: str = ""
    route_reason: str = ""
    requires_confirmation: bool = False
    auto_execute: bool = True
    hidca_domain: str = ""
    failure_class: str = ""
    friendly_detail: str = ""


@dataclass
class CaseResult:
    case_id: str
    tier: str
    category: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    layers: LayerSnapshot = field(default_factory=LayerSnapshot)


def _load_cases(path: Path, *, mvp_only: bool) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"case file not found: {path}")
    cases: list[dict[str, Any]] = []
    text = path.read_text(encoding="utf-8-sig")
    for line_no, line in enumerate(text.splitlines(), start=1):
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
        if not isinstance(item, dict) or not item.get("id"):
            raise ValueError(f"{path}:{line_no}: each case must be an object with 'id'")
        if mvp_only and str(item.get("category") or "") != "mvp":
            continue
        cases.append(item)
    if not cases:
        raise ValueError(f"no cases loaded from {path}" + (" (mvp-only filter)" if mvp_only else ""))
    return cases


def _norm_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(v).strip() for v in values if str(v).strip()]


def _correction_tuples(corrections: list[Any]) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    for item in corrections:
        out.append((str(item.kind), str(item.original), str(item.canonical)))
    return out


def _run_pipeline(user_input: str, tools: list[dict[str, str]]) -> LayerSnapshot:
    from l3_node.capability_router import choose_capability_route
    from l3_node.intent_orchestrator import analyze_intent
    from l3_node.mission_control_center import should_hold_for_confirmation
    from l3_node.mission_intent_schema import MissionIntent, MissionSlots, MissionTaskType
    from l3_node.mission_runtime import build_plan_preview, classify_failure
    from l3_node.mission_user_feedback import _friendly_detail
    from l3_node.semantic_slot_parser import parse_mission_intent
    from l3_node.voice_entity_correction import correct_voice_entities
    from l3_node.voice_risk_gate import decide_secondary_recognition

    snap = LayerSnapshot(stt_raw=str(user_input or ""))
    correction = correct_voice_entities(snap.stt_raw)
    snap.cleaned_text = correction.cleaned_text
    snap.corrected_text = correction.corrected_text
    snap.corrections = [
        {"kind": c.kind, "original": c.original, "canonical": c.canonical, "reason": c.reason}
        for c in correction.corrections
    ]
    snap.suspect_tokens = [
        {
            "token": s.token,
            "kind": s.kind,
            "candidates": list(s.candidates),
            "reason": s.reason,
            "confidence": s.confidence,
        }
        for s in correction.suspect_tokens
    ]

    intent = parse_mission_intent(snap.stt_raw)
    snap.task_type = intent.task_type.value
    snap.missing_slots = list(intent.missing_slots)
    snap.recipients = list(intent.slots.recipients)
    snap.message = str(intent.slots.message or "")
    snap.app_name = str(intent.slots.app_name or "")

    decision = analyze_intent(snap.stt_raw, tools=tools)
    route = choose_capability_route(intent, tools)
    plan = build_plan_preview(intent, route)

    snap.route_ok = bool(route.ok)
    snap.route_tool_id = str(route.tool_id or "")
    snap.route_reason = str(route.reason or "")
    snap.requires_confirmation = bool(plan.requires_confirmation)
    snap.auto_execute = bool(plan.auto_execute)
    snap.hidca_domain = str((decision.hidca or {}).get("semantic_router_domain") or "")

    risk = decide_secondary_recognition(
        text=snap.corrected_text,
        confidence=intent.confidence,
        suspect_tokens=snap.suspect_tokens,
        intent_task_type=snap.task_type,
    )
    snap.secondary_should_run = risk.should_run
    snap.secondary_risk_level = risk.risk_level
    snap.secondary_reasons = list(risk.reasons)
    snap.secondary_provider = risk.preferred_provider

    # FileNotFoundError must map to human-friendly detail, not raw exception text.
    filenotfound_intent = MissionIntent(
        task_type=MissionTaskType.APP_CONTROL,
        confidence=0.8,
        slots=MissionSlots(app_name="luck"),
        raw_text="open luck",
    )
    filenotfound_route = choose_capability_route(
        filenotfound_intent,
        [{"id": "mcp:windows_open_app"}],
    )
    raw_err = {
        "ok": False,
        "detail": "failed:FileNotFoundError(2, '系统找不到指定的文件。', None, 2, None)",
    }
    snap.failure_class = classify_failure(filenotfound_intent, filenotfound_route, raw_err)
    snap.friendly_detail = _friendly_detail(snap.failure_class)

    # Confirmation gate for this utterance (default policy = high_risk_only).
    snap.requires_confirmation = snap.requires_confirmation or should_hold_for_confirmation(intent, plan)
    return snap


def _assert_case(case: dict[str, Any], snap: LayerSnapshot) -> list[str]:
    failures: list[str] = []

    expected_corrections = case.get("expected_corrections")
    if expected_corrections is not None:
        got = [(c["kind"], c["original"], c["canonical"]) for c in snap.corrections]
        want = [(str(x["kind"]), str(x["original"]), str(x["canonical"])) for x in expected_corrections]
        if got != want:
            failures.append(f"correction: want {want}, got {got}")

    if "expected_task_type" in case and snap.task_type != case["expected_task_type"]:
        failures.append(f"task_type: want {case['expected_task_type']}, got {snap.task_type}")

    if "expected_recipients" in case:
        want = _norm_list(case["expected_recipients"])
        if [r.lower() for r in snap.recipients] != [r.lower() for r in want]:
            failures.append(f"recipients: want {want}, got {snap.recipients}")

    if "expected_message_contains" in case:
        needle = str(case["expected_message_contains"])
        if needle not in snap.message:
            failures.append(f"message missing {needle!r}: got {snap.message!r}")

    if "message_body_must_not_contain" in case:
        banned = str(case["message_body_must_not_contain"])
        if banned in snap.message:
            failures.append(f"message body illegally contains {banned!r}: {snap.message!r}")

    if "expected_app_name" in case and snap.app_name.lower() != str(case["expected_app_name"]).lower():
        failures.append(f"app_name: want {case['expected_app_name']}, got {snap.app_name}")

    if "must_have_missing_slots" in case:
        want = sorted(_norm_list(case["must_have_missing_slots"]))
        got = sorted(snap.missing_slots)
        for slot in want:
            if slot not in got:
                failures.append(f"missing_slots: expected to include {slot!r}, got {got}")

    if case.get("route_must_be_blocked") and snap.route_ok:
        failures.append(f"route should be blocked, but ok=True tool={snap.route_tool_id}")

    if "expected_tool_id" in case and snap.route_tool_id != case["expected_tool_id"]:
        failures.append(f"route tool: want {case['expected_tool_id']}, got {snap.route_tool_id}")

    for forbidden in _norm_list(case.get("forbidden_tool_ids")):
        if snap.route_ok and snap.route_tool_id == forbidden:
            failures.append(f"forbidden route selected: {forbidden}")

    if case.get("requires_confirmation") is True:
        if not snap.requires_confirmation:
            failures.append("expected requires_confirmation=True")

    if "expected_hidca_domain" in case and snap.hidca_domain != case["expected_hidca_domain"]:
        failures.append(f"hidca domain: want {case['expected_hidca_domain']}, got {snap.hidca_domain}")

    if "expected_secondary_should_run" in case and snap.secondary_should_run != bool(case["expected_secondary_should_run"]):
        failures.append(
            f"secondary recognition: want {case['expected_secondary_should_run']}, got {snap.secondary_should_run}"
        )

    if "expected_secondary_reasons" in case:
        for reason in _norm_list(case["expected_secondary_reasons"]):
            if reason not in snap.secondary_reasons:
                failures.append(f"secondary reasons should include {reason!r}, got {snap.secondary_reasons}")

    return failures


def _run_static_contract_checks() -> list[str]:
    """Cross-cutting MVP contracts not tied to a single utterance."""
    from l3_node.capability_router import choose_capability_route
    from l3_node.mission_intent_schema import MissionIntent, MissionSlots, MissionTaskType
    from l3_node.mission_runtime import classify_failure
    from l3_node.mission_user_feedback import _friendly_detail
    from l3_node.semantic_slot_parser import parse_mission_intent
    from l3_node.voice_entity_correction import export_hotwords
    from l3_node.voice_risk_gate import decide_secondary_recognition

    failures: list[str] = []
    tools = DEFAULT_TOOLS

    hotwords = export_hotwords()
    for key in ("Lark", "Vivian", "Jachin"):
        if key not in hotwords:
            failures.append(f"hotwords missing canonical {key!r}")

    intent = MissionIntent(
        task_type=MissionTaskType.APP_CONTROL,
        confidence=0.8,
        slots=MissionSlots(app_name="luck"),
        raw_text="open luck",
    )
    route = choose_capability_route(intent, [{"id": "mcp:windows_open_app"}])
    raw_err = {"ok": False, "detail": "failed:FileNotFoundError(2, '系统找不到指定的文件。', None, 2, None)"}
    failure_class = classify_failure(intent, route, raw_err)
    friendly = _friendly_detail(failure_class)
    if failure_class != "app_executable_not_found":
        failures.append(f"FileNotFoundError should classify as app_executable_not_found, got {failure_class!r}")
    if "FileNotFoundError" in friendly or "系统找不到指定的文件" in friendly:
        failures.append(f"FileNotFoundError should not passthrough raw text: {friendly!r}")
    if "没有找到" not in friendly and "启动程序" not in friendly:
        failures.append(f"FileNotFoundError friendly message too terse: {friendly!r}")

    missing_recipient = MissionIntent(
        task_type=MissionTaskType.LARK_MESSAGE_SEND,
        confidence=0.8,
        slots=MissionSlots(recipients=[], message="hello"),
        missing_slots=["recipients"],
        raw_text="send hello",
    )
    blocked = choose_capability_route(missing_recipient, tools)
    if blocked.ok or blocked.reason != "lark_send_missing_recipient":
        failures.append(
            f"empty recipients must block lark send route (ok={blocked.ok}, reason={blocked.reason!r})"
        )

    missing_message = MissionIntent(
        task_type=MissionTaskType.LARK_MESSAGE_SEND,
        confidence=0.8,
        slots=MissionSlots(recipients=["Vivian"], message=""),
        missing_slots=["message"],
        raw_text="send to vivian",
    )
    blocked_msg = choose_capability_route(missing_message, tools)
    if blocked_msg.ok or blocked_msg.reason != "lark_send_missing_message":
        failures.append(
            f"empty message must block lark send route (ok={blocked_msg.ok}, reason={blocked_msg.reason!r})"
        )

    negated = parse_mission_intent("\u4e0d\u8981\u7ed9 Vivian \u53d1\u6d88\u606f \u5185\u5bb9\u662f \u660e\u5929\u518d\u8bf4")
    if negated.task_type != MissionTaskType.UNKNOWN:
        failures.append(f"Chinese negated send must downgrade to unknown, got {negated.task_type.value}")

    secondary = decide_secondary_recognition(
        text="\u7ed9 v \u8587 m \u53d1\u9001\u6d88\u606f \u5185\u5bb9\u662f \u5220\u9664\u6587\u4ef6",
        confidence=0.50,
        suspect_tokens=[{"token": "v \u8587 m"}],
        intent_task_type="lark_message_send",
    )
    if not secondary.should_run:
        failures.append("high-risk low-confidence voice command must trigger secondary recognition decision")

    return failures


def _evaluate_cases(cases: list[dict[str, Any]], tools: list[dict[str, str]]) -> tuple[list[CaseResult], list[str]]:
    results: list[CaseResult] = []
    static_failures = _run_static_contract_checks()

    for case in cases:
        snap = _run_pipeline(str(case.get("input") or ""), tools)
        failures = _assert_case(case, snap)
        results.append(
            CaseResult(
                case_id=str(case["id"]),
                tier=str(case.get("tier") or "T0"),
                category=str(case.get("category") or "mvp"),
                passed=not failures,
                failures=failures,
                layers=snap,
            )
        )
    return results, static_failures


def _print_report(results: list[CaseResult], static_failures: list[str]) -> None:
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print("=" * 72)
    print("VOICE STT Robustness Eval (T0 text chain)")
    print("=" * 72)
    print(f"Cases: {passed}/{total} passed")
    if static_failures:
        print(f"Static contract checks: FAILED ({len(static_failures)})")
    else:
        print("Static contract checks: OK")
    print()

    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"[{status}] {result.case_id} ({result.category})")
        if not result.passed:
            for msg in result.failures:
                print(f"       - {msg}")
            print(f"       raw -> corrected: {result.layers.stt_raw!r} -> {result.layers.corrected_text!r}")
            print(
                f"       route: ok={result.layers.route_ok} tool={result.layers.route_tool_id!r} "
                f"reason={result.layers.route_reason!r}"
            )

    if static_failures:
        print()
        print("Static contract failures:")
        for msg in static_failures:
            print(f"  - {msg}")

    print()
    if passed == total and not static_failures:
        print("All T0 checks passed.")
    else:
        print("Some checks failed.")


def _write_json(path: Path, results: list[CaseResult], static_failures: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": {
            "passed": sum(1 for r in results if r.passed),
            "total": len(results),
            "static_ok": not static_failures,
        },
        "static_failures": static_failures,
        "cases": [
            {
                "case_id": r.case_id,
                "tier": r.tier,
                "category": r.category,
                "passed": r.passed,
                "failures": r.failures,
                "layers": asdict(r.layers),
            }
            for r in results
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote report: {path}")


def _run_pytest() -> int:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-o",
        "addopts=",
        "tests/unit/test_voice_entity_correction.py",
        "tests/unit/test_mission_runtime.py",
        "-q",
    ]
    print("Running unit tests:", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(ROOT))
    return int(proc.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Eval VOICE_STT_ROBUSTNESS_PROPOSAL core (T0 text chain)")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES, help="JSONL case file")
    parser.add_argument("--mvp-only", action="store_true", help="Only run cases tagged category=mvp")
    parser.add_argument("--json-out", type=Path, default=None, help="Write structured JSON report")
    parser.add_argument("--with-pytest", action="store_true", help="Also run related unit tests")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))

    try:
        cases = _load_cases(args.cases, mvp_only=args.mvp_only)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    results, static_failures = _evaluate_cases(cases, DEFAULT_TOOLS)
    _print_report(results, static_failures)
    if args.json_out:
        _write_json(args.json_out, results, static_failures)

    exit_code = 0 if all(r.passed for r in results) and not static_failures else 1
    if args.with_pytest:
        pytest_code = _run_pytest()
        exit_code = max(exit_code, pytest_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
