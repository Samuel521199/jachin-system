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
| **Layer2** | Python 3.10+ | Conda（Python 3.13 时推荐） |
| **Layer3** | Node.js + npm | Rust + Tauri CLI（完整桌面端） |

**快速安装（Windows 管理员 PowerShell）：**
```powershell
winget install OpenJS.NodeJS.LTS    # Cloud / Layer3
winget install Python.Python.3.11  # Layer2
```

**检查依赖：** `.\scripts\check-prerequisites.ps1 [cloud|layer2|layer3]`（无参数检查全部）

---

## 一、各层安装与启动

Cloud（平台商）、Layer2（用户）、Layer3（用户）完全分离。

| 层 | 角色 | Windows 安装 | Windows 启动 | Linux/macOS 安装 | Linux/macOS 启动 |
|------|------|--------------|--------------|------------------|------------------|
| **Cloud** | 平台商 | `install-cloud.ps1` | `start-cloud.ps1` | `install-cloud.sh` | `start-cloud.sh` |

**L1 Linux 生产包（云端部署，默认非 Docker）**：
- 交付物为 **`dist\jachin-l1-linux-amd64-v*.tar.gz`**：解压即可运行（内含 **runtime/node**，类似 Windows 便携版），服务器 **无需 Docker**、**无需安装 Node**（默认包）。
- **Windows 本机构建**：`.\scripts\build-l1-linux-via-docker.ps1`（仅用 Docker 当 linux 构建机，产物仍是普通目录/tar）。
- **ECS 方式 B 一键目录**：`deploy\l1-ecs-bundle\`（compose、`l1.env` 预填示例见 `l1.env.example`，真实 `l1.env` 勿提交 Git）。
- **上传镜像到当前 ECS**：`.\scripts\scp-l1-docker-artifacts-to-server.ps1`（目标 **47.86.39.173**，换机请改脚本内 `$DeployHost`；优先上传 bundle 内文件）。
- **L2 Docker 上传 ECS**：`.\scripts\scp-l2-docker-artifacts-to-server.ps1`（目录 **`/opt/jachin-l2`**，端口 **18888**，详见 **`deploy\l2-ecs-bundle\README.txt`**）。
- **Linux/WSL 本机构建**：`./scripts/build-l1-linux-release.sh`。
- 服务器：`./start.sh`。详见 `docs/L1_LINUX_CLOUD_DEPLOY.md`。**Docker 跑 L1、PostgreSQL 装宿主机**：`docker/compose.l1.yml` + `docker/l1.env`（从 `docker/l1.env.example` 复制）。
| **Layer2** | 用户 | `install-layer2.ps1` | `start-layer2.ps1` | `install-layer2.sh` | `start-layer2.sh` |
| **Layer3** | 用户 | `install-layer3.ps1` | `start-layer3.ps1` | `install-layer3.sh` | `start-layer3.sh` |

### 根目录快捷方式

- `start.bat [cloud|layer2|daemon|layer3|pair]` — 默认 layer2；`pair` 为边缘智能体配对
- `start.bat layer2` — 启动时展示选项菜单（nexus_daemon 完整版 / daemon 轻量版 / Gateway 模式）
- `start.bat daemon` — 直接启动轻量版守护进程（心跳 + Agent Loop 自主执行）
- `restart.bat` — 重启完整栈

---

## 二、模型下载（特殊用途）

| 脚本 | 说明 |
|------|------|
| `download_models.bat` | 一键下载 VAD / TTS / Whisper 模型。用法：`download_models.bat [vad|tts|whisper|all]`，无参数=全部 |
| `download_vad_model.py` | 下载 Silero VAD 模型 (silero_vad.onnx) |
| `download_tts_models.py` | 下载 TTS MOSS ONNX 模型 |
| `download_whisper_model.py` | 下载 Whisper 语音识别模型 |

---

## 三、端口与进程

| 脚本 | 说明 |
|------|------|
| `check_port.ps1 [端口]` | 查看端口占用，默认 18888 |
| `kill_port.ps1 [端口]` | 释放占用端口的进程（必要时需管理员权限） |

---

## 四、Docker 排障（可选）

| 脚本 | 说明 |
|------|------|
| `docker_fix_conflicts.ps1` | 修复 Docker 容器名冲突 |
| `docker_diagnose.ps1` | 诊断 Docker 服务未运行原因 |

---

## 五、配对与 CLI

| 脚本 | 说明 |
|------|------|
| `run-pair.ps1` / `run-pair.sh` | L1↔L2 **辅助**配对（CLI 6 位码）；有 Web 时优先 L2 `/gateway` Nexus 账号登录 |
| `start.bat pair` | 同上，根目录快捷方式 |

**L3 桌面端**：默认启动本机 L3 Gateway + Tauri 桌面端；L1 通过 Capability/L1 Profile 配置，L2 仅作为可选扩展。仅终端 L3：`-SourceOnly`；仅桌面 Sidecar：`-DesktopOnly`。

---

## 六、测试与验证

| 脚本 | 说明 |
|------|------|
| `test.ps1` / `test.sh` | 测试 API：健康、路由、聊天 |
| `run_tests.ps1` | 运行 pytest 单元/集成测试 |
| `verify_system.ps1` | 系统验证：Conda、端口、数据库 |

---

## 推荐流程

**纯 Nexus 用户：**
```
install-layer2.ps1  →  start-layer2.ps1
```
（install 自动执行首次配对，已配对则跳过）
- `start-layer2.ps1` 启动时展示选项：`[1] nexus_daemon (完整版)` / `[2] daemon (轻量版)` / `[3] Gateway 模式`
- 快捷启动轻量版：`start.bat daemon` 或 `.\scripts\start-layer2.ps1 -Mode daemon`
- **轻量版 daemon**：心跳拉取蓝图后，由 **Agent Loop (ReAct)** 自主执行

**平台商部署 Cloud：**
```
install-cloud.ps1   →   start-cloud.ps1
```
（install-cloud 会执行数据库迁移：Drizzle `db:push`）

**端口占用时：** `kill_port.ps1 18888`
