# Model Abstraction Layer (MAL) - 模型抽象层

## 概述

MAL 层是 Jachin-System 的核心组件，实现了模型无关的 LLM 调用接口。所有业务逻辑必须通过此层调用 LLM，严禁直接调用特定模型的 API。

## 架构设计

```
BaseLLMProvider (抽象接口)
    ├── QwenAdapter (阿里云 Qwen)
    └── LocalAdapter (本地模型，OpenAI-compatible)

ModelRouter (智能路由)
    └── 根据任务复杂度选择 Provider

LLMProviderFactory (工厂模式)
    └── 创建和管理 Provider 实例
```

## 使用示例

### 1. 使用 Factory 创建 Provider

```python
from core.llm import LLMProviderFactory

# 创建 Qwen Provider
qwen = LLMProviderFactory.create_provider(
    "qwen",
    api_key="your-api-key",
    model="qwen-max"
)

# 创建 Local Provider
local = LLMProviderFactory.create_provider(
    "local",
    base_url="http://localhost:8000",
    model="qwen-7b-chat"
)
```

### 2. 使用 Router 智能路由

```python
from core.llm import LLMProviderFactory

# 创建 Router（自动从环境变量读取配置）
router = LLMProviderFactory.create_router()

# 处理用户请求（自动分析复杂度并路由）
response = await router.process_request(
    user_input="帮我写一个 Python 函数",
    context={"user_id": "user123"}
)
```

### 3. 手动路由

```python
from core.llm import ModelRouter, TaskComplexity

router = ModelRouter(
    qwen_adapter=qwen,
    local_adapter=local
)

# 分析任务复杂度
complexity = router.analyze_complexity("开灯")

# 路由到合适的 Provider
provider = router.route(complexity)

# 调用模型
messages = [{"role": "user", "content": "开灯"}]
response = await provider.chat(messages)
```

### 4. 流式响应

```python
async for chunk in provider.stream_chat(messages):
    print(chunk, end="", flush=True)
```

## 配置

### 环境变量

```bash
# Qwen 配置
QWEN_API_KEY=your-api-key
LLM_MODEL=qwen-max

# Local LLM 配置
LOCAL_LLM_URL=http://localhost:8000
LOCAL_LLM_MODEL=qwen-7b-chat
LOCAL_LLM_API_KEY=optional-api-key

# 默认 Provider
LLM_PROVIDER=qwen
```

### 配置文件

参考 `config/model_config.yaml` 了解详细的策略配置。

## 扩展

### 添加新的 Provider

1. 实现 `BaseLLMProvider` 接口
2. 在 `factory.py` 中注册新 Provider
3. 更新 `__init__.py` 导出

示例：

```python
# core/llm/openai_adapter.py
from .base import BaseLLMProvider

class OpenAIAdapter(BaseLLMProvider):
    # 实现接口方法
    pass

# core/llm/factory.py
elif provider_type == "openai":
    provider = OpenAIAdapter(**kwargs)
```

## 测试

```bash
# 运行测试
pytest tests/core/llm/

# 测试特定 Provider
pytest tests/core/llm/test_qwen_adapter.py
```

## 注意事项

1. **严禁在业务逻辑中直接调用模型 API**
2. **所有 Provider 必须实现完整的接口方法**
3. **Router 应该处理 Provider 不可用的情况（降级策略）**
4. **生产环境应配置适当的超时和重试机制**
