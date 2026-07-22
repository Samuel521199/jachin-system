# Voice Unified Input Live Test Log

Created at: 2026-07-17

This document is the master log for voice and text unified-input testing. Its purpose is not to describe the feature, but to preserve enough evidence so that later failures can be reproduced, compared, and fixed.

The core rule: voice and text are only different input modalities. After input adaptation, both must enter the same GoalInterpreter -> TaskDecomposer -> Dispatcher -> Verification -> Recovery -> Memory path.

## Log Locations

- Human-readable master log: `docs/19_voice_unified_input_live_test_log.md`
- Stress report: `docs/16_voice_always_on_stress_report.md`
- Owner voiceprint live check: `docs/17_voice_owner_voiceprint_live_check.md`
- Runtime voice learning events: `%USERPROFILE%\.jachin\voice_false_trigger_learning.jsonl`
- Voice session fragments: `%USERPROFILE%\.jachin\voice_sessions\*.json`
- Cognitive Kernel ledger: `%USERPROFILE%\.jachin\cognitive_kernel\*.jsonl`
- Memory Growth raw evidence: `%USERPROFILE%\.jachin\memory_growth\raw_events\*.jsonl`
- L3 debug log: `%USERPROFILE%\.jachin\l3_debug.log` or packaged `dist_jachin_desktop\logs\l3_debug.log`
- JVS / voice companion logs: `%USERPROFILE%\.jachin\jachin_debug\voice_companion.log`, `%USERPROFILE%\.jachin\jachin_debug\voice_chat.log`

When a test fails, copy the key event ids and file paths into this document instead of only relying on console output.

## Required Fields Per Test

Each test record should preserve these fields:

- `test_id`: stable id, for example `voice_live_20260717_001`.
- `mode`: `always_on`, `push_to_talk`, `text`, or `lark`.
- `environment`: dev / packaged, headset / laptop mic, quiet / noisy, owner / non-owner.
- `raw_input`: what the user actually said or typed.
- `asr_text`: speech recognition result. For text mode, same as raw input.
- `normalized_text`: text after language normalization and alias correction.
- `session_id`: voice or chat session id.
- `turn_id`: Cognitive Kernel turn id / run id.
- `endpoint_decision`: ready / wait / merged, plus reason.
- `guard_decision`: allow / confirm / drop, plus reason.
- `goal_interpretation`: detected goal, constraints, missing slots, risk.
- `task_dag`: generated nodes and dependencies.
- `dispatcher_path`: RoleExecutor / tool / capability used by each node.
- `verification_result`: pass / fail, with evidence.
- `recovery_result`: whether recovery was needed, selected strategy, attempt count.
- `memory_result`: what was recalled, written, rejected, or promoted.
- `evidence_paths`: ledger, screenshot, OCR, JSONL, report paths.
- `expected_result`: what should happen.
- `actual_result`: what happened.
- `status`: PASS / FAIL / NEEDS_CONFIRMATION / BLOCKED.
- `root_cause`: filled only after analysis.
- `fix_or_next_action`: concrete follow-up.

## JSONL Record Shape

Use this shape when exporting structured records:

```json
{
  "test_id": "voice_live_20260717_001",
  "created_at": "2026-07-17T17:15:00+08:00",
  "mode": "always_on",
  "environment": {
    "runtime": "dev",
    "microphone": "headset",
    "noise": "quiet",
    "speaker": "owner"
  },
  "input": {
    "raw_input": "打开 Lark 给 Neil 发送你好",
    "asr_text": "打开 lock 给 Neil 发送你好",
    "normalized_text": "打开 Lark 给 Neil 发送你好",
    "session_id": "",
    "turn_id": ""
  },
  "voice": {
    "endpoint_decision": {"action": "ready", "reason_code": "complete"},
    "guard_decision": {"action": "allow", "reason_code": "accepted"},
    "speaker_verification": {"status": "owner_verified", "score": null},
    "corrections": [{"from": "lock", "to": "Lark", "source": "alias_memory"}]
  },
  "kernel": {
    "goal_interpretation": {},
    "task_dag": [],
    "dispatcher_path": [],
    "verification_result": {},
    "recovery_result": {},
    "memory_result": {}
  },
  "evidence_paths": [],
  "expected_result": "",
  "actual_result": "",
  "status": "PASS",
  "root_cause": "",
  "fix_or_next_action": ""
}
```

## Baseline Entries

| Time | Test ID | Mode | Scenario | Expected | Actual | Status | Evidence |
|---|---|---|---|---|---|---|---|
| 2026-07-17 17:02 | voice_stress_baseline_001 | simulated always-on | 40 continuous voice guard scenarios | endpointing, guard, confirmation, speaker, duplicate, echo, and multi-tool cases all pass | 40/40 passed | PASS | `docs/16_voice_always_on_stress_report.md` |
| 2026-07-17 17:02 | voice_unit_baseline_001 | unit | voice input adapter, interruption, replan, guard, pending confirmation, evidence | all voice units pass | 47 passed | PASS | pytest output |

## Live Test Queue

These are the next live tests. Do not mark them as passed until real UI / microphone evidence exists.

| Test ID | Mode | Scenario | Expected Result | Status | Notes |
|---|---|---|---|---|---|
| voice_live_owner_001 | always_on | owner says "打开 Lark" in quiet room | Lark opens through unified Dispatcher path | TODO | Requires owner voiceprint profile |
| voice_live_owner_002 | always_on | owner pauses: "打开" then "Lark" | endpoint waits, then merges and executes | TODO | Need session id evidence |
| voice_live_owner_003 | always_on | owner says "不是发给 Neil，改成 Vivian" during active message task | current task is replanned, not treated as unrelated chat | TODO | Need active task context |
| voice_live_noise_001 | always_on | background speech near microphone | system drops or asks confirmation, no tool execution | TODO | Record guard reason |
| voice_live_non_owner_001 | always_on | non-owner says an executable command | system blocks or asks confirmation | TODO | Requires voiceprint profile |
| voice_live_ptt_001 | push_to_talk | user clicks record and says a command | more permissive confidence, same downstream memory/recovery path | TODO | Compare with always_on |
| voice_live_text_equiv_001 | text | type the same command as voice_live_owner_001 | same goal, DAG, dispatcher path as voice | TODO | Used to verify voice/text parity |

## Debug Checklist

When a test fails, check in this order:

1. Did ASR produce the right text?
2. Did language normalization correct obvious alias errors such as `lock -> Lark`?
3. Did endpointing wait, merge, or prematurely execute?
4. Did false-trigger guard allow, confirm, or drop with a reasonable reason?
5. Did GoalInterpreter detect the same goal as typed text?
6. Did TaskDecomposer generate the expected DAG nodes?
7. Did Dispatcher call a real RoleExecutor / tool, or only produce a user-facing claim?
8. Did Verification prove the action happened?
9. Did RecoveryPlanner use the failure reason to pick a better next path?
10. Did MemoryWrite / FailureLearning record useful durable learning?

## Quality Gates

A live voice test is not accepted unless all of these are true:

- The task path is visible in Evidence or ledger.
- The final reply is backed by verification, not only by intent prediction.
- Failed actions do not claim success.
- Voice-only preprocessing does not bypass the unified text path after InputAdapter.
- Memory records include trust state: confirmed, inferred, rejected, or needs confirmation.
- Repeated failures create a reusable failure-learning record.

## Current Assessment

The simulated baseline is stable enough to start live microphone testing. The main remaining risk is not the kernel path, but real-world input quality: ASR drift, speaker verification readiness, environmental noise, and timing around user pauses.

## 2026-07-21 Offline Contextual Operation Stress

| Time | Test ID | Mode | Scenario | Expected | Actual | Status | Evidence |
|---|---|---|---|---|---|---|---|
| 2026-07-21 16:26 | voice_contextual_operation_001 | simulated always-on | owner task priority, background noise drop, pending message recipient resume, fake Lark tool evidence, speaker threshold checks, open-app noise prefix/suffix matrix, active-task noise survival | owner commands execute, background chatter is ignored, `发送消息，你好 -> Neil/A/1/发给 Neil` resumes the same pending task, Lark send uses a tool and records task-session evidence, active executable task is not interrupted by nearby chatter | 17/17 passed, native desktop adapters disabled, no real App or Lark action performed | PASS | `docs/21_voice_contextual_operation_stress_report.md`; `output/voice_contextual_stress/20260721_162604/voice_contextual_operation_stress.jsonl` |

### Notes

- This run verifies the unified voice/text logic layer before live L3 testing.
- It specifically guards against the recent failures where a pending recipient reply was treated as unrelated chat or noise.
- The first `发送消息，你好` turn is now required to call no send tool until the recipient slot is filled.
- Real Lark delivery still needs live validation because OCR/window evidence depends on the current desktop state.
