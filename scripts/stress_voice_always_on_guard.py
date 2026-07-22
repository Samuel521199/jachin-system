from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("JACHIN_COGNITIVE_KERNEL_HOME", str(ROOT / "output" / "voice_stress" / "kernel_home"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from l3_node.cognitive_kernel.input_adapter import adapt_input_for_cognitive_kernel  # noqa: E402


@dataclass
class VoiceScenario:
    name: str
    category: str
    text: str
    confidence: float | None
    mode: str = "continuous_listen"
    context: dict[str, Any] = field(default_factory=dict)
    expected_action: str = "allow"
    expected_reason: str = "accepted"


def _ctx(s: VoiceScenario) -> dict[str, Any]:
    ctx = {
        "voice_interaction_mode": s.mode,
        "voice_raw_stt_text": s.text,
        "voice_asr_raw_text": s.text,
        "voice_final_text": s.text,
        "voice_stt_confidence": s.confidence,
        "voice_stt_finalized": True,
        "source": "voice_stress",
    }
    ctx.update(s.context)
    return ctx


def _scenarios() -> list[VoiceScenario]:
    now_ms = int(time.time() * 1000)
    return [
        VoiceScenario("clear_owner_open_browser", "valid_task", "打开浏览器", 0.93, context={"voice_speaker_verified": True}),
        VoiceScenario("clear_owner_open_calculator", "valid_task", "打开计算器，计算 99 加 100", 0.94, context={"voice_speaker_verified": True}),
        VoiceScenario("ptt_lowish_command_allowed", "push_to_talk", "打开 Lark", 0.43, mode="push_to_talk"),
        VoiceScenario("wake_owner_message_task", "valid_task", "打开 Lark 给 Neil 发送你好", 0.91, mode="wake_conversation", context={"voice_speaker_verified": True}),
        VoiceScenario("side_chat_high_confidence", "valid_chat", "你现在在做什么", 0.89, context={"voice_speaker_verified": True}),
        VoiceScenario("pause_only_open", "pause_incomplete", "打开", 0.92, expected_action="drop", expected_reason="voice_session_bare_action_without_target"),
        VoiceScenario("pause_only_send", "pause_incomplete", "发给", 0.91, expected_action="drop", expected_reason="voice_session_ends_with_missing_slot"),
        VoiceScenario("provisional_partial_browser", "pause_incomplete", "打开浏览", 0.88, context={"voice_stt_provisional": True}, expected_action="drop", expected_reason="voice_session_stt_not_finalized"),
        VoiceScenario("provisional_not_finalized", "pause_incomplete", "搜索最新 AI", 0.86, context={"voice_stt_finalized": False}, expected_action="drop", expected_reason="voice_session_stt_not_finalized"),
        VoiceScenario("final_after_pause", "pause_recovery", "打开浏览器", 0.91, context={"voice_speaker_verified": True}),
        VoiceScenario("filler_um", "noise", "嗯", 0.95, expected_action="drop", expected_reason="filler_or_backchannel"),
        VoiceScenario("filler_ah", "noise", "啊", 0.91, expected_action="drop", expected_reason="filler_or_backchannel"),
        VoiceScenario("english_test_noise", "noise", "test", 0.87, expected_action="drop", expected_reason="filler_or_backchannel"),
        VoiceScenario("short_object_noise", "noise", "桌子", 0.82, expected_action="drop", expected_reason="background_noise_fragment"),
        VoiceScenario("low_confidence_background", "noise", "旁边有人在聊天", 0.24, expected_action="drop", expected_reason="low_confidence_non_action"),
        VoiceScenario("assistant_playback_echo", "echo", "好的，已经完成", 0.96, context={"assistant_speaking": True}, expected_action="drop", expected_reason="assistant_playback_echo"),
        VoiceScenario("tts_playing_echo", "echo", "我已经打开浏览器", 0.96, context={"tts_playing": True}, expected_action="drop", expected_reason="assistant_playback_echo"),
        VoiceScenario("duplicate_recent", "duplicate", "打开浏览器", 0.9, context={"voice_last_text": "打开浏览器", "voice_last_text_at_ms": now_ms}, expected_action="drop", expected_reason="duplicate_fragment"),
        VoiceScenario("duplicate_without_timestamp", "duplicate", "打开浏览器", 0.9, context={"voice_last_text": "打开浏览器"}, expected_action="drop", expected_reason="duplicate_fragment"),
        VoiceScenario("low_confidence_action", "confidence", "打开浏览器", 0.31, expected_action="confirm", expected_reason="low_confidence_action"),
        VoiceScenario("low_confidence_send", "confidence", "发给 Neil 你好", 0.45, expected_action="confirm", expected_reason="low_confidence_action"),
        VoiceScenario("risky_no_confidence", "confidence", "发给 Neil 你好", None, expected_action="confirm", expected_reason="risky_action_requires_voice_confirmation"),
        VoiceScenario("non_owner_bool_false", "speaker", "打开浏览器", 0.94, context={"voice_speaker_verified": False}, expected_action="drop", expected_reason="non_owner_speaker"),
        VoiceScenario("non_owner_explicit_reject", "speaker", "打开浏览器", 0.94, context={"voice_speaker_rejected": True}, expected_action="drop", expected_reason="non_owner_speaker"),
        VoiceScenario("strict_score_low_reject", "speaker", "打开浏览器", 0.94, context={"voice_speaker_verification_score": 0.42, "voice_speaker_verification_threshold": 0.67, "voice_speaker_verification_strict": True}, expected_action="drop", expected_reason="non_owner_speaker"),
        VoiceScenario("ambiguous_speaker_action", "speaker", "打开浏览器", 0.93, context={"voice_speaker_verification_status": "ambiguous"}, expected_action="confirm", expected_reason="speaker_verification_ambiguous"),
        VoiceScenario("profile_missing_action", "speaker", "打开浏览器", 0.93, context={"voice_speaker_profile_missing": True}, expected_action="confirm", expected_reason="speaker_verification_ambiguous"),
        VoiceScenario("owner_verified_action", "speaker", "打开浏览器", 0.93, context={"voice_speaker_verified": True}),
        VoiceScenario("owner_track_ratio_low_strict", "speaker", "打开浏览器", 0.93, context={"voice_owner_duration_ms": 200, "voice_total_duration_ms": 1200, "voice_speaker_verification_strict": True}, expected_action="drop", expected_reason="non_owner_speaker"),
        VoiceScenario("owner_track_ratio_low_non_strict", "speaker", "打开浏览器", 0.93, context={"voice_owner_duration_ms": 200, "voice_total_duration_ms": 1200}, expected_action="allow", expected_reason="accepted"),
        VoiceScenario("confirmed_pending_skip_once", "pending", "确认执行", 0.2, context={"voice_false_trigger_skip_once": True}, expected_action="allow", expected_reason="confirmed_pending_voice"),
        VoiceScenario("normal_question_owner", "chat", "帮我解释一下这个功能", 0.9, context={"voice_speaker_verified": True}),
        VoiceScenario("web_research_owner", "multi_tool", "搜索最新 AI 模型消息，整理后发给 Neil", 0.92, context={"voice_speaker_verified": True}),
        VoiceScenario("file_reveal_owner", "multi_tool", "打开最近的下载文件夹", 0.9, context={"voice_speaker_verified": True}),
        VoiceScenario("noisy_action_with_music", "noise", "音乐声音打开浏览器", 0.37, expected_action="confirm", expected_reason="low_confidence_action"),
        VoiceScenario("english_command", "valid_task", "open browser", 0.88),
        VoiceScenario("english_incomplete", "pause_incomplete", "open", 0.86, expected_action="drop", expected_reason="voice_session_bare_action_without_target"),
        VoiceScenario("english_low_conf", "confidence", "send message to Neil", 0.33, expected_action="confirm", expected_reason="low_confidence_action"),
        VoiceScenario("ambient_long_high_confidence", "chat", "我刚刚说的是一个背景里的普通句子", 0.88, expected_action="allow", expected_reason="accepted"),
        VoiceScenario("ambient_long_low_confidence", "noise", "我刚刚说的是一个背景里的普通句子", 0.27, expected_action="drop", expected_reason="low_confidence_non_action"),
    ]


def run() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    passed = 0
    scenarios = _scenarios()
    for idx, scenario in enumerate(scenarios, start=1):
        ctx = _ctx(scenario)
        adaptation = adapt_input_for_cognitive_kernel(
            turn_id=f"voice-stress-{idx:03d}",
            user_input=scenario.text,
            channel="voice_stress",
            session_id="voice-stress",
            desktop_companion_context=ctx,
        )
        guard = adaptation.modality_evidence.get("voice_false_trigger_guard", {})
        action = str(guard.get("action") or "")
        reason = str(guard.get("reason_code") or "")
        ok = action == scenario.expected_action and reason == scenario.expected_reason
        passed += 1 if ok else 0
        rows.append(
            {
                "name": scenario.name,
                "category": scenario.category,
                "text": scenario.text,
                "confidence": scenario.confidence,
                "expected_action": scenario.expected_action,
                "expected_reason": scenario.expected_reason,
                "actual_action": action,
                "actual_reason": reason,
                "ok": ok,
                "steps": [step.get("name") for step in adaptation.adapter_evidence.get("steps", [])],
            }
        )
    summary = {
        "total": len(scenarios),
        "passed": passed,
        "failed": len(scenarios) - passed,
        "pass_rate": round(passed / max(1, len(scenarios)), 4),
        "rows": rows,
    }
    _write_report(summary)
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"}, ensure_ascii=False, indent=2))
    return summary


def _write_report(summary: dict[str, Any]) -> None:
    report = ROOT / "docs" / "16_voice_always_on_stress_report.md"
    lines = [
        "# Voice Always-on Stress Report",
        "",
        f"- Generated at: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Total scenarios: {summary['total']}",
        f"- Passed: {summary['passed']}",
        f"- Failed: {summary['failed']}",
        f"- Pass rate: {summary['pass_rate'] * 100:.2f}%",
        "",
        "## Scope",
        "",
        "This stress test simulates continuous voice, wake conversation, and push-to-talk ingress without using a physical microphone. Each scenario enters the same L3 InputAdapter and VoiceFalseTriggerGuard path used by live voice turns.",
        "",
        "Covered categories: valid task, pause/incomplete speech, noise, duplicate fragments, assistant echo, low confidence, speaker verification, pending confirmation, multi-tool intent.",
        "",
        "## Results",
        "",
        "| # | Category | Scenario | Expected | Actual | OK |",
        "|---:|---|---|---|---|---|",
    ]
    for idx, row in enumerate(summary["rows"], start=1):
        expected = f"{row['expected_action']} / {row['expected_reason']}"
        actual = f"{row['actual_action']} / {row['actual_reason']}"
        ok = "PASS" if row["ok"] else "FAIL"
        name = str(row["name"]).replace("|", "\\|")
        category = str(row["category"]).replace("|", "\\|")
        lines.append(f"| {idx} | {category} | {name} | {expected} | {actual} | {ok} |")
    failures = [row for row in summary["rows"] if not row["ok"]]
    lines.extend(["", "## Failure Details", ""])
    if not failures:
        lines.append("No failures in this run.")
    else:
        for row in failures:
            lines.append(
                f"- {row['name']}: expected {row['expected_action']} / {row['expected_reason']}, got {row['actual_action']} / {row['actual_reason']}"
            )
    lines.extend(
        [
            "",
            "## Findings",
            "",
            "- Continuous mode must treat incomplete action fragments as non-executable, not as normal chat.",
            "- Speaker verification should be enforced twice: Rust/JVS owner-track first, L3 guard second for any bypassed or simulated input.",
            "- Push-to-talk can use a more permissive confidence threshold because the user intentionally started recording.",
            "- Evidence must include guard reason codes so false positives can be debugged without watching the UI.",
            "",
            "## Next Focus",
            "",
            "- Run live microphone tests for owner/non-owner voice after an owner voiceprint is enrolled.",
            "- Add utterance aggregation if product experience requires combining short pauses into one task instead of dropping incomplete fragments.",
            "- Feed repeated guard blocks into Memory/FailureLearning so the system can adapt thresholds per user environment.",
        ]
    )
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    run()
