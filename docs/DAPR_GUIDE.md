# Dapr 完整配置指南

## 目录

1. [运行时设置](#运行时设置)
2. [Placement 与 Scheduler 地址配置](#placement-与-scheduler-地址配置)
3. [Redis 连接配置](#redis-连接配置)
4. [手动安装指南](#手动安装指南)

---

## 运行时设置

### 问题 1: Dapr 运行时文件缺失

如果遇到错误：
```
fork/exec C:\Users\thore\.dapr\bin\daprd.exe: The system cannot find the file specified.
```

说明 Dapr 运行时文件未安装。

### 问题 2: Dapr 容器冲突

如果遇到错误：
```
dapr_placement container exists or is running. please run `dapr uninstall` first before running `dapr init`
```

这是因为：
- 之前初始化过 Dapr，残留了容器
- 或者 docker-compose 中的 `jachin-dapr-placement-dev` 与 Dapr 默认容器冲突

**重要**：我们的项目使用 `docker-compose.dev.yml` 管理 Dapr Placement 服务，所以初始化 Dapr 时需要跳过 placement 的创建。

### 解决方案

#### 方法 1: 使用修复脚本（推荐）

```powershell
.\scripts\fix_dapr.ps1
```

这个脚本会：
1. 检查并卸载旧的 Dapr 容器
2. 使用 `--skip-placement` 初始化（因为我们用 docker-compose 管理 placement）
3. 验证安装

#### 方法 2: 手动修复

```powershell
# 1. 卸载旧的 Dapr 安装
dapr uninstall

# 2. 使用 --skip-placement 初始化（因为我们用 docker-compose 管理 placement）
dapr init --skip-placement

# 3. 验证
dapr --version
```

#### 方法 3: 如果 Docker 连接失败

如果 `dapr init` 提示无法连接 Docker：

1. **启动 Docker Desktop**
   - 打开 Docker Desktop 应用
   - 等待 Docker 完全启动（状态栏显示 "Docker Desktop is running"）

2. **验证 Docker 连接**
   ```powershell
   docker ps
   # 应该能正常列出容器，而不是报错
   ```

3. **重新初始化**
   ```powershell
   dapr uninstall
   dapr init --skip-placement
   ```

#### 方法 4: 指定版本初始化

如果默认初始化失败，尝试指定版本：

```powershell
dapr uninstall
dapr init --skip-placement --runtime-version 1.16.5
```

### 为什么使用 --skip-placement？

我们的项目在 `docker-compose.dev.yml` 中已经配置了 Dapr Placement 服务：

```yaml
dapr-placement:
  image: "daprio/dapr:latest"
  container_name: jachin-dapr-placement-dev
  command: ["./placement", "-port", "50005"]
```

因此，初始化 Dapr 时不需要创建额外的 placement 容器。使用 `--skip-placement` 可以避免容器冲突。

### 验证安装

#### 检查 Dapr CLI

```powershell
dapr --version
# 应该显示：
# CLI version: 1.16.5
# Runtime version: 1.16.5  (不是 n/a)
```

#### 检查运行时文件

```powershell
Test-Path "$env:USERPROFILE\.dapr\bin\daprd.exe"
# 应该返回 True
```

#### 检查 Dapr 组件

```powershell
ls "$env:USERPROFILE\.dapr\components"
# 应该看到默认组件文件
```

### 常见问题

#### Q: `dapr init` 失败，提示无法连接 Docker

**A**: 
1. 确保 Docker Desktop 正在运行
2. 检查 Docker 服务状态：`docker ps`
3. 重启 Docker Desktop
4. 重新运行 `dapr init --skip-placement`

#### Q: Runtime version 显示 n/a

**A**: 
- 运行时文件未下载
- 运行 `dapr init --skip-placement` 重新下载
- 或手动下载：https://github.com/dapr/dapr/releases

#### Q: 初始化后仍然找不到 daprd.exe

**A**:
1. 检查文件是否存在：`Test-Path "$env:USERPROFILE\.dapr\bin\daprd.exe"`
2. 如果不存在，检查网络连接（需要下载文件）
3. 尝试手动下载并放置到正确位置

#### Q: 容器冲突错误

**A**:
1. 运行 `.\scripts\fix_dapr.ps1` 自动修复
2. 或手动：`dapr uninstall` 然后 `dapr init --skip-placement`
3. 确保 docker-compose 中的 placement 服务正在运行

---

## Placement 与 Scheduler 地址配置

### 背景

`start.ps1` 使用 `dapr run` 启动后端时，daprd 需要连接 Placement 和 Scheduler 服务。不同部署场景下，这些服务的地址不同。

### 部署场景

| 场景 | Placement | Scheduler | 配置方式 |
|------|-----------|-----------|----------|
| **本地开发** | Docker 映射到 localhost:6050 | Docker 映射到 localhost:6060 | 默认，无需配置 |
| **云/多级部署** | 远程主机，如 placement.example.com:6050 | 远程主机，如 scheduler.example.com:6060 | 在 .env 或环境变量中设置 |
| **同网段 mDNS** | 通过 mDNS 发现 | 通过 mDNS 发现 | 设置 `DAPR_PLACEMENT_HOST_ADDRESS=skip` 等 |

### 环境变量

在 `.env` 或系统环境变量中可设置：

```bash
# 云/远程部署示例
DAPR_PLACEMENT_HOST_ADDRESS=placement.example.com:6050
DAPR_SCHEDULER_HOST_ADDRESS=scheduler.example.com:6060

# 使用 mDNS 发现（同网段、K8s 等）
DAPR_PLACEMENT_HOST_ADDRESS=skip
DAPR_SCHEDULER_HOST_ADDRESS=skip
```

### 说明

- **不设置**：默认使用 `localhost:6050`、`localhost:6060`，适用于 `start.bat` + Docker Compose 本地开发
- **设为 skip / mdns / 空**：不传递对应参数，daprd 使用 mDNS 发现
- **设为具体地址**：用于云、多级部署或端口与默认不同的情况

### 常见错误

若出现 `dial tcp 172.19.0.5:6060: i/o timeout`：

- 原因：mDNS 返回了 Docker 容器内网 IP，宿主机无法访问
- 处理：显式指定 `localhost:6060`（默认已配置），或检查 `.env` 中是否误设为 `skip`

---

## Redis 连接配置

### 核心配置说明

在**混合开发模式**下，Dapr 组件需要明确连接到 `jachin-redis-dev` 容器，而不是 Dapr 默认的 `dapr_redis`。

### 关键点

#### 为什么使用 `localhost:6379`？

- **开发环境**: 后端代码运行在本地 Conda 环境
- **Redis**: 运行在 Docker 容器 `jachin-redis-dev` 中
- **连接方式**: 通过 Docker 端口映射 `6379:6379`，从本地 `localhost:6379` 连接

### 配置位置

Dapr 组件配置文件位于 `dapr/components/`:
- `statestore-redis.yaml` - 状态存储
- `pubsub-redis.yaml` - 发布订阅

### 配置文件

#### statestore-redis.yaml

```yaml
apiVersion: dapr.io/v1alpha1
kind: Component
metadata:
  name: statestore
  namespace: default
spec:
  type: state.redis
  version: v1
  metadata:
    - name: redisHost
      value: localhost:6379  # 指向 jachin-redis-dev（通过端口映射）
    - name: redisPassword
      value: ""  # Redis 默认无密码
    - name: enableTLS
      value: "false"
    - name: maxRetries
      value: "3"
    - name: maxRetryBackoff
      value: "2s"
```

#### pubsub-redis.yaml

```yaml
apiVersion: dapr.io/v1alpha1
kind: Component
metadata:
  name: pubsub
  namespace: default
spec:
  type: pubsub.redis
  version: v1
  metadata:
    - name: redisHost
      value: localhost:6379  # 指向 jachin-redis-dev（通过端口映射）
    - name: redisPassword
      value: ""
    - name: enableTLS
      value: "false"
    - name: maxRetries
      value: "3"
    - name: maxRetryBackoff
      value: "2s"
```

### 验证配置

#### 1. 检查 Redis 容器

```powershell
# 检查容器是否运行
docker ps | Select-String "jachin-redis-dev"

# 检查端口映射
docker port jachin-redis-dev
# 应该显示: 6379/tcp -> 0.0.0.0:6379
```

#### 2. 测试 Redis 连接

```powershell
# 从容器内部测试
docker exec jachin-redis-dev redis-cli ping
# 应该返回: PONG

# 从本地测试（如果安装了 redis-cli）
redis-cli -h localhost -p 6379 ping
# 应该返回: PONG
```

#### 3. 使用验证脚本

```powershell
.\scripts\verify_dapr_redis.ps1
```

#### 4. 启动后端并检查 Dapr 组件

```powershell
# 启动后端
conda activate jachin-dev
.\scripts\start_backend.ps1

# 在另一个终端检查 Dapr 组件
curl http://localhost:3500/v1.0/components
```

### 常见问题

#### Q: Dapr 仍然连接到默认的 dapr_redis？

**A**: 确保：
1. 使用 `--resources-path ./dapr/components` 参数启动 Dapr
2. 组件文件在正确的路径
3. 组件文件名正确（`statestore-redis.yaml`, `pubsub-redis.yaml`）

#### Q: 连接被拒绝？

**A**: 
1. 检查 Redis 容器是否运行: `docker ps | grep redis`
2. 检查端口映射: `docker port jachin-redis-dev`
3. 检查防火墙设置

#### Q: 如何确认 Dapr 使用了正确的 Redis？

**A**: 
1. 启动后端服务
2. 查看 Dapr 日志（在启动输出中）
3. 应该看到类似信息：
   ```
   component loaded. name: statestore, type: state.redis
   component loaded. name: pubsub, type: pubsub.redis
   ```

### 环境差异

#### 开发环境（本地 Conda）

```yaml
metadata:
  - name: redisHost
    value: localhost:6379  # 通过端口映射连接 Docker 容器
```

#### 生产环境（Docker/Kubernetes）

```yaml
metadata:
  - name: redisHost
    value: redis:6379  # 容器内部网络
```

---

## 手动安装指南

### 问题：网络连接失败

如果 `dapr init` 失败，错误信息类似：
```
Failed to get runtime version: 'https://api.github.com/repos/dapr/dapr/releases - 401 Unauthorized'
cannot get the latest release version: 'Get "https://dapr.github.io/helm-charts/index.yaml": ...'
```

这是因为 Dapr CLI 无法从网络获取最新版本信息。

### 解决方案

#### 方案 1: 使用指定版本初始化（推荐）

```powershell
# 指定版本号，避免网络请求
dapr init -s --runtime-version 1.16.5
```

如果仍然失败，使用方案 2。

#### 方案 2: 手动下载运行时文件

##### 步骤 1: 创建目录

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.dapr\bin"
```

##### 步骤 2: 下载 Dapr 运行时

**方法 A: 使用脚本（推荐）**

```powershell
.\scripts\download_dapr_runtime.ps1
```

**方法 B: 手动下载**

1. 访问：https://github.com/dapr/dapr/releases/tag/v1.16.5
2. 下载：`daprd_windows_amd64.zip`
3. 解压，找到 `daprd.exe`
4. 复制到：`C:\Users\<你的用户名>\.dapr\bin\daprd.exe`

##### 步骤 3: 验证安装

```powershell
# 检查文件是否存在
Test-Path "$env:USERPROFILE\.dapr\bin\daprd.exe"
# 应该返回 True

# 检查版本（可能仍然显示 n/a，但文件存在即可）
dapr --version
```

#### 方案 3: 使用代理或镜像

如果你有代理或镜像源：

```powershell
# 设置代理（如果有）
$env:HTTP_PROXY = "http://your-proxy:port"
$env:HTTPS_PROXY = "http://your-proxy:port"

# 然后运行
dapr init -s --runtime-version 1.16.5
```

### 验证安装

安装后检查：

```powershell
# 1. 检查运行时文件
Test-Path "$env:USERPROFILE\.dapr\bin\daprd.exe"
# 应该返回 True

# 2. 检查版本
dapr --version
# CLI version: 1.16.5
# Runtime version: 可能显示 n/a，但只要文件存在就可以使用

# 3. 尝试启动服务
.\scripts\restart_and_start.ps1
```

### 重要说明

#### 为什么 Runtime version 可能显示 n/a？

即使 `daprd.exe` 文件存在，`dapr --version` 可能仍然显示 `Runtime version: n/a`。这是因为：

1. Dapr CLI 通过检查文件或网络来确定版本
2. 如果网络不可用，可能无法确定版本
3. **但这不影响使用** - 只要 `daprd.exe` 文件存在，Dapr 就可以正常工作

#### 验证运行时是否可用

真正验证 Dapr 是否可用，应该尝试启动服务：

```powershell
# 启动后端服务
.\scripts\start_backend.ps1

# 如果看到 "Application discovery..." 和 "Starting Dapr with id jachin-brain"
# 说明 Dapr 运行时正常工作
```

### 安装后启动服务

```powershell
# 完整重启
.\scripts\restart_and_start.ps1

# 或仅启动后端
.\scripts\start_backend.ps1
```

### 如果仍然失败

如果手动下载后仍然无法启动：

1. **检查文件权限**：
   ```powershell
   Get-Item "$env:USERPROFILE\.dapr\bin\daprd.exe" | Select-Object FullName, Mode
   ```

2. **检查文件完整性**：
   ```powershell
   # 文件大小应该约为 50-100 MB
   (Get-Item "$env:USERPROFILE\.dapr\bin\daprd.exe").Length
   ```

3. **尝试直接运行**：
   ```powershell
   & "$env:USERPROFILE\.dapr\bin\daprd.exe" --version
   ```

4. **查看详细错误**：
   ```powershell
   # 启动时会显示详细错误信息
   dapr run --app-id test --app-port 8000 -- python -c "print('test')"
   ```

---

## 相关文档

- [Dapr Redis 状态存储](https://docs.dapr.io/reference/components-reference/supported-state-stores/setup-redis/)
- [Dapr Redis 发布订阅](https://docs.dapr.io/reference/components-reference/supported-pubsub/setup-redis-pubsub/)

---

**💡 提示**：如果网络问题持续，建议使用手动下载方案（方案 2），这是最可靠的方法。
