# 可插拔向量引擎 (Pluggable Vector Engine)

**文档类型**: 白皮书 · 技术设计  
**版本**: V2.3  
**更新日期**: 2026-06  
**定位**: **L2** Semantic Router 的 Embedding 层（与 L3 Memory Nexus **分立**）

---

## 一、设计哲学

用户可按场景选择：

- **Cloud**：OpenAI 兼容 API，零本地下载  
- **Edge/Local**：ONNX / sentence-transformers，断网可用  

配置入口：`~/.jachin/nexus_config.json` → `embedding.embedding_mode`。

---

## 二、适用范围（重要）

| 子系统 | Embedding 实现 | 存储 |
|--------|------------------|------|
| **L2 Semantic Router** | `core/embedding/` + `get_embedder()` | LanceDB `skills` 表 |
| **L2 记忆检索** | 同上（hybrid search） | LanceDB `memories` |
| **L3 Memory Nexus** | **FastEmbed**（进程内） | SQLite `drawers` 表 |

**切换 L2 embedding_mode 时需重建 LanceDB 表**（Cloud 1536 维 vs Edge 384 维）。  
L3 Nexus **不**读取 `embedding_mode`；见 [MEMORY_NEXUS_L3.md](../architecture/MEMORY_NEXUS_L3.md)。

---

## 三、双核架构

| 引擎 | 实现 | 场景 |
|------|------|------|
| ☁️ Cloud | `OpenAIEmbedder` | 默认；OpenAI `text-embedding-3-small` |
| 🛡️ Edge | `ONNXEmbedder` | `all-MiniLM-L6-v2`，本地 ONNX |

```python
# core/embedding/__init__.py
def get_embedder(config) -> BaseEmbedder: ...
```

---

## 四、与 Semantic Router 集成

- **组件**：`core/vector_router.py` → `SemanticRouter.match_local_skill(intent)`
- **L3 编排封装**：`l3_node/orchestration/skill_routing.py` → `suggest_skills_from_intent()`
- **配置**：`nexus_config.orchestration` — `skill_routing_enabled`、`vector_router_threshold`
- **安全红线**：云端「意念下载」未知 Skill **须 HITL**，禁止静默执行

详见 [ORCHESTRATION_ARCHITECTURE.md](../ORCHESTRATION_ARCHITECTURE.md)。

---

## 五、配置

```json
{
  "embedding": {
    "embedding_mode": "cloud"
  }
}
```

| 值 | 说明 |
|----|------|
| `"cloud"` | OpenAI 兼容 API |
| `"local"` / `"edge"` | 本地 ONNX |

---

## 六、文件结构

```text
core/
├── embedding/__init__.py    # BaseEmbedder, get_embedder
├── vector_router.py         # SemanticRouter
└── db/l2_memory_lancedb.py  # LanceDB 记忆层
```

---

## 七、参考

- [06_LAYER2_EDGE.md](./06_LAYER2_EDGE.md)
- [MEMORY_SCORING.md](../MEMORY_SCORING.md)
- `.cursor/rules/040-rag-memory.mdc`
