-- L1 云端商城 schema：item_type/visibility/runtime_tier 枚举、plugins_registry 新列、user_licenses 表
-- 修复：字段 "item_type" 不存在、关系 "user_licenses" 不存在

-- 1. 创建枚举类型（若已存在则忽略）
DO $$ BEGIN
  CREATE TYPE "item_type" AS ENUM ('SKILL', 'MCP');
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE "visibility" AS ENUM ('PUBLIC', 'PRIVATE');
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE "runtime_tier" AS ENUM ('L3_LOCAL', 'L2_GATEWAY', 'L1_CLOUD');
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TYPE "license_status" AS ENUM ('ACTIVE', 'EXPIRED', 'REVOKED');
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

-- 2. plugins_registry 添加缺失列
ALTER TABLE "plugins_registry" ADD COLUMN IF NOT EXISTS "item_type" "item_type" DEFAULT 'SKILL';
ALTER TABLE "plugins_registry" ADD COLUMN IF NOT EXISTS "visibility" "visibility" DEFAULT 'PUBLIC';
ALTER TABLE "plugins_registry" ADD COLUMN IF NOT EXISTS "package_url" TEXT;
ALTER TABLE "plugins_registry" ADD COLUMN IF NOT EXISTS "price_monthly" integer DEFAULT 0;
ALTER TABLE "plugins_registry" ADD COLUMN IF NOT EXISTS "runtime_tier" "runtime_tier" DEFAULT 'L3_LOCAL';
ALTER TABLE "plugins_registry" ADD COLUMN IF NOT EXISTS "required_mcps" jsonb DEFAULT '[]';
ALTER TABLE "plugins_registry" ADD COLUMN IF NOT EXISTS "developer_id" TEXT;

-- 兼容旧 schema：若有 download_url 无 package_url，则复制
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'plugins_registry' AND column_name = 'download_url') THEN
    UPDATE "plugins_registry" SET "package_url" = "download_url" WHERE "package_url" IS NULL AND "download_url" IS NOT NULL;
  END IF;
END $$;

-- 兼容旧 schema：若有 download_hash 无 package_sha256，则复制
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'plugins_registry' AND column_name = 'download_hash') THEN
    UPDATE "plugins_registry" SET "package_sha256" = "download_hash" WHERE "package_sha256" IS NULL AND "download_hash" IS NOT NULL;
  END IF;
END $$;

-- 将 item_type 设为 NOT NULL（先填默认值）
UPDATE "plugins_registry" SET "item_type" = 'SKILL' WHERE "item_type" IS NULL;
ALTER TABLE "plugins_registry" ALTER COLUMN "item_type" SET NOT NULL;
ALTER TABLE "plugins_registry" ALTER COLUMN "item_type" SET DEFAULT 'SKILL';

-- 3. 创建 user_licenses 表
CREATE TABLE IF NOT EXISTS "user_licenses" (
  "id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  "tenant_id" text NOT NULL,
  "item_id" uuid NOT NULL REFERENCES "plugins_registry"("id") ON DELETE CASCADE,
  "status" "license_status" NOT NULL DEFAULT 'ACTIVE',
  "purchased_at" timestamp with time zone NOT NULL DEFAULT now(),
  "expires_at" timestamp with time zone
);

CREATE UNIQUE INDEX IF NOT EXISTS "user_licenses_tenant_item_unique"
  ON "user_licenses" ("tenant_id", "item_id");
