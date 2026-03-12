# Qwen API 架构设计文档

> **v8.0 统一**：本设计已并入 **可插拔认知引擎 (Pluggable Cognitive Engines)**。  
> 详见 `docs/whitepaper/PLUGGABLE_COGNITIVE_ENGINES.md`。以下为历史实现细节，供扩展参考。

## 概述

本文档描述了 Jachin-System 中 Qwen API 的架构设计，包括多地域支持、多模态调用、工具调用等功能。

## 架构层次

```
┌─────────────────────────────────────────┐
│         API Layer (FastAPI)            │
│  - /api/v1/chat (基础接口)              │
│  - /api/v2/chat (增强接口)              │
└─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────┐
│      LLM Provider Factory               │
│  - 创建和管理 Provider 实例              │
│  - 支持 qwen, qwen-v2, local           │
└─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────┐
│      BaseLLMProvider (抽象接口)          │
│  - chat()                                │
│  - stream_chat()                         │
│  - chat_with_image()                     │
│  - chat_with_web_search()               │
│  - chat_with_tools()                    │
│  - ...                                  │
└─────────────────────────────────────────┘
                    │
        ┌───────────┴───────────┐
        │                       │
        ▼                       ▼
┌───────────────┐      ┌───────────────┐
│ QwenAdapter   │      │ LocalAdapter  │
│ (基础版)       │      │ (本地模型)     │
└───────────────┘      └───────────────┘
        │
        ▼
┌─────────────────────────────────────────┐
│      QwenAdapterV2 (增强版)              │
│  - 多地域支持                            │
│  - 多模态支持                            │
│  - 工具调用支持                          │
│  - 联网搜索支持                          │
└─────────────────────────────────────────┘
                    │
        ┌───────────┴───────────┐
        │                       │
        ▼                       ▼
┌───────────────┐      ┌───────────────┐
│ Region Config │      │ Model Config  │
│ - 北京        │      │ - 能力定义     │
│ - 新加坡      │      │ - 模型列表     │
│ - 美国        │      │ - 特性检查     │
└───────────────┘      └───────────────┘
```

## 核心组件

### 1. 地域配置 (`regions.py`)

支持三个地域：
- **华北2（北京）**: `cn-beijing` - 默认地域
- **新加坡**: `ap-singapore`
- **美国（弗吉尼亚）**: `us-virginia`

每个地域包含：
- Base URL
- API Endpoint
- 描述信息

### 2. 调用方式定义 (`call_types.py`)

定义了9种调用方式：
1. **TEXT** - 文本输入
2. **STREAM** - 流式输出
3. **IMAGE** - 图像输入
4. **VIDEO** - 视频输入
5. **AUDIO** - 音频输入
6. **WEB_SEARCH** - 联网搜索
7. **TOOL_CALL** - 工具调用
8. **ASYNC** - 异步调用
9. **DOCUMENT** - 文档理解

### 3. 模型配置 (`qwen_models.py`)

定义了所有Qwen模型及其能力：
- 文本模型：qwen-turbo, qwen-plus, qwen-max等
- 视觉模型：qwen-vl, qwen-vl-max等
- 全模态模型：qwen-omni, qwen3-omni
- 音频模型：qwen-audio等

每个模型的能力包括：
- 是否支持文本/图像/视频/音频
- 是否支持流式输出
- 是否仅支持流式输出（某些模型要求）
- 是否支持工具调用、联网搜索等

### 4. QwenAdapterV2 (`qwen_adapter_v2.py`)

增强版适配器，实现：
- 多地域支持
- 多模态输入（图像、视频、音频）
- 联网搜索
- 工具调用
- 自动处理流式要求（某些模型仅支持流式）

## 使用示例

### 基础文本对话

```python
from core.llm.factory import LLMProviderFactory

provider = LLMProviderFactory.create_provider("qwen-v2")
response = await provider.chat([{"role": "user", "content": "你好"}])
```

### 多地域使用

```python
from core.llm.regions import Region

provider = LLMProviderFactory.create_provider(
    "qwen-v2",
    region=Region.AP_SINGAPORE  # 使用新加坡地域
)
```

### 图像输入

```python
from core.llm.call_types import ImageInput

images = [ImageInput(image_url="https://example.com/image.jpg")]
provider = LLMProviderFactory.create_provider("qwen-v2", model="qwen-vl-max")
response = await provider.chat_with_image(messages, images)
```

### 联网搜索

```python
from core.llm.call_types import WebSearchConfig

web_search = WebSearchConfig(enabled=True, max_results=5)
provider = LLMProviderFactory.create_provider("qwen-v2", model="qwen-max")
response = await provider.chat_with_web_search(messages, web_search)
```

### 工具调用

```python
from core.llm.call_types import ToolCall

tools = [
    ToolCall(
        name="get_weather",
        description="获取天气",
        parameters={"type": "object", "properties": {...}}
    )
]
provider = LLMProviderFactory.create_provider("qwen-v2", model="qwen-max")
result = await provider.chat_with_tools(messages, tools)
```

## API 端点

### V1 API (基础)

- `POST /api/v1/chat/` - 基础文本聊天
- `GET /api/v1/chat/health` - 健康检查
- `POST /api/chat` - 简化版聊天（MVP兼容）

### V2 API (增强)

- `POST /api/v2/chat/text` - 文本聊天（支持流式）
- `POST /api/v2/chat/image` - 图像聊天
- `POST /api/v2/chat/web-search` - 联网搜索聊天
- `POST /api/v2/chat/tools` - 工具调用
- `GET /api/v2/chat/capabilities` - 获取模型能力

## 配置

### 环境变量

```bash
# .env 文件
QWEN_API_KEY=your-api-key
LLM_PROVIDER=qwen-v2
LLM_MODEL=qwen-max
QWEN_REGION=cn-beijing  # cn-beijing, ap-singapore, us-virginia
```

### 代码配置

```python
from core.llm.factory import LLMProviderFactory
from core.llm.regions import Region

provider = LLMProviderFactory.create_provider(
    "qwen-v2",
    api_key="your-api-key",
    model="qwen-max",
    region=Region.CN_BEIJING,
    temperature=0.7,
    max_tokens=2000
)
```

## 扩展性设计

### 添加新平台

1. 创建新的 Adapter 类，继承 `BaseLLMProvider`
2. 实现必需的方法（chat, stream_chat, embed等）
3. 可选实现扩展方法（chat_with_image等）
4. 在 `LLMProviderFactory` 中注册

### 添加新调用方式

1. 在 `call_types.py` 中添加新的 `CallType`
2. 在 `BaseLLMProvider` 中添加抽象方法
3. 在各个 Adapter 中实现该方法
4. 在 API 层添加对应的端点

### 添加新地域

1. 在 `regions.py` 中添加新的 `Region` 枚举值
2. 添加对应的 `RegionConfig` 配置
3. 更新文档

## 注意事项

1. **流式要求**: 某些模型（qwen-vl, qwen-omni等）仅支持流式输出，调用 `chat()` 会自动转换为流式并收集结果。

2. **模型选择**: 根据任务类型选择合适的模型：
   - 简单对话：qwen-turbo
   - 复杂任务：qwen-max
   - 视觉任务：qwen-vl-max
   - 全模态：qwen3-omni

3. **地域选择**: 根据用户地理位置选择合适的地域以获得最低延迟。

4. **API Key**: 确保API Key有足够的权限和配额。

5. **错误处理**: 所有方法都包含错误处理，会抛出适当的异常。

## 未来扩展

- [ ] 视频输入完整实现
- [ ] 音频输入完整实现
- [ ] 文档理解完整实现
- [ ] 异步调用完整实现
- [ ] 批量调用支持
- [ ] 缓存机制
- [ ] 重试机制
- [ ] 监控和日志
