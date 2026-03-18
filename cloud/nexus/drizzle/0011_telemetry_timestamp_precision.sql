-- 修复 telemetry_logs.timestamp 数字溢出
-- 原 numeric(12,4) 整数部分仅 8 位，Unix 时间戳 1772889014（10 位）溢出
-- 改为 numeric(15,4)，整数部分 11 位，可容纳至 2286 年
ALTER TABLE "telemetry_logs"
  ALTER COLUMN "timestamp" TYPE numeric(15, 4);
