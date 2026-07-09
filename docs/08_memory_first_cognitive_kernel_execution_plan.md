# Memory-first Cognitive Kernel Implementation Plan

Source spec: `docs/07_memory_first_main_agent_and_voice_app_agents.md`

This document is the execution ledger for replacing the old L3 ReAct-centered architecture with the Memory-first Cognitive Kernel and Role Agent Network architecture.

## Ground Rules

- The Cognitive Kernel is the only decision and authorization boundary.
- Every user-visible input source must be normalized into `AgentInputEnvelope`.
- Memory recall and state snapshot reading happen before prompt construction.
- The kernel may create control-plane records and WorkOrders, but it must not directly mutate the external world.
- Execution Agents call tools only inside an authorized `WorkOrder`.
- Verification and recovery are separate role responsibilities; the kernel accepts, rejects, or reissues work.
- Old architecture docs and comments must be removed or rewritten. They must not remain as "legacy but safe" guidance.
- Business skills such as PMO, BI, HR, game QA, and English learning must remain capabilities/skills, not kernel logic.

## Implementation Nodes

### Node 0 - Architecture Intake and Cutover Ledger

Status: `done`

Actions:

- Read the new L3 architecture spec.
- Identify old architecture surfaces:
  - `docs/arch/`
  - `docs/architecture/`
  - top-level architecture docs that describe L3 as ReAct-first, L1/L2/L3-first, hybrid-agent-first, or PMO-first.
  - `l3_node/agent_core.py` header and prompt comments that describe the old "single main ReAct axis".
- Create this execution ledger.

Exit criteria:

- This plan exists in the repo.
- Each large implementation phase records status here.

### Node 1 - Core Contracts and Kernel Context Skeleton

Status: `done`

Actions:

- Add `l3_node/cognitive_kernel/` as the new architecture boundary.
- Define:
  - `AgentInputEnvelope`
  - `StateSnapshot`
  - `MemoryRecallRequest`
  - `RelevantMemoryBundle`
  - `DecisionContract`
  - `WorkOrder`
  - `VerificationReport`
  - `RecoveryPlan`
  - `MemoryWriteRequest`
  - `TurnClosure`
  - `TaskLedgerEntry`
- Add a lightweight context builder that can be called before existing prompt construction.
- Disable voice exact-template bypass by default so voice/chat still enters the kernel path.

Exit criteria:

- New contract modules import cleanly.
- `run_agent` can inject a Cognitive Kernel context block without breaking existing execution.
- Voice template fast path is opt-in legacy behavior only.

Completion note:

- Added `l3_node/cognitive_kernel/`.
- Added the core dataclass contracts and context builder.
- Injected the Cognitive Kernel context into top-level `run_agent` messages.
- Made the old voice template fast path opt-in through `JACHIN_LEGACY_VOICE_TEMPLATE_FAST_PATH`.

### Node 2 - State Fabric Snapshot Layer

Status: `done`

Actions:

- Add a `StateFabric` facade.
- Normalize current desktop/session/voice/task metadata into `StateSnapshot`.
- Ensure state reading is cheap and non-blocking.
- Move blocking sniffers out of the hot loop or mark them as on-demand tools only.

Exit criteria:

- The kernel receives a freshness-stamped snapshot every top-level turn.
- No full process/window/file scan runs implicitly inside the main loop.

Completion note:

- Added `l3_node/cognitive_kernel/state_watcher.py`.
- State Watcher samples lightweight process/resource/platform state and persists `latest_state.json`.
- `state_fabric.py` now merges caller metadata with watcher state and emits freshness.
- Heavy desktop/OCR/filesystem sensing remains explicit tools, not hidden main-loop work.

### Node 3 - Memory Recall Before Prompt

Status: `done`

Actions:

- Add `MemoryRecallAgent`.
- Fan out recall over:
  - recent action chain
  - conversation short-term
  - task state
  - user preferences
  - aliases and corrections
  - entity memory
  - failure memory
  - environment events
- Package recall as `RelevantMemoryBundle`.
- Inject only evidence summaries into the model prompt; raw memory store remains behind Memory Nexus.

Exit criteria:

- Every top-level turn has a memory recall bundle before LLM prompt construction.
- "close", "continue", "that", and contact/file aliases are resolved from state + memory, not from ad hoc string rules.

Completion note:

- `MemoryRecallAgent` now fans out over short-term conversation, State Watcher task/resource state, Memory Nexus semantic recall, and recent Cognitive Kernel ledger events.
- Failure and recovery hints are read from recent ledger entries and packed into `RelevantMemoryBundle`.
- Raw memory stores remain behind Memory Nexus; the prompt receives bounded evidence summaries only.

### Node 4 - Decision Contract and WorkOrder Gate

Status: `done`

Actions:

- Introduce `DecisionContract` generation before any external-world action.
- Wrap existing mission router / MCP / native tool execution under `WorkOrder`.
- Add `ToolPolicy` and risk metadata.
- High-risk operations require explicit confirmation or preconfigured policy.

Exit criteria:

- Kernel output shows why a workflow/tool was chosen.
- Execution agents cannot call side-effect tools without a WorkOrder.

Completion note:

- Added `l3_node/cognitive_kernel/runtime.py`.
- Every normal ReAct tool call is converted into `DecisionContract` plus `WorkOrder`.
- `core:local_memory_search` and `core:local_memory_append` special paths are also wrapped.
- Tool risk is classified from tool id and action input. Critical operations are blocked unless explicitly configured.

### Node 5 - Role Agent Network

Status: `done`

Actions:

- Map existing primitives into role agents:
  - `ConversationAgent`
  - `MemoryRecallAgent`
  - `IntentGraphAgent`
  - `AppControlPlannerAgent`
  - `MessageExecutorAgent`
  - `FileExecutorAgent`
  - `VerificationAgent`
  - `RecoveryAgent`
  - `MemoryWriteAgent`
- Reframe current delegate/fanout/pipeline as role-agent infrastructure, not as the main architecture.

Exit criteria:

- Role names and responsibilities match the new spec.
- PMO/BI/HR/Game/English remain skills behind the role network.

Completion note:

- Existing execution paths are now mapped to role names in contracts:
  - `MemoryRecallAgent`
  - `MemoryWriteAgent`
  - `ToolExecutionAgent`
  - `VerificationAgent`
  - `RecoveryAgent`
  - `TurnClosureAgent`
- The old ReAct loop is only a compatibility executor under WorkOrders.
- Business skills remain outside the kernel and are invoked through tools/capabilities.

### Node 6 - Verification, Recovery, and TurnClosure

Status: `done`

Actions:

- Create `VerificationReport` records for observable actions.
- Create `RecoveryPlan` when verification fails.
- End every turn with `TurnClosure`.
- Persist TaskLedger entries for replay and evidence.

Exit criteria:

- Every action turn ends as one of: completed, answered, waiting_user, backgrounded, blocked, failed_recoverable, failed_final.
- Evidence panel can show envelope, state, memory, decision, work orders, verification, recovery, closure.

Completion note:

- Added append-only Cognitive Kernel ledger under `~/.jachin/cognitive_kernel/ledger/`.
- Tool observations produce `VerificationReport`.
- Failed verification produces `RecoveryPlan`.
- Normal ReAct completion and direct/OOD bypass paths now write `TurnClosure`.
- A local smoke verified `turn_started -> decision_contract -> work_order -> verification_report -> turn_closure` persistence.

### Node 7 - Remove Old Architecture Docs

Status: `done`

Actions:

- Delete old architecture directories and docs that conflict with the new spec.
- Replace surviving docs with pointers to:
  - `docs/07_memory_first_main_agent_and_voice_app_agents.md`
  - this execution plan
- Remove PMO-first, L2-required, single-ReAct-axis, and hybrid-agent-first architecture language from comments and prompts.

Initial deletion candidates:

- `docs/arch/`
- `docs/architecture/`
- `docs/ARCHITECTURE.md`
- `docs/ARCHITECTURE_L1_WORKSPACE_L2_GATEWAY_L3.md`
- `docs/ARCHITECTURE_V2_LAYER3_STANDALONE.md`
- `docs/JACHIN_FULL_ARCHITECTURE_2026.md`
- `docs/MULTI_AGENT_ARCHITECTURE.md`
- `docs/ORCHESTRATION_ARCHITECTURE.md`
- deleted legacy context/memory/prompt architecture notes
- `docs/L3_AMBIGUOUS_INTENT_ARCHITECTURE.md`
- `docs/L3_FUZZY_INTENT_CLARIFICATION.md`
- `docs/USER_INTENT_RECOGNITION_ARCHITECTURE.md`
- `docs/USER_INTENT_RECOGNITION_REMEDIATION_PLAN.md`

Exit criteria:

- `rg "single main ReAct|单主轴|Hybrid Agent|旧架构|主从式 Agent|L2 required" l3_node docs --glob "!docs/08_memory_first_cognitive_kernel_execution_plan.md"` returns no active architecture guidance.
- Remaining docs do not contradict the Cognitive Kernel model.

Completion note:

- Removed `docs/arch/`, `docs/architecture/`, old whitepaper docs, old V2 diagrams, and top-level old architecture docs.
- Rewrote `docs/README.md` to point to the new architecture spec and this execution ledger.
- Replaced active code comments that referenced deleted old architecture docs.

### Node 8 - End-to-end Migration Tests

Status: `in_progress`

Actions:

- Add smoke tests for:
  - text chat
  - voice chat
  - "open calculator" then "close"
  - Codex-to-Lark workflow
  - Lark message send
  - English helper card
  - PMO skill invocation as external capability
- Verify no business skill is hard-coded into the kernel.

Exit criteria:

- Tests prove the new input -> memory -> state -> decision -> workorder -> verification -> closure path.
- Packaged mode still loads only installed skills.

### Node 9 - ReviewBoard / Arbiter Mainline

Status: `in_progress`

Purpose:

- This is the architecture mainline from `07_memory_first_main_agent_and_voice_app_agents.md`.
- Side cleanups such as HR/SQLite/domain guard extraction are not the primary path.
- The primary path is: `AgentInputEnvelope -> StateSnapshot -> MemoryRecall -> ReviewBoard -> Arbiter -> DecisionContract -> WorkOrder -> RoleExecutor -> Verification -> Recovery -> TurnClosure -> MemoryWrite`.

Actions:

- Add structured role-review contracts:
  - `RoleAgentReviewInput`
  - `RoleAgentReview`
  - `ReviewSummary`
- Add `ReviewBoard` as a coordinator that fans out evidence to review roles and never mutates the external world.
- Add `Arbiter` as the only component that converts review evidence into a final `DecisionContract`.
- Add a finite `kernel_loop` planner that creates WorkOrders only after Arbiter approval.
- Cover the core voice/app-control examples from the spec:
  - `打开计算器`
  - next-turn short command `关闭`
  - low-confidence/risky voice close requiring confirmation
  - `你好` staying conversational with no external WorkOrder.

Exit criteria:

- No external-world action can be planned without ReviewBoard evidence and an Arbiter-issued `DecisionContract`.
- `关闭` can resolve its target from active window or recent action memory.
- Conversation-only input produces no external WorkOrder.
- ReviewBoard, Arbiter, WorkOrder, and TurnClosure events appear in the ledger.

## Current Node Log

- 2026-07-08: Node 0 completed. Node 1 started.
- 2026-07-08: Node 1 completed. Node 2 started with a cheap non-blocking snapshot facade.
- 2026-07-08: Node 7 completed first pass. Old architecture docs and active code references were removed or redirected to the new Cognitive Kernel SSOT.
- 2026-07-08: Node 2 completed with State Watcher and freshness-stamped snapshots.
- 2026-07-08: Node 3 completed with multi-route recall: short-term, state, Memory Nexus, recent ledger failures/actions.
- 2026-07-08: Node 4 completed with DecisionContract and WorkOrder wrapping for normal tools and memory tools.
- 2026-07-08: Node 5 completed by mapping execution paths to role-agent responsibilities without embedding business skills in the kernel.
- 2026-07-08: Node 6 completed with VerificationReport, RecoveryPlan, TurnClosure, and append-only ledger persistence.
- 2026-07-08: Node 7 second pass removed old L1/L2 pairing docs, legacy voice routing proposals, old MCP lifecycle docs, PMO architecture doc dependencies, and active comments pointing to deleted architecture files.
- 2026-07-08: Node 8 stage A completed. Desktop voice messages now default into the Cognitive Kernel path instead of the old WS voice fast lane. TurnClosure now writes short-term action memory requests when WorkOrders execute, and unit tests cover voice envelope normalization plus memory write-back.
- 2026-07-08: Node 8 stage B completed. Added `RoleAgentRegistry` and `dispatch_tool_work_order`, formalized role-agent permissions for `ConversationAgent`, `AppControlExecutorAgent`, `FileExecutorAgent`, `MessageExecutorAgent`, `VerificationAgent`, `RecoveryAgent`, and `MemoryWriteAgent`, and routed the generic ReAct tool execution point through the WorkOrder Dispatcher before the legacy tool executor. Unit tests cover role selection, permission-gated dispatch, WorkOrder ledger emission, and verification.
- 2026-07-09: Node 8 stage C completed. Added role-specific execution adapters in `role_executors.py` for `AppControlExecutorAgent`, `FileExecutorAgent`, `MessageExecutorAgent`, `MemoryWriteAgent`, and compatibility `ToolExecutionAgent`. Dispatcher now invokes the matching Role Executor instead of directly calling the legacy tool callback, writes `role_execution_started` / `role_execution_finished` ledger events, and attaches role execution evidence to `VerificationReport`. Unit tests cover file and message role adapter metadata.
- 2026-07-09: Node 8 stage D completed. `FileExecutorAgent` now directly handles native `core:fs_read` / `core:fs_write` with path extraction, write/read evidence, and fallback only for non-native file MCPs. `MemoryWriteAgent` now directly calls Memory Nexus append for `core:local_memory_append` and classifies memory writes. `MessageExecutorAgent` now adds send preview, recipient extraction, payload validation, retry-on-transient-failure, and post-send evidence parsing. `AppControlExecutorAgent` records window/app hints and foreground-verification evidence from observations. Verification now trusts structured JSON `ok:true` before scanning textual failure words and includes role execution evidence. The Evidence Console now exposes `role_executions` so users can see which Role Agent executed which tool and what evidence it produced.
- 2026-07-09: Node 8 stage E completed first pass. OS Evidence Console now merges classic `.evidence.json` files with Cognitive Kernel ledger `.jsonl` turns, so DecisionContract, WorkOrder, RoleExecutor events, VerificationReport, RecoveryPlan, recovery execution, TurnClosure, files, screenshots, recipients, and role evidence are visible through one query path. Dispatcher now executes safe automatic recovery retries for retryable failed WorkOrders instead of only writing a RecoveryPlan, while leaving message-send retry inside `MessageExecutorAgent` to avoid duplicate sends. Role routing was tightened so Windows Lark tools route to `MessageExecutorAgent` rather than generic app control. Added `scripts/smoke_cognitive_kernel_stage_e.py` to smoke file read/write, app switch recovery, dry Lark send, and memory write through the full ledger/evidence path. Rewrote the active `l3_node` README and `agent_core.py` header so old ReAct-first / deleted architecture references no longer define the system.
- 2026-07-09: Node 8 stage F completed first pass. `agent_core.py` no longer owns pseudo-action ids, SQL/Data SOP prompt policy, local memory recall implementation, hallucinated-final-error recovery predicates, or the recovery prompt text used for fake MCP/weather JSON continuation. These moved to `l3_node/cognitive_kernel/pseudo_actions.py`, `prompt_policies.py`, `memory_tools.py`, and `recovery_guards.py`; `agent_core.py` now keeps only compatibility wrapper calls for the legacy text transport. Unit tests cover the exported policy boundary so new strategy code must enter the Cognitive Kernel / Role Agent side instead of growing `agent_core.py` again.
- 2026-07-09: Node 8 stage G completed first pass. Added `l3_node/capability_policies/` as the boundary for skill/tool-domain guardrails and expanded `capability_agent_hooks.py` into the aggregation surface. Workspace write-back guards, SQLite grounding/fake-query guards, SQLite Action Critic pre-execution gates, and HR recruitment final-answer side-effect guards now live in capability policy modules instead of inline `agent_core.py` logic. `agent_core.py` keeps only compatibility wrapper calls and hook dispatch. Added unit tests for these policy hooks plus source-level regression checks that the old long inline guard bodies do not return to `agent_core.py`.
- 2026-07-09: Node 9 stage A started as the architecture mainline from the 07 spec, correcting the previous drift into domain-policy cleanup. Added ReviewBoard/Arbiter/kernel-loop planning modules plus contracts for `RoleAgentReviewInput`, `RoleAgentReview`, and `ReviewSummary`. Unit tests now cover the spec path where `打开计算器` generates an AppControl WorkOrder, next-turn `关闭` resolves Calculator from active window or recent action memory, risky low-confidence voice close waits for user confirmation, and `你好` stays conversation-only with no external WorkOrder.
- 2026-07-09: Node 9 stage B connected the mainline planner to the real top-level `run_agent` entry. Top-level turns now build `CognitiveTurnContext`, run `ReviewBoard -> Arbiter -> WorkOrder planning`, write review/decision/plan ledger events, and inject a concise Cognitive Kernel plan block into the system prompt before legacy gateway/React execution continues. The real entry uses planning-only mode so it does not prematurely write final TurnClosure; final closure remains tied to actual execution/response.
- 2026-07-09: Node 9 stage C completed. Low-risk AppControl plans now bypass legacy ReAct: after tools are assembled, `run_agent` executes Arbiter-issued app-control WorkOrders directly through `dispatch_existing_work_order -> AppControlExecutorAgent -> Windows UIA MCP`, then writes VerificationReport and TurnClosure. Added the real `mcp:windows_window_close` tool and local UIA implementation so `打开计算器 / 关闭 / 切换窗口` can use the same ReviewBoard/Arbiter/WorkOrder path. Mainline WorkOrders now also execute retry recovery for transient AppControl failures such as timeout, window not ready, window not found, and unverified focus/close results. The compatibility `dispatch_tool_work_order` path still accepts injected executors for tests and old transports; only Arbiter-issued `mainline` WorkOrders use the direct AppControl adapter.

- 2026-07-09: Node 9 stage D completed first pass. ReviewBoard now extracts message recipients/content and file paths, and maps message delivery to the real Windows Lark tool `mcp:windows_lark_send_message`. Arbiter now writes executable `action_input` into WorkOrders for message delivery and safe file reads, so RoleExecutors no longer need old ReAct to reinterpret the user request. `agent_core.py` now has one unified direct mainline executor for AppControl, Message, and low-risk File WorkOrders; the older AppControl-only direct helper was removed. Unit tests prove `send to Neil: ...` and `read file README.md` create structured WorkOrders and execute through `dispatch_existing_work_order -> MessageExecutorAgent/FileExecutorAgent`, with FileExecutor bypassing `run_tool` for direct `core:fs_read`.

- 2026-07-09: Node 9 stage E completed. Added a top-level `run_agent` integration smoke that builds the real Cognitive Kernel plan, assembles a mocked real tool pool, executes `send to Neil: ...` through `try_execute_cognitive_direct_plan -> dispatch_existing_work_order -> MessageExecutorAgent`, and asserts the legacy ReAct core is not entered. The direct mainline bridge was moved out of `agent_core.py` into `l3_node/cognitive_kernel/direct_mainline.py`, so `agent_core.py` only calls the kernel bridge after tool-pool assembly. File mainline planning now distinguishes safe `read`, `open`, and `reveal in explorer`; safe open/reveal go through `FileExecutorAgent` and Windows UIA, while mutating file requests such as delete/write/move/rename remain confirmation-gated. `MessageExecutorAgent` now has duplicate-send protection based on WorkOrder id, normalized recipients, and message hash, with dedupe evidence attached to role execution results. OS Evidence Console ledger rendering now sorts the Cognitive Kernel timeline by the architecture order: ReviewBoard -> Arbiter -> WorkOrder -> RoleExecution -> Verification -> Recovery -> TurnClosure.

- 2026-07-09: Node 9 stage F completed. Added top-level `run_agent` integration smokes for AppControl direct open/switch/close and File direct read/open/reveal, alongside the existing Message smoke, and each smoke asserts the legacy ReAct core is not entered. OS Evidence Console timeline rows now show explicit architecture-stage badges such as ReviewBoard, Arbiter, WorkOrder, RoleExecution, Verification, Recovery, and TurnClosure. Confirmation-gated WorkOrders now persist a pending DecisionContract/WorkOrder pair; when the same session replies with confirmation, direct mainline resumes that exact pending contract instead of replanning, executes through Dispatcher/RoleExecutor, verifies, writes TurnClosure, and clears the pending record. File mutating operations remain confirmation-gated and are not auto-executed by direct mainline.

- 2026-07-09: Node 9 stage G completed first pass. `TurnClosure.memory_write_requests` are now materialized through `MemoryWriteAgent` via `execute_turn_closure_memory_writes`, so closure memory writes produce their own DecisionContract, WorkOrder, RoleExecution, Verification, and ledger events instead of remaining hints. Pending confirmations now support text cancel, explicit expiry, `confirmation_cancelled`, `confirmation_expired`, and no-pending guard replies so stale DecisionContracts cannot silently execute. OS Evidence aggregation now collects pending confirmation lifecycle events into `pending_decisions`, and the Evidence Console renders a Pending DecisionContract block plus timeline badges for Pending/Confirmation events. Unit tests cover closure memory writes through `MemoryWriteAgent`, pending cancel/expiry, direct AppControl/File/Message mainline, and confirmation-resume.

Remaining mainline items:

- 2026-07-09: Node 9 stage H completed first pass. Pending DecisionContract replies now include a hidden `jachin-ui:pending-confirmation` chat UI protocol with decision/work-order/tool metadata; `AssistantMessageContent` parses and strips that protocol, renders confirm/cancel buttons in every chat shell, and sends the backend-supported `确认执行` / `取消` commands through the real chat send path. `l3_node/cognitive_kernel/text_transport_compat.py` was added as the compatibility boundary for old text-transport quirks. Added `scripts/demo_cognitive_kernel_desktop_workflow.py`, a live desktop demo that drives AppControlExecutorAgent, FileExecutorAgent, MessageExecutorAgent, Verification, TurnClosure, ledger, and bridge Evidence in one runnable script, with safe dry-run Lark by default and opt-in real file reveal/send flags.

- 2026-07-09: Node 9 stage I completed first pass. The text compatibility loop was demoted further: the old text-loop tool/core entrypoints were demoted to WorkOrder transport names, and every parsed tool call now enters `dispatch_tool_work_order` before any MCP/native/skill transport can run. Unknown future tools are covered by `ToolExecutionAgent`, so new capabilities do not need new core code just to get DecisionContract, WorkOrder, RoleExecution, Verification, Recovery, and ledger coverage. `MemoryRecallAgent` is now a first-class executable role for `core:local_memory_search` / `recall_memory`, while `MemoryWriteAgent` remains the writer. StateWatcher now captures foreground window, running apps, recent foreground changes, resource state, and risk hints, and StateSnapshot carries that data into ReviewBoard. MemoryRecall now returns short-term conversation evidence, Memory Nexus matches, user preferences, aliases, corrections, recent action/failure ledger evidence, and conflict markers. The pre-kernel WS/server voice fast lane is retired; voice input now contributes evidence to the Cognitive Kernel path instead of bypassing it. Unit tests cover the new role routing and future-tool dispatcher fallback.

Remaining mainline items:

- Continue shrinking `_run_compat_text_core` until it only handles text streaming and parser fallback. It no longer directly invokes tools, but it still contains old prompt-format and parser compatibility text.
- Run one manual live desktop demo in the real app window and inspect Evidence Console rendering, because automated tests cannot fully validate Windows focus/screenshot behavior.
- Keep expanding direct mainline coverage for additional low-risk tools only after the AppControl/File/Message path has been manually verified.

- 2026-07-09: Node 9 stage J completed first pass. Added a production-style `StateFabricService` that runs as a durable background sampler, keeps latest/history state snapshots, persists `state_fabric_latest.json` and `state_fabric_history.jsonl`, emits window-change and service lifecycle ledger events, and is now started from the Cognitive Kernel turn pipeline. Added `memory_lifecycle.py` as the deterministic lifecycle layer in front of Memory Nexus: memory writes are classified, deduped, merged, TTL-expired, indexed, recalled, and also written from TurnClosure. Added `TaskDag` and `TaskGuardian` so multi-step tasks become persisted DAGs with dependency promotion, ready-node detection, stale-task hints, and proactive scan ledger events. The kernel planner now creates DAGs for complex/multi-step work instead of leaving them as one flat action. File direct mainline was expanded to confirmation-gated `core:fs_write`, so after user confirmation file writes execute through `FileExecutorAgent` rather than old ReAct. Runtime tests now cover StateFabric persistence/status, memory lifecycle dedupe/expiry/recall, DAG dependency promotion, guardian scans, direct AppControl/File/Message mainline, confirmation resume/cancel/expiry, role execution evidence, recovery retry, and future-tool dispatcher fallback.

Current implementation completion estimate:

- Main architecture path from the 07 spec is now about 90 percent implemented in code: Envelope, StateFabric, MemoryRecall, ReviewBoard, Arbiter, DecisionContract, WorkOrder, RoleExecutor, Verification, Recovery, TurnClosure, MemoryWrite, DAG, Guardian, and Evidence ledger are all present and covered by unit/integration smoke tests.
- Remaining 10 percent is mainly live-product validation and further coverage broadening: manual Windows UI demo verification, deeper StateFabric adapters for every app inventory source, more direct mainline mappings for specialized installed skills/MCPs, and final deletion of old prompt-format/parser compatibility once packaged/live paths are proven stable.
