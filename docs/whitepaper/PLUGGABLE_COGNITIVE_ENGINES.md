# 可插拔认知引擎 (Pluggable Cognitive Engines)

**文档类型**: 白皮书 · 技术设计  
**版本**: V2.3  
**更新日期**: 2026-06  
**定位**: **L3** LLM 路由 — 大脑皮层，非 Skill

---

## 一、概念纠偏

| 概念 | 定位 |
|------|------|
| Tools / MCP / Skills | 「手脚」与声明式知识 |
| **LLM** | 驱动 ReAct 的「大脑」— **不可**做成 SKILL.md |

模型应作为 **Cognitive Engine**，与 Wasm/MCP 平级，由 `l3_node/llm_client.py` + LiteLLM 统一调度。

---

## 二、L3 实现（现行）

### 2.1 LiteLLM 统一接口

- **入口**：`l3_node/llm_client.py`、`l3_node/agent_core.py` 内 completion/stream
- **模型 env**：`LLM_MODEL`（默认）、`LLM_COMPLEX_MODEL`、`LLM_CODER_MODEL` — 三档自动路由（规则 063）
- **旁路**：`routing/output_format_signals.py` → `direct_llm_bypass`（轻量 JSON 时可跳过完整工具表）

### 2.2 区域 Key 与端点

**SSOT**：[DASHSCOPE_REGIONAL_KEYS.md](../DASHSCOPE_REGIONAL_KEYS.md)

| 来源 | 说明 |
|------|------|
| `JACHIN_ACTIVE_REGION` | CN \| SEA → 不同 compatible-mode URL |
| `DASHSCOPE_API_KEY_SEA` / `_CN` / 通用 Key | 瀑布流合并 |
| L2 下发 Key | **不覆盖**已配置的区域专用 Key |
| `~/.jachin/nexus_config.json` → `llm_keys` | credential_loader |
| 项目 `.env` | `load_l3_env_vars` 白名单注入 L3 子进程 |

### 2.3 大小脑路由（可选）

| 层级 | 配置 | 职责 |
|------|------|------|
| 小脑 Edge | `llm.edge_model`、Ollama | 意图分类、轻量总结 |
| 大脑 Cloud | `llm.cloud_model` | 复杂推理、代码 |

`nexus_config.json` → `llm.cognitive_mode`: `dual` | `edge` | `cloud`

**注意**：生产桌面默认以 **云端 Qwen 三档** 为主；Ollama 小脑为可选降级。

---

## 三、L2 Legacy 路径

`core/brain/llm/`、`core/llm_provider.py` 仍为 v8.0 **L2 daemon** 认知引擎，**非**桌面 Omni 主路径。新功能应加在 `l3_node/llm_client.py`。

---

## 四、神盾与 Compaction

- **Token 折叠**：`core/compaction_hook.py`；L3 桥接 `l3_compaction_bridge.py`
- **Memory Flush**：compaction 前写入 core_memory / Nexus drawer
- **重试/fallback**：LiteLLM fallback 模型链；须配合步骤级上限（[JACHIN_EXECUTION_RESILIENCE_CONTRACT.md](../JACHIN_EXECUTION_RESILIENCE_CONTRACT.md)）

---

## 五、配置示例

```json
{
  "llm": {
    "cognitive_mode": "dual",
    "edge_model": "ollama/qwen2.5:0.5b",
    "cloud_model": "qwen-max",
    "compaction_threshold": 6000
  },
  "llm_keys": {
    "dashscope": "sk-xxx"
  }
}
```

环境变量优先：`LLM_MODEL=qwen3.5-plus` 等见 `.env` 与 [L3_KEY_AND_ENV_ANALYSIS.md](../L3_KEY_AND_ENV_ANALYSIS.md)。

---

## 六、参考

- [PLUGGABLE_COGNITIVE_ENGINES.md](./PLUGGABLE_COGNITIVE_ENGINES.md)（本文）
- [AGI_OPTIMIZATION_ROADMAP.md](../AGI_OPTIMIZATION_ROADMAP.md)（智能化路线图，替代已删除的 INTELLIGENCE_UPGRADE_OVERVIEW）
- `.cursor/rules/063-l3-qwen-tri-model-routing.mdc`
