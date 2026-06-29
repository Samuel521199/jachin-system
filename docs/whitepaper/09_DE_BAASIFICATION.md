# 09 — 去 BaaS 化战役：Layer 1 绝对主权架构 (De-BaaSification)

**文档类型**: 白皮书 · 架构升维路线图  
**版本**: V2.3  
**更新日期**: 2026-06  
**状态**: **P0 已落地**；P1+ 为增强与私有化交付

---

## 一、战役背景

Layer 1 若依赖第三方 BaaS，将面临数据主权、私有化交付成本与厂商锁定问题。

**目标**：Layer 1 可自建 PostgreSQL + Auth.js + 可选 Redis/MinIO，支持 Helm 私有化部署。

---

## 二、四大改造支柱

### 2.1 身份认证：Auth.js ✅

| 项 | 状态 |
|----|------|
| Drizzle Adapter + users/accounts/sessions | ✅ |
| Credentials + OAuth | ✅ |
| JWT 含 orgId/orgRole | ✅ |
| Default Deny middleware | ✅ |
| Magic Link / Passkey | ⏳ 可选增强 |

### 2.2 数据中枢：Drizzle + PostgreSQL ✅

| 项 | 状态 |
|----|------|
| `src/db/schema.ts` | ✅ |
| `npm run db:migrate` / `db:push` | ✅ |
| 组织/舰队/商城表 | ✅ |

### 2.3 队列：PostgreSQL 轮询 → Redis ⏳

| 项 | 现状 | 目标 |
|----|------|------|
| `agent_message_queue` | PG 表 + 轮询/拉取 | Redis Streams |
| `deploy_commands` | PG | Redis 推送 |
| 心跳元数据 | 直写 PG | Redis 缓冲 + 异步落库 |

**L2 侧 Redis**（MCP Pull、在线 L3）**已可选**用于集群；与 L1 队列改造独立。

### 2.4 物资库：S3 / MinIO ⏳

| 项 | 状态 |
|----|------|
| 插件包存储 | AWS S3（`cloud/nexus` 现有集成） |
| MinIO 自建 | ⏳ 私有化方案 |
| IPFS 冷备份 | ⏳ 规划 |

---

## 三、Helm 私有化交付 ⏳

```bash
helm install jachin-nexus ./charts/layer1   # 规划中
```

目标组件：Next.js、PostgreSQL、Redis、MinIO、Ingress。

---

## 四、实施路线图

| 阶段 | 任务 | 状态 |
|------|------|------|
| **P0** | Drizzle Schema + Relations | ✅ |
| **P0** | Auth.js 闭环 + 组织 API | ✅ |
| **P0** | Store/Sync/Fleet API 迁 Drizzle | ✅ |
| **P1** | MinIO 或纯 S3 配置文档化 | 部分 |
| **P2** | Redis 承接 IM/部署队列 | ⏳ |
| **P3** | Helm Chart | ⏳ |

---

## 五、与 L2/L3 的兼容性

- L2/L3 协议（manifest、配对、MCP 委托）**不依赖** L1 内部 ORM 实现。
- 多租户 `organizations` / `organization_users` 模型 **保持不变**。
- V2.2 显式 workspace onboarding **已**在 Auth 流程落地。

---

## 六、参考

- [05_LAYER1_NEXUS.md](./05_LAYER1_NEXUS.md)
- [L1_LINUX_CLOUD_DEPLOY.md](../L1_LINUX_CLOUD_DEPLOY.md)
- `cloud/nexus/package.json` — `db:*` 脚本
