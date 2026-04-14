-- =============================================================================
-- Jachin L1 (Nexus) — 空库 / 全量重建 public schema
-- 来源：cloud/nexus/src/db/schema.ts（与 drizzle 迁移链终点对齐）
--
-- ⚠️  警告：会删除 public 下全部对象（含登录、商城、许可证等）。仅用于受控环境或首次装机。
-- 用法（示例）：
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f l1_public_schema_fresh_install.sql
--
-- 说明：
-- 1) 服务器仅跑 Docker 镜像、无源码时，把本文件随部署包放到 /opt/jachin-l1/ 一类目录执行即可。
-- 2) 执行前请确认 DATABASE_URL 指向正确库；生产务必先备份。
-- =============================================================================

BEGIN;

DROP SCHEMA IF EXISTS public CASCADE;
CREATE SCHEMA public;
GRANT ALL ON SCHEMA public TO CURRENT_USER;
GRANT ALL ON SCHEMA public TO PUBLIC;

-- -----------------------------------------------------------------------------
-- 枚举类型
-- -----------------------------------------------------------------------------
CREATE TYPE "org_role" AS ENUM ('owner', 'admin', 'member', 'fleet_admin', 'viewer');
CREATE TYPE "device_group_member_role" AS ENUM ('admin', 'viewer');
CREATE TYPE "edge_agent_status" AS ENUM ('pending', 'active', 'offline');
CREATE TYPE "item_type" AS ENUM ('SKILL', 'MCP');
CREATE TYPE "visibility" AS ENUM ('PUBLIC', 'PRIVATE');
CREATE TYPE "runtime_tier" AS ENUM ('L3_LOCAL', 'L2_GATEWAY', 'L1_CLOUD');
CREATE TYPE "license_status" AS ENUM ('ACTIVE', 'EXPIRED', 'REVOKED');

-- -----------------------------------------------------------------------------
-- 平台管理员
-- -----------------------------------------------------------------------------
CREATE TABLE "platform_admins" (
  "id" text PRIMARY KEY,
  "username" text NOT NULL UNIQUE,
  "password_hash" text NOT NULL,
  "role" text NOT NULL DEFAULT 'super_admin',
  "created_at" timestamptz NOT NULL DEFAULT now(),
  "updated_at" timestamptz NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- Auth.js 用户 / 会话（Drizzle Adapter）
-- -----------------------------------------------------------------------------
CREATE TABLE "users" (
  "id" text PRIMARY KEY,
  "name" text,
  "email" text UNIQUE,
  "email_verified" timestamp,
  "image" text,
  "password_hash" text,
  "is_root" boolean DEFAULT false
);

CREATE TABLE "accounts" (
  "user_id" text NOT NULL REFERENCES "users"("id") ON DELETE CASCADE,
  "type" text NOT NULL,
  "provider" text NOT NULL,
  "provider_account_id" text NOT NULL,
  "refresh_token" text,
  "access_token" text,
  "expires_at" integer,
  "token_type" text,
  "scope" text,
  "id_token" text,
  "session_state" text,
  PRIMARY KEY ("provider", "provider_account_id")
);

CREATE TABLE "sessions" (
  "session_token" text PRIMARY KEY,
  "user_id" text NOT NULL REFERENCES "users"("id") ON DELETE CASCADE,
  "expires" timestamp NOT NULL
);

CREATE TABLE "verification_tokens" (
  "identifier" text NOT NULL,
  "token" text NOT NULL,
  "expires" timestamp NOT NULL,
  PRIMARY KEY ("identifier", "token")
);

-- -----------------------------------------------------------------------------
-- 多租户（Organization = Tenant）
-- -----------------------------------------------------------------------------
CREATE TABLE "organizations" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "name" text NOT NULL,
  "slug" varchar(64),
  "billing_plan" text DEFAULT 'free',
  "is_personal_default" boolean NOT NULL DEFAULT false,
  "created_at" timestamptz NOT NULL DEFAULT now(),
  "updated_at" timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX "organizations_slug_unique" ON "organizations" ("slug");

CREATE TABLE "organization_users" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "org_id" uuid NOT NULL REFERENCES "organizations"("id") ON DELETE CASCADE,
  "user_id" text NOT NULL REFERENCES "users"("id") ON DELETE CASCADE,
  "role" "org_role" NOT NULL DEFAULT 'member',
  "created_at" timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT "organization_users_org_id_user_id_unique" UNIQUE ("org_id", "user_id")
);

-- -----------------------------------------------------------------------------
-- P2 Fleet：设备组
-- -----------------------------------------------------------------------------
CREATE TABLE "device_groups" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "org_id" uuid NOT NULL REFERENCES "organizations"("id") ON DELETE CASCADE,
  "name" text NOT NULL,
  "description" text,
  "created_at" timestamptz NOT NULL DEFAULT now(),
  "updated_at" timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX "device_groups_org_id_idx" ON "device_groups" ("org_id");
CREATE UNIQUE INDEX "device_groups_org_id_name_unique" ON "device_groups" ("org_id", "name");

CREATE TABLE "device_group_members" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "group_id" uuid NOT NULL REFERENCES "device_groups"("id") ON DELETE CASCADE,
  "user_id" text NOT NULL REFERENCES "users"("id") ON DELETE CASCADE,
  "role" "device_group_member_role" NOT NULL DEFAULT 'viewer',
  "created_at" timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX "device_group_members_group_user_unique" ON "device_group_members" ("group_id", "user_id");
CREATE INDEX "device_group_members_user_id_idx" ON "device_group_members" ("user_id");

-- -----------------------------------------------------------------------------
-- 蓝图 / 边缘节点 / 交易 / 队列 / 部署命令
-- -----------------------------------------------------------------------------
CREATE TABLE "blueprints" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "creator_id" text REFERENCES "users"("id") ON DELETE SET NULL,
  "organization_id" uuid REFERENCES "organizations"("id") ON DELETE SET NULL,
  "name" text NOT NULL,
  "description" text,
  "ast_json" jsonb NOT NULL DEFAULT '{}',
  "price" numeric(12, 4) DEFAULT '0',
  "created_at" timestamptz NOT NULL DEFAULT now(),
  "updated_at" timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE "plugins_registry" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "plugin_id" text NOT NULL UNIQUE,
  "version" text NOT NULL DEFAULT '1.0.0',
  "item_type" "item_type" NOT NULL,
  "name" text NOT NULL,
  "description" text,
  "developer_id" text,
  "visibility" "visibility" NOT NULL DEFAULT 'PRIVATE',
  "price_monthly" integer NOT NULL DEFAULT 0,
  "runtime_tier" "runtime_tier" NOT NULL,
  "required_mcps" jsonb DEFAULT '[]',
  "package_url" text,
  "package_sha256" text,
  "category" text DEFAULT 'skill',
  "download_count" integer DEFAULT 0,
  "manifest_json" jsonb,
  "status" text NOT NULL DEFAULT 'pending',
  "reject_reason" text,
  "created_at" timestamptz NOT NULL DEFAULT now(),
  "updated_at" timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX "plugins_registry_status_created_idx" ON "plugins_registry" ("status", "created_at");

CREATE TABLE "edge_agents" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "user_id" text REFERENCES "users"("id") ON DELETE SET NULL,
  "organization_id" uuid REFERENCES "organizations"("id") ON DELETE SET NULL,
  "device_group_id" uuid REFERENCES "device_groups"("id") ON DELETE SET NULL,
  "name" text,
  "pairing_code" varchar(6) NOT NULL UNIQUE,
  "status" "edge_agent_status" NOT NULL DEFAULT 'pending',
  "current_blueprint_id" uuid REFERENCES "blueprints"("id") ON DELETE SET NULL,
  "auth_token" text,
  "pairing_expires_at" timestamptz,
  "last_heartbeat" timestamptz,
  "im_binding_id" text,
  "im_platform" text DEFAULT 'telegram',
  "created_at" timestamptz NOT NULL DEFAULT now(),
  "updated_at" timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX "edge_agents_organization_id_idx" ON "edge_agents" ("organization_id");
CREATE INDEX "edge_agents_device_group_id_idx" ON "edge_agents" ("device_group_id");

CREATE TABLE "agent_message_queue" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "agent_id" uuid NOT NULL REFERENCES "edge_agents"("id") ON DELETE CASCADE,
  "message_text" text NOT NULL,
  "direction" text NOT NULL DEFAULT 'inbound',
  "status" text NOT NULL DEFAULT 'pending',
  "source_meta" jsonb,
  "processed_at" timestamptz,
  "created_at" timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE "transactions" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "buyer_id" text NOT NULL REFERENCES "users"("id") ON DELETE CASCADE,
  "organization_id" uuid REFERENCES "organizations"("id") ON DELETE SET NULL,
  "resource_type" text NOT NULL,
  "resource_id" uuid NOT NULL,
  "resource_plugin_id" text,
  "action" text NOT NULL DEFAULT 'acquire',
  "license_key" text,
  "created_at" timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE "deploy_commands" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "user_id" text NOT NULL,
  "layer2_instance_id" text NOT NULL,
  "resource_type" text NOT NULL DEFAULT 'plugin',
  "resource_id" uuid NOT NULL,
  "plugin_id" text,
  "download_url" text NOT NULL,
  "temp_token" text NOT NULL,
  "token_expires_at" timestamptz NOT NULL,
  "status" text NOT NULL DEFAULT 'pending',
  "created_at" timestamptz NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- 许可证 / 桌面端发布 / 遥测
-- -----------------------------------------------------------------------------
CREATE TABLE "user_licenses" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "tenant_id" text NOT NULL,
  "item_id" uuid NOT NULL REFERENCES "plugins_registry"("id") ON DELETE CASCADE,
  "status" "license_status" NOT NULL DEFAULT 'ACTIVE',
  "purchased_at" timestamptz NOT NULL DEFAULT now(),
  "expires_at" timestamptz
);
CREATE UNIQUE INDEX "user_licenses_tenant_item_unique" ON "user_licenses" ("tenant_id", "item_id");

CREATE TABLE "desktop_app_releases" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "version" text NOT NULL UNIQUE,
  "notes" text,
  "pub_date" timestamptz NOT NULL,
  "artifacts" jsonb NOT NULL,
  "created_at" timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE "telemetry_logs" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "tenant_id" text NOT NULL,
  "original_id" text NOT NULL,
  "sub_account_id" text,
  "item_id" text NOT NULL,
  "action_name" text NOT NULL,
  "status" text NOT NULL,
  "latency_ms" numeric(12, 2),
  "timestamp" numeric(15, 4) NOT NULL,
  "created_at" timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX "idx_telemetry_logs_tenant_ts" ON "telemetry_logs" ("tenant_id", "timestamp");
CREATE INDEX "idx_telemetry_logs_tenant_item" ON "telemetry_logs" ("tenant_id", "item_id");
CREATE INDEX "idx_telemetry_logs_item" ON "telemetry_logs" ("item_id");

CREATE TABLE "developer_payouts" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "developer_id" text NOT NULL,
  "item_id" text NOT NULL,
  "total_calls" integer NOT NULL DEFAULT 0,
  "unpaid_amount_cents" integer NOT NULL DEFAULT 0,
  "paid_amount_cents" integer NOT NULL DEFAULT 0,
  "last_updated_at" timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX "developer_payouts_dev_item" ON "developer_payouts" ("developer_id", "item_id");

COMMIT;
