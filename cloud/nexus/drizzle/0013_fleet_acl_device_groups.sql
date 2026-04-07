-- P2 Fleet ACL：device_groups、org_role 扩展、device_group_members、edge_agents.device_group_id
-- 依赖：0012_p1_tenant_ssot
-- 注意：若 PostgreSQL < 15 且在「单语句事务」中执行失败，可将下方 ALTER TYPE … ADD VALUE 拆出单独会话执行（旧版 PG 对枚举追加与事务有限制）。

-- 1) 扩展 org_role（与 drizzle-kit push 可并存：枚举值已由 schema 创建时跳过）
DO $$ BEGIN
  ALTER TYPE "org_role" ADD VALUE 'fleet_admin';
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  ALTER TYPE "org_role" ADD VALUE 'viewer';
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

-- 2) 组内成员角色枚举
DO $$ BEGIN
  CREATE TYPE "device_group_member_role" AS ENUM ('admin', 'viewer');
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

-- 3) 设备资源组
CREATE TABLE IF NOT EXISTS "device_groups" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "org_id" uuid NOT NULL REFERENCES "organizations"("id") ON DELETE CASCADE,
  "name" text NOT NULL,
  "description" text,
  "created_at" timestamp with time zone DEFAULT now() NOT NULL,
  "updated_at" timestamp with time zone DEFAULT now() NOT NULL
);

CREATE INDEX IF NOT EXISTS "device_groups_org_id_idx" ON "device_groups" ("org_id");
CREATE UNIQUE INDEX IF NOT EXISTS "device_groups_org_id_name_unique" ON "device_groups" ("org_id", "name");

-- 4) 组级成员（org 权限之下的细粒度覆写）
CREATE TABLE IF NOT EXISTS "device_group_members" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "group_id" uuid NOT NULL REFERENCES "device_groups"("id") ON DELETE CASCADE,
  "user_id" text NOT NULL REFERENCES "users"("id") ON DELETE CASCADE,
  "role" "device_group_member_role" DEFAULT 'viewer' NOT NULL,
  "created_at" timestamp with time zone DEFAULT now() NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS "device_group_members_group_user_unique" ON "device_group_members" ("group_id", "user_id");
CREATE INDEX IF NOT EXISTS "device_group_members_user_id_idx" ON "device_group_members" ("user_id");

-- 5) 边缘设备关联组
ALTER TABLE "edge_agents" ADD COLUMN IF NOT EXISTS "device_group_id" uuid;
DO $$ BEGIN
  ALTER TABLE "edge_agents"
    ADD CONSTRAINT "edge_agents_device_group_id_device_groups_id_fk"
    FOREIGN KEY ("device_group_id") REFERENCES "device_groups"("id") ON DELETE SET NULL ON UPDATE NO ACTION;
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS "edge_agents_organization_id_idx" ON "edge_agents" ("organization_id");
CREATE INDEX IF NOT EXISTS "edge_agents_device_group_id_idx" ON "edge_agents" ("device_group_id");
