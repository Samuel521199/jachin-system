# Memory-first Cognitive Kernel Execution State

Source spec: `docs/07_memory_first_main_agent_and_voice_app_agents.md`

This file records the current execution state of the L3 architecture. It is not
a migration history. The active runtime model is:

`AgentInputEnvelope -> StateFabric -> MemoryRecall -> ReviewBoard -> Arbiter -> DecisionContract -> WorkOrder -> RoleExecutor -> Verification -> Recovery -> TurnClosure -> MemoryWrite`

## Non-Negotiable Boundaries

- The Cognitive Kernel is the only decision and authorization boundary.
- Every user-visible input source is normalized into `AgentInputEnvelope`.
- State and memory are read before planning.
- External-world mutation requires an authorized `WorkOrder`.
- Tools, MCPs, Skills, native actions, app control, file operations, messages,
  and memory writes are executed only by Role Executors.
- Verification and recovery are separate role responsibilities.
- Every action turn produces ledger evidence and a `TurnClosure`.
- Business behavior belongs in Skill/MCP packages or capability hooks, not in
  the kernel.

## Implemented Runtime Components

- `l3_node/cognitive_kernel/contracts.py`
  Defines `AgentInputEnvelope`, `StateSnapshot`, `RelevantMemoryBundle`,
  `DecisionContract`, `WorkOrder`, `VerificationReport`, `RecoveryPlan`,
  `MemoryWriteRequest`, `TurnClosure`, and task ledger records.
- `l3_node/cognitive_kernel/state_service.py`
  Runs durable StateFabric sampling and persists latest/history snapshots.
- `l3_node/cognitive_kernel/memory_recall_agent.py`
  Builds bounded memory evidence before prompt construction.
- `l3_node/cognitive_kernel/review_board.py`
  Runs role review and evidence collection.
- `l3_node/cognitive_kernel/arbiter.py`
  Converts review evidence into `DecisionContract`.
- `l3_node/cognitive_kernel/dispatcher.py`
  Dispatches every tool intent through `WorkOrder` and Role Executor.
- `l3_node/cognitive_kernel/role_executors.py`
  Contains specialized executors for AppControl, File, Message, Browser, OS,
  MemoryRecall, MemoryWrite, and generic tool execution.
- `l3_node/cognitive_kernel/recovery_planner.py`
  Chooses recovery attempts from capability-owned recovery playbooks and prior
  failure evidence.
- `l3_node/cognitive_kernel/task_dag.py`
  Persists complex task DAGs with dependency promotion.
- `l3_node/cognitive_kernel/task_guardian.py`
  Scans active tasks and emits stale/ready evidence.
- `l3_node/cognitive_kernel/memory_lifecycle.py`
  Classifies, dedupes, merges, expires, recalls, and writes lifecycle memory.
- `l3_node/cognitive_kernel/task_memory.py`
  Converts finished `DecisionContract`/`WorkOrder`/`VerificationReport` state
  into task experience memory: historical summaries, tool habits, and failure
  hints.
- `l3_node/cognitive_kernel/direct_mainline.py`
  Executes low-risk Arbiter-issued WorkOrders directly and feeds verified task
  experience back into `TurnClosure` memory writes.
- `l3_node/cognitive_kernel/capability_work_order_adapter.py`
  Adapts dynamic Skill/MCP metadata and capability hook suggestions into WorkOrders.
- `l3_node/cognitive_kernel/capability_hook_bridge.py`
  Defines the structured hook-to-WorkOrder suggestion envelope.
- `l3_node/cognitive_kernel/transport_errors.py`
  Converts transport failures into verifiable observations.
- `l3_node/cognitive_kernel/capability_recovery_registry.py`
  Loads recovery playbooks from capability manifests.
- `l3_node/cognitive_kernel/recovery_playbook_schema.py`
  Validates recovery playbook schema for publish/install/runtime quality gates.

## Role Agent Matrix

- `ConversationAgent`
- `UserFacingReplyAgent`
- `MemoryRecallAgent`
- `ReviewBoardAgent`
- `ArbiterAgent`
- `AppControlExecutorAgent`
- `BrowserExecutorAgent`
- `FileExecutorAgent`
- `MessageExecutorAgent`
- `OSExecutorAgent`
- `ToolExecutionAgent`
- `VerificationAgent`
- `RecoveryAgent`
- `MemoryWriteAgent`
- `TurnClosureAgent`

All role execution must be visible in the Cognitive Kernel ledger and Evidence
Console.

## Tool Execution Rule

No tool path may call a tool transport as the authorization boundary.

Allowed sequence:

1. Parse or infer intent.
2. Build `DecisionContract`.
3. Build `WorkOrder`.
4. Select Role Executor.
5. Execute through Dispatcher.
6. Verify.
7. Recover, ask for confirmation, or close the turn.
8. Write evidence and memory.

Capability metadata and capability hooks may produce structured WorkOrder
suggestions. They are only planning inputs: no suggestion may mutate external
state until Dispatcher creates and authorizes the WorkOrder.

## Release Quality Gates

Capability release and install flows validate:

- `plugin.json`
- required MCP/model dependencies
- recovery playbook schema
- source and installed package records
- L1 profile/source isolation

Invalid recovery playbooks are rejected before publishing, rejected before local
installation, and ignored defensively at runtime.

## Verification State

Automated tests cover:

- StateFabric persistence/status.
- Memory lifecycle dedupe, expiry, and recall.
- ReviewBoard and Arbiter planning.
- AppControl/File/Message direct WorkOrder execution.
- Confirmation resume/cancel/expiry.
- Role execution evidence.
- Recovery retry and final failure reports.
- Future-tool Dispatcher fallback.
- L1 publish/install recovery playbook validation.

Manual live validation is still useful for Windows focus, screenshots, OCR, and
real app behavior, but manual validation is product QA rather than architecture
completion.
