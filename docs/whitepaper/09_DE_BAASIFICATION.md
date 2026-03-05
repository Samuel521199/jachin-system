# 09 — 去 BaaS 化战役：Layer 1 绝对主权架构 (De-BaaSification)

**文档类型**: 白皮书 · 架构升维路线图  
**版本**: v8.0+ (The Singularity OS)  
**更新日期**: 2026-02  
**状态**: 战略蓝图，待实施

---

## 一、 战役背景：为何必须去 BaaS 化

当 Jachin Nexus 进化到 **V8.0 The Singularity OS** 维度时，若其心脏（Layer 1 数据中枢）仍依赖第三方商业 BaaS（Supabase），将面临：

| 枷锁 | 影响 |
|------|------|
| **数据主权** | 合规审计过不去，政企/金融客户无法接受数据在第三方云 |
| **私有化部署** | 让客户自配 Supabase 项目 = 灾难级交付成本 |
| **厂商锁定** | Vendor Lock-in 定价风险，随时面临涨价或条款变更 |
| **架构割裂** | 曲率引擎级的技术栈，却依赖别人的加油站 |

**目标**：将 Layer 1 重塑为可一键打包、全球任意节点独立部署的 **主权云原生底座 (Sovereign Cloud Native Foundation)**。

---

## 二、 四大改造支柱

### 2.1 身份认证：Supabase Auth → Auth.js (NextAuth)

| 维度 | 现状 (Supabase Auth) | 目标 (Auth.js) |
|------|----------------------|----------------|
| **数据归属** | 黑盒，存于 Supabase 云 | 完全开源，数据存自有 PostgreSQL |
| **表结构** | auth.users（不可控） | users, accounts, sessions（自建表） |
| **OAuth** | Supabase 配置 | GitHub/Google 直连，标准 OAuth 2.0 |
| **Magic Link** | Supabase 内置 | Resend 或自建 SMTP 发信 |
| **鉴权逻辑** | 依赖 Supabase RLS | 中间件 + RBAC 拦截器，逻辑收口自控 |

**实施要点**：
- 引入 `authjs` / `next-auth`，适配 Next.js App Router
- 生成 `users`, `accounts`, `sessions`, `verification_tokens` 表
- 结合多租户架构：超级管理员、租户管理员、普通成员，数据库层面 RBAC

---

### 2.2 数据中枢：PostgREST/supabase-js → Drizzle ORM + 原生 PostgreSQL

| 维度 | 现状 | 目标 |
|------|------|------|
| **客户端** | @supabase/supabase-js | Drizzle ORM |
| **类型安全** | 部分推导 | 完整 TypeScript 推导 |
| **迁移工具** | supabase db push | drizzle-kit generate + migrate |
| **部署兼容** | 仅 Supabase 托管 | 任意 PostgreSQL（AWS RDS、自建、裸金属） |

**实施要点**：
- 15+ 核心业务表用 Drizzle Schema 重写
- `drizzle-kit push` 或 `drizzle-kit migrate` 生成原生 SQL
- 一条命令完成数据库初始化，无 Supabase 依赖

---

### 2.3 神经突触与队列：Supabase Realtime/轮询 → Redis

| 维度 | 现状 | 目标 |
|------|------|------|
| **agent_message_queue** | PostgreSQL 表 + 轮询 | Redis Streams 或 Pub/Sub |
| **deploy_commands 下发** | 数据库轮询 | Redis 队列 + 订阅 |
| **海量心跳** | Postgres I/O 压力 | Redis 缓存 + 异步落库 |
| **延迟** | 百毫秒级 | 亚毫秒级 |

**实施要点**：
- Redis 作为 Layer 1 高性能神经总线
- `agent_message_queue` → Redis Streams（持久化 + 消费组）
- `deploy_commands` 待下发指令 → Redis List/Stream
- 心跳元数据可先写 Redis，定时批量落 Postgres

---

### 2.4 物资库：Supabase Storage → MinIO (S3 兼容)

| 维度 | 现状 | 目标 |
|------|------|------|
| **.jmp 武器包** | Supabase Storage (jmp-packages) | MinIO Bucket |
| **API** | Supabase SDK | AWS S3 SDK (aws-sdk-js / @aws-sdk/client-s3) |
| **冷备份** | 可选 IPFS | MinIO 热数据 + IPFS 去中心化冷备份 |
| **部署** | 绑定 Supabase 项目 | 自建 MinIO 或任意 S3 兼容存储 |

**实施要点**：
- MinIO 提供 S3 兼容 API，代码仅需切换 SDK
- 双模存储：MinIO 热分发 + IPFS 冷备份（已有规划）

---

## 三、 终极交付形态：Kubernetes + Helm

改造完成后，Layer 1 可被彻底容器化，面向政企/金融私有化交付：

```bash
helm install jachin-nexus ./charts/layer1
```

**Helm Chart 包含**：
- Next.js 控制台 (Layer 1 前端 + API)
- PostgreSQL（原生，非 Supabase）
- Redis
- MinIO
- 可选：Ingress、TLS、备份 Job

**效果**：客户私有集群内，内部网络物理隔离，数据主权 100% 自控。

---

## 四、 实施路线图（建议优先级）

| 阶段 | 任务 | 状态 |
|------|------|------|
| **P0** | Drizzle ORM 体系初始化 | ✅ 已完成：src/db/index.ts、schema.ts、drizzle.config.ts |
| **P0** | Auth.js 表 + 多租户 Schema | ✅ 已完成：users, accounts, sessions, verification_tokens, organizations, organization_users, edge_agents, blueprints, transactions |
| **P0** | Drizzle Relations 定义 | ✅ 已完成 |
| **P0** | Auth.js 替换 Supabase Auth | 待实施：接入 NextAuth + DrizzleAdapter |
| **P0** | 迁移现有 API 至 Drizzle | 待实施：逐步替换 getSupabase() 调用 |
| **P1** | drizzle-kit 迁移，废弃 supabase db push | 待实施：`npm run db:push` 或 `db:migrate` |
| **P1** | MinIO 替换 Supabase Storage | S3 SDK 接入 |
| **P2** | Redis 承接 agent_message_queue | 需评估现有轮询逻辑改造量 |
| **P2** | Redis 承接 deploy_commands 下发 | 与 Layer 2 心跳拉取协议协同 |
| **P3** | Helm Chart 打包 | 上述全部完成后 |

---

## 五、 与现有架构的兼容性

- **Layer 2 / Layer 3**：无需改动。心跳、配对、IM 网关等协议保持不变，仅 Layer 1 内部实现切换。
- **多租户**：organizations、organization_users、organization_id 等设计完全保留，Drizzle Schema 直接映射。
- **IPFS**：已有「IPFS 优先于 Supabase Storage」设计，MinIO 作为热数据层，IPFS 作为冷备份，双模并存。

---

## 六、 参考文档

- [05_LAYER1_NEXUS.md](./05_LAYER1_NEXUS.md) — Layer 1 现状
- [02_FRAMEWORK.md](./02_FRAMEWORK.md) — Platform First 原则
- [.cursor/rules/070-layer1-platform.mdc](../../.cursor/rules/070-layer1-platform.mdc) — 多租户宪法
