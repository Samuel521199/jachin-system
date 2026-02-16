# Jachin-System v2.0 规则重构脚本
# 彻底清除旧规则并写入新规则

# 1. 创建目录（如果不存在）
New-Item -ItemType Directory -Force -Path ".\.cursor\rules" | Out-Null
New-Item -ItemType Directory -Force -Path ".\docs" | Out-Null

# 2. 彻底清除旧规则 (清除矛盾)
Write-Host "Clearing old rule files..." -ForegroundColor Yellow
Remove-Item -Path ".\.cursor\rules\*.mdc" -Force -ErrorAction SilentlyContinue

# 3. 写入新规则 1: 全局架构
$rule_arch = @"
---
description: Jachin-System v2.0 全局架构与设计哲学
globs: *
---
# Jachin-System v2.0 Architecture

## Core Philosophy
Jachin-System 是一个 **Local-First, Distributed AI OS**。
它融合了 **OpenClaw 的能力发现机制** 和 **Dapr 的分布式服务网格**。

## The "Iron Man" Architecture
1.  **Brain (Backend):** 运行在高性能服务器(Python)。不硬编码工具，而是通过 **Registry** 动态查找能力。
2.  **Limbs (Nodes):** 运行在边缘设备(Rust/Python)。必须通过 **JCP (Jachin Capability Protocol)** 主动广播能力。
3.  **Nerves (Dapr):** 负责所有节点间的消息传递 (Pub/Sub & Service Invocation).

## Key Protocols
- **JCP (Handshake):** 设备启动时向 `system/announce` 频道广播 `DeviceCapability`。
- **Routing:** Brain 根据用户指令，生成路由包 `{ target: "device_id", action: "camera.snap" }`。

## Tech Stack
- **Infrastructure:** Dapr, Docker Compose, Tailscale, Redis, Qdrant.
- **Backend:** Python (FastAPI), Pydantic.
- **Client:** Tauri (Desktop), Python (IoT).
"@
[System.IO.File]::WriteAllText("$PSScriptRoot\..\.cursor\rules\000-jachin-architecture.mdc", $rule_arch, [System.Text.Encoding]::UTF8)

# 4. 写入新规则 2: 协议与注册表
$rule_proto = @"
---
description: JCP 协议定义与设备注册逻辑
globs: backend/core/protocol.py, backend/core/registry.py, clients/**
---
# Jachin Capability Protocol (JCP) Rules

## 1. Handshake Structure
所有设备 (Client/Node) 必须使用 `backend.core.protocol.DeviceAnnounce` 模型进行广播。
必须包含：`device_id`, `capabilities` (List), `location`.

## 2. Registry Logic
- **Write:** `DeviceRegistry` 监听 `system/announce` Topic，将设备信息存入 Dapr State Store (Redis)。
- **Read:** Agent 在规划任务时，必须先调用 `registry.get_tools()` 获取当前活跃设备列表。

## 3. Forbidden Patterns
- ❌ 禁止在代码中硬编码设备 IP。
- ❌ 禁止假设某个设备一定在线（必须处理 Device Offline 异常）。
"@
[System.IO.File]::WriteAllText("$PSScriptRoot\..\.cursor\rules\010-protocol-registry.mdc", $rule_proto, [System.Text.Encoding]::UTF8)

# 5. 写入新规则 3: 智能体路由
$rule_agent = @"
---
description: AI Agent 的思考与动态工具调用规则
globs: backend/core/llm/**
---
# Agent Reasoning & Routing

## Dynamic Tooling
Agent 不再拥有一组静态的 Tools。
**Runtime Flow:**
1. User input: "Turn on the living room light."
2. Agent queries `DeviceRegistry`: Find devices in `location="living_room"` with capability `"light.switch"`.
3. Agent generates `DeviceCommand`: `target="raspi-01", action="light.switch", params={"state": "on"}`.
4. Dapr publishes command to specific node.

## Context Awareness
Agent 应时刻知晓：
- 哪些设备在线？
- 用户的当前位置（如果由 Client 上报）。
"@
[System.IO.File]::WriteAllText("$PSScriptRoot\..\.cursor\rules\020-agent-brain.mdc", $rule_agent, [System.Text.Encoding]::UTF8)

# 6. 写入架构图到 docs
$doc_arch = @"
# Jachin-System v2.0 Architecture Diagram

\`\`\`mermaid
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
    end

    subgraph "Limb Layer (Clients)"
        Desktop[<b>Desktop Sprite</b><br/>Tauri/Rust]:::limb
        Pi[<b>Raspberry Pi</b><br/>Python Node]:::limb
        ESP[<b>ESP32</b><br/>MicroPython]:::limb
    end

    Desktop & Pi & ESP -->|1. Announce| PubSub
    PubSub -->|2. Register| Registry
    Agent -->|3. Query Tools| Registry
    Registry -->|4. Return Devices| Agent
    Agent -->|5. Route Command| PubSub
    PubSub -->|6. Execute Action| Pi
\`\`\`
"@
[System.IO.File]::WriteAllText("$PSScriptRoot\..\docs\architecture.md", $doc_arch, [System.Text.Encoding]::UTF8)

Write-Host "Jachin-System v2.0 rules rebuilt successfully! Old files cleared, new rules active." -ForegroundColor Green
