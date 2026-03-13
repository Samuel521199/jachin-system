-- users 表补齐 tenant_id、is_root（与 schema.ts 对齐，配对确认插入默认用户需要）
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "tenant_id" TEXT;
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "is_root" BOOLEAN NOT NULL DEFAULT false;
