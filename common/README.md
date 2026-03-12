# Common - 共享协议层 (The Bridge)

## 设计原则

**"共享协议，隔离实现"**

此目录是 Tier 1 (Cloud)、Tier 2 (Hive)、Tier 3 (Terminal) 之间的**唯一通信桥梁**。

### ✅ 允许的内容

- Protocol Buffers 定义文件 (`.proto`)
- Pydantic 数据模型 (`schemas/*.py`)
- 加密工具类 (`crypto/*.py`)
- 常量定义

### ❌ 严禁的内容

- **业务逻辑代码**
- 数据库访问代码
- 网络通信代码（除了协议定义）
- 任何 Tier 特定的实现

---

## 目录结构

```
common/
├── protocols/          # gRPC 协议定义
│   └── jachin_link.proto
├── schemas/           # 数据模型（Pydantic）
│   ├── manifest.py    # 插件清单模型
│   ├── telemetry.py   # 监控数据模型
│   └── sdui.py        # Server-Driven UI 模型 (Adaptive Cards)
└── crypto/            # 加密工具类
    └── signature.py   # 签名验证
```

---

## 使用指南

### Tier 1 (Cloud) 使用方式

```python
# ✅ 正确：引用 common 中的模型
from common.schemas.manifest import PluginManifest
from common.crypto.signature import SignatureVerifier

# ❌ 错误：引用 core 中的代码
# from core.system.plugin_manager import PluginManager  # 禁止！
```

### Tier 2 (Hive) 使用方式

```python
# ✅ 正确：引用 common 中的模型
from common.schemas.manifest import PluginManifest
from common.schemas.sdui import AdaptiveCard, TextBlock, SubmitAction
from common.protocols import jachin_link_pb2

# ❌ 错误：引用 cloud 中的代码
# from cloud.market_backend import MarketAPI  # 禁止！
```

### Server-Driven UI (SDUI) 使用示例

```python
from common.schemas.sdui import AdaptiveCard, TextBlock, SubmitAction, FontSize

# 插件开发者构建 UI
card = AdaptiveCard(
    body=[
        TextBlock(text="当前股价: $120", size=FontSize.LARGE),
        TextBlock(text="苹果公司 (AAPL)")
    ],
    actions=[
        SubmitAction(title="买入", id="buy_action", data={"action": "buy"})
    ]
)

# 序列化为 JSON，放入 PluginResponse.ui_render_schema
ui_schema = card.to_json()
```

---

## 版本管理

- `common/` 的版本变更需要**向后兼容**
- 新增字段使用 `Optional` 类型
- 删除字段需要先标记为 `deprecated`

---

**维护者**: Jachin-System Architecture Team
