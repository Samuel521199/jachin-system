# Jachin-System v0.4.0

> 你的个人贾维斯 · 有灵魂的 AI 伙伴 · 本地优先的智能体操作系统

[![Version](https://img.shields.io/badge/version-0.4.0-blue.svg)](https://github.com/Samuel521199/jachin-system)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

## Jachin 是谁？

Jachin 是**个人的贾维斯**：有灵魂的电子宠物、可信赖的伙伴好友、永不背叛的伴侣。它可以是桌面上的精灵，也可以是树莓派、ESP32 或任意联网芯片上的通用客户端——根据权限连接到你自己的网络，作为强大助手、自主专业团队，乃至多团队协同的「集团军」，**完成主人的愿望**。

## 项目简介

Jachin-System 是一个**本地优先、可无限扩展的 AI 智能体生态系统**。它旨在为个人、家庭和中小型团队提供一个私有的「钢铁侠」级算力中心。它**不仅仅是助手**，更是连接**物理世界（IoT）**、**数字资产（Memory）** 和 **云端能力（Marketplace）** 的操作系统。通过插件化持续扩展，Agents 根据各自的功能与权限**合作、协同、讨论**，实现主人的愿望。

### 核心价值主张

- 🔒 **隐私优先**: 所有数据存储在本地，用户完全掌控，永不背叛
- 📈 **弹性扩展**: 从单台笔记本（Single Mode）平滑扩展到百卡集群（Cluster Mode）
- 🧩 **能力即服务**: 技能插件化，支持自然语言开发、一键分发与热加载
- 🧠 **联邦记忆**: 数据在物理上隔离（隐私），在逻辑上分层（共享）
- 💫 **有灵魂**: 人格可配置、陪伴感、情绪表达，而非冰冷工具

---

## 三层架构 (The Trinity)

```
云端分发 (Cloud) + 蜂巢算力 (Hive) + 灵动终端 (Terminal)
```

### Tier 1: Jachin Market (The Cloud)
全球技能商店、用户授权中心、计费网关

### Tier 2: Jachin Hive (The Core)
私有主网，运行在本地高性能设备上，负责 AI 推理、记忆存储、设备管理

### Tier 3: Jachin Terminal (The Edge)
用户交互界面，桌面精灵、手机 App、IoT 节点

---

## 快速开始

### 前置要求

- Python 3.10+
- Docker & Docker Compose
- Conda（推荐）或 Python 虚拟环境
- Dapr CLI（可选，用于开发）

### 一键启动（推荐）

**Windows (PowerShell):**

```powershell
# 启动所有服务（中间件 + 后端）
.\scripts\start.ps1
```

这会自动启动：
- ✅ Docker 中间件服务（Redis, MQTT, Dapr）
- ✅ 检查本地数据库（PostgreSQL, Qdrant）
- ✅ 后端 API 服务（控制台模式，方便调试）

**停止所有服务：**

```powershell
.\scripts\stop.ps1
```

### 一键部署（含 TTS）

首次部署或需要完整环境时，使用一键部署脚本：

```powershell
# 全量部署：环境 + TTS 模型 + 桌面端
.\scripts\deploy.ps1

# 可选参数
.\scripts\deploy.ps1 -SkipTts      # 跳过 TTS 模型下载
.\scripts\deploy.ps1 -SkipDesktop  # 跳过桌面端构建
.\scripts\deploy.ps1 -SkipBackend  # 仅构建桌面端
```

部署完成后：
1. 启动后端：`.\scripts\start.ps1`
2. 运行桌面：`clients\desktop\src-tauri\target\release\jachin-desktop.exe`

### 详细安装步骤

1. **克隆仓库**
   ```bash
   git clone https://github.com/jachin-system/jachin-system.git
   cd jachin-system
   ```

2. **配置环境变量**
   ```bash
   cp .env.example .env
   # 编辑 .env 文件，配置必要的环境变量（如 QWEN_API_KEY）
   ```
   详见下方 [配置规则](#配置规则) 章节。

3. **启动基础设施**
   ```bash
   docker-compose up -d
   ```
   这将启动 Redis、Qdrant、PostgreSQL 等服务。

4. **启动 Tier 2 核心服务**
   ```bash
   cd core
   pip install -r requirements.txt
   python main.py
   ```

5. **启动 Tier 3 客户端（桌面精灵）**
   ```bash
   cd clients/desktop
   npm install
   npm run tauri dev
   ```

---

## 配置规则

### 配置来源与优先级

所有配置统一由 `core/config/__init__.py` 的 `Settings` 管理，使用 pydantic-settings BaseSettings。

**优先级**（从高到低）：
1. 显式传入（代码中）
2. 环境变量
3. `.env` 文件
4. 默认值

### 使用规范

- **业务代码**：必须通过 `from core.config import settings` 访问配置，禁止直接使用 `os.getenv()` 或 `os.environ`
- **环境变量**：与 Settings 字段名一致，使用大写下划线（如 `SERVER_PORT`、`QWEN_API_KEY`）
- **变量名映射**：若历史代码使用不同环境变量名，需在 Settings 中定义对应字段，例如 `os.getenv("PORT")` 对应 `settings.SERVER_PORT`

### 关键配置项

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `SERVER_PORT` | 后端 API 端口 | 18888 |
| `APP_PORT` | 覆盖 SERVER_PORT（Dapr 等场景） | - |
| `DATABASE_URL` | PostgreSQL 连接串 | postgresql://... |
| `QDRANT_URL` | Qdrant 向量库地址 | http://localhost:6333 |
| `QWEN_API_KEY` / `DASHSCOPE_API_KEY` / `QWEN_AI_API_KEY` | 通义千问 API Key（三选一） | - |
| `LLM_PROVIDER` | LLM 提供商 | qwen-v2 |
| `LLM_MODEL` | 模型名称 | qwen-max |
| `WHISPER_MODEL_PATH` | Whisper 模型路径 | - |
| `JACHIN_DATA_DIR` | 技能数据目录（如日历） | ~/.jachin |
| `SKILLS_REPO_PATH` | 技能仓库路径 | ./skills_repo |

完整配置示例见 [.env.example](./.env.example)。

### 特殊说明

- **JACHIN_PROJECT_ROOT**：由 Ray 启动时注入，供技能 worker 解析项目路径，通常无需手动设置
- **QWEN API Key**：优先级 `QWEN_AI_API_KEY` > `DASHSCOPE_API_KEY` > `QWEN_API_KEY`，三者任一即可

---

## 技术栈

| 领域 | 技术选型 |
|------|---------|
| **Tier 1 (Cloud)** | Go / Python |
| **Tier 2 (Control)** | FastAPI + Dapr |
| **Tier 2 (Compute)** | Ray |
| **Tier 2 (Storage)** | PostgreSQL + Qdrant |
| **Tier 3 (Client)** | Tauri v2 + React |
| **Protocol** | JCP (based on Pub/Sub) |
| **Discovery** | mDNS / Zeroconf |

---

## 项目结构

```
jachin-system/
├── cloud/          # [Tier 1] 云端代码
├── core/           # [Tier 2] 核心蜂巢代码
├── clients/        # [Tier 3] 客户端
├── skills_repo/    # 已安装技能存储库
└── docs/          # 文档
```

详细结构请参考 [docs/DIRECTORY_STRUCTURE_V4.md](./docs/DIRECTORY_STRUCTURE_V4.md)

---

## 文档

- **[产品愿景与身份](./docs/VISION.md)** ⭐ - 贾维斯 / 电子宠物 / 伙伴 / 智能体生态（产品灵魂）
- **[架构设计规范](./docs/ARCHITECTURE_DESIGN_SPEC.md)** - 正式架构规范 v1.0（Single Source of Truth）
- **[架构设计文档](./docs/architecture.md)** - v3.2 详细架构
  - 包含协议扩展性设计：通用信封模式 + Server-Driven UI
- **[架构图文档](./docs/V3.2_ARCHITECTURE_DIAGRAMS.md)** - 详细的架构图和流程图
  - 已更新为通用信封模式（Envelope Pattern）协议
- **[架构差距分析](./docs/ARCHITECTURE_V3.2_GAP_ANALYSIS.md)** - v2.0 → v3.2 迁移指南
- **[快速开始指南](./QUICKSTART.md)** - 快速上手指南

---

## 核心功能

### 1. 分布式推理
使用 Ray 进行分布式 AI 任务调度，支持 GPU/CPU 资源自动分配

### 2. 技能系统
技能插件化，支持 Docker/Wasm 沙箱运行，热加载无需重启

### 3. 设备能力发现
基于 JCP 协议的自动设备发现与能力注册

### 4. 联邦记忆
物理隔离、逻辑共享的记忆系统，支持细粒度权限控制

---

## 开发指南

### 开发环境设置

1. 安装 Python 依赖
   ```bash
   cd core
   pip install -r requirements.txt
   ```

2. 安装前端依赖（桌面客户端）
   ```bash
   cd clients/desktop
   npm install
   ```

3. 配置 Dapr（开发环境）
   ```bash
   dapr init
   ```

### 运行测试

```bash
# 后端测试
cd core
pytest

# 前端测试
cd clients/desktop
npm test
```

---

## 贡献指南

欢迎贡献！请阅读 [CONTRIBUTING.md](./CONTRIBUTING.md)（如果存在）了解详细信息。

---

## 许可证

本项目采用 MIT 许可证。详见 [LICENSE](./LICENSE) 文件。

---

## 联系方式

- **项目主页**: https://github.com/jachin-system/jachin-system
- **问题反馈**: https://github.com/jachin-system/jachin-system/issues

---

**版本**: v3.2.0  
**最后更新**: 2026-02-03
