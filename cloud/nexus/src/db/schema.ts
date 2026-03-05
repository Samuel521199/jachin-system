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
} from "drizzle-orm/pg-core";
import { relations } from "drizzle-orm";

// =============================================================================
// Auth.js 基础表（Drizzle Adapter 标准规范）
// =============================================================================

export const users = pgTable("users", {
  id: text("id")
    .primaryKey()
    .$defaultFn(() => crypto.randomUUID()),
  name: text("name"),
  email: text("email").unique(),
  emailVerified: timestamp("email_verified", { mode: "date" }),
  image: text("image"),
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
// SaaS 多租户核心表
// =============================================================================

export const organizations = pgTable("organizations", {
  id: uuid("id").primaryKey().defaultRandom(),
  name: text("name").notNull(),
  billingPlan: text("billing_plan").default("free"),
  createdAt: timestamp("created_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
});

export const orgRoleEnum = pgEnum("org_role", ["owner", "admin", "member"]);

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

export const edgeAgents = pgTable("edge_agents", {
  id: uuid("id").primaryKey().defaultRandom(),
  userId: text("user_id").references(() => users.id, { onDelete: "set null" }),
  organizationId: uuid("organization_id").references(() => organizations.id, {
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
});

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
// Drizzle Relations（关系映射）
// =============================================================================

export const usersRelations = relations(users, ({ many }) => ({
  accounts: many(accounts),
  sessions: many(sessions),
  organizationUsers: many(organizationUsers),
  edgeAgents: many(edgeAgents),
  blueprints: many(blueprints),
  transactions: many(transactions),
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
}));

export const organizationUsersRelations = relations(organizationUsers, ({ one }) => ({
  organization: one(organizations, { fields: [organizationUsers.orgId], references: [organizations.id] }),
  user: one(users, { fields: [organizationUsers.userId], references: [users.id] }),
}));

export const blueprintsRelations = relations(blueprints, ({ one, many }) => ({
  creator: one(users, { fields: [blueprints.creatorId], references: [users.id] }),
  organization: one(organizations, { fields: [blueprints.organizationId], references: [organizations.id] }),
  edgeAgents: many(edgeAgents),
}));

export const edgeAgentsRelations = relations(edgeAgents, ({ one }) => ({
  user: one(users, { fields: [edgeAgents.userId], references: [users.id] }),
  organization: one(organizations, { fields: [edgeAgents.organizationId], references: [organizations.id] }),
  currentBlueprint: one(blueprints, {
    fields: [edgeAgents.currentBlueprintId],
    references: [blueprints.id],
  }),
}));

export const transactionsRelations = relations(transactions, ({ one }) => ({
  buyer: one(users, { fields: [transactions.buyerId], references: [users.id] }),
  organization: one(organizations, { fields: [transactions.organizationId], references: [organizations.id] }),
}));
