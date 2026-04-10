# 动态专家智囊团（Dynamic Expert Panel）

## 目的

在单主轴 ReAct 上，根据用户意图自动推断 1～3 个资深专家身份（如「资深系统架构师」「高级产品经理」），通过 **Persona Multiplexing（身份钢印 + 多视角 Thought 协议）** 提升复杂任务的行业深度与推演质量；简单闲聊时专家列表为空，不增加提示词负担。

## 数据流

1. **Intent Gateway 小模型**（`l3_node/intent_gateway/classification_llm.py` · `infer_domain_experts_async`）在 `run_agent` 内、组装工具池之前执行，输出 JSON 字段 `domain_experts: string[]`（长度 0～3）。
2. 结果写入 **`GatewayContextBundle.domain_experts`**，并镜像到 **`bundle.extra["domain_experts"]`**；日志行 **`[IntentGatewayObs] domain_experts=...`** 便于审计。
3. **`l3_node/agent_core.py`** 将归一化后的列表传入 **`_build_system_prompt(..., domain_experts=...)`**：
   - 非空时在 system 前缀注入 **【动态智囊团授权】**；
   - 在 ReAct 说明段（非 `pure_json_contract`）按专家人数注入 **【多视角推演协议】** 或单专家简要指引；`pure_json_contract` 路径仅注入身份块，避免干扰强 JSON 契约。
4. **`ctx.metadata["_domain_experts"]`** 供 strict 只读 verify 轮重建 system 时保持一致。

## 配置（`nexus_config.json` → `intent_gateway`）

| 键 | 含义 |
|----|------|
| `domain_experts_llm_enabled` | 是否启用小模型推断（默认 `true`） |
| `domain_experts_llm_timeout_sec` | 超时秒数（默认 `3.0`） |
| `domain_experts_llm_max_tokens` | 小模型 max_tokens 上限（默认 `220`） |

## 与四大原语的关系

本机制不改变 **Tools / MCP / Skills / Agent Tasks** 的定义，仅在 **主 ReAct 的 system prompt** 层增加路由型人格与 Thought 规范，仍由模型在 **Action** 中决定是否调用工具。
