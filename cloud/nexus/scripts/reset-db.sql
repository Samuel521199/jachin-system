-- 重置 Nexus 表（按外键依赖顺序删除）
-- 用法: psql -U postgres -d jachin_nexus -f scripts/reset-db.sql

-- 删除表（先删有外键的）
DROP TABLE IF EXISTS agent_message_queue CASCADE;
DROP TABLE IF EXISTS deploy_commands CASCADE;
DROP TABLE IF EXISTS plugins_registry CASCADE;
DROP TABLE IF EXISTS transactions CASCADE;
DROP TABLE IF EXISTS edge_agents CASCADE;
DROP TABLE IF EXISTS blueprints CASCADE;
DROP TABLE IF EXISTS organization_users CASCADE;
DROP TABLE IF EXISTS accounts CASCADE;
DROP TABLE IF EXISTS sessions CASCADE;
DROP TABLE IF EXISTS verification_tokens CASCADE;
DROP TABLE IF EXISTS organizations CASCADE;
DROP TABLE IF EXISTS users CASCADE;

-- 删除枚举类型
DROP TYPE IF EXISTS edge_agent_status CASCADE;
DROP TYPE IF EXISTS org_role CASCADE;
