-- =============================================================================
-- 从「旧版全量 SQL / 旧库」补到当前 Nexus 所需结构（幂等，可重复执行）
-- 解决：organizations.is_personal_default 不存在、缺少 Fleet ACL 表、org_role 缺枚举值
-- 用法（与 l1.env 同机）：
--   docker run --rm --network host -v /opt/jachin-l1:/work:ro --entrypoint psql postgres:16-bookworm \
--     "$(grep '^DATABASE_URL=' /opt/jachin-l1/l1.env | sed 's/^DATABASE_URL=//' | tr -d '\"')" \
--     -v ON_ERROR_STOP=1 -f /work/l1_hotfix_p1_p2_schema.sql
-- =============================================================================

-- 1) P1：organizations.is_personal_default
ALTER TABLE public.organizations
  ADD COLUMN IF NOT EXISTS is_personal_default boolean NOT NULL DEFAULT false;

UPDATE public.organizations
SET is_personal_default = false
WHERE is_personal_default IS NULL;

-- 2) org_role 扩展（旧库可能只有 owner/admin/member；重复执行忽略）
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

-- 3) device_group_member_role
DO $$ BEGIN
  CREATE TYPE public.device_group_member_role AS ENUM ('admin', 'viewer');
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

-- 4) device_groups
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

-- 5) device_group_members
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

-- 6) edge_agents.device_group_id
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
