# 用户意图识别 — 当前系统架构说明

本文描述 **Jachin 系统在「用户一句话进来之后、到 ReAct/工具执行之前」** 如何分流与打标。  
**核心结论**：不存在单一的「意图分类模型」；当前是 **多入口规则路由 + 可选直连 LLM + 主路径由大模型在 ReAct 中自行选工具** 的组合架构。

**模糊 / 不标准说法**（短句遥控澄清、澄清态门控、与主模型分工）见专项文档：**[L3_AMBIGUOUS_INTENT_ARCHITECTURE.md](./L3_AMBIGUOUS_INTENT_ARCHITECTURE.md)**。

---

## 1. 总览分层

| 层级 | 作用 | 典型实现 |
|------|------|----------|
| **入口 / 通道** | 接收原文、附带元数据（chat_id、implicit_signals、channel） | WebSocket `18981`、飞书 IM、`POST /api/v3/agent/run`、Nexus `SensoryInputEvent` |
| **前置硬路由** | 不调用主 LLM 或仅短路径回复 | 遥控指令拦截、HR 招聘包、`clients/desktop` 内 BI 正则 |
| **轻量意图信号（正则/启发式）** | 影响提示词形态、是否绕过 ReAct、招聘域后缀 | `l3_node/routing/*` |
| **隐式行为信号** | 不直接改「业务意图」，写入情报事件供分析与记忆 | `core/intelligence_implicit`、`docs/IMPLICIT_SIGNALS.md` |
| **语义与工具意图** | 真正「要干什么」主要由 **ReAct 轮次内 LLM + 工具定义** 决定 | `l3_node/agent_core.run_agent` |

---

## 2. 按入口拆解

### 2.1 桌面 MIND STREAM / WebSocket（`l3_node/ws_server.py`）

1. 客户端发 JSON：`intent`（或 `content`），可选 `implicit_signals`、`chat_id`（Lark 镜像）。
2. **遥控/工作流短路径**：`try_lark_workflow_command_intercept` 若命中，直接返回固定文案，不进 `run_agent`。
3. 否则 **`run_agent(..., on_chunk=..., implicit_attribution={ channel: websocket_* , ... })`**。
4. 首包可下发 `thought`（排队提示），再进入内部钩子与 LLM。

### 2.2 飞书 IM（`l3_node/im_channels/dispatcher.py`）

1. **去重**：同 `chat_id` + 归一化文案在 TTL 内只处理一次。
2. **选岗 prelude**：招聘域下可能在拦截器之前合并 Boss 选岗行（`apply_job_select_from_hr_im_text`）。
3. **工作流拦截**：同 WS 的 `try_lark_workflow_command_intercept`。
4. **HR 分流**：关键词 / 选岗行解析命中且 HR 包可用 → **`process_lark_message`**（内再决定是否调 `run_agent_fn`）；否则 → **通用 `run_agent`**。
5. 执行在线程池，避免阻塞飞书长连接线程。

### 2.3 桌面在无 WS 时的兜底（`clients/desktop/src/lib/api.ts`）

- **`tryL3AgentForIntent`**：仅当用户输入匹配 **BI 相关正则** 时，才 `POST /api/v3/agent/run`；否则返回 `null`，由 L2 文本流式等承接。  
- 这是 **客户端侧、极窄的规则意图**，不是全局分类器。

### 2.4 Nexus / 感官总线（`core/event_bus.py` 等）

- `SensoryInputEvent` → Session 调度 → `agent_run`；意图仍由 **L3 `run_agent`** 处理，总线负责排队与多路输出，不负责细粒度意图 NLU。

### 2.5 语音 / 命令协议（`core/voice/intent_router.py`）

- **`IntentRouter.route`**：基于 **命令前缀** 与 **高危关键词表** 将 utterance 分为 `COMMAND`（含 risk_level）或 `CHAT`。  
- 用于 **安全与是否允许文件/Shell 类能力** 的闸门，不是通用任务分类。

---

## 3. L3 `run_agent` 内的「意图相关」逻辑（`l3_node/agent_core.py`）

进入 `run_agent` 后，在拼 **system prompt** 与是否 **直连 LLM** 之前，会做几类 **纯文本启发式**（非单独训练模型）：

### 3.1 输出格式 / 直连绕过（`l3_node/routing/output_format_signals.py`）

- **`analyze_output_format_signals`**：正则检测用户是否 **强约束输出形态**（禁止套话、仅 JSON、`必须且只能以 { 开头` 等）或 **轻量「要 JSON」**。
- **`heuristic_tool_need`**：正则检测是否 **明显需要工具**（读文件、shell、mcp、招聘名等）；命中则 **禁止** 直连。
- **`should_use_direct_llm_bypass`**：在 **无 delegate、非后台子任务通道、无工具意图** 前提下，若格式信号满足，则 **跳过 ReAct**，走 `_run_direct_llm_completion`（可带 `response_format: json_object`）。**传入 `raw_user_input`** 时，若原始句命中 **§12.4 混合注入/键盘乱码 OOD**，**禁止直连**（防分类面截断或小模型抠句后仍走 JSON 捷径）。

→ 这里的「意图」是 **「只要结构化回答 / 是否要走工具链」** 的二分与细分，由 **规则** 决定；**OOD 闸**见 `l3_node/intent_gateway/ood_signals.py` 与 **`evaluate_gateway_ood_gates`**（含 **整轮 LLM 硬拦截**）。

### 3.2 招聘域信号（`l3_node/routing/intent_signals.py`）

- **`user_message_suggests_recruitment_domain`**：当前句或 **最近若干轮历史** 拼接文本是否命中招聘相关关键词。  
- 用于 **`recruitment_longform`、HR 提示后缀** 等提示词形态，**不**单独决定「是否进 HR 包」（IM 侧已在 dispatcher 分流）。

### 3.3 隐式学习信号（`core/intelligence_implicit` + `docs/IMPLICIT_SIGNALS.md`）

- 重复追问、停留、跳过等 → 写入 `intelligence_events.jsonl`。  
- **`implicit_signals`（WS）**、`implicit_attribution`（渠道打标）随 `run_agent` 传入。  
→ 影响 **长期情报与记忆策略**，不直接等价于「本轮 NLU 意图标签」。

### 3.4 Compaction（`core/compaction_hook.py`）

- 与「意图识别」正交：按 **token 估算阈值** 触发上下文折叠/记忆刷新；可能 **显著拉长首包时间**，需在 UX/超时策略上单独考虑。

### 3.5 真正的「要调哪个工具 / 任务是什么」

- 由 **ReAct 循环** 中 **LLM + tools schema + 用户当前句与历史** 共同决定；没有并行的独立「意图分类微服务」。  
- 子 Agent / `delegate` 路径会 **关闭直连绕过**，并可能缩小工具白名单。

### 3.6 意图网关模型（`l3_node/intent_gateway`，与主 ReAct 解耦）

- **文本侧小模型**（默认 **`qwen-turbo`**）：配置于 `intent_gateway.classification_model` 或环境变量 `INTENT_GATEWAY_CLASSIFICATION_MODEL`，由 `get_classification_model_litellm_id()` 解析；用于 L2 轻量分类等（与 `LLM_MODEL` 独立）。  
- **多模态模型**（默认 **`qwen-vl-max`**）：`intent_gateway.multimodal_model` / `INTENT_GATEWAY_MULTIMODAL_MODEL`；入站流水线写入 `GatewayContextBundle.extra`；**直连 LLM bypass** 且附件元数据 **`has_image`** 时，`agent_core` 通过 `l3_override_model` 优先走该模型。  
- **可选**：`embedding_router_enabled` → `embedding_router.py`（Top-K + 稀疏边际 OOD）；`classification_llm_rewrite_enabled` → 扩写 `routing_utterance`；`multimodal_routing_head_enabled` → 仅用 Feature Slots 的多模态路由头；**DAG 校验后** `execution_inject` 将子意图与规划说明注入 **system prompt**（单 ReAct 内执行）。  
- 规格与快照表见 **`docs/USER_INTENT_RECOGNITION_REMEDIATION_PLAN.md`** §9。

---

## 4. 其它代码库中的「Intent」（易混淆）

- **`core/brain/planner/intent_parser.py` + `task_planner.py`**：面向 **Ray/任务规划** 的另一条链路，与 **L3 聊天主路径** 不混写在一处；桌面/飞书默认对话 **不经过** 该 Parser。  
- 文档阅读时请将 **「Planner 的 intent_type」** 与 **「L3 run_agent 路由」** 区分看待。

---

## 5. 小结表（从用户输入到行为）

| 阶段 | 是否调用主推理模型 | 如何「识别意图」 |
|------|-------------------|------------------|
| BI 兜底（桌面） | 仅当正则命中后 HTTP 调 L3 | 客户端正则 |
| 飞书 HR | 常进 HR 插件逻辑，内部再决定是否 `run_agent` | 关键词 + 选岗行解析 |
| 工作流拦截 | 通常不调用主模型 | 规则表 |
| 直连 LLM | 一次 completion，无 ReAct | 格式/工具启发式 |
| 默认 L3 对话 | 多轮 ReAct | **模型在对话中选工具** |
| 语音命令 | 路由 COMMAND/CHAT + 风险 | 前缀 + 关键词 |

---

## 6. 维护建议

- 新增「固定话术 / 固定流程」优先放在 **拦截器或通道路由**，避免和长提示词抢语义。  
- 调整「仅 JSON / 不要套话」类需求时，同步检查 **`output_format_signals.py` 正则** 与 **`agent_core` 提示词后缀**。  
- 观测意图误判时，应同时看：**入口通道**、**是否 direct bypass**、**ReAct 原始 thought/action**、**IMPLICIT 事件**，而不是假设存在单一意图模型。

---

*文档版本：与仓库 `l3_node/agent_core.py`、`l3_node/intent_gateway/*`、`l3_node/routing/*`、`l3_node/im_channels/dispatcher.py`、`clients/desktop/src/lib/api.ts`、`core/voice/intent_router.py` 当前实现对齐；若代码变更请以源码为准。*
