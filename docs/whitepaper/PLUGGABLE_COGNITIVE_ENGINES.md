# 可插拔认知引擎 (Pluggable Cognitive Engines)

**文档类型**: 白皮书 · 技术设计  
**版本**: v8.0 (The Singularity OS)  
**定位**: 动态认知路由 (Dynamic Cognitive Routing) — LLM 作为大脑皮层，非 Skill

---

## 一、 概念纠偏：模型不是「手脚」，而是「大脑皮层」

| 概念 | 定位 | 类比 |
|------|------|------|
| **Tools / MCP / Skills**（四大原语） | Agent 的「手脚」与声明式知识 | 文件读取、爬虫、计算器、`mcp:*`、`jpp:*` |
| **LLM (大语言模型)** | 驱动 ReAct 循环的「大脑」 | 认知引擎，不可作为 Skill 调用 |

**若将「大脑」变成「手脚（Skill）」**，系统会陷入逻辑死锁：谁来思考并调用这个「大脑技能」？

**最佳实践**：模型不作为 SKILL.md，而应抽象为 **可插拔的认知引擎 (Pluggable Cognitive Engines)**，与 Wasm 沙箱平级，作为守护进程的核心驱动组件。

---

## 二、 三层认知引擎池 (Cognitive Pool)

### 2.1 统一接口适配器 (LLM Adapter Factory)

所有模型（阿里 Qwen、OpenAI、本地 Ollama）必须实现统一接口 `BaseCognitiveEngine`（即 `BaseLLMProvider`）：

| 引擎类 | 实现 | 场景 |
|--------|------|------|
| **QwenEngine** | `QwenAdapter` / `QwenAdapterV2` | 云端 Qwen-Max、Qwen-Turbo |
| **OpenAIEngine** | OpenAI 兼容 API | GPT-4o、Claude 等 |
| **OllamaEngine** | `LocalAdapter` | 本地 Ollama (qwen2.5:0.5b 等) |

抛给 ReAct 循环的永远是统一的 `chat()` / `stream_chat()` 文本流。

### 2.2 密钥的瀑布流降级读取 (Waterfall Credentialing)

系统加载云端模型时，读取密钥的优先级：

| 优先级 | 来源 | 说明 |
|--------|------|------|
| 1 | `os.environ.get("DASHSCOPE_API_KEY")` | 系统环境变量，优先 |
| 2 | `~/.jachin/nexus_config.json` → `llm_keys.dashscope` | 本地配置文件 |
| 3 | `~/.jachin/.qwen_api_key` | 桌面端保存的覆盖 |
| 4 | `.env` 中的 `QWEN_API_KEY` / `DASHSCOPE_API_KEY` | 项目级配置 |

**若全部为空**：终端抛出赛博风格红色警告，挂起进程，禁止静默降级。

### 2.3 大小脑动态切换机制 (Dual-Brain Routing)

| 层级 | 代号 | 实现 | 职责 |
|------|------|------|------|
| **小脑** | Edge LLM | Ollama (qwen2.5:0.5b) / ONNX 小模型 | 意图分类、简单日志总结、本地闲聊、决定是否唤醒大脑 |
| **大脑** | Cloud LLM | Qwen-Max / GPT-4o | 复杂推理、代码生成、财务分析、Wasm 插件编写 |

**路由逻辑**：
- 小脑发现「帮我分析这个 Excel 的财务逻辑并写一个 Wasm 插件」→ 主动将上下文移交给云端大脑
- 小脑处理「开灯」「查询天气」→ 零延迟，不消耗 API Token，断网可用

**配置**：Layer 3 设置界面提供下拉菜单或开关，或后台根据任务复杂度自动路由。

---

## 三、 配置文件

**nexus_config.json 示例：**

```json
{
  "access_token": "...",
  "embedding": {
    "embedding_mode": "cloud"
  },
  "llm": {
    "cognitive_mode": "dual",
    "edge_model": "qwen2.5:0.5b",
    "cloud_model": "qwen-max"
  },
  "llm_keys": {
    "dashscope": "sk-xxx",
    "openai": "sk-xxx"
  }
}
```

| 字段 | 说明 |
|------|------|
| `llm.cognitive_mode` | `"dual"` = 大小脑动态路由；`"edge"` = 仅小脑；`"cloud"` = 仅大脑 |
| `llm_keys.dashscope` | 阿里云 DashScope API Key（瀑布流第 2 优先级） |
| `llm_keys.openai` | OpenAI API Key（可选） |

---

## 四、 文件结构

```text
core/
├── brain/
│   └── llm/
│       ├── base.py              # BaseCognitiveEngine (BaseLLMProvider)
│       ├── factory.py           # LLMProviderFactory
│       ├── router.py            # ModelRouter (大小脑路由)
│       ├── qwen_adapter.py      # QwenEngine
│       ├── local_adapter.py     # OllamaEngine (LocalAdapter)
│       └── credential_loader.py  # 瀑布流密钥读取
└── config/
    └── __init__.py              # get_effective_qwen_api_key 调用 credential_loader
```

---

## 五、 参考

- `docs/whitepaper/06_LAYER2_EDGE.md` 3.3 前额叶皮层
- `.cursor/rules/042-pluggable-cognitive-engines.mdc`
