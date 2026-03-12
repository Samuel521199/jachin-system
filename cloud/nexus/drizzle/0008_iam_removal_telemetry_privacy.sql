-- IAM 下放 L2：删除 L1 iam_roles、iam_role_permissions
-- 遥测隐私：sub_account_id 改为 nullable，保护企业员工隐私

-- 1. 删除 IAM 表（先删子表，再删父表）
DROP TABLE IF EXISTS "iam_role_permissions";
DROP TABLE IF EXISTS "iam_roles";

-- 2. telemetry_logs.sub_account_id 改为可空
ALTER TABLE "telemetry_logs" ALTER COLUMN "sub_account_id" DROP NOT NULL;
