# Jachin 安全锁（JACHIN_SAFETY_LOCK.md）

## 目的

- 与 **MEMORY.md**、**core:local_memory_search** 检索到的会话记忆 **分离**。
- 存放 **经人工或受控流程确认** 的事实、schema、约束（例如真实表名、禁止操作），降低模型用「推测」当事实的幻觉风险。
- L3 `system prompt` 注入时赋予 **更高后缀优先级**（`eviction_rank=98`），页脚说明 **与安全锁冲突时以安全锁为准**。
- **风险与治理（四项）** 见 **[JACHIN_SAFETY_LOCK_REMEDIATION.md](./JACHIN_SAFETY_LOCK_REMEDIATION.md)**：按需域挂载、pending 审批、移除与维护扫描。

## 文件位置

| 路径 | 作用 |
|------|------|
| `~/.jachin/JACHIN_SAFETY_LOCK.md` | **主文件**；审批通过后由 CLI 或 `direct_append_to_md` 追加 |
| `~/.jachin/workspace/JACHIN_SAFETY_LOCK.md` | **可选**；只读合并进 prompt（工具不写此路径） |
| `~/.jachin/safety_lock/db_safety_lock.md` | **按需**：话术命中 DB 类意图时注入 |
| `~/.jachin/safety_lock/shell_safety_lock.md` | **按需**：话术命中 Shell 类意图时注入 |
| `~/.jachin/safety_lock/pin.md` | **可选短引**：任意意图可挂载（建议短小） |
| `~/.jachin/safety_lock/pending/*.json` | Agent 调用 `core:safety_lock_append` 的 **待审批** 队列 |

`JACHIN_HOME` 可覆盖 `~/.jachin` 根目录。

## 受控追加（自动「学习」）

原生工具：

- **`core:safety_lock_append`**：默认在开启学习时写入 **pending**，**不**把管理员密钥交给模型。
- **`core:safety_lock_list_pending`**：列出待审批条目（供 Agent 自检文案）。
- **`core:safety_lock_remove`**：按正式 MD 中条目的 `id=` 删除整段（高权限场景；误学入 MD 后可纠偏）。

**默认禁止直写主 MD**，避免模型无人监督污染安全锁。开启学习其一即可：

- 环境变量：`JACHIN_SAFETY_LOCK_LEARN=1`（或 `true` / `yes` / `on`）
- `~/.jachin/nexus_config.json`：

```json
{
  "safety_lock": {
    "learn_enabled": true,
    "append_requires_approval": true,
    "direct_append_to_md": false,
    "inject_max_total_chars": 8192,
    "archived_global_head_chars": 2048
  }
}
```

- **`append_requires_approval`**：默认 `true`；`false` 时（且未强制 pending）行为以 `jachin_safety_lock.py` 为准，**慎用**。
- **`direct_append_to_md`**：开发/单机可 `true`，使 `append` 跳过 pending **直接**写 `JACHIN_SAFETY_LOCK.md`（仍无「模型持管理员 token」设计）。
- 历史字段 **`append_secret` / WorkOrder `token`**：**已废弃**；模型侧传入的 token **被忽略**，不得用于授权写正式文件（见 REMEDIATION 文档「密钥悖论」）。

### tool input 示例（append）

```json
{
  "body": "- 生产库审计日志表名为 `audit_events`，无 `logs` 表；删冗余前须 SELECT 确认分区。",
  "source": "dba_review",
  "tags": ["db", "prod"]
}
```

### 审批刷入正式 MD（仅本机进程外）

设置环境变量 **`JACHIN_SAFETY_LOCK_ADMIN_TOKEN`**（勿写入可被模型读取的 prompt / 工具描述）。

```bash
python -m l3_node.jachin_safety_lock_admin list
python -m l3_node.jachin_safety_lock_admin approve <pending_id>
python -m l3_node.jachin_safety_lock_admin reject <pending_id>
python -m l3_node.jachin_safety_lock_admin maintenance
```

## Prompt 注入（按需域 + 预算）

- 由 `heuristic_safety_lock_domains(user_text)`（`l3_node/routing/output_format_signals.py`）决定是否挂载 db/shell 域文件。
- 未命中域时，可注入全局 `JACHIN_SAFETY_LOCK.md` 的 **头段**（长度由 `archived_global_head_chars` 控制，`0` 可关闭）。
- 总字符预算：`inject_max_total_chars`（硬帽见 `jachin_safety_lock.py`）；**全量模式** `full_inject` / `JACHIN_SAFETY_LOCK_FULL_INJECT=1` 仍 **截断**，禁止数十万字符灌入 system。

## 推荐工作流（问题 → 结果 → 改进 → 落盘）

1. 执行任务，保留 **真实错误/查询输出**（如 `information_schema`、DBA 回复）。
2. 人工或外部助手整理为 **短条目**（事实陈述，不写推测）。
3. 在开启 `learn_enabled` 的前提下，由 Agent 调用 **`core:safety_lock_append`** → 生成 **pending**，或由运维 **`approve`** 写入 MD，或 **手工编辑**。
4. **不要**把同一段未经审查的文字写进 MEMORY.md 当「权威」。

## 限制

- 安全锁 **不能替代** 执行层策略（Shell/DB 审批、只读预检、沙箱）。
- 单条正文上限 16k 字符；主文件仍可设运维级软上限（归档策略见运维）。
- 子账号若使用 `allowed_skills` 白名单，需在技能包中 **显式包含** 相应 `core:safety_lock_*` 工具。

## 治理与执行说明

安全锁只保留受控事实约束。知识沉淀、复盘、冲突治理与长期记忆生命周期由 Memory Growth 主线承担。
四项风险与实现对照：**[JACHIN_SAFETY_LOCK_REMEDIATION.md](./JACHIN_SAFETY_LOCK_REMEDIATION.md)**。

## 相关代码

- `l3_node/jachin_safety_lock.py`：读/写、pending、审批、移除、维护扫描、注入预算
- `l3_node/jachin_safety_lock_admin.py`：CLI
- `l3_node/routing/output_format_signals.py`：`heuristic_safety_lock_domains`
- `l3_node/agent_core.py`：`kernel prompt composer(..., safety_lock_user_text=...)`
- `core/native_tools.py`：`core:safety_lock_append` / `list_pending` / `remove`
- `l3_node/primitives/tools/loader.py`：工具元数据与参数解析
