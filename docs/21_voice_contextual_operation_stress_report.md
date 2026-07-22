# Voice Contextual Operation Stress Report

- Generated at: 2026-07-21 16:26:25
- JSONL evidence: `D:\Projects\jachi\jachin-system-main\output\voice_contextual_stress\20260721_162604\voice_contextual_operation_stress.jsonl`
- Total: 17
- Passed: 17
- Failed: 0
- Pass rate: 100.00%

## Scope

This offline stress run verifies the always-on voice path before live L3 testing. It uses simulated STT text and fake tool executors, with native desktop adapters disabled, so no real App or Lark action is performed.

Covered checks:

- Owner command should win over trailing background speech.
- Background chatter should be ignored before tool execution.
- Incomplete message task should suspend and resume through Neil / A / 1 / 发给 Neil.
- Lark delivery must invoke a send tool and return task-session evidence.
- Speaker verification threshold should allow owner, block non-owner, and confirm ambiguous speaker.
- Noise during a pending task must not clear that task.

## Result Matrix

| Test ID | Category | Expected | Actual | Status |
|---|---|---|---|---|
| voice_ctx_001 | owner_task_priority | owner command wins over trailing background speech and opens WeChat | normalized=打开WeChat intent=open_app calls=['mcp:windows_open_app'] guard=accepted | PASS |
| voice_ctx_002 | background_noise | background speech is dropped before tool planning | guard=drop/background_noise_fragment intent=conversation | PASS |
| voice_ctx_003_neil | pending_message_session | missing-recipient message task resumes from short voice/text reply | first=我还不知道这条消息要发给谁。请选择联系人，或直接回复编号/字母：1/A = Neil; 2/B = Vivian; 3/C = 测试备注冒烟草稿。 second=已发送消息给 Neil。 calls=['mcp:windows_lark_send_message'] | PASS |
| voice_ctx_003_a | pending_message_session | missing-recipient message task resumes from short voice/text reply | first=我还不知道这条消息要发给谁。请选择联系人，或直接回复编号/字母：1/A = Neil; 2/B = Vivian; 3/C = 测试备注冒烟草稿。 second=已发送消息给 Neil。 calls=['mcp:windows_lark_send_message'] | PASS |
| voice_ctx_003_one | pending_message_session | missing-recipient message task resumes from short voice/text reply | first=我还不知道这条消息要发给谁。请选择联系人，或直接回复编号/字母：1/A = Neil; 2/B = Vivian; 3/C = 测试备注冒烟草稿。 second=已发送消息给 Neil。 calls=['mcp:windows_lark_send_message'] | PASS |
| voice_ctx_003_send_to_neil | pending_message_session | missing-recipient message task resumes from short voice/text reply | first=我还不知道这条消息要发给谁。请选择联系人，或直接回复编号/字母：1/A = Neil; 2/B = Vivian; 3/C = 测试备注冒烟草稿。 second=已发送消息给 Neil。 calls=['mcp:windows_lark_send_message'] | PASS |
| voice_ctx_004 | lark_delivery_evidence | Lark send uses tool and returns task-session verification evidence | markers=True calls=['mcp:windows_open_app', 'mcp:windows_lark_send_message'] | PASS |
| voice_ctx_006 | pending_noise_survival | background speech during pending task is ignored and task resumes later | noise=drop/pending_task_background_noise_ignored third=pending_task_slot_reply calls=['mcp:windows_lark_send_message'] | PASS |
| voice_ctx_008 | active_task_noise_survival | background speech during an active executable task is ignored and does not interrupt it | guard=drop/active_task_background_noise_ignored active=True | PASS |
| voice_ctx_007_wechat_suffix_noise | owner_task_noise_matrix | owner open-app task should execute once despite nearby chatter (WeChat) | normalized=打开WeChat intent=open_app target=WeChat calls=['mcp:windows_open_app'] guard=allow/accepted | PASS |
| voice_ctx_007_wechat_prefix_noise | owner_task_noise_matrix | owner open-app task should execute once despite nearby chatter (WeChat) | normalized=打开WeChat intent=open_app target=WeChat calls=['mcp:windows_open_app'] guard=allow/accepted | PASS |
| voice_ctx_007_lark_suffix_noise | owner_task_noise_matrix | owner open-app task should execute once despite nearby chatter (Lark) | normalized=打开 Lark intent=open_app target=Lark calls=['mcp:windows_open_app'] guard=allow/accepted | PASS |
| voice_ctx_007_lark_prefix_noise | owner_task_noise_matrix | owner open-app task should execute once despite nearby chatter (Lark) | normalized=打开 Lark intent=open_app target=Lark calls=['mcp:windows_open_app'] guard=allow/accepted | PASS |
| voice_ctx_005_1_owner_clear | speaker_threshold | owner verified should allow clear action | action=allow reason=accepted | PASS |
| voice_ctx_005_2_non_owner | speaker_threshold | non-owner command should not execute | action=drop reason=non_owner_speaker | PASS |
| voice_ctx_005_3_ambiguous | speaker_threshold | ambiguous speaker should ask confirmation | action=confirm reason=speaker_verification_ambiguous | PASS |
| voice_ctx_005_4_weak_owner_ratio | speaker_threshold | strict low owner ratio should block command | action=drop reason=non_owner_speaker | PASS |

## Category Summary

- `active_task_noise_survival`: 1/1 passed
- `background_noise`: 1/1 passed
- `lark_delivery_evidence`: 1/1 passed
- `owner_task_noise_matrix`: 4/4 passed
- `owner_task_priority`: 1/1 passed
- `pending_message_session`: 4/4 passed
- `pending_noise_survival`: 1/1 passed
- `speaker_threshold`: 4/4 passed

## Failure Analysis

No failure found in this offline run. Ready for live L3 microphone validation.

## Live Test Guidance

After starting L3, repeat these exact user-visible cases:

1. In a noisy room, owner says: 打开微信。
2. Let someone nearby say: 行，对，就那个。Jachin should ignore it.
3. Owner says: 发送消息，你好。Then say or click Neil / A / 1. It should send exactly once.
4. Check the chat bubble task chain: it should show Goal Interpreter, WorkOrder, MessageExecutorAgent, Verification.
5. If owner voiceprint is missing or ambiguous, executable actions should ask confirmation instead of silently running.

## Engineering Notes

- Native RoleExecutor adapters were disabled in this run through `JACHIN_DISABLE_ROLE_NATIVE_ADAPTERS=1`.
- Turn-closure memory writes were disabled to avoid polluting real user memory during stress generation.
- Real Lark send still requires live validation because OCR/window evidence depends on the desktop state.
