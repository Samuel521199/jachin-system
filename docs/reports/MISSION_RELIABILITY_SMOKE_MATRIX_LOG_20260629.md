# Mission Reliability Smoke Matrix Log

Date: 2026-06-29
Project: Jachin OS Assistant
Purpose: Verify that OS mission workflows are repeatable, observable, and diagnosable, not just one-off successful demos.

## Test Scope

| Scenario | Planned Runs | Target | Workflow | Evidence Required |
| --- | ---: | --- | --- | --- |
| Vivian single send | 5 | Vivian | Lark verified message send | Router evidence, tool evidence, screenshot/OCR, duration |
| Vivian + Samuel send | 3 | Vivian, Samuel | Lark multi-recipient send | Router evidence, tool evidence, per-recipient verification |
| Vivian + group send | 3 | Vivian, 测试备注冒烟草稿 | Lark mixed single/group send | Router evidence, tool evidence, OCR send result |
| Codex project briefing | 5 | Configured recipient(s) | Codex project summary -> Lark | Codex output, report md, Lark send verification |
| App switch matrix | 5 | Codex, Lark, browser, Explorer | Windows app open/switch | active window title, screenshot, timing |

## Environment

| Item | Value |
| --- | --- |
| Machine | Windows local desktop |
| Repo | `D:\Projects\jachi\jachin-system-main` |
| Jachin build/version | TBD |
| Lark login state | TBD |
| Codex login state | TBD |
| Confirmation mode | TBD |
| Evidence root | `D:\Projects\jachi\jachin-system-main\output` |
| Tester | TBD |

## Success Criteria

- Each run must write an Evidence JSON file.
- Each run must record start time, end time, duration, workflow id, target recipients/apps, and final status.
- Lark send runs must include screenshot/OCR or equivalent visual verification.
- Codex briefing runs must include the prompt, Codex output/report path, copied content validation, and Lark send verification.
- App switch runs must verify the foreground window title or visual state after each switch.
- Failures must identify the blocked stage, failure class, retry decision, and evidence path.

## Summary

| Scenario | Runs | Passed | Failed | Success Rate | Avg Duration | Top Failure |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| Vivian single send | 0/5 | 0 | 0 | TBD | TBD | TBD |
| Vivian + Samuel send | 0/3 | 0 | 0 | TBD | TBD | TBD |
| Vivian + group send | 0/3 | 0 | 0 | TBD | TBD | TBD |
| Codex project briefing | 0/5 | 0 | 0 | TBD | TBD | TBD |
| App switch matrix | 0/5 | 0 | 0 | TBD | TBD | TBD |

## Failure Taxonomy

| Failure Class | Meaning | Typical Fix |
| --- | --- | --- |
| `intent_not_matched` | User input did not route to the expected mission | Improve semantic parser or template mapping |
| `preview_not_confirmed` | Task stopped at preview/confirmation | Confirm execution or adjust confirmation policy |
| `codex_open_failed` | Codex window could not be opened/focused | App switch retry, window title detection |
| `codex_output_invalid` | Codex output empty or missing expected fields | Wait longer, recopy, rerun prompt |
| `lark_open_failed` | Lark could not be opened/focused | App switch retry, login/session check |
| `recipient_not_verified` | Recipient search did not land on expected chat | Search strategy, alias memory, OCR check |
| `message_not_verified` | Sent message not visible after send | Retry policy, send button detection, OCR confirmation |
| `app_focus_failed` | Target app did not become foreground window | Window enumeration/focus fallback |
| `timeout` | Run exceeded expected wait time | Increase wait time or add stage-specific waits |
| `unknown` | Failure did not match known classes | Inspect Evidence JSON and screenshot |

## Run Details

### Scenario A: Vivian Single Send, 5 Runs

#### A-01

| Field | Value |
| --- | --- |
| Command/Input | TBD |
| Start Time | TBD |
| End Time | TBD |
| Duration | TBD |
| Result | TBD |
| Failure Class | TBD |
| Retry Count | TBD |
| Router Evidence | TBD |
| Tool Evidence | TBD |
| Screenshot/OCR Evidence | TBD |
| Notes | TBD |

#### A-02

| Field | Value |
| --- | --- |
| Command/Input | TBD |
| Start Time | TBD |
| End Time | TBD |
| Duration | TBD |
| Result | TBD |
| Failure Class | TBD |
| Retry Count | TBD |
| Router Evidence | TBD |
| Tool Evidence | TBD |
| Screenshot/OCR Evidence | TBD |
| Notes | TBD |

#### A-03

| Field | Value |
| --- | --- |
| Command/Input | TBD |
| Start Time | TBD |
| End Time | TBD |
| Duration | TBD |
| Result | TBD |
| Failure Class | TBD |
| Retry Count | TBD |
| Router Evidence | TBD |
| Tool Evidence | TBD |
| Screenshot/OCR Evidence | TBD |
| Notes | TBD |

#### A-04

| Field | Value |
| --- | --- |
| Command/Input | TBD |
| Start Time | TBD |
| End Time | TBD |
| Duration | TBD |
| Result | TBD |
| Failure Class | TBD |
| Retry Count | TBD |
| Router Evidence | TBD |
| Tool Evidence | TBD |
| Screenshot/OCR Evidence | TBD |
| Notes | TBD |

#### A-05

| Field | Value |
| --- | --- |
| Command/Input | TBD |
| Start Time | TBD |
| End Time | TBD |
| Duration | TBD |
| Result | TBD |
| Failure Class | TBD |
| Retry Count | TBD |
| Router Evidence | TBD |
| Tool Evidence | TBD |
| Screenshot/OCR Evidence | TBD |
| Notes | TBD |

### Scenario B: Vivian + Samuel, 3 Runs

#### B-01

| Field | Value |
| --- | --- |
| Command/Input | TBD |
| Start Time | TBD |
| End Time | TBD |
| Duration | TBD |
| Result | TBD |
| Failure Class | TBD |
| Retry Count | TBD |
| Router Evidence | TBD |
| Tool Evidence | TBD |
| Screenshot/OCR Evidence | TBD |
| Notes | TBD |

#### B-02

| Field | Value |
| --- | --- |
| Command/Input | TBD |
| Start Time | TBD |
| End Time | TBD |
| Duration | TBD |
| Result | TBD |
| Failure Class | TBD |
| Retry Count | TBD |
| Router Evidence | TBD |
| Tool Evidence | TBD |
| Screenshot/OCR Evidence | TBD |
| Notes | TBD |

#### B-03

| Field | Value |
| --- | --- |
| Command/Input | TBD |
| Start Time | TBD |
| End Time | TBD |
| Duration | TBD |
| Result | TBD |
| Failure Class | TBD |
| Retry Count | TBD |
| Router Evidence | TBD |
| Tool Evidence | TBD |
| Screenshot/OCR Evidence | TBD |
| Notes | TBD |

### Scenario C: Vivian + Group, 3 Runs

#### C-01

| Field | Value |
| --- | --- |
| Command/Input | TBD |
| Start Time | TBD |
| End Time | TBD |
| Duration | TBD |
| Result | TBD |
| Failure Class | TBD |
| Retry Count | TBD |
| Router Evidence | TBD |
| Tool Evidence | TBD |
| Screenshot/OCR Evidence | TBD |
| Notes | TBD |

#### C-02

| Field | Value |
| --- | --- |
| Command/Input | TBD |
| Start Time | TBD |
| End Time | TBD |
| Duration | TBD |
| Result | TBD |
| Failure Class | TBD |
| Retry Count | TBD |
| Router Evidence | TBD |
| Tool Evidence | TBD |
| Screenshot/OCR Evidence | TBD |
| Notes | TBD |

#### C-03

| Field | Value |
| --- | --- |
| Command/Input | TBD |
| Start Time | TBD |
| End Time | TBD |
| Duration | TBD |
| Result | TBD |
| Failure Class | TBD |
| Retry Count | TBD |
| Router Evidence | TBD |
| Tool Evidence | TBD |
| Screenshot/OCR Evidence | TBD |
| Notes | TBD |

### Scenario D: Codex Project Briefing, 5 Runs

#### D-01

| Field | Value |
| --- | --- |
| Command/Input | TBD |
| Project | TBD |
| Start Time | TBD |
| End Time | TBD |
| Duration | TBD |
| Result | TBD |
| Failure Class | TBD |
| Retry Count | TBD |
| Codex Prompt Evidence | TBD |
| Codex Output/Report | TBD |
| Router Evidence | TBD |
| Tool Evidence | TBD |
| Lark Screenshot/OCR | TBD |
| Notes | TBD |

#### D-02

| Field | Value |
| --- | --- |
| Command/Input | TBD |
| Project | TBD |
| Start Time | TBD |
| End Time | TBD |
| Duration | TBD |
| Result | TBD |
| Failure Class | TBD |
| Retry Count | TBD |
| Codex Prompt Evidence | TBD |
| Codex Output/Report | TBD |
| Router Evidence | TBD |
| Tool Evidence | TBD |
| Lark Screenshot/OCR | TBD |
| Notes | TBD |

#### D-03

| Field | Value |
| --- | --- |
| Command/Input | TBD |
| Project | TBD |
| Start Time | TBD |
| End Time | TBD |
| Duration | TBD |
| Result | TBD |
| Failure Class | TBD |
| Retry Count | TBD |
| Codex Prompt Evidence | TBD |
| Codex Output/Report | TBD |
| Router Evidence | TBD |
| Tool Evidence | TBD |
| Lark Screenshot/OCR | TBD |
| Notes | TBD |

#### D-04

| Field | Value |
| --- | --- |
| Command/Input | TBD |
| Project | TBD |
| Start Time | TBD |
| End Time | TBD |
| Duration | TBD |
| Result | TBD |
| Failure Class | TBD |
| Retry Count | TBD |
| Codex Prompt Evidence | TBD |
| Codex Output/Report | TBD |
| Router Evidence | TBD |
| Tool Evidence | TBD |
| Lark Screenshot/OCR | TBD |
| Notes | TBD |

#### D-05

| Field | Value |
| --- | --- |
| Command/Input | TBD |
| Project | TBD |
| Start Time | TBD |
| End Time | TBD |
| Duration | TBD |
| Result | TBD |
| Failure Class | TBD |
| Retry Count | TBD |
| Codex Prompt Evidence | TBD |
| Codex Output/Report | TBD |
| Router Evidence | TBD |
| Tool Evidence | TBD |
| Lark Screenshot/OCR | TBD |
| Notes | TBD |

### Scenario E: App Open/Switch Matrix, 5 Runs

#### E-01

| Field | Value |
| --- | --- |
| Command/Input | TBD |
| Apps | Codex, Lark, browser, Explorer |
| Start Time | TBD |
| End Time | TBD |
| Duration | TBD |
| Result | TBD |
| Failure Class | TBD |
| Retry Count | TBD |
| Active Window Evidence | TBD |
| Screenshot Evidence | TBD |
| Notes | TBD |

#### E-02

| Field | Value |
| --- | --- |
| Command/Input | TBD |
| Apps | Codex, Lark, browser, Explorer |
| Start Time | TBD |
| End Time | TBD |
| Duration | TBD |
| Result | TBD |
| Failure Class | TBD |
| Retry Count | TBD |
| Active Window Evidence | TBD |
| Screenshot Evidence | TBD |
| Notes | TBD |

#### E-03

| Field | Value |
| --- | --- |
| Command/Input | TBD |
| Apps | Codex, Lark, browser, Explorer |
| Start Time | TBD |
| End Time | TBD |
| Duration | TBD |
| Result | TBD |
| Failure Class | TBD |
| Retry Count | TBD |
| Active Window Evidence | TBD |
| Screenshot Evidence | TBD |
| Notes | TBD |

#### E-04

| Field | Value |
| --- | --- |
| Command/Input | TBD |
| Apps | Codex, Lark, browser, Explorer |
| Start Time | TBD |
| End Time | TBD |
| Duration | TBD |
| Result | TBD |
| Failure Class | TBD |
| Retry Count | TBD |
| Active Window Evidence | TBD |
| Screenshot Evidence | TBD |
| Notes | TBD |

#### E-05

| Field | Value |
| --- | --- |
| Command/Input | TBD |
| Apps | Codex, Lark, browser, Explorer |
| Start Time | TBD |
| End Time | TBD |
| Duration | TBD |
| Result | TBD |
| Failure Class | TBD |
| Retry Count | TBD |
| Active Window Evidence | TBD |
| Screenshot Evidence | TBD |
| Notes | TBD |

## Investigation Notes

Use this section for cross-run patterns, such as repeated recipient search failures, OCR instability, app focus issues, Codex copy mistakes, or confirmation/pending task state problems.

- TBD

## Final Conclusion

TBD
