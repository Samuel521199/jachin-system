# HeronCloud / HeronCloud-Game 技术文档精华汇编

> **来源**：`HeronCloud 项目架构设计文档.pdf`、`HeronCloud 项目开发文档.pdf`、`HeronCloud-Game 项目架构总结.pdf`（本地路径抽取文本后整理）。
> **性质**：要点摘要与结构归纳，**非**全文照搬，便于 BI 与 Agent 理解服务端技术语境。

---

## 一、HeronCloud 平台框架（架构设计精华）

### 1.1 定位与版本

- **Go 1.24.4** + **Kratos v2.8.0** 微服务框架。
- 目标：**高可用、高性能、易扩展**；多业务模块并发；服务治理、链路追踪、集中日志、自动化运维。

### 1.2 四层架构（逻辑分层）

| 层次 | 职责摘要 |
|------|----------|
| **网关层** | 统一接入、认证、限流、路由、安全防护；屏蔽后端细节。 |
| **业务服务层** | 核心业务；对外 API / 内部服务微服务化；可独立扩缩容。 |
| **基础设施层** | MySQL、Redis、消息队列等；保障一致性与高可用。 |
| **服务治理层** | 注册发现、配置、链路追踪、日志、监控告警。 |

### 1.3 网关能力（设计文档重点）

- **API 网关**：协议转换、路由、负载均衡、统一错误与响应格式。
- **JWT**：无状态鉴权；Bearer 提取、签名校验、过期校验、**Redis 黑名单**（注销）；支持 Refresh、即将过期自动刷新；**路径白名单**（登录/注册等）。
- **限流**：Redis + Lua、**滑动窗口**；维度含全局 / IP / 用户 / API 路径 / 服务级；敏感接口（登录、注册、支付）可单独收紧；多维度白名单。
- **中间件链**：Recovery → JWT → 限流 → 业务；可扩展。
- **链路追踪**：集成 **Jaeger**，跨服务可视化。

### 1.4 基础设施与治理（文档表述）

- **MySQL**：关系型存储；文档提及主从、分库分表方向。
- **Redis**：缓存、分布式锁、限流与 JWT 黑名单等。
- **Kafka**：异步解耦、削峰、事件驱动（与 Game 侧 RocketMQ 表述区分见下文）。
- **Nacos**：注册发现、配置、健康检查。
- **ELK**、**Prometheus + Grafana**：日志与监控告警。

### 1.5 部署与运维要点

- **Linux**、**Docker**、**Kubernetes**；多实例、多可用区、CI/CD、蓝绿/灰度。
- **监控指标示例**：限流触发率、JWT 成功/失败、Token 刷新、Redis 性能、延迟 P99、错误率。
- **告警示例**：限流触发率过高、认证异常、Redis/服务异常、重要配置变更。
- **配置**：Nacos 动态配置、热加载、校验与版本回滚。

### 1.6 框架特性（归纳）

高可用（多实例、健康检查、降级熔断）、可扩展（微服务独立部署）、安全（JWT + 多层限流 + 加密传输/存储表述）、可观测（追踪+日志+监控）、性能（限流单次 Redis 原子操作等）。

---

## 二、HeronCloud 项目开发（规范与流程精华）

### 2.1 本地环境

- OS：Linux / macOS 为主，Windows 可用 PowerShell 或 WSL。
- **Go 1.24.4**、**Kratos v2.8**、**protoc 3.21+** 及 go/grpc/kratos 相关 `protoc-gen-*` 插件。
- **Docker**；依赖 **MySQL、Redis、Kafka**（文档建议 Docker Compose）。
- **Nacos 2.4.3**（单机 `startup.sh -m standalone`，控制台 8848）。

### 2.2 仓库目录约定（文档树）

- `api/`：Protobuf、接口与错误码定义；变更需重新生成代码。
- `apps/`：各微服务；通常含 `cmd/`、`internal/`（service / biz / data / server / conf）、`test/`。
- `common/`：公共错误、中间件、工具。
- `configs/`：多环境配置，忌硬编码密钥。
- `scripts/`、`deployments/`、`docs/`、`Makefile`、`README.md`。

### 2.3 开发规范（与 K11 知识库一致处强调）

- **业务与数据访问分层**；**禁止在 Service 层直接操作数据库**。
- 错误统一走 **`common/errors`**，避免裸透底层错误。
- 接口**必须在 `api/` 的 proto 中定义**；错误码与响应结构统一；API 变更需评审。
- 日志格式统一，重要路径有清晰日志与注释。

### 2.4 Gateway → API 调用链（文档步骤）

客户端 HTTPS → Gateway（JWT、限流、追踪）→ **Nacos 发现** API 实例并负载均衡 → API 执行业务（可能调用户/订单/支付等）→ 经 Gateway 统一响应 → **Jaeger / ELK** 全链路可观测。

### 2.5 日常开发建议

IDE（VS Code / GoLand）、`kratos run` 热加载、`docker-compose` 起依赖、本地也建议开日志/追踪/监控便于排障。

---

## 三、HeronCloud-Game（有状态游戏服架构精华）

### 3.1 定位

- **高性能、微服务化、Stateful 游戏服务端**。
- 核心范式：**Gateway 接入 + 业务节点分离 + 异步持久化 + Actor 并发模型**。
- 文档标注版本 **Go 1.24.4**，更新日期示例为 2025-12-31。

### 3.2 流量与数据流（概念）

- 玩家 **WebSocket** → **Gateway**（路由 Map、协议 ID）→ **gRPC** → 各游戏服务（如 Mines、Solitaire）。
- Gateway **本地广播**：游戏结果经 Gateway **Broadcast**，按 PlayerID 找 WS 连接推送，**减少内网 N+1 RPC**。
- **RocketMQ**：游戏内事件异步落库；**支付中台**等可通过 MQ 回调进 **RoomManager.Call → Actor**，与对局逻辑**同队列串行**，保证余额一致。

### 3.3 目录职能（文档映射）

| 区域 | 职责 |
|------|------|
| `apps/gateway` | WS 长连接、解包、按 Protocol ID 路由、gRPC 转发、本地广播。 |
| `apps/auth` | 登录、JWT 签发。 |
| `apps/<game>` | 具体玩法、内存状态。 |
| `game/actor` | **单 Room 单 Actor**，Channel 串行，避免锁。 |
| `game/manager` | Actor 生命周期、**RoomID ↔ PlayerID** 双向索引、`Call` 投递闭包。 |
| `game/domain` | Room / Player 等领域对象。 |
| `game/rmq` | MQ 生产/消费（充值到账、GM 等跨进程）。 |
| `common/middleware` | 限流、Recovery、Trace 等。 |
| `common/websocket` | 网络层封装。 |

### 3.4 核心机制

1. **内存优先**：局内金币、牌局状态先内存，毫秒级响应；**Actor 单协程**处理同一房间所有事件，业务侧避免 `sync.Mutex`。
2. **异步落库（Write-Behind）**：内存更新 → 先 Ack 客户端 → MQ / 快照通道异步持久化；`changed=true` 触发快照克隆与 `saveCh`，**计算与 IO 分离**。
3. **Actor 细节**：`eventCh` 业务、`timerCh` 定时（仍在 Actor 线程）、`saveCh` 持久化；**Panic recover** 隔离单次请求；Channel 积压监控、**Context 超时**防卡死。
4. **开发禁忌**：**禁止在 Event 内做耗时同步 RPC/DB**；必须传 Actor context；改状态后 **`changed=true`** 否则不落库。
5. **Gateway 性能注意**：WS ↔ gRPC 存在二次序列化 CPU 成本；高在线时 **ConnectionManager** 锁竞争可考虑分段锁等优化（文档提及 sync.Map 与遍历/上下线抖动）。

### 3.5 典型闭环（文档示例）

- **Solitaire**：Handler → `RoomManager.Call` → Actor → Entities 改内存 → Gateway Broadcast；机器人复用真人接口、`RobotLv` 策略；**Wire** 依赖注入。
- **充值到账**：中台 MQ → Consumer → `PlayerRoomId` → `rm.Call` 进 Actor 加币，与下注串行。

### 3.6 开发者工作流（摘要）

定义 proto → `make proto` → Handler 收 gRPC → `roomManager.Call` → entities 纯内存算法 → 改 `wire.go` 生成依赖 → 启动服务。

---

## 四、给 BI / 数据分析的提示

- **指标与稳定性**：网关限流触发率、JWT 失败率、延迟与错误率与「活动高峰、支付、登录」等行为相关，可与运营活动、买量节奏对照。
- **经济类指标**：游戏侧状态在内存，持久化异步；BI 若读库/日志，需注意**与实时局内状态的时间差**。
- **与 K11 知识库对齐**：HeronCloud-Game 的 Actor + Gateway 广播 + MQ 落库，对应 K11 文档中的 **HeronCloud-Game、Actor、WebSocket、RocketMQ** 等表述；平台通用层则侧重 **Kratos、Nacos、JWT、Kafka（平台文档）与 RocketMQ（游戏文档）** 的语境区分——实际以各环境配置为准。

---

## 五、原始文件索引（便于人工核对）

| 摘要章节 | 原始 PDF |
|----------|----------|
| 一 | `HeronCloud 项目架构设计文档.pdf` |
| 二 | `HeronCloud 项目开发文档.pdf` |
| 三、四 | `HeronCloud-Game 项目架构总结.pdf` |
