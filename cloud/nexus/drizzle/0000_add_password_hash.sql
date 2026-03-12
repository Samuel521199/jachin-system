-- 为 users 表添加 password_hash 列（Auth.js Drizzle Adapter 规范）
-- 配对确认时插入默认用户需要此列；若表已存在则仅添加缺失列
ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "password_hash" TEXT;
