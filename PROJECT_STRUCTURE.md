# Jachin-System v3.2 项目结构

## 文档信息

- **版本**: v3.2
- **最后更新**: 2026-02-03
- **架构**: 分布式智能体操作系统 (Distributed Agent OS)

---

## 目录树

```
jachin-system/
├── .cursor/                  # Cursor IDE 规则配置
│   └── rules/
│       ├── 000-structure.mdc      # v3.2 三层架构目录规范
│       └── 050-distributed.mdc    # Ray 集群与 Dapr 通信规范
│
├── cloud/                    # [Tier 1] 云端代码 (Go/Python)
│   ├── marketplace/          # 商城后端
│   └── auth/                 # 鉴权中心
│
├── core/                     # [Tier 2] 核心蜂巢代码 (Python)
│   ├── app/                  # 桌面服务封装 (Tray Icon, System Service)
│   ├── api/                  # FastAPI 网关
│   ├── brain/                # 智能层
│   │   ├── llm/              # 本地/云端模型适配器
│   │   ├── ray_cluster/      # [NEW] Ray 集群管理与调度
│   │   └── planner/          # 任务编排
│   ├── config/               # 配置管理
│   ├── dapr/                 # Dapr 集成
│   ├── memory/               # Qdrant 记忆管理 (含权限过滤)
│   │   └── schema/           # 数据库Schema (PostgreSQL)
│   ├── registry/             # JCP 设备与能力注册表
│   ├── runtime/              # 技能运行沙箱 (Docker/Wasm)
│   │   ├── sandbox/          # 沙箱实现
│   │   └── schemas/          # Schema定义
│   ├── voice/                # 语音处理 (STT/TTS)
│   ├── web_ui/               # [NEW] 本地管理后台 (React Build)
│   ├── main.py               # 启动入口 (自动检测 Single/Cluster 模式)
│   └── requirements.txt      # Python 依赖
│
├── clients/                  # [Tier 3] 客户端
│   ├── desktop/              # Tauri v2 桌面精灵
│   ├── mobile/               # Flutter App
│   └── iot/                  # 树莓派/ESP32 脚本
│
├── skills_repo/              # [Local] 已安装技能存储库
│
├── jachin-plugin-sdk/        # [Dev] JPP 开发者脚手架（Rust Wasm、版税、5 分钟上架）
├── jachin-plugin-sdk-python/ # [Dev] JPP Python 脚手架（py2wasm、@jachin_plugin、分润）
│
├── installer/                # [Deploy] 一键安装/集群配对脚本
│
├── dapr/                     # Dapr 配置
│   ├── components/           # Dapr 组件配置
│   └── config/               # Dapr 运行时配置
│
├── docs/                     # 文档
│   ├── architecture.md       # v3.2 架构设计（主文档）
│   ├── whitepaper_v3.2.md   # v3.2 完整白皮书
│   └── ARCHITECTURE_V3.2_GAP_ANALYSIS.md  # 架构差距分析
│
├── docker-compose.yml        # 基础设施 (Redis, Qdrant, Postgres)
├── .env.example              # 环境变量示例
├── .gitignore               # Git 忽略文件
└── README.md                 # 项目主文档
```

---

## 三层架构职责

### Tier 1: Jachin Market (The Cloud)

**目录**: `cloud/`

**职责**:
- 全球技能商店：浏览、搜索、购买技能
- 用户授权：OAuth 2.0 / JWT 认证
- 计费网关：订阅、一次性购买、使用量计费
- 技能审核：代码安全扫描、功能测试

**技术栈**: Go / Python（高并发接口）

**部署**: 云端 SaaS（AWS / 阿里云）

---

### Tier 2: Jachin Hive (The Core)

**目录**: `core/`

**职责**:
- **AI 推理**: 本地/云端模型适配，支持 Ray 分布式计算
- **记忆存储**: PostgreSQL（关系型数据）+ Qdrant（向量数据）
- **设备管理**: JCP 协议，设备注册与能力发现
- **任务编排**: Ray Scheduler，智能任务分发
- **技能运行时**: Docker / Wasm 沙箱，安全执行

**架构模式**:
- **Master Node (The Queen)**: Control Plane、Brain Orchestrator、Memory、Device Registry
- **Worker Nodes (The Drones)**: GPU Node、CPU Node，执行实际计算任务

**技术栈**:
- Control Plane: FastAPI + Dapr
- Compute: Ray（分布式计算框架）
- Storage: PostgreSQL + Qdrant
- Protocol: JCP (基于 Dapr Pub/Sub)
- Discovery: mDNS / Zeroconf

**部署**: Docker / PyInstaller（Server 模式用 Docker，Personal 模式打包成 .exe 服务）

---

### Tier 3: Jachin Terminal (The Edge)

**目录**: `clients/`

**职责**: 用户交互界面，只负责 I/O，不负责重度计算

**核心功能**:
- **桌面精灵**: Tauri v2 + React，透明窗口、系统级 API
- **手机 App**: Flutter，跨平台移动端
- **IoT 节点**: 树莓派/ESP32，传感器数据采集、设备控制

**技术栈**:
- Desktop: Tauri v2 + React
- Mobile: Flutter
- IoT: Python / MicroPython

**通信**: 通过 Dapr Pub/Sub 与 Tier 2 通信

---

## 关键目录说明

### `core/brain/ray_cluster/`
Ray 集群管理与调度
- `cluster_manager.py` - Ray 集群管理
- `task_scheduler.py` - 任务调度
- `resource_monitor.py` - 资源监控
- `worker_pool.py` - Worker 池管理

### `core/runtime/`
技能运行沙箱
- `skill_loader.py` - 技能加载器
- `skill_runner.py` - 技能运行器（Docker/Wasm）
- `skill_registry.py` - 技能注册表
- `sandbox.py` - 沙箱环境

### `core/web_ui/`
本地管理后台（React Build）
- 设备管理界面
- 技能管理界面
- 集群监控界面

### `skills_repo/`
已安装技能存储库
- 每个技能一个子目录
- 包含 `manifest.yaml` 和技能代码

---

## 开发工作流

1. **后端开发**: 在 `core/` 目录下进行
2. **客户端开发**: 在 `clients/` 对应子目录下进行
3. **技能开发**: 在 `skills_repo/` 目录下创建新技能
4. **Wasm 插件开发**: 使用 `jachin-plugin-sdk/` 脚手架，`make build` 生成 `dist/main.wasm`，控制台上传
5. **云端开发**: 在 `cloud/` 目录下进行（可选）
6. **文档更新**: 在 `docs/` 目录下维护

---

## 配置文件说明

- `.cursor/rules/`: Cursor IDE 规则，指导 AI 助手理解项目架构
- `docker-compose.yml`: 本地开发环境服务编排（Redis, Qdrant, PostgreSQL）
- `.env.example`: 环境变量模板，复制为 `.env` 后配置实际值
- `dapr/`: Dapr 组件和配置

---

## 下一步

1. 配置环境变量（复制 `.env.example` 为 `.env`）
2. 启动基础设施：`docker-compose up -d`
3. 启动 Tier 2 核心服务：`python core/main.py`
4. 启动 Tier 3 客户端（如桌面精灵）
5. 参考 `docs/architecture.md` 了解完整架构
