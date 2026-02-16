# Scripts 使用指南

## 系统架构图

### Jachin-System v2.0 三层架构

```mermaid
graph TD
    classDef brain fill:#ffecb3,stroke:#ff6f00,stroke-width:2px;
    classDef nerve fill:#e1f5fe,stroke:#0277bd,stroke-width:2px;
    classDef limb fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px;

    subgraph "Brain Layer (Python/GPU)"
        Agent[<b>AI Agent</b><br/>Qwen/Planner]:::brain
        Registry[<b>Device Registry</b><br/>Redis Store]:::brain
        Protocol[<b>JCP Protocol</b><br/>Parser]:::brain
    end

    subgraph "Nerve Layer (Dapr & Infra)"
        PubSub(<b>Dapr Pub/Sub</b><br/>Event Bus):::nerve
        Tailscale(<b>Tailscale</b><br/>Secure Tunnel):::nerve
        Redis[(<b>Redis</b><br/>State Store)]:::nerve
    end

    subgraph "Limb Layer (Clients)"
        Desktop[<b>Desktop Sprite</b><br/>Tauri/Rust]:::limb
        Pi[<b>Raspberry Pi</b><br/>Python Node]:::limb
        ESP[<b>ESP32</b><br/>MicroPython]:::limb
    end

    Desktop & Pi & ESP -->|1. Announce| PubSub
    PubSub -->|2. Register| Registry
    Registry -->|3. Store| Redis
    Agent -->|4. Query Tools| Registry
    Registry -->|5. Return Devices| Agent
    Agent -->|6. Route Command| PubSub
    PubSub -->|7. Execute Action| Pi
```

### 组件说明

- **Brain Layer（大脑层）**: AI 推理、设备管理、协议解析
- **Nerve Layer（神经层）**: 消息总线、状态存储、安全隧道
- **Limb Layer（肢体层）**: 各种客户端和设备节点

## 核心流程

### 能力发现流程

```mermaid
sequenceDiagram
    participant Device as 设备节点
    participant PubSub as Dapr Pub/Sub
    participant Registry as Device Registry
    participant Redis as Redis Store

    Device->>PubSub: 1. 广播能力 (system/announce)
    Note over Device: DeviceAnnounce<br/>device_id, capabilities, location
    
    PubSub->>Registry: 2. 接收广播消息
    Registry->>Registry: 3. 验证数据格式
    Registry->>Redis: 4. 存储设备信息
    Redis-->>Registry: 5. 确认存储成功
    Registry->>Registry: 6. 更新内存缓存
    
    Note over Registry: 设备已注册<br/>能力可用
```

### 指令执行流程

```mermaid
sequenceDiagram
    participant User as 用户
    participant Agent as AI Agent
    participant Registry as Device Registry
    participant PubSub as Dapr Pub/Sub
    participant Device as 目标设备

    User->>Agent: 1. 发送指令
    Note over User: "打开客厅的灯"
    
    Agent->>Registry: 2. 查询可用工具
    Registry-->>Agent: 3. 返回设备列表
    Note over Agent: 发现: raspi-living-room-001<br/>能力: light.control
    
    Agent->>Agent: 4. 选择目标设备
    Agent->>PubSub: 5. 发布指令 (device/{id}/command)
    Note over Agent: DeviceCommand<br/>device_id, capability, params
    
    PubSub->>Device: 6. 推送指令到设备
    Device->>Device: 7. 执行操作
    Device->>PubSub: 8. 返回结果 (device/{id}/response)
    PubSub->>Agent: 9. 接收执行结果
    Agent->>User: 10. 反馈结果
```

### 脚本执行流程

```mermaid
flowchart TD
    Start([开始]) --> Setup{首次运行?}
    Setup -->|是| RunSetup[执行 setup.ps1/sh]
    Setup -->|否| CheckDocker{检查 Docker}
    
    RunSetup --> InstallConda[安装/激活 Conda]
    InstallConda --> InstallDeps[安装依赖]
    InstallDeps --> SetupDapr[配置 Dapr]
    SetupDapr --> CheckDocker
    
    CheckDocker -->|未运行| StartDocker[启动 Docker Desktop]
    CheckDocker -->|已运行| StartServices[执行 start.ps1/sh]
    
    StartDocker --> StartServices
    
    StartServices --> ActivateEnv[激活 jachin-dev 环境]
    ActivateEnv --> StartInfra[启动基础设施<br/>docker-compose up]
    StartInfra --> StartBackend[启动后端<br/>dapr run backend]
    StartBackend --> Ready([服务就绪])
    
    Ready --> Test{需要测试?}
    Test -->|是| RunTest[执行 test.ps1/sh]
    Test -->|否| End([结束])
    
    RunTest --> CheckHealth[检查健康状态]
    CheckHealth --> TestAPI[测试 API 端点]
    TestAPI --> End
```

## 核心脚本（简化版）

### Windows (PowerShell)

| 脚本 | 功能 | 使用 |
|------|------|------|
| `setup.ps1` | 初始设置（Conda、依赖、Dapr） | `.\scripts\setup.ps1` |
| `start.ps1` | 启动所有服务（自动激活环境） | `.\scripts\start.ps1` |
| `stop.ps1` | 停止所有服务 | `.\scripts\stop.ps1` |
| `restart.ps1` | 重启所有服务（自动激活环境） | `.\scripts\restart.ps1` |
| `test.ps1` | 测试 API | `.\scripts\test.ps1` |

### Linux/macOS (Bash)

| 脚本 | 功能 | 使用 |
|------|------|------|
| `setup.sh` | 初始设置 | `chmod +x scripts/setup.sh`<br>`./scripts/setup.sh` |
| `start.sh` | 启动所有服务（自动激活环境） | `./scripts/start.sh` |
| `stop.sh` | 停止所有服务 | `./scripts/stop.sh` |
| `restart.sh` | 重启所有服务（自动激活环境） | `./scripts/restart.sh` |
| `test.sh` | 测试 API | `./scripts/test.sh` |

## 快速开始

### Windows

```powershell
# 1. 首次设置
.\scripts\setup.ps1

# 2. 启动服务（自动激活环境，无需手动激活）
.\scripts\start.ps1

# 3. 测试（在另一个终端）
.\scripts\test.ps1
```

### Linux/macOS

```bash
# 1. 首次设置
chmod +x scripts/*.sh
./scripts/setup.sh

# 2. 启动服务（自动激活环境，无需手动激活）
./scripts/start.sh

# 3. 测试（在另一个终端）
./scripts/test.sh
```

## 注意事项

- **脚本会自动激活 jachin-dev 环境**，无需手动激活
- 确保 Docker Desktop/Docker 正在运行
- 确保已配置 API Key（`.env` 文件中的 `QWEN_API_KEY`）
- 首次运行需要运行 `setup` 脚本
