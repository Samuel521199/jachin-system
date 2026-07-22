# Jachin Core System Handoff for Codex

Created at: 2026-07-19

Audience: another Codex window that needs to analyze the current Jachin system from architecture level before making concrete changes.

This document is a technical handoff, not a product pitch. It summarizes the current core architecture, major modules, execution path, memory system, intent recognition, tool execution, recovery, evidence, voice input, capability system, tests, known risks, and suggested analysis entry points.

## 1. Product Direction

Jachin is being pushed toward an OS-level AI assistant, not a simple chat bot and not a hard-coded automation script.

The intended behavior is:

- Understand user goals from natural language, voice, Lark, and system events.
- Use memory, current state, and available capabilities before planning.
- Decompose complex goals into structured tasks.
- Execute through authorized WorkOrders, not through free-form tool calls.
- Verify real-world effects instead of claiming success from intent alone.
- Recover from failures by using failure reasons, capability metadata, and learned playbooks.
- Write useful memory back so the system becomes better over time.
- Keep business skills independent from the main kernel.

The central design principle is:

```text
Input is flexible.
Decision is centralized.
Execution is role-based.
Verification is mandatory.
Memory is cumulative.
Business logic belongs to Skill/MCP packages.
```

## 2. Current Active Mainline

The current target runtime model is:

```text
AgentInputEnvelope
-> StateFabric
-> MemoryRecall
-> GoalInterpreter
-> ReviewBoard
-> Arbiter
-> DecisionContract
-> TaskDecomposer
-> WorkOrder DAG
-> Dispatcher
-> RoleExecutor
-> VerificationReport
-> RecoveryPlan if needed
-> TurnClosure
-> MemoryWrite / FailureLearning / Evidence
```

This is the architecture that should be protected. New features should plug into it instead of creating side channels.

Important files:

```text
l3_node/cognitive_kernel/contracts.py
l3_node/cognitive_kernel/pipeline.py
l3_node/cognitive_kernel/input_adapter.py
l3_node/cognitive_kernel/goal_interpreter.py
l3_node/cognitive_kernel/review_board.py
l3_node/cognitive_kernel/arbiter.py
l3_node/cognitive_kernel/task_decomposer.py
l3_node/cognitive_kernel/dispatcher.py
l3_node/cognitive_kernel/role_executors.py
l3_node/cognitive_kernel/runtime.py
l3_node/cognitive_kernel/recovery_planner.py
l3_node/cognitive_kernel/memory_recall_agent.py
l3_node/cognitive_kernel/memory_lifecycle.py
l3_node/cognitive_kernel/failure_learning_loop.py
```

## 3. Non-Negotiable Architecture Rules

1. No external-world mutation without WorkOrder.
2. Tools, MCPs, Skills, app operations, file operations, messages, and memory writes must go through Dispatcher and RoleExecutor.
3. The Cognitive Kernel decides and authorizes; it should not become a business-state-machine dump.
4. Verification must be separate from execution.
5. Failure must not be hidden. Failed execution should produce structured failure reasons and recovery evidence.
6. Memory writes should be explicit and typed.
7. User-confirmed memory, inferred memory, rejected memory, and expired memory must be treated differently.
8. Voice and text are input modalities only; after InputAdapter they should share the same mainline.
9. Skill/MCP metadata should drive routing, decomposition, validation, and recovery as much as possible.
10. Evidence Console should be able to replay what happened.

## 4. Input Unification

All user-visible inputs should be normalized into a shared envelope.

Input sources include:

- Desktop text chat.
- Voice always-on mode.
- Push-to-talk voice mode.
- Lark long-connection messages.
- System or scheduled events.
- Future app triggers.

Core file:

```text
l3_node/cognitive_kernel/input_adapter.py
```

The adapter preserves:

- raw text
- normalized text
- source
- session id
- confidence
- modality evidence
- voice endpointing evidence
- false-trigger guard evidence
- voice correction and replan evidence

Voice-specific preprocessing should happen before shared planning:

```text
ASR text
-> session endpointing
-> false-trigger / speaker guard
-> language normalization
-> interruption / replan detection
-> AgentInputEnvelope
```

After that, voice should share the same memory, goal interpretation, task decomposition, dispatcher, recovery, and evidence logic as text.

Voice-related files:

```text
l3_node/voice_session_endpointing.py
l3_node/voice_false_trigger_guard.py
l3_node/voice_false_trigger_learning.py
l3_node/voice_interruption_agent.py
l3_node/voice_task_replan.py
l3_node/voice_task_handle_registry.py
l3_node/voice_evidence_agent.py
l3_node/cognitive_kernel/voice_pending_confirmation.py
```

Voice test/log documents:

```text
docs/15_voice_always_on_upgrade_plan.md
docs/16_voice_always_on_stress_report.md
docs/17_voice_owner_voiceprint_live_check.md
docs/19_voice_unified_input_live_test_log.md
```

Current voice baseline:

- Simulated always-on pressure matrix: 40/40 passed.
- Voice unit tests: 47 passed.
- Real owner voiceprint live validation still depends on enrolled owner profile and live microphone/JVS evidence.

## 5. Intent Recognition

Intent recognition is not supposed to be a fixed keyword state machine.

Current intent recognition is composed from:

1. User text or normalized voice text.
2. Conversation context.
3. State snapshot.
4. Memory recall.
5. Capability metadata and Skill/MCP manifests.
6. Semantic candidates.
7. ReviewBoard evidence.
8. Arbiter final decision.

Important files:

```text
l3_node/cognitive_kernel/goal_interpreter.py
l3_node/cognitive_kernel/review_board.py
l3_node/cognitive_kernel/semantic_intent_agent.py
l3_node/capability_semantic_registry.py
l3_node/cognitive_kernel/entity_corrections.py
l3_node/cognitive_kernel/arbiter.py
```

Typical supported intent families:

- app control: open, close, switch, focus
- message delivery: send to Lark contact or group
- file operation: read, open, reveal, copy, move, rename, delete with confirmation
- calculator / native app workflows
- web research delivery: search -> fetch -> summarize -> send
- project briefing delivery: Codex project summary -> Lark
- memory correction and user preference learning
- voice interruption or active-task correction

Important behavior:

- If the user says `lock` and past confirmation says it meant `Lark`, the system should use memory to map it.
- If confidence is low, the system should ask one clarification, not keep guessing.
- User confirmation should become memory.
- User rejection should suppress or heavily downgrade future recalls.
- New capabilities should become discoverable through manifest metadata rather than code changes in the core loop.

Known risk:

- Natural language still needs broader real-world coverage.
- Ambiguous short commands need careful use of recent action memory.
- Voice ASR errors can still create wrong entities if correction memory is missing.

## 6. Capability Intelligence Layer

Jachin is moving toward manifest-driven capability routing.

Capability metadata should describe:

- capability id
- capability type: Skill, MCP, Model, Tool
- input slots
- supported intents
- examples
- required MCPs
- required models
- preconditions
- decomposition nodes
- verification criteria
- recovery playbook
- quality gates
- risk level

Important files:

```text
l3_node/cognitive_kernel/capability_work_order_adapter.py
l3_node/cognitive_kernel/capability_hook_bridge.py
l3_node/cognitive_kernel/capability_contract_validator.py
l3_node/cognitive_kernel/capability_intelligence.py
l3_node/cognitive_kernel/capability_recovery_registry.py
l3_node/cognitive_kernel/recovery_playbook_schema.py
l3_node/cognitive_kernel/capability_governance_policy.py
```

Current expectation:

- Skill/MCP manifests participate in intent matching.
- Manifest decomposition can generate TaskDAG nodes.
- Manifest recovery_playbook can be consumed by RecoveryPlanner.
- Publish/install/startup scan should validate contract quality.
- Low-quality capabilities can be installed for testing but should be downgraded, warned, or gated at runtime.

## 7. Task Decomposition

TaskDecomposer converts a user goal into one or more task nodes.

Each node should contain:

- goal
- role_agent
- tool or capability
- inputs
- depends_on
- risk_level
- verification_criteria
- recovery_policy

Important files:

```text
l3_node/cognitive_kernel/task_decomposer.py
l3_node/cognitive_kernel/task_dag.py
l3_node/cognitive_kernel/arbiter.py
```

Examples:

```text
"打开计算器，计算 99+100"
-> open Calculator
-> calculate expression
-> verify result

"搜索最新 AI 模型消息，发给 Neil"
-> web search
-> fetch pages
-> per-page summary
-> final brief composer
-> quality gate
-> open/focus Lark
-> send message
-> verify delivery

"总结 Jachin 最近开发了什么，发给 Neil"
-> resolve project path from memory
-> call Codex project briefing workflow
-> capture Codex output
-> quality check
-> send Lark
-> verify
```

Known risk:

- DAG execution is strongest in explicit direct-mainline flows.
- More Skill/MCP manifests need richer decomposition metadata.

## 8. Tool Invocation

Tool invocation should never be the planning boundary.

Correct path:

```text
DecisionContract
-> WorkOrder
-> Dispatcher
-> RoleExecutor
-> Tool/MCP/Skill/native API
-> VerificationReport
```

Important files:

```text
l3_node/cognitive_kernel/dispatcher.py
l3_node/cognitive_kernel/role_executors.py
l3_node/cognitive_kernel/tool_input_adapter.py
l3_node/cognitive_kernel/transport_errors.py
l3_node/cognitive_kernel/direct_mainline.py
```

Role executor families:

- AppControlExecutorAgent
- BrowserExecutorAgent
- FileExecutorAgent
- MessageExecutorAgent
- OSExecutorAgent
- ToolExecutionAgent
- MemoryRecallAgent
- MemoryWriteAgent
- VerificationAgent
- RecoveryAgent
- UserFacingReplyAgent

Key rule:

If a tool returns `ok=true` but verification evidence is missing, the final user reply must not claim success.

This rule was especially important for:

- Lark sending
- browser open/focus
- calculator visual verification
- file reveal/open
- web research summaries

## 9. Verification

Verification is required for any external action.

Verification examples:

- App opened: window/process/focus/screenshot evidence.
- App closed: window disappears or process state changes.
- Lark message sent: UI/API/OCR/post-send evidence.
- Calculator result: visual/OCR result evidence.
- File reveal/open: Explorer/window/file evidence.
- Web research: readable source pages, no bot wall, no truncated summary, valid links.

Important files:

```text
l3_node/cognitive_kernel/verification.py
l3_node/cognitive_kernel/role_executors.py
clients/desktop/src-tauri/src/commands/os_evidence.rs
clients/desktop/src/console/pages/OsEvidencePanel.tsx
```

Known risk:

- Some live UI verification still depends on Windows focus stability and OCR/screenshot quality.
- If a capability lacks verification metadata, the runtime should degrade confidence and surface warnings.

## 10. Failure Recovery and Retry

Recovery is being upgraded from fixed fallback paths to adaptive, learned recovery.

Desired model:

```text
Attempt A fails
-> classify failure reason
-> consult capability recovery_playbook
-> consult learned failure memory
-> choose next path B

Attempt B fails
-> combine A+B evidence
-> choose path C based on accumulated failure reasons

Max attempts reached
-> stop
-> report tried paths, failure reasons, next recommendation
-> write failure learning
```

Important files:

```text
l3_node/cognitive_kernel/recovery_planner.py
l3_node/cognitive_kernel/failure_learning_loop.py
l3_node/cognitive_kernel/capability_recovery_registry.py
l3_node/cognitive_kernel/tool_quality.py
l3_node/cognitive_kernel/task_memory.py
```

Current recovery capabilities:

- classifies failure reason
- uses capability recovery playbooks
- uses prior failure evidence
- uses adaptive scorecards
- respects max attempts
- writes failure hints
- can produce final failure summary

Quality gates:

- Do not retry high-risk operations blindly.
- Do not repeat the same failed path if the reason says it cannot work.
- Prefer switching strategy when the same failure pattern repeats.
- Ask the user when the next recovery action changes risk level or needs new information.

Known risk:

- More capabilities need complete recovery_playbook metadata.
- Live recovery with multiple real tools still needs broader coverage.

## 11. Memory System

The memory system is now more than chat history or basic RAG.

Current memory categories include:

- short-term action memory
- long-term user preferences
- task summaries
- tool habits
- failure hints
- entity corrections
- capability playbooks
- concepts
- outputs
- trust-weighted memories

Important files:

```text
l3_node/cognitive_kernel/memory_lifecycle.py
l3_node/cognitive_kernel/memory_recall_agent.py
l3_node/cognitive_kernel/memory_growth.py
l3_node/cognitive_kernel/daily_review.py
l3_node/cognitive_kernel/concept_curator.py
l3_node/cognitive_kernel/playbook_builder.py
l3_node/cognitive_kernel/output_review.py
l3_node/cognitive_kernel/memory_trust_layer.py
l3_node/cognitive_kernel/entity_corrections.py
l3_node/cognitive_kernel/task_memory.py
```

Important docs:

```text
docs/09_ai_self_growing_knowledge_system_plan.md
docs/10_ai_self_growing_knowledge_system_execution_log.md
docs/11_memory_governance_and_confidence_architecture.md
docs/13_memory_optimization_mvp_test_report.md
docs/14_memory_recall_precision_100k_report.md
docs/15_memory_recall_precision_1m_report.md
```

Memory recall model:

1. Fast candidate recall by keyword / inverted index.
2. Rule scoring by match count, confidence, recency, trust, success history, freshness.
3. Semantic reranking by normalized vector dot product where available.

Trust states:

- confirmed: user explicitly confirmed; should score higher.
- inferred: system inferred; usable but less authoritative.
- floating: neither confirmed nor rejected; normal confidence.
- needs_confirmation: should trigger clarification in risky contexts.
- rejected: should be filtered or scored extremely low.
- expired: should be downgraded or ignored unless explicitly needed for history.

Memory Growth model:

```text
Raw evidence
-> Daily Review
-> Concepts
-> Playbooks
-> Outputs
-> Wiki / method layer
-> Later recall
-> New execution
-> New raw evidence
```

Current test evidence:

- Memory MVP test report says the memory loop can write, dedupe, expire, recall, review, promote, and influence recovery.
- 100k and 1m recall precision reports exist.
- User correction such as `lock -> Lark` can influence later ReviewBoard behavior.

Known risk:

- Memory quality governance still needs long-running real-world validation.
- Need more hard tests for conflicting memories and rejected memory suppression in live tasks.

## 12. Output Quality Control

Output quality became important after web research summaries exposed weak outputs such as truncated text, page fragments, Markdown artifacts, and bad links.

Current goal:

```text
search
-> fetch
-> per-page model summary
-> final model brief composer
-> quality gate
-> Lark delivery
```

Important files:

```text
l3_node/cognitive_kernel/tool_quality.py
l3_node/cognitive_kernel/task_decomposer.py
l3_node/cognitive_kernel/recovery_planner.py
docs/17_failure_learning_tool_quality_intent_generalization_strategy.md
docs/18_stage5_pressure_matrix_test_report.md
```

Quality problems that must be blocked:

- missing source title
- inaccessible page
- login wall, captcha, bot wall, 403, Access Denied
- CSS/HTML noise
- Markdown nested-link artifacts
- code block/table fragments
- ellipsis truncation
- no source links
- half-sentence final output

Known risk:

- qwen-turbo is useful for fast simple steps, but higher-quality web briefing may require a stronger model.
- Final user-facing briefs should be written like human-readable updates, not raw extractor output.

## 13. Evidence System

Evidence is a first-class product surface.

It should show:

- recognized intent
- selected workflow
- memory used
- DecisionContract
- WorkOrders
- RoleExecutions
- VerificationReports
- RecoveryPlans
- FailureLearning records
- ToolQuality reports
- screenshots / OCR / file paths where available
- final TurnClosure

Important files:

```text
clients/desktop/src/console/pages/OsEvidencePanel.tsx
clients/desktop/src-tauri/src/commands/os_evidence.rs
l3_node/cognitive_kernel/ledger.py
l3_node/cognitive_kernel/runtime.py
l3_node/cognitive_kernel/failure_learning_loop.py
```

Known risk:

- Evidence Console is strong for diagnosis, but every new execution path must keep writing structured evidence.
- If a flow only replies to the user but has no ledger/verification, it should be treated as suspect.

## 14. L1/L3 Capability Release and Install

Jachin uses L1 as cloud capability catalog and L3 as local runtime.

The desired flow:

```text
Developer L3 publishes Skill/MCP/Model package to L1.
Packaged user L3 connects to selected L1 profile.
Install Center compares L1 catalog with local installed registry.
User installs a business Skill.
Required MCPs and models install together.
Installed capability appears in local console only if actually installed.
Runtime loads capability metadata and executes through the kernel.
```

Important concepts:

- L3 -> L1 direct publish.
- L2 should be optional, not required.
- Multiple L1 profiles are supported.
- Source-isolated package store is needed so private/test/prod L1 sources do not overwrite each other.
- Business Skill should pull required MCPs and models automatically.

Important files are spread across:

```text
cloud/nexus
clients/desktop/src/console/pages
clients/desktop/src-tauri/src/commands
skills_repo
l3_client/local_mcps
l3_node/cognitive_kernel/capability_contract_validator.py
```

Known risk:

- Real cloud L1 deployment and profile switching need continued regression testing.
- Manifest validation should remain strict enough to prevent broken production capabilities.

## 15. Business Skills and MCP Independence

Business capabilities should be separate from the kernel.

Important business skill families:

- BI 数据增长官
- PMO 项目治理中枢
- AI 招聘总监
- 企业桌面执行 Agent
- 游戏 QA / 自动化测试平台
- 英语学习助手

Main rule:

Business-specific policy belongs in Skill/MCP package, manifest, recovery playbook, or capability hook. It should not be hard-coded into the core agent loop.

Known risk:

- Some historical business code may still exist in older areas and should be reviewed before claiming complete separation.
- Packaged mode should only display installed business capabilities, not everything in the developer repository.

## 16. Desktop / OS Automation

Core OS assistant direction:

- window awareness
- app open/switch/close
- file read/open/reveal and safe mutation
- calculator visual verification
- Lark send/summary/history flows
- browser research
- evidence chain for each step

Important local MCP:

```text
l3_client/local_mcps/windows_uia_mcp
```

Important behavior:

- If user says "close" after opening WeChat, recent action memory should infer WeChat, not randomly close Browser.
- If user says "open browser" and Chrome opens but focus verification fails, success should not be claimed until verification passes or recovery tries another focus path.
- If user says "send to Neil" and no recipient slot is preserved, the system should fail with missing slot, not fake-send.

Known risk:

- Windows UI focus is inherently unstable and needs recovery.
- Lark UI automation needs verification to prevent false sends.

## 17. Voice System Current State

Voice has two modes:

- Always-on: continuously listens, filters noise, supports interruption and replan.
- Push-to-talk: user actively records, confidence threshold can be more permissive.

Voice-specific responsibilities:

- endpointing: determine whether user has finished speaking
- false-trigger guard: avoid background speech / echo / non-owner execution
- language normalizer: fix ASR/entity mistakes
- pending confirmation: low-confidence voice tasks wait for yes/no
- task replan: modify active task from voice
- evidence: every voice decision should be inspectable

Current logs and reports:

```text
docs/16_voice_always_on_stress_report.md
docs/17_voice_owner_voiceprint_live_check.md
docs/19_voice_unified_input_live_test_log.md
```

Known risk:

- Real microphone, real owner voiceprint, background noise, and user pauses need more live data.
- Voice should not become a separate mini-agent; it must remain a first-class input adapter into the same kernel.

## 18. Important Test Reports

Read these first:

```text
docs/13_memory_optimization_mvp_test_report.md
docs/14_intent_tool_memory_combo_test_report.md
docs/14_memory_recall_precision_100k_report.md
docs/15_memory_recall_precision_1m_report.md
docs/16_voice_always_on_stress_report.md
docs/17_failure_learning_tool_quality_intent_generalization_strategy.md
docs/18_stage5_pressure_matrix_test_report.md
docs/19_voice_unified_input_live_test_log.md
```

Current known baselines from recent work:

- Voice unit set: 47 passed.
- Voice simulated always-on stress: 40/40 passed.
- Memory MVP combination test: documented as passing.
- Memory recall large-scale tests: 100k and 1m reports exist.
- Stage 5 pressure matrix exists for intent/tool quality/failure learning.

The next Codex window should verify current test commands before trusting all historical conclusions, because the working tree may have uncommitted changes.

## 19. Current Working Tree Warning

At the time of this handoff, the repo has uncommitted changes related to voice, evidence, desktop UI, and cognitive kernel files.

Important untracked voice files include:

```text
l3_node/voice_session_endpointing.py
l3_node/voice_false_trigger_guard.py
l3_node/voice_false_trigger_learning.py
l3_node/voice_interruption_agent.py
l3_node/voice_task_replan.py
l3_node/voice_task_handle_registry.py
l3_node/voice_evidence_agent.py
l3_node/cognitive_kernel/voice_pending_confirmation.py
scripts/stress_voice_always_on_guard.py
scripts/voice_owner_voiceprint_live_check.py
tests/unit/test_voice_*.py
docs/15_voice_always_on_upgrade_plan.md
docs/16_voice_always_on_stress_report.md
docs/17_voice_owner_voiceprint_live_check.md
docs/19_voice_unified_input_live_test_log.md
```

Do not assume this handoff reflects a clean committed version unless git status is checked.

## 20. Analysis Checklist for the Next Codex Window

Start with these questions:

1. Does every real execution path still go through DecisionContract -> WorkOrder -> Dispatcher -> RoleExecutor?
2. Are there any remaining direct tool-call side channels in `agent_core.py` or IM dispatchers?
3. Are voice and text truly equivalent after InputAdapter?
4. Does TaskDecomposer use capability metadata enough, or still rely too much on built-in rules?
5. Are failed sends, failed app opens, failed browser fetches, and failed calculator checks prevented from claiming success?
6. Does RecoveryPlanner consume both manifest playbooks and learned failure memory?
7. Are rejected memories suppressed in recall?
8. Are confirmed memories scored higher than inferred/floating memories?
9. Does Evidence Console show the whole chain for recent tasks?
10. Does packaged mode hide uninstalled business skills?
11. Can a newly installed Skill bring its required MCPs and models automatically?
12. Are old architecture docs or comments still misleading developers?

## 21. Suggested Immediate Verification Commands

Use `pytest -o addopts=` if coverage plugin or repo-level pytest config causes noise.

```powershell
python -m pytest -q -o addopts= tests\unit\test_voice_interruption_agent.py tests\unit\test_voice_task_handle_registry.py tests\unit\test_voice_task_replan.py tests\unit\test_voice_evidence_agent.py tests\unit\test_voice_entity_correction.py tests\unit\test_voice_followup_policy.py tests\unit\test_voice_false_trigger_guard.py tests\unit\test_voice_pending_confirmation.py tests\unit\test_voice_false_trigger_learning.py tests\unit\test_voice_session_endpointing.py
python scripts\stress_voice_always_on_guard.py
python -m pytest -q -o addopts= tests\unit\test_memory_stress_mvp.py tests\unit\test_memory_deep_mvp.py tests\unit\test_memory_growth.py tests\unit\test_cognitive_kernel_architecture.py tests\unit\test_cognitive_kernel_runtime.py
python -m pytest -q -o addopts= tests\unit\test_stage5_pressure_matrix.py
```

If the next Codex window is doing deep architecture audit, run:

```powershell
rg -n "legacy|ReAct|direct tool|fake|虚假|绕过|side channel|TODO|if False|WorkOrder:" l3_node clients docs
```

Manual live tests should be recorded in:

```text
docs/19_voice_unified_input_live_test_log.md
```

## 22. Current Architecture Rating Snapshot

This is an approximate snapshot, not a final product certification.

| Area | Approx Level | Notes |
|---|---:|---|
| Intent recognition | 78-82 | Better than keyword routing, but more real phrasing needed |
| Tool invocation discipline | 78-83 | WorkOrder path is core direction; still audit for side channels |
| Failure recovery | 75-82 | Adaptive recovery exists; more manifest playbooks needed |
| Memory system | 82-88 | Strongest area; large-scale recall and trust layer are in place |
| Evidence chain | 82-88 | Strong for debugging; must ensure all paths write evidence |
| Output quality control | 72-80 | Web research improved, but final brief quality still needs stronger composition |
| Voice input | 70-78 | Simulated baseline good; live owner/noise testing still needed |
| Capability packaging | 70-78 | L1/L3 flow exists, but packaged-mode install and dependency closure need regression |

## 23. What Not To Do

Do not:

- Add a new route by hard-coding another if/else in the main loop.
- Let a tool result directly become a success reply without verification.
- Treat voice as a separate assistant with separate memory/recovery.
- Hide failed UI automation behind friendly success text.
- Put business-specific PMO/HR/BI/Game logic into the cognitive kernel.
- Let a new Skill/MCP install without checking manifest contract quality.
- Let rejected or stale memory silently override user intent.

## 24. What To Do Next

Recommended next phase:

1. Audit `agent_core.py` and IM dispatchers for remaining side channels.
2. Run the suggested tests on the current working tree.
3. Pick one real workflow and prove full chain replay in Evidence Console:
   - voice/text input
   - memory recall
   - intent
   - DAG
   - WorkOrder
   - RoleExecutor
   - verification
   - recovery if needed
   - memory write
4. Check packaged mode capability visibility and dependency install.
5. Improve web research final brief composition quality.
6. Continue live voice owner/noise testing and log results in `docs/19_voice_unified_input_live_test_log.md`.

The key question for the next Codex window is not "can Jachin call a tool?".

The key question is:

```text
Can Jachin understand a goal, choose the right capability, execute with evidence, recover from failure, and remember what it learned without adding another hard-coded branch?
```

