# L1 + L2 一键部署

将 Jachin Layer 1（平台）和 Layer 2（控制面）部署到任意机器，**仅需 Docker**，无需预装 Node.js、Python、PostgreSQL。

## 前置要求

- **Docker Desktop**（Windows/Mac）或 **Docker Engine + Docker Compose**（Linux）
- 下载: https://www.docker.com/products/docker-desktop/

---

## 方式一：目标机器无代码（推荐）

适用于目标机器没有项目源码，只需拷贝部署包即可运行。

### 步骤 1：在开发机（有完整代码）导出镜像

**Windows:**
```cmd
deploy\export-images.bat
```

**Linux/Mac:**
```bash
chmod +x deploy/export-images.sh
./deploy/export-images.sh
```

会生成 `jachin-l1-l2-images.tar`（约 1–2GB）。

### 步骤 2：拷贝到目标机器

将以下文件拷贝到目标机器同一目录：

- `docker-compose.deploy-l1-l2-images.yml`
- `jachin-l1-l2-images.tar`
- `deploy/import-and-run.bat`（Windows）或 `deploy/import-and-run.sh`（Linux）

### 步骤 3：在目标机器执行

**Windows:** 双击 `import-and-run.bat` 或在命令行运行

**Linux/Mac:**
```bash
chmod +x import-and-run.sh
./import-and-run.sh
```

---

## 方式二：目标机器有完整代码

在项目根目录执行：

**Windows:**
```cmd
deploy\start-l1-l2.bat
```

**Linux/Mac:**
```bash
chmod +x deploy/start-l1-l2.sh
./deploy/start-l1-l2.sh
```

---

## 访问地址

| 服务 | 地址 |
|------|------|
| L1 平台 (Nexus) | http://localhost:3000 |
| L2 控制面 API | http://localhost:18888 |

## 停止服务

```bash
docker compose -f docker-compose.deploy-l1-l2-images.yml down
```

## 依赖说明

部署会自动拉起以下容器（无需手动安装）：

- **PostgreSQL 15**：L1 平台数据库（端口 5432）
- **Redis 7**：L2 协同调度（端口 6379）
- **L1 Nexus**：Next.js 平台（端口 3000）
- **L2 Control**：Python FastAPI 控制面（端口 18888）

数据持久化在 Docker volumes 中，重启不丢失。
