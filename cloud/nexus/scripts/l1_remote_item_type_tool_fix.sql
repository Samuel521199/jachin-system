-- =============================================================================
-- L1 远程库修复：为商城枚举 item_type 增加 TOOL（原子工具包）
--
-- 背景：早期迁移仅有 SKILL、MCP；未执行 drizzle/0015 时，上架 TOOL 会因枚举值不存在而失败
-- （Skill/MCP 不受影响）。本脚本与 0015_item_type_tool.sql 等价，可重复执行（幂等）。
--
-- 用法（在可连 47.86.39.173 Postgres 的机器上）：
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f cloud/nexus/scripts/l1_remote_item_type_tool_fix.sql
-- 或：
--   psql -h 127.0.0.1 -U jachin -d jachin_nexus -v ON_ERROR_STOP=1 -f l1_remote_item_type_tool_fix.sql
--
-- 说明：
-- - 仅数据库层；若重复发布 TOOL 仍 500，请确认 L1 镜像/代码已包含「更新 plugins_registry 时不写 plugin_id」等修复。
-- - 执行后无需重启 Postgres；L1 容器可保持运行或按需重启。
-- =============================================================================

SET search_path = public;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_enum e
    JOIN pg_type t ON e.enumtypid = t.oid
    JOIN pg_namespace n ON t.typnamespace = n.oid
    WHERE n.nspname = 'public'
      AND t.typname = 'item_type'
      AND e.enumlabel = 'TOOL'
  ) THEN
    ALTER TYPE public.item_type ADD VALUE 'TOOL';
  END IF;
END $$;

-- 校验（可选：执行后应能看到 TOOL）
-- SELECT e.enumlabel
-- FROM pg_enum e
-- JOIN pg_type t ON e.enumtypid = t.oid
-- JOIN pg_namespace n ON t.typnamespace = n.oid
-- WHERE n.nspname = 'public' AND t.typname = 'item_type'
-- ORDER BY e.enumsortorder;
