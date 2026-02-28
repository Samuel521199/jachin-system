-- 允许 deploy_commands 在未登录时使用（开发阶段）
ALTER TABLE deploy_commands ALTER COLUMN user_id DROP NOT NULL;

-- 插入默认系统用户（用于无登录态时的 deploy）
INSERT INTO nexus_users (id, external_id, role)
VALUES ('00000000-0000-0000-0000-000000000001', 'system@nexus', 'super_admin')
ON CONFLICT DO NOTHING;
