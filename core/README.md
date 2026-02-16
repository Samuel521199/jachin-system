# Core (Tier 2 - Jachin Hive)

服务端核心代码，负责复杂推理、长期记忆、任务编排和模型调度。

## 目录结构

```
core/
├── core/              # 核心模块
│   ├── llm/          # 模型抽象层 (MAL)
│   │   ├── base.py   # BaseLLMProvider 接口
│   │   ├── qwen_adapter.py
│   │   ├── local_adapter.py
│   │   ├── router.py # ModelRouter
│   │   └── factory.py
│   ├── memory/       # 记忆管理
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
