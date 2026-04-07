-- users 表补齐 tenant_id、is_root（与 schema.ts 对齐，配对确认插入默认用户需要）
-- Auth 表由 drizzle-kit push 从 schema 创建；若仅 migrate 未 push，跳过以免 42P01。
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'users'
  ) THEN
    ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "tenant_id" TEXT;
    ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "is_root" BOOLEAN NOT NULL DEFAULT false;
  END IF;
END $$;
