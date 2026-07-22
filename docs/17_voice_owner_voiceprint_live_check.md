# Voice Owner Voiceprint Live Check

- Generated at: 2026-07-17 16:37:12
- Overall: NEEDS_ACTION
- Reason: owner_voiceprint_profile,jvs_health

## Owner Profile
- Path: `C:\Users\Legion\.jachin\voice\owner_voiceprint.json`
- Exists: False
- Valid: False
- Centroid length: 0

## JVS Health
- Base URL: `http://127.0.0.1:18990`
- OK: False
- Elapsed: 2031.4 ms
- Detail: connection_refused

## Recent Live Evidence
- Counters: `{"owner_accept": 0, "owner_reject": 0, "owner_drop_utterance": 0, "wake_accept": 0, "wake_reject": 0, "ptt_owner_track": 0, "ptt_fast_bypass": 8, "profile_missing": 9, "jvs_fail": 0}`

Recent lines:
- `voice_chat.log: 1783933341648ms [pid=17512] [trace=4f5d5379] [stage=sv.owner_track_ptt_fast_bypass] sv.owner_track_ptt_fast_bypass | {"iso":"2026-07-13T09:02:21.643Z","elapsedMs":9311,"sincePrevMs":6,"profile":"chat_ptt","webview":"chat","reason":"companio`
- `voice_chat.log: 1783933421912ms [pid=17512] [trace=3d19ba50] [stage=sv.owner_track_ptt_fast_bypass] sv.owner_track_ptt_fast_bypass | {"iso":"2026-07-13T09:03:41.909Z","elapsedMs":7216,"sincePrevMs":318,"profile":"chat_ptt","webview":"chat","reason":"compan`
- `voice_chat.log: 1783933432264ms [pid=17512] [trace=ca79186e] [stage=sv.owner_track_ptt_fast_bypass] sv.owner_track_ptt_fast_bypass | {"iso":"2026-07-13T09:03:52.262Z","elapsedMs":4962,"sincePrevMs":14,"profile":"chat_ptt","webview":"chat","reason":"compani`
- `voice_chat.log: 1783993084126ms [pid=73456] [trace=2bac2d18] [stage=sv.owner_track_ptt_fast_bypass] sv.owner_track_ptt_fast_bypass | {"iso":"2026-07-14T01:38:04.102Z","elapsedMs":5207,"sincePrevMs":348,"profile":"chat_ptt","webview":"chat","reason":"compan`
- `voice_chat.log: 1783993098653ms [pid=73456] [trace=67f672c6] [stage=sv.owner_track_ptt_fast_bypass] sv.owner_track_ptt_fast_bypass | {"iso":"2026-07-14T01:38:18.646Z","elapsedMs":2132,"sincePrevMs":6,"profile":"chat_ptt","webview":"chat","reason":"companio`
- `voice_chat.log: 1783993666859ms [pid=44620] [trace=bc9a1431] [stage=sv.owner_track_ptt_fast_bypass] sv.owner_track_ptt_fast_bypass | {"iso":"2026-07-14T01:47:46.857Z","elapsedMs":5399,"sincePrevMs":13,"profile":"chat_ptt","webview":"chat","reason":"compani`
- `voice_chat.log: 1783993678594ms [pid=44620] [trace=none] [stage=sv.owner_track_ptt_fast_bypass] sv.owner_track_ptt_fast_bypass | {"iso":"2026-07-14T01:47:58.592Z","profile":"chat_ptt","webview":"chat","reason":"companion_fast_mode_non_strict"}`
- `voice_chat.log: 1783994053221ms [pid=37988] [trace=53f1a372] [stage=sv.owner_track_ptt_fast_bypass] sv.owner_track_ptt_fast_bypass | {"iso":"2026-07-14T01:54:13.208Z","elapsedMs":4390,"sincePrevMs":3,"profile":"chat_ptt","webview":"chat","reason":"companio`

## Adaptive Learning
- Learning samples: 23
- Thresholds: `{"continuous_action_confirm_threshold": 0.55, "continuous_non_action_drop_threshold": 0.38, "ptt_action_confirm_threshold": 0.35, "ptt_non_action_drop_threshold": 0.22, "risky_continuous_confirm_threshold": 0.72, "risky_ptt_confirm_threshold": 0.55, "speaker_ambiguous_requires_confirmation": true, "adaptive": true, "sample_count": 23, "reason_counts": {"filler_or_backchannel": 2, "low_confidence_action": 2, "accepted": 3, "duplicate_fragment": 2, "confirmed_pending_voice": 2, "stt_not_finalized": 2, "incomplete_action_fragment": 2, "non_owner_speaker": 2, "speaker_verification_ambiguous": 2, "low_confidence_non_action": 2, "owner_validation_not_ready": 2}, "learning_path": "C:\\Users\\Legion\\.jachin\\cognitive_kernel\\state\\voice_false_trigger_learning.jsonl"}`

## Next Action
- If profile is missing: open Jachin Console -> Wake Mode -> enroll 3 owner samples.
- If owner pass evidence is missing: say one safe command in always-on mode and rerun with `--expect-owner-pass`.
- If non-owner block evidence is missing: let a non-owner/noise source speak near the mic and rerun with `--expect-non-owner-block`.
