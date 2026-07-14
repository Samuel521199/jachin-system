# TaskDecomposerAgent Architecture

TaskDecomposerAgent is the formal boundary between a DecisionContract and executable WorkOrders.

## Purpose

ReviewBoard understands the user request, Arbiter authorizes the execution boundary, and TaskDecomposerAgent turns that approved goal into a structured Task DAG.

This prevents the system from treating multi-step operating-system tasks as a single tool call.

## Input

- User original task, already normalized into ReviewSummary and DecisionContract.
- Current state and memory references carried by the DecisionContract.
- Available tool/capability candidates from ReviewBoard and Arbiter.

## Output

Each decomposed node contains:

- `goal`
- `role_agent`
- `tool`
- `capability`
- `inputs`
- `depends_on`
- `risk_level`
- `verification_criteria`
- `recovery_policy`

Arbiter converts every node into a WorkOrder, so one DecisionContract can now produce multiple WorkOrders.

## Current Direct Decompositions

- Capability metadata decomposition:
  - ReviewBoard now reads Skill/MCP `plugin.json` manifests into the Capability Registry.
  - SemanticIntentAgent emits ranked `semantic_candidates` and `capability_candidates` with confidence, target patches, descriptor evidence, and matched terms.
  - TaskDecomposerAgent first checks selected capability metadata for `decomposition.nodes`.
  - If metadata nodes exist, they become the Task DAG without adding new branches to core code.
- Message delivery:
  - Open or focus the target app.
  - Send the message through MessageExecutorAgent.
- Calculator calculation:
  - Open or focus Windows Calculator.
  - Enter the expression and verify the result.
- File/App single-step tasks:
  - Keep as one node unless a capability manifest later supplies a richer decomposition.

## Runtime Contract

Direct mainline executes WorkOrders in DAG order and stops on the first failed verification.

Role Agents may use their own direct execution channels. For example, AppControlExecutorAgent can open/focus an app without going through the legacy tool transport, while the following task node still executes through its own Role Agent and verification path.

## Extension Rule

Future Skill/MCP packages should declare decomposition and recovery policies in capability metadata. TaskDecomposerAgent should consume those declarations instead of adding one-off branches to the mainline.

Recommended manifest metadata shape:

```json
{
  "capability": {
    "task_type": "app_control",
    "workflow_id": "browser.open",
    "examples": ["open browser", "打开浏览器"],
    "decomposition": {
      "nodes": [
        {
          "id": "open_app",
          "goal": "Open $target.name",
          "role_agent": "AppControlExecutorAgent",
          "tool": "mcp:windows_open_app",
          "capability": "app_control.open_or_focus",
          "inputs": {
            "target": {
              "type": "app",
              "name": "$target.name"
            }
          },
          "work_order_input": {
            "app": "$target.name"
          },
          "verification_criteria": ["$target.name window is visible"],
          "recovery_policy": {
            "strategy": "capability_playbook_then_retry",
            "max_attempts": 2
          }
        }
      ]
    }
  }
}
```

## Intent Routing Rule

ReviewBoard still owns the first deterministic parse, but it no longer relies only on hard-coded rules. It now merges:

- deterministic intent extraction;
- lightweight semantic parsing from the task-understanding engine;
- Skill/MCP manifest descriptors from Capability Registry;
- ranked alternatives such as `lock -> Lark`, `browser -> Browser/Chrome/Edge`, and message surfaces such as Lark/WeChat/mail;
- confirmed correction memory from the unified memory lifecycle.

Semantic candidates are advisory. They can fill missing slots or repair confirmed entity mistakes, but Arbiter remains the execution gate. This prevents capability metadata or a lightweight model from silently hijacking an already clear task.
