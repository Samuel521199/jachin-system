# 07 — Layer 3: 单体执行节点 (V2)

**文档类型**: 白皮书 · Layer 3 详细说明  
**版本**: V2  
**基准**: [ARCHITECTURE_V2_LAYER3_STANDALONE.md](../ARCHITECTURE_V2_LAYER3_STANDALONE.md)

---

## 一、定位与职责 (V2)

**Layer 3 单体 = OpenClaw 单机**：多 Agent、多 Skill、本地记忆、任务执行、可协同。

| 维度 | 说明 |
|------|------|
| **Agent** | 每节点有主 Agent，可分身多个子 Agent |
| **Skill** | MCP + SKILL.md + JPP .wasm |
| **执行** | ReAct 循环 + 工具调用 |
| **记忆** | 本地 Memory Nexus（Chroma）；不向 L2 同步宿主记忆 |
| **API Key** | 本地持**密文**，请求时解密后**自行调用**外部 API；L2 只管理 Key |

---

## 二、与 v8.0 的差异

| 维度 | v8.0 | V2 |
|------|------|-----|
| **L3 角色** | UI、HITL、感官外壳 | **完整执行节点**（Agent + Skill + 直连 LLM） |
| **推理** | 发往 L2，L2 代理 | **L3 直连** api.openai.com 等 |
| **Key** | 无 | L2 密文下发，L3 解密后内存持有 |

---

## 三、L3 入口 (Ingress)

- **Tauri 桌面端**：UI、HITL 弹窗、语音唤醒
- **IM 渠道**：Telegram、飞书等
- **CLI**：jachin-cli shell
- **Web 控制台**：内嵌或独立

所有入口归一化为「任务」注入 L3 的 Agent，由 Agent + Skills 执行。

---

## 四、L3 代码结构 (l3_node/)

```
l3_node/
├── llm_client.py      # SecurityContext + LiteLLMEngine 直连
├── agent_core.py      # ReAct Agent + Memory Nexus
├── bootstrap.py       # 引导：注册、拉 Key
├── crypto.py          # RSA 解密
└── engine/hooks_pipeline.py
```

---

## 五、参考

- [ARCHITECTURE_V2_LAYER3_STANDALONE.md](../ARCHITECTURE_V2_LAYER3_STANDALONE.md)
- [l3_node/README.md](../../l3_node/README.md)
