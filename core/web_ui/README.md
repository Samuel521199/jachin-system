# Tier 2 Hive Dashboard (Web UI)

## 定位

Jachin 算力集群的本地 Web 管理后台，风格参考 **ROG Armoury Crate**：左侧图标菜单、顶部算力概览、中部网格卡片、底部运行模式。所有数据由 **Ray / Dapr / FastAPI** 上报，禁止静态死图。

## 布局规范

```
┌─────────────────────────────────────────────────────────────────┐
│ [图标] [图标] [图标]  │  顶部: CPU/GPU 综合概览（Ray 上报）        │
│ 左侧垂直              │  曲线或热力图，实时刷新                     │
│ 图标菜单              ├───────────────────────────────────────────┤
│                      │  卡片 A          卡片 B          卡片 C   │
│                      │  节点管理         技能市场预览    实时对话流 │
│                      │  Worker IP/算力   最新 AI Skill  缩略图   │
│                      ├───────────────────────────────────────────┤
│                      │  底部: 系统运行模式                         │
│                      │  [节能] [默认] [高性能] [上帝模式]           │
└─────────────────────────────────────────────────────────────────┘
```

### 区域说明

| 区域 | 内容 | 数据来源 |
|------|------|----------|
| 左侧 | 垂直图标菜单 | 静态导航 |
| 顶部 | CPU/GPU 综合概览图 | `GET /api/v3/cluster/stats`、Ray ResourceMonitor |
| 卡片 A | 节点管理：所有 Worker 的 IP、算力、状态 | `GET /api/v3/cluster/nodes` |
| 卡片 B | 技能市场预览：最新可用 AI Skill | `GET /api/v3/skills` 或市场 API |
| 卡片 C | 实时对话流缩略图 | 最近会话或 Mind Stream 摘要 |
| 底部 | 运行模式切换 | Inference Strategy API（节能/默认/高性能/上帝模式） |

## 视觉规范

遵循 `.cursor/rules/070-visual-aesthetic.mdc`：

- 背景 `#0D0D0D`，警示色 `#FF0032`，指令色 `#00F0FF`。
- 卡片：小圆角、1px 微光描边、可毛玻璃。
- 字体：数据/日志用 JetBrains Mono / Fira Code。
- 所有数值需可轮询或 WebSocket 更新，禁止写死。

## 当前实现

- `index.html`：单页静态 Hive Dashboard，通过 `fetch` 轮询 `/api/v3/cluster/nodes`、`/api/v3/cluster/stats`、`/api/v3/monitoring/stats` 等，用于本地开发与联调。
- 生产环境可替换为 React/Vue 构建产物，由 FastAPI 挂载同一路由（如 `/hive`）提供。

## 如何运行

1. 启动 Tier 2：`python core/main.py`（确保 Dapr 与 Ray 可用）。
2. 将 `core/web_ui/` 配置为静态目录或挂载到 FastAPI：
   ```python
   from fastapi.staticfiles import StaticFiles
   app.mount("/hive", StaticFiles(directory="core/web_ui", html=True), name="hive")
   ```
3. 浏览器访问 `http://localhost:8000/hive/`（端口以实际为准）。

## 后续扩展

- **NVML 集成**：Tier 2 调用 NVML 获取 GPU 温度/利用率，Dashboard 展示并在过热（如 >85°C）时触发告警或云端分流。
- **推理策略 API**：暴露 `POST /api/v3/inference/strategy`，切换「极致精准」/「敏捷反应」/「上帝模式」，与底部按钮联动。
