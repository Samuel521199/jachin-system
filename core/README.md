# Core (Tier 2 - Jachin Hive)

服务端核心代码，负责复杂推理、长期记忆、任务编排和模型调度。

## 目录结构

```
core/
├── agent_memory.py   # Agent 持久化记忆（add_memory, get_context），供 ReAct 循环
├── agent_loop.py     # ReAct 代理循环（Thought→Action→Observation），蓝图 Persona & Skillset
├── daemon.py         # 轻量版守护进程（心跳 + Agent Loop）
├── wasm_runner.py    # WASM 物理沙箱（Pure Compute + WASI stdin/stdout）
├── core/              # 核心模块
│   ├── brain/llm/    # 模型抽象层 (MAL)
│   ├── memory/       # 向量记忆、RAG（与 agent_memory 区分：后者为 Agent 对话上下文）
│   ├── planner/      # 任务编排
│   └── auth/         # 认证授权
├── services/         # 业务服务
│   ├── inference/    # 推理服务
│   ├── chat/         # 对话服务
│   └── device/       # 设备管理服务
├── api/              # API 路由
│   ├── routes/       # 路由定义
│   └── middleware/   # 中间件
├── models/           # 数据模型
├── utils/            # 工具函数
└── main.py           # 应用入口
```

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 运行开发服务器

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### 使用 Docker

```bash
docker-compose up backend
```

## 环境变量

参考根目录下的 `.env.example` 文件。

## 开发规范

- 所有模型调用必须通过 MAL 层（见 `.cursor/rules/010-model-layer.mdc`）
- API 路由遵循 RESTful 规范
- 使用类型提示（Type Hints）
- 编写单元测试
