# 控制台 HUD 设计愿景 (Console HUD Design Vision)

## 文档信息

- **版本**: v1.0
- **创建日期**: 2026-02-11
- **状态**: 设计文档（非实现规范）
- **目的**: 从「UI」演进到「HUD」的界面哲学与模块级设计，供后续迭代参考

---

## 一、设计哲学：从 "UI" 到 "HUD" (Head-Up Display)

在 AI 时代，界面不应是死板的表格和按钮，而应是**动态的数据流和思维的可视化**。

### 核心变革点

| 维度 | 传统 UI | HUD 目标 |
|------|---------|----------|
| **透视与层级 (Depth)** | 平铺、无景深 | 前后景深：背景是流动的神经网络，前景是悬浮的玻璃面板 |
| **过程可视化 (Process Visibility)** | 只展示结果 | 展示 AI 如何思考、调度算力、调用技能，建立信任感 |
| **主动性 (Proactivity)** | 等待用户点击 | 控制台主动展示「我正在为你做什么」 |

---

## 二、Main 界面框架：指挥官甲板 (The Commander's Deck)

当前控制台侧边栏偏传统，目标改为更沉浸的布局。

### 2.1 背景 (The Void)

- **不再是**：简单深色背景。
- **目标**：
  - 使用 **Canvas 或 WebGL** 绘制低透明度、缓慢旋转的**星空拓扑图**。
  - 每个节点代表一个**记忆点**或**已连接设备**，象征 Jachin 的「潜意识」。
  - 背景层始终存在，与前景玻璃面板形成景深。

### 2.2 导航栏 (The Bridge)

- **形态**：将侧边栏改为**悬浮玻璃侧栏**。
- **交互**：
  - 图标具备**激活态光晕**效果。
  - 底部增加 **「系统心跳」(System Heartbeat)** 脉冲条，随 Tier 2 (Core) 的 CPU/GPU 负载跳动。

### 2.3 顶部状态栏 (The Horizon)

- **环境**：显示当前环境，如 `Home Network (Secure)`。
- **算力池**：显示算力池状态，如 `Ray Cluster: 2 Nodes Active (1 GPU)`。
- **模型**：显示当前大脑模型，如 `Brain: Qwen-72B (Int4) - Quantized`。

---

## 三、Dashboard 重构：战情室 (Situation Room)

当前 Dashboard 仅有简单 CPU/RAM 与快捷操作，需向「智能战情室」演进。

### 3.1 实时思维流 (Live Thought Stream) —— 核心改动

- **位置**：占据屏幕**中央最显眼**区域。
- **内容**：
  - 实时滚动显示 **Tier 2 日志**，经**自然语言化**处理。
  - 示例：
    - 「正在后台索引『项目文档.pdf』…」
    - 「检测到 CPU 温度升高，已自动降低后台任务优先级。」
    - 「鹰眼系统注意到你离开了座位，已暂停音乐。」
- **视觉**：类似终端代码流滚动，配色柔和，关键信息高亮。
- **意义**：让用户感知「系统正在做什么」，建立信任与可控感。

### 3.2 算力拓扑 (Compute Topology)

- **内容**：可视化 **Ray 集群**。
- **视觉**：
  - 中央为**主节点 (Master)**，周围环绕 **Worker** 节点。
  - 正在处理任务的节点（如视频分析）**发光**，并有连线指向主节点。
- **意义**：一眼看到算力分布与负载状态。

### 3.3 待办与建议 (Agenda & Suggestions)

- **内容**：AI 根据日历、习惯**主动生成**的建议。
- **形式**：**卡片流**。
- **示例**：
  - 「明天上午 9 点有会议，需要我帮你整理相关邮件吗？」[执行]
  - 「C 盘空间不足 10%，建议清理缓存。」[清理]
- **意义**：从「等用户点」变为「主动提议」。

---

## 四、Neural Nexus 重构：大脑扫描 (Brain Scan)

当前 Neural Nexus 仅展示在线/离线状态，目标为展示「智力资产」。

### 4.1 记忆星云 (Memory Nebula)

- **功能**：可视化 **Qdrant 向量数据库**。
- **视觉**：可交互的 **3D 点云球体**，每个点代表一条记忆。
- **交互**：
  - 鼠标悬停：显示记忆片段（如：「喜欢喝冰美式」）。
  - 支持**关键词搜索**，高亮相关记忆点。
  - **框选区域 → 右键 →「遗忘」(Forget)**。
- **意义**：记忆从「黑盒」变为可感知、可管理。

### 4.2 上下文窗口监视器 (Context Gauge)

- **功能**：显示当前对话**上下文占用 Token 量**。
- **视觉**：**环形进度条**，类似「能量槽」。
- **意义**：用户知道 AI 还能「记住」多少当前对话，何时会触发「遗忘」。

### 4.3 模型热切换 (Model Swap)

- **功能**：**卡片式**切换大脑模型。
- **展示示例**：
  - 「Qwen-14B」—— 速度快，适合聊天
  - 「DeepSeek-Coder」—— 编程专用
  - 「GPT-4o (Cloud)」—— 逻辑最强，但费钱
- **意义**：场景化选模型，而非配置项罗列。

---

## 五、Skill Matrix 重构：军械库 (The Armory)

当前技能为列表形式，目标为「插件即能力」的感知。

### 5.1 模块化卡片 (Modular Cards)

- **布局**：由列表改为**网格布局**的功能块。
- **内容**：每个卡片除名称外，具备**实时状态**。
  - 例：「系统管家」卡片上直接显示「已拦截 3 次操作」。
  - 例：「股票助手」卡片背景为**今日大盘走势图**。
- **意义**：技能即「装备」，状态即「战况」。

### 5.2 权限透视 (Permission X-Ray)

- **功能**：鼠标悬停某技能时，显示**光线**连接到其所用系统权限（摄像头、文件读写等）。
- **意义**：用户直观看到「这个天气插件在用麦克风？关掉！」。
- **目标**：透明化权限，减少不信任感。

### 5.3 技能组合视图 (Chain View)

- **功能**：展示**最近一次** AI 如何**组合多个技能**完成任务。
- **视觉**：链条式可视化。
  - 示例：`[语音识别] → [意图分析] → [调用: 文件搜索] → [调用: 邮件发送] → [任务完成]`。
- **意义**：过程可见，理解「AI 是怎么做到的」。

---

## 六、与现有架构的关系

- **Tier 2 (Core)**：提供日志流、算力状态、记忆/上下文、技能执行链等**数据与 API**。
- **Tier 3 (Desktop)**：控制台 (Main) 作为 HUD 载体，消费上述数据并呈现为「指挥官甲板 / 战情室 / 大脑扫描 / 军械库」。
- 本设计为**体验与信息架构**层面的愿景，具体接口与实现需在开发阶段与 `architecture.md`、`DESKTOP_CLIENT.md`、后端 API 对齐。

---

## 七、实现状态与组件索引

以下为当前控制台（`clients/desktop`）与设计条目的对应关系，便于开发与文档同步。

### 7.0 完成度概览

| 类别 | 数量 | 说明 |
|------|------|------|
| **已实现（含占位）** | **18 项** | 指挥官甲板 4、战情室 4、大脑扫描 3、军械库 5、Jachin Link 1、Persona 1；部分为占位/本地数据，待后端或 Qdrant 接入即可替换 |
| **待实现 / 待增强** | **8 项** | 见下表 7.2；多数依赖后端 API 或 Tier 2/原生（NVML） |
| **可选扩展** | 2 项 | Void 与设备·记忆数据绑定、Canvas/WebGL 星空；非必须 |

**依赖关系简要**：思维流全量日志、算力拓扑真实节点、建议卡片数据、模型热切换、技能链多步、权限数据 → **后端 API**；记忆高亮/遗忘 → **Qdrant/记忆 API**；GPU 监控与过热策略 → **Tier 2 或 Tauri + NVML**。

### 7.1 已实现

| 设计条目 | 实现位置 | 说明 |
|----------|----------|------|
| **指挥官甲板** 深空背景、网格动画、悬浮玻璃侧栏 | `ConsoleLayout.tsx`、`Sidebar.tsx`、`globals.css`（`.console-deep-space`、`.glass-panel`、`grid-drift`） | 背景与导航已按愿景落地 |
| **指挥官甲板** 背景星空/拓扑层 (The Void) | `VoidBackground.tsx`、`ConsoleLayout.tsx`、`globals.css`（`@keyframes void-float`） | `ConsoleLayout` 轮询 `getDevices()`（约 20s），将设备数传入 `nodeCount`（8～60）；无设备或未就绪时默认 36 节点 |
| **指挥官甲板** 顶部状态栏 (The Horizon) | `Horizon.tsx`、`ConsoleLayout.tsx` | 支持 `VITE_ENVIRONMENT`、`VITE_MODEL_NAME` 环境变量（构建时注入），算力池轮询 cluster/stats |
| **系统心跳** 底部脉冲条 | `SystemHeartbeat.tsx`、Sidebar 底部 | 由 Tauri `get_system_stats` 的 CPU 驱动 |
| **战情室** 思维流 | `MindStream.tsx`、`Dashboard.tsx` | 打字机效果、Demo 轮播；底部 **liveStatsLines** 由 Dashboard 轮询 `GET /api/v3/cluster/stats` 注入集群实时统计（节点数、任务运行/待处理）；完整 Tier 2 日志流仍待后端接口 |
| **战情室** 算力拓扑 | `ComputeTopology.tsx`、`Dashboard.tsx` | Worker 数量由 `GET /api/v3/cluster/stats` 的 `nodes.total - 1` 驱动，有运行中任务时高亮 Worker0；CPU/RAM 与 fallback 活跃态由本地 `get_system_stats` 驱动 |
| **战情室** 快捷操作与建议卡片 | `Dashboard.tsx`（Quick Actions、ProactiveSuggestions） | 可选 `suggestions` 与 `onSuggestionAction(id, action)`，不传则用占位数据；后端可注入建议并处理点击 |
| **大脑扫描** 记忆星云 | `MemoryVisualizer.tsx`、`NeuralNexus.tsx` | 3D 粒子环 + 受控搜索框；NeuralNexus 传入 `onSearch`，点击搜索显示「记忆搜索将随 Qdrant 接口开放」提示，接入 Qdrant 后替换为真实请求即可 |
| **大脑扫描** 上下文窗口监视器 | `ModelController.tsx`、`NeuralNexus.tsx` | 环形进度条（Token 数），当前为占位，可接 LLM 状态 API |
| **大脑扫描** 当前模型卡片 | `ModelController.tsx` | 展示当前模型名与副标题；底部「切换模型 (即将开放)」占位按钮，待后端 API |
| **军械库** 网格布局 | `SkillMatrix.tsx`（CSS Grid 响应式 1~4 列） | 已实现 |
| **军械库** 模块化卡片 / 实时状态 | `LiveTile.tsx` | 名称、版本、最近一次执行结果/状态（已执行/错误摘要） |
| **军械库** 权限透视 (Permission X-Ray) | `LiveTile.tsx` 悬停 | 可选 `permissions: { id, label }[]`，后端下发则替代 mock；支持 file/network/system/sandbox 等 id 映射图标 |
| **军械库** 技能详情与逐项执行 | `SkillDetailModal.tsx` | 点击磁贴打开：描述、能力列表、执行、上次结果 |
| **军械库** 技能组合视图 (Chain View) | `SkillChainView.tsx`、`SkillMatrix.tsx` | 横向链条展示；自然语言执行后显示「用户输入 → 编排: 「…」→ 完成/失败」；后端可返回 `metadata.chain` 以展示多技能链 |
| **Jachin Link** 网络拓扑与设备列表 | `JachinLink.tsx` | 与战情室/大脑扫描/军械库一致的 HUD 布局；设备拓扑条（节点连线占位）；设备卡片网格，数据来自 `getDevices()`，约 15s 轮询 |
| **Persona** 形象与声音设置 | `Persona.tsx` | HUD 布局与 glass-panel；语音 (TTS) 列表来自 `listVoices`，精灵形象与主题为占位说明 |

### 7.2 待实现或占位

| 设计条目 | 说明 |
|----------|------|
| 背景星空拓扑图与数据绑定 | 当前为 VoidBackground 轻量节点漂移（CSS）；Canvas/WebGL 或节点数/位置与设备·记忆数据绑定可后续扩展 |
| 思维流完整日志流与自然语言化 | 当前已接入集群统计；全量 Tier 2 日志流与自然语言化待后端支持 |
| Compute Topology 节点详情与任务分布 | 当前 Worker 数量与「有任务时高亮」已由 cluster/stats 驱动；节点详情、任务与 Worker 的映射可由 Ray API 扩展 |
| 记忆星云：关键词高亮、框选遗忘 | 需 Qdrant/记忆 API |
| 模型热切换 (Model Swap) | 需后端模型列表与切换 API |
| 技能链多步明细由后端提供 | 前端已支持：成功返回后若存在 `metadata.chain` 数组则解析并展示多步链，否则展示 3 步简化链 |
| 权限数据从后端下发 | LiveTile 已支持可选 `permissions` 数组，后端在技能元数据中提供即可覆盖 mock |

### 7.3 控制台关键文件路径

```
clients/desktop/src/
├── console/
│   ├── ConsoleLayout.tsx      # 主布局、深空背景、Horizon
│   ├── Sidebar.tsx            # 玻璃侧栏、系统心跳
│   ├── components/
│   │   ├── Horizon.tsx        # 顶部状态栏（环境/算力/模型）
│   │   ├── VoidBackground.tsx  # 背景星空/拓扑节点层
│   │   ├── MindStream.tsx     # 思维流
│   │   ├── MemoryVisualizer.tsx  # 记忆星云
│   │   ├── ModelController.tsx   # 当前模型 + 上下文环
│   │   ├── ComputeTopology.tsx   # 算力拓扑 SVG（Master/Worker + 连线与数据流动画）
│   │   ├── LiveTile.tsx       # 技能磁贴 + 权限悬停
│   │   ├── SkillChainView.tsx   # 技能组合链条视图（最近执行链）
│   │   ├── SkillDetailModal.tsx  # 技能详情弹层
│   │   └── SystemHeartbeat.tsx
│   └── pages/
│       ├── Dashboard.tsx      # 战情室
│       ├── NeuralNexus.tsx   # 大脑扫描
│       ├── SkillMatrix.tsx   # 军械库
│       ├── JachinLink.tsx    # 网络拓扑与设备列表
│       └── Persona.tsx      # 形象与声音设置
├── styles/globals.css        # 深空、玻璃、滚动条、网格动画
└── ...
```

视觉规范见 **`.cursor/rules/070-visual-aesthetic.mdc`**（主题色、ROG 式卡片、字体、实时数据要求）。

### 7.4 后端对接清单（待实现功能）

前端已预留接口或解析逻辑，后端按下列约定实现即可对接：

| 功能 | 预期接口或数据格式 | 前端接入点 |
|------|-------------------|------------|
| 思维流完整日志 | 推送或轮询：如 `GET /api/v3/logs/recent?limit=20` 返回 `{ lines: string[] }`，或 SSE/WebSocket | MindStream 可扩展为接受 `lines` 或订阅流 |
| 主动建议卡片 | 如 `GET /api/v3/suggestions` 返回 `{ items: { id, text, action, type? }[] }`；点击执行可调 `POST /api/v3/suggestions/{id}/execute` | Dashboard 传入 `suggestions` 与 `onSuggestionAction` |
| 环境与当前模型 | 已支持构建时注入：`VITE_ENVIRONMENT`、`VITE_MODEL_NAME`；也可由后端 `GET /api/v3/config` 等注入 | ConsoleLayout 从 import.meta.env 传入 Horizon |
| 记忆搜索 / 高亮 | 如 `GET /api/v3/memory/search?q=...` 或 Qdrant 封装；高亮/遗忘需对应 API | NeuralNexus 传入 MemoryVisualizer 的 `onSearch` |
| 模型列表与热切换 | `GET /api/v3/models`，`POST /api/v3/models/current` 或 body `{ model_id }` | ModelController 占位按钮接好后即可启用 |
| 技能权限 | 技能元数据中增加 `permissions: { id, label }[]`（如 file、network、system、sandbox） | LiveTile 的 `permissions` prop，由 SkillMatrix 从 listSkills 或详情传入 |
| 技能链多步 | 编排器返回 `metadata.chain: [ { id, label, type? } ]` | SkillMatrix 已解析并写入 SkillChainView |
| Void 节点数 | 已由设备数驱动：ConsoleLayout 轮询 getDevices() 并传 nodeCount；记忆数可后续叠加或替代 | 已接入 getDevices().length |

---

## 八、后续技术建议：原生 GPU 监控 (NVML)

在 Tier 2 或桌面客户端集成 **NVML (Nvidia Management Library)**，可实现：

- **感知过热**：当显卡温度超过阈值（如 85°C）时，桌面精灵自动变红并提示：「算力负载过高，正在自动分流任务到云端 API 以保护硬件。」同时可触发推理策略降级或云端回退。
- **感知算力**：当用户进行高负载任务（如渲染视频、大批量推理）时，Jachin 自动进入「增强模式」，调用 Ray 集群中所有空闲 Worker 加速处理。
- **Hive Dashboard**：在算力概览中展示 GPU 温度、利用率、显存占用，数据由 Tier 2 通过 NVML 采集并经由 API 提供给前端。

实现时需在 Tier 2 或 Tauri 侧封装 NVML 调用（或通过本地服务暴露），并与现有推理策略、Ray 调度联动。

---

## 九、文档修订记录

| 日期       | 版本 | 说明     |
|------------|------|----------|
| 2026-02-11 | v1.0 | 初版：设计哲学 + 四大模块设计 |
| 2026-02-11 | v1.1 | 增加 NVML 技术建议 |
| 2026-02-11 | v1.2 | 增加「实现状态与组件索引」：已实现/待实现、控制台文件路径、与 070 规则引用 |
| 2026-02-11 | v1.3 | 算力拓扑改为 ComputeTopology 组件（SVG 节点+连线+数据流动画），设计文档与文件树同步 |
| 2026-02-11 | v1.4 | 思维流接入集群实时统计：api getClusterStats、MindStream liveStatsLines、Dashboard 轮询 |
| 2026-02-11 | v1.5 | 军械库技能链视图：SkillChainView 组件 + 自然语言执行后 3 步链，设计文档与文件树同步 |
| 2026-02-11 | v1.6 | 指挥官甲板顶部状态栏 Horizon（环境/算力池/模型），算力由 cluster/stats 轮询 |
| 2026-02-11 | v1.7 | 模型热切换占位按钮、记忆星云搜索框受控 + Qdrant 接入提示文案 |
| 2026-02-11 | v1.8 | 背景星空/拓扑层 VoidBackground（轻量节点漂移），设计文档与待实现说明更新 |
| 2026-02-11 | v1.9 | Jachin Link 页 HUD 风格统一、设备拓扑条、glass-panel 与 7.1/文件树同步 |
| 2026-02-11 | v2.0 | Persona 页 HUD 风格统一（glass-panel、font-mono），7.1 与文件树同步 |
| 2026-02-11 | v2.1 | 预留接口：Dashboard suggestions/onSuggestionAction、Horizon environment/modelName、MemoryVisualizer onSearch、LiveTile permissions；SkillMatrix 解析 metadata.chain 多步链 |
| 2026-02-11 | v2.2 | 算力拓扑 Worker 数由 cluster/stats 驱动；VoidBackground 支持 nodeCount；7.4 后端对接清单 |
| 2026-02-11 | v2.3 | Void 与设备数绑定：ConsoleLayout 轮询 getDevices 并传 nodeCount 给 VoidBackground |
| 2026-02-11 | v2.4 | Horizon 支持 VITE_ENVIRONMENT / VITE_MODEL_NAME；NeuralNexus 记忆搜索占位反馈（Qdrant 提示） |
