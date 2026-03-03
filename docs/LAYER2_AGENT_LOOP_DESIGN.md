# Layer 2 边缘智能体：Agent Loop 与自主执行架构

**版本**: 1.0  
**状态**: 已实现  
**定位**: 进化战役一 — 自我意识觉醒，对标 OpenClaw 的高自由度 Agent 架构

---

## 一、设计哲学：从流水线到数字生命体

### 1.1 旧范式（已废弃）

> 蓝图 = 死板的 Trigger → Processor → Action 流水线  
> 边缘智能体 = 机械执行器，收到蓝图后按顺序跑完即止

### 1.2 新范式（当前实现）

> **蓝图 = 岗位说明书 (Persona & Skillset)**  
> 赋予边缘智能体一个「人设」和一堆「Wasm 技能武器」。  
> 拿到武器后，智能体通过 **ReAct (Reasoning and Acting)** 代理循环**自主决定**怎么打。

**核心转变**：边缘智能体不再是被动执行流水线，而是**拥有人类直觉的分布式数字生命体**。

---

## 二、蓝图重定义：Persona & Skillset

### 2.1 蓝图的新语义

| 维度 | 旧设计 | 新设计 |
|------|--------|--------|
| **本质** | 流程图（节点 + 连线） | 岗位说明书（人设 + 技能清单） |
| **Trigger** | 固定入口，机械触发 | 可保留为「唤醒词」等元信息，不参与执行顺序 |
| **Processor** | 按序执行，一个接一个 | **Wasm 技能武器**，由 Agent 按需调用 |
| **Action** | 固定出口 | 由 Agent 在 ReAct 循环中自主决定何时结束 |

### 2.2 技能提取

从蓝图 AST 的 `Processor` 节点提取：

```json
{
  "type": "processor",
  "data": {
    "label": "天气查询",
    "wasm_path": "plugins/weather.wasm",
    "fuel_limit": 100000
  }
}
```

→ 技能：`{ "label": "天气查询", "wasm_path": "...", "fuel_limit": 100000 }`

Agent 的系统 Prompt 会动态组装：*「你可以使用以下 Wasm 技能：1. 天气查询 (wasm_path: ...) ...」*

---

## 三、持久化记忆 (core/agent_memory.py)

### 3.1 职责

- **短期对话上下文**：最近 N 轮 user/assistant 交换
- **长期关键事件记忆**：重要决策、执行结果

### 3.2 API

| 函数 | 说明 |
|------|------|
| `add_memory(role, content)` | 写入一条记忆，role 为 `user` / `assistant` / `system` |
| `get_context(limit=10)` | 获取最近 N 条记忆，供 Agent 上下文使用 |
| `clear_memory()` | 清空（慎用） |

### 3.3 存储

- **优先**：SQLite，`~/.jachin/memory.db`（表 `conversations`）
- **降级**：JSON 文件，`~/.jachin/agent_memory.json`

---

## 四、ReAct 代理循环 (core/agent_loop.py)

### 4.1 流程

```
User Input (任务/聊天消息)
    ↓
┌─────────────────────────────────────┐
│  Thought: LLM 思考下一步策略         │
│  Action: 解析输出，若需执行技能则调用  │
│  Observation: Wasm 返回结果，写入记忆  │
└─────────────────────────────────────┘
    ↓ 循环直至 LLM 输出 Answer:
最终回复
```

### 4.2 输出格式约定

| 格式 | 含义 |
|------|------|
| `Action: run <技能名或序号>` | 执行指定 Wasm 技能 |
| `Action: <Mock 工具名>` | 无 Wasm 时：get_weather、read_local_file |
| `Final Answer:` / `Answer:` | 任务完成，返回给用户 |

### 4.3 LLM 配置

- **优先**：本地模型（`LOCAL_LLM_URL`，如 Ollama、vLLM）
- **降级**：Qwen API（`QWEN_API_KEY`）

### 4.4 调用入口

```python
from core.agent_loop import run

result = await run(
    user_input="新蓝图已下发，请基于当前技能自主待命",
    ast_json=blueprint_ast,
    max_iterations=5,
)
```

---

## 五、守护进程升级 (core/daemon.py)

### 5.1 执行逻辑变更

| 时机 | 旧行为 | 新行为 |
|------|--------|--------|
| 心跳返回蓝图 | 机械执行 Trigger→Processor→Action | 将蓝图 + 任务喂给 `AgentLoop.run()` |
| 心跳返回 task/message | 无 | 作为 `user_input` 传入 Agent |

### 5.2 默认任务

当仅收到蓝图、无显式任务时，默认 `user_input` 为：

> *「新蓝图已下发，请基于当前技能自主待命。若有待办任务请执行，否则保持就绪。」*

### 5.3 IM 网关扩展（已实现）

心跳 API 已支持返回 `task`、`pending_message_ids`。用户通过 Telegram/飞书发消息 → Webhook 入队 → 心跳拉取 → Agent 执行 → `POST /api/v1/agents/result` 回传。详见 [IM_GATEWAY_SPEC.md](./IM_GATEWAY_SPEC.md)。

---

## 六、文件与模块映射

| 模块 | 路径 | 职责 |
|------|------|------|
| 持久化记忆 | `core/agent_memory.py` | add_memory, get_context |
| ReAct 循环 | `core/agent_loop.py` | run(), 技能提取、LLM 调用、Wasm 执行 |
| 守护进程 | `core/daemon.py` | 心跳、蓝图接收、调用 AgentLoop |
| Wasm 沙箱 | `core/wasm_runner.py` | run_plugin()（Pure Compute）、run_plugin_wasi()（Python stdin/stdout） |

---

## 七、相关文档

- [plugins/README.md](../plugins/README.md) - Wasm 技能插件目录与配置
- [jachin-plugin-sdk](../jachin-plugin-sdk/README.md) - JPP Rust 脚手架
- [jachin-plugin-sdk-python](../jachin-plugin-sdk-python/README.md) - JPP Python SDK（WASI stdin/stdout）
- [NEXUS_DAEMON.md](./NEXUS_DAEMON.md) - 守护进程总览（含轻量版 daemon）
- [IM_GATEWAY_SPEC.md](./IM_GATEWAY_SPEC.md) - IM 网关（TG/飞书、消息队列、result API）
- [LAYER1_ARCHITECTURE_AND_DESIGN.md](./LAYER1_ARCHITECTURE_AND_DESIGN.md) - Layer 1 与 Forge 蓝图编排
