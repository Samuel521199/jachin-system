-- 驳回理由字段
ALTER TABLE "plugins_registry" ADD COLUMN IF NOT EXISTS "reject_reason" TEXT;
