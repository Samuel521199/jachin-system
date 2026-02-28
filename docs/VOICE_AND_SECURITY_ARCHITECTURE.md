# Jachin (SagotBot) 语音交互与安全路由架构设计

## 第一部分：核心设计理念与数据流转

### 1. 核心设计理念 (Core Philosophy)

#### 双轨输入 (Dual Pipeline)

同时支持两种输入方式，用户在 UI 上可自由切换：

- **智能连续监听 (VAD)**：后台持续切分语音，解放双手。
- **传统按键录音 (PTT)**：按住说话、松开发送，行为明确、资源占用低。

#### 边缘大脑决策 (Edge-Heavy Routing)

- **端侧 (Layer 3)**：只做“无脑”的音频切分与发送，不做意图判断。
- **家庭数据中心 (Layer 2)**：意图判断、声纹识别、安全拦截全部收敛到此处。

#### 三道防线 (Three-Tier Defense)

| 防线 | 问题 | 技术 |
|------|------|------|
| 1 | **你是谁？** | Voiceprint / 声纹识别 (CAM++) |
| 2 | **你在跟谁说话？** | Semantic Router / 意图路由器 |
| 3 | **你要干什么？** | Action 2FA / 高危指令二次确认 |

---

### 2. 交互模式与数据流转 (Interaction Flow)

#### 阶段 A：音频采集层 (Layer 3 - Client)

系统提供两种并行的音频采集策略：

**模式 1：按键录音 (Manual / PTT)**

- **逻辑**：用户按住按钮 → 录音 → 松开按钮 → 直接发送 `wav_base64`。
- **特点**：最安全、零持续资源消耗（适合低端设备）。
- **后端策略**：此模式下的音频**默认信任**，跳过“是否在跟机器人说话”的判断，**必定回应**。

**模式 2：智能连续监听 (VAD Auto)**

- **逻辑**：用户点击开启 VAD → 系统后台持续静默切分（如 800ms 尾音）→ 发送 `wav_base64`。
- **特点**：全解放双手；在后端**必须**经过严格的“意图路由器”过滤，避免误触发。

---

#### 阶段 B：识别与鉴权层 (Layer 2 - Brain: STT & Auth)

Layer 2 收到 `wav_base64` 后，**并行**执行：

1. **STT (Paraformer)**：将音频转录为文本。
2. **声纹识别 (CAM++)**（可选/进阶）：提取音频特征，比对本地特征库，输出 `User: Owner` 或 `User: Guest`。

---

#### 阶段 C：语义路由层 (Layer 2 - Brain: Semantic Router)

将转录文本送入**极速本地 LLM**（如 Qwen-7B）进行“打标”。

**输入示例**：

```json
{
  "text": "今天天气真热",
  "input_mode": "VAD",
  "user": "Guest"
}
```

**路由规则**：

| 条件 | 标签 | 说明 |
|------|------|------|
| `input_mode == "Manual"` | **ENGAGE** | 按键录音必定回复 |
| 没有叫名字，且只是感叹句 | **IGNORE** | 不回应，仅入记忆池 |
| 明确查询或指令 | **ENGAGE** | 正常进入执行与回复 |

---

#### 阶段 D：执行与安全层 (Layer 2 - Brain: Execution)

**若判定为 IGNORE**

- 将文本**悄悄存入** ContextBuffer（最近 5 分钟记忆池）。
- **不发出任何声音**，不调用 TTS。

**若判定为 ENGAGE**

1. **检查是否涉及系统级 / 高危 Skill**（如删除、关机等）。
2. **若是高危指令**：
   - 检查是否为 **Owner**；
   - 若为 Guest 或需二次确认，向客户端下发 **REQUIRE_CONFIRMATION** 状态（如 UI 变红，要求用户确认）。
3. **若是普通闲聊**：正常生成回复并 TTS 播报。

---

### 3. 数据流小结

```
Layer 3 (Client)                    Layer 2 (Brain)
─────────────────                   ─────────────────
[PTT] 按住→录音→松手→wav_base64  ──┐
                                  ├──→ STT (Paraformer)
[VAD] 持续切分→wav_base64  ────────┘    + 声纹 (CAM++) → user
                                              ↓
                                    Semantic Router (Qwen-7B)
                                    → ENGAGE / IGNORE
                                              ↓
                                    IGNORE → ContextBuffer only
                                    ENGAGE → 高危检查 → 2FA? → 回复 + TTS
```

---

## 第二部分：Cursor 落地执行指南 (Action Plan)

为让 Cursor 理解并实现该逻辑，且不破坏现有代码，按 **4 步** 下达指令。**每完成一步，自行测试通过后再发下一步。**

---

### CTO 接口契约审查 (API Contract)

Step 1 / Step 2 中前后端传递的 JSON 必须严格遵循以下契约。

**1. 前端 → 后端 (Request)**

```json
{
  "wav_base64": "UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA...",
  "input_mode": "manual",
  "client_timestamp": 1716300000
}
```

- `input_mode`：`"manual"`（按键录音）或 `"vad"`（VAD 截断）。
- `client_timestamp`：客户端 Unix 时间戳（秒），可选但建议携带。

**2. 后端 → 前端 (Response)**

```json
{
  "status": "success",
  "recognized_text": "把这份文件删除了",
  "intent_routing": "ENGAGE",
  "security_action": "REQUIRE_CONFIRMATION",
  "reply_text": "检测到高危操作：删除文件。请确认是否执行？",
  "reply_audio_base64": "..."
}
```

- `intent_routing`：`"ENGAGE"` 或 `"IGNORE"`。
- `security_action`：`"NONE"` | `"REQUIRE_CONFIRMATION"` | `"REJECTED"`。
- `reply_text`：展示/播报文案；`reply_audio_base64`：TTS 音频（可选）。

**设计说明**：前端只做“无脑渲染”。`intent_routing === "IGNORE"` 时前端当无事发生；`security_action === "REQUIRE_CONFIRMATION"` 时前端无脑弹红框。业务逻辑全部在后端。

---

### Step 1：巩固 Layer 3（前端）的双轨输入模式

**目标**：在 React UI 中明确分离“按键录音”和“VAD 监听”的状态，并为两种模式打标，让后端知道音频来源。

**# Role**  
Frontend Architect (React + Tauri)

**# Context**  
我们正在完善 `HolographicChat` 的语音输入功能。系统必须支持“双轨输入”：既保留最基础的“按键录音(Manual)”，也支持基于底层引擎的“VAD 连续监听(Auto)”。

**# Task**  
修改 `src/components/HolographicChat/ChatInput.tsx`（或包含话筒按钮的文件）。

**# Implementation Details**

1. **双模式 UI**
   - 保留原有的一个 **Mic** 按钮用于**按键录音 (Push-to-Talk)**：按下开始录音，松开发送 `wav_base64`。
   - 增加一个专门的 **Toggle** 按钮（或波浪线/监听图标）用于开启/关闭 **VAD 监听模式**（调用 `invoke("start_voice_capture")`）。
2. **Payload 打标**
   - 前端向后端发送音频时（HTTP 或 Tauri Event），必须在 payload 中增加 `input_mode` 字段：
     - 按键录音：`{ "wav_base64": "...", "input_mode": "manual" }`
     - VAD 截断：`{ "wav_base64": "...", "input_mode": "vad" }`

**# Requirements**  
两种输入模式互不冲突；若设备无法开启 VAD，用户仍可通过长按话筒完成交互。

**Step 1 验收标准（完成后自测）**

- **UI 视觉隔离**：必须有明确的**麦克风图标（按住说话）**；必须有独立的 **VAD 监听开关**（Toggle/雷达图标）。
- **状态互斥**：按住麦克风说话时，即便 VAD 已开启，也应优先以 `input_mode: "manual"` 处理该段音频，或在按键期间暂时挂起 VAD 的发送。
- **Rust 引擎联动**：点击 VAD 开关应正确触发 `invoke("start_voice_capture")` 与 `invoke("stop_voice_capture")`。

---

### Step 2：搭建 Layer 2（后端）语音处理总线

**目标**：在 Python 服务端提供统一接口，接收音频并做 STT 转录。

**# Role**  
Backend Engineer (Python + FastAPI)

**# Context**  
前端将采集到的音频（base64）及输入模式（`manual` 或 `vad`）发送给 Layer 2。需在服务端提供统一处理端点。

**# Task**  
在 `core/api/voice.py`（或现有 FastAPI 路由）中，新增 **POST** `/api/v1/voice/process` 接口。

**# Implementation Details**

1. **Request Schema**
   ```python
   class VoiceProcessRequest(BaseModel):
       wav_base64: str
       input_mode: str  # "manual" | "vad"
   ```
2. **接口逻辑**
   - 接收 `VoiceProcessRequest`，解码 `wav_base64` 为音频字节。
   - 调用 STT 引擎（如 Paraformer 或现有 `core/voice/stt.py`）进行转录，得到 `text`。
   - （可选）调用声纹模块得到 `user: "owner" | "guest"`。
   - 将 `text`、`input_mode`、`user` 传入语义路由层（Step 3），或先返回 `{"text": "...", "input_mode": "...", "user": "..."}` 供上层使用。
3. **Response**
   - 至少返回：`{"text": "<转录结果>", "input_mode": "<manual|vad>", "user": "<owner|guest|unknown>"}`；若与路由/执行一体化，可在此接口内直接串起路由与执行并返回最终结果。

**# Requirements**  
接口幂等、错误时返回 4xx/5xx 及明确错误信息；与现有 `/api/chat` 等路由兼容，不破坏现有调用。

---

### Step 3：实现语义路由层 (Semantic Router)

**目标**：根据 `text`、`input_mode`、`user` 判定 ENGAGE 或 IGNORE，并决定是否进入执行/回复流程。

**# Role**  
Backend Engineer (Python + LLM)

**# Context**  
Step 2 产出转录文本与输入模式。需用轻量规则或极速本地 LLM（如 Qwen-7B）对语句打标。

**# Task**  
在 `core/brain/` 下实现语义路由模块（如 `semantic_router.py`），供 Step 2 的 `/api/v1/voice/process` 或执行引擎调用。

**# Implementation Details**

1. **输入**：`{"text": "...", "input_mode": "manual"|"vad", "user": "owner"|"guest"|"unknown"}`。
2. **规则**：
   - `input_mode == "manual"` → 直接返回 **ENGAGE**。
   - 若为 `vad`：用规则或 LLM 判断是否为“叫名字/明确指令/查询” → **ENGAGE**；否则（如仅感叹句）→ **IGNORE**。
3. **输出**：`{"intent": "ENGAGE"|"IGNORE", "reason": "可选说明"}`。
4. **IGNORE 处理**：将文本写入 ContextBuffer（最近 5 分钟记忆池），不调用 TTS、不返回语音。
5. **ENGAGE 处理**：进入 Step 4 执行与 2FA 逻辑。

**# Requirements**  
路由逻辑可配置、可扩展；不改变现有聊天接口的请求/响应形态，仅作为语音管线的一环。

---

### Step 4：实现高危指令与 2FA（二次确认机制）

**目标**：对高危操作做二次确认，防止误操作与恶意指令。

**# Role**  
Security & Logic Architect (Python)

**# Context**  
经 Router 判定为 **ENGAGE** 的指令会进入大模型生成回复或执行 Skill。需拦截高危操作并要求确认。

**# Task**  
在执行引擎（如 `core/brain/executor.py` 或 Agent 逻辑）中增加 2FA 安全锁。

**# Implementation Details**

1. **Risk Assessment**
   - 在 Tool/Skill 定义中增加属性，例如 `risk_level = "HIGH"`。
   - 删除文件、关机等为 `"HIGH"`；默认闲聊、查询类为 `"LOW"`。
2. **Execution Logic**
   - 若大模型决定调用 `risk_level == "HIGH"` 的工具：
     - **中止执行**，返回特殊状态，例如：  
       `{"action": "require_confirmation", "message": "检测到高危操作：[删除文件]。请确认是否执行？"}`
3. **Frontend Handling (Tauri/React)**
   - 前端收到 `require_confirmation` 时：
     - 聊天气泡边框变红（警告色）。
     - 显示带“确认”和“取消”的 UI。
     - 或支持用户通过下一次语音“我确认”放行。

**# Requirements**  
给出高危指令拦截的代码结构：后端检测钩子 + 前端对 `require_confirmation` 的 UI 与交互逻辑。

---

*文档版本：第一部分 + 第二部分（4 步 Action Plan）。Step 1/2/3/4 提示词可按顺序发给 Cursor，每步测试通过后再执行下一步。*
