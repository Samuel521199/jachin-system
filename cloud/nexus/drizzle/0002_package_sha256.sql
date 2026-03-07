-- 包 SHA-256 校验码（L2 下载后完整性校验）
ALTER TABLE "plugins_registry" ADD COLUMN IF NOT EXISTS "package_sha256" TEXT;
