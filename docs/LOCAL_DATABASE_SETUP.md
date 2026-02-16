# 本地数据库配置指南

在开发环境中，PostgreSQL 和 Qdrant 使用本地安装的版本，而不是 Docker 容器。

## 📋 前置要求

- Windows 10/11 或 Linux/macOS
- 管理员权限（用于安装服务）

## 🐘 PostgreSQL 安装与配置

### Windows

#### 方法 1: 使用 PostgreSQL 官方安装程序（推荐）

1. **下载 PostgreSQL**
   - 访问：https://www.postgresql.org/download/windows/
   - 下载 PostgreSQL 15+ 安装程序

2. **安装 PostgreSQL**
   - 运行安装程序
   - 设置端口：`5432`（默认）
   - 设置超级用户密码：`secure_password`（或自定义）
   - 选择安装位置（建议使用默认路径）

3. **创建数据库和用户**
   ```powershell
   # 使用 psql 命令行工具
   psql -U postgres
   
   # 在 PostgreSQL 命令行中执行：
   CREATE USER jachin WITH PASSWORD 'secure_password';
   CREATE DATABASE jachin_brain OWNER jachin;
   GRANT ALL PRIVILEGES ON DATABASE jachin_brain TO jachin;
   \q
   ```

#### 方法 2: 使用 Chocolatey（快速安装）

```powershell
# 安装 Chocolatey（如果未安装）
Set-ExecutionPolicy Bypass -Scope Process -Force; [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))

# 安装 PostgreSQL
choco install postgresql15 --params '/Password:secure_password'

# 创建数据库和用户
psql -U postgres -c "CREATE USER jachin WITH PASSWORD 'secure_password';"
psql -U postgres -c "CREATE DATABASE jachin_brain OWNER jachin;"
psql -U postgres -c "GRANT ALL PRIVILEGES ON DATABASE jachin_brain TO jachin;"
```

### Linux (Ubuntu/Debian)

```bash
# 安装 PostgreSQL
sudo apt update
sudo apt install postgresql postgresql-contrib

# 启动 PostgreSQL 服务
sudo systemctl start postgresql
sudo systemctl enable postgresql

# 创建数据库和用户
sudo -u postgres psql << EOF
CREATE USER jachin WITH PASSWORD 'secure_password';
CREATE DATABASE jachin_brain OWNER jachin;
GRANT ALL PRIVILEGES ON DATABASE jachin_brain TO jachin;
\q
EOF
```

### macOS

```bash
# 使用 Homebrew 安装
brew install postgresql@15

# 启动 PostgreSQL 服务
brew services start postgresql@15

# 创建数据库和用户
psql postgres << EOF
CREATE USER jachin WITH PASSWORD 'secure_password';
CREATE DATABASE jachin_brain OWNER jachin;
GRANT ALL PRIVILEGES ON DATABASE jachin_brain TO jachin;
\q
EOF
```

### 验证 PostgreSQL 安装

```powershell
# Windows PowerShell
psql -U jachin -d jachin_brain -c "SELECT version();"

# Linux/macOS
psql -U jachin -d jachin_brain -c "SELECT version();"
```

## 🔍 Qdrant 安装与配置

### Windows

#### 方法 1: 使用预编译二进制文件（推荐）

1. **下载 Qdrant**
   - 访问：https://github.com/qdrant/qdrant/releases
   - 下载最新版本的 `qdrant-windows-x86_64.zip`

2. **解压并运行**
   ```powershell
   # 解压到目录，例如：C:\qdrant
   Expand-Archive -Path qdrant-windows-x86_64.zip -DestinationPath C:\qdrant
   
   # 运行 Qdrant
   cd C:\qdrant
   .\qdrant.exe
   ```

3. **配置为 Windows 服务（可选）**
   ```powershell
   # 使用 NSSM (Non-Sucking Service Manager)
   # 下载 NSSM: https://nssm.cc/download
   nssm install Qdrant "C:\qdrant\qdrant.exe"
   nssm start Qdrant
   ```

#### 方法 2: 使用 Docker（如果已安装 Docker Desktop）

```powershell
# 即使使用本地 PostgreSQL，也可以使用 Docker 运行 Qdrant
docker run -p 6333:6333 -p 6334:6334 qdrant/qdrant:latest
```

### Linux

```bash
# 下载并运行 Qdrant
curl -L https://github.com/qdrant/qdrant/releases/download/v1.7.0/qdrant-x86_64-unknown-linux-musl.tar.gz | tar xz
./qdrant
```

### macOS

```bash
# 使用 Homebrew 安装
brew install qdrant

# 启动 Qdrant
qdrant
```

### 验证 Qdrant 安装

```powershell
# 检查健康状态
curl http://localhost:6333/health

# 应该返回：{"status":"ok"}
```

## ⚙️ 配置环境变量

确保 `.env` 文件中的数据库连接配置正确：

```env
# PostgreSQL 配置（本地）
DATABASE_URL=postgresql://jachin:secure_password@localhost:5432/jachin_brain

# Qdrant 配置（本地）
QDRANT_URL=http://localhost:6333
QDRANT_GRPC_URL=http://localhost:6334
```

## 🚀 启动开发环境

1. **启动其他 Docker 服务**（Redis、MQTT、Dapr 等）
   ```powershell
   docker-compose -f docker-compose.dev.yml up -d
   ```

2. **验证服务状态**
   ```powershell
   # 检查 PostgreSQL
   psql -U jachin -d jachin_brain -c "SELECT 1;"
   
   # 检查 Qdrant
   curl http://localhost:6333/health
   
   # 检查 Redis（Docker）
   docker exec jachin-redis-dev redis-cli ping
   ```

3. **初始化数据库**
   ```powershell
   .\installer\init_database.ps1
   ```

## 🔧 故障排查

### PostgreSQL 连接失败

**问题**: `Connection refused` 或 `password authentication failed`

**解决**:
1. 检查 PostgreSQL 服务是否运行：
   ```powershell
   # Windows
   Get-Service postgresql*
   
   # Linux
   sudo systemctl status postgresql
   
   # macOS
   brew services list | grep postgresql
   ```

2. 检查端口是否被占用：
   ```powershell
   netstat -an | findstr :5432
   ```

3. 检查 `pg_hba.conf` 配置（Linux/macOS）：
   ```bash
   sudo nano /etc/postgresql/15/main/pg_hba.conf
   # 确保有：host all all 127.0.0.1/32 md5
   ```

### Qdrant 连接失败

**问题**: `Connection refused` 或无法访问 `http://localhost:6333`

**解决**:
1. 检查 Qdrant 是否运行：
   ```powershell
   # 检查进程
   Get-Process | Where-Object {$_.ProcessName -like "*qdrant*"}
   
   # 检查端口
   netstat -an | findstr :6333
   ```

2. 检查防火墙设置（Windows）：
   ```powershell
   # 允许 Qdrant 通过防火墙
   New-NetFirewallRule -DisplayName "Qdrant" -Direction Inbound -LocalPort 6333,6334 -Protocol TCP -Action Allow
   ```

## 📝 注意事项

1. **端口冲突**: 确保本地 PostgreSQL 和 Qdrant 使用的端口（5432、6333、6334）未被其他服务占用

2. **数据持久化**: 本地数据库的数据会保存在本地文件系统中，不会因为 Docker 容器重启而丢失

3. **性能**: 本地数据库通常比 Docker 容器中的数据库性能更好，特别是在 Windows 上

4. **备份**: 定期备份本地数据库：
   ```powershell
   # PostgreSQL 备份
   pg_dump -U jachin jachin_brain > backup_$(Get-Date -Format "yyyyMMdd").sql
   
   # Qdrant 备份（如果配置了快照）
   # Qdrant 数据存储在配置的数据目录中
   ```

## 🔄 切换回 Docker 数据库

如果需要切换回使用 Docker 容器中的数据库，只需：

1. 取消注释 `docker-compose.dev.yml` 中的 PostgreSQL 和 Qdrant 服务
2. 更新 `.env` 文件中的连接配置
3. 重启 Docker 服务：
   ```powershell
   docker-compose -f docker-compose.dev.yml up -d postgres qdrant
   ```
