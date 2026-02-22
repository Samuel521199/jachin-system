# 语音系统完整指南

## 目录

1. [系统概述](#系统概述)
2. [API 端点](#api-端点)
3. [使用示例](#使用示例)
4. [语音录制按钮使用](#语音录制按钮使用)
5. [配置](#配置)

---

## 系统概述

Jachin-System 现在支持完整的语音交互功能，包括：
- **语音识别（STT）**：将语音转换为文本
- **语音合成（TTS）**：将文本转换为语音
- **语音聊天**：完整的语音对话流程
- **三种语音模式**：录音 (Push-to-Talk)、唤醒 (Wake-Up)、识别 (Continuous)，以及**安全指令协议**（命令前缀、二次确认、Alert Mode）→ 详见 [VOICE_MODES_AND_SAFETY_PROTOCOL.md](./VOICE_MODES_AND_SAFETY_PROTOCOL.md) 与 [TTS_RULES_AND_ARCHITECTURE.md](./TTS_RULES_AND_ARCHITECTURE.md)。

### 架构设计

```
用户语音输入
    ↓
[STT] 语音识别 → 文本
    ↓
[LLM] 文本对话 → 回复文本
    ↓
[TTS] 语音合成 → 语音回复
    ↓
返回给用户
```

---

## API 端点

### 1. 语音识别

**POST** `/api/v2/voice/recognize`

将上传的音频文件识别为文本。

**请求格式：**
- `multipart/form-data`
- `audio_file`: 音频文件（可选）
- `audio_base64`: Base64编码的音频数据（可选）
- `format`: 音频格式（wav, mp3, m4a等）
- `language`: 语言代码（zh-CN, en-US等）

**响应：**
```json
{
  "text": "识别出的文本",
  "language": "zh-CN",
  "confidence": 0.95
}
```

### 2. 语音合成

**POST** `/api/v2/voice/synthesize`

将文本合成为语音。

**请求：**
```json
{
  "text": "要合成的文本",
  "voice": "zh-CN-XiaoxiaoNeural",
  "language": "zh-CN",
  "speed": 1.0,
  "pitch": 1.0
}
```

**响应：**
- 音频文件（WAV格式）

### 3. 流式语音合成

**POST** `/api/v2/voice/synthesize-stream`

流式合成语音，适合长文本。

### 4. 语音聊天（完整流程）

**POST** `/api/v2/voice/chat`

完整的语音对话流程：
1. 接收语音输入
2. 识别为文本
3. 调用 LLM 生成回复
4. 合成语音回复
5. 返回文本和音频

**请求格式：**
- `multipart/form-data`
- `audio_file`: 音频文件
- `audio_base64`: Base64编码的音频数据
- `format`: 音频格式
- `language`: 语言代码
- `return_audio`: 是否返回语音回复（默认true）
- `voice`: TTS语音名称
- `speed`: TTS语速（0.5-2.0）
- `pitch`: TTS音调（0.5-2.0）

**响应：**
```json
{
  "text": "AI的文本回复",
  "user_text": "用户识别的文本",
  "audio_base64": "<base64编码的音频>",
  "audio_format": "wav"
}
```

### 5. 列出可用语音

**GET** `/api/v2/voice/voices?language=zh-CN`

获取可用的语音列表。

---

## 使用示例

### Python 客户端示例

```python
import requests
import base64

# 1. 语音识别
with open("question.wav", "rb") as f:
    files = {"audio_file": f}
    data = {"format": "wav", "language": "zh-CN"}
    response = requests.post(
        "http://localhost:8000/api/v2/voice/recognize",
        files=files,
        data=data
    )
    result = response.json()
    print(f"识别结果: {result['text']}")

# 2. 语音合成
response = requests.post(
    "http://localhost:8000/api/v2/voice/synthesize",
    json={
        "text": "你好，我是Jachin助手",
        "voice": "zh-CN-XiaoxiaoNeural",
        "language": "zh-CN"
    }
)
with open("reply.wav", "wb") as f:
    f.write(response.content)

# 3. 语音聊天
with open("question.wav", "rb") as f:
    files = {"audio_file": f}
    data = {
        "format": "wav",
        "language": "zh-CN",
        "return_audio": True,
        "voice": "zh-CN-XiaoxiaoNeural"
    }
    response = requests.post(
        "http://localhost:8000/api/v2/voice/chat",
        files=files,
        data=data
    )
    result = response.json()
    print(f"文本回复: {result['text']}")
    
    # 保存音频回复
    if result['audio_base64']:
        audio_data = base64.b64decode(result['audio_base64'])
        with open("reply.wav", "wb") as f:
            f.write(audio_data)
```

### JavaScript/TypeScript 客户端示例

```typescript
// 语音识别
async function recognizeAudio(audioFile: File) {
  const formData = new FormData();
  formData.append('audio_file', audioFile);
  formData.append('format', 'wav');
  formData.append('language', 'zh-CN');
  
  const response = await fetch('http://localhost:8000/api/v2/voice/recognize', {
    method: 'POST',
    body: formData
  });
  
  return await response.json();
}

// 语音合成
async function synthesizeSpeech(text: string) {
  const response = await fetch('http://localhost:8000/api/v2/voice/synthesize', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      text,
      voice: 'zh-CN-XiaoxiaoNeural',
      language: 'zh-CN'
    })
  });
  
  return await response.blob();
}

// 语音聊天
async function voiceChat(audioFile: File) {
  const formData = new FormData();
  formData.append('audio_file', audioFile);
  formData.append('format', 'wav');
  formData.append('language', 'zh-CN');
  formData.append('return_audio', 'true');
  formData.append('voice', 'zh-CN-XiaoxiaoNeural');
  
  const response = await fetch('http://localhost:8000/api/v2/voice/chat', {
    method: 'POST',
    body: formData
  });
  
  return await response.json();
}
```

---

## 语音录制按钮使用

### 功能说明

在聊天界面中添加了语音录制按钮，支持完整的语音对话流程。

### 界面位置

#### 对话气泡窗口（chat.tsx）
- 位置：输入框左侧
- 图标：🎤 麦克风图标（未录音时）/ ⏹ 停止图标（录音时）

#### 主应用聊天面板（ChatPanel.tsx）
- 位置：输入框左侧
- 图标：🎤 麦克风图标（未录音时）/ ⏹ 停止图标（录音时）

### 使用方法

#### 1. 开始录音
1. 点击麦克风图标 🎤
2. 浏览器会请求麦克风权限（首次使用）
3. 允许权限后，按钮变为红色并显示"停止"
4. 开始说话

#### 2. 停止录音
1. 点击停止图标 ⏹
2. 录音自动停止
3. 系统自动处理：
   - 语音识别（STT）
   - 调用 LLM 生成回复
   - 语音合成（TTS）
   - 播放语音回复

#### 3. 查看结果
- 识别出的文本会显示在消息中（标记为 🎤 [语音]）
- AI 的文本回复会显示在消息中
- 语音回复会自动播放

### 状态提示

界面会显示当前状态：
- **正在录音...** - 红色提示，按钮闪烁
- **录音完成，正在处理...** - 紫色提示
- **正在处理语音...** - 处理中
- **错误信息** - 红色提示（如果有错误）

### 精灵状态同步

在对话气泡窗口中，精灵状态会自动更新：
- **录音时**：`listening`（监听状态）
- **处理中**：`thinking`（思考状态）
- **播放回复**：`speaking`（说话状态）
- **完成后**：`idle`（待机状态）

### 注意事项

1. **浏览器权限**：首次使用需要允许麦克风访问权限
2. **HTTPS要求**：某些浏览器在非 HTTPS 环境下可能限制麦克风（localhost 通常可以）
3. **录音时长**：建议每次录音不超过 60 秒
4. **网络连接**：语音合成需要网络连接（Edge TTS）
5. **音频格式**：自动使用 WAV 格式

### 故障排除

#### 无法访问麦克风
- 检查浏览器权限设置
- 确认系统麦克风权限已开启
- 尝试刷新页面重新请求权限

#### 录音没有反应
- 检查后端服务是否运行
- 查看浏览器控制台错误信息
- 确认网络连接正常

#### 语音识别不准确
- 确保环境安静
- 说话清晰
- 靠近麦克风

#### 语音回复没有播放
- 检查浏览器音频权限
- 确认系统音量已开启
- 查看浏览器控制台错误信息

### 技术实现

- **录音**：使用浏览器 `MediaRecorder` API
- **语音识别**：调用 `/api/v2/voice/recognize` API
- **语音合成**：调用 `/api/v2/voice/synthesize` API
- **语音聊天**：调用 `/api/v2/voice/chat` API（完整流程）
- **音频播放**：使用 HTML5 `Audio` API

---

## 配置

### 默认提供商

在 `core/api/voice.py` 中配置：

```python
stt_provider = STTProvider.OPENAI_WHISPER  # 或 STTProvider.ALIYUN
tts_provider = TTSProvider.EDGE_TTS  # 或 TTSProvider.ALIYUN
```

### 环境变量

```bash
# 阿里云API Key（如果使用阿里云服务）
QWEN_AI_API_KEY=your_api_key_here
```

---

## 集成到桌面客户端

桌面客户端（Tauri）可以集成语音功能：

1. **录音**：使用浏览器的 `MediaRecorder` API
2. **播放**：使用 HTML5 `Audio` API
3. **调用后端API**：使用 `fetch` 或 `axios`

示例代码请参考 `clients/desktop/src/components/VoiceChat.tsx`。

---

## 下一步

- [ ] 前端语音录制组件
- [ ] 实时语音流式处理
- [ ] 语音唤醒词检测
- [ ] 多语言支持增强
- [ ] 语音情感识别
- [ ] 实时语音流式识别
- [ ] 录音可视化（波形显示）
- [ ] 录音时长显示
