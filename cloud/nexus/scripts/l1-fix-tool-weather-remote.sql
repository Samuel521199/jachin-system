-- 在 47.86.39.173（或任意远端 Nexus 的 Postgres）上执行，用于修复「极简天气」TOOL 上架 500。
-- 原因常见有二：① item_type 枚举未含 TOOL（未跑 drizzle/0015）；② 旧行卡住，需删掉后重新走 INSERT。
--
-- 用法：psql "$DATABASE_URL" -f cloud/nexus/scripts/l1-fix-tool-weather-remote.sql
-- 然后在本机：见 scripts/republish-util-weather-tool.ps1

-- 1) 确保枚举含 TOOL（与 drizzle/0015_item_type_tool.sql 一致）
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_enum e
    JOIN pg_type t ON e.enumtypid = t.oid
    WHERE t.typname = 'item_type' AND e.enumlabel = 'TOOL'
  ) THEN
    ALTER TYPE item_type ADD VALUE 'TOOL';
  END IF;
END $$;

-- 2) 删除半成功/卡住的那条，便于重新 publish 走 INSERT（user_licenses 子表会随 ON DELETE CASCADE 清理）
DELETE FROM plugins_registry
WHERE plugin_id = 'com.jachin.tool.util-weather-lite';
