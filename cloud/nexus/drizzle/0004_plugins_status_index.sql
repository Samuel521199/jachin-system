-- 优化 status = 'pending' 的审核列表查询
CREATE INDEX IF NOT EXISTS "plugins_registry_status_created_idx" ON "plugins_registry" ("status", "created_at");
