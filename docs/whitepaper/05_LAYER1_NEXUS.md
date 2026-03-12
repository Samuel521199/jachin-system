# 05 — Layer 1: Jachin Nexus (平台)

**文档类型**: 白皮书 · Layer 1 详细说明  
**版本**: V2  
**基准**: [ARCHITECTURE_V2_LAYER3_STANDALONE.md](../ARCHITECTURE_V2_LAYER3_STANDALONE.md)

---

## 〇、 Platform First（平台优先原则）

**Layer 1 默认为官方托管的多租户 SaaS 平台。** 个人、家庭和企业用户开箱即用，只需在边缘端拉起 Layer 2/3 并连接到云端即可。

**私有化部署（Self-Hosted Layer 1）** 仅作为政企、金融等强合规场景的 fallback 方案。

---

## 一、 定位与哲学 (Positioning & Philosophy)

Layer 1 是 Jachin 系统的**平台**：用户主账号注册/登录，平台主账号管理平台内部。**与 L2/L3 无直接耦合**。

* **核心戒律：绝对不存储边缘节点的隐私记忆。** 用户的聊天记录、梦境反思、本地文件数据均隔离在 Layer 2 的 SQLite 中。
* **主要职责：** 资产确权（蓝图与插件）、设备状态监控（心跳呼吸灯）、跨网指令路由（IM 网关）、舰队级批量下发。
* **商业定位：** B 端企业的“航母指挥室”，C 端极客的“神经元商城”。

---

## 二、 核心模块全景 (Core Modules)

### 2.1 极简免密之门 (Jachin ID & Magic Auth)
* **废弃密码与强 Web3 绑定**：基于 Auth.js（去 BaaS 化目标），支持 Magic Link（邮箱/手机验证码）与主流 OAuth 免密登录。
* **权限降维**：消除用户的注册摩擦，登录即进入上帝视角。

### 2.2 舰队指挥大屏 (Fleet Management) - B端杀器
* **数字孪生拓扑**：实时监控全球各地物理设备（Edge Agents）的在线/离线状态、心跳延迟、当前运行的蓝图版本。
* **一键批量热更新**：管理员勾选目标节点（支持全选/分组），选择指定 AST 蓝图点击“批量下发”。底层修改 `current_blueprint_id`，边缘节点在 10 秒心跳内自动热重载，实现千台设备算力阵型的瞬间切换。

### 2.3 造物厂 (The Forge) - 逻辑铸造中心
* **可视化编排**：基于 React Flow 打造的极客工作台。通过拖拽 `Trigger` (触发器)、`Processor` (ReAct 思考/WASI 沙箱) 和 `Action` (输出)。
* **AST 编译**：前端将连线逻辑一键编译为标准 AST JSON（抽象语法树），固化至 `blueprints` 表，成为边缘节点可执行的“岗位说明书”。

### 2.4 神经元商城与悬赏榜 (Market & Bounty Board)
* **JPP 生态大厅**：全球极客上传 `.wasm` 插件与 `plugin.json` 版税清单的集散地。
* **版税结算中心**：记录边缘节点对付费插件的调用次数，依据智能合约/平台账本为开发者进行 Crypto/法币的自动化分润。

---

## 三、 跨网通讯枢纽 (IM Gateway & Message Queue)

为了让内网深处的 Layer 2 能够随时随地响应手机端的指令，Layer 1 充当了完美的 NAT 穿透桥梁。

### 3.1 Universal Message Adapter (全渠道统一适配)

**划时代意义**：把所有 IM 渠道降维成统一的「感官输入流」。

- **统一格式**：Discord、Slack、WhatsApp、iMessage、飞书、钉钉等 Webhook 进入后，全部清洗成标准 **Jachin Message** 格式入队。
- **核心逻辑只写一次**：渠道无限扩展，无需为每个平台重写业务逻辑。
- **路由**：`/api/v1/webhooks/{platform}` → 解析 → 写入 `agent_messages` → 下发（过渡期：心跳拉取；P0：WS 长连推送）。

### 3.2 跨网通信链路 (以 Telegram 为例)
1. **Webhook 捕获**：接收用户消息，解析 Chat ID，写入队列。
2. **任务下发**：Layer 2 拉取 `pending` 任务（过渡期：心跳；P0：WS 长连推送）。
3. **结果回传**：Layer 1 调用平台 API 将结果推回用户。

---

## 四、 云端数据底座 (Drizzle Schema)

Layer 1 的核心表结构（`cloud/nexus/src/db/schema.ts`，**多租户**）：

* `organizations`: 组织表（id, name, billing_plan），企业舰队归属。
* `organization_users`: 组织-用户关联（org_id, user_id, role: owner/admin/member）。
* `edge_agents`: 设备 ID、绑定用户、**organization_id**（NULL=个人，非空=企业舰队）、当前 `blueprint_id`、最后心跳时间、`im_binding_id`、`im_platform`。
* `blueprints`: AST JSON、**organization_id**（NULL=个人，非空=企业共享）。
* `transactions`: 技能订阅、**organization_id**（企业采购可共享给组织内所有设备）。
* `agent_messages`: 跨网指令队列（`agent_id`, `content`, `direction`, `status`）。
* `plugins`: JPP 神经元商城元数据（Wasm 文件存储路径、`royalty_fee` 分润金额、输入输出 Schema）。

---

## 五、 v8.0 废弃声明 (Deprecation in v8.0)

1. **废弃复杂的私有化身份认证系统**：不再自行维护复杂的 JWT 签发与密码哈希，全面托付给 Auth.js。
2. **废弃 Dapr Pub/Sub 中继**：在广域网（WAN）环境下，Pub/Sub 的穿透与稳定性维护成本极高，现已全面替换为**Jachin Mesh (WebSocket 优先) + HTTP 心跳兜底**。未来将演进为控制面/数据面分离（WS 长连推送、mDNS/P2P 直连），详见 [10_CONTROL_DATA_PLANE.md](./10_CONTROL_DATA_PLANE.md)。

---

## 六、 去 BaaS 化 (De-BaaSification) — P0 已落地

Layer 1 去 BaaS 化已完成：Drizzle ORM + Auth.js Schema，`src/db/schema.ts` 定义 users/accounts/sessions、organizations、organization_users、edge_agents、blueprints、transactions。使用 PostgreSQL + Drizzle，无第三方 BaaS 依赖。详见 [09_DE_BAASIFICATION.md](./09_DE_BAASIFICATION.md)。

规划中的后续阶段：

- **Auth**：Auth.js（Schema 已就绪）
- **ORM**：Drizzle ORM（已就绪）
- **队列**：数据库轮询 → Redis Streams/Pub/Sub
- **存储**：MinIO (S3 兼容) 或 IPFS
- **交付**：Helm Chart 一键部署，客户私有集群 100% 数据主权

详见 [09_DE_BAASIFICATION.md](./09_DE_BAASIFICATION.md)。