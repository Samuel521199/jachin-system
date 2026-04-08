/**
 * Jachin Nexus Layer 1 - Drizzle Schema
 * 去 BaaS 化 P0：Auth.js + 多租户 + 舰队资产
 * 完全遵循 Auth.js Drizzle Adapter 规范，严丝合缝关联 Organizations 与舰队
 */
import {
  pgTable,
  text,
  timestamp,
  uuid,
  primaryKey,
  integer,
  jsonb,
  numeric,
  varchar,
  pgEnum,
  uniqueIndex,
  index,
  boolean,
} from "drizzle-orm/pg-core";
import { relations } from "drizzle-orm";

// =============================================================================
// Layer 1 平台身份体系
// =============================================================================

export const platformAdmins = pgTable("platform_admins", {
  id: text("id")
    .primaryKey()
    .$defaultFn(() => crypto.randomUUID()),
  username: text("username").notNull().unique(),
  passwordHash: text("password_hash").notNull(),
  role: text("role").notNull().default("super_admin"),
  createdAt: timestamp("created_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
});

// =============================================================================
// Auth.js 基础表（Drizzle Adapter 标准规范）
// users 支持 OAuth；password_hash 可选，用于 Credentials 密码登录
// =============================================================================

/**
 * 平台用户（自然人账号）。
 *
 * @warning **禁止**用本表推断「当前租户」或做资源隔离。租户边界 **唯一** 来自
 * {@link organizations} + {@link organizationUsers}。配对 Fleet / 计费 / 许可证须使用
 * `organizations.id`（UUID）并与 `organization_users` 校验成员身份。
 */
export const users = pgTable("users", {
  id: text("id")
    .primaryKey()
    .$defaultFn(() => crypto.randomUUID()),
  name: text("name"),
  email: text("email").unique(),
  emailVerified: timestamp("email_verified", { mode: "date" }),
  image: text("image"),
  passwordHash: text("password_hash"),
  /** 是否超级管理员（区分平台根账号与普通租户管理员） */
  isRoot: boolean("is_root").default(false),
});

export const accounts = pgTable(
  "accounts",
  {
    userId: text("user_id")
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    type: text("type").notNull(),
    provider: text("provider").notNull(),
    providerAccountId: text("provider_account_id").notNull(),
    refresh_token: text("refresh_token"),
    access_token: text("access_token"),
    expires_at: integer("expires_at"),
    token_type: text("token_type"),
    scope: text("scope"),
    id_token: text("id_token"),
    session_state: text("session_state"),
  },
  (account) => [
    primaryKey({
      columns: [account.provider, account.providerAccountId],
    }),
  ]
);

export const sessions = pgTable("sessions", {
  sessionToken: text("session_token").primaryKey(),
  userId: text("user_id")
    .notNull()
    .references(() => users.id, { onDelete: "cascade" }),
  expires: timestamp("expires", { mode: "date" }).notNull(),
});

export const verificationTokens = pgTable(
  "verification_tokens",
  {
    identifier: text("identifier").notNull(),
    token: text("token").notNull(),
    expires: timestamp("expires", { mode: "date" }).notNull(),
  },
  (vt) => [
    primaryKey({ columns: [vt.identifier, vt.token] }),
  ]
);

// =============================================================================
// SaaS 多租户核心表 — Organization = Tenant（单一事实来源，SSOT）
// =============================================================================

/**
 * **租户（Tenant）即组织（Organization）**。`id` 是全系统隔离边界主键；API / JWT / `X-Tenant-Id`
 * 中的 `tenant_id` **必须**等于本表某一行的 `id`（UUID 字符串）。
 *
 * - **企业 / 团队**：用户于 `/console/workspace` 创建，`is_personal_default = false`。
 * - **个人默认组织**：仅 **历史迁移或旧数据** 可能出现 `is_personal_default = true`；**新注册**
 *   仅创建 `users`，不自动插入组织（见 `docs/ARCHITECTURE_L1_WORKSPACE_L2_GATEWAY_L3.md`）。
 *
 * @see docs/MIGRATION_P1_TENANT.md
 */
export const organizations = pgTable(
  "organizations",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    name: text("name").notNull(),
    /** 可选短码，全局唯一；API 仍以 UUID `id` 为契约主键 */
    slug: varchar("slug", { length: 64 }),
    billingPlan: text("billing_plan").default("free"),
    /**
     * 是否为系统自动创建的「个人默认工作区」。每用户至多一个 such org（由迁移与应用层保证）。
     * 企业组织恒为 `false`。
     */
    isPersonalDefault: boolean("is_personal_default").notNull().default(false),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [uniqueIndex("organizations_slug_unique").on(t.slug)]
);

/** P2 Fleet ACL：在 org 内增加车队管理员（管设备/组）与只读成员 */
export const orgRoleEnum = pgEnum("org_role", [
  "owner",
  "admin",
  "member",
  "fleet_admin",
  "viewer",
]);

/**
 * **用户 ↔ 组织成员关系（唯一合法归属来源）**。
 *
 * @warning 任何「该用户是否属于某租户」的判断 **必须** 查询本表（`org_id` + `user_id`）。
 * 禁止从 `users` 推断租户；禁止仅凭未校验的 Header/Cookie 中的 `tenant_id` 放行写操作。
 *
 * **极简分配**：`POST .../members/join` 验签邀请后插入；`POST .../active-org` 切换会话内当前 `org_id`；
 * 成员列表与角色见 `.../members`、`.../members/invite` 等。
 */
export const organizationUsers = pgTable(
  "organization_users",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    orgId: uuid("org_id")
      .notNull()
      .references(() => organizations.id, { onDelete: "cascade" }),
    userId: text("user_id")
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    role: orgRoleEnum("role").notNull().default("member"),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [{ unique: [t.orgId, t.userId] }]
);

/** P2：组内成员角色（与 org_role 独立；见 device_group_members 表注释） */
export const deviceGroupMemberRoleEnum = pgEnum("device_group_member_role", [
  "admin",
  "viewer",
]);

/**
 * P2 Fleet ACL：组织下的设备资源组（车队 / 站点 / 产线）。
 * `org_id` 为强租户边界；组内设备见 {@link edgeAgents.deviceGroupId}。
 */
export const deviceGroups = pgTable(
  "device_groups",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    orgId: uuid("org_id")
      .notNull()
      .references(() => organizations.id, { onDelete: "cascade" }),
    name: text("name").notNull(),
    description: text("description"),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [
    index("device_groups_org_id_idx").on(t.orgId),
    uniqueIndex("device_groups_org_id_name_unique").on(t.orgId, t.name),
  ]
);

/**
 * 组级授权：**在 organization_users.role（org 级）之下的细粒度覆写**。
 * - 典型用法：限制某用户仅管理/查看特定 `device_groups` 下的 edge_agent。
 * - 应用层须保证：effective 权限 = f(org_role, group membership)；不得仅信任其一。
 */
export const deviceGroupMembers = pgTable(
  "device_group_members",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    groupId: uuid("group_id")
      .notNull()
      .references(() => deviceGroups.id, { onDelete: "cascade" }),
    userId: text("user_id")
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    role: deviceGroupMemberRoleEnum("role").notNull().default("viewer"),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [
    uniqueIndex("device_group_members_group_user_unique").on(t.groupId, t.userId),
    index("device_group_members_user_id_idx").on(t.userId),
  ]
);

// =============================================================================
// 核心业务与资产表（多租户穿透）
// =============================================================================

export const edgeAgentStatusEnum = pgEnum("edge_agent_status", [
  "pending",
  "active",
  "offline",
]);

export const blueprints = pgTable("blueprints", {
  id: uuid("id").primaryKey().defaultRandom(),
  creatorId: text("creator_id").references(() => users.id, { onDelete: "set null" }),
  organizationId: uuid("organization_id").references(() => organizations.id, {
    onDelete: "set null",
  }),
  name: text("name").notNull(),
  description: text("description"),
  astJson: jsonb("ast_json").notNull().default({}),
  price: numeric("price", { precision: 12, scale: 4 }).default("0"),
  createdAt: timestamp("created_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
});

export const edgeAgents = pgTable(
  "edge_agents",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    userId: text("user_id").references(() => users.id, { onDelete: "set null" }),
    /** 顶层租户边界（冗余 SSOT：应与 device_groups.org_id 一致当 device_group_id 已设） */
    organizationId: uuid("organization_id").references(() => organizations.id, {
      onDelete: "set null",
    }),
    /** P2：强关联资源组；未分组时为 null（或后续迁移写入 org 默认组） */
    deviceGroupId: uuid("device_group_id").references(() => deviceGroups.id, {
      onDelete: "set null",
    }),
    name: text("name"),
    pairingCode: varchar("pairing_code", { length: 6 }).notNull().unique(),
    status: edgeAgentStatusEnum("status").notNull().default("pending"),
    currentBlueprintId: uuid("current_blueprint_id").references(() => blueprints.id, {
      onDelete: "set null",
    }),
    authToken: text("auth_token"),
    pairingExpiresAt: timestamp("pairing_expires_at", { withTimezone: true }),
    lastHeartbeat: timestamp("last_heartbeat", { withTimezone: true }),
    imBindingId: text("im_binding_id"),
    imPlatform: text("im_platform").default("telegram"),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [
    index("edge_agents_organization_id_idx").on(t.organizationId),
    index("edge_agents_device_group_id_idx").on(t.deviceGroupId),
  ]
);

export const transactions = pgTable("transactions", {
  id: uuid("id").primaryKey().defaultRandom(),
  buyerId: text("buyer_id")
    .notNull()
    .references(() => users.id, { onDelete: "cascade" }),
  organizationId: uuid("organization_id").references(() => organizations.id, {
    onDelete: "set null",
  }),
  resourceType: text("resource_type").notNull(),
  resourceId: uuid("resource_id").notNull(),
  resourcePluginId: text("resource_plugin_id"),
  action: text("action").notNull().default("acquire"),
  licenseKey: text("license_key"),
  createdAt: timestamp("created_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
});

// =============================================================================
// 消息队列与部署
// =============================================================================

export const agentMessageQueue = pgTable("agent_message_queue", {
  id: uuid("id").primaryKey().defaultRandom(),
  agentId: uuid("agent_id")
    .notNull()
    .references(() => edgeAgents.id, { onDelete: "cascade" }),
  messageText: text("message_text").notNull(),
  direction: text("direction").notNull().default("inbound"),
  status: text("status").notNull().default("pending"),
  sourceMeta: jsonb("source_meta"),
  processedAt: timestamp("processed_at", { withTimezone: true }),
  createdAt: timestamp("created_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
});

export const deployCommands = pgTable("deploy_commands", {
  id: uuid("id").primaryKey().defaultRandom(),
  userId: text("user_id").notNull(),
  layer2InstanceId: text("layer2_instance_id").notNull(),
  resourceType: text("resource_type").notNull().default("plugin"),
  resourceId: uuid("resource_id").notNull(),
  pluginId: text("plugin_id"),
  downloadUrl: text("download_url").notNull(),
  tempToken: text("temp_token").notNull(),
  tokenExpiresAt: timestamp("token_expires_at", { withTimezone: true }).notNull(),
  status: text("status").notNull().default("pending"),
  createdAt: timestamp("created_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
});

// =============================================================================
// L1 云端商城 — 云边协同数字发行核心模型
// =============================================================================

// 任务 1：核心枚举 (Enums)
// -----------------------------------------------------------------------------

/** 商品类型：SKILL=轻量 Wasm 逻辑，MCP=重型数据驱动 */
export const itemTypeEnum = pgEnum("item_type", ["SKILL", "MCP"]);

/** 可见性：PUBLIC=公开售卖，PRIVATE=企业私有自用 */
export const visibilityEnum = pgEnum("visibility", ["PUBLIC", "PRIVATE"]);

/** 运行时层级：L3_LOCAL=终端执行，L2_GATEWAY=网关驻留，L1_CLOUD=云端托管 */
export const runtimeTierEnum = pgEnum("runtime_tier", [
  "L3_LOCAL",
  "L2_GATEWAY",
  "L1_CLOUD",
]);

/** License 状态：ACTIVE=有效，EXPIRED=已过期，REVOKED=已撤销 */
export const licenseStatusEnum = pgEnum("license_status", [
  "ACTIVE",
  "EXPIRED",
  "REVOKED",
]);

// 任务 2：plugins_registry 表 (全球数字商品总仓)
// -----------------------------------------------------------------------------

export const pluginsRegistry = pgTable(
  "plugins_registry",
  {
    /** 主键 */
    id: uuid("id").primaryKey().defaultRandom(),
    /** 插件唯一标识（反向域名，如 com.jachin.weather），用于 upsert */
    pluginId: text("plugin_id").notNull().unique(),
    /** 语义化版本号 */
    version: text("version").notNull().default("1.0.0"),
    /** 商品类型：SKILL 或 MCP */
    itemType: itemTypeEnum("item_type").notNull(),
  /** 商品名称 */
  name: text("name").notNull(),
  /** 商品描述 */
  description: text("description"),
  /** 开发者/创作者 ID（关联 users.id 或组织） */
  developerId: text("developer_id"),
  /** 可见性：PUBLIC 公开售卖，PRIVATE 私有自用，默认 PRIVATE */
  visibility: visibilityEnum("visibility").notNull().default("PRIVATE"),
  /** 月付价格（分/厘），0 表示免费 */
  priceMonthly: integer("price_monthly").notNull().default(0),
  /** 物理执行层级：终端/网关/云端 */
  runtimeTier: runtimeTierEnum("runtime_tier").notNull(),
  /** 依赖的 MCP ID 数组，JSONB 存储如 ["mcp:filesystem", "mcp:shell"] */
  requiredMcps: jsonb("required_mcps").$type<string[]>().default([]),
  /** 包下载链接（L2 同步时拉取） */
  packageUrl: text("package_url"),
  /** 包 SHA-256 校验码（L2 下载后完整性校验） */
  packageSha256: text("package_sha256"),
  /** 分类：skill | persona | memory，兼容商城展示 */
  category: text("category").default("skill"),
  /** 下载次数，兼容商城排序 */
  downloadCount: integer("download_count").default(0),
  /** manifest/plugin.json 完整内容，兼容商城展示 */
  manifestJson: jsonb("manifest_json").$type<Record<string, unknown>>(),
  /** 审核状态：pending 待审，approved 准许上架，rejected 驳回 */
  status: text("status").notNull().default("pending"),
  /** 驳回理由（status = rejected 时填写） */
  rejectReason: text("reject_reason"),
  /** 创建时间 */
  createdAt: timestamp("created_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
  /** 更新时间 */
  updatedAt: timestamp("updated_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
},
  (table) => [
    /** 优化 status = 'pending' 的审核列表查询 */
    index("plugins_registry_status_created_idx").on(table.status, table.createdAt),
  ]
);

// 任务 3：user_licenses 表 (数字资产印钞机)
// -----------------------------------------------------------------------------

export const userLicenses = pgTable(
  "user_licenses",
  {
    /** 主键 */
    id: uuid("id").primaryKey().defaultRandom(),
    /**
     * **租户 ID = `organizations.id`**（UUID 字符串）。与 JWT `tenant_id` / `X-Tenant-Id` 对齐。
     * 禁止写入 `users.id` 作为本字段（历史误用已随 P1 废止）。
     */
    tenantId: text("tenant_id").notNull(),
    /** 关联的商品 ID */
    itemId: uuid("item_id")
      .notNull()
      .references(() => pluginsRegistry.id, { onDelete: "cascade" }),
    /** License 状态 */
    status: licenseStatusEnum("status").notNull().default("ACTIVE"),
    /** 购买时间 */
    purchasedAt: timestamp("purchased_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
    /** 过期时间，空表示永久有效 */
    expiresAt: timestamp("expires_at", { withTimezone: true }),
  },
  (table) => [
    /** 硬性约束：同一租户对同一商品仅能有一条记录，防并发漏洞 */
    uniqueIndex("user_licenses_tenant_item_unique").on(
      table.tenantId,
      table.itemId
    ),
  ]
);


// =============================================================================
// Drizzle Relations（关系映射）
// =============================================================================

export const usersRelations = relations(users, ({ many }) => ({
  accounts: many(accounts),
  sessions: many(sessions),
  organizationUsers: many(organizationUsers),
  edgeAgents: many(edgeAgents),
  blueprints: many(blueprints),
  transactions: many(transactions),
  deviceGroupMembers: many(deviceGroupMembers),
}));

export const accountsRelations = relations(accounts, ({ one }) => ({
  user: one(users, { fields: [accounts.userId], references: [users.id] }),
}));

export const sessionsRelations = relations(sessions, ({ one }) => ({
  user: one(users, { fields: [sessions.userId], references: [users.id] }),
}));

export const organizationsRelations = relations(organizations, ({ many }) => ({
  organizationUsers: many(organizationUsers),
  edgeAgents: many(edgeAgents),
  blueprints: many(blueprints),
  transactions: many(transactions),
  deviceGroups: many(deviceGroups),
}));

export const organizationUsersRelations = relations(organizationUsers, ({ one }) => ({
  organization: one(organizations, { fields: [organizationUsers.orgId], references: [organizations.id] }),
  user: one(users, { fields: [organizationUsers.userId], references: [users.id] }),
}));

export const deviceGroupsRelations = relations(deviceGroups, ({ one, many }) => ({
  organization: one(organizations, { fields: [deviceGroups.orgId], references: [organizations.id] }),
  edgeAgents: many(edgeAgents),
  members: many(deviceGroupMembers),
}));

export const deviceGroupMembersRelations = relations(deviceGroupMembers, ({ one }) => ({
  group: one(deviceGroups, { fields: [deviceGroupMembers.groupId], references: [deviceGroups.id] }),
  user: one(users, { fields: [deviceGroupMembers.userId], references: [users.id] }),
}));

export const blueprintsRelations = relations(blueprints, ({ one, many }) => ({
  creator: one(users, { fields: [blueprints.creatorId], references: [users.id] }),
  organization: one(organizations, { fields: [blueprints.organizationId], references: [organizations.id] }),
  edgeAgents: many(edgeAgents),
}));

export const edgeAgentsRelations = relations(edgeAgents, ({ one }) => ({
  user: one(users, { fields: [edgeAgents.userId], references: [users.id] }),
  organization: one(organizations, { fields: [edgeAgents.organizationId], references: [organizations.id] }),
  deviceGroup: one(deviceGroups, { fields: [edgeAgents.deviceGroupId], references: [deviceGroups.id] }),
  currentBlueprint: one(blueprints, {
    fields: [edgeAgents.currentBlueprintId],
    references: [blueprints.id],
  }),
}));

export const transactionsRelations = relations(transactions, ({ one }) => ({
  buyer: one(users, { fields: [transactions.buyerId], references: [users.id] }),
  organization: one(organizations, { fields: [transactions.organizationId], references: [organizations.id] }),
}));

/** plugins_registry 与 user_licenses 关系 */
export const pluginsRegistryRelations = relations(pluginsRegistry, ({ many }) => ({
  licenses: many(userLicenses),
}));

export const userLicensesRelations = relations(userLicenses, ({ one }) => ({
  item: one(pluginsRegistry, { fields: [userLicenses.itemId], references: [pluginsRegistry.id] }),
}));

// =============================================================================
// 桌面端安装包分发（私有对象存储 + 预签名 URL；Tauri 热更新 JSON）
// =============================================================================

/** 单平台构建产物：MinIO/S3 对象键 + Tauri/minisign 签名（与 tauri signer 一致） */
export type DesktopArtifactMeta = {
  objectKey: string;
  signature: string;
};

export const desktopAppReleases = pgTable(
  "desktop_app_releases",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    /** semver，如 0.8.17 */
    version: text("version").notNull().unique(),
    notes: text("notes"),
    pubDate: timestamp("pub_date", { withTimezone: true }).notNull(),
    /** Tauri 平台键，如 windows-x86_64 → objectKey + signature */
    artifacts: jsonb("artifacts")
      .notNull()
      .$type<Record<string, DesktopArtifactMeta>>(),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  }
);

// =============================================================================
// L1 遥测与结算 — 边缘用量上报、开发者分润
// =============================================================================

/** 遥测日志：来自全球 L2 的原始调用记录 */
export const telemetryLogs = pgTable("telemetry_logs", {
  id: uuid("id").primaryKey().defaultRandom(),
  /** 租户 ID：等同 `organizations.id`（L2 归属边界） */
  tenantId: text("tenant_id").notNull(),
  /** L2 原始记录 ID */
  originalId: text("original_id").notNull(),
  /** 子账号标识：可为哈希值或 nullable，保护企业员工隐私（IAM 已下放 L2） */
  subAccountId: text("sub_account_id"),
  itemId: text("item_id").notNull(),
  actionName: text("action_name").notNull(),
  status: text("status").notNull(),
  latencyMs: numeric("latency_ms", { precision: 12, scale: 2 }),
  /** Unix 秒时间戳，需容纳 10 位（~2286 年前） */
  timestamp: numeric("timestamp", { precision: 15, scale: 4 }).notNull(),
  createdAt: timestamp("created_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
});

/** 开发者分润账单：按开发者 + 商品维度 */
export const developerPayouts = pgTable(
  "developer_payouts",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    developerId: text("developer_id").notNull(),
    /** 商品标识（plugin_id 或 item_id，如 skill:com.jachin.weather） */
    itemId: text("item_id").notNull(),
    totalCalls: integer("total_calls").notNull().default(0),
    unpaidAmountCents: integer("unpaid_amount_cents").notNull().default(0),
    paidAmountCents: integer("paid_amount_cents").notNull().default(0),
    lastUpdatedAt: timestamp("last_updated_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (t) => [uniqueIndex("developer_payouts_dev_item").on(t.developerId, t.itemId)]
);
