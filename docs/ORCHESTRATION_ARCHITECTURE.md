# Jachin 长期编排架构（三层）

**更新**: 2026-03-16（与总览、对标文 `JACHIN_VS_OPENCLAW_INTELLIGENCE_ANALYSIS.md`、规则 **078** 同步）  
**代码入口**: `l3_node/orchestration/`、`l3_node/workflow_spec_runner.py`、`core/native_tools.py`（`core:domain_workflow_run`）  
**与 HR 关系**: 招聘 **领域子图** 仍为 `l3_node/primitives/skills/hr_recruitment_dag.py` + `DAGWorkflow`（见 [HR_RECRUITMENT.md](./HR_RECRUITMENT.md)）；本架构 **不替换** HR 实现，只提供 **注册与调用面**。

---

## 1. 三层模型

| 层 | 职责 | 典型实现 |
|----|------|-----------|
| **L1 Skill 发现/路由** | 上万 skill → 小候选集（不把全量工具塞进一次 ReAct） | `SemanticRouter`（`core/vector_router.py`）+ `l3_node/orchestration/skill_routing.py` 封装 |
| **L2 领域子图** | 强业务状态机、信号、持久化约定 | HR：`build_hr_recruitment_dag`；未来 BI/合规等可 `register_domain()` |
| **L3 通用 Glue** | 跨域串联：工具链 YAML + 可选嵌入领域一步 | `core:workflow_run`；YAML 中 `domain_ref`；`core:domain_workflow_run` |

**原则**: Skill 规模 ∝ 索引/路由；DAG 深度 ∝ **当前任务**；禁止「一张图挂全站所有 skill」。

---

## 2. L1 配置（可选）

`~/.jachin/nexus_config.json`:

```json
{
  "orchestration": {
    "skill_routing_enabled": true,
    "vector_router_threshold": 0.75
  }
}
```

Python:

```python
from l3_node.orchestration import suggest_skills_from_intent
suggest_skills_from_intent("帮我做代码审查")
```

---

## 3. L2 领域注册与调用

内置：`hr_recruitment` → `run_hr_recruitment_domain`（委托 `DAGWorkflow.run`）。

扩展（插件/主仓）:

```python
from l3_node.orchestration import register_domain

def run_my_domain(params: dict | None) -> dict:
    return {"ok": True, "domain": "my_domain"}

register_domain("my_domain", run_my_domain)
```

工具调用：

```json
{
  "domain_id": "hr_recruitment",
  "workflow_id": "hr_recruitment_main",
  "include_analyze": false,
  "context": { "skip_hr_plan_init_node": true }
}
```

→ `core:domain_workflow_run`，或 `run_domain("hr_recruitment", {...})`。

---

## 4. L3 YAML 嵌入领域（`domain_ref`）

与 `tool_id` **二选一**：

```yaml
version: 1
id: cross_domain_demo
steps:
  - id: prep
    tool_id: core:fs_read
    input: '{"file_path":"notes.txt"}'
  - id: hr_segment
    domain_ref: hr_recruitment
    depends_on: [prep]
    input:
      workflow_id: hr_recruitment_main
      include_analyze: false
      context: {}
```

等价思路：先跑若干 **原子 tool**，再 **单步委托** 整块 HR 子图（不把 Harvest 内部拆成 50 个 YAML 节点）。

---

## 5. 与 HR 调度器的关系

- **APScheduler / `recruitment_scheduler`** 仍是无人值守 tick 的推荐入口（已验证、带 `skip_*` 上下文）。
- **`domain_ref` / `core:domain_workflow_run`** 适合：显式编排、Agent 决策、跨域 YAML **胶水**；长循环前请评估超时与资源。

---

## 6. 参考

- [INTELLIGENCE_UPGRADE_OVERVIEW.md](./INTELLIGENCE_UPGRADE_OVERVIEW.md)
- [HR_RECRUITMENT.md](./HR_RECRUITMENT.md)
- `core/workflow_engine.py`（`DAGWorkflow`）
