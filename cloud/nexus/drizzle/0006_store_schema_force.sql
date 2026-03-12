-- 强制补齐 store schema（显式 public schema）
-- 若 0005 未生效或连接了不同 DB，此迁移可独立修复

-- 1. 枚举（幂等，创建于 public schema）
DO $$ BEGIN
  CREATE TYPE public.item_type AS ENUM ('SKILL', 'MCP');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE public.visibility AS ENUM ('PUBLIC', 'PRIVATE');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE public.runtime_tier AS ENUM ('L3_LOCAL', 'L2_GATEWAY', 'L1_CLOUD');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE public.license_status AS ENUM ('ACTIVE', 'EXPIRED', 'REVOKED');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 2. plugins_registry 补齐列（显式 public）
ALTER TABLE public.plugins_registry ADD COLUMN IF NOT EXISTS item_type public.item_type DEFAULT 'SKILL';
ALTER TABLE public.plugins_registry ADD COLUMN IF NOT EXISTS visibility public.visibility DEFAULT 'PUBLIC';
ALTER TABLE "public"."plugins_registry" ADD COLUMN IF NOT EXISTS "package_url" TEXT;
ALTER TABLE "public"."plugins_registry" ADD COLUMN IF NOT EXISTS "price_monthly" integer DEFAULT 0;
ALTER TABLE "public"."plugins_registry" ADD COLUMN IF NOT EXISTS "runtime_tier" "public"."runtime_tier" DEFAULT 'L3_LOCAL';
ALTER TABLE "public"."plugins_registry" ADD COLUMN IF NOT EXISTS "required_mcps" jsonb DEFAULT '[]';
ALTER TABLE "public"."plugins_registry" ADD COLUMN IF NOT EXISTS "developer_id" TEXT;

-- 3. user_licenses（若不存在）
CREATE TABLE IF NOT EXISTS "public"."user_licenses" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "tenant_id" text NOT NULL,
  "item_id" uuid NOT NULL REFERENCES "public"."plugins_registry"("id") ON DELETE CASCADE,
  "status" "public"."license_status" NOT NULL DEFAULT 'ACTIVE',
  "purchased_at" timestamp with time zone NOT NULL DEFAULT now(),
  "expires_at" timestamp with time zone
);

CREATE UNIQUE INDEX IF NOT EXISTS "user_licenses_tenant_item_unique"
  ON "public"."user_licenses" ("tenant_id", "item_id");
