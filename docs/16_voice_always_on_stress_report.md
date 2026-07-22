# Voice Always-on Stress Report

- Generated at: 2026-07-17 17:02:15
- Total scenarios: 40
- Passed: 40
- Failed: 0
- Pass rate: 100.00%

## Scope

This stress test simulates continuous voice, wake conversation, and push-to-talk ingress without using a physical microphone. Each scenario enters the same L3 InputAdapter and VoiceFalseTriggerGuard path used by live voice turns.

Covered categories: valid task, pause/incomplete speech, noise, duplicate fragments, assistant echo, low confidence, speaker verification, pending confirmation, multi-tool intent.

## Results

| # | Category | Scenario | Expected | Actual | OK |
|---:|---|---|---|---|---|
| 1 | valid_task | clear_owner_open_browser | allow / accepted | allow / accepted | PASS |
| 2 | valid_task | clear_owner_open_calculator | allow / accepted | allow / accepted | PASS |
| 3 | push_to_talk | ptt_lowish_command_allowed | allow / accepted | allow / accepted | PASS |
| 4 | valid_task | wake_owner_message_task | allow / accepted | allow / accepted | PASS |
| 5 | valid_chat | side_chat_high_confidence | allow / accepted | allow / accepted | PASS |
| 6 | pause_incomplete | pause_only_open | drop / voice_session_bare_action_without_target | drop / voice_session_bare_action_without_target | PASS |
| 7 | pause_incomplete | pause_only_send | drop / voice_session_ends_with_missing_slot | drop / voice_session_ends_with_missing_slot | PASS |
| 8 | pause_incomplete | provisional_partial_browser | drop / voice_session_stt_not_finalized | drop / voice_session_stt_not_finalized | PASS |
| 9 | pause_incomplete | provisional_not_finalized | drop / voice_session_stt_not_finalized | drop / voice_session_stt_not_finalized | PASS |
| 10 | pause_recovery | final_after_pause | allow / accepted | allow / accepted | PASS |
| 11 | noise | filler_um | drop / filler_or_backchannel | drop / filler_or_backchannel | PASS |
| 12 | noise | filler_ah | drop / filler_or_backchannel | drop / filler_or_backchannel | PASS |
| 13 | noise | english_test_noise | drop / filler_or_backchannel | drop / filler_or_backchannel | PASS |
| 14 | noise | short_object_noise | drop / background_noise_fragment | drop / background_noise_fragment | PASS |
| 15 | noise | low_confidence_background | drop / low_confidence_non_action | drop / low_confidence_non_action | PASS |
| 16 | echo | assistant_playback_echo | drop / assistant_playback_echo | drop / assistant_playback_echo | PASS |
| 17 | echo | tts_playing_echo | drop / assistant_playback_echo | drop / assistant_playback_echo | PASS |
| 18 | duplicate | duplicate_recent | drop / duplicate_fragment | drop / duplicate_fragment | PASS |
| 19 | duplicate | duplicate_without_timestamp | drop / duplicate_fragment | drop / duplicate_fragment | PASS |
| 20 | confidence | low_confidence_action | confirm / low_confidence_action | confirm / low_confidence_action | PASS |
| 21 | confidence | low_confidence_send | confirm / low_confidence_action | confirm / low_confidence_action | PASS |
| 22 | confidence | risky_no_confidence | confirm / risky_action_requires_voice_confirmation | confirm / risky_action_requires_voice_confirmation | PASS |
| 23 | speaker | non_owner_bool_false | drop / non_owner_speaker | drop / non_owner_speaker | PASS |
| 24 | speaker | non_owner_explicit_reject | drop / non_owner_speaker | drop / non_owner_speaker | PASS |
| 25 | speaker | strict_score_low_reject | drop / non_owner_speaker | drop / non_owner_speaker | PASS |
| 26 | speaker | ambiguous_speaker_action | confirm / speaker_verification_ambiguous | confirm / speaker_verification_ambiguous | PASS |
| 27 | speaker | profile_missing_action | confirm / speaker_verification_ambiguous | confirm / speaker_verification_ambiguous | PASS |
| 28 | speaker | owner_verified_action | allow / accepted | allow / accepted | PASS |
| 29 | speaker | owner_track_ratio_low_strict | drop / non_owner_speaker | drop / non_owner_speaker | PASS |
| 30 | speaker | owner_track_ratio_low_non_strict | allow / accepted | allow / accepted | PASS |
| 31 | pending | confirmed_pending_skip_once | allow / confirmed_pending_voice | allow / confirmed_pending_voice | PASS |
| 32 | chat | normal_question_owner | allow / accepted | allow / accepted | PASS |
| 33 | multi_tool | web_research_owner | allow / accepted | allow / accepted | PASS |
| 34 | multi_tool | file_reveal_owner | allow / accepted | allow / accepted | PASS |
| 35 | noise | noisy_action_with_music | confirm / low_confidence_action | confirm / low_confidence_action | PASS |
| 36 | valid_task | english_command | allow / accepted | allow / accepted | PASS |
| 37 | pause_incomplete | english_incomplete | drop / voice_session_bare_action_without_target | drop / voice_session_bare_action_without_target | PASS |
| 38 | confidence | english_low_conf | confirm / low_confidence_action | confirm / low_confidence_action | PASS |
| 39 | chat | ambient_long_high_confidence | allow / accepted | allow / accepted | PASS |
| 40 | noise | ambient_long_low_confidence | drop / low_confidence_non_action | drop / low_confidence_non_action | PASS |

## Failure Details

No failures in this run.

## Findings

- Continuous mode must treat incomplete action fragments as non-executable, not as normal chat.
- Speaker verification should be enforced twice: Rust/JVS owner-track first, L3 guard second for any bypassed or simulated input.
- Push-to-talk can use a more permissive confidence threshold because the user intentionally started recording.
- Evidence must include guard reason codes so false positives can be debugged without watching the UI.

## Next Focus

- Run live microphone tests for owner/non-owner voice after an owner voiceprint is enrolled.
- Add utterance aggregation if product experience requires combining short pauses into one task instead of dropping incomplete fragments.
- Feed repeated guard blocks into Memory/FailureLearning so the system can adapt thresholds per user environment.
