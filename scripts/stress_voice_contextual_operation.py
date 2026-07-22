from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RUN_TS = time.strftime("%Y%m%d_%H%M%S")
OUT_DIR = ROOT / "output" / "voice_contextual_stress"
RUN_DIR = OUT_DIR / RUN_TS
JSONL_PATH = RUN_DIR / "voice_contextual_operation_stress.jsonl"
REPORT_PATH = ROOT / "docs" / "21_voice_contextual_operation_stress_report.md"

os.environ["JACHIN_COGNITIVE_KERNEL_HOME"] = str(RUN_DIR / "kernel_home")
os.environ["JACHIN_DISABLE_ROLE_NATIVE_ADAPTERS"] = "1"
os.environ["JACHIN_DISABLE_TURN_CLOSURE_MEMORY_WRITE"] = "1"


ZH_OPEN_WECHAT_NOISE = "\u6253\u5f00\u5fae\u4fe1\uff0c\u884c\uff0c\u5bf9\uff0c\u5c31\u770b\u90a3\u4e2a\u3002"
ZH_OPEN_WECHAT = "\u6253\u5f00\u5fae\u4fe1\u3002"
ZH_OPEN_LARK = "\u6253\u5f00 Lark\u3002"
ZH_OPEN_LARK_LOCK = "\u6253\u5f00 lock"
ZH_SEND_HELLO = "\u53d1\u9001\u6d88\u606f\uff0c\u4f60\u597d\u3002"
ZH_SEND_HELLO_NOISE = "\u53d1\u9001\u6d88\u606f\uff0c\u4f60\u597d\u3002\u884c\uff0c\u5bf9\uff0c\u5c31\u770b\u90a3\u4e2a\u3002"
ZH_BACKGROUND_NOISE = "\u884c\uff0c\u5bf9\uff0c\u5c31\u770b\u90a3\u4e2a\u3002"
ZH_SEND_TO_NEIL = "\u53d1\u7ed9 Neil"
ZH_CANCEL = "\u53d6\u6d88"


@dataclass(slots=True)
class ScenarioResult:
    test_id: str
    category: str
    input_text: str
    expected: str
    actual: str
    status: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "category": self.category,
            "input_text": self.input_text,
            "expected": self.expected,
            "actual": self.actual,
            "status": self.status,
            "details": self.details,
        }


def _voice_ctx(
    text: str,
    *,
    confidence: float = 0.9,
    owner: bool | None = True,
    mode: str = "continuous_listen",
    session_id: str = "voice-contextual-stress",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "source": "desktop_voice_companion",
        "local_voice_session": True,
        "voice_interaction_mode": mode,
        "voice_raw_stt_text": text,
        "voice_asr_raw_text": text,
        "voice_final_text": text,
        "voice_stt_confidence": confidence,
        "voice_stt_finalized": True,
        "session_id": session_id,
        "channel": "websocket_terminal",
    }
    if owner is True:
        ctx["voice_speaker_verified"] = True
        ctx["voice_owner_track_accepted"] = True
        ctx["voice_owner_duration_ms"] = 900
        ctx["voice_total_duration_ms"] = 1000
    elif owner is False:
        ctx["voice_speaker_verified"] = False
        ctx["voice_speaker_rejected"] = True
    if extra:
        ctx.update(extra)
    return ctx


async def _build_plan(
    text: str,
    *,
    run_id: str,
    session_id: str,
    voice_context: dict[str, Any] | None = None,
):
    from l3_node.cognitive_kernel.kernel_loop import plan_cognitive_turn
    from l3_node.cognitive_kernel.pipeline import build_cognitive_turn_context

    ctx = await build_cognitive_turn_context(
        run_id=run_id,
        user_input=text,
        channel="websocket_terminal",
        session_id=session_id,
        prior_messages=[],
        desktop_companion_context=voice_context or {},
    )
    return ctx, plan_cognitive_turn(ctx, emit_non_execution_closure=False)


async def _execute_plan(
    *,
    text: str,
    run_id: str,
    session_id: str,
    voice_context: dict[str, Any] | None,
    fake_tool: Callable[[str, str, list[str] | None], str],
    tools: list[dict[str, Any]] | None = None,
) -> tuple[Any, Any, str | None]:
    from l3_node.cognitive_kernel.direct_mainline import try_execute_cognitive_direct_plan

    ctx, plan = await _build_plan(text, run_id=run_id, session_id=session_id, voice_context=voice_context)
    reply = await try_execute_cognitive_direct_plan(
        plan=plan,
        tools=tools
        or [
            {"id": "mcp:windows_open_app"},
            {"id": "mcp:windows_lark_send_message"},
            {"id": "mcp:windows_calculator_calculate"},
        ],
        allowed_skills=None,
        run_tool_func=fake_tool,
        user_input=ctx.envelope.normalized_text,
        session_id=session_id,
        channel="websocket_terminal",
    )
    return ctx, plan, reply


def _make_fake_tool(calls: list[dict[str, Any]]) -> Callable[[str, str, list[str] | None], str]:
    def fake_tool(tool_id: str, work_order_input: str, allowed_skills: list[str] | None = None) -> str:
        payload: dict[str, Any]
        try:
            payload = json.loads(work_order_input or "{}")
            if not isinstance(payload, dict):
                payload = {"raw": work_order_input}
        except Exception:
            payload = {"raw": work_order_input}
        calls.append({"tool": tool_id, "payload": payload})
        if tool_id == "mcp:windows_open_app":
            app = str(payload.get("app") or payload.get("app_name") or payload.get("name") or "")
            return json.dumps(
                {
                    "ok": True,
                    "task": "open_app",
                    "app": app,
                    "active_window": app,
                    "detail": "offline_window_verified",
                    "screenshot": str(RUN_DIR / f"{app or 'app'}_open.png"),
                    "ocr_text": app,
                },
                ensure_ascii=False,
            )
        if tool_id == "mcp:windows_lark_send_message":
            recipients = _decode_recipients(payload.get("recipients_json"))
            message = str(payload.get("message") or "")
            return json.dumps(
                {
                    "ok": bool(recipients and message),
                    "send_ok": bool(recipients and message),
                    "task": "lark_send_message",
                    "recipient": recipients[0] if recipients else "",
                    "recipients": recipients,
                    "message": message,
                    "message_id": f"offline-{len(calls)}",
                    "screenshot": str(RUN_DIR / "lark_send_verified.png"),
                    "ocr_text": f"{','.join(recipients)} {message}",
                    "post_send_verified": bool(recipients and message),
                },
                ensure_ascii=False,
            )
        if tool_id == "mcp:windows_calculator_calculate":
            expr = str(payload.get("expression") or payload.get("expr") or "")
            return json.dumps(
                {
                    "ok": True,
                    "task": "calculator",
                    "expression": expr,
                    "result": "199",
                    "screenshot": str(RUN_DIR / "calculator_verified.png"),
                    "ocr_text": "199",
                },
                ensure_ascii=False,
            )
        return json.dumps({"ok": True, "tool": tool_id, "echo": payload}, ensure_ascii=False)

    return fake_tool


def _decode_recipients(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(x) for x in parsed if str(x).strip()]
        except Exception:
            if value.strip():
                return [value.strip()]
    return []


def _guard(ctx: Any) -> dict[str, Any]:
    return ctx.input_adaptation.desktop_companion_context.get("voice_false_trigger_guard") or {}


async def scenario_owner_task_priority() -> ScenarioResult:
    calls: list[dict[str, Any]] = []
    session_id = "stress-owner-task-priority"
    ctx, plan, reply = await _execute_plan(
        text=ZH_OPEN_WECHAT_NOISE,
        run_id="stress-owner-task-priority",
        session_id=session_id,
        voice_context=_voice_ctx(ZH_OPEN_WECHAT_NOISE, session_id=session_id),
        fake_tool=_make_fake_tool(calls),
    )
    guard = _guard(ctx)
    target = plan.review_summary.target
    ok = (
        ctx.envelope.normalized_text == "\u6253\u5f00WeChat"
        and plan.review_summary.top_intent == "open_app"
        and target.get("name") == "WeChat"
        and any(c["tool"] == "mcp:windows_open_app" for c in calls)
        and "\u5df2\u6253\u5f00" in str(reply or "")
    )
    return ScenarioResult(
        "voice_ctx_001",
        "owner_task_priority",
        ZH_OPEN_WECHAT_NOISE,
        "owner command wins over trailing background speech and opens WeChat",
        "normalized=%s intent=%s calls=%s guard=%s"
        % (ctx.envelope.normalized_text, plan.review_summary.top_intent, [c["tool"] for c in calls], guard.get("reason_code")),
        "PASS" if ok else "FAIL",
        {
            "normalized_text": ctx.envelope.normalized_text,
            "intent": plan.review_summary.top_intent,
            "target": target,
            "reply": _visible_reply(reply),
            "calls": calls,
            "guard": guard,
        },
    )


async def scenario_owner_task_noise_case(text: str, label: str, expected_app: str) -> ScenarioResult:
    calls: list[dict[str, Any]] = []
    session_id = f"stress-owner-task-noise-{label}"
    ctx, plan, reply = await _execute_plan(
        text=text,
        run_id=f"stress-owner-task-noise-{label}",
        session_id=session_id,
        voice_context=_voice_ctx(text, session_id=session_id),
        fake_tool=_make_fake_tool(calls),
    )
    guard = _guard(ctx)
    target = plan.review_summary.target
    open_calls = [c for c in calls if c["tool"] == "mcp:windows_open_app"]
    ok = (
        guard.get("action") == "allow"
        and plan.review_summary.top_intent == "open_app"
        and target.get("name") == expected_app
        and len(open_calls) == 1
        and str(open_calls[0]["payload"].get("app") or "") == expected_app
        and "\u5df2\u6253\u5f00" in str(reply or "")
    )
    return ScenarioResult(
        f"voice_ctx_007_{label}",
        "owner_task_noise_matrix",
        text,
        f"owner open-app task should execute once despite nearby chatter ({expected_app})",
        "normalized=%s intent=%s target=%s calls=%s guard=%s/%s"
        % (
            ctx.envelope.normalized_text,
            plan.review_summary.top_intent,
            target.get("name"),
            [c["tool"] for c in calls],
            guard.get("action"),
            guard.get("reason_code"),
        ),
        "PASS" if ok else "FAIL",
        {
            "normalized_text": ctx.envelope.normalized_text,
            "intent": plan.review_summary.top_intent,
            "target": target,
            "reply": _visible_reply(reply),
            "calls": calls,
            "guard": guard,
        },
    )


async def scenario_background_noise_ignored() -> ScenarioResult:
    session_id = "stress-background-noise"
    ctx, plan = await _build_plan(
        ZH_BACKGROUND_NOISE,
        run_id="stress-background-noise",
        session_id=session_id,
        voice_context=_voice_ctx(ZH_BACKGROUND_NOISE, session_id=session_id, confidence=0.82),
    )
    guard = _guard(ctx)
    ok = guard.get("action") == "drop" and guard.get("should_continue_planning") is False
    return ScenarioResult(
        "voice_ctx_002",
        "background_noise",
        ZH_BACKGROUND_NOISE,
        "background speech is dropped before tool planning",
        "guard=%s/%s intent=%s" % (guard.get("action"), guard.get("reason_code"), plan.review_summary.top_intent),
        "PASS" if ok else "FAIL",
        {"guard": guard, "intent": plan.review_summary.top_intent, "work_orders": [w.to_dict() for w in plan.work_orders]},
    )


async def scenario_message_pending_reply(reply_text: str, label: str) -> ScenarioResult:
    calls: list[dict[str, Any]] = []
    session_id = f"stress-message-pending-{label}"
    first_ctx, first_plan, first_reply = await _execute_plan(
        text=ZH_SEND_HELLO,
        run_id=f"stress-message-pending-{label}-1",
        session_id=session_id,
        voice_context=_voice_ctx(ZH_SEND_HELLO, session_id=session_id),
        fake_tool=_make_fake_tool(calls),
        tools=[{"id": "mcp:windows_lark_send_message"}],
    )
    first_call_count = len(calls)
    second_ctx, second_plan, second_reply = await _execute_plan(
        text=reply_text,
        run_id=f"stress-message-pending-{label}-2",
        session_id=session_id,
        voice_context=_voice_ctx(reply_text, session_id=session_id, confidence=0.55),
        fake_tool=_make_fake_tool(calls),
        tools=[{"id": "mcp:windows_lark_send_message"}],
    )
    send_calls = [c for c in calls if c["tool"] == "mcp:windows_lark_send_message"]
    first_guard = _guard(first_ctx)
    second_guard = _guard(second_ctx)
    ok = (
        first_call_count == 0
        and "jachin-ui:pending-confirmation" in str(first_reply or "")
        and second_guard.get("reason_code") == "pending_task_slot_reply"
        and len(send_calls) == 1
        and _decode_recipients(send_calls[0]["payload"].get("recipients_json")) == ["Neil"]
        and send_calls[0]["payload"].get("message") == "\u4f60\u597d"
        and "\u5df2\u53d1\u9001\u6d88\u606f\u7ed9 Neil" in str(second_reply or "")
    )
    return ScenarioResult(
        f"voice_ctx_003_{label}",
        "pending_message_session",
        f"{ZH_SEND_HELLO} -> {reply_text}",
        "missing-recipient message task resumes from short voice/text reply",
        "first=%s second=%s calls=%s"
        % (_visible_reply(first_reply), _visible_reply(second_reply), [c["tool"] for c in calls]),
        "PASS" if ok else "FAIL",
        {
            "first_guard": first_guard,
            "second_guard": second_guard,
            "first_intent": first_plan.review_summary.top_intent,
            "second_intent": second_plan.review_summary.top_intent,
            "first_reply": first_reply,
            "second_reply": second_reply,
            "calls": calls,
        },
    )


async def scenario_lark_tool_evidence() -> ScenarioResult:
    calls: list[dict[str, Any]] = []
    session_id = "stress-lark-evidence"
    ctx, plan, reply = await _execute_plan(
        text="send to Neil: hello evidence check",
        run_id="stress-lark-evidence",
        session_id=session_id,
        voice_context=_voice_ctx("send to Neil: hello evidence check", session_id=session_id, confidence=0.92),
        fake_tool=_make_fake_tool(calls),
    )
    markers_ok = "jachin-ui:task-session" in str(reply or "")
    send_calls = [c for c in calls if c["tool"] == "mcp:windows_lark_send_message"]
    ok = markers_ok and len(send_calls) == 1 and send_calls[0]["payload"].get("message") == "hello evidence check"
    return ScenarioResult(
        "voice_ctx_004",
        "lark_delivery_evidence",
        "send to Neil: hello evidence check",
        "Lark send uses tool and returns task-session verification evidence",
        "markers=%s calls=%s" % (markers_ok, [c["tool"] for c in calls]),
        "PASS" if ok else "FAIL",
        {
            "normalized_text": ctx.envelope.normalized_text,
            "intent": plan.review_summary.top_intent,
            "reply": reply,
            "calls": calls,
        },
    )


async def scenario_speaker_thresholds() -> list[ScenarioResult]:
    from l3_node.voice_false_trigger_guard import evaluate_voice_false_trigger

    rows: list[ScenarioResult] = []
    cases = [
        (
            "owner_clear",
            _voice_ctx(ZH_OPEN_LARK_LOCK, confidence=0.91, owner=True),
            "allow",
            "owner verified should allow clear action",
        ),
        (
            "non_owner",
            _voice_ctx(ZH_OPEN_LARK_LOCK, confidence=0.94, owner=False),
            "drop",
            "non-owner command should not execute",
        ),
        (
            "ambiguous",
            _voice_ctx(
                ZH_OPEN_LARK_LOCK,
                confidence=0.9,
                owner=None,
                extra={"voice_speaker_verification_status": "ambiguous"},
            ),
            "confirm",
            "ambiguous speaker should ask confirmation",
        ),
        (
            "weak_owner_ratio",
            _voice_ctx(
                ZH_OPEN_LARK_LOCK,
                confidence=0.9,
                owner=True,
                extra={
                    "voice_owner_duration_ms": 120,
                    "voice_total_duration_ms": 1000,
                    "voice_speaker_verification_strict": True,
                },
            ),
            "drop",
            "strict low owner ratio should block command",
        ),
    ]
    for idx, (name, ctx, expected_action, expected) in enumerate(cases, start=1):
        decision = evaluate_voice_false_trigger(ZH_OPEN_LARK_LOCK, voice_context=ctx, run_id=f"stress-speaker-{name}")
        ok = decision.action == expected_action
        rows.append(
            ScenarioResult(
                f"voice_ctx_005_{idx}_{name}",
                "speaker_threshold",
                ZH_OPEN_LARK_LOCK,
                expected,
                "action=%s reason=%s" % (decision.action, decision.reason_code),
                "PASS" if ok else "FAIL",
                {"decision": decision.to_dict()},
            )
        )
    return rows


async def scenario_noise_during_pending_does_not_clear_task() -> ScenarioResult:
    calls: list[dict[str, Any]] = []
    session_id = "stress-pending-noise-survival"
    first_ctx, first_plan, first_reply = await _execute_plan(
        text=ZH_SEND_HELLO,
        run_id="stress-pending-noise-survival-1",
        session_id=session_id,
        voice_context=_voice_ctx(ZH_SEND_HELLO, session_id=session_id),
        fake_tool=_make_fake_tool(calls),
        tools=[{"id": "mcp:windows_lark_send_message"}],
    )
    noise_ctx, noise_plan = await _build_plan(
        ZH_BACKGROUND_NOISE,
        run_id="stress-pending-noise-survival-2",
        session_id=session_id,
        voice_context=_voice_ctx(ZH_BACKGROUND_NOISE, session_id=session_id, confidence=0.8),
    )
    third_ctx, third_plan, third_reply = await _execute_plan(
        text="Neil",
        run_id="stress-pending-noise-survival-3",
        session_id=session_id,
        voice_context=_voice_ctx("Neil", session_id=session_id, confidence=0.58),
        fake_tool=_make_fake_tool(calls),
        tools=[{"id": "mcp:windows_lark_send_message"}],
    )
    send_calls = [c for c in calls if c["tool"] == "mcp:windows_lark_send_message"]
    noise_guard = _guard(noise_ctx)
    third_guard = _guard(third_ctx)
    ok = (
        "jachin-ui:pending-confirmation" in str(first_reply or "")
        and noise_guard.get("action") == "drop"
        and len(send_calls) == 1
        and third_guard.get("reason_code") == "pending_task_slot_reply"
        and "\u5df2\u53d1\u9001\u6d88\u606f\u7ed9 Neil" in str(third_reply or "")
    )
    return ScenarioResult(
        "voice_ctx_006",
        "pending_noise_survival",
        f"{ZH_SEND_HELLO} -> {ZH_BACKGROUND_NOISE} -> Neil",
        "background speech during pending task is ignored and task resumes later",
        "noise=%s/%s third=%s calls=%s"
        % (noise_guard.get("action"), noise_guard.get("reason_code"), third_guard.get("reason_code"), [c["tool"] for c in calls]),
        "PASS" if ok else "FAIL",
        {
            "first_reply": first_reply,
            "noise_guard": noise_guard,
            "noise_intent": noise_plan.review_summary.top_intent,
            "third_reply": third_reply,
            "third_guard": third_guard,
            "calls": calls,
        },
    )


async def scenario_noise_during_active_task_is_ignored() -> ScenarioResult:
    from l3_node.voice_false_trigger_guard import evaluate_voice_false_trigger

    ctx = _voice_ctx(ZH_BACKGROUND_NOISE, session_id="stress-active-task-noise", confidence=0.86)
    ctx["voice_active_task_context"] = {
        "active_tasks": [{"id": "task-open-wechat", "title": "open WeChat"}],
        "focused_task_id": "task-open-wechat",
        "source": "desktop_voice_active_task_context",
    }
    decision = evaluate_voice_false_trigger(
        ZH_BACKGROUND_NOISE,
        voice_context=ctx,
        run_id="stress-active-task-noise",
    )
    ok = (
        decision.action == "drop"
        and decision.reason_code in {"active_task_background_noise_ignored", "background_noise_fragment", "filler_or_backchannel"}
        and decision.evidence.get("active_execution", {}).get("active") is True
    )
    return ScenarioResult(
        "voice_ctx_008",
        "active_task_noise_survival",
        ZH_BACKGROUND_NOISE,
        "background speech during an active executable task is ignored and does not interrupt it",
        "guard=%s/%s active=%s"
        % (decision.action, decision.reason_code, decision.evidence.get("active_execution", {}).get("active")),
        "PASS" if ok else "FAIL",
        {"guard": decision.to_dict()},
    )


async def run() -> list[ScenarioResult]:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    results: list[ScenarioResult] = []
    owner_noise_cases = [
        (f"{ZH_OPEN_WECHAT} {ZH_BACKGROUND_NOISE}", "wechat_suffix_noise", "WeChat"),
        (f"{ZH_BACKGROUND_NOISE} {ZH_OPEN_WECHAT}", "wechat_prefix_noise", "WeChat"),
        (f"{ZH_OPEN_LARK} {ZH_BACKGROUND_NOISE}", "lark_suffix_noise", "Lark"),
        (f"{ZH_BACKGROUND_NOISE} {ZH_OPEN_LARK}", "lark_prefix_noise", "Lark"),
    ]
    scenario_fns = [
        scenario_owner_task_priority,
        scenario_background_noise_ignored,
        lambda: scenario_message_pending_reply("Neil", "neil"),
        lambda: scenario_message_pending_reply("A", "a"),
        lambda: scenario_message_pending_reply("1", "one"),
        lambda: scenario_message_pending_reply(ZH_SEND_TO_NEIL, "send_to_neil"),
        scenario_lark_tool_evidence,
        scenario_noise_during_pending_does_not_clear_task,
        scenario_noise_during_active_task_is_ignored,
        *[
            (lambda text=text, label=label, expected_app=expected_app: scenario_owner_task_noise_case(text, label, expected_app))
            for text, label, expected_app in owner_noise_cases
        ],
    ]
    for fn in scenario_fns:
        try:
            results.append(await fn())
        except Exception as exc:
            results.append(
                ScenarioResult(
                    f"voice_ctx_exception_{len(results) + 1}",
                    "exception",
                    "",
                    "scenario should complete",
                    f"{type(exc).__name__}: {exc}",
                    "FAIL",
                    {},
                )
            )
    results.extend(await scenario_speaker_thresholds())
    _write_jsonl(results)
    _write_report(results)
    return results


def _visible_reply(text: str | None) -> str:
    raw = str(text or "")
    return raw.split("<!-- jachin-ui:", 1)[0].strip()


def _write_jsonl(results: list[ScenarioResult]) -> None:
    JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with JSONL_PATH.open("w", encoding="utf-8") as f:
        for row in results:
            f.write(json.dumps(row.to_dict(), ensure_ascii=False) + "\n")


def _write_report(results: list[ScenarioResult]) -> None:
    passed = sum(1 for r in results if r.status == "PASS")
    total = len(results)
    grouped: dict[str, list[ScenarioResult]] = {}
    for row in results:
        grouped.setdefault(row.category, []).append(row)
    lines = [
        "# Voice Contextual Operation Stress Report",
        "",
        f"- Generated at: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- JSONL evidence: `{JSONL_PATH}`",
        f"- Total: {total}",
        f"- Passed: {passed}",
        f"- Failed: {total - passed}",
        f"- Pass rate: {passed / max(1, total) * 100:.2f}%",
        "",
        "## Scope",
        "",
        "This offline stress run verifies the always-on voice path before live L3 testing. It uses simulated STT text and fake tool executors, with native desktop adapters disabled, so no real App or Lark action is performed.",
        "",
        "Covered checks:",
        "",
        "- Owner command should win over trailing background speech.",
        "- Background chatter should be ignored before tool execution.",
        "- Incomplete message task should suspend and resume through Neil / A / 1 / 发给 Neil.",
        "- Lark delivery must invoke a send tool and return task-session evidence.",
        "- Speaker verification threshold should allow owner, block non-owner, and confirm ambiguous speaker.",
        "- Noise during a pending task must not clear that task.",
        "",
        "## Result Matrix",
        "",
        "| Test ID | Category | Expected | Actual | Status |",
        "|---|---|---|---|---|",
    ]
    for row in results:
        lines.append(
            "| %s | %s | %s | %s | %s |"
            % (
                row.test_id,
                row.category,
                _md(row.expected),
                _md(row.actual),
                row.status,
            )
        )
    lines.extend(["", "## Category Summary", ""])
    for category, rows in sorted(grouped.items()):
        ok = sum(1 for r in rows if r.status == "PASS")
        lines.append(f"- `{category}`: {ok}/{len(rows)} passed")
    failures = [r for r in results if r.status != "PASS"]
    lines.extend(["", "## Failure Analysis", ""])
    if not failures:
        lines.append("No failure found in this offline run. Ready for live L3 microphone validation.")
    else:
        for row in failures:
            lines.append(f"- `{row.test_id}`: {row.actual}")
    lines.extend(
        [
            "",
            "## Live Test Guidance",
            "",
            "After starting L3, repeat these exact user-visible cases:",
            "",
            "1. In a noisy room, owner says: 打开微信。",
            "2. Let someone nearby say: 行，对，就那个。Jachin should ignore it.",
            "3. Owner says: 发送消息，你好。Then say or click Neil / A / 1. It should send exactly once.",
            "4. Check the chat bubble task chain: it should show Goal Interpreter, WorkOrder, MessageExecutorAgent, Verification.",
            "5. If owner voiceprint is missing or ambiguous, executable actions should ask confirmation instead of silently running.",
            "",
            "## Engineering Notes",
            "",
            "- Native RoleExecutor adapters were disabled in this run through `JACHIN_DISABLE_ROLE_NATIVE_ADAPTERS=1`.",
            "- Turn-closure memory writes were disabled to avoid polluting real user memory during stress generation.",
            "- Real Lark send still requires live validation because OCR/window evidence depends on the desktop state.",
        ]
    )
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _md(text: str) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    rows = asyncio.run(run())
    print(json.dumps(
        {
            "total": len(rows),
            "passed": sum(1 for r in rows if r.status == "PASS"),
            "failed": sum(1 for r in rows if r.status != "PASS"),
            "report": str(REPORT_PATH),
            "jsonl": str(JSONL_PATH),
        },
        ensure_ascii=False,
        indent=2,
    ))
