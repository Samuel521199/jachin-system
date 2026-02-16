# Qwen API 调用指南

## 概述

本文档详细说明如何使用 Qwen 的各种模型和调用方式，包括不同地域、多模态输入、工具调用等功能。

## 地域支持

Qwen 支持以下三个地域：

### 1. 华北2（北京）- 默认
- **地域代码**: `cn-beijing`
- **Base URL**: `https://dashscope.aliyuncs.com`
- **适用场景**: 中国大陆用户，延迟最低

### 2. 新加坡
- **地域代码**: `ap-singapore`
- **Base URL**: `https://dashscope.ap-southeast-1.aliyuncs.com`
- **适用场景**: 亚太地区用户

### 3. 美国（弗吉尼亚）
- **地域代码**: `us-virginia`
- **Base URL**: `https://dashscope.us-east-1.aliyuncs.com`
- **适用场景**: 北美地区用户

## 模型列表

### 文本模型
- `qwen-turbo` - 快速响应，适合简单对话
- `qwen-plus` - 平衡性能和成本
- `qwen-max` - 最强性能，支持联网搜索
- `qwen-flash` - 超快响应
- `qwen-coder` - 代码生成专用

### 视觉模型
- `qwen-vl` - 基础视觉理解
- `qwen-vl-max` - 最强视觉理解
- `qwen-vl-plus` - 平衡版视觉理解

### 全模态模型
- `qwen-omni` - 支持文本、图像、视频、音频
- `qwen3-omni` - 增强版全模态，支持工具调用和联网搜索

### 音频模型
- `qwen-audio` - 音频理解
- `qwen3-omni-captioner` - 音频字幕生成

## 调用方式

### 1. 文本输入（基础）

```python
from core.llm.factory import LLMProviderFactory
from core.llm.regions import Region

# 创建Provider（使用默认地域-北京）
provider = LLMProviderFactory.create_provider(
    "qwen-v2",
    api_key="your-api-key",
    model="qwen-max"
)

# 文本对话
messages = [{"role": "user", "content": "你好，介绍一下你自己"}]
response = await provider.chat(messages)
print(response)
```

### 2. 流式输出

```python
# 流式对话
async for chunk in provider.stream_chat(messages):
    print(chunk, end="", flush=True)
```

### 3. 图像输入

```python
from core.llm.call_types import ImageInput

# 准备图像输入
images = [
    ImageInput(image_url="https://example.com/image.jpg")
    # 或 ImageInput(image_base64="base64_string")
    # 或 ImageInput(image_bytes=image_bytes)
]

# 使用视觉模型
provider = LLMProviderFactory.create_provider(
    "qwen-v2",
    model="qwen-vl-max"  # 使用视觉模型
)

messages = [{"role": "user", "content": "描述这张图片"}]
response = await provider.chat_with_image(messages, images)
print(response)
```

### 4. 视频输入

```python
from core.llm.call_types import VideoInput

# 准备视频输入
videos = [VideoInput(video_url="https://example.com/video.mp4")]

# 使用全模态模型
provider = LLMProviderFactory.create_provider(
    "qwen-v2",
    model="qwen-omni"
)

messages = [{"role": "user", "content": "总结这个视频的主要内容"}]
response = await provider.chat_with_video(messages, videos)
print(response)
```

### 5. 音频输入

```python
from core.llm.call_types import AudioInput

# 准备音频输入
audios = [AudioInput(audio_url="https://example.com/audio.wav")]

# 使用音频模型
provider = LLMProviderFactory.create_provider(
    "qwen-v2",
    model="qwen-audio"
)

messages = [{"role": "user", "content": "转写这段音频"}]
response = await provider.chat_with_audio(messages, audios)
print(response)
```

### 6. 联网搜索

```python
from core.llm.call_types import WebSearchConfig

# 配置联网搜索
web_search = WebSearchConfig(
    enabled=True,
    max_results=5
)

# 使用支持联网搜索的模型
provider = LLMProviderFactory.create_provider(
    "qwen-v2",
    model="qwen-max"  # qwen-max 和 qwen3-omni 支持联网搜索
)

messages = [{"role": "user", "content": "今天北京的天气怎么样？"}]
response = await provider.chat_with_web_search(messages, web_search)
print(response)
```

### 7. 工具调用

```python
from core.llm.call_types import ToolCall

# 定义工具
tools = [
    ToolCall(
        name="get_weather",
        description="获取指定城市的天气信息",
        parameters={
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名称"}
            },
            "required": ["city"]
        }
    )
]

# 使用支持工具调用的模型
provider = LLMProviderFactory.create_provider(
    "qwen-v2",
    model="qwen-max"  # qwen-max, qwen-plus 等支持工具调用
)

messages = [{"role": "user", "content": "查询北京的天气"}]
result = await provider.chat_with_tools(messages, tools)
print(result["content"])
print(result["tool_calls"])  # 工具调用结果
```

### 8. 异步调用

```python
# 异步调用（后台任务）
task_id = await provider.async_chat(messages)
# 稍后查询结果
result = await provider.get_async_result(task_id)
```

### 9. 文档理解

```python
from core.llm.call_types import DocumentInput

# 准备文档输入
documents = [
    DocumentInput(
        document_url="https://example.com/document.pdf",
        document_type="pdf"
    )
]

messages = [{"role": "user", "content": "总结这个文档的主要内容"}]
response = await provider.chat_with_document(messages, documents)
print(response)
```

## 多地域使用

```python
from core.llm.regions import Region

# 使用新加坡地域
provider = LLMProviderFactory.create_provider(
    "qwen-v2",
    api_key="your-api-key",
    model="qwen-max",
    region=Region.AP_SINGAPORE
)

# 使用美国地域
provider = LLMProviderFactory.create_provider(
    "qwen-v2",
    api_key="your-api-key",
    model="qwen-max",
    region=Region.US_VIRGINIA
)
```

## 模型能力检查

```python
# 检查模型是否支持某种功能
if provider.supports_call_type(CallType.IMAGE):
    # 使用图像输入
    pass

# 获取模型信息
info = provider.get_model_info()
print(info["capabilities"])
```

## 注意事项

1. **流式输出要求**: 某些模型（如 `qwen-vl`, `qwen-omni`, `qvq`）仅支持流式输出，调用 `chat()` 方法会自动转换为流式并收集结果。

2. **地域选择**: 根据用户地理位置选择合适的地域，以获得最低延迟。

3. **API Key**: 确保API Key有足够的权限和配额。

4. **模型选择**: 
   - 简单对话：`qwen-turbo` 或 `qwen-flash`
   - 复杂任务：`qwen-plus` 或 `qwen-max`
   - 视觉任务：`qwen-vl-max`
   - 全模态：`qwen3-omni`

## 配置示例

### 环境变量配置

```bash
# .env 文件
QWEN_API_KEY=your-api-key
LLM_MODEL=qwen-max
QWEN_REGION=cn-beijing  # 可选：cn-beijing, ap-singapore, us-virginia
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
