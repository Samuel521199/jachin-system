# Layer 1 (Jachin Nexus) 架构与界面设计总览

**版本**: 2.1  
**创建日期**: 2026-02-16  
**定位**: 灵界枢纽的架构、界面分类、风格与流程说明  
**升级**: 微内核生态深度融合；生态与商业化白皮书对齐，详见 [ECOSYSTEM_AND_COMMERCIALIZATION_WHITEPAPER.md](./ECOSYSTEM_AND_COMMERCIALIZATION_WHITEPAPER.md)

---

## 一、架构概览

### 1.1 核心哲学：剥离「指挥权」与「执行权」

| 层级 | 角色 | 职责 | 状态 |
|------|------|------|------|
| **Layer 1 (Jachin Nexus)** | 指挥部 & 兵工厂 | 协议制定 (JMP)、武器研发 (Forge)、武器分发 (Market)、舰队大盘 (Console)、智能版税路由 | **无状态**，无用户对话记忆 |
| **Layer 2 (Microkernel Node)** | 前线边缘智能体 | 加载 JMP 包、RAG 检索、多级权限管控、VAD 语音 I/O、私密对话上下文 | **有状态**，本地执行 |

### 1.1b 五阶层生态角色（白皮书对齐）

| 阶层 | 角色 | 对应界面 |
|------|------|----------|
| **阶层一** | 底层铸造者 (极客) | CLI、开发者文档、API 密钥管理台 |
| **阶层二** | 蓝图架构师 (魔法师) | The Forge、参数配置表 |
| **阶层三** | 布道者联盟 | 推广返佣仪表盘（待建） |
| **阶层四** | 企业领主 | Console 舰队指挥台、SaaS 账单 |
| **阶层五** | 普通用户 | 精简 App、消费端页面、「切换大脑」 |

### 1.2 定位与职责

| 维度 | 内容 |
|------|------|
| **定位** | 轻量化、协议化、去中心化的智慧分发枢纽 |
| **角色** | 公有云端，只提供插件与人设分发，**绝不碰隐私数据** |
| **核心职责** | 智慧分发、协议标准、神经元商城、鉴权中心 |

### 1.2 设计原则

| 原则 | 说明 |
|------|------|
| **轻量化 (Lightweight)** | 不存储用户数据（照片、聊天记录），只存储**代码、模型权重、配置清单**。像 GitHub，不像 iCloud。 |
| **协议至上 (Protocol First)** | 不是简单网站，而是 **JMP (Jachin Module Protocol)** 的标准制定者和分发者。 |
| **去中心化** | 拥抱 IPFS 等去中心化存储，智慧永不丢失、防篡改。 |
| **可视化** | 抛弃传统表格和列表，拥抱**「神经元网络」**形态的技能树。 |

### 1.3 技术栈

| 领域 | 技术选型 | 说明 |
|------|----------|------|
| **前端** | Next.js 14 + Framer Motion + Tailwind CSS + React Flow | 极速、动画、玻璃拟态 UI |
| **3D（规划）** | Three.js / React Three Fiber | 3D 神经元效果（当前用 SVG 模拟） |
| **后端 API** | Next.js Route Handlers | 极轻 Serverless |
| **数据库** | Supabase (PostgreSQL) | 可配置，未配置时用 mock |
| **存储（规划）** | IPFS | 去中心化，永不丢失、防篡改 |
| **身份（规划）** | Passkey / Web3 钱包 | 无密码登录，强调所有权 |

---

## 二、核心模块与界面分类

### 2.1 模块与页面映射

| 模块 | 设计理念 | 路由 | 实现状态 |
|------|----------|------|----------|
| **Landing Page** | 赛博朋克风格，全屏 3D 演示 Jachin 能力 | `/` | ✅ 已实现（紫色渐变 Hero） |
| **Neural Market（神经元商城）** | 技能树形态，非传统货架 | `/market` | ✅ 已实现（SVG 节点图） |
| **The Forge（铸造厂）** | Web 版「钢铁侠实验室」 | `/forge` | ✅ 已实现（React Flow 编排） |
| **Jachin ID & Console（指挥台）** | 极简「舰队管理」 | `/console` | ✅ 已实现（舰队拓扑 + 隐私审计） |
| **The Agora（广场）** | Agent 展示、悬赏大厅 (Bounty Board) | — | ❌ 待建 |
| **Auth Center** | Jachin ID 登录 | — | ❌ 待建（当前无登录） |

### 2.2 内容分类（Neural Market）

| 类型 | 英文 | 说明 | 颜色标识 |
|------|------|------|----------|
| **Skills（左脑能力）** | skill | Python 脚本、Docker 容器、API 连接器 | `#22d3ee` (cyan) |
| **Personas（右脑灵魂）** | persona | 语音包 (VITS)、Live2D/3D 模型、性格提示词 | `#f472b6` (pink) |
| **Memories（海马体）** | memory | 预训练向量知识库（如法律、医学） | `#a78bfa` (purple) |

### 2.3 核心模块设计详解

> **设计哲学**：抛弃列表，拥抱关系图谱；抛弃数据存储，拥抱隐私审计；抛弃单纯下载，拥抱在线铸造与模拟。

> **无感安全**：密码学对用户隐形。配对用 6 位码、权限用大白话、打包一键完成。详见 [INVISIBLE_SECURITY_UX.md](./INVISIBLE_SECURITY_UX.md)。

| 模块 | 设计理念 | 关键形态 |
|------|----------|----------|
| **Neural Market** | 技能树形态，非传统货架 | 3D 神经元网络球体，节点为 Skill/Persona，支持组合预览 |
| **The Forge** | Web 版「钢铁侠实验室」 | Agent Builder 拖拽编排、模拟沙箱在线测试、一键发布 |
| **The Agora** | Agent 与 Agent 社交展示台 | Showcase 视频、悬赏大厅 (Bounty Board) 去中心化撮合 |
| **Jachin ID & Console** | 极简舰队管理 | 地图显示 L2/L3 在线、绿色盾牌隐私审计「0 次上传」、6 位码添加边缘智能体 |

---

## 三、界面与视觉设计

### 3.1 整体风格

| 维度 | 设计 |
|------|------|
| **主题** | 深色赛博朋克 (Dark Cyberpunk) |
| **背景** | `#050505` 深黑 + 紫色径向渐变光晕 |
| **玻璃拟态** | `backdrop-blur-xl` + `bg-black/20~30` + `border-white/5~10` |
| **主色** | 紫 `#a78bfa` / `#6366f1`、青 `#22d3ee`、粉 `#f472b6` |
| **字体** | Inter，`tracking-widest` 用于标题 |
| **发光** | `animate-pulse-glow`、`drop-shadow`、`filter: url(#glow)` |

### 3.2 各页面风格

| 页面 | 视觉特征 |
|------|----------|
| **Landing** | 紫→蓝→青渐变标题、`animate-pulse-glow` CTA、径向渐变背景 |
| **Market** | 70% 左侧 SVG 神经元图（节点 + 虚线连接 + 呼吸动画），30% 右侧详情面板（毛玻璃） |
| **Forge** | 左侧节点组件库（拖拽区），右侧 React Flow 画布（点阵背景、紫色边线） |
| **Console** | 舰队拓扑卡片（绿色在线指示）、隐私审计大数字「0 Bytes」、终端风格日志 |

### 3.4 设计变量（globals.css）

```css
--background: #050505
--glass-bg: rgba(10, 10, 15, 0.6)
--glass-border: rgba(255, 255, 255, 0.08)
--glow-cyan / --glow-purple / --glow-pink
```

---

## 四、核心流程

### 4.1 技能浏览与部署（端云握手）

**当前流程**:
```
用户访问 /market
    → GET /api/v1/plugins (category, sort)
    → 展示神经元节点图（SVG）
    → 点击节点 → 右侧详情面板
    → 点击「Deploy to Layer 2」
    → POST /api/v1/deploy { plugin_id, target_instance_id }
    → 云端写入 deploy_commands，生成 temp_token
    → Layer 2 UpdaterAgent 轮询 GET /api/v1/deploy/poll
    → 收到指令 → 下载 .jmp → PluginManager.install_plugin() → 热加载
```

**升级流程（GitOps 式 Deployment Handshake）**:
```
1. 编排与下单：用户在 /forge 连线多个插件
2. 生成清单：点击发布 → Layer 1 生成「目标状态清单 (Desired State Manifest)」写入 deploy_commands
3. 拉取与比对：Layer 2 拉取清单 → 微内核与本地 current state 做 Diff
4. 智能热插拔：新增→下载校验挂载；移除→优雅关闭卸载
5. 状态回传：Layer 2 → POST /api/v1/agents/heartbeat → /console 节点亮绿（含 blueprint、task、IM 消息）
```

### 4.2 API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `GET /api/v1/plugins` | GET | 插件列表，支持 `category`、`sort` |
| `POST /api/v1/deploy` | POST | 生成部署指令与 temp_token |
| `GET /api/v1/deploy/poll?instance_id=xxx` | GET | Layer 2 轮询拉取指令 |
| `POST /api/v1/forge/publish` | POST | Forge 画布发布为 JMP（骨架已建） |
| `POST /api/v1/agents/heartbeat` | POST | Layer 2 心跳与状态回传（✅ 已实现，含 blueprint、task、pending_message_ids） |
| `POST /api/v1/agents/result` | POST | 边缘 Agent 执行结果回传，推回 TG/飞书（✅ 已实现） |
| `POST /api/v1/webhooks/telegram` | POST | Telegram 机器人 Webhook（✅ 已实现） |

### 4.3 数据流

```
plugins_registry (Supabase)
    → GET /api/v1/plugins
    → 映射为 PluginRecord (含 x, y, color, connections, compatibility)
    → 前端渲染 SVG 节点图（含环境透镜过滤）

edge_agents (Supabase)
    ← POST /api/v1/agents/heartbeat 更新 last_heartbeat
    → /console 舰队视图、心跳返回 blueprint + task（IM 消息队列）

deploy_commands (Supabase)
    ← POST /api/v1/deploy 写入
    → GET /api/v1/deploy/poll 读取
```

---

## 五、数据模型（Supabase Schema）

| 表 | 说明 |
|------|------|
| `nexus_users` | RBAC 用户（super_admin / developer / consumer / promoter） |
| `plugins_registry` | 技能插件（plugin_id, download_url, manifest_json, category, status） |
| `personas_library` | 人设（persona_id UUID 全局唯一） |
| `transactions` | 交易记录 |
| `deploy_commands` | 部署指令（端云握手） |
| `edge_agents` | 边缘智能体（pairing_code, auth_token, last_heartbeat, current_blueprint_id, im_binding_id, im_platform） |
| `blueprints` | 蓝图资产（name, ast_json，Forge 画布持久化） |
| `agent_message_queue` | IM 消息队列（agent_id, message_text, direction, status） |
| `promoters`（规划） | 布道者（affiliate_code, payout_address） |
| `transactions` 扩展（规划） | 分润明细（promoter_id, split_json） |

---

## 六、与设计蓝图的对应关系

| 蓝图概念 | 当前实现 |
|----------|----------|
| Neural Market | ✅ `/market`，SVG 节点图（规划为 Three.js 3D） |
| The Forge | ✅ `/forge`，React Flow 可视化编排 |
| The Agora | ❌ 待建 |
| Jachin ID & Console | ✅ `/console`，舰队视图 + 隐私审计 |
| JMP 协议 | ✅ `docs/JMP_SPEC.md`，`.jmp` 包结构，**JMP 2.0** 环境感知与依赖树 |
| IPFS 存储 | ❌ 当前用 mock URL |
| Passkey / Web3 | ❌ 待建 |

### 蓝图语义升级（Layer 2 Agent Loop）

**蓝图** 不再仅是流程图，而是 **岗位说明书 (Persona & Skillset)**：  
Forge 中的 Processor 节点 = Wasm 技能武器，下发到 Layer 2 后由 **ReAct Agent** 自主决定调用顺序与时机。详见 [LAYER2_AGENT_LOOP_DESIGN.md](./LAYER2_AGENT_LOOP_DESIGN.md)。

---

## 七、导航结构

```
Navbar (固定顶部)
├── JACHIN NEXUS (Logo → /)
├── Market → /market
├── The Forge → /forge
└── Console → /console
```

---

## 八、实施状态小结

| 战役 | 状态 | 说明 |
|------|------|------|
| 战役一：数据字典 | ✅ | Supabase migrations 已建 |
| 战役二：JMP 协议 | ✅ | 规范已定 |
| 战役三：API 血管 | ✅ | plugins / deploy / poll 已实现，部分 mock |
| 战役四：端云握手 | ✅ | UpdaterAgent 轮询逻辑已打通 |
| Supabase 接入 | 🔄 | 可配置，未配置时用 fallback |
| 3D 神经元 | 🔄 | 当前为 2D SVG，规划为 Three.js |
| The Agora | ❌ | 待建 |
| edge_agents / blueprints | ✅ | 迁移已建 |
| 环境透镜 / 冲突检测 | 🔄 | 规划中 |
| 心跳 API | ✅ | POST /api/v1/agents/heartbeat，含 blueprint、task、IM 扩展 |
| IM 网关 | ✅ | Telegram Webhook、消息队列、result API，详见 [IM_GATEWAY_SPEC.md](./IM_GATEWAY_SPEC.md) |

---

**相关文档**:
- [LAYER2_AGENT_LOOP_DESIGN.md](./LAYER2_AGENT_LOOP_DESIGN.md) - Layer 2 Agent Loop、蓝图 Persona & Skillset
- [IM_GATEWAY_SPEC.md](./IM_GATEWAY_SPEC.md) - IM 网关（TG/飞书、消息队列、NAT 穿透）
- [ECOSYSTEM_AND_COMMERCIALIZATION_WHITEPAPER.md](./ECOSYSTEM_AND_COMMERCIALIZATION_WHITEPAPER.md) - 全球生态与商业化白皮书（五阶层、智能版税、GTM）
- [REVENUE_AND_ROYALTY_SPEC.md](./REVENUE_AND_ROYALTY_SPEC.md) - 盈利模型与智能版税分润规范
- [GTM_STRATEGY.md](./GTM_STRATEGY.md) - Go-to-Market 市场推演与阶段落地
- [INVISIBLE_SECURITY_UX.md](./INVISIBLE_SECURITY_UX.md) - 无感安全与渐进式授权（傻瓜式配对、权限大白话、云端无感打包）
- [MICROKERNEL_ECOSYSTEM_UPGRADE.md](./MICROKERNEL_ECOSYSTEM_UPGRADE.md) - 微内核生态深度融合升级方案
- [MICROKERNEL_ECOSYSTEM_BATTLE_PLAN.md](./MICROKERNEL_ECOSYSTEM_BATTLE_PLAN.md) - 战术战役规划（含 Layer 1 已完成战役）
- [JMP_SPEC.md](./JMP_SPEC.md) - JMP 2.0 协议规范
- [architecture.md](./architecture.md) - 系统架构总览
