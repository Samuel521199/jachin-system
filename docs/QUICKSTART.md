# Jachin-System v3.2 快速开始

**版本**: v3.2 | **最后更新**: 2026-02

---

## 一、快速启动（5 分钟）

### 1. 验证系统

```powershell
.\scripts\verify_system.ps1
```

检查：Conda、后端端口 18888、PostgreSQL、Qdrant、Docker、Python 依赖。

### 2. 启动服务

**方式 A：一键启动（推荐）**

```powershell
.\scripts\start_all.ps1
```

自动启动：Docker、Redis、MQTT、Dapr、后端 API。

**方式 B：仅后端**

```powershell
.\scripts\start.ps1
```

### 3. 验证

```powershell
curl http://localhost:18888/health
start http://localhost:18888/docs
```

### 4. 启动桌面客户端

```powershell
cd clients\desktop
npm run tauri:dev
```

---

## 二、首次安装（开发环境）

### 1. 创建 Conda 环境

```bash
conda env create -f environment.yml
conda activate jachin-dev
```

### 2. 安装 Python 依赖

```bash
# 项目根目录
pip install -r requirements.txt
```

### 3. 启动基础设施

```bash
docker-compose -f docker-compose.dev.yml up -d
```

启动：Redis、MQTT、Dapr Placement。PostgreSQL 与 Qdrant 需本地安装，见 [LOCAL_DATABASE_SETUP.md](./LOCAL_DATABASE_SETUP.md)。

### 4. 初始化数据库

```powershell
.\installer\init_database.ps1
```

### 5. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，至少设置 QWEN_API_KEY
```

---

## 三、常见问题

| 问题 | 检查 | 解决 |
|------|------|------|
| 后端无法启动 | `netstat -ano \| findstr :18888` | 停止占用进程 |
| 桌面无法连接 | 后端是否运行在 18888 | 检查 `clients/desktop/src/lib/api.ts` 中 `BACKEND_URL` |
| 查询无响应 | 检查 `.env` 中 LLM 配置 | 配置 QWEN_API_KEY | 运行 `.\scripts\test_functionality.ps1` |
| 数据库连接失败 | `.\scripts\check_local_databases.ps1` | 参考 [LOCAL_DATABASE_SETUP.md](./LOCAL_DATABASE_SETUP.md) |

---

## 四、下一步

- 架构：[whitepaper_v3.2_final.md](./whitepaper_v3.2_final.md)
- 开发：[DEVELOPMENT.md](./DEVELOPMENT.md)
- 端口配置：[PORT_CONFIGURATION.md](./PORT_CONFIGURATION.md)
