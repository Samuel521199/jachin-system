-- =============================================================================
-- L1 Nexus：将已有 PostgreSQL 库升级到当前架构（P1 租户 SSOT + P2 Fleet ACL + slug）
-- 对应仓库：cloud/nexus/drizzle/0012_p1_tenant_ssot.sql
--          cloud/nexus/drizzle/0013_fleet_acl_device_groups.sql（枚举改为幂等）
--          cloud/nexus/drizzle/0014_organizations_slug.sql
-- 说明文档：docs/ARCHITECTURE_L1_WORKSPACE_L2_GATEWAY_L3.md
--
-- 特性：尽量幂等（可重复执行）。执行前请备份数据库。
-- 要求：PostgreSQL 12+（建议 14+）；schema 默认 public。
--
-- 示例（与 l1.env 同机，只读挂载 bundle）：
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f l1_migrate_architecture_p1_p2_slug.sql
--
-- 若你使用 drizzle-kit 管理迁移：执行本脚本后，请用 drizzle 元数据表对齐记录，
-- 或改由 `npx drizzle-kit migrate` 统一执行，避免重复应用同一变更。
-- =============================================================================

-- -----------------------------------------------------------------------------
-- P1：organizations.is_personal_default；无组织用户 → 个人默认工作区；删除 users.tenant_id
-- -----------------------------------------------------------------------------

ALTER TABLE public.organizations
  ADD COLUMN IF NOT EXISTS is_personal_default boolean NOT NULL DEFAULT false;

UPDATE public.organizations
SET is_personal_default = false
WHERE is_personal_default IS NULL;

-- 为每个尚未加入任何组织的用户创建个人默认组织（与 Drizzle 0012 一致）
DO $$
DECLARE
  r RECORD;
  new_org_id uuid;
BEGIN
  FOR r IN
    SELECT u.id AS uid FROM public.users u
    WHERE NOT EXISTS (
      SELECT 1 FROM public.organization_users ou WHERE ou.user_id = u.id
    )
  LOOP
    new_org_id := gen_random_uuid();
    INSERT INTO public.organizations (id, name, billing_plan, is_personal_default, created_at, updated_at)
    VALUES (
      new_org_id,
      'Personal workspace',
      'free',
      true,
      now(),
      now()
    );
    INSERT INTO public.organization_users (id, org_id, user_id, role, created_at)
    VALUES (gen_random_uuid(), new_org_id, r.uid, 'owner', now());
  END LOOP;
END $$;

ALTER TABLE public.users DROP COLUMN IF EXISTS tenant_id;

-- -----------------------------------------------------------------------------
-- P2 Fleet ACL：扩展 org_role、device_groups、device_group_members、edge_agents.device_group_id
-- （枚举追加使用 DO 块，可重复执行）
-- -----------------------------------------------------------------------------

DO $$ BEGIN
  ALTER TYPE public.org_role ADD VALUE 'fleet_admin';
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  ALTER TYPE public.org_role ADD VALUE 'viewer';
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE public.device_group_member_role AS ENUM ('admin', 'viewer');
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS public.device_groups (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  org_id uuid NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
  name text NOT NULL,
  description text,
  created_at timestamp with time zone DEFAULT now() NOT NULL,
  updated_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE INDEX IF NOT EXISTS device_groups_org_id_idx ON public.device_groups (org_id);
CREATE UNIQUE INDEX IF NOT EXISTS device_groups_org_id_name_unique ON public.device_groups (org_id, name);

CREATE TABLE IF NOT EXISTS public.device_group_members (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  group_id uuid NOT NULL REFERENCES public.device_groups(id) ON DELETE CASCADE,
  user_id text NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
  role public.device_group_member_role DEFAULT 'viewer'::public.device_group_member_role NOT NULL,
  created_at timestamp with time zone DEFAULT now() NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS device_group_members_group_user_unique
  ON public.device_group_members (group_id, user_id);
CREATE INDEX IF NOT EXISTS device_group_members_user_id_idx ON public.device_group_members (user_id);

ALTER TABLE public.edge_agents ADD COLUMN IF NOT EXISTS device_group_id uuid;

DO $$ BEGIN
  ALTER TABLE public.edge_agents
    ADD CONSTRAINT edge_agents_device_group_id_device_groups_id_fk
    FOREIGN KEY (device_group_id) REFERENCES public.device_groups(id) ON DELETE SET NULL;
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS edge_agents_organization_id_idx ON public.edge_agents (organization_id);
CREATE INDEX IF NOT EXISTS edge_agents_device_group_id_idx ON public.edge_agents (device_group_id);

-- -----------------------------------------------------------------------------
-- 工作区 slug（可选，全局唯一；契约主键仍为 organizations.id UUID）
-- -----------------------------------------------------------------------------

ALTER TABLE public.organizations ADD COLUMN IF NOT EXISTS slug character varying(64);
CREATE UNIQUE INDEX IF NOT EXISTS organizations_slug_unique ON public.organizations (slug);

-- =============================================================================
-- 完成。请部署与之一致的 Nexus 应用版本并配置 L1_L2_LOGIN_SHARED_SECRET 等环境变量。
-- =============================================================================
