# Jachin Desktop Console

Jachin-System 的桌面控制台应用，使用 Tauri v2 + React + TypeScript 构建。

## 技术栈

- **前端**: React 18 + TypeScript + Tailwind CSS
- **后端**: Rust (Tauri v2)
- **状态管理**: Zustand
- **UI组件**: 自定义组件（未来可集成 shadcn/ui）
- **通信**: Dapr (通过 Rust HTTP 客户端)

## 功能特性

- ✅ 与后端 AI 助手对话
- ✅ 实时连接状态监控
- ✅ 设备列表显示（待实现）
- ✅ 通过 Dapr 调用后端服务
- ✅ Rust 后端支持硬件控制（待扩展）

## 开发环境设置

### 前置要求

1. **Node.js** (v18+)
2. **Rust** (最新稳定版)
   ```bash
   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
   ```
3. **Tauri CLI**
   ```bash
   npm install -g @tauri-apps/cli@next
   ```

### 安装依赖

```bash
cd clients/desktop
npm install
```

### 开发模式

**重要：需先启动 Python 后端**，否则 Mind Stream、GPU 统计等将无法获取数据，且可能显示 "pynvml not installed" 等调试信息。

```powershell
# 终端 1：在项目根目录启动后端（使用 jachin-dev 环境，含 pynvml）
cd E:\jachin-system
conda activate jachin-dev
.\scripts\start_backend_dev.ps1

# 终端 2：启动桌面客户端
cd clients\desktop
npm run tauri:dev
```

或使用一键脚本（同时启动中间件 + 后端）：
```powershell
cd E:\jachin-system
.\scripts\start.ps1   # 会启动后端，保持运行
# 另开终端
cd clients\desktop; npm run tauri:dev
```

这将启动：
- Vite 开发服务器（端口 31421）
- Tauri 应用窗口

### 语音采集（VAD / 连续监听）

需要**启用 `ambient` 特性**才能使用端侧语音采集（Silero VAD、按键录音 PTT 等）。

**开发：**
```powershell
cd clients\desktop
npm run tauri:dev:ambient
```

**构建（启用语音采集的生产包）：**
```powershell
npm run tauri:build:ambient
```

构建产物在 `src-tauri/target/release/` 目录。未带 `ambient` 的构建不包含 VAD/语音采集功能。

### 使用 Dapr 启动（推荐，用于接收设备指令）

桌面客户端需要 Dapr sidecar 才能接收来自大脑的设备指令（如精灵状态、通知等）。

```powershell
# Windows (PowerShell)
.\run_with_dapr.ps1
```

```bash
# macOS / Linux
chmod +x run_with_dapr.sh
./run_with_dapr.sh
```

或手动运行：

```bash
cd clients/desktop
dapr run \
  --app-id desktop-client \
  --app-port 8002 \
  --dapr-http-port 3502 \
  --resources-path ../../dapr/components \
  --config ../../dapr/config/config.yaml \
  -- npm run tauri:dev
```

**前置条件**：已安装 [Dapr CLI](https://docs.dapr.io/getting-started/install-dapr-cli/)，且 `dapr/components`、`dapr/config` 目录存在。

### 构建生产版本

```bash
# 默认构建（无语音采集）
npm run tauri:build

# 启用语音采集（VAD/PTT）的构建
npm run tauri:build:ambient
```

构建产物在 `src-tauri/target/release/` 目录。

### 控制台 HUD 与可选环境变量

主窗口为 **控制台 (Console)**，采用 HUD 风格布局（指挥官甲板、战情室、大脑扫描、军械库等），详见仓库根目录 **`docs/CONSOLE_HUD_DESIGN_VISION.md`**。

可选环境变量（在项目根或本目录下建 `.env`，以 `VITE_` 开头才会被 Vite 注入）：

- `VITE_ENVIRONMENT` — 顶栏环境描述，如 `Home Network (Secure)`
- `VITE_MODEL_NAME` — 顶栏当前模型名，如 `Qwen-72B (Int4)`
- `VITE_BACKEND_URL` — 后端 API 地址，默认 `http://localhost:18888`（V2 统一直连，Dapr 已废弃）

## 项目结构

```
clients/desktop/
├── src/                    # React 前端代码
│   ├── console/           # 控制台 HUD（主窗口）
│   │   ├── components/   # MindStream, Horizon, VoidBackground, ComputeTopology, LiveTile, …
│   │   ├── pages/         # Dashboard, NeuralNexus, SkillMatrix, JachinLink, Persona
│   │   ├── ConsoleLayout.tsx
│   │   ├── Sidebar.tsx
│   │   └── routes.tsx
│   ├── components/        # 聊天/精灵等通用组件
│   │   ├── ChatPanel.tsx
│   │   ├── DevicePanel.tsx
│   │   └── …
│   ├── lib/               # api.ts（含 getClusterStats、listSkills、getDevices 等）
│   ├── store/
│   ├── styles/            # globals.css（深空背景、glass-panel、HUD 样式）
│   ├── App.tsx
│   └── main.tsx           # 按窗口 label 挂载 ConsoleApp / App / 精灵
├── src-tauri/
│   ├── src/
│   │   ├── main.rs
│   │   ├── dapr.rs
│   │   └── device.rs
│   └── tauri.conf.json
├── .env.example           # 可选环境变量示例（VITE_ENVIRONMENT、VITE_MODEL_NAME 等）
├── package.json
├── vite.config.ts
└── tailwind.config.js
```

## 与后端通信

### V2 直连后端（Dapr 已废弃）

前端通过 Tauri 或 fetch 直连后端 API（默认 `http://localhost:18888`）：

```
React UI → fetch(BACKEND_URL/api/...) 或 Tauri invoke
```

### API 调用示例

```typescript
// 前端调用
import { sendChatMessage } from "./lib/api";

const response = await sendChatMessage("你好");
```

```rust
// Rust 后端处理
#[tauri::command]
async fn invoke_backend(method: String, data: Option<Value>) -> Result<Value, String> {
    let client = DaprClient::new();
    client.invoke("jachin-brain", &method, data, "POST").await
}
```

## 设备控制

未来可以通过 Rust 后端直接控制硬件：

```rust
// GPIO 控制（树莓派）
#[tauri::command]
async fn control_gpio(pin: u8, value: bool) -> Result<(), String> {
    // 实现 GPIO 控制逻辑
}

// 串口通信（ESP32）
#[tauri::command]
async fn send_serial_command(port: String, command: String) -> Result<(), String> {
    // 实现串口通信逻辑
}
```

## 配置

### Tauri 配置

窗口大小、标题等配置在 `src-tauri/tauri.conf.json` 中。

## 下一步开发

- [ ] 集成 shadcn/ui 组件库
- [ ] 实现设备控制功能
- [ ] 添加 GPIO/串口支持
- [ ] 实现流式输出
- [ ] 添加消息历史记录
- [ ] 支持多模态输入（图片、音频）
- [ ] 添加主题切换
- [ ] 实现快捷键支持

## 相关文档

- [Tauri v2 文档](https://v2.tauri.app/)
- [V2 架构文档](../../docs/ARCHITECTURE_V2_LAYER3_STANDALONE.md)
