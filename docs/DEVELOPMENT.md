# 开发环境配置指南

## 混合开发流程（Hybrid Workflow）

Jachin-System 采用混合开发模式，充分利用本地高性能硬件和容器化基础设施：

- **基础设施** → Docker Compose（Qdrant、PostgreSQL、Redis 等）
- **后端代码/AI 模型** → Conda 环境（直接使用本地 GPU）
- **客户端** → 本地原生工具（Rust/C++ 直接编译）

## 为什么使用混合模式？

### 1. GPU 开发优势

在 Docker 容器内调试 CUDA 往往比宿主机麻烦：
- 需要配置 NVIDIA Container Toolkit
- 镜像体积大，构建时间长
- 环境切换不灵活

使用 Conda 可以直接利用本地 GPU：
- 快速切换 PyTorch/TensorFlow/CUDA 版本
- 无需反复构建 Docker 镜像
- 更好的调试体验

### 2. 环境隔离与复用

你的机器可能同时跑多个 AI 实验：
- Conda 可以快速创建/切换环境
- 不同项目可以使用不同的 CUDA/PyTorch 版本
- 避免环境冲突

## 环境要求

### 必需工具

1. **Conda** (Miniconda 或 Anaconda)
   - 下载: https://docs.conda.io/en/latest/miniconda.html
   - 用于管理 Python 环境和依赖

2. **Docker & Docker Compose**
   - 下载: https://www.docker.com/products/docker-desktop
   - 用于运行基础设施服务

3. **Dapr CLI**
   - Windows: `choco install dapr-cli`
   - macOS: `brew install dapr/tap/dapr-cli`
   - Linux: `wget -q https://raw.githubusercontent.com/dapr/cli/master/install/install.sh -O - | /bin/bash`

### 可选工具

- **NVIDIA CUDA Toolkit** (如果使用 GPU)
- **Git** (版本控制)

## 快速开始

### 1. 自动设置（推荐）

**Windows (PowerShell)**:
```powershell
.\scripts\setup_dev_env.ps1
```

**Linux/macOS**:
```bash
chmod +x scripts/setup_dev_env.sh
./scripts/setup_dev_env.sh
```

### 2. 手动设置

#### 步骤 1: 创建 Conda 环境

```bash
# environment.yml 在项目根目录
conda env create -f environment.yml
conda activate jachin-dev
```

#### 步骤 2: 启动基础设施服务

```bash
# 使用开发环境配置（仅基础设施）
docker-compose -f docker-compose.dev.yml up -d

# 验证服务状态
docker-compose -f docker-compose.dev.yml ps
```

#### 步骤 3: 初始化 Dapr（如果未初始化）

```bash
dapr init
```

#### 步骤 4: 配置环境变量

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env 文件，配置必要的变量
# 至少需要设置 QWEN_API_KEY（如果使用 Qwen）
```

#### 步骤 5: 启动后端服务

**Windows (PowerShell)**:
```powershell
.\scripts\start_backend.ps1
```

**Linux/macOS**:
```bash
chmod +x scripts/start_backend.sh
./scripts/start_backend.sh
```

**手动启动**:
```bash
# 激活 Conda 环境
conda activate jachin-dev

# 设置环境变量
export DAPR_HTTP_PORT=3500
export DAPR_GRPC_PORT=50001
export APP_PORT=8000

# 使用 Dapr 启动（从项目根目录）
# 这是一个强大的命令：启动 Python 服务并挂载 Dapr Sidecar
# 既能使用本地 GPU，又能连接 Docker 中的数据库
dapr run \
    --app-id jachin-brain \
    --app-port 8000 \
    --dapr-http-port 3500 \
    --dapr-grpc-port 50001 \
    --resources-path ./dapr/components \
    --config ./dapr/config/config.yaml \
    -- python core/main.py
```

## 开发工作流

### 日常开发

1. **启动基础设施**（一次性）:
   ```bash
   docker-compose -f docker-compose.dev.yml up -d
   ```

2. **激活 Conda 环境**:
   ```bash
   conda activate jachin-dev
   ```

3. **启动后端服务**（带 Dapr sidecar）:
   ```bash
   ./scripts/start_backend.sh  # 或 start_backend.ps1
   ```

4. **开发代码**: 
   - 代码修改会自动重载（uvicorn --reload）
   - Dapr sidecar 自动处理服务发现和状态管理

5. **运行测试**:
   ```bash
   pytest tests/
   ```

6. **运行示例**:
   ```bash
   python examples/llm_usage_example.py
   python examples/memory_usage_example.py
   python examples/dapr_usage_example.py
   ```

### 停止服务

```bash
# 停止后端服务: Ctrl+C

# 停止基础设施服务
docker-compose -f docker-compose.dev.yml down

# 或仅停止不删除数据
docker-compose -f docker-compose.dev.yml stop
```

## Conda 环境管理

### 更新环境

```bash
conda env update -f environment.yml --prune
```

### 添加新依赖

1. 编辑 `environment.yml`（项目根目录）
2. 运行更新命令（见上）
3. 或使用 conda/pip 安装后导出:
   ```bash
   conda env export > environment.yml
   ```

### 切换环境

```bash
# 激活环境
conda activate jachin-dev

# 停用环境
conda deactivate
```

### 删除环境

```bash
conda env remove -n jachin-dev
```

## GPU 支持（可选）

如果需要使用 GPU 进行本地模型推理：

### 1. 验证 GPU 配置

`environment.yml` 已包含 PyTorch 和 CUDA 12.1 支持（适配 Nvidia DGX）。

如果使用不同的 CUDA 版本，编辑 `environment.yml`：

```yaml
dependencies:
  - pytorch-cuda=11.8  # 或其他版本
```

然后更新环境：
```bash
conda env update -f environment.yml --prune
```

### 2. 验证 GPU

```python
import torch
print(f"CUDA available: {torch.cuda.is_available()}")
print(f"CUDA device: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A'}")
```

## 故障排查

### 问题 1: Conda 环境创建失败

**解决方案**:
- 检查网络连接（需要下载包）
- 尝试使用国内镜像源:
  ```bash
  conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/free/
  conda config --add channels https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main/
  ```

### 问题 2: Docker 服务无法启动

**解决方案**:
- 检查 Docker Desktop 是否运行
- 检查端口是否被占用: `netstat -an | grep 5432`
- 查看日志: `docker-compose -f docker-compose.dev.yml logs`

### 问题 3: Dapr sidecar 无法连接

**解决方案**:
- 检查 Dapr 是否初始化: `dapr --version`
- 检查端口是否被占用: `netstat -an | grep 3500`
- 查看 Dapr 日志（在启动脚本的输出中）

### 问题 4: 无法连接到基础设施服务

**解决方案**:
- 确保服务已启动: `docker-compose -f docker-compose.dev.yml ps`
- 检查网络连接: `docker network inspect jachin-network-dev`
- 验证服务健康: `curl http://localhost:6333/health` (Qdrant)

## 生产部署

开发环境使用 Conda，但生产环境仍使用 Docker：

```bash
# 构建生产镜像
docker-compose build backend

# 启动生产环境（包含所有服务）
docker-compose up -d
```

## 相关文档

- [Conda 用户指南](https://docs.conda.io/projects/conda/en/latest/user-guide/)
- [Dapr 本地开发](https://docs.dapr.io/developing-applications/developing-applications-overview/)
- [Docker Compose 文档](https://docs.docker.com/compose/)
