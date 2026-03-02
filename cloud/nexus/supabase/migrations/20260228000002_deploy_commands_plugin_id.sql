-- Forge 直连部署：deploy_commands 增加 plugin_id，便于 poll 直接返回
ALTER TABLE deploy_commands ADD COLUMN IF NOT EXISTS plugin_id TEXT;
CREATE INDEX IF NOT EXISTS idx_deploy_commands_plugin_id ON deploy_commands(plugin_id);
