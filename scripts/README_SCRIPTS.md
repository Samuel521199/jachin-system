# Scripts 脚本说明（scripts 下仅保留以下脚本）

## 一键安装 / 启动 / 停止

| 脚本 | 说明 |
|------|------|
| `setup.ps1` | 一键安装：Conda 环境、依赖、Dapr、.env |
| `start.ps1` | 一键启动：Docker 中间件 + 后端(Dapr)，端口 18888 |
| `stop.ps1` | 一键停止：Dapr、后端进程、Docker；可选移除容器 |
| `restart.ps1` | 先 stop 再 start |
| `deploy.ps1` | 一键部署：环境、依赖、TTS 模型、桌面端构建（可 `-SkipTts` / `-SkipDesktop` / `-SkipBackend`） |

## 模型与后端

| 脚本 | 说明 |
|------|------|
| `download_models.bat [vad\|tts\|whisper\|all]` | 一键下载模型，无参数=全部 |
| `run_backend.bat` | 被 start.ps1 调用，一般无需单独运行 |

## 端口

| 脚本 | 说明 |
|------|------|
| `check_port.ps1 [端口]` | 查看端口占用，默认 18888 |
| `kill_port.ps1 [端口]` | 释放端口进程（必要时管理员运行） |

## Docker / Dapr 排障

| 脚本 | 说明 |
|------|------|
| `docker_fix_conflicts.ps1` | 修复容器名冲突 |
| `docker_diagnose.ps1` | 诊断服务未运行原因 |
| `dapr_restart_scheduler.ps1` | 重启 Dapr Scheduler 容器 |

## 测试与验证

| 脚本 | 说明 |
|------|------|
| `test.ps1` | 测试 API：健康、路由、聊天、Dapr |
| `run_tests.ps1` | 运行 pytest（单元/集成） |
| `verify_system.ps1` | 系统验证：Conda、端口、数据库、Dapr |

---

**推荐流程**：首次 `.\scripts\setup.ps1` → 编辑 `.env` → 可选 `scripts\download_models.bat` → 日常 `.\scripts\start.ps1` / `.\scripts\stop.ps1`；端口占用用 `.\scripts\kill_port.ps1 18888`，容器冲突用 `.\scripts\docker_fix_conflicts.ps1`。
