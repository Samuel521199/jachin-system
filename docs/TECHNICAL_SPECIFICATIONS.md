# Jachin-System v3.2 技术规范文档

## 文档信息

- **版本**: v3.2
- **创建日期**: 2026-02-03
- **状态**: 编码前技术规范
- **目的**: 提供完整的技术规范，确保编码前所有设计已完成

---

## Nexus (Layer 1) Schema 补充

Jachin Nexus 使用 Supabase，Schema 见 `cloud/nexus/supabase/migrations/`：

- **edge_agents**：边缘智能体（pairing_code, auth_token, last_heartbeat, current_blueprint_id, **im_binding_id**, **im_platform**）
- **blueprints**：蓝图资产（name, ast_json）
- **agent_message_queue**：IM 消息队列（agent_id, message_text, direction, status）

详见 [IM_GATEWAY_SPEC.md](./IM_GATEWAY_SPEC.md)、[LAYER1_ARCHITECTURE_AND_DESIGN.md](./LAYER1_ARCHITECTURE_AND_DESIGN.md)。

---

## 一、数据库 Schema 设计

### 1.1 PostgreSQL 表结构

#### 用户表 (users)

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(255) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE,
    password_hash VARCHAR(255),
    role VARCHAR(50) DEFAULT 'user',  -- admin, user
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_users_username ON users(username);
CREATE INDEX idx_users_email ON users(email);
```

#### 技能表 (skills)

```sql
CREATE TABLE skills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    skill_id VARCHAR(255) UNIQUE NOT NULL,  -- 技能唯一标识
    name VARCHAR(255) NOT NULL,
    version VARCHAR(50) NOT NULL,
    description TEXT,
    author VARCHAR(255),
    license VARCHAR(50),
    runtime VARCHAR(50) NOT NULL,  -- docker, wasm, native
    entrypoint VARCHAR(255),
    manifest_path TEXT NOT NULL,
    install_path TEXT NOT NULL,  -- skills_repo/{skill_id}/
    status VARCHAR(50) DEFAULT 'installed',  -- installed, active, disabled, error
    installed_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    last_used_at TIMESTAMP,
    usage_count INTEGER DEFAULT 0
);

CREATE INDEX idx_skills_skill_id ON skills(skill_id);
CREATE INDEX idx_skills_status ON skills(status);
```

#### 技能能力映射表 (skill_capabilities)

```sql
CREATE TABLE skill_capabilities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    skill_id UUID REFERENCES skills(id) ON DELETE CASCADE,
    capability_name VARCHAR(255) NOT NULL,
    capability_type VARCHAR(50) NOT NULL,  -- action, sensor, processor
    description TEXT,
    input_schema JSONB,  -- JSON Schema
    output_schema JSONB,  -- JSON Schema
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(skill_id, capability_name)
);

CREATE INDEX idx_skill_capabilities_skill_id ON skill_capabilities(skill_id);
CREATE INDEX idx_skill_capabilities_name ON skill_capabilities(capability_name);
CREATE INDEX idx_skill_capabilities_type ON skill_capabilities(capability_type);
```

#### 记忆表 (memories)

```sql
CREATE TABLE memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    content_type VARCHAR(50) NOT NULL,  -- text, image, audio, video, file
    vector_id VARCHAR(255),  -- Qdrant collection ID
    collection_name VARCHAR(255),  -- Qdrant collection name
    permission_level VARCHAR(50) DEFAULT 'private',  -- private, shared, public
    metadata JSONB,  -- 额外元数据
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_memories_user_id ON memories(user_id);
CREATE INDEX idx_memories_permission ON memories(permission_level);
CREATE INDEX idx_memories_created_at ON memories(created_at);
```

#### 记忆权限表 (memory_permissions)

```sql
CREATE TABLE memory_permissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    memory_id UUID REFERENCES memories(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    permission_type VARCHAR(50) NOT NULL,  -- read, write, delete
    granted_at TIMESTAMP DEFAULT NOW(),
    granted_by UUID REFERENCES users(id),
    UNIQUE(memory_id, user_id, permission_type)
);

CREATE INDEX idx_memory_permissions_memory_id ON memory_permissions(memory_id);
CREATE INDEX idx_memory_permissions_user_id ON memory_permissions(user_id);
```

#### 任务表 (tasks)

```sql
CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id VARCHAR(255) UNIQUE NOT NULL,  -- Ray task ID
    user_id UUID REFERENCES users(id),
    task_type VARCHAR(50) NOT NULL,  -- llm_inference, skill_execution, video_processing, etc.
    status VARCHAR(50) DEFAULT 'pending',  -- pending, running, completed, failed, cancelled
    skill_id UUID REFERENCES skills(id),
    capability_name VARCHAR(255),
    input_data JSONB,
    output_data JSONB,
    error_message TEXT,
    worker_node VARCHAR(255),  -- Ray worker node ID
    priority INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    duration_ms INTEGER  -- 执行时长（毫秒）
);

CREATE INDEX idx_tasks_task_id ON tasks(task_id);
CREATE INDEX idx_tasks_user_id ON tasks(user_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_created_at ON tasks(created_at);
```

#### 集群节点表 (cluster_nodes)

```sql
CREATE TABLE cluster_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id VARCHAR(255) UNIQUE NOT NULL,  -- 节点唯一标识
    node_type VARCHAR(50) NOT NULL,  -- master, worker
    host VARCHAR(255) NOT NULL,
    port INTEGER NOT NULL,
    ray_port INTEGER,  -- Ray端口
    dapr_port INTEGER,  -- Dapr端口
    has_gpu BOOLEAN DEFAULT FALSE,
    gpu_count INTEGER DEFAULT 0,
    gpu_memory_gb INTEGER,
    cpu_count INTEGER DEFAULT 0,
    memory_gb INTEGER,
    disk_gb INTEGER,
    status VARCHAR(50) DEFAULT 'offline',  -- online, offline, maintenance, error
    last_heartbeat TIMESTAMP,
    registered_at TIMESTAMP DEFAULT NOW(),
    metadata JSONB  -- 额外信息
);

CREATE INDEX idx_cluster_nodes_node_id ON cluster_nodes(node_id);
CREATE INDEX idx_cluster_nodes_status ON cluster_nodes(status);
CREATE INDEX idx_cluster_nodes_type ON cluster_nodes(node_type);
```

### 1.2 SQLAlchemy Models

**需要创建的文件：**
```
core/memory/schema/
  ├── __init__.py
  ├── models.py          # SQLAlchemy models
  ├── database.py        # 数据库连接和会话管理
  └── migrations/        # Alembic migrations
      ├── env.py
      └── versions/
          └── 001_initial_schema.py
```

---

## 二、技能系统详细设计

### 2.1 Skill Manifest Schema (JSON Schema)

**需要创建的文件：**
```
core/runtime/schemas/
  └── manifest_schema.json
```

**Schema定义：**

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["name", "version", "runtime", "capabilities"],
  "properties": {
    "name": {
      "type": "string",
      "minLength": 1,
      "maxLength": 255
    },
    "version": {
      "type": "string",
      "pattern": "^\\d+\\.\\d+\\.\\d+$"
    },
    "description": {
      "type": "string"
    },
    "author": {
      "type": "string"
    },
    "license": {
      "type": "string",
      "enum": ["MIT", "Apache-2.0", "GPL-3.0", "proprietary"]
    },
    "runtime": {
      "type": "object",
      "required": ["type"],
      "properties": {
        "type": {
          "type": "string",
          "enum": ["docker", "wasm", "native"]
        },
        "image": {
          "type": "string"
        },
        "entrypoint": {
          "type": "string"
        },
        "requirements": {
          "type": "array",
          "items": {
            "type": "string"
          }
        }
      }
    },
    "capabilities": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["name", "type"],
        "properties": {
          "name": {
            "type": "string"
          },
          "type": {
            "type": "string",
            "enum": ["action", "sensor", "processor"]
          },
          "description": {
            "type": "string"
          },
          "input_schema": {
            "type": "object"
          },
          "output_schema": {
            "type": "object"
          }
        }
      }
    },
    "resources": {
      "type": "object",
      "properties": {
        "cpu": {
          "type": "integer",
          "minimum": 1
        },
        "memory": {
          "type": "string"
        },
        "gpu": {
          "type": "boolean"
        },
        "disk": {
          "type": "string"
        }
      }
    },
    "permissions": {
      "type": "array",
      "items": {
        "type": "string"
      }
    },
    "lifecycle": {
      "type": "object",
      "properties": {
        "install": {
          "type": "string"
        },
        "uninstall": {
          "type": "string"
        },
        "health_check": {
          "type": "string"
        }
      }
    }
  }
}
```

### 2.2 技能运行时接口

**需要创建的文件：**
```
core/runtime/
  ├── __init__.py
  ├── interfaces.py       # 接口定义
  ├── manifest.py        # Manifest解析和验证
  ├── skill_loader.py    # 技能加载器
  ├── skill_runner.py    # 技能运行器
  ├── skill_registry.py  # 技能注册表
  └── sandbox/
      ├── __init__.py
      ├── base.py        # 沙箱基类
      ├── docker_sandbox.py
      └── wasm_sandbox.py
```

---

## 三、Ray 集成详细设计

### 3.1 Ray 任务类型定义

**需要创建的文件：**
```
core/brain/ray_cluster/
  ├── __init__.py
  ├── task_types.py      # 任务类型定义
  ├── cluster_manager.py # 集群管理
  ├── task_scheduler.py # 任务调度
  ├── resource_monitor.py # 资源监控
  ├── worker_pool.py    # Worker池管理
  └── decorators.py     # Ray装饰器封装
```

### 3.2 Ray 远程函数封装

**LLM推理任务：**

```python
# core/brain/ray_cluster/tasks.py
import ray

@ray.remote(num_gpus=0, num_cpus=1)
async def llm_inference_task(
    provider: str,
    model: str,
    messages: List[Dict],
    temperature: float = 0.7,
    max_tokens: int = 2000
) -> Dict[str, Any]:
    """LLM推理任务"""
    from core.llm.factory import LLMProviderFactory
    
    factory = LLMProviderFactory()
    llm_provider = factory.create_provider(provider)
    
    result = await llm_provider.chat(
        messages=messages,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens
    )
    
    return {
        "text": result.text,
        "usage": result.usage,
        "model": result.model
    }

@ray.remote(num_gpus=1, num_cpus=2)
async def skill_execution_task(
    skill_id: str,
    capability_name: str,
    input_data: Dict[str, Any]
) -> Dict[str, Any]:
    """技能执行任务"""
    from core.runtime.skill_runner import SkillRunner
    
    runner = SkillRunner()
    result = await runner.execute_capability(
        skill_id=skill_id,
        capability_name=capability_name,
        input_data=input_data
    )
    
    return result
```

---

## 四、API 接口详细设计

### 4.1 技能管理 API

**需要创建的文件：**
```
core/api/skills.py
```

**端点设计：**

```python
from fastapi import APIRouter, UploadFile, File, HTTPException
from typing import List, Optional
from pydantic import BaseModel

router = APIRouter(prefix="/api/v3/skills", tags=["skills"])

class SkillInfo(BaseModel):
    skill_id: str
    name: str
    version: str
    description: Optional[str]
    status: str
    capabilities: List[Dict]

class SkillExecutionRequest(BaseModel):
    capability_name: str
    input_data: Dict[str, Any]

@router.post("", status_code=201)
async def install_skill(
    skill_file: UploadFile = File(...),
    overwrite: bool = False
):
    """
    安装技能
    - 接收技能zip包
    - 解压到skills_repo/
    - 验证manifest.yaml
    - 注册到数据库
    """
    pass

@router.get("", response_model=List[SkillInfo])
async def list_skills(
    status: Optional[str] = None,
    capability_type: Optional[str] = None
):
    """列出所有已安装技能"""
    pass

@router.get("/{skill_id}", response_model=SkillInfo)
async def get_skill(skill_id: str):
    """获取技能详情"""
    pass

@router.post("/{skill_id}/execute")
async def execute_skill(
    skill_id: str,
    request: SkillExecutionRequest
):
    """执行技能能力"""
    pass

@router.delete("/{skill_id}")
async def uninstall_skill(skill_id: str):
    """卸载技能"""
    pass

@router.post("/{skill_id}/enable")
async def enable_skill(skill_id: str):
    """启用技能"""
    pass

@router.post("/{skill_id}/disable")
async def disable_skill(skill_id: str):
    """禁用技能"""
    pass
```

### 4.2 集群管理 API

**需要创建的文件：**
```
core/api/cluster.py
```

**端点设计：**

```python
router = APIRouter(prefix="/api/v3/cluster", tags=["cluster"])

@router.get("/nodes")
async def list_nodes():
    """列出所有集群节点"""
    pass

@router.get("/nodes/{node_id}")
async def get_node(node_id: str):
    """获取节点详情"""
    pass

@router.get("/tasks")
async def list_tasks(
    status: Optional[str] = None,
    skill_id: Optional[str] = None,
    limit: int = 100
):
    """列出所有任务"""
    pass

@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """获取任务详情"""
    pass

@router.post("/tasks")
async def submit_task(task: RayTask):
    """提交任务到Ray集群"""
    pass

@router.delete("/tasks/{task_id}")
async def cancel_task(task_id: str):
    """取消任务"""
    pass

@router.get("/stats")
async def get_cluster_stats():
    """获取集群统计信息"""
    pass
```

---

## 五、Brain Orchestrator 详细设计

### 5.1 任务规划流程

**需要创建的文件：**
```
core/brain/planner/
  ├── __init__.py
  ├── task_planner.py
  ├── intent_parser.py
  └── resource_allocator.py
```

**流程设计：**

```python
# core/brain/planner/task_planner.py
class TaskPlanner:
    def __init__(
        self,
        intent_parser: IntentParser,
        resource_allocator: ResourceAllocator,
        skill_registry: SkillRegistry,
        device_registry: DeviceRegistry
    ):
        self.intent_parser = intent_parser
        self.resource_allocator = resource_allocator
        self.skill_registry = skill_registry
        self.device_registry = device_registry
    
    async def plan_task(self, user_input: str, user_id: str) -> List[RayTask]:
        """
        任务规划流程：
        1. 解析用户意图
        2. 识别需要的技能和能力
        3. 查询设备能力（如果需要）
        4. 生成任务计划
        5. 资源分配
        6. 返回任务列表
        """
        # Step 1: 解析意图
        intent = await self.intent_parser.parse_intent(user_input)
        
        # Step 2: 查找技能
        required_skills = []
        for capability in intent.required_capabilities:
            skill = await self.skill_registry.find_skill_by_capability(
                capability_name=capability.name,
                capability_type=capability.type
            )
            if skill:
                required_skills.append(skill)
        
        # Step 3: 查询设备（如果需要）
        if intent.requires_device:
            devices = await self.device_registry.find_devices_by_capability(
                capability_name=intent.device_capability
            )
        
        # Step 4: 生成任务
        tasks = []
        for skill in required_skills:
            task = RayTask(
                task_id=generate_task_id(),
                task_type=TaskType.SKILL_EXECUTION,
                skill_id=skill.skill_id,
                capability_name=intent.capability_name,
                input_data=intent.parameters,
                requires_gpu=skill.resources.get("gpu", False)
            )
            tasks.append(task)
        
        # Step 5: 资源分配
        for task in tasks:
            node_id = await self.resource_allocator.allocate_resources(task)
            task.worker_node = node_id
        
        return tasks
```

---

## 六、配置文件设计

### 6.1 集群配置

**需要创建的文件：**
```
config/cluster.yaml
```

**配置内容：**

```yaml
# 集群模式：single, cluster
mode: single

# Master节点配置
master:
  node_id: master-001
  host: localhost
  port: 8000
  dapr_port: 3500
  ray_port: 10001
  ray_dashboard_port: 8265

# Worker节点配置（仅在cluster模式下使用）
workers:
  - node_id: worker-001
    host: worker1.local
    port: 8001
    dapr_port: 3501
    ray_port: 10001
    gpu: true
    gpu_count: 1
    gpu_memory_gb: 8
    cpu_count: 8
    memory_gb: 16
  
  - node_id: worker-002
    host: worker2.local
    port: 8002
    dapr_port: 3502
    ray_port: 10001
    gpu: false
    cpu_count: 4
    memory_gb: 8

# 服务发现配置
discovery:
  method: mdns  # mdns, static, kubernetes
  service_name: jachin-hive
  domain: local
  port: 8000

# 技能系统配置
skills:
  repo_path: ./skills_repo
  runtime: docker  # docker, wasm
  sandbox_enabled: true
  auto_load: true
  health_check_interval: 60  # 秒
```

### 6.2 Ray 配置

**需要创建的文件：**
```
config/ray_config.yaml
```

**配置内容：**

```yaml
ray:
  # 集群模式
  mode: single  # single, cluster
  
  # Head节点配置
  head:
    host: localhost
    port: 10001
    dashboard_port: 8265
    num_cpus: 4
    num_gpus: 0
    object_store_memory: 2000000000  # 2GB
    redis_password: null
  
  # Worker节点配置
  workers:
    - node_id: worker-001
      host: worker1.local
      port: 10001
      num_cpus: 8
      num_gpus: 1
      gpu_memory: 8000000000  # 8GB
      object_store_memory: 4000000000  # 4GB
  
  # 任务调度配置
  scheduling:
    max_concurrent_tasks: 10
    task_timeout: 300  # 秒
    retry_policy:
      max_retries: 3
      retry_delay: 5  # 秒
      exponential_backoff: true
  
  # 资源分配策略
  resource_allocation:
    strategy: round_robin  # round_robin, least_loaded, gpu_first
    gpu_threshold: 0.8  # GPU使用率阈值
    cpu_threshold: 0.9  # CPU使用率阈值
  
  # 监控配置
  monitoring:
    enable_dashboard: true
    dashboard_port: 8265
    metrics_port: 8266
    log_level: INFO
```

---

## 七、启动流程详细设计

### 7.1 主入口重构

**需要修改的文件：**
```
core/main.py
```

**启动流程：**

```python
# core/main.py
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from core.config.settings import Settings
from core.memory.schema.database import init_database, close_database
from core.brain.ray_cluster.cluster_manager import RayClusterManager
from core.runtime.skill_registry import SkillRegistry
from core.registry import DeviceRegistry
from core.memory import MemorySystem

settings = Settings()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时执行
    logger.info("Starting Jachin-System v3.2...")
    
    # 1. 加载配置
    logger.info("Loading configuration...")
    cluster_config = load_cluster_config()
    ray_config = load_ray_config()
    
    # 2. 初始化数据库
    logger.info("Initializing database...")
    await init_database()
    
    # 3. 初始化Ray集群（如果是Cluster Mode）
    ray_manager = None
    if cluster_config.mode == "cluster":
        logger.info("Initializing Ray cluster...")
        ray_manager = RayClusterManager(ray_config)
        await ray_manager.initialize()
        app.state.ray_manager = ray_manager
    
    # 4. 初始化技能系统
    logger.info("Initializing skill system...")
    skill_registry = SkillRegistry()
    await skill_registry.load_all_skills()
    app.state.skill_registry = skill_registry
    
    # 5. 初始化设备注册表
    logger.info("Initializing device registry...")
    device_registry = DeviceRegistry()
    app.state.device_registry = device_registry
    
    # 6. 初始化记忆系统
    logger.info("Initializing memory system...")
    memory_system = MemorySystem()
    await memory_system.initialize()
    app.state.memory_system = memory_system
    
    # 7. 初始化Brain Orchestrator
    logger.info("Initializing Brain Orchestrator...")
    from core.brain.planner.task_planner import TaskPlanner
    task_planner = TaskPlanner(
        intent_parser=IntentParser(),
        resource_allocator=ResourceAllocator(ray_manager),
        skill_registry=skill_registry,
        device_registry=device_registry
    )
    app.state.task_planner = task_planner
    
    logger.info("Jachin-System v3.2 started successfully!")
    
    yield
    
    # 关闭时执行
    logger.info("Shutting down Jachin-System v3.2...")
    
    # 1. 关闭Ray集群
    if ray_manager:
        await ray_manager.shutdown()
    
    # 2. 关闭技能运行时
    await skill_registry.shutdown()
    
    # 3. 关闭数据库连接
    await close_database()
    
    logger.info("Jachin-System v3.2 shutdown complete")

app = FastAPI(
    title="Jachin-System v3.2",
    version="3.2.0",
    lifespan=lifespan
)
```

---

## 八、依赖更新

### 8.1 requirements.txt 更新

**需要添加的依赖：**

```txt
# Ray 分布式计算
ray[default]>=2.8.0

# PostgreSQL 客户端（已存在，确认版本）
psycopg2-binary>=2.9.0
sqlalchemy>=2.0.0
alembic>=1.12.1
asyncpg>=0.29.0

# Docker 客户端（技能运行时）
docker>=6.1.0

# Wasm 运行时（core/wasm_runner.py：Pure Compute + WASI stdin/stdout）
wasmtime>=15.0.0

# 配置管理
pyyaml>=6.0.1

# 服务发现
zeroconf>=0.131.0  # mDNS/Zeroconf

# JSON Schema验证
jsonschema>=4.20.0
```

### 8.2 docker-compose.yml 更新

**需要添加的服务：**

```yaml
services:
  # ... 现有服务 ...
  
  # Ray Head（可选，如果使用Docker部署）
  ray-head:
    image: rayproject/ray:latest
    container_name: jachin-ray-head
    command: ray start --head --port=10001 --dashboard-port=8265
    ports:
      - "10001:10001"
      - "8265:8265"  # Ray Dashboard
    volumes:
      - ray_data:/tmp/ray
    networks:
      - jachin-network

volumes:
  # ... 现有volumes ...
  ray_data:
```

---

## 九、目录结构创建

### 9.1 需要创建的新目录

```
core/
  ├── brain/
  │   ├── ray_cluster/     # [NEW]
  │   │   ├── __init__.py
  │   │   ├── cluster_manager.py
  │   │   ├── task_scheduler.py
  │   │   ├── task_types.py
  │   │   ├── resource_monitor.py
  │   │   ├── worker_pool.py
  │   │   └── decorators.py
  │   └── planner/         # [NEW]
  │       ├── __init__.py
  │       ├── task_planner.py
  │       ├── intent_parser.py
  │       └── resource_allocator.py
  ├── runtime/             # [NEW]
  │   ├── __init__.py
  │   ├── interfaces.py
  │   ├── manifest.py
  │   ├── skill_loader.py
  │   ├── skill_runner.py
  │   ├── skill_registry.py
  │   ├── sandbox/
  │   │   ├── __init__.py
  │   │   ├── base.py
  │   │   ├── docker_sandbox.py
  │   │   └── wasm_sandbox.py
  │   └── schemas/
  │       └── manifest_schema.json
  ├── memory/
  │   ├── schema/          # [NEW]
  │   │   ├── __init__.py
  │   │   ├── models.py
  │   │   ├── database.py
  │   │   └── migrations/
  │   ├── relational_store.py  # [NEW]
  │   ├── permission_manager.py  # [NEW]
  │   └── federated_memory.py    # [NEW]
  └── api/
      ├── skills.py        # [NEW]
      ├── cluster.py       # [NEW]
      └── memory.py        # [NEW] 扩展

config/                    # [NEW]
  ├── cluster.yaml
  ├── ray_config.yaml
  └── skills_config.yaml

skills_repo/               # [NEW]
  ├── .gitkeep
  └── README.md

installer/                 # [NEW]
  ├── install.sh
  ├── install.ps1
  ├── cluster_setup.sh
  ├── init_database.sh
  └── validate_setup.sh
```

---

## 十、编码前检查清单

### ✅ 设计文档
- [x] 架构设计完成
- [x] 差距分析完成
- [x] **数据库Schema设计** ✅
- [x] **技能Manifest Schema设计** ✅
- [x] **Ray任务接口设计** ✅
- [x] **API接口设计** ✅
- [x] **Brain Orchestrator设计** ✅

### ✅ 配置文件
- [x] **Ray配置文件** ✅
- [x] **集群配置文件** ✅
- [x] **技能系统配置** ✅

### ⏳ 依赖管理
- [ ] **requirements.txt更新** ⚠️ 需要添加Ray等依赖
- [ ] **docker-compose.yml更新** ⚠️ 需要添加PostgreSQL和Ray服务

### ⏳ 启动流程
- [x] **主入口重构设计** ✅
- [x] **初始化流程设计** ✅

### ⏳ 目录结构
- [ ] **创建新目录** ⚠️ 需要创建所有新目录

---

## 🎯 立即行动清单

### 编码前必须完成（本周）

1. **更新依赖文件**
   - [ ] 更新 `requirements.txt` 添加Ray、Docker等依赖
   - [ ] 更新 `docker-compose.yml` 添加PostgreSQL和Ray服务

2. **创建目录结构**
   - [ ] 创建所有新目录（见第九节）

3. **创建配置文件模板**
   - [ ] `config/cluster.yaml`
   - [ ] `config/ray_config.yaml`
   - [ ] `config/skills_config.yaml`

4. **数据库初始化**
   - [ ] 创建SQLAlchemy models
   - [ ] 创建Alembic迁移脚本
   - [ ] 创建数据库初始化脚本

### 第一周编码任务

5. **Ray基础集成**
   - [ ] Ray集群管理器
   - [ ] 基础任务调度
   - [ ] 资源监控

6. **技能系统基础**
   - [ ] Manifest解析器
   - [ ] 技能加载器
   - [ ] Docker沙箱实现

7. **Brain Orchestrator基础**
   - [ ] 意图解析器
   - [ ] 任务规划器
   - [ ] 资源分配器

---

**文档版本**: v3.2.0  
**最后更新**: 2026-02-03  
**状态**: ✅ 技术规范完成，可以开始编码
