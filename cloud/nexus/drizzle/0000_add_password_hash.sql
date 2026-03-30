-- 为 users 表添加 password_hash 列（Auth.js Drizzle Adapter 规范）
-- 空库时 users 尚未由本迁移链创建：仅当表已存在再 ALTER，避免 42P01
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = 'users'
  ) THEN
    ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "password_hash" TEXT;
  END IF;
END $$;
