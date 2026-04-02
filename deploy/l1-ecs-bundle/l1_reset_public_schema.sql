-- =============================================================================
-- Jachin L1 (Nexus) — 清空 PostgreSQL public schema，用于「旧表结构 → 当前版本」全量重建
-- =============================================================================
-- ⚠️  破坏性操作：删除当前库 public 下全部表、视图、枚举、函数、Drizzle 迁移记录等。
-- ⚠️  执行前请备份数据库（阿里云 RDS：控制台「备份恢复」或逻辑备份）。
--
-- 执行本文件后，必须接着跑 Drizzle 迁移（任选其一）：
--   A) 服务器一条龙：同目录 ./server-l1-db-reset-and-migrate.sh --yes /path/to/cloud/nexus
--   B) 本机：cd cloud/nexus && npm ci && npm run db:migrate && npm run db:init-store（DATABASE_URL 指向该库）
--
-- 阿里云 RDS PostgreSQL：使用高权限账号或数据库 Owner；若 GRANT ... PUBLIC 报错可注释掉末行 GRANT。
-- =============================================================================

BEGIN;

DROP SCHEMA IF EXISTS public CASCADE;

CREATE SCHEMA public;

-- 恢复默认权限（与 cloud/nexus/scripts/reset-db.ts 一致）
GRANT ALL ON SCHEMA public TO CURRENT_USER;
GRANT ALL ON SCHEMA public TO PUBLIC;

COMMIT;

-- 验证：\dn+ public （psql 内）应仅剩空 public schema
