/**
 * 直接修复 store / 租户 schema（plugins_registry 新列、user_licenses、organizations.slug 等）
 * 使用与 Next.js 相同的 DATABASE_URL，解决：
 * - 迁移与应用连不同库
 * - journal 已登记但某次 migrate 未真正执行 SQL（列仍缺失）——与 drizzle 迁移幂等互补
 *
 * 用法: cd cloud/nexus && npx tsx scripts/init-store-schema.ts
 */
import "dotenv/config";
import { config } from "dotenv";
config({ path: ".env.local" });
config({ path: ".env" });

import postgres from "postgres";
import { log, error } from "../src/lib/console-utc";

const url = process.env.DATABASE_URL ?? "postgres://postgres:postgres@localhost:5432/postgres";

async function main() {
  const sql = postgres(url);
  log("[init-store-schema] Connecting to DB...");

  try {
    // 1. 枚举
    await sql.unsafe(`
      DO $$ BEGIN
        CREATE TYPE public.item_type AS ENUM ('SKILL', 'MCP');
      EXCEPTION WHEN duplicate_object THEN NULL;
      END $$;
    `);
    await sql.unsafe(`
      DO $$ BEGIN
        CREATE TYPE public.visibility AS ENUM ('PUBLIC', 'PRIVATE');
      EXCEPTION WHEN duplicate_object THEN NULL;
      END $$;
    `);
    await sql.unsafe(`
      DO $$ BEGIN
        CREATE TYPE public.runtime_tier AS ENUM ('L3_LOCAL', 'L2_GATEWAY', 'L1_CLOUD');
      EXCEPTION WHEN duplicate_object THEN NULL;
      END $$;
    `);
    await sql.unsafe(`
      DO $$ BEGIN
        CREATE TYPE public.license_status AS ENUM ('ACTIVE', 'EXPIRED', 'REVOKED');
      EXCEPTION WHEN duplicate_object THEN NULL;
      END $$;
    `);
    log("[init-store-schema] Enums OK");

    // 2. plugins_registry 列（需表已存在）
    await sql.unsafe(`
      ALTER TABLE public.plugins_registry ADD COLUMN IF NOT EXISTS item_type public.item_type DEFAULT 'SKILL';
    `);
    await sql.unsafe(`
      ALTER TABLE public.plugins_registry ADD COLUMN IF NOT EXISTS visibility public.visibility DEFAULT 'PUBLIC';
    `);
    await sql.unsafe(`
      ALTER TABLE public.plugins_registry ADD COLUMN IF NOT EXISTS package_url TEXT;
    `);
    await sql.unsafe(`
      ALTER TABLE public.plugins_registry ADD COLUMN IF NOT EXISTS price_monthly integer DEFAULT 0;
    `);
    await sql.unsafe(`
      ALTER TABLE public.plugins_registry ADD COLUMN IF NOT EXISTS runtime_tier public.runtime_tier DEFAULT 'L3_LOCAL';
    `);
    await sql.unsafe(`
      ALTER TABLE public.plugins_registry ADD COLUMN IF NOT EXISTS required_mcps jsonb DEFAULT '[]';
    `);
    await sql.unsafe(`
      ALTER TABLE public.plugins_registry ADD COLUMN IF NOT EXISTS developer_id TEXT;
    `);
    await sql.unsafe(`
      ALTER TABLE public.plugins_registry ADD COLUMN IF NOT EXISTS package_sha256 TEXT;
    `);
    await sql.unsafe(`
      ALTER TABLE public.plugins_registry ADD COLUMN IF NOT EXISTS reject_reason TEXT;
    `);
    await sql.unsafe(`
      ALTER TABLE public.plugins_registry ADD COLUMN IF NOT EXISTS version TEXT NOT NULL DEFAULT '1.0.0';
    `);
    await sql.unsafe(`
      ALTER TABLE public.plugins_registry ADD COLUMN IF NOT EXISTS updated_at timestamp with time zone NOT NULL DEFAULT now();
    `);
    await sql.unsafe(`
      CREATE INDEX IF NOT EXISTS plugins_registry_status_created_idx ON public.plugins_registry (status, created_at);
    `);
    // 兼容旧 schema：download_url 已弃用，改用 package_url；移除 NOT NULL 约束
    await sql.unsafe(`
      DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'plugins_registry' AND column_name = 'download_url') THEN
          ALTER TABLE public.plugins_registry ALTER COLUMN download_url DROP NOT NULL;
        END IF;
      END $$;
    `);
    log("[init-store-schema] plugins_registry columns OK");

    // 3. user_licenses
    await sql.unsafe(`
      CREATE TABLE IF NOT EXISTS public.user_licenses (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
        tenant_id text NOT NULL,
        item_id uuid NOT NULL REFERENCES public.plugins_registry(id) ON DELETE CASCADE,
        status public.license_status NOT NULL DEFAULT 'ACTIVE',
        purchased_at timestamp with time zone NOT NULL DEFAULT now(),
        expires_at timestamp with time zone
      );
    `);
    await sql.unsafe(`
      CREATE UNIQUE INDEX IF NOT EXISTS user_licenses_tenant_item_unique
        ON public.user_licenses (tenant_id, item_id);
    `);
    log("[init-store-schema] user_licenses OK");

    // 4. telemetry_logs, developer_payouts（IAM 已下放 L2，不再创建 iam_roles/iam_role_permissions）
    await sql.unsafe(`
      CREATE TABLE IF NOT EXISTS public.telemetry_logs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
        tenant_id text NOT NULL,
        original_id text NOT NULL,
        sub_account_id text,
        item_id text NOT NULL,
        action_name text NOT NULL,
        status text NOT NULL,
        latency_ms numeric(12, 2),
        timestamp numeric(15, 4) NOT NULL,
        created_at timestamp with time zone NOT NULL DEFAULT now()
      );
    `);
    await sql.unsafe(`
      CREATE INDEX IF NOT EXISTS idx_telemetry_logs_tenant_ts ON public.telemetry_logs (tenant_id, timestamp);
    `);
    await sql.unsafe(`
      CREATE TABLE IF NOT EXISTS public.developer_payouts (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
        developer_id text NOT NULL,
        item_id text NOT NULL,
        total_calls integer NOT NULL DEFAULT 0,
        unpaid_amount_cents integer NOT NULL DEFAULT 0,
        paid_amount_cents integer NOT NULL DEFAULT 0,
        last_updated_at timestamp with time zone NOT NULL DEFAULT now()
      );
    `);
    await sql.unsafe(`
      CREATE UNIQUE INDEX IF NOT EXISTS developer_payouts_dev_item ON public.developer_payouts (developer_id, item_id);
    `);
    // 修复：若表由旧迁移 0001 创建，timestamp 为 numeric(12,4)，Unix 10 位时间戳溢出
    await sql.unsafe(`
      ALTER TABLE public.telemetry_logs ALTER COLUMN timestamp TYPE numeric(15, 4);
    `);
    log("[init-store-schema] telemetry_logs, developer_payouts OK");

    // 5. users.is_root（配对等）。tenant_id 已废止（P1 SSOT），见 drizzle/0012、docs/MIGRATION_P1_TENANT.md
    await sql.unsafe(`
      ALTER TABLE public.users ADD COLUMN IF NOT EXISTS is_root BOOLEAN NOT NULL DEFAULT false;
    `);
    log("[init-store-schema] users columns OK");

    // 6. organizations.slug（Drizzle 0014；listOrganizationsForUser / JWT 依赖）
    await sql.unsafe(`
      ALTER TABLE public.organizations ADD COLUMN IF NOT EXISTS slug varchar(64);
    `);
    await sql.unsafe(`
      CREATE UNIQUE INDEX IF NOT EXISTS organizations_slug_unique ON public.organizations (slug);
    `);
    log("[init-store-schema] organizations.slug OK");

    // 打印连接信息（脱敏）便于排查 DB 不一致
    const masked = url.replace(/:([^:@]+)@/, ":****@");
    log("[init-store-schema] DATABASE_URL:", masked);
    log("[init-store-schema] Done. Restart Nexus.");
  } catch (e) {
    error("[init-store-schema] Error:", e);
    process.exit(1);
  } finally {
    await sql.end();
  }
}

main();
