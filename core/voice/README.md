# Voice Module - 语音模块

## 概述

语音模块提供语音识别（STT）和语音合成（TTS）功能，支持多种服务提供商。

## 功能特性

### 语音识别（STT）
- ✅ 阿里云语音识别
- ✅ OpenAI Whisper（开源）
- 🔄 百度语音识别（待实现）
- 🔄 腾讯语音识别（待实现）

### 语音合成（TTS）
- ✅ Microsoft Edge TTS（免费，开源）
- ✅ 阿里云语音合成
- 🔄 百度语音合成（待实现）
- 🔄 腾讯语音合成（待实现）

## 快速开始

### 1. 安装依赖

```bash
pip install edge-tts openai-whisper
```

### 2. 使用语音识别

```python
from core.voice import SpeechToText, STTProvider

# 使用 Edge TTS（免费）
stt = SpeechToText(provider=STTProvider.OPENAI_WHISPER)

# 读取音频文件
with open("audio.wav", "rb") as f:
    audio_data = f.read()

# 识别语音
text = await stt.recognize(
    audio_data=audio_data,
    format="wav",
    language="zh-CN"
)

print(f"识别结果: {text}")
```

### 3. 使用语音合成

```python
from core.voice import TextToSpeech, TTSProvider

# 使用 Edge TTS（免费）
tts = TextToSpeech(provider=TTSProvider.EDGE_TTS)

# 合成语音
audio_data = await tts.synthesize(
    text="你好，我是Jachin助手",
    voice="zh-CN-XiaoxiaoNeural",
    language="zh-CN",
    speed=1.0,
    pitch=1.0
)

# 保存音频文件
with open("output.wav", "wb") as f:
    f.write(audio_data)
```

### 4. 流式语音合成

```python
# 流式合成（适合长文本）
async for chunk in tts.synthesize_stream(
    text="这是一段很长的文本...",
    voice="zh-CN-XiaoxiaoNeural"
):
    # 实时处理音频块
    process_audio_chunk(chunk)
```

## API 端点

### 语音识别

**POST** `/api/v2/voice/recognize`

```bash
curl -X POST "http://localhost:8000/api/v2/voice/recognize" \
  -F "audio_file=@audio.wav" \
  -F "format=wav" \
  -F "language=zh-CN"
```

或使用 Base64：

```bash
curl -X POST "http://localhost:8000/api/v2/voice/recognize" \
  -F "audio_base64=<base64_encoded_audio>" \
  -F "format=wav" \
  -F "language=zh-CN"
```

### 语音合成

**POST** `/api/v2/voice/synthesize`

```bash
curl -X POST "http://localhost:8000/api/v2/voice/synthesize" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "你好，我是Jachin助手",
    "voice": "zh-CN-XiaoxiaoNeural",
    "language": "zh-CN",
    "speed": 1.0,
    "pitch": 1.0
  }' \
  --output speech.wav
```

### 语音聊天（完整流程）

**POST** `/api/v2/voice/chat`

```bash
curl -X POST "http://localhost:8000/api/v2/voice/chat" \
  -F "audio_file=@question.wav" \
  -F "format=wav" \
  -F "language=zh-CN" \
  -F "return_audio=true" \
  -F "voice=zh-CN-XiaoxiaoNeural"
```

响应：
```json
{
  "text": "这是AI的文本回复",
  "audio_base64": "<base64_encoded_audio>",
  "audio_format": "wav"
}
```

### 列出可用语音

**GET** `/api/v2/voice/voices?language=zh-CN`

```bash
curl "http://localhost:8000/api/v2/voice/voices?language=zh-CN"
```

## 配置

### 环境变量

```bash
# 阿里云API Key（用于阿里云STT/TTS）
QWEN_AI_API_KEY=your_api_key_here

# 或使用其他名称
QWEN_API_KEY=your_api_key_here
DASHSCOPE_API_KEY=your_api_key_here
# 若与主 LLM 一致使用分区域百炼 Key，见仓库 docs/DASHSCOPE_REGIONAL_KEYS.md
```

### 代码配置

```python
# 在 api/voice.py 中修改默认提供商
stt_provider = STTProvider.OPENAI_WHISPER  # 或 STTProvider.ALIYUN
tts_provider = TTSProvider.EDGE_TTS  # 或 TTSProvider.ALIYUN
```

## Edge TTS 语音列表

### 中文语音

- `zh-CN-XiaoxiaoNeural` - 晓晓（女声，推荐）
- `zh-CN-YunyangNeural` - 云扬（男声）
- `zh-CN-YunxiNeural` - 云希（男声）
- `zh-CN-XiaoyiNeural` - 晓伊（女声）
- `zh-CN-YunjianNeural` - 云健（男声）

### 英文语音

- `en-US-AriaNeural` - Aria（女声）
- `en-US-GuyNeural` - Guy（男声）
- `en-US-JennyNeural` - Jenny（女声）

完整列表请访问：https://github.com/rany2/edge-tts/blob/main/src/edge_tts/voices.json

## 注意事项

1. **Edge TTS** 是免费的，但需要网络连接
2. **Whisper** 是开源的，但首次使用需要下载模型（可能较大）
3. **阿里云服务** 需要 API Key，可能有使用限制
4. 音频格式支持：WAV, MP3, M4A 等（取决于提供商）

## 故障排除

### Whisper 模型下载失败

```bash
# 手动下载模型
python -c "import whisper; whisper.load_model('base')"
```

### Edge TTS 连接失败

检查网络连接，Edge TTS 需要访问 Microsoft Edge 的 TTS 服务。

### 阿里云 API 错误

检查 API Key 是否正确设置，以及是否有足够的配额。
