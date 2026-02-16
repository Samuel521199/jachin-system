# Desktop Client 开发指南

## 概述

Desktop Client 是 Jachin-System 的桌面控制台应用，使用 **Tauri v2 + React + TypeScript** 构建。它不仅是一个展示界面，更是一个**控制台（Console）**，可以直接控制硬件设备。

### 界面设计愿景（HUD）

控制台的长远方向是**从「UI」到「HUD」(Head-Up Display)**：透视与层级、过程可视化、主动性。Main 框架（指挥官甲板）、Dashboard（战情室）、Neural Nexus（大脑扫描）、Skill Matrix（军械库）的详细设计见 **[CONSOLE_HUD_DESIGN_VISION.md](./CONSOLE_HUD_DESIGN_VISION.md)**。该文档包含设计愿景与**实现状态、组件索引、后端对接清单**（第七章），便于与代码同步。顶栏环境与模型名可通过 `clients/desktop` 下的环境变量注入：`VITE_ENVIRONMENT`、`VITE_MODEL_NAME`（参见该目录下的 `.env.example`）。

## 为什么选择 Tauri v2？

### 1. 极致轻量
- **Electron**: 100MB+ 安装包
- **Tauri**: 5-10MB 安装包
- 对于需要常驻后台的"助理"程序，体积至关重要

### 2. Rust 原生能力
- **UI 层（React）**: 负责发指令："打开机械臂"
- **Core 层（Rust）**: 直接调用 DLL 或串口通信控制硬件
- Rust 在硬件控制方面比 Node.js (Electron) 更安全、更强大

### 3. 跨平台
- Windows, Linux, macOS ✅
- Android, iOS ✅ (Tauri v2 支持)

### 4. 安全性
- 严格的 IPC（进程间通信）和权限系统
- 防止 AI 生成的代码意外执行危险操作

## 架构设计

### 通信流程

```
┌─────────────┐
│  React UI   │  ← 用户交互
└──────┬──────┘
       │ invoke('command')
       ▼
┌─────────────┐
│ Tauri Rust  │  ← 硬件控制、Dapr 通信
└──────┬──────┘
       │ HTTP/gRPC
       ▼
┌─────────────┐
│ Dapr Sidecar│  ← 服务发现、路由
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Backend   │  ← Python Brain
│ (jachin-    │
│   brain)    │
└─────────────┘
```

### 目录结构

```
clients/desktop/
├── src/                    # React 前端
│   ├── console/           # 控制台 HUD：Layout、Sidebar、Horizon、Void、各页与组件
│   ├── components/       # 聊天/精灵等通用 UI 组件
│   ├── lib/               # API 客户端（getClusterStats、listSkills、getDevices 等）
│   ├── store/             # Zustand 状态管理
│   └── styles/            # Tailwind + 深空/玻璃/HUD 全局样式
├── src-tauri/             # Rust 后端
│   ├── src/
│   │   ├── main.rs        # Tauri 命令定义
│   │   ├── dapr.rs        # Dapr 客户端
│   │   └── device.rs      # 设备控制
│   └── Cargo.toml
├── .env.example            # 可选 VITE_ 环境变量示例
└── package.json
```

## 快速开始

### 1. 安装依赖

**Windows:**
```powershell
cd clients/desktop
.\scripts\setup.ps1
```

**Linux/macOS:**
```bash
cd clients/desktop
chmod +x scripts/setup.sh
./scripts/setup.sh
```

### 2. 启动后端服务

确保后端服务已启动（Dapr sidecar 运行在端口 3500）：

```bash
# 在项目根目录
.\start.bat  # Windows
./scripts/start.sh  # Linux/macOS
```

### 3. 启动桌面客户端

```bash
cd clients/desktop
npm run tauri:dev
```

## 核心功能

### 1. 与 AI 助手对话

前端通过 Dapr 调用后端 `/api/chat` 端点：

```typescript
// src/lib/api.ts
export async function sendChatMessage(message: string): Promise<ChatResponse> {
  return invokeBackend<ChatResponse>("/api/chat", { message });
}
```

### 2. 设备控制

通过 Rust 后端直接控制硬件：

```rust
// src-tauri/src/device.rs
pub async fn execute(
    &self,
    device_id: &str,
    action: &str,
    params: Option<Value>,
) -> Result<Value, Box<dyn std::error::Error>> {
    // GPIO 控制、串口通信等
}
```

### 3. Dapr 服务调用

Rust 后端通过 Dapr 调用其他服务：

```rust
// src-tauri/src/dapr.rs
pub async fn invoke(
    &self,
    app_id: &str,
    method: &str,
    data: Option<Value>,
) -> Result<Value, Box<dyn std::error::Error>> {
    let url = format!(
        "http://localhost:3500/v1.0/invoke/{}/method/{}",
        app_id, method
    );
    // HTTP 请求...
}
```

## 开发指南

### 添加新的 Tauri 命令

1. **在 Rust 中定义命令**:

```rust
// src-tauri/src/main.rs
#[tauri::command]
async fn my_command(param: String) -> Result<String, String> {
    Ok(format!("Received: {}", param))
}

fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![my_command])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
```

2. **在前端调用**:

```typescript
import { invoke } from "@tauri-apps/api/core";

const result = await invoke<string>("my_command", { param: "hello" });
```

### 添加新的 UI 组件

1. 在 `src/components/` 创建组件
2. 使用 Tailwind CSS 样式
3. 使用 Zustand 管理状态

### 集成 shadcn/ui

```bash
cd clients/desktop
npx shadcn-ui@latest init
npx shadcn-ui@latest add button
npx shadcn-ui@latest add card
```

## 硬件控制扩展

### GPIO 控制（树莓派）

```rust
// src-tauri/src/device.rs
use rppal::gpio::Gpio;

pub async fn control_gpio(pin: u8, value: bool) -> Result<(), String> {
    let gpio = Gpio::new().map_err(|e| e.to_string())?;
    let mut pin = gpio.get(pin).map_err(|e| e.to_string())?.into_output();
    pin.write(if value { Level::High } else { Level::Low });
    Ok(())
}
```

### 串口通信（ESP32）

```rust
// src-tauri/src/device.rs
use serialport::SerialPort;

pub async fn send_serial_command(
    port: String,
    command: String,
) -> Result<(), String> {
    let mut port = serialport::new(&port, 115200)
        .open()
        .map_err(|e| e.to_string())?;
    
    port.write(command.as_bytes())
        .map_err(|e| e.to_string())?;
    Ok(())
}
```

## 构建和分发

### 开发构建

```bash
npm run tauri:dev
```

### 生产构建

```bash
npm run tauri:build
```

构建产物在 `src-tauri/target/release/` 目录。

### 平台特定构建

```bash
# Windows
npm run tauri:build -- --target x86_64-pc-windows-msvc

# Linux
npm run tauri:build -- --target x86_64-unknown-linux-gnu

# macOS
npm run tauri:build -- --target x86_64-apple-darwin
```

## 故障排除

### 问题1: Rust 编译错误

**解决方案**: 确保 Rust 工具链已正确安装
```bash
rustup update
rustup component add rustfmt clippy
```

### 问题2: Dapr 连接失败

**症状**: 无法调用后端服务

**解决方案**:
1. 确认后端服务已启动
2. 确认 Dapr sidecar 运行在端口 3500
3. 检查 `src-tauri/src/dapr.rs` 中的端口配置

### 问题3: 前端无法调用 Tauri 命令

**解决方案**:
1. 确认命令已在 `main.rs` 中注册
2. 检查 `tauri.conf.json` 中的安全配置
3. 查看浏览器控制台错误信息

## 下一步开发

- [ ] 集成 shadcn/ui 组件库
- [ ] 实现 GPIO 控制
- [ ] 实现串口通信
- [ ] 添加流式输出支持
- [ ] 实现消息历史记录
- [ ] 支持多模态输入
- [ ] 添加主题切换
- [ ] 实现快捷键支持

## 相关文档

- [Tauri v2 文档](https://v2.tauri.app/)
- [React 文档](https://react.dev/)
- [Tailwind CSS 文档](https://tailwindcss.com/)
- [Dapr 文档](https://docs.dapr.io/)
- [项目架构文档](./architecture.md)
