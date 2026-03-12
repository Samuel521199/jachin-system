# 可插拔向量引擎 (Pluggable Vector Engine)

**文档类型**: 白皮书 · 技术设计  
**版本**: v8.0 (The Singularity OS)  
**定位**: 全域向量路由 (Semantic Router) 的 Embedding 层

---

## 一、 设计哲学

**「做选择题」是小孩子和传统软件的思维，真正的数字生命底座应提供「全都要」的包容性。**

- 强行绑定 OpenAI → 得罪追求「纯物理断网隔离」的极客和涉密企业
- 强行推行本地 ONNX → 让追求极致速度、零本地负担的轻量用户感到冗余

**由用户按需自选**：在 Layer 3 设置界面轻轻拨动 **"Local AI Mode (纯本地隐私模式)"** 开关即可切换。

---

## 二、 双核架构 (策略模式)

| 引擎 | 代号 | 实现类 | 适用场景 |
|------|------|--------|----------|
| **极速云端核** | ☁️ Cloud | `OpenAIEmbedder` | 对隐私要求不高、追求本地零负担、极致速度 |
| **深渊边缘核** | 🛡️ Edge | `ONNXEmbedder` | 军工、医疗、金融等需绝对断网隔离的场景 |

### 2.1 极速云端核 (Cloud Engine)

- **组件**: `core/embedding/` → `OpenAIEmbedder`
- **依赖**: `openai` 包，调用 OpenAI / 兼容 API
- **模型**: 默认 `text-embedding-3-small` (1536 维)
- **特点**: 零本地下载、零推理负担

### 2.2 深渊边缘核 (Edge Engine)

- **组件**: `core/embedding/` → `ONNXEmbedder`
- **依赖**: `sentence-transformers`，可选 `onnxruntime` 获得更轻量推理
- **模型**: 默认 `all-MiniLM-L6-v2` (~90MB，384 维)
- **特点**: 首次使用自动下载，之后**拔掉网线也能完成技能「顿悟」**

---

## 三、 接口与实现

### 3.1 抽象基类

```python
class BaseEmbedder(ABC):
    @abstractmethod
    async def embed_text(self, text: str) -> list[float]:
        """将文本转换为向量。"""
        ...
```

### 3.2 工厂函数

```python
def get_embedder(config: dict | None = None) -> BaseEmbedder:
    """从 ~/.jachin/nexus_config.json 读取 embedding_mode，动态实例化"""
```

- `embedding_mode: "cloud"` → `OpenAIEmbedder`
- `embedding_mode: "local"` 或 `"edge"` → `ONNXEmbedder`

### 3.3 容错与日志

- **ONNXEmbedder**：若未安装 `onnxruntime`，优雅日志提示，建议 `pip install onnxruntime`
- **启动时**：Rich 终端打印当前引擎，例如：
  - `[INFO] Vector Router initialized. Engine: ☁️ Cloud (OpenAI)`
  - `[INFO] Vector Router initialized. Engine: 🛡️ Edge (ONNX Local)`

---

## 四、 配置驱动

**配置文件**: `~/.jachin/nexus_config.json`

```json
{
  "access_token": "...",
  "embedding": {
    "embedding_mode": "cloud"
  }
}
```

| 字段 | 值 | 说明 |
|------|-----|------|
| `embedding.embedding_mode` | `"cloud"` | ☁️ OpenAI API（默认） |
| `embedding.embedding_mode` | `"local"` / `"edge"` | 🛡️ 本地 ONNX 断网可用 |
| `embedding_mode` (顶层) | 同上 | 兼容简写 |

Layer 3 设置界面的 **"Local AI Mode (纯本地隐私模式)"** 开关 → 写入 `embedding_mode: "local"`。

---

## 五、 与 Semantic Router 的集成

- **组件**: `core/vector_router.py` → `SemanticRouter`
- **注入**: 初始化时通过 `get_embedder(config)` 获取 Embedder，注入 `SemanticRouter`
- **存储**: LanceDB (`~/.jachin/vector_db/`)，skills 向量表
- **不破坏**: 原有 LanceDB / SQLite-VSS 存储逻辑，仅将「生成向量」这一步解耦

### 5.1 向量维度注意

| 引擎 | 维度 |
|------|------|
| Cloud (text-embedding-3-small) | 1536 |
| Edge (all-MiniLM-L6-v2) | 384 |

**切换模式时需重建 LanceDB `skills` 表**，否则检索会出错。

---

## 六、 文件结构

```text
core/
├── embedding/
│   └── __init__.py    # BaseEmbedder, OpenAIEmbedder, ONNXEmbedder, get_embedder
├── vector_router.py   # SemanticRouter，注入 BaseEmbedder
└── ...
```

---

## 七、 参考

- `docs/whitepaper/06_LAYER2_EDGE.md` 3.5 全域向量路由
- `docs/whitepaper/04_FILE_STRUCTURE.md` 用户配置
- `.cursor/rules/040-rag-memory.mdc`
