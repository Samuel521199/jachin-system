# Scripts 脚本说明

Jachin 各层独立安装/启动，以及特殊用途脚本说明。

**原则**：所有安装、启动、配对均通过 scripts/ 统一入口，实现超简单、无脑化使用。详见 `.cursor/rules/060-scripts-one-click.mdc`。

---

## 傻瓜式入口（双击即用）

| 文件 | 说明 |
|------|------|
| `安装.bat` | 首次使用：检查依赖并安装 Cloud |
| `启动配对Demo.bat` | 一键启动配对流程：控制台 + 配对终端 + 浏览器 |
| `启动控制台.bat` | 仅启动 Nexus 控制台 (http://localhost:3000) |

无需命令行，双击即可运行。

---

## 前置依赖（一键安装前需满足）

| 层 | 必需 | 可选 |
|------|------|------|
| **Cloud** | Node.js + npm | - |
| **Layer2** | Python 3.10+ | Docker（Qdrant）、Conda（Python 3.13 时推荐，Ray 需 3.10–3.12） |
| **Layer3** | Node.js + npm | Rust + Tauri CLI（完整桌面端） |

**快速安装（Windows 管理员 PowerShell）：**
```powershell
winget install OpenJS.NodeJS.LTS    # Cloud / Layer3
winget install Python.Python.3.11    # Layer2
winget install Docker.DockerDesktop  # Layer2 Qdrant（可选）
```

**检查依赖：** `.\scripts\check-prerequisites.ps1 [cloud|layer2|layer3]`（无参数检查全部）

---

## 一、各层安装与启动

Cloud（平台商）、Layer2（用户）、Layer3（用户）完全分离。

| 层 | 角色 | Windows 安装 | Windows 启动 | Linux/macOS 安装 | Linux/macOS 启动 |
|------|------|--------------|--------------|------------------|------------------|
| **Cloud** | 平台商 | `install-cloud.ps1` | `start-cloud.ps1` | `install-cloud.sh` | `start-cloud.sh` |
| **Layer2** | 用户 | `install-layer2.ps1` | `start-layer2.ps1` | `install-layer2.sh` | `start-layer2.sh` |
| **Layer3** | 用户 | `install-layer3.ps1` | `start-layer3.ps1` | `install-layer3.sh` | `start-layer3.sh` |

### 根目录快捷方式

- `start.bat [cloud|layer2|daemon|layer3|full|pair]` — 默认 layer2；`pair` 为边缘智能体配对
- `start.bat layer2` — 启动时展示选项菜单（nexus_daemon 完整版 / daemon 轻量版）
- `start.bat daemon` — 直接启动轻量版守护进程（心跳 + Agent Loop 自主执行）
- `restart.bat` — 重启完整栈

---

## 二、完整栈（开发环境）

| 脚本 | 说明 |
|------|------|
| `setup.ps1` / `setup.sh` | 一键安装：Conda 环境、依赖、Dapr、.env |
| `start-full.ps1` / `start-full.sh` | 启动完整栈：Docker + Dapr + 后端 (端口 18888) |
| `stop.ps1` / `stop.sh` | 停止完整栈 |
| `restart.ps1` / `restart.sh` | 先 stop 再 start-full |
| `deploy.ps1` | 一键部署：环境、依赖、TTS 模型、桌面端构建（可 `-SkipTts` / `-SkipDesktop` / `-SkipBackend`） |

---

## 三、模型下载（特殊用途）

| 脚本 | 说明 |
|------|------|
| `download_models.bat` | 一键下载 VAD / TTS / Whisper 模型。用法：`download_models.bat [vad|tts|whisper|all]`，无参数=全部 |
| `download_vad_model.py` | 下载 Silero VAD 模型 (silero_vad.onnx) |
| `download_tts_models.py` | 下载 TTS Kokoro 模型 |
| `download_whisper_model.py` | 下载 Whisper 语音识别模型 |

---

## 四、端口与进程

| 脚本 | 说明 |
|------|------|
| `check_port.ps1 [端口]` | 查看端口占用，默认 18888 |
| `kill_port.ps1 [端口]` | 释放占用端口的进程（必要时需管理员权限） |

---

## 五、Docker / Dapr 排障

| 脚本 | 说明 |
|------|------|
| `docker_fix_conflicts.ps1` | 修复 Docker 容器名冲突 |
| `docker_diagnose.ps1` | 诊断 Docker 服务未运行原因 |
| `dapr_restart_scheduler.ps1` | 重启 Dapr Scheduler 容器 |

---

## 六、配对与 CLI

| 脚本 | 说明 |
|------|------|
| `run-pair.ps1` / `run-pair.sh` | 边缘智能体配对（6 位码，极客终端版） |
| `start.bat pair` | 同上，根目录快捷方式 |

---

## 七、测试与验证

| 脚本 | 说明 |
|------|------|
| `test.ps1` / `test.sh` | 测试 API：健康、路由、聊天、Dapr |
| `run_tests.ps1` | 运行 pytest 单元/集成测试 |
| `verify_system.ps1` | 系统验证：Conda、端口、数据库、Dapr |

---

## 八、内部脚本（一般无需单独运行）

| 脚本 | 说明 |
|------|------|
| `run_backend_uvicorn_conda.bat` | 启动 uvicorn 后端（Conda jachin-dev），被 start-full.ps1 通过 dapr run 调用 |

---

## 推荐流程

**纯 Nexus 用户：**
```
install-layer2.ps1  →  start-layer2.ps1
```
（install 自动执行首次配对，已配对则跳过）
- `start-layer2.ps1` 启动时展示选项：`[1] nexus_daemon (完整版)` / `[2] daemon (轻量版)`
- 快捷启动轻量版：`start.bat daemon` 或 `.\scripts\start-layer2.ps1 -Mode daemon`
- **轻量版 daemon**：心跳拉取蓝图后，由 **Agent Loop (ReAct)** 自主执行，不再机械跑 Trigger→Processor→Action。详见 [docs/LAYER2_AGENT_LOOP_DESIGN.md](../docs/LAYER2_AGENT_LOOP_DESIGN.md)

**平台商部署 Cloud：**
```
install-cloud.ps1   →  start-cloud.ps1
```
（install-cloud 会执行数据库迁移：Supabase 或 Drizzle `db:push`）

**完整开发环境：**
```
setup.ps1  →  编辑 .env  →  可选 download_models.bat  →  start-full.ps1
```

**端口占用时：** `kill_port.ps1 18888`  
**容器冲突时：** `docker_fix_conflicts.ps1`
